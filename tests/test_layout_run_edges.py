"""Where an inline formula starts and ends (docs/eval-realworld.md, "the edges of inline spans").

The author's math mode stops at the dollar sign: the parentheses of "(x = 0.9)", the unit of
"t = 915 s" and the citation of "x = 5 [8]" are the sentence's, not the formula's. On the 25
development papers 27 of the 31 formulas the layout cut inside a pair of parentheses had the
pair outside the author's maths (the 4 others were tuples, "(a, b)"), and of the formulas that
ended in an upright unit 37 were span failures against 2 that were exact. The formula keeps
what is inside; the words stay text. These are a TeX author's conventions: they apply to PDFs
typeset by TeX (`page_type.producer_hint`), not to a Chromium print of MathML.
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
    for text, font in pieces:
        page.insert_text((x, y), text, fontsize=BODY, fontname=font)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=BODY)


def found(sentence: str) -> list[str]:
    def fill(page):
        line(page, 100, (sentence, "helv"))

    elements: list[Element] = analyze(one_page(fill)).elements
    return [e.text or "" for e in elements if e.kind == Kind.EQUATION]


def test_a_list_with_an_ellipsis_is_one_formula():
    assert found(
        "The index runs over i = 1, ..., S in every one of the sums that we write down."
    ) == ["i = 1, ..., S"]


def _row(text: str):
    from readablestem.pipeline.layout import _Char

    return [_Char(c, k * 5.0, 0, k * 5.0 + 5, 10, 8, 10, "CMR10", 0) for k, c in enumerate(text)]


def test_the_signature_of_a_map_is_one_formula():
    from readablestem.pipeline.layout import _join_signatures

    chars = _row("H : R \u2192 S")
    runs = [(0, 1, ["a"]), (4, 7, ["b"])]
    assert _join_signatures(chars, runs) == [(0, 7, ["a", "b"])]
