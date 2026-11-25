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
from readablestem.pipeline.layout import analyze, display

PDF = Path(__file__).parent / "fixtures" / "layout" / "latex" / "display_groups.pdf"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
needs_dejavu = pytest.mark.skipif(
    not DEJAVU.exists(), reason="needs DejaVu Sans for the maths signs"
)


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


def one_row(text: str, x0: float, y0: float, step: float = 7.0, size: float = 11.0) -> layout._Row:
    chars = [
        char(c, x0 + i * step, y0, x0 + (i + 1) * step - 1, y0 + size, size)
        for i, c in enumerate(text)
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


# ------------------------------------------------------------ a line set off like a display

PROSE = (
    "Because the tree is a fair-split tree, we can bound the diameter of every cell in the",
    "partition and then sum the bounds over the cells that the query point can reach, which",
    "gives the estimate that we use in the rest of this note. We write the bound out below",
    "and then state what it implies for the error of the estimator.",
)


def prose(page, top: float) -> None:
    for i, line in enumerate(PROSE):
        page.insert_text((72, top + 14 * i), line, fontsize=11)


@needs_dejavu
def test_a_line_of_maths_with_a_word_in_it_set_off_from_its_text_is_a_display():
    """ "Diam(C) <= ... for all i" has a prose word, so it was read as a line of text and cut
    around the word; set in from both edges of the column between paragraphs it is a display."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_font(fontname="dv", fontfile=str(DEJAVU))
    prose(page, 100)
    page.insert_text(
        (190, 190),
        "Diam(C) \u2264 x\u00b2 + y\u00b2 \u2212 2xy for all i",
        fontsize=11,
        fontname="dv",
    )
    prose(page, 220)
    shown = blocks(analyze(doc.tobytes(), display_grouping=True).elements)
    assert len(shown) == 1 and "forall" in nospace(shown[0].text)
    assert not blocks(analyze(doc.tobytes()).elements)


# ------------------------------------------------------------ a piece inside a display


def test_a_piece_that_lies_inside_the_box_of_a_display_is_a_part_of_it():
    """The top of a tall bracket, a line of its own inside the box of the two rows of the
    formula it belongs to; a numbered display beside it stays apart."""
    rows = layout._Line([one_row("Y=F(ab+c)d", 125, 420), one_row("xyz", 140, 438)], [])
    top = layout._Line([one_row("\u239b", 164, 424)], [])
    beside = layout._Line([one_row("abc", 300, 420)], [])
    formula, piece, other = region_of(rows), region_of(top), region_of(beside)
    kept = display._absorb_contained_regions([formula, piece, other])
    assert kept == [formula, other]
    assert len(formula.rows) == 3


def test_a_limit_met_before_its_line_goes_to_the_display_it_stands_in():
    """The upper limit of a sum behind its bracket, "(n", starts a little above the line it
    belongs to, so it is met first and joins the numbered display over it: equation (12) of
    arXiv 2408.09889 page 5 then reached into (13). The piece goes to the display whose line
    it stands in, and the two numbered displays do not overlap."""
    twelve = layout._Line(
        [one_row("e=\u2211wd-\u222bL(X)dX", 181, 595, 9.0)], [], [one_row("(12)", 544, 595)]
    )
    opening = layout._Line([one_row("(n", 287, 608, 6.0, size=7.5)], [])
    closing = layout._Line([one_row(")n", 420, 608, 6.0, size=7.5)], [])
    thirteen = layout._Line(
        [
            one_row("=-L+\u2211wL-L+\u2211w(d-L).", 189, 617, 16.0),
            one_row("1", 360, 610, size=7.5),  # the limit over an integral, folded into the line
        ],
        [],
        [one_row("(13)", 544, 617)],
    )
    upper, lower = display._stack_lines(
        [twelve, opening, closing, thirteen], document=layout._Document(grouping=True)
    )
    assert upper.box == twelve.box and upper.box[3] < lower.box[1]
    assert opening.rows[0] in lower.rows


# ------------------------------------------------------------ limits at the size of the row


def test_the_limits_of_a_sum_set_at_the_size_of_its_row_are_a_part_of_the_display():
    main = layout._Line([one_row("K=\u2211CF=1", 150, 200)], [])
    limits = layout._Line([one_row("k=1", 154, 212)], [])
    assert not display._joins_line(main, main.box, limits)
    assert display._joins_line(main, main.box, limits, grouping=True)
    far = layout._Line([one_row("k=1", 300, 212)], [])  # not under the sign: another display
    assert not display._joins_line(main, main.box, far, grouping=True)


def a_paragraph_whose_last_line_begins_with_a_sum() -> bytes:
    """Text style: the sign hangs on a baseline of its own with the upper limit beside it, and
    the row of the words that go on starts with the lower limit, a sign's width to its right
    (arXiv 2411.02531 page 1). The next paragraph is indented."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    words = "the weights are counted once for every pair of nodes and then added to the total "
    for i, top in enumerate((100, 114, 128)):
        page.insert_text((75, top), (words * 3)[i * 25 : i * 25 + 88], fontsize=12, fontname="tiro")
    writer = pymupdf.TextWriter(page.rect)
    writer.append((75, 138), "∑", font=pymupdf.Font("cjk"), fontsize=12)
    writer.write_text(page)
    page.insert_text((87.5, 135), "n", fontsize=8, fontname="tiit")
    page.insert_text((87.5, 146), "i=1", fontsize=8, fontname="tiit")
    page.insert_text((101, 142), "f", fontsize=12, fontname="tiit")
    page.insert_text((108, 145), "ik", fontsize=8, fontname="tiit")
    page.insert_text((120, 142), "= 0 for k = 1, . . . , d.", fontsize=12, fontname="tiro")
    new = "The latent factors can be read via a set of node observables with the following model:"
    page.insert_text((92, 156), new, fontsize=12, fontname="tiro")
    page.insert_text((75, 170), "factors and the model are fixed before data is seen.", fontsize=12)
    return doc.tobytes()


def test_a_last_line_that_begins_with_a_sum_stays_in_its_paragraph():
    found = analyze(
        a_paragraph_whose_last_line_begins_with_a_sum(),
        region_tree=True,
        inline_joins=True,
        display_grouping=True,
        join_panels=True,
    ).elements
    paragraphs = [e for e in found if e.kind == Kind.PARAGRAPH]
    assert len(paragraphs) == 2
    assert (paragraphs[0].text or "").endswith("d.")
    assert (paragraphs[1].text or "").startswith("The latent factors")
    assert "∑" in "".join(e.text or "" for e in found if e.kind == Kind.EQUATION)


# ------------------------------------------------------------ the setting (off by default)
