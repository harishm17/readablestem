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


@pytest.mark.parametrize(("a", "b", "reason"), UNEQUAL)
def test_unequal_pairs_say_why(a: str, b: str, reason: str) -> None:
    result = mc.compare(a, b)
    assert result.kind is Kind.NONE
    assert not result.equal
    assert not result.holds
    assert result.reason == reason
    assert result.detail


def test_empty_and_unreadable_readings() -> None:
    assert mc.compare("", "x").reason == "empty_a"
    assert mc.compare("x", "  ").reason == "empty_b"
    assert mc.compare(r"\frac{", "x").reason == "unreadable_a"
    assert mc.compare("x", r"\frac{").reason == "unreadable_b"
    assert mc.compare("<math><mi>", "x").reason == "unreadable_a"


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
