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

from a11y_stem.core.types import Element, Kind
from a11y_stem.pipeline import layout
from a11y_stem.pipeline.layout import analyze

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


def test_a_fraction_of_digits_in_a_sentence_is_one_inline_equation():
    def fill(page):
        page.insert_text((72, 140), "The common denominator of", fontsize=BODY)
        end = fraction(page, 216, 140, "1", "5")
        page.insert_text((end + 3, 140), "and its neighbour is their product.", fontsize=BODY)

    elements = prose_page(fill)
    found = equations(elements)
    assert [spaceless(e.text) for e in found] == ["15"]
    assert found[0].display == "inline"
    assert found[0].reasons == ["stacked fraction"]
    paragraph = next(e for e in elements if e.text and "common denominator" in e.text)
    assert (
        paragraph.text
        == f"The common denominator of ⟦{found[0].id}⟧ and its neighbour is their product."
    )


def test_numerator_and_denominator_are_not_split_into_two_equations():
    def fill(page):
        page.insert_text((72, 140), "Decompose", fontsize=BODY)
        end = fraction(page, 130, 140, "P(x)", "Q(x)")
        page.insert_text((end + 3, 140), ", where Q has only linear factors.", fontsize=BODY)

    found = equations(prose_page(fill))
    assert [spaceless(e.text) for e in found] == ["P(x)Q(x)"]
    _, y0, _, y1 = found[0].bbox
    assert y1 - y0 > 2 * SMALL * 0.7  # both the numerator and the denominator are inside


def test_the_neighbours_of_the_fraction_on_the_line_join_its_equation():
    def fill(page):
        page.insert_text((72, 140), "Notice that y = ", fontsize=BODY, fontname="tiit")
        end = fraction(page, 150, 140, "2", "5")
        page.insert_text((end + 1, 140), "x + 5 is linear in the variable.", fontsize=BODY)

    found = equations(prose_page(fill))
    assert len(found) == 1
    assert "y" in spaceless(found[0].text) and "25" in spaceless(found[0].text)


def test_a_fraction_with_another_fraction_in_it_is_left_as_it_was():
    def fill(page):
        page.insert_text((72, 140), "A complex fraction here:", fontsize=BODY)
        x = 220.0
        # outer bar with a small fraction in its numerator
        page.draw_line((x, 141), (x + 30, 141), width=0.5)
        fraction(page, x + 8, 134, "1", "2")
        page.insert_text((x + 12, 150), "4", fontsize=SMALL, fontname="tiit")

    elements = prose_page(fill)
    assert not any("stacked fraction" in e.reasons for e in equations(elements))


def test_a_rule_under_words_is_not_a_fraction():
    def fill(page):
        page.insert_text((72, 140), "An underlined phrase in the text", fontsize=BODY)
        page.draw_line((72, 143), (200, 143), width=0.5)

    assert equations(prose_page(fill)) == []


def test_a_stacked_fraction_keeps_the_sizes_and_baselines_of_its_line():
    def fill(page):
        page.insert_text((72, 140), "Half of the pool is", fontsize=BODY)
        end = fraction(page, 190, 140, "1", "2")
        page.insert_text((end + 3, 140), "full and the rest is empty.", fontsize=BODY)

    elements = prose_page(fill)
    paragraphs = [e for e in elements if e.kind == Kind.PARAGRAPH]
    assert len(paragraphs) == 3  # first line, the sentence with its equation, last line
    assert "Half of the pool is ⟦" in (paragraphs[1].text or "")
    assert (paragraphs[1].text or "").endswith("full and the rest is empty.")


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


def test_brackets_around_one_line_of_text_are_not_a_pair():
    def fill(page):
        page.insert_text((72, 140), "A sentence with (an aside) in it and x_1 here.", fontsize=BODY)
        page.insert_text((72, 154), "(see 7) and another line of ordinary words.", fontsize=BODY)

    assert equations(prose_page(fill)) == []


