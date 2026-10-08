"""Inline-maths fragments that are not formulas (2026-10-08 smoke test and eval pages).

The OpenStax pages produced inline "equations" that were pieces of something else:
"/h)] = 40" (a plain-text formula cut by a line break), "-49 m" (a quantity with an
italic unit) and "b. - 40 m/s" (a multiple-choice option, now a list item; see
test_layout_lists.py). Real inline maths must still be found, including maths written
as plain text with italic variables.
"""

from __future__ import annotations

import pymupdf
import pytest

from a11y_stem.core.types import Element, Kind
from a11y_stem.pipeline.layout import analyze

BODY = 11.0


def one_page(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def line(page: pymupdf.Page, x: float, y: float, *pieces: tuple[str, str]) -> None:
    """Text pieces (text, base-14 font) set one after another on a baseline."""
    for text, font in pieces:
        page.insert_text((x, y), text, fontsize=BODY, fontname=font)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)


def inline(elements: list[Element]) -> list[str]:
    return [e.text or "" for e in elements if e.kind == Kind.EQUATION]


def test_a_plain_text_formula_cut_by_a_line_break_is_not_an_inline_equation():
    def fill(page):
        line(page, 72, 100, ("Caution students that average speed is [(50 km)/(30 km", "helv"))
        line(page, 72, 115, ("/h)] = 40 km/h, not the average of the two speeds.", "helv"))

    assert inline(analyze(one_page(fill)).elements) == []


def test_a_quantity_with_a_leading_hyphen_and_an_italic_unit_is_not_maths():
    def fill(page):
        line(
            page,
            72,
            100,
            ("Displacement is -15 ", "helv"),
            ("m", "heit"),
            (" and distance is -49 ", "helv"),
            ("m", "heit"),
            (" for this walk.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == []


@pytest.mark.parametrize(
    ("pieces", "expected"),
    [
        ([("We set ", "helv"), ("x", "heit"), (" = 5 at the start.", "helv")], "x = 5"),
        ([("At ", "helv"), ("t", "heit"), (" = 0 s the cart is at rest.", "helv")], "t = 0 s"),
        ([("The speed is ", "helv"), ("v", "heit"), (" + 2 in the end.", "helv")], "v + 2"),
        ([("The change is ", "helv"), ("\u2212", "symb"), ("3 m to the left.", "helv")], None),
    ],
)
def test_prose_maths_with_italic_variables_is_still_found(pieces, expected):
    elements = analyze(one_page(lambda page: line(page, 72, 100, *pieces))).elements
    found = inline(elements)
    if expected is None:
        assert len(found) == 1  # a signed number with a real minus sign is maths
    else:
        assert found == [expected]


def test_a_run_never_starts_with_an_infix_operator_or_ends_with_one():
    def fill(page):
        line(page, 72, 100, ("so ", "helv"), ("b", "heit"), (" - ", "helv"), ("a", "heit"))
        line(page, 72, 115, ("/ and ", "helv"), ("x", "heit"), (" = ", "helv"), ("y", "heit"))
        line(page, 72, 130, ("where the rest of the sentence ends here.", "helv"))

    for text in inline(analyze(one_page(fill)).elements):
        assert text[0] not in "/=+-*\u2212" and text[-1] not in "/=+-*\u2212"
