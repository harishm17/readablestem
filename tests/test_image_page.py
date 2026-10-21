"""Image pages (ADR 0007): the structured output, how its blocks become elements and fixes,
the review tiers, the headings, the call and its failure paths.

No model is asked: `FakeVisionClient` answers with the payloads below. The page pictures are
generated (a scan is a page rendered to a JPEG and set in a PDF with nothing else on it).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from image_page_support import block, cell, math, png_of, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.core.types import (
    IMAGE_PAGE_ORIGINAL_REASON,
    IMAGE_PAGE_REASON,
    Element,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_images, heading_step, image_page, layout
from readablestem.pipeline.image_page import ImagePageOutput

BOX = (0.0, 0.0, 612.0, 792.0)


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def parsed(*blocks: dict[str, Any]) -> ImagePageOutput:
    return ImagePageOutput.model_validate({"blocks": list(blocks)})


def build(*blocks: dict[str, Any], png: bytes | None = None) -> image_page.Built:
    built = image_page.build(0, parsed(*blocks), box=BOX, png=png)
    assert built is not None
    return built


def by_kind(built: image_page.Built, kind: Kind) -> list[Element]:
    return [e for e in built.elements if e.kind is kind]


# --------------------------------------------------------------------------- the schema


def test_a_page_answer_parses_with_every_kind_of_block():
    out = parsed(
        block("heading", level=1, runs=[text("Kinematics")]),
        block("paragraph", runs=[text("The speed "), math("v"), text(" is small.")]),
        block("list_item", marker="1.", level=1, runs=[text("Measure d.")]),
        block("equation", latex="v = \\frac{d}{t}", label="(2.4)"),
        block("figure", description="A cart on a ramp.", caption="Figure 1"),
        block(
            "table",
            rows=[
                {
                    "cells": [
                        {"text": "t", "latex": "", "header": "col", "row_span": 1, "col_span": 1},
                    ]
                }
            ],
        ),
    )
    assert [b.kind for b in out.blocks] == [
        "heading",
        "paragraph",
        "list_item",
        "equation",
        "figure",
        "table",
    ]


def test_the_schema_is_strict():
    with pytest.raises(ValidationError):  # an unknown field
        ImagePageOutput.model_validate({"blocks": [{**block("paragraph"), "extra": 1}]})
    with pytest.raises(ValidationError):  # a number sent as text
        ImagePageOutput.model_validate({"blocks": [block("paragraph", x0="10")]})
    with pytest.raises(ValidationError):  # a confidence nobody defined
        ImagePageOutput.model_validate({"blocks": [block("paragraph", confidence="certain")]})
    with pytest.raises(ValidationError):  # a kind nobody defined
        ImagePageOutput.model_validate({"blocks": [block("footnote")]})
    with pytest.raises(ValidationError):  # a field missing
        ImagePageOutput.model_validate({"blocks": [{"kind": "paragraph"}]})


# -------------------------------------------------------------------- text blocks and tiers


def test_a_confident_paragraph_is_an_optional_note():
    built = build(block("paragraph", runs=[text("Speed is distance over time.")]))
    [paragraph] = by_kind(built, Kind.PARAGRAPH)
    fix = built.fixes[paragraph.id]
    assert paragraph.text == "Speed is distance over time."
    assert paragraph.image_confidence == "high"
    assert IMAGE_PAGE_REASON in paragraph.reasons
    assert (fix.kind, fix.status, fix.review) == (Kind.PARAGRAPH, Status.ACCEPTED, "optional")
    assert [c.name for c in fix.failed_checks] == ["image_page.no_text_layer"]
    assert not fix.pending and fix.optional_note


@pytest.mark.parametrize("confidence", ["medium", "low"])
def test_an_unsure_paragraph_is_a_required_decision(confidence):
    built = build(block("paragraph", confidence=confidence, runs=[text("Some words.")]))
    fix = built.fixes[by_kind(built, Kind.PARAGRAPH)[0].id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert fix.pending
    assert f"read_{confidence}" in {c.code for c in fix.failed_checks}


def test_an_illegible_marker_makes_a_confident_paragraph_a_required_decision():
    built = build(
        block("paragraph", runs=[text("The force is [illegible] newtons.")]),
        block("paragraph", runs=[text("A clear sentence.")]),
    )
    first, second = by_kind(built, Kind.PARAGRAPH)
    assert (built.fixes[first.id].status, built.fixes[first.id].review) == (
        Status.NEEDS_REVIEW,
        "required",
    )
    assert "illegible" in {c.code for c in built.fixes[first.id].failed_checks}
    assert built.fixes[second.id].review == "optional"
    assert built.record.illegible == 1


def test_a_list_item_keeps_its_marker_level_and_kind():
    built = build(
        block("list_item", marker="a)", level=1, runs=[text("First.")]),
        block("list_item", marker="", level=2, runs=[text("Nested bullet.")]),
    )
    first, second = by_kind(built, Kind.LIST_ITEM)
    assert (first.list_marker, first.list_ordered, first.list_level) == ("a)", True, 1)
    assert (second.list_marker, second.list_ordered, second.list_level) == ("\u2022", False, 2)
    assert built.fixes[first.id].kind is Kind.PARAGRAPH  # one review kind for all prose


def test_inline_formulas_are_inline_equations_inside_their_paragraph():
    built = build(
        block(
            "paragraph", runs=[text("The speed"), math("v = 5.0\\ \\text{m/s}"), text("is small.")]
        )
    )
    [paragraph] = by_kind(built, Kind.PARAGRAPH)
    [equation] = by_kind(built, Kind.EQUATION)
    assert paragraph.text == f"The speed \u27e6{equation.id}\u27e7 is small."
    assert (equation.display, equation.parent_id) == ("inline", paragraph.id)
    assert equation.bbox == paragraph.bbox and image_page.NO_CROP_REASON in equation.reasons
    fix = built.fixes[equation.id]
    assert fix.status is Status.ACCEPTED and fix.review == "none"
    assert fix.equation is not None and "<math" in fix.equation.mathml
    # It is the paragraph that is asked about: optional here, nothing of its own.
    assert built.fixes[paragraph.id].review == "optional"


def test_a_formula_that_does_not_validate_stays_in_the_text_and_asks_a_person():
    built = build(
        block("paragraph", runs=[text("Take "), math("\\frac{a}{"), text(" of it.")]),
    )
    [paragraph] = by_kind(built, Kind.PARAGRAPH)
    assert by_kind(built, Kind.EQUATION) == []
    assert paragraph.text == "Take [\\frac{a}{] of it."
    fix = built.fixes[paragraph.id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert "inline_math_invalid" in {c.code for c in fix.failed_checks}


def test_a_forbidden_latex_command_never_becomes_mathml():
    built = build(block("paragraph", runs=[text("x"), math("\\input{/etc/passwd}")]))
    assert by_kind(built, Kind.EQUATION) == []
    assert built.fixes[by_kind(built, Kind.PARAGRAPH)[0].id].review == "required"


def test_text_from_the_model_cannot_fake_an_equation_marker():
    built = build(block("paragraph", runs=[text("See \u27e6p1-e9\u27e7 and\u200b more\x00.")]))
    [paragraph] = by_kind(built, Kind.PARAGRAPH)
    assert "\u27e6" not in (paragraph.text or "") and "\u27e7" not in (paragraph.text or "")
    assert "\u301ap1-e9\u301b" in (paragraph.text or "")
    assert "\u200b" not in (paragraph.text or "") and "\x00" not in (paragraph.text or "")


def test_a_block_with_nothing_in_it_is_dropped_and_a_page_of_them_is_nothing():
    assert (
        image_page.build(
            0,
            parsed(block("paragraph", runs=[text("   ")]), block("equation", latex="  ")),
            box=BOX,
            png=None,
        )
        is None
    )
    built = build(block("paragraph", runs=[]), block("paragraph", runs=[text("Real text.")]))
    assert [e.text for e in by_kind(built, Kind.PARAGRAPH)] == ["Real text."]


def test_ids_orders_and_the_original_page_come_in_reading_order():
    built = build(
        block("heading", level=1, runs=[text("Title")]),
        block("paragraph", runs=[text("Body.")]),
    )
    assert [e.id for e in built.elements] == ["p1-e0", "p1-e1", "p1-e2"]
    assert [e.order for e in built.elements] == [0, 1, 2]
    original = built.elements[0]
    assert original.kind is Kind.FIGURE and IMAGE_PAGE_ORIGINAL_REASON in original.reasons
    assert original.bbox == BOX
    assert original.id not in built.fixes  # nothing to decide about it


# ------------------------------------------------------------------------- equations


def test_a_display_equation_goes_to_the_equation_step_with_the_page_reading():
    built = build(block("equation", latex="v = \\frac{d}{t}", label=" (2.4) "))
    [equation] = by_kind(built, Kind.EQUATION)
    assert equation.display == "block" and equation.equation_label == "(2.4)"
    assert equation.text is None  # there is no text layer to hold
    assert equation_images.REASON in equation.reasons and IMAGE_PAGE_REASON in equation.reasons
    assert (equation.page_status, equation.page_latex) == ("span", "v = \\frac{d}{t}")
    assert equation.id not in built.fixes  # decided by the equation step, from the crop


def test_a_display_equation_whose_latex_does_not_validate_has_no_second_reading():
    built = build(block("equation", latex="\\frac{a}{"))
    [equation] = by_kind(built, Kind.EQUATION)
    assert equation.page_status is None and equation.page_latex is None
    assert equation.id not in built.fixes  # the crop is still read, once


def test_an_equation_without_a_usable_box_is_the_page_readers_proposal_and_a_decision():
    built = build(block("equation", latex="E = mc^2", x0=500, x1=500))
    [equation] = by_kind(built, Kind.EQUATION)
    assert image_page.NO_BOX_REASON in equation.reasons and equation.bbox == BOX
    assert equation.page_status is None
    fix = built.fixes[equation.id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert fix.equation is not None and fix.equation.latex == "E = mc^2"
    assert {"no_text_layer", "single_reading_no_box"} <= {c.code for c in fix.failed_checks}


def test_an_unusable_box_and_unusable_latex_keeps_the_equation_as_an_image():
    built = build(block("equation", latex="\\frac{a}{", y0=300, y1=300))
    fix = built.fixes[by_kind(built, Kind.EQUATION)[0].id]
    assert (fix.status, fix.reason, fix.review) == (Status.FAILED, "invalid_latex", "required")
    assert built.record.kept_as_image == 1


# ----------------------------------------------------------------------------- tables


def test_a_table_is_a_required_decision_whatever_the_confidence():
    built = build(
        block(
            "table",
            caption="Table 1 Speeds",
            rows=[
                {"cells": [cell("Car", "col"), cell("Speed", "col")]},
                {"cells": [cell("A"), cell("5 m/s")]},
            ],
        )
    )
    [table] = by_kind(built, Kind.TABLE)
    fix = built.fixes[table.id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert fix.table is not None and (fix.table.grid.rows, fix.table.grid.cols) == (2, 2)
    assert fix.table.grid.source == "model" and fix.table.grid.caption == "Table 1 Speeds"
    assert table.caption == "Table 1 Speeds"
    assert "no_text_layer" in {c.code for c in fix.failed_checks}
    assert not any(c.code == "no_pdf_text" for c in fix.checks)  # nothing to compare with


def test_a_table_whose_cell_maths_does_not_convert_cannot_be_accepted_as_it_is():
    bad = cell("x", latex="\\frac{a}{")
    built = build(block("table", rows=[{"cells": [cell("h", "col")]}, {"cells": [bad]}]))
    fix = built.fixes[by_kind(built, Kind.TABLE)[0].id]
    assert fix.status is Status.NEEDS_REVIEW
    assert any(
        c.name == "table.math" and not c.passed and c.severity == "error" for c in fix.checks
    )


def test_a_table_with_no_rows_is_kept_as_an_image():
    built = build(block("table", rows=[]), block("paragraph", runs=[text("Text.")]))
    fix = built.fixes[by_kind(built, Kind.TABLE)[0].id]
    assert (fix.status, fix.reason, fix.review) == (Status.FAILED, "invalid_table", "required")
    assert fix.table is None


# ---------------------------------------------------------------------------- figures


def test_a_confident_figure_is_an_optional_note_and_an_unsure_one_a_decision():
    built = build(
        block("figure", description="A cart rolls down a ramp.", caption="Figure 1 A cart."),
        block("figure", description="A smudged plot.", confidence="low"),
    )
    sure, unsure = by_kind(built, Kind.FIGURE)[1:]  # [0] is the original page
    assert (built.fixes[sure.id].status, built.fixes[sure.id].review) == (
        Status.ACCEPTED,
        "optional",
    )
    assert built.fixes[sure.id].figure is not None
    assert built.fixes[sure.id].figure.alt == "A cart rolls down a ramp."  # type: ignore[union-attr]
    assert (built.fixes[unsure.id].status, built.fixes[unsure.id].review) == (
        Status.NEEDS_REVIEW,
        "required",
    )


def test_a_figure_without_a_description_is_a_decision():
    built = build(block("figure", description="  "))
    fix = built.fixes[by_kind(built, Kind.FIGURE)[1].id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")


# -------------------------------------------------------------------------- the boxes


def test_a_box_is_trimmed_to_the_ink_inside_it():
    # 600 x 800 picture; ink at x 150-450, y 200-240 on a page of 612 x 792 points.
    png = png_of([(150, 200, 450, 240)])
    built = image_page.build(
        0,
        parsed(block("equation", latex="E = mc^2", x0=100, y0=150, x1=800, y1=330)),
        box=BOX,
        png=png,
    )
    assert built is not None
    x0, y0, x1, y1 = by_kind(built, Kind.EQUATION)[0].bbox
    assert 140 < x0 < 160 and 440 < x1 < 480  # the white around the ink is gone (5 px kept)
    assert 188 < y0 < 198 and 238 < y1 < 250  # (points = pixels x 0.99 vertically)


def test_a_box_with_no_ink_keeps_the_grown_box_and_a_picture_that_cannot_be_read_is_fine():
    built = image_page.build(
        0,
        parsed(block("paragraph", runs=[text("Blank scan.")], x0=100, y0=100, x1=500, y1=200)),
        box=BOX,
        png=png_of(),
    )
    assert built is not None
    x0, _, x1, _ = by_kind(built, Kind.PARAGRAPH)[0].bbox
    assert x0 < 100 / 1000 * 612 and x1 > 500 / 1000 * 612  # grown, not trimmed
    plain = image_page.build(
        0,
        parsed(block("paragraph", runs=[text("Text.")])),
        box=BOX,
        png=b"not a picture",
    )
    assert plain is not None  # only the trimming is lost


def test_a_box_outside_or_inside_out_is_clamped_or_unusable():
    assert image_page._units(parsed(block("paragraph", x0=-50, x1=2000)).blocks[0]) == (
        0,
        100,
        1000,
        160,
    )
    assert image_page._units(parsed(block("paragraph", x0=700, x1=300)).blocks[0]) is None


# -------------------------------------------------------------------------- headings


def element(eid: str, text_: str, order: int, **kw: Any) -> Element:
    return Element(
        id=eid, kind=Kind.HEADING, page=0, bbox=(0, 0, 10, 10), order=order, text=text_, **kw
    )


def heading_fixes(*blocks: dict[str, Any]) -> tuple[image_page.Built, list[Fix]]:
    built = build(*blocks)
    headings = by_kind(built, Kind.HEADING)
    fixes = heading_step.heading_fixes(headings)
    image_page.tier_headings(fixes, headings)
    return built, fixes


def test_headings_use_the_page_readers_level_and_the_outline_repair():
    _, fixes = heading_fixes(
        block("heading", level=1, runs=[text("Motion")]),
        block("heading", level=3, runs=[text("Average speed")]),
    )
    assert [f.heading.level for f in fixes if f.heading] == [1, 2]  # h1 then h3: repaired to h2


def test_a_confident_heading_is_an_optional_note_and_an_unsure_one_is_a_decision():
    _, fixes = heading_fixes(
        block("heading", level=1, runs=[text("Motion")]),
        block("heading", level=2, confidence="medium", runs=[text("Average speed")]),
        block("heading", level=2, runs=[text("Rate [illegible]")]),
    )
    sure, unsure, illegible = fixes
    assert (sure.status, sure.review) == (Status.ACCEPTED, "optional")
    assert (unsure.status, unsure.review) == (Status.NEEDS_REVIEW, "required")
    assert (illegible.status, illegible.review) == (Status.NEEDS_REVIEW, "required")
    assert "image_page" in {c.code for c in sure.failed_checks}


def test_a_heading_the_step_asks_about_stays_required():
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.HEADING,
        status=Status.NEEDS_REVIEW,
        heading=HeadingFix(level=2, text="Given x, solve it."),
        review="required",
    )
    heading = element(
        "p1-e1", "Given x, solve it.", 1, image_confidence="high", reasons=[IMAGE_PAGE_REASON]
    )
    image_page.tier_headings([fix], [heading])
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")


def test_a_typed_heading_is_not_touched():
    typed = element("p1-e1", "Typed heading", 1)
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.HEADING,
        status=Status.ACCEPTED,
        heading=HeadingFix(level=1, text="Typed heading"),
        review="none",
    )
    image_page.tier_headings([fix], [typed])
    assert fix.review == "none" and fix.checks == []


# ----------------------------------------------------------------------------- the call


def renders(pdf: bytes, pages: list[int]) -> dict[int, Any]:
    return {n: (lambda n=n: layout.render_page(pdf, n, dpi=60)) for n in pages}


def fake(payload: dict[str, Any] | None, **kw: Any) -> FakeVisionClient:
    return FakeVisionClient(responders={"image_page": lambda request: payload}, **kw)


GOOD = {"blocks": [block("paragraph", runs=[text("Hello page.")])]}


def test_the_request_sends_the_picture_and_no_text():
    request = image_page.build_request(settings(), b"\x89PNG-picture")
    assert request.step == "image_page"
    assert request.images == [b"\x89PNG-picture"] and request.context_text is None
    assert request.model == settings().main_model  # IMAGE_PAGES_MODEL unset
    assert request.effort == image_page.DEFAULT_EFFORT
    assert "no text layer" in request.instructions
    assert request.prompt_version == image_page.PROMPT_VERSION
    chosen = settings(image_pages_model="gpt-6-luna", anthropic_effort_image_pages="high")
    request = image_page.build_request(chosen, b"x")
    assert (request.model, request.effort) == ("gpt-6-luna", "high")


def test_pages_are_read_and_a_page_that_cannot_be_drawn_is_reported():
    pdf = scan_pdf(2)
    client = fake(GOOD)
    pages = {**renders(pdf, [0]), 1: lambda: None}
    result = image_page.run(client, settings(), pages)
    assert result.stopped is None
    assert result.reads[0].output is not None and result.reads[0].outcome == "ok"
    assert (result.reads[1].output, result.reads[1].outcome) == (None, "no_image")
    assert len(client.requests) == 1


def test_a_render_that_raises_is_a_page_with_no_picture():
    def boom() -> bytes:
        raise RuntimeError("nope")

    result = image_page.run(fake(GOOD), settings(), {0: boom})
    assert result.reads[0].outcome == "no_image"


@pytest.mark.parametrize("fail", ["refused", "invalid_output", "timeout", "api_error"])
def test_a_failed_call_is_a_read_with_no_output(fail):
    pdf = scan_pdf()
    client = FakeVisionClient(fail_models={settings().main_model: fail})
    result = image_page.run(client, settings(), renders(pdf, [0]))
    assert (result.reads[0].output, result.reads[0].outcome) == (None, fail)
    assert result.reads[0].calls  # what was paid for stays on the record


def test_an_answer_that_is_not_the_schema_is_invalid_output():
    result = image_page.run(
        fake({"blocks": [{"kind": "paragraph"}]}), settings(), renders(scan_pdf(), [0])
    )
    assert result.reads[0].outcome == "invalid_output"


def test_a_budget_refusal_stops_the_pass_and_the_pages_before_it_stay():
    pdf = scan_pdf(3)
    guard = InMemorySpendGuard(cap_usd=0.02)
    budget = JobBudget(guard=guard, max_usd=0.02, max_calls=100)
    client = fake(GOOD, budget=budget)
    result = image_page.run(client, settings(), renders(pdf, [0, 1, 2]), concurrency=1)
    assert isinstance(result.stopped, BudgetExceeded)
    outcomes = {n: r.outcome for n, r in result.reads.items()}
    assert "budget_exceeded" in outcomes.values()
    assert set(outcomes) == {0, 1, 2}


def test_the_summary_counts_pages_calls_and_cost_per_page():
    pdf = scan_pdf(2)
    client = FakeVisionClient(
        responders={"image_page": lambda r: GOOD if r.images[0] else None}, input_tokens=4000
    )
    pages = {**renders(pdf, [0]), 1: lambda: None}
    result = image_page.run(client, settings(), pages)
    built = {
        0: image_page.build(0, result.reads[0].output, box=BOX, png=result.reads[0].png)  # type: ignore[arg-type]
    }
    summary = image_page.summarise(result.reads, built, settings(), result.stopped)  # type: ignore[arg-type]
    assert (summary.pages, summary.converted, summary.fell_back, summary.calls) == (2, 1, 1, 1)
    first, second = summary.per_page
    assert first.outcome == "converted" and first.cost_usd > 0 and first.blocks == 1
    assert first.model == settings().main_model and first.input_tokens == 4000
    assert (second.outcome, second.cost_usd, second.calls) == ("no_image", 0.0, 0)
    assert summary.cost_usd == first.cost_usd


def test_an_answer_with_no_usable_block_is_reported_as_empty():
    pdf = scan_pdf()
    result = image_page.run(fake({"blocks": []}), settings(), renders(pdf, [0]))
    read = result.reads[0]
    assert image_page.build(0, read.output, box=BOX, png=read.png) is None  # type: ignore[arg-type]
    summary = image_page.summarise(result.reads, {}, settings(), None)
    assert summary.per_page[0].outcome == "empty" and summary.fell_back == 1


def test_only_scanned_and_handwritten_kept_pages_are_read():
    kept = {
        0: "scanned_page",
        1: "handwritten_page",
        2: "unread_page",
        3: "handwriting_on_page",
    }
    assert image_page.kept_reads(kept) == [0, 1]
