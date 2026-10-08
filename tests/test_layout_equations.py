"""Display equations in layout.analyze: multi-row regions and equation numbers.

Pins the fixes for defects seen on the OpenStax eval pages (2026-10-08):

- one aligned equation came out as four elements ("v =", "Delta d", "Delta t",
  "= 2000 m - 525 m , 2.8 ... = 250 m/s"), and "Delta d" was cut off the equation below;
- two separate equations stacked close together ("time = distance / v" over
  "distance = v x time") came out as one element;
- equation numbers ("2.4", "(2.4)", "Eq. 2.4") were swallowed into the equation's box and
  text, so the crop sent to the model included them.

Equation numbers now go to `Element.equation_label` and stay out of bbox and text.
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


def prose(page: pymupdf.Page, y: float, text: str) -> None:
    page.insert_text((72, y), text, fontsize=BODY)


def fraction(page, x: float, axis: float, top: str, bottom: str, width: float, size=9.0):
    """A fraction centred on `axis` (the bar's height), numerator and denominator at x."""
    page.insert_text((x, axis - 2.5), top, fontsize=size)
    page.draw_line((x - 2, axis), (x + width, axis), width=0.6)
    page.insert_text((x, axis + size), bottom, fontsize=size)


def equations(elements: list[Element]) -> list[Element]:
    return [e for e in elements if e.kind == Kind.EQUATION]


def nospace(text: str | None) -> str:
    return "".join((text or "").split())


# ---------------------------------------------------------------- one equation, many rows


def aligned_derivation(page: pymupdf.Page, label_x: float) -> None:
    """2-3's worked example: v = (d/t) / = (2000 m - 525 m)/(6.4 s - 0.50 s), 2.8 / = 250 m/s"""
    prose(page, 100, "Substitute the values of the chosen points into the equation:")
    page.insert_text((250, 140), "v", fontsize=12)
    page.insert_text((266, 140), "=", fontsize=12)
    fraction(page, 300, 136, "d", "t", 10)
    page.insert_text((266, 166), "=", fontsize=12)
    fraction(page, 290, 162, "2000 m - 525 m", "6.4 s - 0.50 s", 62)
    page.insert_text((356, 166), ",", fontsize=12)
    page.insert_text((label_x, 166), "2.8", fontsize=12)
    page.insert_text((266, 190), "= 250 m/s", fontsize=12)
    prose(page, 230, "This is an impressively high speed for a car on the ground.")


@pytest.mark.parametrize("label_x", [400, 520])  # merged into the row by baseline, or apart
def test_an_aligned_derivation_is_one_equation_with_its_number_kept_apart(label_x):
    elements = analyze(one_page(lambda page: aligned_derivation(page, label_x))).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]
    equation = elements[1]
    assert equation.equation_label == "2.8"
    assert "2.8" not in (equation.text or "")
    for piece in ("d", "v=", "t", "2000m-525m", "6.4s-0.50s", ",", "=250m/s"):
        assert piece in nospace(equation.text)
    x0, y0, x1, y1 = equation.bbox
    assert x0 <= 250 and y0 <= 128 and y1 >= 186  # all three rows ...
    assert x1 < 395  # ... and not the number
    assert {"fraction bar", "equation number"} <= set(equation.reasons)


def test_a_line_starting_with_a_relation_continues_the_equation_above():
    def fill(page):
        prose(page, 100, "Expanding the square gives three terms:")
        page.insert_text((240, 140), "y = (a + b)(a + b)", fontsize=12)
        page.insert_text((250, 156), "= a a + 2 a b + b b", fontsize=12)
        prose(page, 196, "which is the identity used in the next example on this page.")

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]


def test_whole_equations_with_aligned_relations_are_one_system():
    def fill(page):
        prose(page, 100, "When the acceleration is constant,")
        page.insert_text((240, 140), "v = u + a t", fontsize=12)
        page.insert_text((240, 156), "x = u t + a t t / 2", fontsize=12)
        prose(page, 196, "and eliminating the time gives a third equation for the speed.")

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]


