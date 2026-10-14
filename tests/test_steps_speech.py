"""MathML -> speech: every gold equation reads without error, plus targeted rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_stem.eval.gold_scoring import iter_gold_records
from a11y_stem.pipeline.speech import SpeechError, mathml_to_speech
from a11y_stem.pipeline.validate import safe_latex_to_mathml

REPO = Path(__file__).resolve().parents[1]
GOLD = list(iter_gold_records(REPO / "data" / "gold", kind="equation"))
NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def say(latex: str) -> str:
    return mathml_to_speech(safe_latex_to_mathml(latex))


def gold(suffix: str) -> str:
    return next(r["mathml"] for r in GOLD if r["id"].endswith(suffix))


def test_gold_set_has_30_equations():
    assert len(GOLD) == 30


@pytest.mark.parametrize("record", GOLD, ids=lambda r: r["id"].removeprefix("openstax/physics/"))
def test_every_gold_equation_has_speech(record):
    speech = mathml_to_speech(record["mathml"])
    assert speech.strip()
    assert "<" not in speech and "\\" not in speech


def test_gold_examples_read_naturally():
    assert mathml_to_speech(gold("2-1-relative-motion-distance-and-displacement/eq-002")) == (
        "capital delta bold d equals bold d sub f minus bold d sub 0"
    )
    assert mathml_to_speech(gold("2-2-speed-and-velocity/eq-005")) == (
        "row 1: v sub avg equals distance over time; "
        "row 2: equals 150 km over 3.2 h; row 3: equals 47 km/h"
    )
    assert mathml_to_speech(gold("2-3-position-vs-time-graphs/eq-000")) == ("y equals m x plus b")


def test_openstax_and_latex_markup_read_the_same():
    """mstyle-bold + mtext Delta (OpenStax) and \\mathbf + \\Delta (LaTeX) normalise alike."""
    openstax = mathml_to_speech(gold("2-1-relative-motion-distance-and-displacement/eq-002"))
    assert openstax == say(r"\Delta \mathbf{d} = \mathbf{d}_\text{f} - \mathbf{d}_0.")


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
        # ions: the charge needs no "end power"; a nuclide's numbers come before its symbol
        (r"\text{SO}_4^{2-}", "SO sub 4 to the power 2 minus"),
        (r"\text{Fe}^{3+}", "Fe to the power 3 plus"),
        (r"{}^{14}_{6}\text{C}", "superscript 14 subscript 6 C"),
        (r"{}^{235}\text{U}", "superscript 235 U"),
        (r"{}^{0}_{-1}\text{e}", "superscript 0 subscript minus 1 e"),
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
        (r"\text{distance} = 5\ \text{km}", "distance equals 5 km"),
        # tables
        (
            r"\begin{array}{rcl} a & = & b \\ & = & c \end{array}",
            "row 1: a equals b; row 2: equals c",
        ),
        (
            r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}",
            "open parenthesis row 1: a, b; row 2: c, d close parenthesis",
        ),
        (r"\begin{aligned} a &= b \end{aligned}", "a equals b"),
        # sentence punctuation at the end is not read
        (r"x = 1.", "x equals 1"),
        (r"x = 1,", "x equals 1"),
    ],
)
def test_speech_rules(latex, expected):
    assert say(latex) == expected


def test_text_tokens_are_verbatim_except_lone_symbols():
    mathml = f"<math {NS}><mtext>time =</mtext><mtext>m/s)(46 s)</mtext><mtext>Δ</mtext></math>"
    assert mathml_to_speech(mathml) == "time equals m/s)(46 s) capital delta"


def test_unknown_symbols_fall_back_to_their_unicode_name():
    assert mathml_to_speech(f"<math {NS}><mo>⨁</mo></math>") == "n-ary circled plus operator"


def test_speech_is_deterministic():
    mathml = gold("2-2-speed-and-velocity/eq-010")
    assert {mathml_to_speech(mathml) for _ in range(3)} == {mathml_to_speech(mathml)}


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
