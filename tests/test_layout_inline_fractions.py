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
