# ruff: noqa: RUF001
"""The "chem" scorer (secondary): what it forgives for chemistry (the spellings the two kinds of
chemistry gold use), what it must not, and that the primary scorer is left as it was."""

from __future__ import annotations

import pytest

from readablestem.eval import equations_chem as chem
from readablestem.eval import equations_symbols as sym
from readablestem.eval import pipeline_eval as pe
from readablestem.eval.equations import latex_to_mathml, score_latex
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_eval_support import eq_fix, equation


def mml(body: str) -> str:
    return f"<math>{body}</math>"


def same(pred: str, gold: str) -> bool:
    return chem.score_chem(mml(pred), mml(gold)).exact


def latex_same(pred_latex: str, gold_latex: str) -> bool:
    """Both written by the pipeline's converter (the authored gold is made that way)."""
    return chem.score_chem(
        safe_latex_to_mathml(pred_latex, "inline"), safe_latex_to_mathml(gold_latex, "inline")
    ).exact


# ------------------------------------------------- the same chemistry, spelled differently


@pytest.mark.parametrize(
    "pred",
    [
        r"\text{H}_2\text{O}",
        r"\mathrm{H_2O}",
        r"\mathrm{H}_2\mathrm{O}",
        r"H_2O",
        r"\text{H}_{2}\text{O}",
        r"\mathrm{H}_{\mathrm{2}}O",
    ],
)
def test_water_is_water_in_every_markup_the_primary_scorer_calls_different(pred):
    gold = r"\mathrm{H_2O}"
    assert chem.score_chem_latex(pred, safe_latex_to_mathml(gold, "inline")).exact


def test_the_primary_scorer_does_not_see_it_and_is_not_changed():
    """`\\text{H}_2\\text{O}` is `mtext`, the gold's `\\mathrm{H_2O}` is one upright `mi` per
    letter: the primary scorer calls it wrong, the chemistry scorer right."""
    gold = safe_latex_to_mathml(r"\mathrm{H_2O}", "inline")
    assert not score_latex(r"\text{H}_2\text{O}", gold).exact_lenient
    assert chem.score_chem_latex(r"\text{H}_2\text{O}", gold).exact


def test_upright_italic_bold_and_text_are_one_letter():
    upright = (
        "<msub><mi mathvariant='normal'>H</mi><mn>2</mn></msub><mi mathvariant='normal'>O</mi>"
    )
    text = "<msub><mtext>H</mtext><mn>2</mn></msub><mtext>O</mtext>"
    italic = "<msub><mi>H</mi><mn>2</mn></msub><mi>O</mi>"
    bold = "<msub><mi mathvariant='bold'>H</mi><mn>2</mn></msub><mi mathvariant='bold'>O</mi>"
    assert same(upright, text) and same(text, italic) and same(bold, text)


def test_a_run_of_symbols_is_the_same_as_one_symbol_at_a_time():
    assert same(
        "<msub><mtext>NH</mtext><mn>4</mn></msub>",
        "<mi>N</mi><msub><mi>H</mi><mn>4</mn></msub>",
    )


@pytest.mark.parametrize("arrow", ["→", "⟶", "➔"])
def test_a_forward_arrow_is_one_arrow(arrow):
    gold = "<mn>2</mn><mi>A</mi><mo>→</mo><mi>B</mi>"
    assert same(f"<mn>2</mn><mi>A</mi><mi>{arrow}</mi><mi>B</mi>", gold)
    assert same(f"<mn>2</mn><mi>A</mi><mo>{arrow}</mo><mi>B</mi>", gold)


@pytest.mark.parametrize("arrow", ["⇌", "⇋", "⇄"])
def test_an_equilibrium_arrow_is_one_arrow(arrow):
    assert same(f"<mi>A</mi><mo>{arrow}</mo><mi>B</mi>", "<mi>A</mi><mo>⇌</mo><mi>B</mi>")


def test_a_label_over_the_arrow_is_a_script_marker_whatever_makes_it():
    over = "<mi>A</mi><mover><mo>→</mo><mi>Δ</mi></mover><mi>B</mi>"
    assert same(over, over.replace("<mover>", "<mover>").replace("Δ", "∆"))


