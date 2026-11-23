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

import re

import pymupdf
import pytest

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


def typeset(sentence: str, producer: str, **switches) -> list[str]:
    """The inline formulas of one line: `_x_` is an italic letter, `~0~` a lowered digit, the
    rest is upright text."""

    def fill(page):
        x = 72.0
        for piece in re.split(r"(_.+?_|~.+?~)", sentence):
            text, font, size, lift = piece, "helv", BODY, 0.0
            if piece[:1] == "_" and piece[-1:] == "_":
                text, font = piece[1:-1], "heit"
            elif piece[:1] == "~" and piece[-1:] == "~":
                text, size, lift = piece[1:-1], 0.7 * BODY, 0.25 * BODY
            page.insert_text((x, 100 + lift), text, fontsize=size, fontname=font)
            x += pymupdf.get_text_length(text, fontname=font, fontsize=size)

    elements: list[Element] = analyze(one_page(fill, producer), **switches).elements
    return [e.text or "" for e in elements if e.kind == Kind.EQUATION]


TEX = "pdfTeX-1.40.25"
WHOLE_INLINE_FORMULA = [
    # (a) a maths word and its operand join the formula before it
    ("We know that _a_ = _b_ mod _n_ holds for every integer in the whole range.", TEX,
     ["a = b mod n"], ["a = b", "n"]),
    # (b) a label that ends in a colon joins the formula after it
    ("The null hypothesis _H_~0~: _m_ = 5 is tested against the alternative one.", TEX,
     ["H0: m = 5"], ["H0", "m = 5"]),
    # operator names: "erfi" is a maths word, so the formula that holds it is whole
    ("We bound the tail by erfi(_x_) for every large value of the argument.", TEX,
     ["erfi(x)"], []),
    # (f) a colon inside a bracket that closes on the row does not end the formula
    ("We compute _I_(_A_ : _B_) for the two halves of the whole system as shown.", TEX,
     ["I(A : B)"], ["B"]),
    # "for" and "and" set in italic (a theorem's statement) are words, not the variables f, o, r
    ("_a_ = 2, _for_ _j_ = 1, 2 _and_ _b_ = 3", TEX,
     ["a = 2", "j = 1", "b = 3"], ["a = 2, for j = 1, 2 and b = 3"]),
]  # fmt: skip


@pytest.mark.parametrize(("sentence", "producer", "whole", "before"), WHOLE_INLINE_FORMULA)
def test_the_box_holds_the_whole_inline_formula(sentence, producer, whole, before):
    assert typeset(sentence, producer) == before
    assert typeset(sentence, producer, inline_joins=True) == whole


def test_an_index_with_a_colon_stays_in_its_formula_in_a_converted_file():
    """`dx_{1:T}` of a TeX paper whose producer field was rewritten (no `pdfTeX`): the colon of
    the index is set in Computer Modern, which says TeX set it."""
    from pathlib import Path

    pdf = (Path(__file__).parent.parent / "data/real-tables/2407.13734.pdf").read_bytes()

    def texts(**switches) -> list[str]:
        elements = analyze(pdf, pages=[0], **switches).elements
        return [e.text or "" for e in elements if e.kind == Kind.EQUATION]

    assert not any(t.endswith("dx1:T") for t in texts())
    assert any(t.endswith("dx1:T") for t in texts(inline_joins=True))
