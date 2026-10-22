"""A variable set in an italic text font is maths (real LaTeX PDFs, 2026-10-21).

mathptmx, mathpazo, Charter and newtx set the Latin letters of a formula in the text family's
italic, not in a maths font, so a lone `S` or `K(t)` in a sentence has no maths-font glyph to
show it is one. The real-world set (docs/eval-realworld.md) counted 126 single symbols and 86
two- or three-symbol expressions in running text that were not found, most of them these. The
evidence is the italic itself: a short italic group inside upright prose, whose neighbours are
upright. Emphasised words, italic sentences and labels stay text.
"""

from __future__ import annotations

import pymupdf

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import analyze

BODY = 11.0


def one_page(fill, producer: str = "pdfTeX-1.40.25") -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"producer": producer})
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def line(page: pymupdf.Page, y: float, *pieces: tuple[str, str], x: float = 72) -> None:
    """Text pieces (text, base-14 font) set one after another on a baseline."""
    for text, font in pieces:
        page.insert_text((x, y), text, fontsize=BODY, fontname=font)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)


def inline(elements: list[Element]) -> list[str]:
    return [(e.text or "").replace(" ", "") for e in elements if e.kind == Kind.EQUATION]


def test_a_lone_italic_letter_in_a_sentence_is_a_variable():
    def fill(page):
        line(
            page,
            100,
            ("Let us consider ", "helv"),
            ("S", "heit"),
            (" species, characterized by their abundances in the community.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == ["S"]


def test_an_italic_letter_with_brackets_is_one_variable_expression():
    def fill(page):
        line(
            page,
            100,
            ("The carrying capacity ", "helv"),
            ("K", "heit"),
            ("(", "helv"),
            ("t", "heit"),
            (") changes slowly during the experiment and is measured daily.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == ["K(t)"]


def test_two_italic_variables_apart_are_two_equations():
    def fill(page):
        line(
            page,
            100,
            ("With ", "helv"),
            ("n", "heit"),
            (" samples and ", "helv"),
            ("m", "heit"),
            (" features the matrix is large and dense in memory.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == ["n", "m"]


def test_an_emphasised_word_is_not_a_variable():
    def fill(page):
        line(
            page,
            100,
            ("The answer is ", "helv"),
            ("not", "heit"),
            (" obvious, and the ", "helv"),
            ("paradox", "heit"),
            (" of the plankton remains open for many communities.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == []


def test_a_letter_inside_an_italic_sentence_is_not_a_variable():
    def fill(page):
        line(
            page,
            100,
            ("Theorem 1. ", "hebo"),
            ("Let there be a solution of the problem and it is unique.", "heit"),
        )

    assert inline(analyze(one_page(fill)).elements) == []


def test_an_italic_list_label_is_not_a_variable():
    def fill(page):
        line(
            page,
            100,
            ("(", "helv"),
            ("a", "heit"),
            (") Use the technique described in the section to create a spreadsheet.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == []


def test_a_figure_part_reference_is_not_a_variable():
    def fill(page):
        line(
            page,
            100,
            ("The eigenvalues are shown in Fig. 6", "helv"),
            ("(", "helv"),
            ("a", "heit"),
            (") and the vectors in the next panel of the figure.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == []


def test_short_words_of_an_italic_sentence_with_a_function_name_are_not_variables():
    # A theorem set in italics: "for all" and "and" are words even though log(n) is upright.
    def fill(page):
        line(
            page,
            100,
            ("Suppose that the matrix is dense and ", "heit"),
            ("log(", "helv"),
            ("n", "heit"),
            (") is large for all values of the other parameters in the model.", "heit"),
        )

    found = inline(analyze(one_page(fill)).elements)
    assert not any(word in "".join(found) for word in ("and", "for", "all"))


def test_a_bold_letter_with_an_index_is_a_vector():
    # "the eigenvector **v**2": a bold letter in the text font with a lowered digit.
    def fill(page):
        page.insert_text((72, 100), "to the eigenvector ", fontsize=BODY, fontname="helv")
        x = 72 + pymupdf.get_text_length("to the eigenvector ", fontname="helv", fontsize=BODY)
        page.insert_text((x, 100), "v", fontsize=BODY, fontname="hebo")
        x += pymupdf.get_text_length("v", fontname="hebo", fontsize=BODY)
        page.insert_text((x, 103), "2", fontsize=7.4, fontname="helv")
        x += pymupdf.get_text_length("2", fontname="helv", fontsize=7.4)
        page.insert_text(
            (x, 100),
            ", it is recommended to remove that part of the signal.",
            fontsize=BODY,
            fontname="helv",
        )

    assert inline(analyze(one_page(fill)).elements) == ["\U0001d42f2"]  # bold v, then 2


def test_the_unit_exponent_of_upright_text_is_still_text():
    def fill(page):
        page.insert_text((72, 100), "The tank holds 5 cm", fontsize=BODY, fontname="helv")
        x = 72 + pymupdf.get_text_length("The tank holds 5 cm", fontname="helv", fontsize=BODY)
        page.insert_text((x, 96), "3", fontsize=7.4, fontname="helv")
        x += pymupdf.get_text_length("3", fontname="helv", fontsize=7.4)
        page.insert_text(
            (x, 100), " of water at room temperature in the lab.", fontsize=BODY, fontname="helv"
        )

    assert inline(analyze(one_page(fill)).elements) == []


def test_italic_letters_in_a_chromium_print_are_emphasis_not_maths():
    def fill(page):
        line(
            page,
            100,
            ("Let us consider ", "helv"),
            ("S", "heit"),
            (" species, characterized by their abundances in the community.", "helv"),
        )

    assert inline(analyze(one_page(fill, producer="Skia/PDF m120")).elements) == []
