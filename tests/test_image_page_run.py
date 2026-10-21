"""`run.convert_pdf` with `FEATURE_IMAGE_PAGES` (ADR 0007): the real layout reads a scan, a
fake model answers, and what comes out is checked: the elements and fixes, the second reading
of the display equations, the fallback to a kept image, the cost, and the output page.
"""

from __future__ import annotations

from typing import Any

import pymupdf
import pytest

from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import ConversionOptions, DocumentResult, Kind, Status
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
    pdf: bytes, *, fake: FakeVisionClient | None = None, on: bool = True, **kw: Any
) -> DocumentResult:
    s = settings(feature_image_pages=on)
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


def test_with_the_flag_off_the_page_is_kept_as_its_image_as_before():
    fake = client()
    result = convert(scan_pdf(), fake=fake, on=False)
    assert result.image_only_pages == [0] and result.image_pages is None
    assert [r.step for r in fake.requests] == []
    [fix] = result.fixes
    assert (fix.status, fix.reason) == (Status.FAILED, "scanned_page")
    assert "Not converted" in result.html and "Converted from an image" not in result.html


def test_the_option_comes_from_the_settings_flag():
    assert ConversionOptions.from_settings(settings(feature_image_pages=True)).image_pages
    assert not ConversionOptions.from_settings(settings()).image_pages


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
    assert (sure.status, sure.review) == (Status.ACCEPTED, "optional")
    assert (unsure.status, unsure.review) == (Status.NEEDS_REVIEW, "required")
    [table] = [e for e in result.elements if e.kind is Kind.TABLE]
    assert (by_reason[table.id].status, by_reason[table.id].review) == (
        Status.NEEDS_REVIEW,
        "required",
    )
    figure = [e for e in result.elements if e.kind is Kind.FIGURE][1]
    assert by_reason[figure.id].review == "optional"
    # What a person must decide: the equation, the unsure paragraph and the table.
    assert result.stats.required_decisions == 3
    assert result.stats.by_review["required"] == 3


def test_the_display_equation_is_read_again_from_its_crop_and_stays_a_decision():
    result = convert(scan_pdf())
    [block_eq] = [e for e in result.elements if e.kind is Kind.EQUATION and e.display == "block"]
    fix = result.fix_for(block_eq.id)
    assert fix is not None and fix.equation is not None
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")  # no text layer to check
    names = {c.name: c for c in fix.checks}
    assert names["equation.no_text_layer"].passed is False
    assert names["equation.page_reading"].passed is True  # the two readings agree
    assert fix.confidence is not None and fix.confidence >= 0.9
    assert block_eq.equation_label == "(1)" and block_eq.text is None
    assert any(c.step == "equation" for c in fix.calls)


def test_when_the_two_readings_disagree_a_person_is_told_so():
    result = convert(scan_pdf(), fake=client(equation="v = d t"))
    [block_eq] = [e for e in result.elements if e.kind is Kind.EQUATION and e.display == "block"]
    fix = result.fix_for(block_eq.id)
    assert fix is not None and fix.review == "required"
    assert any(c.code == "page_reading_mismatch" for c in fix.failed_checks)


def test_the_inline_formula_is_in_the_paragraph_and_in_the_output():
    result = convert(scan_pdf())
    [inline] = [e for e in result.elements if e.kind is Kind.EQUATION and e.display == "inline"]
    fix = result.fix_for(inline.id)
    assert fix is not None and fix.status is Status.ACCEPTED and fix.review == "none"
    assert result.html.count("<math") >= 2  # the inline formula and the display equation


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


def test_cost_is_per_page_and_in_the_totals():
    result = convert(scan_pdf(), fake=client(input_tokens=5000, output_tokens=2000))
    summary = result.image_pages
    assert summary is not None
    assert (summary.pages, summary.converted, summary.fell_back) == (1, 1, 0)
    [record] = summary.per_page
    assert record.page == 0 and record.outcome == "converted" and record.calls == 1
    assert record.cost_usd > 0 and summary.cost_usd == record.cost_usd
    assert record.model == settings().main_model
    assert (record.input_tokens, record.output_tokens) == (5000, 2000)
    assert record.by_kind == {
        "equation": 1,
        "figure": 1,
        "heading": 1,
        "paragraph": 2,
        "table": 1,
    }
    assert (record.required, record.optional) == (3, 3)
    assert record.low_confidence == 1 and record.illegible == 1
    # In the document's numbers, once, and on the page's.
    fix_cost = sum(f.cost_usd for f in result.fixes)
    assert result.stats.cost_usd == pytest.approx(fix_cost + summary.cost_usd)
    assert result.stats.per_page[0].cost_usd >= summary.cost_usd
    assert result.stats.model_calls == summary.calls + sum(len(f.calls) for f in result.fixes)
    assert result.prompt_versions["image_page"] == image_page.PROMPT_VERSION


