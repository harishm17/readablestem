"""heading_step.heading_fixes and layout.heading_levels (deterministic, no model)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from readablestem.core.types import Element, Kind, Status
from readablestem.pipeline.heading_step import ACCEPT_AT, heading_fixes, repair_outline
from readablestem.pipeline.layout import (
    REASON_BOLD_BODY,
    REASON_LARGER_FONT,
    REASON_LIST_NUMBER,
    REASON_NUMBERED,
    REASON_NUMBERED_LINE,
    analyze,
    heading_levels,
    section_number,
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


@pytest.mark.parametrize(
    ("raw", "repaired"),
    [
        ([], []),
        ([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]),
        ([1, 3], [1, 2]),
        ([1, 6, 6, 2], [1, 2, 2, 2]),
        ([3], [2]),  # first heading is h1 or h2
        ([3, 4], [2, 3]),
        ([1, 3, 3, 2, 3], [1, 2, 2, 2, 3]),  # siblings stay siblings
        ([2, 1, 3], [2, 1, 2]),  # going back up is fine
        ([1, 2, 4, 2, 4], [1, 2, 3, 2, 3]),
    ],
)
def test_repair_outline(raw, repaired):
    assert repair_outline(raw) == repaired


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


def test_heading_levels_never_exceed_six():
    elements = [large(i, f"Heading {i}", 30.0 - 2 * i) for i in range(8)]
    assert list(heading_levels(elements).values()) == [1, 2, 3, 4, 5, 6, 6, 6]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2.3 Speed and Velocity", (2, False)),
        ("2.3.1. Average speed", (3, False)),
        ("Chapter 2: Motion", (1, False)),
        ("2 Motion in One Dimension", (1, False)),
        ("1. Measure the ramp", (1, True)),
        ("3) Time the fall", (1, True)),
        ("IV. Results", (1, False)),
        ("A) Units", (1, True)),
        ("A ball rolls down", None),
        ("I think so", None),
        ("2024 was a good year", None),
        ("Speed and velocity", None),
    ],
)
def test_section_number(text, expected):
    assert section_number(text) == expected


def by_id(elements: list[Element]):
    fixes = heading_fixes(elements)
    return {f.element_id: f for f in fixes}


def failed_codes(fix) -> list[str]:
    return [c.code for c in fix.failed_checks]


def test_every_heading_is_accepted_and_uncertain_ones_carry_notes():
    elements = [
        large(0, "Chapter 2 Motion", 22.0, REASON_NUMBERED),
        large(1, "Velocity", 16.0),
        heading(2, "Worked example", 11.0, REASON_BOLD_BODY),
        heading(3, "2.3.1 Units of speed", 11.0, REASON_BOLD_BODY, REASON_NUMBERED),
        heading(4, "2.4 Acceleration", 11.0, REASON_NUMBERED_LINE, REASON_NUMBERED, bold=False),
        large(5, "KINEMATICS SUMMARY", 16.0),
        large(6, "Ex.", 16.0),
        heading(7, "1. Setup", 11.0, REASON_BOLD_BODY, REASON_LIST_NUMBER),
        large(8, "1. Introduction", 16.0, REASON_LIST_NUMBER),
    ]
    fixes = by_id(elements)
    # Every label is used once, so no decision is required: a single uncertain heading
    # is an optional note (its confidence and checks say why).
    expected = {
        "p1-e0": (0.95, []),
        "p1-e1": (0.90, []),
        "p1-e2": (0.60, ["heading.weak_evidence"]),
        "p1-e3": (0.88, []),
        "p1-e4": (0.88, []),
        "p1-e5": (0.75, ["heading.all_caps"]),
        "p1-e6": (0.70, ["heading.very_short"]),
        "p1-e7": (0.45, ["heading.weak_evidence", "heading.list_like"]),
        "p1-e8": (0.90, []),  # "1. Introduction" in a large font is fine
    }
    for element_id, (confidence, codes) in expected.items():
        fix = fixes[element_id]
        assert (fix.status, fix.confidence, failed_codes(fix)) == (
            Status.ACCEPTED,
            confidence,
            codes,
        ), element_id
        assert fix.review == ("optional" if codes else "none"), element_id
        assert fix.group_key is None and fix.group_size is None
        if not codes:
            assert fix.confidence is not None and fix.confidence >= ACCEPT_AT


def test_repeated_uncertain_labels_make_one_group_decision():
    elements = [
        large(0, "Motion", 18.0),
        large(1, "Teacher Support", 14.0),
        heading(2, "Solution", 11.0, REASON_BOLD_BODY),
        heading(3, "Teacher Support", 11.0, REASON_BOLD_BODY),
        large(4, "Procedure", 14.0),
        large(5, "Teacher Support", 14.0),
        large(6, "Discussion", 14.0),
        heading(7, "teacher  support:", 11.0, REASON_BOLD_BODY),  # same label, normalised
        large(8, "Review", 14.0),
        heading(9, "Teacher Support", 10.0, REASON_BOLD_BODY),  # another style: not grouped
        large(10, "Check", 14.0),
        heading(11, "Teacher Support", 11.0, REASON_BOLD_BODY),
    ]
    fixes = by_id(elements)
    members = ["p1-e3", "p1-e7", "p1-e11"]
    for element_id in members:
        fix = fixes[element_id]
        assert (fix.status, fix.review, fix.group_key, fix.group_size) == (
            Status.NEEDS_REVIEW,
            "required",
            "hg-p1-e3",
            3,
        ), element_id
        assert fix.heading is not None and fix.heading.level == 3
    # Certain copies of the same words are not part of it, nor is a label used once.
    assert fixes["p1-e1"].status is Status.ACCEPTED and fixes["p1-e1"].group_key is None
    for element_id in ("p1-e2", "p1-e9"):
        assert fixes[element_id].status is Status.ACCEPTED
        assert fixes[element_id].review == "optional"
        assert fixes[element_id].group_key is None
    assert re.fullmatch(r"hg-p\d+-e\d+", fixes["p1-e3"].group_key or "")


@pytest.mark.parametrize(
    ("text", "required"),
    [
        ("Given a binomial, write it in expanded form.", True),
        ("Can factorials always be found using a calculator?", True),
        ("Remember the order of operations:", True),
        ("Solve a system by substitution.)", True),  # a closing bracket after the mark
        ("Solve each equation, and give a reason for every step", False),  # no end mark
        ("Answer:", False),  # a label, not a sentence
        ("Definition.", False),
        ("Worked example", False),
    ],
)
def test_a_bold_line_that_reads_as_a_sentence_is_a_required_decision(text, required):
    (fix,) = heading_fixes([heading(0, text, 11.0, REASON_BOLD_BODY)])
    assert (fix.review == "required") is required
    assert (fix.status is Status.NEEDS_REVIEW) is required
    assert ("heading.sentence_like" in failed_codes(fix)) is required
    assert fix.heading is not None and fix.heading.text == text  # written as a heading either way


def test_a_sentence_in_a_large_font_or_with_a_section_number_is_a_heading():
    elements = [
        large(0, "Why does the sign change?", 16.0),
        heading(1, "2.3 What is speed?", 11.0, REASON_BOLD_BODY, REASON_NUMBERED),
    ]
    assert [(f.status, f.review) for f in heading_fixes(elements)] == [
        (Status.ACCEPTED, "none"),
        (Status.ACCEPTED, "none"),
    ]


def test_repeated_sentence_like_lines_are_each_a_decision():
    elements = [
        heading(0, "Take a break when stuck:", 11.0, REASON_BOLD_BODY),
        large(1, "Review", 14.0),
        heading(2, "Take a break when stuck:", 11.0, REASON_BOLD_BODY),
    ]
    fixes = heading_fixes(elements)
    assert [f.review for f in fixes] == ["required", "none", "required"]
    assert all(f.group_key is None for f in fixes)


def test_a_heading_with_the_same_text_and_level_as_the_one_above_becomes_bold_text():
    elements = [
        large(0, "Motion", 18.0),
        large(1, "Teacher Support", 14.0),
        large(2, "Teacher Support", 14.0),  # the same box title again, at the same level
        large(3, "Teacher Demonstration", 14.0),
        large(4, "teacher  demonstration:", 14.0),  # same label, normalised
        large(5, "Teacher Demonstration", 14.0),  # a third copy repeats the second
        large(6, "Summary", 14.0),
    ]
    fixes = by_id(elements)
    assert list(fixes) == [f"p1-e{i}" for i in range(7)]  # one fix per heading, in order
    for element_id in ("p1-e2", "p1-e4", "p1-e5"):
        fix = fixes[element_id]
        assert (fix.status, fix.review, fix.reason, fix.heading) == (
            Status.ACCEPTED,
            "none",
            "repeats_heading_above",
            None,
        ), element_id
        assert fix.group_key is None and not fix.pending
    # The first copy stays a heading, and the repeats take no part in the outline.
    assert [fixes[f"p1-e{i}"].heading.level for i in (0, 1, 3, 6)] == [1, 2, 2, 2]


def test_the_same_text_one_level_down_is_a_real_sub_heading():
    """OpenStax sets "Teacher Support" as a box title and again, smaller, as its first
    line: the book's own outline has both (h3 then h4), so both stay headings."""
    elements = [
        large(0, "Motion", 18.0),
        large(1, "Teacher Support", 14.0),
        large(2, "Teacher Support", 12.0),
        heading(3, "Teacher Support", 11.0, REASON_BOLD_BODY),  # lower still
    ]
    fixes = by_id(elements)
    assert all(f.reason is None and f.heading is not None for f in fixes.values())
    assert [f.heading.level for f in fixes.values()] == [1, 2, 3, 4]


