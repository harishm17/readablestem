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


def test_a_row_of_two_equations_joined_by_and_is_one_display(elements):
    (found,) = [e for e in blocks(elements) if "0.75" in nospace(e.text)]
    assert nospace(found.text) == "P(A=1|L=1)=0.75andP(A=1|L=0)=0.50."
    assert "0.75" not in " ".join(inline(elements))


def test_a_condition_after_for_all_stays_with_its_formula(elements):
    (found,) = [e for e in blocks(elements) if "foralli" in nospace(e.text)]
    assert nospace(found.text).startswith("0<π(Li)<1")
    assert "i" not in inline(elements)  # the lone "i" no longer stands as an equation of its own


def test_words_that_are_not_connectives_do_not_join_formulas(elements):
    # inline "x = 1 and y = 2" in a sentence, and "a = b  where we assume  c = d" in a display
    assert {"x=1", "y=2", "a=b", "c=d"} <= set(inline(elements))
    assert not [e for e in blocks(elements) if "where" in nospace(e.text)]


def test_the_tail_of_a_sentence_at_the_head_of_a_page_is_not_a_display(elements):
    # "p_1 = 3 and q_1 = 4" starts at the left margin, with no row above it to wrap from
    second = [e for e in elements if e.page == 1]
    assert not [e for e in second if e.kind == Kind.EQUATION and e.display == "block"]
    assert inline(second) == ["p1=3", "q1=4"]


def test_two_numbered_equations_stay_apart(elements):
    labelled = [e for e in blocks(elements) if e.equation_label in ("(2)", "(3)")]
    assert [e.equation_label for e in labelled] == ["(2)", "(3)"]


# ------------------------------------------------------------ a trailing ellipsis


def test_a_trailing_ellipsis_stays_in_an_inline_list_or_chain(elements):
    found = inline(elements)
    assert "j=1,2,3,..." in found
    assert "0>s1>s2>..." in found


@pytest.mark.parametrize(
    "tail", ["3, ...", "3, ..., n", "x > ...", "x > ..."], ids=["list", "closed", "chain", "again"]
)
def test_the_helper_keeps_an_ellipsis_only_after_a_comma_or_a_sign(tail):
    chars = chars_of(f"j = 1, 2, {tail}")
    ends_open = tail.endswith("...")
    assert layout._trailing_ellipsis(chars, 0, len(chars)) is ends_open


def test_a_full_stop_after_three_dots_still_goes():
    chars = chars_of("j = 1, 2, 3, ....")  # an ellipsis, then the sentence's own full stop
    assert not layout._trailing_ellipsis(chars, 0, len(chars))
    chars = chars_of("x = 1.")
    assert not layout._trailing_ellipsis(chars, 0, len(chars))
    chars = chars_of("x...")  # no comma or sign before: not a list
    assert not layout._trailing_ellipsis(chars, 0, len(chars))


def chars_of(text: str) -> list[layout._Char]:
    out, x = [], 0.0
    for c in text:
        out.append(layout._Char(c, x, 0.0, x + 5, 10.0, 8.0, 10.0, "CMMI10", 0))
        x += 5
    return out


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


def test_a_fraction_far_from_the_row_is_not_joined():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (72, 100), "Consequently the integrals simplify, using the relation above,", fontsize=11
    )
    page.insert_text((110, 154), "E", fontsize=12)
    page.insert_text((124, 154), "=", fontsize=12)
    fraction(page, 142, 150, "f(s)", "s", 24)
    fraction(page, 420, 150, "h(s)/g(n)", "s(t - 1)", 60, size=12.0)  # 250 pt on: not a column
    page.insert_text((72, 260), "which is what we wanted to show in this note.", fontsize=11)
    found = [
        e for e in analyze(doc.tobytes(), display_grouping=True).elements if e.kind == Kind.EQUATION
    ]
    assert len(found) == 2


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


def test_a_region_that_does_not_start_with_a_sign_stays_apart():
    first = layout._Line([one_row("E=-1", 110, 329), one_row("xyz", 181, 343)], [])
    lower = layout._Line([one_row("x+y", 142, 361)], [])  # starts with a letter: its own equation
    row_one, row_two = region_of(first), region_of(lower)
    merged = layout._merge_continued_regions([row_one, row_two], [[first], [lower]])
    assert merged == [row_one, row_two]


def test_two_numbered_displays_are_never_merged():
    first = layout._Line([one_row("E=-1", 110, 329), one_row("xyz", 181, 343)], [])
    lead = layout._Line([one_row("=-1", 142, 361)], [])
    row_one, row_two = region_of(first), region_of(lead)
    row_one.labels.append(one_row("(1)", 500, 329))
    row_two.labels.append(one_row("(2)", 500, 361))
    merged = layout._merge_continued_regions([row_one, row_two], [[first], [lead]])
    assert len(merged) == 2


# ------------------------------------------------------------ the setting (off by default)


def test_with_the_setting_off_the_output_is_the_old_output():
    from readablestem.config import Settings

    assert Settings().layout_display_grouping is False
    off = analyze(PDF.read_bytes()).elements
    on = analyze(PDF.read_bytes(), display_grouping=True).elements
    # off: "and" is a paragraph after the two equations, the ellipsis is trimmed, no row joins
    assert [e for e in off if e.kind == Kind.PARAGRAPH and nospace(e.text) == "and"]
    assert "j=1,2,3" in inline(off) and "j=1,2,3,..." not in inline(off)
    assert len(off) > len(on)
    # and the flag does not stay set for the next call
    assert [e.model_dump() for e in analyze(PDF.read_bytes()).elements] == [
        e.model_dump() for e in off
    ]
