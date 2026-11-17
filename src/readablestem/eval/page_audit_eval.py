"""`readablestem eval page-audit`: the page audit in shadow, on public and generated sets.

Each document of the set is converted, the audit (`pipeline/page_audit.py`) is asked about
every page, and the verdicts are written in a report beside the run's recording. Nothing
else changes: no output, no stored result, no decision. A verdict is one run's: the
report keeps, for each page, the key of the question asked, so two runs of one input can be
set side by side (the measured share of verdicts that differ between two runs is the reason
one run's verdict raises nothing).

Two kinds of set:

- the corpus (`eval.corpus`), each upload converted under the production profile with its
  recorded answers, as `eval cost` converts it;
- a layout gold set's folder (`gold.layout_gold`: `files/<id>.pdf`), each file converted
  with no model, so what is audited is the layout and the reading order.

The report holds codes, ids, boxes, counts and dollars, and no text of any document.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from readablestem.pipeline import page_audit
from readablestem.pipeline.page_audit import PageVerdict

if TYPE_CHECKING:
    from readablestem.config import Settings
    from readablestem.core.types import DocumentResult
    from readablestem.eval.render import ChromiumRenderer
    from readablestem.models.base import VisionClient


@dataclass(frozen=True)
class Document:
    """One converted document, or the code of why there is none to audit."""

    name: str
    pdf: bytes = b""
    result: DocumentResult | None = None
    error: str = ""


@dataclass
class Audited:
    """One document's verdicts."""

    name: str
    verdicts: list[PageVerdict] = field(default_factory=list)
    error: str = ""


@dataclass
class Run:
    documents: list[Audited] = field(default_factory=list)
    stopped: str | None = None  # the scope of the spend cap that ended the run


# ------------------------------------------------------------------------------- the sets


def corpus_documents(folder: Path, only: Sequence[str] = ()) -> Iterator[Document]:
    """The corpus's uploads, converted as a job converts them, from their recorded
    answers (`eval.measure`). `folder` is a scratch folder for the answers file."""
    from readablestem.eval import measure

    settings = measure.replay_settings(measure.answers_file(folder))
    for upload in measure.every_upload():
        if only and upload.entry not in only and upload.name not in only:
            continue
        converted = measure.result_of(upload, settings)
        if isinstance(converted, str):
            yield Document(upload.name, error=converted)
        else:
            yield Document(upload.name, *converted)


def gold_documents(root: Path, settings: Settings, only: Sequence[str] = ()) -> Iterator[Document]:
    """The files of a layout gold set's folder, converted with no model: every element
    keeps the text the file gives, and no request is sent."""
    from readablestem.models.fake import FakeVisionClient
    from readablestem.pipeline import layout, run

    for pdf in sorted((root / "files").glob("*.pdf")):
        if only and pdf.stem not in only:
            continue
        data = pdf.read_bytes()
        try:
            output = run.convert_pdf(data, settings=settings, client=FakeVisionClient())
        except layout.LayoutError as exc:
            yield Document(pdf.stem, error=exc.code)
            continue
        yield Document(pdf.stem, data, output.result)


# -------------------------------------------------------------------------------- the run


def _page_pictures(opened: Any) -> Callable[[int], bytes]:
    return lambda page: page_audit.page_png(opened, page)


def _output_pictures(
    renderer: ChromiumRenderer, result: DocumentResult
) -> Callable[[int], bytes | None]:
    """The picture of one page's output: the part of the document's HTML that holds the
    page's elements, as a browser draws it."""

    def picture(page: int) -> bytes | None:
        ids = [element.id for element in result.elements if element.page == page]
        return renderer.part_png(result.html, ids, width_px=page_audit.OUTPUT_WIDTH_PX)

    return picture


def audit_documents(
    documents: Iterable[Document],
    client: VisionClient,
    settings: Settings,
    *,
    renderer: ChromiumRenderer | None = None,
    say: Callable[[str], None] = print,
) -> Run:
    """Audit every page of every document. A spend cap ends the run: the pages after it
    are "not audited", and `stopped` names the cap."""
    import pymupdf

    run = Run()
    for document in documents:
        result = document.result
        if result is None or result.origin != "pdf":
            # (a document made from LaTeX has no page of a PDF to set its output against)
            run.documents.append(Audited(document.name, error=document.error or "not_a_pdf"))
            continue
        pages = page_audit.pages_of(result)
        if run.stopped is not None:
            late = [
                PageVerdict(page=page, verdict="not_audited", by="code", reason="cap_reached")
                for page in pages
            ]
            run.documents.append(Audited(document.name, late))
            continue
        with pymupdf.open(stream=document.pdf, filetype="pdf") as opened:
            done = page_audit.audit(
                client,
                settings,
                result,
                _page_pictures(opened),
                page_audit.page_words(opened),
                _output_pictures(renderer, result) if renderer is not None else None,
                pages,
            )
        run.documents.append(Audited(document.name, done.verdicts))
        run.stopped = done.stopped
        say(
            f"{document.name}: {len(pages)} pages, {done.count('flagged')} flagged, "
            f"{done.count('not_audited')} not audited"
        )
    return run


