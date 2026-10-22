"""Where an inline formula starts and ends (docs/eval-realworld.md, "the edges of inline spans").

The author's math mode stops at the dollar sign: the parentheses of "(x = 0.9)", the unit of
"t = 915 s" and the citation of "x = 5 [8]" are the sentence's, not the formula's. On the 25
development papers 27 of the 31 formulas the layout cut inside a pair of parentheses had the
pair outside the author's maths (the 4 others were tuples, "(a, b)"), and 37 of 40 that ended in
an upright unit had the unit outside. The formula keeps what is inside; the words stay text.
"""

from __future__ import annotations

import pymupdf

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import analyze

BODY = 11.0


def one_page(fill) -> bytes:
    doc = pymupdf.open()
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


def test_the_parentheses_around_a_whole_formula_are_the_sentences():
    assert found("We then set the damping to (x = 0.9) for all of the runs in this study.") == [
        "x = 0.9"
    ]


def test_a_tuple_keeps_its_parentheses():
    assert found("The optimum is reached at the point (x, y = 2) of the plane we study here.") == [
        "(x, y = 2)"
    ]


def test_parentheses_that_do_not_enclose_the_whole_run_stay():
    assert found(
        "The estimate (x + 1)^2 = 9 follows from the bound we proved in this section."
    ) == ["(x + 1)^2 = 9"]


def test_a_unit_after_a_number_is_text():
    assert found(
        "The switch happens at t = 915 s which differs greatly from the true behaviour."
    ) == ["t = 915"]


def test_a_citation_after_a_formula_is_text():
    assert found("The ratio is x = 5 [8] in the experiment of the second group of authors.") == [
        "x = 5"
    ]


def test_an_opening_bracket_after_the_formula_is_the_sentences():
    assert found("The estimate x = 5 (see the appendix for the derivation of this bound).") == [
        "x = 5"
    ]


def test_two_formulas_with_a_word_space_between_them_are_two_equations():
    # "x = 5" and "y = 2" with the parenthesis of the sentence between: not one formula.
    assert found(
        "The pair of values y = 2 (x = 5) is reached after the first step of the loop."
    ) == [
        "y = 2",
        "x = 5",
    ]


def test_spaces_around_an_operator_do_not_end_a_formula():
    assert found("The total x = 5 + y - 2 follows from the two steps that we described above.") == [
        "x = 5 + y - 2"
    ]


def test_a_list_with_an_ellipsis_is_one_formula():
    assert found(
        "The index runs over i = 1, ..., S in every one of the sums that we write down."
    ) == ["i = 1, ..., S"]


def test_a_comma_between_two_formulas_ends_the_first():
    assert found("With x = 1, y = 2 and z = 3 the three values are fixed in the example.") == [
        "x = 1",
        "y = 2",
        "z = 3",
    ]


def _row(text: str):
    from readablestem.pipeline.layout import _Char

    return [_Char(c, k * 5.0, 0, k * 5.0 + 5, 10, 8, 10, "CMR10", 0) for k, c in enumerate(text)]


def test_the_signature_of_a_map_is_one_formula():
    from readablestem.pipeline.layout import _join_signatures

    chars = _row("H : R \u2192 S")
    runs = [(0, 1, ["a"]), (4, 7, ["b"])]
    assert _join_signatures(chars, runs) == [(0, 7, ["a", "b"])]


def test_a_colon_between_formulas_without_an_arrow_does_not_join():
    from readablestem.pipeline.layout import _join_signatures

    chars = _row("a : b = c")
    runs = [(0, 1, ["a"]), (4, 9, ["b"])]
    assert _join_signatures(chars, runs) == runs


def test_a_colon_after_a_word_is_not_inside_a_formula():
    assert found("Note: x = 5 is the value that we use in the experiment of the section.") == [
        "x = 5"
    ]


def test_a_defining_colon_equals_stays_in_the_formula():
    assert found("Given n, we define x := 5 + y for the length of the run in the experiment.") == [
        "x := 5 + y"
    ]
