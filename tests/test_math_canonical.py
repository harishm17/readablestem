"""The canonical comparator: which kind of sameness holds between two readings of a formula."""

from __future__ import annotations

import pytest

from readablestem.pipeline import math_canonical as mc
from readablestem.pipeline.math_canonical import Kind

# (a, b): the tree is the same, so every kind holds.
TREE_EQUAL = [
    (r"x^{2}", r"x^2"),
    (r"x_1", r"x_{1}"),
    (r"a\,b", r"a b"),
    (r"E=mc^2", r"E = m c^{2}"),
    (r"\bm{x}", r"\boldsymbol{x}"),
    (r"\left( a+b \right)^2", r"(a+b)^2"),
    (r"\bigl(x\bigr)", r"(x)"),
    (r"\left\{ x \right\}", r"\{x\}"),
    (r"\sqrt{2}", r"\sqrt 2"),
    (r"x^b_a", r"x_a^b"),
    (r"{x}+1", r"x+1"),
    (r"a\quad b", r"a b"),
    (r"v_{\textrm{max}}", r"v_{\text{max}}"),
    (r"\frac{1}{2}", r"\frac{1}{2}\,"),
]

# Same formula once notation is written alike, but not the same tree.
NOTATION_ONLY = [
    (r"\mathrm{in}", r"\text{in}"),
    (r"\operatorname{sin} x", r"\mathrm{sin} x"),
    (r"{\rm d}x", r"\text{d}x"),
    (r"\left. x \right|_0", r"x|_0"),
    (r"x^{\text{ab}}_{b}", r"x_b^{\mathrm{ab}}"),
]

# Not the same tree or notation, said in the same words.
SPOKEN_ONLY = [
    (r"\mathbf{x}", r"\boldsymbol{x}"),
]

UNEQUAL = [
    (r"a+b", r"a-b", "symbols"),
    (r"a+b", r"b+a", "order"),
    (r"x^2", r"x_2", "symbols"),
    (r"\frac{1}{2}", r"1/2", "symbols"),
    (r"x", r"y", "symbols"),
    (r"\alpha", r"a", "symbols"),
    (r"\frac{a}{b}", r"\frac{b}{a}", "order"),
]


@pytest.mark.parametrize(("a", "b"), TREE_EQUAL)
def test_tree_equal_pairs_hold_at_every_kind(a: str, b: str) -> None:
    for first, second in ((a, b), (b, a)):
        result = mc.compare(first, second)
        assert result.kind is Kind.TREE, (first, second, result)
        assert {Kind.TREE, Kind.NOTATION, Kind.SPOKEN, Kind.SYMBOLS} <= result.holds
        assert result.reason is None


@pytest.mark.parametrize(("a", "b"), NOTATION_ONLY)
def test_notation_neutral_pairs(a: str, b: str) -> None:
    for first, second in ((a, b), (b, a)):
        result = mc.compare(first, second)
        assert result.kind is Kind.NOTATION, (first, second, result)
        assert Kind.TREE not in result.holds
        assert result.equal


@pytest.mark.parametrize(("a", "b"), SPOKEN_ONLY)
def test_spoken_equal_pairs(a: str, b: str) -> None:
    result = mc.compare(a, b)
    assert result.kind is Kind.SPOKEN
    assert Kind.NOTATION not in result.holds
    assert mc.equal(a, b, Kind.SPOKEN)
    assert not mc.equal(a, b, Kind.TREE)


@pytest.mark.parametrize(("a", "b", "reason"), UNEQUAL)
def test_unequal_pairs_say_why(a: str, b: str, reason: str) -> None:
    result = mc.compare(a, b)
    assert result.kind is Kind.NONE
    assert not result.equal
    assert not result.holds
    assert result.reason == reason
    assert result.detail


def test_detail_points_at_the_first_difference() -> None:
    result = mc.compare("a+b", "a-b")
    assert result.detail is not None
    assert result.detail.startswith("1:+")


def test_letter_style_alone_is_symbols_unstyled() -> None:
    result = mc.compare(r"\mathbf{x}", "x")
    assert result.kind is Kind.SYMBOLS_UNSTYLED
    assert result.holds == {Kind.SYMBOLS_UNSTYLED}
    assert not mc.equal(r"\mathbf{x}", "x", Kind.SPOKEN)