# ----------------------------------------------------------------------------- the report


def report(
    run: Run,
    *,
    set_name: str,
    settings: Settings,
    date: str,
    client_label: str,
    with_output_picture: bool,
) -> dict[str, Any]:
    """What the run found. Content-free."""
    from readablestem.models.prompts import load_prompt

    verdicts = [(doc.name, v) for doc in run.documents for v in doc.verdicts]
    asked = [v for _, v in verdicts if v.by == "model"]
    cost = round(sum(v.cost_usd for v in asked), 6)
    return {
        "task": "page_audit",
        "date": date,
        "set": set_name,
        "client": client_label,
        "model": settings.page_audit_model,
        "effort": settings.anthropic_effort_page_audit or page_audit.DEFAULT_EFFORT,
        "prompt_version": load_prompt(page_audit.PROMPT_NAME).version,
        "with_output_picture": with_output_picture,
        "per_page_ceiling_usd": settings.page_audit_max_usd_per_page,
        "shadow": True,  # nothing reads these verdicts: no output or decision changes
        "documents": sum(1 for doc in run.documents if not doc.error),
        "pages": len(verdicts),
        "verdicts": dict(sorted(Counter(v.verdict for _, v in verdicts).items())),
        "answered_by": dict(sorted(Counter(v.by for _, v in verdicts).items())),
        "not_audited_by_reason": dict(
            sorted(Counter(v.reason for _, v in verdicts if v.verdict == "not_audited").items())
        ),
        "findings": dict(
            sorted(
                Counter(
                    f"{finding.code}:{finding.severity}"
                    for _, v in verdicts
                    for finding in v.findings
                ).items()
            )
        ),
        "requests": len(asked),
        "cost_usd": cost,
        "usd_per_asked_page": round(cost / len(asked), 6) if asked else None,
        "stopped": run.stopped,
        "skipped": [
            {"document": doc.name, "reason": doc.error} for doc in run.documents if doc.error
        ],
        "rows": [{"document": name} | v.model_dump(mode="json") for name, v in verdicts],
    }


def format_markdown(found: dict[str, Any]) -> str:
    counts = found["verdicts"]
    lines = [
        f"# Page audit in shadow: {found['set']} ({found['date']})",
        "",
        f"Model {found['model']} at {found['effort']} effort, prompt {found['prompt_version']}, "
        f"client {found['client']}; "
        + (
            "page picture, listing and output picture"
            if found["with_output_picture"]
            else "page picture and listing"
        )
        + ".",
        "",
        f"- documents: {found['documents']}, pages: {found['pages']}",
        f"- pass: {counts.get('pass', 0)}, flagged: {counts.get('flagged', 0)}, "
        f"not audited: {counts.get('not_audited', 0)}",
        f"- requests: {found['requests']}, cost of their answers: ${found['cost_usd']:.4f}"
        + (
            f" (${found['usd_per_asked_page']:.4f} a page asked)"
            if found["usd_per_asked_page"] is not None
            else ""
        ),
    ]
    if found["not_audited_by_reason"]:
        why = ", ".join(f"{k} {n}" for k, n in found["not_audited_by_reason"].items())
        lines.append(f"- not audited, by reason: {why}")
    if found["findings"]:
        what = ", ".join(f"{k} {n}" for k, n in found["findings"].items())
        lines.append(f"- findings, by code and severity: {what}")
    for skipped in found["skipped"]:
        lines.append(f"- left out: {skipped['document']} ({skipped['reason']})")
    if found["stopped"]:
        lines.append(
            f"- STOPPED: the spend cap ({found['stopped']}) was reached; the pages after it "
            "are not audited"
        )
    lines += [
        "",
        "One run's verdicts. They change no output and raise no decision; the bars are "
        "judged on pages whose faults a person marked, over two runs of one input.",
        "",
    ]
    return "\n".join(lines)
