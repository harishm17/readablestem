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


def test_a_maths_word_and_an_italic_variable_are_one_expression():
    def fill(page):
        line(
            page,
            100,
            ("The slope of ", "helv"),
            ("ln", "helv"),
            ("X", "heit"),
            (" against the other quantity is steady over the range of the data.", "helv"),
        )

    assert inline(analyze(one_page(fill)).elements) == ["lnX"]
