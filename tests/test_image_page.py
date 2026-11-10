"""Image pages (ADR 0007): the structured output, how its blocks become elements and fixes,
the review tiers, the headings, the call and its failure paths.

No model is asked: `FakeVisionClient` answers with the payloads below. The page pictures are
generated (a scan is a page rendered to a JPEG and set in a PDF with nothing else on it).
"""

from __future__ import annotations

from typing import Any

import pytest

from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.core.types import (
    Element,
    Fix,
    Kind,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import heading_step, image_page, layout
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


# -------------------------------------------------------------------- text blocks and tiers


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


# ------------------------------------------------------------------------- equations


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


def test_a_table_with_no_rows_is_kept_as_an_image():
    built = build(block("table", rows=[]), block("paragraph", runs=[text("Text.")]))
    fix = built.fixes[by_kind(built, Kind.TABLE)[0].id]
    assert (fix.status, fix.reason, fix.review) == (Status.FAILED, "invalid_table", "required")
    assert fix.table is None


# ---------------------------------------------------------------------------- figures


def test_a_figure_without_a_description_is_a_decision():
    built = build(block("figure", description="  "))
    fix = built.fixes[by_kind(built, Kind.FIGURE)[1].id]
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")


# -------------------------------------------------------------------------- the boxes


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


# ----------------------------------------------------------------------------- the call


def renders(pdf: bytes, pages: list[int]) -> dict[int, Any]:
    return {n: (lambda n=n: layout.render_page(pdf, n, dpi=60)) for n in pages}


def fake(payload: dict[str, Any] | None, **kw: Any) -> FakeVisionClient:
    return FakeVisionClient(responders={"image_page": lambda request: payload}, **kw)


GOOD = {"blocks": [block("paragraph", runs=[text("Hello page.")])]}


def test_a_render_that_raises_is_a_page_with_no_picture():
    def boom() -> bytes:
        raise RuntimeError("nope")

    result = image_page.run(fake(GOOD), settings(), {0: boom})
    assert result.reads[0].outcome == "no_image"


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


def test_a_cell_with_a_span_of_zero_does_not_cost_the_page():
    # The provider's decoder does not enforce bounds: Haiku once wrote col_span 0.
    odd = cell("x", "none", col_span=0, row_span=-1)
    built = build(
        block("table", rows=[{"cells": [cell("h", "col"), cell("g", "col")]}, {"cells": [odd]}]),
        block("paragraph", runs=[text("Still here.")]),
    )
    fix = built.fixes[by_kind(built, Kind.TABLE)[0].id]
    assert fix.table is not None and fix.table.grid.cells
    assert all(c.row_span >= 1 and c.col_span >= 1 for c in fix.table.grid.cells)


# --------------------------------------------------------------------------- the rescue


def stepped(status: Status, reason: str | None = None) -> tuple[Element, Fix]:
    built = build(block("equation", latex="v = \\frac{d}{t}"))
    [equation] = by_kind(built, Kind.EQUATION)
    fix = Fix(element_id=equation.id, kind=Kind.EQUATION, status=status, reason=reason)
    return equation, fix


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (Status.ABSTAINED, "not_equation"),
        (Status.ABSTAINED, "low_confidence"),
        (Status.FAILED, "model_refused"),
    ],
)
def test_a_crop_that_could_not_be_read_keeps_the_page_readers_reading_as_a_decision(status, reason):
    equation, fix = stepped(status, reason)
    rescued = image_page.rescued(fix, equation)
    assert (rescued.status, rescued.review) == (Status.NEEDS_REVIEW, "required")
    assert rescued.reason is None and rescued.equation is not None
    assert rescued.equation.latex == "v = \\frac{d}{t}" and "<math" in rescued.equation.mathml
    assert {"no_text_layer", "single_reading_crop_failed"} <= {
        c.code for c in rescued.failed_checks
    }


def test_the_rescue_leaves_everything_else_alone():
    equation, fix = stepped(Status.ABSTAINED, "budget_job_usd")
    assert image_page.rescued(fix, equation) is fix  # a spend cap is not a bad box
    equation, fix = stepped(Status.NEEDS_REVIEW)
    assert image_page.rescued(fix, equation) is fix
    typed = equation.model_copy(update={"reasons": []})
    failed = fix.model_copy(update={"status": Status.ABSTAINED})
    assert image_page.rescued(failed, typed) is failed  # not an image page
    unreadable = equation.model_copy(update={"page_latex": None})
    assert image_page.rescued(failed, unreadable) is failed  # nothing to keep
