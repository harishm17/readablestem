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
from a11y_stem.pipeline import layout
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


def test_an_inline_crop_stops_short_of_the_comma_that_follows_it():
    """The comma stays in the paragraph after the marker; the crop must not show it, or
    the model transcribes it as well ("d0 , ,")."""

    def fill(page):
        line(page, 72, 100, ("The positions ", "helv"), ("d", "heit"))
        page.insert_text((150.2, 102), "0", fontsize=7)  # the subscript
        line(page, 154.1, 100, (", ", "helv"), ("d", "heit"), (" and the rest.", "helv"))

    pdf = one_page(fill)
    elements = analyze(pdf).elements
    first = next(e for e in elements if e.kind == Kind.EQUATION)
    assert (first.text or "").replace(" ", "") == "d0"
    with pymupdf.open(stream=pdf) as doc:
        chars = [
            c
            for b in doc[0].get_text("rawdict")["blocks"]
            for ln in b["lines"]
            for s in ln["spans"]
            for c in s["chars"]
        ]
    comma = next(c for c in chars if c["c"] == ",")
    subscript = next(c for c in chars if c["c"] == "0")
    right = first.bbox[2] + layout.CROP_PAD  # where the default crop ends
    assert right <= comma["bbox"][0] + 0.01
    assert right >= subscript["bbox"][2] - 0.01  # the subscript is still in it


def test_absolute_value_bars_stay_on_both_sides():
    """ "|" was an infix-only operator, so the opening bar of |x| was trimmed: the
    equation read "x| < 1" and the bar stayed behind in the paragraph text."""

    def fill(page):
        line(
            page,
            72,
            100,
            ("The series converges when ", "helv"),
            ("|", "helv"),
            ("x", "heit"),
            ("| < 1 and the error is at most ", "helv"),
            ("|", "helv"),
            ("r", "heit"),
            ("|", "helv"),
        )
        page.insert_text((385.5, 95), "n", fontsize=7, fontname="heit")  # superscript
        line(page, 72, 115, ("after the first few terms of the series.", "helv"))

    elements = analyze(one_page(fill)).elements
    assert [t.replace(" ", "") for t in inline(elements)] == ["|x|<1", "|r|n"]
    assert "|" not in (elements[0].text or "")


def test_an_unpaired_bar_at_the_edge_of_a_run_is_still_dropped():
    chars = [
        layout._Char(c, 6 * i, 0, 6 * i + 5, 10, 9, 10.0, "Helv-Oblique" if c == "x" else "Helv", 0)
        for i, c in enumerate("|x + 1")
    ]
    row = layout._Row(chars)
    start, end = layout._trim_run(row.chars, layout._CharInfo.of(row), 0, len(chars))
    assert "".join(c.c for c in chars[start:end]) == "x + 1"
