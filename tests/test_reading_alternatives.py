r"""Other readings of a formula as choices on its symbols: the stored data shape
(`core.types.ReadingAlternatives`) and `pipeline.reading_alternatives`, which makes it from named
doubts or from a second reading. No model is called.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from readablestem.core.types import Element, Kind, ReadingAlternatives, SymbolAlternative
from readablestem.pipeline.reading_alternatives import (
    Doubt,
    find_symbol,
    from_reading,
    resolve_doubts,
)


@pytest.mark.parametrize(
    ("latex", "symbol", "occurrence", "span"),
    [
        ("x + x", "x", 1, (0, 1)),
        ("x + x", "x", 2, (4, 5)),
        ("x + x", "x", 3, None),
        (r"\max x", "x", 1, (5, 6)),  # not inside the command
        (r"\rho v^2", r"\rho", 1, (0, 4)),
        ("f_i(y)", "f_i", 1, (0, 3)),
        ("f_{10}", "f_{10}", 1, (0, 6)),
        ("12", "1", 1, (0, 1)),
        ("12", "2", 1, (1, 2)),
        ("x", "", 1, None),
        ("x", "x", 0, None),
    ],
)
def test_find_symbol_counts_latex_tokens(latex, symbol, occurrence, span):
    assert find_symbol(latex, symbol, occurrence) == span


# ----------------------------------------------------------------------- named doubts


def test_a_doubt_becomes_a_span_of_the_stored_reading():
    found = resolve_doubts(
        r"p = \rho g h", [Doubt("p", 1, [r"\rho", "P"], kind="greek_latin")], "block"
    )
    assert found is not None and found.latex == r"p = \rho g h"
    (only,) = found.symbols
    assert (only.start, only.end, only.symbol, only.kind) == (0, 1, "p", "greek_latin")
    assert found.apply(0, 1) == r"P = \rho g h" and found.choices == 2


def test_a_script_lost_or_added_is_an_option_that_replaces_the_stretch():
    found = resolve_doubts("f(y)", [Doubt("f", 1, ["f^{-1}"], kind="script")])
    assert found is not None and found.apply(0, 0) == "f^{-1}(y)"


def test_a_doubt_that_points_nowhere_or_changes_nothing_is_dropped():
    found = resolve_doubts(
        "a + b",
        [Doubt("z", 1, ["Z"]), Doubt("a", 1, ["a", " ", ""]), Doubt("b", 1, ["b", "B", "B"])],
    )
    assert found is not None
    assert [(s.symbol, s.options) for s in found.symbols] == [("b", ["B"])]


def test_an_option_that_breaks_the_equation_is_dropped_and_none_left_is_none():
    found = resolve_doubts(r"x + \frac{1}{2}", [Doubt("x", 1, [r"\frac{", "X"])])
    assert found is not None and found.symbols[0].options == ["X"]
    assert resolve_doubts("x", [Doubt("x", 1, [r"\frac{"])]) is None


def test_the_count_of_doubts_and_of_options_is_bounded():
    many = [Doubt(s, 1, ["A", "B", "C", "D", "E"]) for s in "abcde"]
    found = resolve_doubts("a b c d e", many)
    assert found is not None and len(found.symbols) == 3
    assert all(len(s.options) == 3 for s in found.symbols)


def test_overlapping_doubts_keep_the_first():
    found = resolve_doubts("f_i + 1", [Doubt("f_i", 1, ["g"]), Doubt("f", 1, ["F"])])
    assert found is not None and [s.symbol for s in found.symbols] == ["f_i"]


def test_a_doubt_in_chemistry_points_into_the_written_out_reading():
    from readablestem.pipeline import chem

    stored = chem.expand_ce(r"\ce{CO2 + H2O}")
    found = resolve_doubts(stored, [Doubt("O", 1, ["o"])])
    assert found is not None and found.latex == stored and found.symbols[0].symbol == "O"


# --------------------------------------------------------------------- a second reading


def test_greek_against_latin_and_digit_against_letter_are_named():
    greek = from_reading(r"\rho g h", "p g h")
    assert greek is not None and greek.symbols[0].kind == "greek_latin"
    assert greek.apply(0, 0) == "p g h"
    digit = from_reading("U(z)", "U(2)")
    assert digit is not None and digit.symbols[0].kind == "digit_letter"


def test_a_lost_script_is_a_choice_that_adds_it_back():
    found = from_reading("f(y)", "f^{-1}(y)")
    assert found is not None
    assert found.apply(0, 0) == "f^{-1}(y)" and found.symbols[0].kind == "script"


def test_equal_spacing_only_or_unrelated_readings_give_none():
    assert from_reading(r"a\,b", "a b") is None  # spacing is not a difference
    assert from_reading("x", "x") is None
    assert from_reading("", "x") is None
    assert from_reading("a + b + c + d + e + f + g", "p q r s t u v") is None


# ---------------------------------------------------------------- the stored data shape


def test_reading_alternatives_refuse_a_span_that_is_not_the_symbol():
    good = SymbolAlternative(start=0, end=1, symbol="x", kind="case", options=["X"])
    assert ReadingAlternatives(latex="x + 1", symbols=[good]).apply(0, 0) == "X + 1"
    bad = SymbolAlternative(start=0, end=1, symbol="y", kind="case", options=["Y"])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="x + 1", symbols=[bad])
    late = SymbolAlternative(start=9, end=10, symbol="x", kind="case", options=["X"])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="x + 1", symbols=[late])


def test_reading_alternatives_refuse_overlap_and_empty():
    a = SymbolAlternative(start=0, end=3, symbol="f_i", options=["g"])
    b = SymbolAlternative(start=2, end=3, symbol="i", options=["j"])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="f_i", symbols=[a, b])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="f_i", symbols=[])
    with pytest.raises(ValidationError):
        SymbolAlternative(start=2, end=2, symbol="", options=["x"])
    with pytest.raises(ValidationError):
        SymbolAlternative(start=0, end=1, symbol="f", options=[])


def test_an_element_round_trips_with_and_without_alternatives():
    found = from_reading("x", "X")
    plain = Element(id="p1-e0", kind=Kind.EQUATION, page=0, bbox=(0, 0, 10, 10), order=0)
    with_it = plain.model_copy(update={"alternatives": found})
    assert "alternatives" not in plain.model_dump(mode="json")
    assert Element.model_validate_json(with_it.model_dump_json()).alternatives == found
    assert Element.model_validate_json(plain.model_dump_json()).alternatives is None
