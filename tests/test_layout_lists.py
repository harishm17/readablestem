"""List detection in layout.analyze: bullets, ordered markers, nesting, false lists.

A row that starts a list item becomes a `Kind.LIST_ITEM` element with `list_marker` (as
printed), `list_ordered` and `list_level`, and `text` without the marker. Items found in
the 2026-10-08 smoke test: multiple-choice options "a. ... b. ... c. ... d. ..." had been
merged into one paragraph, and "b. - 40 m/s" had become a display equation.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import analyze

BODY = 11.0
PITCH = 15.0


def write(page: pymupdf.Page, x: float, y: float, rows: list[str], **kw) -> float:
    size = kw.pop("fontsize", BODY)
    for row in rows:
        page.insert_text((x, y), row, fontsize=size, **kw)
        y += PITCH * size / BODY
    return y


def one_page(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def html_page(html: str) -> bytes:
    return one_page(lambda page: page.insert_htmlbox(pymupdf.Rect(72, 72, 540, 500), html))


def items(elements: list[Element]) -> list[tuple[str | None, bool | None, int | None, str]]:
    return [
        (e.list_marker, e.list_ordered, e.list_level, e.text or "")
        for e in elements
        if e.kind == Kind.LIST_ITEM
    ]


def kinds(elements: list[Element]) -> list[Kind]:
    return [e.kind for e in elements]


# ---------------------------------------------------------------- bullets


# ---------------------------------------------------------------- ordered markers


def test_openstax_spaced_full_stop_is_part_of_the_marker():
    def fill(page):
        page.insert_text((60, 100), "3", fontsize=BODY)
        page.insert_text((68, 100), ".", fontsize=BODY)
        page.insert_text(
            (76, 100), "In 1999 the orbiter crashed because of a unit error.", fontsize=BODY
        )

    assert items(analyze(one_page(fill)).elements) == [
        ("3.", True, 1, "In 1999 the orbiter crashed because of a unit error.")
    ]


# ---------------------------------------------------------------- option grids


def question_with_options(options: dict[str, tuple[float, float]]) -> bytes:
    """A numbered question with its points at the right margin, then options at x, y."""

    def fill(page):
        page.insert_text((72, 100), "1. Which organelle makes ATP?", fontsize=BODY)
        page.insert_text((490, 100), "(2 points)", fontsize=BODY)
        for text, at in options.items():
            page.insert_text(at, text, fontsize=BODY)
        page.insert_text((72, 190), "2. Name the stages of mitosis.", fontsize=BODY)

    return one_page(fill)


ROW_MAJOR = {
    "(A) Nucleus": (90, 120),
    "(B) Mitochondrion": (300, 120),
    "(C) Ribosome": (90, 135),
    "(D) Golgi body": (300, 135),
}


def test_options_two_across_are_one_list_below_their_question_with_its_points():
    elements = analyze(question_with_options(ROW_MAJOR)).elements
    assert kinds(elements) == [Kind.LIST_ITEM] * 6  # no paragraph for the points
    assert items(elements) == [
        ("1.", True, 1, "Which organelle makes ATP? (2 points)"),
        ("(A)", True, 2, "Nucleus"),
        ("(B)", True, 2, "Mitochondrion"),
        ("(C)", True, 2, "Ribosome"),
        ("(D)", True, 2, "Golgi body"),
        ("2.", True, 1, "Name the stages of mitosis."),
    ]


def test_options_set_down_the_columns_are_read_in_label_order():
    down = {
        "(A) Nucleus": (90, 120),
        "(C) Ribosome": (300, 120),
        "(B) Mitochondrion": (90, 135),
        "(D) Golgi body": (300, 135),
    }
    elements = analyze(question_with_options(down)).elements
    assert [(marker, level, text) for marker, _, level, text in items(elements)[1:5]] == [
        ("(A)", 2, "Nucleus"),
        ("(B)", 2, "Mitochondrion"),
        ("(C)", 2, "Ribosome"),
        ("(D)", 2, "Golgi body"),
    ]


def test_a_points_label_at_the_right_margin_joins_a_question_with_no_options():
    def fill(page):
        page.insert_text((72, 100), "1. Name the stages of mitosis.", fontsize=BODY)
        page.insert_text((490, 100), "(4 points)", fontsize=BODY)

    assert items(analyze(one_page(fill)).elements) == [
        ("1.", True, 1, "Name the stages of mitosis. (4 points)")
    ]


def test_labels_that_are_not_a_full_run_are_left_in_page_order():
    gap = {
        "(A) Nucleus": (90, 120),
        "(C) Ribosome": (300, 120),
        "(B) Mitochondrion": (90, 135),
        "(E) Lysosome": (300, 135),
    }
    elements = analyze(question_with_options(gap)).elements
    assert [e.text for e in elements[1:5]] == [
        "Nucleus",
        "(C) Ribosome",
        "Mitochondrion",
        "(E) Lysosome",
    ]


# ---------------------------------------------------------------- nesting and indents


# ---------------------------------------------------------------- not lists


# ---------------------------------------------------------------- marker readings


# ---------------------------------------------------------------- not lists (review round 2)


@pytest.mark.parametrize("dash", ["-", "\u2013", "\u2014"])
def test_a_spaced_minus_before_a_number_is_not_a_dash_bullet(dash):
    """Two lines starting "- 40 m/s" and "- 20 m/s" matched the sibling rule: the sign
    went into the marker and the values were read out as positive."""
    html = (
        "<p>The velocities of the two carts after the collision are:<br>"
        f"{dash} 40 m/s<br>{dash} 20 m/s<br>so both carts move to the left after they collide.</p>"
    )
    elements = analyze(html_page(html)).elements
    assert not any(e.kind == Kind.LIST_ITEM for e in elements)
    text = " ".join(e.text or "" for e in elements)
    assert f"{dash} 40 m/s" in text and f"{dash} 20 m/s" in text