def test_a_heading_with_other_text_between_two_copies_is_not_a_repeat():
    elements = [
        large(0, "Motion", 18.0),
        large(1, "Summary", 14.0),
        heading(2, "Summary", 11.0, REASON_BOLD_BODY, kind=Kind.PARAGRAPH),  # text between
        large(3, "Summary", 14.0),
    ]
    fixes = heading_fixes(elements)  # the paragraph is in the list, between the copies
    assert all(f.reason is None for f in fixes)
    assert [f.heading.level for f in fixes if f.heading] == [1, 2, 2]


def test_the_repeated_heading_is_written_as_bold_text_in_the_html():
    from readablestem.pipeline.assemble import render_html

    elements = [
        large(0, "Motion", 18.0),
        large(1, "Teacher Support", 14.0),
        large(2, "Teacher Support", 14.0),
        large(3, "Summary", 14.0),
    ]
    html = render_html(
        title="T", lang="en", elements=elements, fixes=heading_fixes(elements), crops={}
    )
    main = html[html.index("<main>") : html.index("</main>")]
    assert main.count("<h2") == 2 and "<h3" not in main  # Teacher Support once, Summary
    assert '<p id="p1-e2"><strong>Teacher Support</strong></p>' in main


def test_repeated_certain_headings_are_not_grouped():
    elements = [large(0, "Summary", 16.0), large(1, "Summary", 16.0)]
    assert [(f.status, f.review, f.group_key) for f in heading_fixes(elements)] == [
        (Status.ACCEPTED, "none", None),
        (Status.ACCEPTED, "none", None),
    ]


