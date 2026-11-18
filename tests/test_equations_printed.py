"""`exact_printed`: LaTeX that prints alike scores right, LaTeX that prints differently stays wrong,
and a pair whose gold is another region's is counted apart (F-580)."""

from __future__ import annotations

import pytest

from readablestem.core.types import Element, Kind
from readablestem.eval import pipeline_eval as pe
from readablestem.eval.equations_printed import exact_printed
from test_eval_support import EQ_A, EQ_V, eq_fix, equation

PRINTS_ALIKE = [
    (r"{\rm d}x", r"\mathrm{d}x"),  # a style command
    (r"\left(x+y\right)", r"(x+y)"),  # sizing
    (r"{x}+y", r"x+y"),  # braces that only group
    (r"a\ldots b", r"a\dots b"),
    (r"a\,b", r"ab"),  # spacing
    (r"\dfrac{a}{b}", r"\frac{a}{b}"),
    (r"\frac{a}2", r"\frac{a}{2}"),
    (r"u'_\theta", r"u_\theta'"),  # a prime before or after a subscript
    (r"\begin{align}a&=b\end{align}", r"\begin{aligned}a&=b\end{aligned}"),
    (r"\mathbf{k=p+q}", r"\mathbf{k}=\mathbf{p}+\mathbf{q}"),  # a style sets letters only
    (r"{a \over b}c", r"\frac{a}{b}c"),  # the old infix fraction
    (r"\begin{array}{rcl}a&=&b\\c&=&d\end{array}", r"\begin{aligned}a&=b\\c&=d\end{aligned}"),
    (r"\begin{pmatrix}a&b\\c&d\end{pmatrix}", r"\left(\begin{array}{cc}a&b\\c&d\end{array}\right)"),
]

PRINTS_DIFFERENTLY = [
    (r"a+b", r"a-b"),  # a sign
    (r"x", r"y"),  # a character
    (r"a=b", r"a\le b"),  # a relation
    (r"\hat{x}+y", r"x+y"),  # an accent on one side
    (r"f'(x)", r"f(x)"),  # a prime
    (r"\frac{a}{b}c", r"\frac{a}{bc}"),  # a structure
    (r"x_{i-1}", r"x_i-1"),  # braces that are not only grouping
    (r"\sqrt[3]{a}b", r"\sqrt[3]{ab}"),  # where an argument ends, in a root, a style, a script
    (r"\mathrm{a}b", r"\mathrm{ab}"),
    (r"x^{a}b", r"x^{ab}"),
    (r"\mathbf{x}+y", r"x+y"),  # bold against plain
    (r"\mathbf{x}+y", r"\mathbf{x}+\mathbf{y}"),  # a bold letter against a plain one
    (r"\begin{pmatrix}1&2\end{pmatrix}", r"\begin{pmatrix}12\end{pmatrix}"),  # two cells, one cell
    (r"\begin{cases}x&y>0\end{cases}", r"\begin{cases}xy>0\end{cases}"),
    (r"{a \over b}c", r"{a \over bc}"),  # where an infix fraction ends
    (r"a\\b", r"a;b"),  # a line break against a semicolon
]


@pytest.mark.parametrize(("pred", "gold"), PRINTS_ALIKE)
def test_latex_that_prints_alike_scores_right(pred, gold):
    assert exact_printed(pred, gold) and exact_printed(gold, pred)


@pytest.mark.parametrize(("pred", "gold"), PRINTS_DIFFERENTLY)
def test_latex_that_prints_differently_scores_wrong(pred, gold):
    assert not exact_printed(pred, gold)


def test_no_latex_on_either_side_is_wrong():
    assert not exact_printed(None, "x") and not exact_printed("x", None)


def test_a_pairing_miss_is_counted_apart_and_never_as_right():
    """The file says `a = b + d`, the answer reads it so, and the gold matched to it is `a=b+c`."""
    gold = [
        {**equation("p", 0, EQ_V, "block"), "latex": r"v=\frac{d}{t}"},
        {**equation("p", 1, EQ_A, "block"), "latex": "a=b+c"},
    ]
    elements = [
        Element(
            id="p1-e0", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0,
            text="v = d/t", display="block",
        ),
        Element(
            id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=1,
            text="a = b + d", display="block",
        ),
    ]  # fmt: skip
    fixes = {"p1-e0": eq_fix("p1-e0", r"v=\frac{d}{t}"), "p1-e1": eq_fix("p1-e1", "a=b+d")}
    row = pe.document_page_item("physics/p", gold, elements, fixes, [], [])
    right, miss = row["pairs"]
    assert (right["exact_printed"], right["pairing_miss"]) == (True, False)
    assert (miss["exact_printed"], miss["pairing_miss"]) == (False, True)
    metrics = pe.document_metrics([row])
    assert metrics["pairing_misses"] == 1
    assert metrics["matched_exact_printed"] == 0.5
    assert metrics["matched_exact_printed_without_pairing_misses"] == 1.0
