"""MathML -> speech: every gold equation reads without error, plus targeted rules."""

from __future__ import annotations

import re
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
        (r"\frac{\Delta d}{\Delta t}", "cap delta d over cap delta t"),
        (r"\frac{a + b}{c}", "the fraction a plus b over c end fraction"),
        (
            r"\frac{1}{\frac{1}{x}}",
            "the fraction 1 over the fraction 1 over x end fraction end fraction",
        ),
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
        (r"x_i^2", "x sub i end sub squared"),
        (r"a+\frac{b}{c}", "a plus the fraction b over c end fraction"),
        (r"\sqrt{a}+b", "the square root of a end root plus b"),
        (r"|x|", "the absolute value of x"),
        (r"|x|+1", "the absolute value of x end absolute value plus 1"),
        (r"2|x-3|", "2 the absolute value of x minus 3 end absolute value"),
        # bars that can be signs of their own are not called an absolute value
        (r"a|b, c|d", "a vertical bar b comma c vertical bar d"),
        (
            r"|a\rangle\langle b|",
            "vertical bar a close angle bracket open angle bracket b vertical bar",
        ),
        # ... and neither is the size of a set, a determinant or a subgroup index
        (
            r"|A \cup B| = |G:H|",
            "vertical bar cap A union cap B vertical bar equals "
            "vertical bar cap G colon cap H vertical bar",
        ),
        (r"\det A = |A|", "determinant cap A equals vertical bar cap A vertical bar"),
        (r"\|x\|", "the norm of x"),
        # a norm with a subscript is still a norm (its second bar is a script's base), and an
        # evaluation bar with a script is not one
        (r"\|A\|_F", "the norm of cap A end norm sub cap F"),
        (r"f|_a + g|_b", "f vertical bar sub a end sub plus g vertical bar sub b"),
        # a bar before an absolute value in set braces is "such that"; with its own pair first
        # the third bar is
        (
            r"\{x | |x|<1\}",
            "open brace x such that the absolute value of x less than 1 close brace",
        ),
        (
            r"\{|x| | x \in S\}",
            "open brace the absolute value of x such that x is an element of cap S close brace",
        ),
        # a cross between things that are not numbers says what is written; between numbers
        # it is a product
        (r"|A \times B|", "vertical bar cap A times cap B vertical bar"),
        (r"|\vec{a}\times\vec{b}|", "vertical bar vector a cross vector b vertical bar"),
        (
            r"|3 \times 10^{-5}|",
            "the absolute value of 3 times 10 to the power minus 5 end power end absolute value",
        ),
        (r"\bar{a+b}", "the quantity a plus b end quantity bar"),
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
        (r"F \propto m", "cap F proportional to m"),
        (r"a \rightarrow b", "a right arrow b"),
        # letters, Greek, fonts, accents
        (r"\Delta t", "cap delta t"),
        (r"\delta t", "delta t"),
        (r"\theta + \omega", "theta plus omega"),
        (r"\mathbf{d}", "bold d"),
        (r"\vec{d}", "vector d"),
        (r"\hat{x}", "x hat"),
        (r"\bar{x}", "x bar"),
        (r"\dot{x}", "x dot"),
        (r"\mathbb{R}", "double-struck cap R"),
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
        (r"a \not\in B", "a is not an element of cap B"),
        (r"a \not= b", "a not equal to b"),
        (r"a \nleq b", "a is neither less than nor equal to b"),
        (r"A \not\subset B", "cap A is not a subset of cap B"),
        (r"\textbf{F} = m a", "bold cap F equals m a"),
        # a sign is said for what it does: a bar, a dot, a cross, a script
        (r"P(A \mid B)", "cap P open parenthesis cap A given cap B close parenthesis"),
        (r"\{x \mid x>0\}", "open brace x such that x greater than 0 close brace"),
        (r"\{n \mid 3 \mid n\}", "open brace n such that 3 divides n close brace"),
        (r"3 \mid 12", "3 divides 12"),
        (r"\mathbf{a} \cdot \mathbf{b}", "bold a dot bold b"),
        (r"\mathbf{a} \times \mathbf{b}", "bold a cross bold b"),
        (r"2 \cdot 3", "2 times 3"),
        (r"A^T", "cap A transpose"),
        # a bracketed run of matrices is a matrix too (it once recursed without end)
        (r"(A+B)^T", "open parenthesis cap A plus cap B close parenthesis transpose"),
        (r"(x+y)^T", "open parenthesis x plus y close parenthesis to the power cap T"),
        (r"x^*", "x star"),
        (r"\sin^{-1} x", "inverse sine x"),
        (r"\lim_{x \to 0^+} f", "the limit as x approaches 0 plus of f"),
        (
            r"X \sim N(0,1)",
            "cap X is distributed as cap N open parenthesis 0 comma 1 close parenthesis",
        ),
        (r"A \setminus B", "cap A set minus cap B"),
        (r"\mathit{diff}", "diff"),
        # sentence punctuation at the end is not read
        (r"x = 1.", "x equals 1"),
        (r"x = 1,", "x equals 1"),
    ],
)
def test_speech_rules(latex, expected):
    assert say(latex) == expected


_ENDS = r"(?: end (?:power|sub|root|fraction|absolute value|norm|enclosure|quantity|bar))+$"


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
        # one saying must not be the other up to a trailing "end ..." word
        (r"e^{-x}+1", r"e^{-x+1}"),
        (r"\sqrt{a}+b", r"\sqrt{a+b}"),
        (r"\sqrt{\sqrt{a}+b}", r"\sqrt{\sqrt{a+b}}"),
        (r"a+\frac{b}{c}", r"\frac{a+b}{c}"),
        (r"x_i+1", r"x_{i+1}"),
        (r"\bar{a}+b", r"\bar{a+b}"),
        (r"\left(\frac{a}{b}\right)^n+1", r"\left(\frac{a}{b}\right)^{n+1}"),
        (r"|x|+1", r"|x+1|"),
        (r"\|x\|+1", r"\|x+1\|"),
        (r"\sum_{i=1}^{n} a_i+1", r"\sum_{i=1}^{n} a_{i+1}"),
    ],
)
def test_two_different_formulas_are_not_said_alike(one, other):
    a, b = say(one), say(other)
    # alike, or alike up to the "end ..." words that close the last scopes
    assert re.sub(_ENDS, "", a) != re.sub(_ENDS, "", b)


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


def test_a_stored_reading_from_older_rules_is_made_again_unless_a_person_wrote_it():
    from readablestem.core.types import EquationFix
    from readablestem.pipeline.speech import SPEECH_VERSION, reading_for

    mathml = safe_latex_to_mathml("K = 2")

    def stored(version: int) -> EquationFix:
        return EquationFix(
            latex="K = 2", mathml=mathml, speech="capital K equals 2", speech_version=version
        )

    assert reading_for(stored(0)) == "cap K equals 2"  # written before rules had a version
    assert (
        reading_for(stored(SPEECH_VERSION)) == "capital K equals 2"
    )  # the rules in force wrote it
    assert reading_for(stored(-1)) == "capital K equals 2"  # a person wrote it: never replaced
