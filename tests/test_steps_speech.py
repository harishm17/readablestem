"""MathML -> speech: every gold equation reads without error, plus targeted rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from readablestem.eval.gold_scoring import iter_gold_records
from readablestem.pipeline.speech import SpeechError, mathml_to_speech
from readablestem.pipeline.validate import safe_latex_to_mathml

REPO = Path(__file__).resolve().parents[1]
GOLD = list(iter_gold_records(REPO / "data" / "gold", kind="equation"))
NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def say(latex: str) -> str:
    return mathml_to_speech(safe_latex_to_mathml(latex))


def gold(suffix: str) -> str:
    return next(r["mathml"] for r in GOLD if r["id"].endswith(suffix))


@pytest.mark.parametrize("record", GOLD, ids=lambda r: r["id"].removeprefix("openstax/physics/"))
def test_every_gold_equation_has_speech(record):
    speech = mathml_to_speech(record["mathml"])
    assert speech.strip()
    assert "<" not in speech and "\\" not in speech


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        # fractions
        (r"\frac{a}{b}", "a over b"),
        (r"\frac{\Delta d}{\Delta t}", "capital delta d over capital delta t"),
        (r"\frac{a + b}{c}", "the fraction a plus b over c end fraction"),
        (r"\frac{1}{\frac{1}{x}}", "the fraction 1 over 1 over x end fraction"),
        (r"\binom{n}{k}", "open parenthesis n choose k close parenthesis"),
        # powers and subscripts
        (r"x^2", "x squared"),
        (r"x^3", "x cubed"),
        (r"x^n", "x to the power n"),
        (r"10^{-3}", "10 to the power minus 3"),
        (r"e^{-kt}", "e to the power minus k t end power"),
        (r"x_0", "x sub 0"),
        (r"v_\text{f}", "v sub f"),
        (r"x_{i+1}", "x sub i plus 1 end sub"),
        (r"x_i^2", "x sub i squared"),
        # ions: the charge needs no "end power"; a nuclide is read as the element (the data
        # of `speech_nuclides`), and one that is not known keeps its numbers before its symbol
        (r"\text{SO}_4^{2-}", "S O 4 2 minus"),
        (r"\text{Fe}^{3+}", "F e 3 plus"),
        (r"{}^{14}_{6}\text{C}", "carbon 14, atomic number 6"),
        (r"{}^{235}\text{U}", "uranium 235"),
        (r"{}^{0}_{-1}\text{e}", "electron, mass number 0, atomic number minus 1"),
        (r"{}^{14}_{6}\text{Xx}", "superscript 14 subscript 6 Xx"),
        (r"y''", "y double prime"),
        (r"30^\circ", "30 degrees"),
        # roots
        (r"\sqrt{x}", "the square root of x"),
        (r"\sqrt{x^2 + y^2}", "the square root of x squared plus y squared end root"),
        (r"\sqrt[3]{8}", "the cube root of 8"),
        (r"\sqrt[n]{x}", "the root of index n of x"),
        # large operators with limits
        (r"\sum_{i=1}^{n} i", "the sum from i equals 1 to n of i"),
        (r"\int_0^1 x\,dx", "the integral from 0 to 1 of x d x"),
        (r"\lim_{x \to 0} x", "the limit as x approaches 0 of x"),
        (r"\prod_{k} a_k", "the product over k of a sub k"),
        # operators and relations
        (r"a - b", "a minus b"),
        (r"a \pm b", "a plus or minus b"),
        (r"a \times b", "a times b"),
        (r"a \div b", "a divided by b"),
        (r"a \leq b", "a less than or equal to b"),
        (r"a \geq b", "a greater than or equal to b"),
        (r"a \neq b", "a not equal to b"),
        (r"a \approx b", "a approximately equals b"),
        (r"F \propto m", "capital F proportional to m"),
        (r"a \rightarrow b", "a right arrow b"),
        # letters, Greek, fonts, accents
        (r"\Delta t", "capital delta t"),
        (r"\delta t", "delta t"),
        (r"\theta + \omega", "theta plus omega"),
        (r"\mathbf{d}", "bold d"),
        (r"\vec{d}", "vector d"),
        (r"\hat{x}", "x hat"),
        (r"\bar{x}", "x bar"),
        (r"\dot{x}", "x dot"),
        (r"\mathbb{R}", "double-struck capital R"),
        # functions and text
        (r"\sin x", "sine x"),
        (r"\ln x", "natural log x"),
        (r"\text{distance} = 5\ \text{km}", "distance equals 5 kilometers"),
        # tables
        (
            r"\begin{array}{rcl} a & = & b \\ & = & c \end{array}",
            "row 1: a equals b; row 2: equals c",
        ),
        (
            r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}",
            "the 2 by 2 matrix; row 1: a, b; row 2: c, d; end matrix",
        ),
        (r"\begin{aligned} a &= b \end{aligned}", "a equals b"),
        # a negated relation is the one sign, said as what it says
        (r"a \not\in B", "a is not an element of capital B"),
        (r"a \not= b", "a not equal to b"),
        (r"a \nleq b", "a is neither less than nor equal to b"),
        (r"A \not\subset B", "capital A is not a subset of capital B"),
        (r"\textbf{F} = m a", "bold capital F equals m a"),
        # a sign is said for what it does: a bar, a dot, a cross, a script
        (r"P(A \mid B)", "capital P open parenthesis capital A given capital B close parenthesis"),
        (r"\{x \mid x>0\}", "open brace x such that x greater than 0 close brace"),
        (r"\{n \mid 3 \mid n\}", "open brace n such that 3 divides n close brace"),
        (r"3 \mid 12", "3 divides 12"),
        (r"\mathbf{a} \cdot \mathbf{b}", "bold a dot bold b"),
        (r"\mathbf{a} \times \mathbf{b}", "bold a cross bold b"),
        (r"2 \cdot 3", "2 times 3"),
        (r"A^T", "capital A transpose"),
        (r"x^*", "x star"),
        (r"\sin^{-1} x", "inverse sine x"),
        (r"\lim_{x \to 0^+} f", "the limit as x approaches 0 plus of f"),
        (
            r"X \sim N(0,1)",
            "capital X is distributed as capital N open parenthesis 0 comma 1 close parenthesis",
        ),
        (r"A \setminus B", "capital A set minus capital B"),
        (r"\mathit{diff}", "diff"),
        # sentence punctuation at the end is not read
        (r"x = 1.", "x equals 1"),
        (r"x = 1,", "x equals 1"),
    ],
)
def test_speech_rules(latex, expected):
    assert say(latex) == expected


@pytest.mark.parametrize(
    ("one", "other"),
    [
        (r"\frac{1}{2m}v^2", r"\frac{1}{2}mv^2"),
        (r"\frac{a}{bc}", r"\frac{a}{b}c"),
        (r"x_{iy}", r"x_i y"),
        (r"a_{nb}", r"a_n b"),
        (r"\frac{ab}{2}", r"a \frac{b}{2}"),
        (r"a_{ab}^2", r"a_a b^2"),
        (r"e^{-x}", r"e^{-}x"),
    ],
)
def test_two_different_formulas_are_not_said_alike(one, other):
    assert say(one) != say(other)


@pytest.mark.parametrize(
    "bad",
    [
        "not xml <",
        f'<!DOCTYPE m [<!ENTITY x "secret">]><math {NS}><mi>&x;</mi></math>',
        f"<math {NS}>" + "<mrow>" * 100 + "</mrow>" * 100 + "</math>",
    ],
)
def test_unreadable_input_raises_content_free_error(bad):
    with pytest.raises(SpeechError) as exc:
        mathml_to_speech(bad)
    assert "secret" not in str(exc.value) and "<" not in str(exc.value)
