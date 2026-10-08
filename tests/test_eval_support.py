"""Shared helpers for the eval tests: a tiny gold set on disk, fake inputs, fake steps.

Every eval test runs offline. Pipeline modules owned by other work packages are still
stubs, so the tests replace them with the fakes here (via monkeypatch), built only on
the published contracts: `transcribe`/`describe` call the `VisionClient` with the
rendered PNG and return a `Fix`.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from pydantic import BaseModel

from a11y_stem.config import Settings
from a11y_stem.core.types import (
    Element,
    EquationFix,
    FigureFix,
    Fix,
    Kind,
    Status,
)
from a11y_stem.eval.equations import latex_to_mathml
from a11y_stem.gold.openstax import write_manifest
from a11y_stem.models.base import VisionRequest

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
CC_BY = "http://creativecommons.org/licenses/by/4.0/"
ARCHIVE = "https://openstax.org/apps/archive/20990101.000000"

# Gold equations used across tests: (mathml, display, correct LaTeX).
EQ_V = '<math display="block"><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>'
EQ_X = '<math display="inline"><mi>x</mi><mo>=</mo><mn>2</mn></math>'
EQ_A = '<math display="block"><mi>a</mi><mo>=</mo><mi>b</mi><mo>+</mo><mi>c</mi></math>'
EQ_F = '<math display="block"><mi>F</mi><mo>=</mo><mi>m</mi><mi>a</mi></math>'


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def fake_png(key: str, width: int = 40, height: int = 20) -> bytes:
    """Bytes with a real PNG signature and IHDR size, unique per key (hash-addressable)."""
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height)
    return header + hashlib.sha256(key.encode()).digest()


def base_record(page_slug: str, kind: str, index: int, **fields) -> dict:
    prefix = {"equation": "eq", "figure": "fig", "table": "tab", "heading": "hd"}[kind]
    page_uuid = f"uuid-{page_slug}"
    record = {
        "id": f"openstax/physics/{page_slug}/{prefix}-{index:03d}",
        "source": "openstax",
        "kind": kind,
        "index_on_page": index,
        "book_slug": "physics",
        "book_title": "Physics",
        "page_slug": page_slug,
        "page_title": f"Title of {page_slug}",
        "page_uuid": page_uuid,
        "content_url": f"{ARCHIVE}/contents/book@v1:{page_uuid}.json",
        "source_url": f"https://openstax.org/books/physics/pages/{page_slug}",
        "license_name": "Creative Commons Attribution License",
        "license_url": CC_BY,
        "retrieved_at": "2026-10-06",
        "element_id": None,
        "context": None,
    }
    record.update(fields)
    return record


def equation(page: str, index: int, mathml: str, display: str) -> dict:
    return base_record(page, "equation", index, mathml=mathml, display=display)


def heading(page: str, index: int, level: int, text: str, context: str | None = None) -> dict:
    return base_record(page, "heading", index, level=level, text=text, context=context)


def figure(page: str, index: int, alt: str, caption: str) -> dict:
    url = f"{ARCHIVE}/resources/{page}-{index}"
    image = {"url": url, "alt": alt, "alt_is_empty": not alt, "media_type": "image/png"}
    return base_record(page, "figure", index, caption=caption, images=[image])


def write_gold(root: Path, records: list[dict]) -> Path:
    """Write records as a gold dir (one JSONL per page, _book.json, manifest)."""
    gold = root / "gold"
    book_dir = gold / "openstax" / "physics"
    book_dir.mkdir(parents=True, exist_ok=True)
    pages: dict[str, list[dict]] = {}
    for record in records:
        pages.setdefault(record["page_slug"], []).append(record)
    for slug, page_records in pages.items():
        lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in page_records]
        (book_dir / f"{slug}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta = {
        "slug": "physics",
        "title": "Physics",
        "authors": ["Ada Author"],
        "version": "v1",
        "archive_version": "20990101.000000",
        "license_name": "Creative Commons Attribution License",
        "license_url": CC_BY,
        "details_url": "https://openstax.org/details/books/physics",
    }
    (book_dir / "_book.json").write_text(json.dumps(meta), encoding="utf-8")
    write_manifest(gold)
    return gold


class FakeMedia:
    """EvalMedia without a browser: deterministic fake PNGs and PDFs, calls recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def equation_png(self, record: dict) -> bytes:
        self.calls.append(("equation", record["id"]))
        return fake_png(record["mathml"])

    def figure_png(self, url: str) -> bytes:
        self.calls.append(("figure", url))
        return fake_png(url)

    def page_pdf(self, page_records: list[dict]) -> bytes:
        slug = page_records[0]["page_slug"]
        self.calls.append(("page", slug))
        return b"%PDF-fake " + slug.encode()