def test_cell_notation_bars_set_as_box_drawing_characters_are_bars():
    gold = "<mi>Cu</mi><mo>│</mo><mi>Ag</mi><mo>║</mo><mi>H</mi>"
    pred = "<mi>Cu</mi><mo>|</mo><mi>Ag</mi><mo>‖</mo><mi>H</mi>"
    assert same(pred, gold)


def test_dash_hyphen_and_minus_in_a_charge_are_one_sign():
    for sign in ("-", "−", "–"):
        assert same(
            f"<msup><mtext>OH</mtext><mo>{sign}</mo></msup>",
            "<msup><mtext>OH</mtext><mtext>−</mtext></msup>",
        )


def test_a_number_the_gold_cut_in_two_is_one_number():
    """OpenStax: `<mn>0.2</mn><mn>57</mn>` for 0.257, `<mn>2.2</mn><mn>0</mn>` for 2.20."""
    assert same("<mn>0.257</mn><mi>V</mi>", "<mn>0.2</mn><mn>57</mn><mi>V</mi>")
    assert same("<mn>2.20</mn>", "<mn>2.2</mn><mn>0</mn>")
    assert not same("<mn>0.257</mn>", "<mn>0.2</mn><mn>75</mn>")


def test_a_thousands_comma_is_one_number_however_it_is_written():
    assert latex_same(r"96{,}485\ \text{C}", r"96{,}485\ \text{C}")
    assert same("<mn>96,485</mn>", "<mn>96</mn><mo>,</mo><mn>485</mn>")
    assert not same("<mn>96,485</mn>", "<mn>96</mn><mo>,</mo><mn>48</mn>")


def test_a_parenthesis_the_span_took_from_the_sentence_is_dropped_on_both_sides():
    """OpenStax gold `[Al(H2O)6]3+)`: the sentence's closing parenthesis is in the math."""
    gold = "<mo>[</mo><mi>Al</mi><mo>]</mo><msup><mi></mi><mrow><mn>3</mn><mo>+</mo></mrow></msup><mo>)</mo>"
    pred = "<mo>[</mo><mi>Al</mi><mo>]</mo><msup><mi></mi><mrow><mn>3</mn><mo>+</mo></mrow></msup>"
    assert same(pred, gold)
    # a bracket that is closed in the equation is a bracket, however it ends
    assert not same("<mi>x</mi><mo>(</mo><mi>y</mi><mo>)</mo>", "<mi>x</mi><mo>(</mo><mi>y</mi>")


def test_a_script_on_a_bracket_group_or_its_closing_bracket_is_the_same():
    group = "<msub><mrow><mo>[</mo><mi>A</mi><mo>]</mo></mrow><mn>0</mn></msub>"
    closing = "<mo>[</mo><mi>A</mi><msub><mo>]</mo><mn>0</mn></msub>"
    assert same(group, closing)


def test_a_nuclide_with_stacked_prefix_scripts_is_the_same_however_it_is_written():
    openstax = (
        "<msubsup><mrow/><mrow><mspace width='0.5em'/><mn>6</mn></mrow><mn>14</mn></msubsup>"
        "<mtext>C</mtext>"
    )
    assert chem.score_chem_latex(r"{}^{14}_{6}\text{C}", mml(openstax)).exact
    assert latex_same(r"{}^{14}_{6}\mathrm{C}", r"{}_{6}^{14}\mathrm{C}")


# ---------------------------------------------------------------- what it must not forgive