def test_outline_repair_is_a_check_and_keeps_strong_headings_accepted():
    elements = [
        large(0, "Kinematics", 22.0),
        large(1, "Constant acceleration", 13.0),  # raw level 3: the h2 size appears later
        large(2, "Summary", 16.0),
    ]
    fixes = heading_fixes(elements)
    assert [f.heading.level for f in fixes if f.heading] == [1, 2, 2]
    repaired = fixes[1]
    assert failed_codes(repaired) == ["heading.level_repaired"]
    assert repaired.status == Status.ACCEPTED and repaired.confidence == 0.87
    assert repaired.review == "optional"  # a level is cheap to change later
    assert failed_codes(fixes[0]) == [] and failed_codes(fixes[2]) == []


def test_fixes_follow_reading_order_skip_other_kinds_and_clean_text():
    elements = [
        large(2, "Second   heading", 16.0),
        heading(1, "A paragraph", 11.0, kind=Kind.PARAGRAPH),
        large(0, " First heading ", 22.0),
    ]
    fixes = heading_fixes(elements)
    assert [(f.element_id, f.heading.text, f.heading.level) for f in fixes if f.heading] == [
        ("p1-e0", "First heading", 1),
        ("p1-e2", "Second heading", 2),
    ]
    assert all(f.kind == Kind.HEADING and f.calls == [] and f.cost_usd == 0 for f in fixes)