# ---------------------------------------------------------------- separate equations


def test_two_whole_equations_centred_on_their_own_stay_apart():
    """2-2: "time = distance / v_avg ." directly above "distance = v_avg x time"."""

    def fill(page):
        prose(page, 100, "You can rearrange the equation to solve for either quantity")
        page.insert_text((276, 140), "t =", fontsize=12)
        fraction(page, 298, 136, "d", "v", 8, size=12)
        page.insert_text((308, 143), ".", fontsize=12)  # Chromium: a baseline of its own
        page.insert_text((266, 166), "d = v x t", fontsize=12)
        prose(page, 206, "Suppose, for example, a car travels 150 kilometers in 3.2 hours.")

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [
        Kind.PARAGRAPH,
        Kind.EQUATION,
        Kind.EQUATION,
        Kind.PARAGRAPH,
    ]
    first, second = equations(elements)
    assert nospace(first.text) in ("dt=.v", "dt=v.", "t=dv.", "dt=v.")
    assert nospace(second.text) == "d=vxt"
    assert first.bbox[3] < second.bbox[1]


def test_two_numbered_equations_stay_apart_even_with_aligned_relations():
    def fill(page):
        prose(page, 100, "The product and chain rules read")
        page.insert_text((240, 140), "y = u v + w", fontsize=12)
        page.insert_text((500, 140), "(1)", fontsize=12)
        page.insert_text((240, 156), "z = f g + h", fontsize=12)
        page.insert_text((500, 156), "(2)", fontsize=12)
        prose(page, 196, "and both follow from the definition of the derivative.")

    elements = analyze(one_page(fill)).elements
    assert [(e.kind, e.equation_label) for e in elements] == [
        (Kind.PARAGRAPH, None),
        (Kind.EQUATION, "(1)"),
        (Kind.EQUATION, "(2)"),
        (Kind.PARAGRAPH, None),
    ]
    assert [nospace(e.text) for e in equations(elements)] == ["y=uv+w", "z=fg+h"]


def test_equations_at_paragraph_spacing_stay_apart():
    def fill(page):
        prose(page, 100, "The two results are")
        page.insert_text((240, 140), "x = a + b", fontsize=12)
        page.insert_text((240, 170), "y = a - b", fontsize=12)  # a full line of space between
        prose(page, 206, "and they are used again in the next section of the notes.")

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements].count(Kind.EQUATION) == 2


# ---------------------------------------------------------------- equation numbers


@pytest.mark.parametrize("gap", [30, 120])  # one merged row, or two rows on one baseline
def test_a_number_after_a_trailing_relation_is_the_right_hand_side_not_a_label(gap):
    def fill(page):
        prose(page, 100, "The ratio of the two lengths is")
        page.insert_text((240, 140), "x =", fontsize=12)
        page.insert_text((258 + gap, 140), "2.4", fontsize=12)
        prose(page, 180, "which is close to the value the textbook gives for this ramp.")

    elements = analyze(one_page(fill)).elements
    assert not any(e.equation_label for e in elements)


def test_split_labels_cuts_a_number_off_a_maths_row_only_with_a_clear_gap():
    def row(*pieces: tuple[str, float]) -> layout._Row:
        chars = []
        for text, x in pieces:
            for i, c in enumerate(text):
                chars.append(
                    layout._Char(c, x + 6 * i, 90, x + 6 * i + 6, 100, 98, 10.0, "Helv", 0)
                )
        return layout._Row(chars)

    split = layout._split_labels([row(("x = 1 + y", 100), ("(3)", 200))])
    assert [r.text for r in split] == ["x = 1 + y", "(3)"]
    kept = layout._split_labels([row(("x = 1 + y (3)", 100))])  # no gap: part of the maths
    assert [r.text for r in kept] == ["x = 1 + y (3)"]
    prose_row = layout._split_labels([row(("see table", 100), ("2.4", 200))])  # no maths
    assert len(prose_row) == 1


