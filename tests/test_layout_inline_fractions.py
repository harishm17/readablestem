"""Inline stacked fractions in running text are one equation each (T2-1).

A fraction set in a line of text has its numerator and denominator in a smaller size on
either side of a thin bar. The text extractor cut it into lines by position: the
numerator in the line above, the denominator in the next, each among the words of its
line. The maths came out as two pieces ("P(x)" and "Q(x)"), or, for plain digits (1/5),
not at all, and the paragraph read "of 15 and 12". The pages are made here with PyMuPDF,
the way `test_layout_equations.py` does.
"""

from __future__ import annotations

import pymupdf

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout
from readablestem.pipeline.layout import analyze

BODY = 11.0
SMALL = 8.0


def one_page(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def fraction(page: pymupdf.Page, x: float, baseline: float, top: str, bottom: str) -> float:
    """A text-style fraction beside a line of text on `baseline`; returns where it ends."""
    width = max(pymupdf.get_text_length(t, fontname="tiit", fontsize=SMALL) for t in (top, bottom))
    axis = baseline - 3.0
    page.insert_text((x, axis - 2.0), top, fontsize=SMALL, fontname="tiit")
    page.draw_line((x - 0.5, axis), (x + width + 0.5, axis), width=0.5)
    page.insert_text((x, axis + SMALL + 0.5), bottom, fontsize=SMALL, fontname="tiit")
    return x + width + 2.0


def equations(elements: list[Element]) -> list[Element]:
    return [e for e in elements if e.kind == Kind.EQUATION]


def spaceless(text: str | None) -> str:
    return "".join((text or "").split())


def prose_page(fill) -> list[Element]:
    def page_fill(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "A first line of ordinary text for the page.", fontsize=BODY)
        fill(page)
        page.insert_text((72, 260), "A last line of ordinary text for the page.", fontsize=BODY)

    return analyze(one_page(page_fill)).elements


# ------------------------------------------------------------ a binomial: no bar


def binomial(page: pymupdf.Page, x: float, baseline: float, top: str, bottom: str) -> float:
    """ "( top over bottom )": brackets on the baseline, the entries one above the other."""
    page.insert_text((x, baseline), "(", fontsize=BODY)
    inner = x + 5.0
    page.insert_text((inner, baseline - 4.0), top, fontsize=SMALL, fontname="tiit")
    page.insert_text((inner, baseline + 6.0), bottom, fontsize=SMALL, fontname="tiit")
    width = max(pymupdf.get_text_length(t, fontname="tiit", fontsize=SMALL) for t in (top, bottom))
    page.insert_text((inner + width + 1.0, baseline), ")", fontsize=BODY)
    return inner + width + 8.0


def test_a_binomial_keeps_its_brackets_and_both_entries():
    def fill(page):
        page.insert_text((72, 140), "The number of ways to choose is", fontsize=BODY)
        end = binomial(page, 250, 140, "5", "2")
        page.insert_text((end + 3, 140), "and it equals ten.", fontsize=BODY)

    found = equations(prose_page(fill))
    assert [spaceless(e.text) for e in found] == ["(52)"]


# ------------------------------------------------------ half a fraction (T2-11)


def glyphs(text: str, x: float, y0: float, size: float = SMALL) -> layout._Row:
    chars, at = [], x
    for ch in text:
        width = pymupdf.get_text_length(ch, fontname="tiit", fontsize=size)
        chars.append(layout._Char(ch, at, y0, at + width, y0 + size, y0 + size - 1, size, "x", 0))
        at += width
    return layout._Row(chars)


# ------------------------------------------------------ T2-6: dots and neighbours
