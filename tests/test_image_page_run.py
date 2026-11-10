"""`run.convert_pdf` with `FEATURE_IMAGE_PAGES` (ADR 0007): the real layout reads a scan, a
fake model answers, and what comes out is checked: the elements and fixes, the second reading
of the display equations, the fallback to a kept image, the cost, and the output page.
"""

from __future__ import annotations

from typing import Any

import pymupdf

from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import DocumentResult, Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import image_page, run


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


PAGE = {
    "blocks": [
        block("heading", level=1, runs=[text("Motion in one dimension")], y0=40, y1=80),
        block(
            "paragraph",
            runs=[text("The speed"), math("v"), text("is distance over time.")],
            y0=100,
            y1=160,
        ),
        block("equation", latex="v = \\frac{d}{t}", label="(1)", y0=200, y1=260),
        block(
            "paragraph", confidence="low", runs=[text("Mass is [illegible] kg.")], y0=300, y1=340
        ),
        block(
            "table",
            rows=[
                {"cells": [cell("t", "col"), cell("d", "col")]},
                {"cells": [cell("1"), cell("5")]},
            ],
            y0=400,
            y1=500,
        ),
        block("figure", description="A cart on a ramp.", y0=550, y1=700),
    ]
}


def equation_answer(latex: str) -> Any:
    return lambda request: {"is_equation": True, "latex": latex, "confidence": 0.95}


def client(
    payload: dict[str, Any] | None = PAGE,
    equation: str = "v = \\frac{d}{t}",
    **kw: Any,
) -> FakeVisionClient:
    return FakeVisionClient(
        responders={"image_page": lambda r: payload, "equation": equation_answer(equation)},
        **kw,
    )


def convert(
    pdf: bytes,
    *,
    fake: FakeVisionClient | None = None,
    on: bool = True,
    config: dict[str, Any] | None = None,
    **kw: Any,
) -> DocumentResult:
    s = settings(feature_image_pages=on, **(config or {}))
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = fake or client(budget=budget)
    return run.convert_pdf(pdf, settings=s, client=fake, **kw).result


def kinds(result: DocumentResult, page: int = 0) -> list[Kind]:
    return [e.kind for e in result.elements if e.page == page]


def fix_of(result: DocumentResult, kind: Kind, nth: int = 0):
    elements = [
        e
        for e in result.elements
        if e.kind is kind and e.id in {f.element_id for f in result.fixes}
    ]
    return result.fix_for(elements[nth].id)


# ------------------------------------------------------------------------ the flag


# ------------------------------------------------------------------- a converted page


def test_a_scanned_page_becomes_elements_and_fixes_with_a_tier_each():
    result = convert(scan_pdf())
    assert result.image_only_pages == []  # read: no longer kept whole
    assert kinds(result)[:2] == [Kind.FIGURE, Kind.HEADING]  # the original page, then the title
    by_reason = {f.element_id: f for f in result.fixes}
    [heading] = [e for e in result.elements if e.kind is Kind.HEADING]
    assert by_reason[heading.id].review == "optional"
    paragraphs = [e for e in result.elements if e.kind is Kind.PARAGRAPH]
    sure, unsure = (by_reason[p.id] for p in paragraphs)
    # the sure paragraph holds a formula: required whatever the reader's confidence (ADR 0007, 12)
    assert (sure.status, sure.review) == (Status.NEEDS_REVIEW, "required")
    assert {c.code for c in sure.failed_checks} >= {"maths_checked"}
    assert (unsure.status, unsure.review) == (Status.NEEDS_REVIEW, "required")
    [table] = [e for e in result.elements if e.kind is Kind.TABLE]
    assert (by_reason[table.id].status, by_reason[table.id].review) == (
        Status.NEEDS_REVIEW,
        "required",
    )
    figure = [e for e in result.elements if e.kind is Kind.FIGURE][1]
    assert by_reason[figure.id].review == "optional"
    # What a person must decide: the equation, both paragraphs (one holds maths) and the table.
    assert result.stats.required_decisions == 4
    assert result.stats.by_review["required"] == 4


def test_the_output_page_has_the_notice_and_the_original_page_to_open():
    result = convert(scan_pdf())
    html = result.html
    assert "Converted from an image of the page." in html
    assert "Check it against the original." in html
    assert 'class="image-page-note' in html
    assert "<details" in html and "<summary>Show the original page 1</summary>" in html
    assert 'alt="Original of page 1, shown for checking the converted text."' in html
    assert "data:image/jpeg;base64," in html
    assert "<script" not in html
    assert "Page 1 was converted from an image of the page" in html  # the note for students
    assert "verified" not in html.lower()


# ----------------------------------------------------------------------------- fallback


def test_a_spend_cap_that_refuses_the_call_leaves_the_page_kept_and_says_why():
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=0.0001), max_usd=0.0001, max_calls=100)
    result = convert(scan_pdf(), fake=client(budget=budget))
    assert result.image_only_pages == [0]
    summary = result.image_pages
    assert summary is not None and summary.stopped_by is not None
    assert summary.per_page[0].outcome == "budget_exceeded"
    assert any(f.reason == "scanned_page" for f in result.fixes)


def test_a_build_that_raises_leaves_the_page_kept(monkeypatch):
    def broken(*args: Any, **kwargs: Any):
        raise RuntimeError("bad block")

    monkeypatch.setattr(image_page, "build", broken)
    result = convert(scan_pdf())
    assert result.image_only_pages == [0]
    assert result.image_pages is not None and result.image_pages.converted == 0


def test_a_page_that_is_not_a_scan_is_never_sent_to_the_pass():
    out = pymupdf.open()
    page = out.new_page(width=612, height=792)
    for line in range(8):
        page.insert_text((72, 100 + 30 * line), f"A typed line number {line}.", fontsize=12)
    fake = client()
    result = convert(out.tobytes(), fake=fake)
    assert "image_page" not in [r.step for r in fake.requests]
    assert result.image_pages is None and result.image_only_pages == []


# ------------------------------------------------------------- review changes the output


def test_a_rejected_paragraph_is_written_as_the_picture_of_its_region():
    from readablestem.pipeline import assemble

    result = convert(scan_pdf())
    unsure = next(
        e for e in result.elements if e.kind is Kind.PARAGRAPH and "illegible" in (e.text or "")
    )
    fix = result.fix_for(unsure.id)
    assert fix is not None
    fix.status = Status.REJECTED
    fix.reviewed = True
    crops = {unsure.id: _png()}
    html = assemble.render_html(
        title=result.title,
        lang=result.lang,
        elements=result.elements,
        fixes=result.fixes,
        crops=crops,
    )
    assert "Mass is [illegible] kg." not in html
    assert "Text on page 1, not converted to text." in html
    assert "Not converted." in html


def _png() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pixmap.set_rect(pixmap.irect, (30, 30, 30))
    return pixmap.tobytes("png")


# ----------------------------------------------------------------- what the reading left out


# --------------------------------------------------- recovery and the second reading