def test_the_period_of_an_exercise_number_is_not_taken_into_a_binomial():
    def fill(page):
        page.insert_text((62, 140), "40.", fontsize=SMALL)
        page.insert_text((63, 150), "(", fontsize=SMALL)
        page.insert_text((66, 148), "12", fontsize=5.2, fontname="tiit")
        page.insert_text((66, 155), "6", fontsize=5.2, fontname="tiit")
        page.insert_text((72, 150), ")!", fontsize=SMALL)

    elements = prose_page(fill)
    texts = [spaceless(e.text) for e in equations(elements)]
    assert all(not t.startswith("40") for t in texts)


# ------------------------------------------------------ half a fraction (T2-11)


def glyphs(text: str, x: float, y0: float, size: float = SMALL) -> layout._Row:
    chars, at = [], x
    for ch in text:
        width = pymupdf.get_text_length(ch, fontname="tiit", fontsize=size)
        chars.append(layout._Char(ch, at, y0, at + width, y0 + size, y0 + size - 1, size, "x", 0))
        at += width
    return layout._Row(chars)


def inline_block(box, reasons=None) -> layout._Block:
    piece = layout._Inline(box, "Q(x)", 1.0, reasons or ["math font or symbol"], 8.0)
    return layout._Block(Kind.PARAGRAPH, box, pieces=["see ", piece])


def test_an_equation_against_a_bar_with_glyphs_on_the_other_side_is_half_a_fraction():
    bar = layout._Path((100.0, 120.0, 130.0, 120.4), "hrule", False, False)
    above = glyphs("P(x)", 104, 112)  # the numerator, 0.5 pt over the bar
    block = inline_block((104.0, 120.6, 126.0, 129.0))  # the denominator is all it holds
    layout._flag_half_fractions([block], [bar], [above])
    assert layout.REASON_HALF_FRACTION in block.pieces[1].reasons


def test_a_bar_with_nothing_on_its_other_side_marks_nothing():
    bar = layout._Path((100.0, 120.0, 130.0, 120.4), "hrule", False, False)
    block = inline_block((104.0, 120.6, 126.0, 129.0))
    layout._flag_half_fractions([block], [bar], [])
    assert block.pieces[1].reasons == ["math font or symbol"]


def test_an_equation_that_holds_its_own_bar_is_not_half_a_fraction():
    bar = layout._Path((100.0, 120.0, 130.0, 120.4), "hrule", False, False)
    above = glyphs("P(x)", 104, 112)
    whole = layout._Block(
        Kind.EQUATION, (100.0, 112.0, 130.0, 129.0), reasons=["fraction bar"], font_size=8.0
    )
    layout._flag_half_fractions([whole], [bar], [above])
    assert whole.reasons == ["fraction bar"]


def test_a_bracket_that_is_a_row_of_its_own_joins_the_line_it_stands_in():
    # TeX sets the tall "(" of d/dx (7x^3 - 15x^2) lowered, on a baseline of its own: the
    # row of a bracket glyph alone, level with the line's height but not its baseline.
    host = glyphs("7x3 -15x2)", 118, 345, 11.0)
    bracket = layout._Row([layout._Char("(", 112.6, 345.6, 117.6, 358.7, 346.1, 11.0, "CMEX10", 0)])
    rows = layout._attach_big_operators([host, bracket])
    assert [r.clean for r in rows] == ["(7x3 -15x2)"]
    apart = layout._attach_big_operators([host, glyphs("(", 300, 345, 11.0)])
    assert len(apart) == 2  # a bracket far from the line is left alone


def test_a_bracket_with_its_subscript_is_an_inline_equation():
    # "(x)_n": the closing bracket and the script after it are the evidence.
    def fill(page):
        page.insert_text((72, 140), "The rate follows (x", fontsize=BODY)
        page.insert_text((186, 140), ")", fontsize=BODY)
        page.insert_text((191, 144), "n", fontsize=SMALL, fontname="tiit")
        page.insert_text((199, 140), "throughout the experiment.", fontsize=BODY)

    found = equations(prose_page(fill))
    assert [spaceless(e.text) for e in found] == ["(x)n"]
    assert found[0].reasons == ["bracket with sub/superscript"]


def test_a_bracket_followed_by_plain_text_is_not_maths():
    def fill(page):
        page.insert_text(
            (72, 140), "The rate follows (see below) and then it stops here.", fontsize=BODY
        )

    assert equations(prose_page(fill)) == []
