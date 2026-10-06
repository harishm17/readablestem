"""MathML normalisation and equation scoring on known pairs."""

from __future__ import annotations

import pytest

from a11y_stem.eval.equations import (
    MathMLError,
    canonical,
    latex_to_mathml,
    score_latex,
    score_mathml,
    summarize,
)

# Shape copied from OpenStax archive output: semantics wrapper, nested mrows, a
# MathML-Content annotation, whitespace between tags, mtext subscripts, trailing period.
OPENSTAX_DELTA_T = """
<math display="block"><semantics><mrow>
 <mrow>
  <mi>Δ</mi><mi>t</mi><mo>=</mo><msub>
   <mi>t</mi>
   <mtext>f</mtext>
  </msub>
  <mo>−</mo><msub>
   <mi>t</mi>
   <mtext>0</mtext>
  </msub>
  <mo>.</mo>
 </mrow>
</mrow><annotation-xml encoding="MathML-Content"><mrow>
  <mi>Δ</mi><mi>t</mi></mrow></annotation-xml></semantics></math>
"""

DELTA_T_CANONICAL = (
    "<math><mi>Δ</mi><mi>t</mi><mo>=</mo><msub><mi>t</mi><mtext>f</mtext></msub>"
    "<mo>−</mo><msub><mi>t</mi><mtext>0</mtext></msub><mo>.</mo></math>"
)


def test_openstax_noise_is_stripped():
    assert canonical(OPENSTAX_DELTA_T) == DELTA_T_CANONICAL


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Attribute order and presentation-only attributes do not matter.
        (
            '<math><mi mathvariant="bold" class="x" id="1">v</mi></math>',
            '<math display="block"><mi id="2" mathvariant="bold">v</mi></math>',
        ),
        # Namespaces and the xmlns declaration are ignored.
        (
            '<m:math xmlns:m="http://www.w3.org/1998/Math/MathML"><m:mi>x</m:mi></m:math>',
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>',
        ),
        # Redundant mrows and mstyle wrappers.
        (
            "<math><mrow><mrow><mi>a</mi><mo>+</mo></mrow><mi>b</mi></mrow></math>",
            '<math><mstyle displaystyle="true"><mi>a</mi><mo>+</mo><mi>b</mi></mstyle></math>',
        ),
        # One-child mrow inside a fixed-arity slot.
        (
            "<math><mfrac><mrow><mi>d</mi></mrow><mrow><mi>t</mi></mrow></mfrac></math>",
            "<math><mfrac><mi>d</mi><mi>t</mi></mfrac></math>",
        ),
        # mfenced vs explicit fences.
        (
            "<math><mfenced><mi>a</mi><mi>b</mi></mfenced></math>",
            "<math><mo>(</mo><mi>a</mi><mo>,</mo><mi>b</mi><mo>)</mo></math>",
        ),
        # Spacing elements and invisible operators.
        (
            '<math><mi>f</mi><mo>&#x2061;</mo><mspace width="0.2em"/><mi>x</mi></math>',
            "<math><mi>f</mi><mi>x</mi></math>",
        ),
        # ASCII hyphen-minus vs U+2212, whitespace inside tokens.
        ("<math><mo> - </mo></math>", "<math><mo>−</mo></math>"),
        # Math alphanumeric characters vs mathvariant.
        ('<math><mi mathvariant="bold">v</mi></math>', "<math><mi>\U0001d42f</mi></math>"),
        # Default mathvariant values are dropped.
        ('<math><mi mathvariant="italic">x</mi></math>', "<math><mi>x</mi></math>"),
        ('<math><mi mathvariant="normal">sin</mi></math>', "<math><mi>sin</mi></math>"),
        # Zero line thickness spellings.
        (
            '<math><mfrac linethickness="0px"><mi>n</mi><mi>k</mi></mfrac></math>',
            '<math><mfrac linethickness="0"><mi>n</mi><mi>k</mi></mfrac></math>',
        ),
        # HTML named entities are accepted.
        ("<math><mtext>a&nbsp;b</mtext></math>", "<math><mtext>a b</mtext></math>"),
    ],
)
def test_equivalent_markup_normalises_identically(a, b):
    assert canonical(a) == canonical(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            "<math><msup><mi>x</mi><mn>2</mn></msup></math>",
            "<math><msup><mi>x</mi><mn>3</mn></msup></math>",
        ),
        (
            "<math><msup><mi>x</mi><mn>2</mn></msup></math>",
            "<math><msub><mi>x</mi><mn>2</mn></msub></math>",
        ),
        ("<math><mi>a</mi><mi>b</mi></math>", "<math><mi>b</mi><mi>a</mi></math>"),
        ('<math><mi mathvariant="bold">v</mi></math>', "<math><mi>v</mi></math>"),
        (
            "<math><mfrac><mi>n</mi><mi>k</mi></mfrac></math>",
            '<math><mfrac linethickness="0"><mi>n</mi><mi>k</mi></mfrac></math>',
        ),
    ],
)
def test_different_maths_stays_different(a, b):
    assert canonical(a) != canonical(b)