# ---------------------------------------------------------------- stacked fragments


def test_a_subscripted_denominator_is_not_folded_into_the_line_above():
    """2-2: Chromium put "time = distance" (main row and numerator) on one row and the
    denominator "v" with a small "avg" below it. The denominator is mostly script-size
    glyphs, so it was folded into the row above as a superscript and sorted in by x:
    "time = dis v ta avg nce". Only a fragment that is small throughout is a script."""

    def chars(text: str, x: float, baseline: float, size: float) -> list[layout._Char]:
        width = 0.6 * size
        return [
            layout._Char(c, x + width * i, baseline - size, x + width * (i + 1), baseline,
                         baseline, size, "Helv", 0)
            for i, c in enumerate(text)
        ]  # fmt: skip

    host = layout._Row(chars("t =", 260, 140, 12) + chars("dist", 286, 138, 12))
    denominator = layout._Row(chars("v", 288, 150, 12) + chars("avg", 295.2, 152, 8))
    rows = layout._attach_scripts([host, denominator])
    assert sorted(r.text for r in rows) == ["t =dist", "vavg"]


def test_a_folded_script_stays_one_block_after_the_glyphs_it_follows():
    def char(c: str, x: float, size: float = 10.0) -> layout._Char:
        return layout._Char(c, x, 0, x + 5, 10, 9, size, "Helv", 0)

    host = [char("x", 0), char("=", 10), char("a", 20), char("b", 25)]
    denominator = [char("c", 20, 7), char("d", 25, 7)]  # under "ab"
    assert "".join(c.c for c in layout._insert_script(host, denominator)) == "x=abcd"
    subscript = [layout._Char("0", 5.0, 3, 8.5, 10, 11, 7.0, "Helv", 0)]  # right after "x"
    assert "".join(c.c for c in layout._insert_script(host, subscript)) == "x0=ab"


def test_cutting_a_number_off_a_list_item_row_keeps_the_item():
    marker = layout._Marker("(a)", True, 4, 120.0)
    chars = [
        layout._Char(c, 100 + 6 * i, 90, 106 + 6 * i, 100, 98, 10.0, "Helv", 0)
        for i, c in enumerate("(a) x = 1 + y")
    ]
    chars += [
        layout._Char(c, 300 + 6 * i, 90, 306 + 6 * i, 100, 98, 10.0, "Helv", 0)
        for i, c in enumerate("(3)")
    ]
    first, number = layout._split_labels([layout._Row(chars, marker=marker)])
    assert first.marker is marker and number.text == "(3)"


@pytest.mark.parametrize("label", ["Eq. 2.4", "Eqn. 2.4", "Equation 2.4", "Eq. (2.4)"])
@pytest.mark.parametrize("gap", [20, 40])  # close enough to be merged into the maths row
def test_a_prefixed_equation_number_close_to_the_maths_becomes_its_label(label, gap):
    """ "v = d / t   Eq. 2.4": only the last token ("2.4") was tested, and its gap to
    "Eq." is a normal space, so the number stayed in the equation's text and crop."""
    width = pymupdf.get_text_length("v = d / t", fontname="Times-Italic", fontsize=12)

    def fill(page):
        prose(page, 100, "The average velocity over the interval is defined as")
        page.insert_text((250, 140), "v = d / t", fontsize=12, fontname="Times-Italic")
        page.insert_text((250 + width + gap, 140), label, fontsize=12)
        prose(page, 180, "and it is measured in metres per second in this course.")

    [equation] = equations(analyze(one_page(fill)).elements)
    assert equation.equation_label == label
    assert nospace(equation.text) == "v=d/t"
    assert equation.bbox[2] < 250 + width + gap  # the number is not in the crop
