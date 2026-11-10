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

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import analyze


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


def test_a_number_drawn_in_a_maths_font_is_not_cut():
    # Symbol is a maths font: a "number" drawn in it is part of the maths.
    pdf = page_of([("18.", "0 = 5 t - 4.9 t", "symb")])
    equations = [e for e in analyze(pdf).elements if e.kind == Kind.EQUATION]
    assert [e.text for e in equations] == ["18. 0 = 5 t - 4.9 t"]