def test_latex_round_trip_exact_match():
    score = score_latex(r"\Delta t = t_\text{f} - t_\text{0}.", OPENSTAX_DELTA_T)
    assert score.exact_strict and score.exact_lenient
    assert score.ted == 0 and score.teds == 1.0


def test_lenient_forgives_token_type_and_trailing_period():
    # LaTeX t_0 gives <mn>0</mn>, OpenStax has <mtext>0</mtext>; no trailing period.
    score = score_latex(r"\Delta t = t_f - t_0", OPENSTAX_DELTA_T)
    assert not score.exact_strict
    assert score.exact_lenient
    # Two token relabels (mi f -> mtext f, mn 0 -> mtext 0) plus one inserted period.
    assert score.ted == 3
    assert score.gold_nodes == 12 and score.pred_nodes == 11
    assert score.teds == pytest.approx(1 - 3 / 12)


def test_wrong_operator_fails_both_exact_metrics():
    score = score_latex(r"\Delta t = t_\text{f} + t_\text{0}.", OPENSTAX_DELTA_T)
    assert not score.exact_strict and not score.exact_lenient
    assert score.ted == 1


def test_fraction_from_latex_matches_openstax_style():
    gold = (
        '<math display="block"><semantics><mrow><mrow><msub><mi>v</mi><mtext>avg</mtext>'
        "</msub><mo>=</mo><mfrac><mi>d</mi><mrow><mi>Δ</mi><mi>t</mi></mrow></mfrac>"
        "</mrow></mrow></semantics></math>"
    )
    assert score_latex(r"v_\text{avg} = \frac{d}{\Delta t}", gold).exact_strict


def test_unparseable_prediction_scores_zero_with_error():
    score = score_mathml("<math><mi>x</math>", OPENSTAX_DELTA_T)
    assert score.error is not None
    assert score.teds == 0.0 and not score.exact_strict


def test_bad_latex_scores_zero_with_error():
    score = score_latex(r"\frac{a}{", OPENSTAX_DELTA_T)
    assert score.error is not None
    assert score.teds == 0.0


def test_latex_to_mathml_returns_math_element():
    out = latex_to_mathml("x^2")
    assert out.startswith("<math") and "<msup>" in out


def test_latex_to_mathml_raises_mathml_error():
    with pytest.raises(MathMLError):
        latex_to_mathml(r"\frac{a}{")


def test_summarize():
    good = score_latex(r"\Delta t = t_\text{f} - t_\text{0}.", OPENSTAX_DELTA_T)
    near = score_latex(r"\Delta t = t_f - t_0", OPENSTAX_DELTA_T)
    summary = summarize([good, near])
    assert summary["n"] == 2
    assert summary["exact_strict"] == 0.5
    assert summary["exact_lenient"] == 1.0
    assert summary["mean_teds"] == pytest.approx((1.0 + 0.75) / 2)
    assert summarize([]) == {"n": 0}
