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

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout
from readablestem.pipeline.layout import analyze

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


# ---------------------------------------------------------------- separate equations


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


# ---------------------------------------------------------------- stacked fragments


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


def test_a_number_at_the_right_edge_is_never_a_paragraph_of_its_own():
    """The numbers of an aligned display set left of the page's column gutter were in the
    other column from the equation and became paragraphs; so did a symbolic tag "(*)", and a
    number centered between two lines set far apart."""

    def fill(page):
        prose(page, 100, "The model is written as the following aligned equations,")
        for i, (lhs, number) in enumerate([("r = b - d", "(2)"), ("K = N + x", "(3)")]):
            page.insert_text((140, 140 + 22 * i), lhs, fontsize=12, fontname="Times-Italic")
            page.insert_text((520, 140 + 22 * i), number, fontsize=12)
        prose(page, 200, "A tagged equation and two lines with one number between them follow.")
        page.insert_text((250, 240), "v = d / t", fontsize=12, fontname="Times-Italic")
        page.insert_text((520, 240), "(*)", fontsize=12)
        page.insert_text((250, 290), "p = m v", fontsize=12, fontname="Times-Italic")
        page.insert_text((250, 340), "E = m c", fontsize=12, fontname="Times-Italic")
        page.insert_text((520, 315), "(4)", fontsize=12)
        prose(page, 380, "The equations above hold for every week in the table below.")
        for i in range(18):  # two columns of text: the page has a gutter
            page.insert_text((72, 420 + 14 * i), f"left column line {i} with words", fontsize=10)
            page.insert_text((320, 420 + 14 * i), f"right column line {i} with words", fontsize=10)

    elements = analyze(one_page(fill)).elements
    assert {e.equation_label for e in equations(elements)} >= {"(2)", "(3)", "(*)", "(4)"}
    assert not [e for e in elements if e.kind != Kind.EQUATION and "(" in (e.text or "")]


FULL_LINES = [
    "The two bounds that the argument needs are stated in the next display, and the reader "
    "should keep in",
    "mind that every constant in them comes from the setting of the second section, where "
    "the sample size",
    "and the number of the parameters were fixed once and for all before any of the "
    "estimates were made:",
]


def number_beside_a_word(page):
    """A limit set under "argmin" splits the word in x order ("argm", k, "in")."""
    prose(page, 100, "The estimate is the minimiser of the empirical risk over the parameters:")
    page.insert_text((200, 140), "x = ", fontsize=12, fontname="Times-Italic")
    page.insert_text((222, 140), "argmin", fontsize=12)
    page.insert_text((228, 150), "k", fontsize=8, fontname="Times-Italic")
    page.insert_text((262, 140), "f ( k )", fontsize=12, fontname="Times-Italic")
    page.insert_text((520, 140), "(106)", fontsize=12)


def number_after_prose_words(page):
    """A line with a few words ("for every n greater than zero") reads as text."""
    prose(page, 100, "The recursion is defined for every sample as follows,")
    line = "h[n] = x[n] + 0.5 h[n-1] for every n greater than zero"
    page.insert_text((150, 140), line, fontsize=12, fontname="Times-Italic")
    page.insert_text((520, 140), "(106)", fontsize=12)


def number_set_close_at_the_edge(page):
    """A display that fills its column leaves under a font size before its number."""
    for i, line in enumerate(FULL_LINES):
        page.insert_text((72, 70 + 14 * i), line, fontsize=BODY)
    maths = "a + b <= c + d + e + f + g + h + k"
    width = pymupdf.get_text_length(maths, fontname="Times-Italic", fontsize=12)
    page.insert_text((566 - 33 - width, 140), maths, fontsize=12, fontname="Times-Italic")
    page.insert_text((566 - 28, 140), "(106)", fontsize=12)


def number_under_the_display(page):
    """A number set right-aligned on the line below its equation."""
    prose(page, 100, "The bound on the difference of the two means is written as")
    maths = "d(x, y) <= c * |x - y| + e"
    page.insert_text((200, 140), maths, fontsize=12, fontname="Times-Italic")
    page.insert_text((290, 156), "(106)", fontsize=12)


@pytest.mark.parametrize(
    "fill",
    [
        number_beside_a_word,
        number_after_prose_words,
        number_set_close_at_the_edge,
        number_under_the_display,
    ],
)
def test_a_number_that_the_row_around_it_hides_is_still_the_equations_label(fill):
    """Four ways the number stayed in the equation's text or became a paragraph: a word of
    the maths split by its limit, words in the line, a number set closer than a font size,
    and a number set under the display."""

    def page_with(page):
        fill(page)
        prose(page, 190, "and that is all.")

    elements = analyze(one_page(page_with)).elements
    [equation] = equations(elements)
    assert equation.display == "block"
    assert equation.equation_label == "(106)"
    assert "(106)" not in (equation.text or "")
    assert not [e for e in elements if e.kind == Kind.PARAGRAPH and "(106)" in (e.text or "")]


# ---------------------------------------------------------------- rows of a table


# ---------------------------------------------------------------- a braced system


# ---------------------------------------------------------------- cases with words


def row_of(text: str, x: float, y0: float, size: float = BODY) -> layout._Row:
    chars, at = [], x
    for ch in text:
        width = 0.5 * size
        chars.append(
            layout._Char(ch, at, y0, at + width, y0 + size, y0 + size - 2, size, "CMR10", 0)
        )
        at += width
    return layout._Row(chars)


def cases_rows() -> tuple[list[layout._Row], list[layout._Row]]:
    """g(t) = { 9t for t > 0 / 45 for t < 5 / 7t otherwise }: the brace as three abutting
    pieces (the CMEX glyphs TeX builds a tall one from), one row each for value and
    condition, a wide gap between them."""
    brace = [row_of("\uf8f1", 250, 130), row_of("\uf8f2", 250, 141), row_of("\uf8f3", 250, 152)]
    values = [row_of("9t", 270, 132), row_of("45", 270, 147), row_of("7t", 270, 160)]
    conditions = [row_of("for t > 0", 360, 132), row_of("for t < 5", 360, 147),
                  row_of("otherwise", 360, 160)]  # fmt: skip
    return brace, [*values, *conditions]


def test_a_brace_with_one_row_beside_it_is_not_cases():
    brace, _ = cases_rows()
    one = [row_of("9t", 270, 140), row_of("for t > 0", 360, 140)]
    rows = [*brace, *one]
    for row in rows:
        layout._classify_row(row)
    layout._mark_case_rows(rows)
    assert not any(r.case_row for r in rows)


def test_a_set_in_braces_is_not_cases():
    brace, rest = cases_rows()
    closing = row_of("}", 400, 140)
    rows = [*brace, *rest, closing]
    for row in rows:
        layout._classify_row(row)
    layout._mark_case_rows(rows)
    assert not any(r.case_row for r in rows)


def test_a_display_fraction_over_a_close_equation_is_not_a_table_row():
    """2-2 of the OpenStax Physics: "time = distance / v" with its fraction at the size of
    its line, directly over "distance = v x time", centred like it, a few points apart:
    two equations, as the source has them."""

    def fill(page):
        prose(page, 100, "You can rearrange the equation to solve for either quantity")
        page.insert_text((276, 140), "t =", fontsize=12)
        fraction(page, 298, 136, "d", "v", 8, size=12)
        page.insert_text((266, 164), "d = v x t", fontsize=12)
        prose(page, 206, "Suppose, for example, a car travels 150 kilometers in 3.2 hours.")

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements].count(Kind.EQUATION) == 2