@pytest.mark.parametrize(
    ("pred", "gold"),
    [
        (r"\text{H}_2\text{O}", r"\text{H}_2\text{O}_2"),  # a count
        (r"\text{H}_2\text{O}", r"\text{H}\text{O}"),  # a missing count
        (r"\text{Fe}^{2+}", r"\text{Fe}^{3+}"),  # a charge
        (r"\text{Fe}^{2+}", r"\text{Fe}^{2-}"),  # a sign
        (r"\text{Na}^+", r"\text{Na}_+"),  # a charge moved to a subscript
        (r"\text{Cl}^-\text{(aq)}", r"\text{Cl}^-\text{(s)}"),  # a state
        (r"\text{Cl}^-\text{(aq)}", r"\text{Cl}^-"),  # a state dropped
        (r"A \longrightarrow B", r"A \rightleftharpoons B"),  # an arrow kind
        (r"A \longrightarrow B", r"A \longleftarrow B"),  # a direction
        (r"2\text{H}_2 + \text{O}_2", r"\text{H}_2 + \text{O}_2"),  # a coefficient
        (r"\text{CO}", r"\text{Co}"),  # an element (capital letters)
        (r"{}^{14}_{6}\text{C}", r"{}^{14}_{7}\text{C}"),  # an atomic number
        (r"{}^{14}_{6}\text{C}", r"{}^{14}\text{C}"),  # a missing atomic number
        (r"[\text{A}]_0", r"[\text{A}]^0"),  # sub for super
        (r"\text{Ca}(\text{OH})_2", r"\text{Ca}\text{OH}_2"),  # a bracket
    ],
)
def test_a_different_formula_is_a_different_formula(pred, gold):
    assert not chem.score_chem_latex(pred, safe_latex_to_mathml(gold, "inline")).exact


def test_a_missing_part_or_an_extra_part_is_wrong():
    gold = safe_latex_to_mathml(r"\text{A} + \text{B} \longrightarrow \text{C}", "inline")
    assert not chem.score_chem_latex(r"\text{A} + \text{B}", gold).exact
    assert not chem.score_chem_latex(
        r"\text{A} + \text{B} \longrightarrow \text{C} + \text{D}", gold
    ).exact


def test_a_unconvertible_reading_scores_zero_with_the_error():
    score = chem.score_chem("<math><mi>x</mi>", mml("<mi>x</mi>"))
    assert not score.exact and score.error and score.similarity == 0.0


def test_a_missing_proposal_is_wrong():
    assert not chem.missing_chem(mml("<mi>x</mi>")).exact


# -------------------------------------------------- what it is built on and where it sits


def test_everything_the_symbols_scorer_calls_exact_is_exact_here_for_chemistry():
    pairs = [
        (r"\text{OH}^-", r"\text{OH}^{-}"),
        (r"[\text{H}_3\text{O}^+]_0 = 0.100\ M", r"[\text{H}_3\text{O}^+]_0 = 0.100\ M"),
        (
            r"\text{Ag}^+\text{(aq)} + \text{Cl}^-\text{(aq)}",
            r"\text{Ag}^+\text{(aq)} + \text{Cl}^-\text{(aq)}",
        ),
    ]
    for pred, gold in pairs:
        gold_mml = latex_to_mathml(gold)
        assert sym.score_symbols_latex(pred, gold_mml).exact
        assert chem.score_chem_latex(pred, gold_mml).exact


def test_the_primary_scorer_and_the_symbols_scorer_are_unchanged_for_algebra():
    """Nothing in the pipeline's primary number moves: only a new field is added."""
    gold = latex_to_mathml(r"x^{2} + 3x = 0")
    assert score_latex(r"x^{2} + 3x = 0", gold).exact_lenient
    assert not score_latex(r"x^{2} + 3x = 1", gold).exact_lenient
    assert not chem.score_chem_latex(r"x^{2} + 3x = 1", gold).exact


def test_pairs_carry_exact_chem_and_the_predicted_mathml_beside_the_other_scorers():
    from readablestem.core.types import Element, Kind

    gold = [equation("p-1", 0, safe_latex_to_mathml(r"\mathrm{H_2O}", "inline"), "inline")]
    element = Element(
        id="p1-e0",
        kind=Kind.EQUATION,
        page=1,
        bbox=(0, 0, 10, 10),
        order=0,
        text="H2O",
        display="inline",
    )
    fixes = {"p1-e0": eq_fix("p1-e0", r"\text{H}_2\text{O}")}
    item = pe.document_page_item("p", gold, [element], fixes, [], [])
    pair = item["pairs"][0]
    assert pair["exact_lenient"] is False
    assert pair["exact_symbols"] is False
    assert pair["exact_chem"] is True
    assert pair["pred_mathml"]
    assert pe.document_metrics([item])["matched_exact_lenient"] == 0.0  # primary, unchanged