# ----------------------------------------------------------------- fake steps (WP3)


class EquationOut(BaseModel):
    latex: str
    confidence: float


class FigureOut(BaseModel):
    alt: str
    confidence: float


def fake_transcribe(
    element: Element, png: bytes, *, client, settings: Settings, context_text=None
) -> Fix:
    """Contract-shaped stand-in for equation_step.transcribe.

    Inline equations start on the cheap model and escalate to the main model below 0.85
    confidence; block equations go straight to the main model. Uses the rendered PNG as
    the request image, so FakeVisionClient answers by image hash.
    """
    display = element.display or "block"

    def ask(model: str):
        request = VisionRequest(
            step="equation",
            model=model,
            prompt_version="equation-test1",
            instructions="Transcribe.",
            images=[png],
        )
        return client.run(request, EquationOut)

    first = settings.main_model if display == "block" else settings.cheap_model
    out, call = ask(first)
    calls = [call]
    if first == settings.cheap_model and out is not None and out.confidence < 0.85:
        out, call = ask(settings.main_model)
        calls.append(call)
    if out is None:
        return Fix(
            element_id=element.id,
            kind=Kind.EQUATION,
            status=Status.FAILED,
            calls=calls,
            reason="model_refused",
        )
    status = Status.ACCEPTED if out.confidence >= 0.85 else Status.NEEDS_REVIEW
    return Fix(
        element_id=element.id,
        kind=Kind.EQUATION,
        status=status,
        confidence=out.confidence,
        equation=EquationFix(
            latex=out.latex,
            mathml=latex_to_mathml(out.latex, display=display),
            speech="",
            display="inline" if display == "inline" else "block",
        ),
        calls=calls,
    )


def fake_describe(
    element: Element, png: bytes, *, client, settings: Settings, context_text=None
) -> Fix:
    """Contract-shaped stand-in for figure_step.describe (abstains on a refusal)."""
    request = VisionRequest(
        step="figure",
        model=settings.main_model,
        prompt_version="figure-test1",
        instructions="Describe.",
        images=[png],
        context_text=context_text,
    )
    out, call = client.run(request, FigureOut)
    if out is None:
        return Fix(
            element_id=element.id,
            kind=Kind.FIGURE,
            status=Status.ABSTAINED,
            calls=[call],
            reason="model_refused",
        )
    return Fix(
        element_id=element.id,
        kind=Kind.FIGURE,
        status=Status.ACCEPTED if out.confidence >= 0.85 else Status.NEEDS_REVIEW,
        confidence=out.confidence,
        figure=FigureFix(figure_type="diagram", alt=out.alt),
        calls=[call],
    )


def eq_fix(element_id: str, latex: str, confidence: float = 0.9) -> Fix:
    """An accepted equation fix whose MathML is latex2mathml's rendering of `latex`."""
    return Fix(
        element_id=element_id,
        kind=Kind.EQUATION,
        status=Status.ACCEPTED,
        confidence=confidence,
        equation=EquationFix(latex=latex, mathml=latex_to_mathml(latex), speech=""),
    )


def test_fake_png_has_a_readable_size_and_is_unique_per_key():
    from a11y_stem.eval.pipeline_eval import _png_size

    assert _png_size(fake_png("a", 40, 20)) == (40, 20)
    assert fake_png("a") != fake_png("b")
    assert _png_size(b"not a png") is None