def test_empty_heading_text_abstains_with_a_category_code():
    (fix,) = heading_fixes([large(0, "   ", 20.0)])
    assert (fix.status, fix.reason, fix.heading, fix.confidence, fix.review) == (
        Status.ABSTAINED,
        "heading.empty_text",
        None,
        None,
        "required",
    )


def test_check_codes_are_content_free_categories():
    elements = [large(0, "SECRET STUDENT NAME", 20.0), heading(1, "Ex", 11.0, REASON_BOLD_BODY)]
    for fix in heading_fixes(elements):
        for check in fix.checks:
            assert re.fullmatch(r"heading\.[a-z_]+", check.name)
            assert check.code is None or re.fullmatch(r"heading\.[a-z_]+", check.code)
            assert check.severity == "warning"


def test_worked_example_fixture_outline():
    layout_ = analyze((FIXTURES / "worked_example.pdf").read_bytes())
    fixes = heading_fixes(layout_.elements)
    assert [(f.heading.level, f.heading.text, f.status) for f in fixes if f.heading] == [
        (1, "Kinematics Review", Status.ACCEPTED),
        (2, "Constant Acceleration", Status.ACCEPTED),  # h1 -> h3 in the source, repaired
        (3, "Worked example", Status.ACCEPTED),  # bold body-size line: an optional note
        (2, "Summary", Status.ACCEPTED),
    ]
    assert failed_codes(fixes[1]) == ["heading.level_repaired"]
    assert failed_codes(fixes[2]) == ["heading.level_repaired", "heading.weak_evidence"]
    assert [f.review for f in fixes] == ["none", "optional", "optional", "none"]


# ----------------------------------------------------------------- T2-8: lines of one heading


def _bold_lines(lines: list[tuple[str, str, float]]) -> list[Element]:
    """A page with some body text and then bold lines (text, built-in font, size), one
    below the other at a normal line pitch; returns the headings and paragraphs found."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 100.0
    for _ in range(6):
        page.insert_text(
            (72, y), "The cart keeps a constant speed along the whole ramp.", fontsize=11
        )
        y += 15
    y += 12
    for text, font, size in lines:
        page.insert_text((72, y), text, fontsize=size, fontname=font)
        y += size * 1.3
    return [e for e in analyze(doc.tobytes()).elements if e.kind != Kind.PARAGRAPH]


def test_two_bold_lines_in_other_typefaces_are_two_headings():
    found = _bold_lines(
        [("Practice Makes Perfect", "hebo", 11.0), ("Solve a System", "tibo", 11.0)]
    )
    assert [e.text for e in found] == ["Practice Makes Perfect", "Solve a System"]


def test_two_bold_lines_of_other_sizes_are_two_headings():
    found = _bold_lines(
        [("Practice Makes Perfect", "hebo", 11.0), ("Solve a System", "hebo", 11.9)]
    )
    assert [e.text for e in found] == ["Practice Makes Perfect", "Solve a System"]


def test_a_long_bold_heading_wrapped_over_two_lines_is_still_one_heading():
    found = _bold_lines(
        [("Solve a System of Equations", "hebo", 12.0), ("by Substitution", "hebo", 12.0)]
    )
    assert [e.text for e in found] == ["Solve a System of Equations by Substitution"]