def test_a_notice_says_the_page_was_converted_from_an_image():
    result = convert(scan_pdf())
    [notice] = [n for n in result.notices if n.code == "image_page_converted"]
    assert (notice.page, notice.review) == (0, "optional") and notice.count == 6


def test_a_typed_page_and_a_scan_in_one_document_keep_their_order():
    result = convert(scan_pdf(typed_first=True))
    pages = [e.page for e in result.elements]
    assert pages == sorted(pages) and set(pages) == {0, 1}
    assert result.image_pages is not None and [r.page for r in result.image_pages.per_page] == [1]
    assert [e.order for e in result.elements] == list(range(len(result.elements)))
    typed = [e for e in result.elements if e.page == 0]
    assert all(image_page.IMAGE_PAGE_REASON not in e.reasons for e in typed)


def test_two_scanned_pages_are_two_calls_and_two_originals():
    fake = client()
    result = convert(scan_pdf(2), fake=fake)
    assert [r.step for r in fake.requests].count("image_page") == 2
    assert result.html.count("<summary>") == 2
    assert "Pages 1 and 2 were converted from images of the pages" in result.html


# ----------------------------------------------------------------------------- fallback


@pytest.mark.parametrize("fail", ["refused", "invalid_output", "timeout", "api_error"])
def test_a_failed_call_leaves_the_page_kept_as_its_image(fail):
    fake = client(fail_models={settings().main_model: fail})
    result = convert(scan_pdf(), fake=fake)
    assert result.image_only_pages == [0]
    [fix] = [f for f in result.fixes if f.reason == "scanned_page"]
    assert fix.status is Status.FAILED
    summary = result.image_pages
    assert summary is not None
    assert (summary.converted, summary.fell_back) == (0, 1)
    assert summary.per_page[0].outcome == fail
    assert summary.per_page[0].cost_usd > 0  # the attempt was paid for, and is counted
    assert result.stats.cost_usd >= summary.cost_usd
    assert "Not converted" in result.html and "Converted from an image" not in result.html


def test_an_answer_with_no_blocks_leaves_the_page_kept():
    result = convert(scan_pdf(), fake=client({"blocks": []}))
    assert result.image_only_pages == [0]
    assert result.image_pages is not None
    assert result.image_pages.per_page[0].outcome == "empty"


def test_an_answer_that_does_not_fit_the_schema_leaves_the_page_kept():
    result = convert(scan_pdf(), fake=client({"blocks": [{"kind": "paragraph"}]}))
    assert result.image_only_pages == [0]
    assert result.image_pages is not None
    assert result.image_pages.per_page[0].outcome == "invalid_output"


def test_a_spend_cap_that_refuses_the_call_leaves_the_page_kept_and_says_why():
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=0.0001), max_usd=0.0001, max_calls=100)
    result = convert(scan_pdf(), fake=client(budget=budget))
    assert result.image_only_pages == [0]
    summary = result.image_pages
    assert summary is not None and summary.stopped_by is not None
    assert summary.per_page[0].outcome == "budget_exceeded"
    assert any(f.reason == "scanned_page" for f in result.fixes)


def test_one_page_failing_does_not_stop_the_others():
    answers = iter([None, PAGE])
    fake = FakeVisionClient(
        responders={
            "image_page": lambda r: next(answers),
            "equation": equation_answer("v = \\frac{d}{t}"),
        }
    )
    options = ConversionOptions(image_pages=True)
    result = run.convert_pdf(
        scan_pdf(2), settings=settings(), client=fake, options=options, concurrency=1
    ).result
    assert result.image_only_pages == [0]  # the first was refused, kept as an image
    assert any(e.page == 1 and e.kind is Kind.HEADING for e in result.elements)
    assert result.image_pages is not None and result.image_pages.converted == 1


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
        e
        for e in result.elements
        if e.kind is Kind.PARAGRAPH and (result.fix_for(e.id) or object).review == "required"  # type: ignore[union-attr]
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