def test_mathml_and_latex_compare_with_each_other() -> None:
    mathml = mc.to_mathml("x^2")
    assert mc.compare(mathml, "x^{2}").kind is Kind.TREE
    assert mc.compare(mathml, mc.to_mathml("x^{2}")).kind is Kind.TREE
    other = mc.compare(mathml, "x^3")
    assert other.kind is Kind.NONE
    assert other.reason == "symbols"


def test_notation_has_nothing_to_fold_in_mathml() -> None:
    a = mc.to_mathml(r"\mathrm{in}")
    b = mc.to_mathml(r"\text{in}")
    assert not mc.compare(a, b).holds_at(Kind.NOTATION)
    assert mc.compare(r"\mathrm{in}", r"\text{in}").holds_at(Kind.NOTATION)


def test_empty_and_unreadable_readings() -> None:
    assert mc.compare("", "x").reason == "empty_a"
    assert mc.compare("x", "  ").reason == "empty_b"
    assert mc.compare(r"\frac{", "x").reason == "unreadable_a"
    assert mc.compare("x", r"\frac{").reason == "unreadable_b"
    assert mc.compare("<math><mi>", "x").reason == "unreadable_a"


def test_two_unreadable_readings_are_equal_only_if_the_notation_is() -> None:
    assert mc.compare(r"\frac{ a", r"\frac{a").holds_at(Kind.NOTATION)
    assert not mc.compare(r"\frac{a", r"\frac{b").equal


def test_text_bodies_are_kept_as_written() -> None:
    assert mc.canonical_latex(r"\text{a  b}") == r"\text{a  b}"
    assert not mc.compare(r"\text{a b}", r"\text{ab}").holds_at(Kind.NOTATION)
    assert not mc.compare(r"\text{a\,b}", r"\text{a b}").holds_at(Kind.NOTATION)


def test_canonical_latex_rules() -> None:
    assert mc.canonical_latex(r"x^b_a") == r"x_a^b"
    assert mc.canonical_latex(r"\left( a \right)^{{2}}") == r"(a)^2"
    assert mc.canonical_latex(r"\alpha x") == r"\alpha x"
    assert mc.canonical_latex(r"\alpha  \, x") == r"\alpha x"
    assert mc.canonical_latex(r"\frac{a}{b}") == r"\frac{a}{b}"
    assert mc.canonical_latex(r"a\\b") == r"a\\b"
    assert mc.canonical_latex(r"\bm{x}") == r"\boldsymbol{x}"
    # a braced group after a command is its argument, not redundant
    assert mc.canonical_latex(r"\mathbf{x}") == r"\mathbf{x}"
    assert mc.canonical_latex(r"\sqrt[3]{x}") == r"\sqrt[3]{x}"
    # two digits are two symbols after a script
    assert mc.canonical_latex(r"x^{12}") == r"x^{12}"


def test_canonical_latex_is_idempotent() -> None:
    for a, b in TREE_EQUAL + NOTATION_ONLY:
        for latex in (a, b):
            once = mc.canonical_latex(latex)
            assert mc.canonical_latex(once) == once


def test_fold_upright_words_is_the_registered_rule() -> None:
    assert mc.fold_upright_words(r"\mathrm{in}+\operatorname*{lim}+{\rm d}x") == (
        r"\text{in}+\text{lim}+\text{d}x"
    )
    # a brace inside the word is not folded, nor is bold or spacing
    assert mc.fold_upright_words(r"\mathrm{a{b}}") == r"\mathrm{a{b}}"
    assert mc.fold_upright_words(r"\mathbf{x}\,") == r"\mathbf{x}\,"


def test_readings_reach_the_speech_rules() -> None:
    plain = mc.compare(r"\mathbf{x}", r"\boldsymbol{x}")
    assert plain.holds_at(Kind.SPOKEN)
    assert mc.speech_of(mc.to_mathml("x")) == "x"
    assert mc.speech_of("<math><unclosed>") is None


def test_latex_that_starts_with_a_bracket_is_not_mathml() -> None:
    assert not mc.is_mathml("<x")
    assert not mc.is_mathml(r"< 5 \mathrm{m}")
    assert mc.is_mathml(mc.to_mathml("x"))
    assert mc.is_mathml("<mrow><mi>x</mi></mrow>")
    assert mc.compare("<x", "< x").kind is Kind.TREE
    assert mc.compare("<x", "x").reason == "symbols"
