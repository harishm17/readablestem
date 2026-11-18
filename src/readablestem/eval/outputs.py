"""`readablestem eval outputs`: what is wrong in the files a student is given.

Made from one measured replay of the corpus (`eval.measure`): every upload converted as the
worker converts a job, under the production profile, with the recorded answers in place of
the provider. Of each document the replay then builds every output the tool gives (the page,
the Word file, the EPUB, the two Canvas files, the Common Cartridge and the Listen
transcript), reads each with its reader and counts what is wrong (`pipeline.output_audit`).
The replay spends nothing; a Word file needs pandoc on the machine.

What a count is. One wrong place in one output: a code (`output_audit.CODES`), the output,
a page and an element id. Nothing of the document's words is kept. The same wrong place in
the page and in five files made from the page is six counts, one per output, because a
student who opens any of them meets it.

What it is measured on. The page as the conversion leaves it, before anyone reviews: its
open decisions are open. So `out_notice_missing` on the page is the number of places where
a draft given to a student shows something undecided with nothing at that place to say so.

Two sets, reported apart. The figures at the top are the uploads whose recording holds every
answer the conversion asks for. An upload with answers missing still gives files (its
unanswered elements are kept as pictures with a flag), so it is audited too, and reported in
a table of its own: its files are ones the tool makes, but not the ones production makes of
that document.

An output that could not be built is not a count: it is named with its code (`failed`), and
nothing is said about what it would have held.

Nothing here gates a release or changes an output. The counts are a baseline and a lane of
the quality ledger. The corpus is a development set, chosen to cover kinds of document and
not as a sample of what people upload.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from readablestem.core.types import DocumentResult
from readablestem.eval import measure
from readablestem.eval.measure import Converted
from readablestem.pipeline import assemble, output_audit
from readablestem.pipeline.output_audit import CODES, OUTPUTS, Facts, Inventory

# What an inventory holds, counted: the size of what was read, so that a count has
# something to stand against.
SIZES = ("blocks", "equations", "pictures", "tables", "links", "notes")
# The folder and course number of the Canvas export. Any would do: they are in addresses,
# which are not read.
_FOLDER, _COURSE = "document-files", "1"
# The Listen page reads the first rows of a table and says how many more there are
# (`listen.MAX_TABLE_ROWS`). The audit reads the transcript with every row, so that what a
# row says is checked for all of them; the cap itself is the Listen page's, by design.
_EVERY_ROW = 1_000_000


def _code(exc: Exception) -> str:
    """Why an output could not be built: the export's own code, or the error's type."""
    return str(getattr(exc, "code", "") or type(exc).__name__)


def build(
    html: str, speeches: Mapping[str, str] | None = None
) -> tuple[dict[str, Inventory], dict[str, str]]:
    """Every output of a student page, built and read: the inventory of each one that could
    be built, and the code of why for each that could not. `speeches`: the stored reading
    of each equation by element id, which the transcript says."""
    from readablestem.pipeline import export_canvas, export_cartridge, export_docx, export_epub
    from readablestem.web import listen

    read: dict[str, Inventory] = {}
    failed: dict[str, str] = {}

    def attempt(output: str, make: Callable[[], Inventory]) -> None:
        try:
            read[output] = make()
        except Exception as exc:  # an output that cannot be built is reported, not raised
            failed[output] = _code(exc)

    attempt(output_audit.HTML, lambda: output_audit.read_html(html))
    attempt(output_audit.DOCX, lambda: output_audit.read_docx(export_docx.export_docx(html).data))
    attempt(
        output_audit.EPUB,
        lambda: output_audit.read_epub(export_epub.build_epub(export_epub.export_epub(html))),
    )
    title = read[output_audit.HTML].title if output_audit.HTML in read else ""
    try:
        canvas = export_canvas.export_canvas(
            html, title=title or assemble.DEFAULT_TITLE, folder=_FOLDER, course_number=_COURSE
        )
    except Exception as exc:
        for output in (output_audit.CANVAS_INDEX, output_audit.CANVAS_PAGE, output_audit.CARTRIDGE):
            failed[output] = _code(exc)
    else:
        attempt(
            output_audit.CANVAS_INDEX,
            lambda: output_audit.read_html(canvas.index_html, output_audit.CANVAS_INDEX),
        )
        attempt(
            output_audit.CANVAS_PAGE,
            lambda: output_audit.read_html(canvas.page_html or "", output_audit.CANVAS_PAGE),
        )
        attempt(
            output_audit.CARTRIDGE,
            lambda: output_audit.read_cartridge(export_cartridge.build_cartridge(canvas)),
        )
    attempt(
        output_audit.TRANSCRIPT,
        lambda: output_audit.read_transcript(
            listen.build_transcript(html, speeches, max_rows=_EVERY_ROW),
            no_reading=listen.NO_READING,
            no_description=listen.NO_DESCRIPTION,
        ),
    )
    return read, failed


def audit_files(html: str, facts: Facts, speeches: Mapping[str, str] | None) -> dict[str, Any]:
    """The output audit of one student page and every file made from it, as plain data
    with none of its words: `places` (each wrong place: code, output, page, element),
    `sizes` (what each output held, counted) and `failed` (the outputs that could not be
    built, with why). `facts`: what the stored result says the page must hold."""
    read, failed = build(html, speeches)
    page = read.get(output_audit.HTML)
    if page is None:
        return {"places": [], "sizes": {}, "failed": failed}
    places = [
        asdict(count)
        for output in OUTPUTS
        if output in read
        for count in output_audit.audit(read[output], page, facts)
    ]
    sizes = {
        output: {name: len(getattr(inventory, name)) for name in SIZES}
        for output, inventory in read.items()
    }
    return {"places": places, "sizes": sizes, "failed": failed}


def audit_document(result: DocumentResult) -> dict[str, Any]:
    """`audit_files` of a converted document: its page, and the readings it stores (the ones
    the page, Listen and the files show, so a reading older rules wrote is made again)."""
    from readablestem.pipeline.speech import refresh_readings

    refresh_readings(result)
    speeches = {
        fix.element_id: fix.equation.speech
        for fix in result.fixes
        if fix.equation is not None and fix.equation.speech
    }
    return audit_files(result.html, output_audit.facts_of(result), speeches)


def _table(measured: Iterable[Converted]) -> dict[str, Any]:
    """The counts of some uploads, added up."""
    counts = {code: dict.fromkeys(OUTPUTS, 0) for code in CODES}
    documents = dict.fromkeys(CODES, 0)  # with at least one, in any output
    sizes = {output: dict.fromkeys(SIZES, 0) for output in OUTPUTS}
    built = dict.fromkeys(OUTPUTS, 0)
    failed: dict[str, Counter[str]] = {output: Counter() for output in OUTPUTS}
    rows: list[dict[str, Any]] = []
    pages = uploads = 0
    for done in measured:
        audit = done.outputs or {}
        uploads += 1
        pages += done.pages
        for place in audit.get("places", []):
            counts[place["code"]][place["output"]] += 1
        for code in {place["code"] for place in audit.get("places", [])}:
            documents[code] += 1
        for output, held in audit.get("sizes", {}).items():
            built[output] += 1
            for name in SIZES:
                sizes[output][name] += held.get(name, 0)
        for output, code in audit.get("failed", {}).items():
            failed[output][code] += 1
        rows.append({"upload": done.upload, "places": len(audit.get("places", []))})
    return {
        "uploads": uploads,
        "pages": pages,
        "built": built,
        "failed": {
            output: dict(sorted(codes.items())) for output, codes in failed.items() if codes
        },
        "counts": counts,
        "documents": documents,
        "sizes": sizes,
        "by_upload": sorted(rows, key=lambda row: (-row["places"], row["upload"])),
    }


def report(measured: Sequence[Converted]) -> dict[str, Any]:
    """The figures of a measured replay, as plain data: the `metrics` of an outputs record
    (`measure.record`), which `render` prints."""
    not_measured = sorted(done.upload for done in measured if done.error or done.outputs is None)
    audited = [done for done in measured if not done.error and done.outputs is not None]
    short = {done.upload: done.missed for done in audited if done.missed}
    return {
        "uploads": {
            "measured": sum(1 for done in audited if not done.missed),
            "answers_missing": dict(sorted(short.items())),
            "not_measured": not_measured,
        },
        "whole": _table(done for done in audited if not done.missed),
        "answers_missing": _table(done for done in audited if done.missed),
    }


def _counts_table(table: dict[str, Any]) -> list[str]:
    rows = [
        (
            code,
            *(table["counts"][code][output] for output in OUTPUTS),
            sum(table["counts"][code].values()),
            table["documents"][code],
        )
        for code in CODES
    ]
    return measure.table(("kind", *OUTPUTS, "all", "documents"), rows)


def render(record: dict[str, Any], worst: int = 10) -> str:
    """An outputs record as text. `worst`: how many uploads to name."""
    metrics = record["metrics"]
    uploads, whole, apart = metrics["uploads"], metrics["whole"], metrics["answers_missing"]
    total = sum(sum(by.values()) for by in whole["counts"].values())
    lines = [
        f"the built files: {uploads['measured']} uploads, {whole['pages']} pages, "
        f"{total} wrong places in {len(OUTPUTS)} outputs",
        *measure.header(record),
    ]
    if uploads["not_measured"]:
        lines.append("not measured: " + ", ".join(uploads["not_measured"]))
    lines += ["", "wrong places by kind and by output (documents: with at least one):"]
    lines += _counts_table(whole)
    lines += ["", "what was read (the built files of the uploads above, added up):"]
    lines += measure.table(
        ("output", "built", "could not be built", *SIZES),
        [
            (
                output,
                whole["built"][output],
                ", ".join(f"{code} {n}" for code, n in whole["failed"].get(output, {}).items())
                or "0",
                *(whole["sizes"][output][name] for name in SIZES),
            )
            for output in OUTPUTS
        ],
    )
    named = [row for row in whole["by_upload"] if row["places"]][:worst]
    if named:
        lines += ["", f"the uploads with the most wrong places (of {whole['uploads']}):"]
        lines += measure.table(
            ("upload", "places"), [(row["upload"], row["places"]) for row in named]
        )
    if apart["uploads"]:
        missing = ", ".join(f"{name} ({n})" for name, n in uploads["answers_missing"].items())
        lines += [
            "",
            f"apart, {apart['uploads']} uploads whose recording lacks answers "
            f"({apart['pages']} pages): {missing}",
        ]
        lines += _counts_table(apart)
    return "\n".join(lines)
