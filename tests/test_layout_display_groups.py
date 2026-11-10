"""Display equations that one printed line or one printed equation is cut into (2026-11-03).

Found on the development papers (docs/eval-dev-baseline-2026-11-03.md, "Display grouping"):

- two formulas set on one line with a word between them ("P_A = ... and P_B = ...", "0 < p < 1
  for all i") came out as two or three elements, with the word left in the paragraph after both;
- the right half of a long aligned row, a bare fraction with no main row to chain it to,
  opened an equation of its own, and a row that starts with "=" joined that one instead of the
  display above;
- a trailing ellipsis ("j = 1, 2, 3, ...", "0 > s_1 > s_2 > ...") was trimmed off an inline
  formula as if it were the full stop.

`tests/fixtures/layout/latex/display_groups.tex` is the TeX source of the PDF the first and last
are checked on (rebuild with `make_pdfs.py`).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout
from readablestem.pipeline.layout import analyze

PDF = Path(__file__).parent / "fixtures" / "layout" / "latex" / "display_groups.pdf"


def nospace(text: str | None) -> str:
    return "".join((text or "").split())


@pytest.fixture(scope="module")
def elements() -> list[Element]:
    return analyze(PDF.read_bytes(), display_grouping=True).elements


def blocks(elements: list[Element]) -> list[Element]:
    return [e for e in elements if e.kind == Kind.EQUATION and e.display == "block"]


def inline(elements: list[Element]) -> list[str]:
    return [nospace(e.text) for e in elements if e.kind == Kind.EQUATION and e.display != "block"]


# ------------------------------------------------------------ two formulas and a word


def test_two_fractions_with_and_between_them_are_one_display_with_one_number(elements):
    (found,) = [e for e in blocks(elements) if "PA" in nospace(e.text)]
    text = nospace(found.text)
    assert (
        "and" in text and "PB" in text and text.index("PA") < text.index("and") < text.index("PB")
    )
    assert found.equation_label == "(1)"
    # the word is part of the display, not a paragraph left after it
    assert not [e for e in elements if e.kind == Kind.PARAGRAPH and nospace(e.text) == "and"]


# ------------------------------------------------------------ a trailing ellipsis


# ------------------------------------------------------------ the right half of a long row


def fraction(page, x: float, axis: float, top: str, bottom: str, width: float, size=9.0):
    page.insert_text((x, axis - 2.5), top, fontsize=size)
    page.draw_line((x - 2, axis), (x + width, axis), width=0.6)
    page.insert_text((x, axis + size), bottom, fontsize=size)


def aligned_row_with_a_bare_fraction_on_the_right() -> bytes:
    """Row one is short; row two is a main row ("= 1/s + 2/t + q(s) ds +") and, 50 pt on, a
    fraction with no main row of its own on the baseline: nothing chains it to the rest."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (72, 100), "Consequently the integrals simplify, using the relation above,", fontsize=11
    )
    page.insert_text((110, 154), "E", fontsize=12)
    page.insert_text((124, 154), "=", fontsize=12)
    fraction(page, 142, 150, "f(s)", "s", 24)
    page.insert_text((124, 180), "=", fontsize=12)
    fraction(page, 142, 176, "1", "s", 24)
    page.insert_text((172, 180), "+", fontsize=12)
    fraction(page, 188, 176, "2", "t", 20)
    page.insert_text((218, 180), "+", fontsize=12)
    page.insert_text((232, 180), "q(s) ds +", fontsize=12)
    fraction(page, 330, 176, "h(s)/g(n)", "s(t - 1)", 60, size=12.0)
    page.insert_text((72, 260), "which is what we wanted to show in this note.", fontsize=11)
    return doc.tobytes()


def test_the_right_half_of_a_row_joins_the_row_it_ends_beside():
    found = [
        e
        for e in analyze(
            aligned_row_with_a_bare_fraction_on_the_right(), display_grouping=True
        ).elements
        if e.kind == Kind.EQUATION
    ]
    assert len(found) == 1
    text = nospace(found[0].text)
    assert "h(s)/g(n)" in text and "q(s)ds" in text
    assert found[0].bbox[2] >= 389  # the box reaches the far fraction


# ------------------------------------------------------------ a row that starts with "="


def char(c: str, x0: float, y0: float, x1: float, y1: float, size: float = 11.0) -> layout._Char:
    return layout._Char(c, x0, y0, x1, y1, y1 - 2, size, "CMMI10", 0)


def one_row(text: str, x0: float, y0: float, step: float = 7.0) -> layout._Row:
    chars = [
        char(c, x0 + i * step, y0, x0 + (i + 1) * step - 1, y0 + 11) for i, c in enumerate(text)
    ]
    return layout._Row(chars, kind="math")


def region_of(*lines: layout._Line) -> layout._Region:
    region = layout._Region(box=layout._union([ln.box for ln in lines]))
    for ln in lines:
        region.rows.extend(ln.rows)
    return region


def test_a_region_that_starts_with_a_relation_joins_the_display_above_it():
    """The pieces of row two come in the order the page has them: the far right fraction
    first, which cannot join row one (it starts 38 pt to its right), then the main row, whose
    first piece "= -1" belongs to row one's display. Both rows are one display."""
    first = layout._Line([one_row("E=-1", 110, 329), one_row("xyz", 181, 343)], [])
    far = layout._Line([one_row("abcdef", 358, 359)], [])
    lead = layout._Line([one_row("=-1", 142, 361), one_row("xyxyxyxy", 163, 361, 8.0)], [])
    row_one, row_two = region_of(first), region_of(far, lead)
    merged = layout._merge_continued_regions([row_one, row_two], [[first], [far, lead]])
    assert merged == [row_one]
    assert len(row_one.rows) == 5


def test_an_equation_number_is_not_a_column_of_the_display_beside_it():
    row = layout._Line([one_row("HF=H+F", 237, 440), one_row("xy", 237, 452)], [])
    number = layout._Line([one_row("14.2", 330, 451)], [])
    column = layout._Line([one_row("xyz", 330, 451)], [])
    assert not layout._beside_row([row], [number], 11.0)
    assert layout._beside_row([row], [column], 11.0)  # the same place, a column of the display


# ------------------------------------------------------------ the setting (off by default)
