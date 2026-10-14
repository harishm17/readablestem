"""An exercise number ("18.", "4)") in a text font is not part of the equation after it.

A numbered exercise that is only an equation ("18. 0 = 5 t - 4.9 t") used to be one maths
row: the number was sent to the model inside the equation's crop, stood in its text layer,
and a model that read it as part of the maths (or left it out) was marked down for it. The
number is cut off the row, kept as text in front of the equation, and out of the crop and
the text layer. The pages are made here with PyMuPDF (our own text, base-14 fonts), the way
`test_layout_equations.py` does.
"""

from __future__ import annotations

import pymupdf
import pytest

from a11y_stem.core.types import Element, Kind
from a11y_stem.pipeline import layout
from a11y_stem.pipeline.layout import analyze


def page_of(rows: list[tuple[str, str, str]]) -> bytes:
    """Prose, then one row per (number, equation, font of the number), then prose."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Solve each equation for the unknown value.", fontsize=11)
    y = 140.0
    for number, equation, number_font in rows:
        x = 72.0
        if number:
            page.insert_text((x, y), number, fontsize=11, fontname=number_font)
            x += pymupdf.get_text_length(number + " ", fontname=number_font, fontsize=11) + 8
        page.insert_text((x, y), equation, fontsize=11, fontname="tiit")
        y += 40
    page.insert_text((72, y + 20), "That is all for this page of exercises.", fontsize=11)
    return doc.tobytes()


def kinds(elements: list[Element]) -> list[str]:
    return [e.kind.value for e in elements]


@pytest.mark.parametrize("number", ["18.", "4)", "7."])
def test_a_lone_numbered_equation_keeps_its_number_as_text_before_the_equation(number):
    pdf = page_of([(number, "0 = 5 t - 4.9 t", "helv")])
    elements = analyze(pdf).elements
    assert kinds(elements) == ["paragraph", "paragraph", "equation", "paragraph"]
    label, equation = elements[1], elements[2]
    assert label.text == number
    assert equation.text == "0 = 5 t - 4.9 t"
    assert not (equation.text or "").startswith(number[:-1])
    assert label.bbox[2] < equation.bbox[0]  # the number sits left of the maths
    assert equation.equation_label is None  # an exercise number is not an equation number


def test_the_number_is_out_of_the_crop_and_the_text_layer_of_the_equation_region():
    pdf = page_of([("18.", "0 = 5 t - 4.9 t", "helv")])
    equation = next(e for e in analyze(pdf).elements if e.kind == Kind.EQUATION)
    x0, y0, x1, y1 = equation.bbox
    padded = pymupdf.Rect(x0 - layout.CROP_PAD, y0 - layout.CROP_PAD, x1 + 2, y1 + 2)
    with pymupdf.open(stream=pdf) as doc:
        assert "18" not in doc[0].get_text("text", clip=padded)
        assert "5" in doc[0].get_text("text", clip=padded)


def test_a_numbered_prose_exercise_is_left_alone():
    pdf = page_of([("18.", "A car travels 5 m in 2 s", "helv")])
    elements = analyze(pdf).elements
    assert "equation" not in kinds(elements)
    assert kinds(elements) == ["paragraph", "list_item", "paragraph"]  # a one-item list, as before


def test_a_number_drawn_in_a_maths_font_is_not_cut():
    # Symbol is a maths font: a "number" drawn in it is part of the maths.
    pdf = page_of([("18.", "0 = 5 t - 4.9 t", "symb")])
    equations = [e for e in analyze(pdf).elements if e.kind == Kind.EQUATION]
    assert [e.text for e in equations] == ["18. 0 = 5 t - 4.9 t"]


def test_a_remainder_that_is_not_a_display_equation_does_not_cut():
    elements = analyze(page_of([("18.", "x", "helv")])).elements
    assert [e.text for e in elements][1] == "18. x"  # one paragraph, as before


def test_a_decimal_number_is_not_an_exercise_number():
    # "2.5" and "12" are not "N." or "N)": left as they were (a bare equation number is
    # told apart from the maths elsewhere, by where it sits).
    pdf = page_of([("2.5", "x = 3 t + 4", "helv"), ("12", "y = 3 t + 4", "helv")])
    texts = [e.text for e in analyze(pdf).elements if e.kind == Kind.EQUATION]
    assert texts == ["2.5 x = 3 t + 4", "12 y = 3 t + 4"]


def test_numbered_exercises_in_a_list_stay_a_list_with_inline_equations():
    rows = [("17.", "v = 3 t + 4", "helv"), ("18.", "0 = 5 t - 4.9 t", "helv"),
            ("19.", "x = a + b c", "helv")]  # fmt: skip
    elements = analyze(page_of(rows)).elements
    assert kinds(elements).count("list_item") == 3
    assert [e.list_marker for e in elements if e.kind == Kind.LIST_ITEM] == ["17.", "18.", "19."]
    assert all(e.display == "inline" for e in elements if e.kind == Kind.EQUATION)


def test_split_exercise_numbers_marks_the_number_row_as_text():
    def chars(text: str, x: float, font: str) -> list[layout._Char]:
        out = []
        for ch in text:
            width = pymupdf.get_text_length(ch, fontname=font, fontsize=11)
            out.append(layout._Char(ch, x, 100, x + width, 111, 109, 11.0, font, 0))
            x += width
        return out

    row = layout._Row(
        chars("18.", 72, "Helvetica") + chars("  0 = 5 t - 4.9 t", 86, "Times-Italic")
    )
    number, rest = layout._split_exercise_numbers([row])
    assert (number.exercise_number, number.clean) == (True, "18.")
    assert rest.clean == "0 = 5 t - 4.9 t" and not rest.exercise_number
    layout._classify_row(number)
    assert number.kind == "text"


def fraction_exercise_page(number: str) -> bytes:
    """An exercise that is only a stacked fraction: the number alone on a row above the
    numerator (OpenStax prints it so), the bar between numerator and denominator."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Decompose each expression.", fontsize=11)
    page.insert_text((72, 146), number, fontsize=11, fontname="helv")
    page.insert_text((84, 157), "5x + 16", fontsize=8, fontname="tiit")
    page.draw_line((72, 161), (118, 161), width=0.7)
    page.insert_text((72, 169), "x2 + 10x + 24", fontsize=8, fontname="tiit")
    page.insert_text((72, 230), "That is all for this page of exercises.", fontsize=11)
    return doc.tobytes()


@pytest.mark.parametrize("number", ["6.", "14)"])
def test_a_number_alone_above_a_stacked_fraction_is_not_part_of_the_equation(number):
    elements = analyze(fraction_exercise_page(number)).elements
    equations = [e for e in elements if e.kind == Kind.EQUATION]
    assert len(equations) == 1
    assert not (equations[0].text or "").startswith(number[:-1])
    assert "5x" in (equations[0].text or "") and "x2" in (equations[0].text or "")
    assert number in [e.text for e in elements if e.kind != Kind.EQUATION]


def test_a_lone_number_row_is_marked_by_its_shape_only():
    def row(text: str, font: str) -> layout._Row:
        x, out = 72.0, []
        for ch in text:
            width = pymupdf.get_text_length(ch, fontname=font, fontsize=11)
            out.append(layout._Char(ch, x, 100, x + width, 111, 109, 11.0, font, 0))
            x += width
        return layout._Row(out)

    kept = layout._split_exercise_numbers([row("6.", "Helvetica"), row("2.5", "Helvetica")])
    assert [r.exercise_number for r in kept] == [True, False]
    symbol = layout._split_exercise_numbers([row("6.", "Symbol")])
    assert not symbol[0].exercise_number  # a maths font: part of the maths
