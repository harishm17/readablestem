"""heading_step.heading_fixes and layout.heading_levels (deterministic, no model)."""

from __future__ import annotations

from pathlib import Path

from readablestem.core.types import Element, Kind, Status
from readablestem.pipeline.heading_step import heading_fixes
from readablestem.pipeline.layout import (
    REASON_BOLD_BODY,
    REASON_LARGER_FONT,
    REASON_NUMBERED,
    heading_levels,
)

FIXTURES = Path(__file__).parent / "fixtures" / "layout"


def heading(
    order: int, text: str, size: float, *reasons: str, bold: bool = True, kind: Kind = Kind.HEADING
) -> Element:
    return Element(
        id=f"p1-e{order}",
        kind=kind,
        page=0,
        bbox=(72.0, 72.0 + 20 * order, 300.0, 86.0 + 20 * order),
        order=order,
        text=text,
        font_size=size,
        bold=bold,
        reasons=list(reasons),
    )


def large(order: int, text: str, size: float, *extra: str) -> Element:
    return heading(order, text, size, REASON_LARGER_FONT, *extra)


def test_heading_levels_cluster_sizes_and_put_bold_lines_below():
    elements = [
        large(0, "Chapter 2 Motion", 22.0),
        large(1, "2.1 Position", 16.0),
        large(2, "2.2 Velocity", 16.5),  # within 5 % of 16: same level
        large(3, "Units", 13.0),
        heading(4, "Worked example", 11.0, REASON_BOLD_BODY),
        heading(5, "2.2.1 Average speed", 11.0, REASON_BOLD_BODY, REASON_NUMBERED),
        heading(6, "2.2.1.1 Sign of velocity", 11.0, REASON_BOLD_BODY, REASON_NUMBERED),
        heading(7, "Not a heading", 30.0, REASON_LARGER_FONT, kind=Kind.PARAGRAPH),
    ]
    assert heading_levels(elements) == {
        "p1-e0": 1,
        "p1-e1": 2,
        "p1-e2": 2,
        "p1-e3": 3,
        "p1-e4": 4,
        "p1-e5": 4,
        "p1-e6": 5,  # one number deeper than "2.2.1" among the bold headings
    }


def by_id(elements: list[Element]):
    fixes = heading_fixes(elements)
    return {f.element_id: f for f in fixes}


def failed_codes(fix) -> list[str]:
    return [c.code for c in fix.failed_checks]


def test_empty_heading_text_abstains_with_a_category_code():
    (fix,) = heading_fixes([large(0, "   ", 20.0)])
    assert (fix.status, fix.reason, fix.heading, fix.confidence, fix.review) == (
        Status.ABSTAINED,
        "heading.empty_text",
        None,
        None,
        "required",
    )


# ----------------------------------------------------------------- T2-8: lines of one heading
