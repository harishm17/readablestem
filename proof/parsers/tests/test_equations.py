"""Equation rows: the primary path, the allowlist-free path and the normalised rows."""

from __future__ import annotations

import pytest
from a11y_stem.pipeline.validate import safe_latex_to_mathml

from parsers_bench import equations as eqs
from parsers_bench.intermediate import Equation


def gold_record(gid: str, latex: str, display: str = "block") -> dict:
    return {
        "id": gid,
        "kind": "equation",
        "display": display,
        "index_on_page": int(gid[-1]),
        "mathml": safe_latex_to_mathml(latex, display),
    }


def eq(order: int, latex: str | None, display: str = "block", raw: str | None = None) -> Equation:
    return Equation(order=order, display=display, latex=latex, raw=raw)


# ------------------------------------------------------------------ normalisation


def test_plan_rule_one_makes_upright_words_text_and_closes_up_spaced_letters() -> None:
    assert eqs.normalise_plan(r"\mathsf { d i s t a n c e }") == r"\text{distance}"
    assert eqs.normalise_plan(r"v_{\mathrm{avg}}") == r"v_{\text{avg}}"
    assert eqs.normalise_plan(r"\operatorname{sin} x") == r"\text{sin} x"


def test_plan_rule_one_leaves_a_single_letter_alone() -> None:
    assert eqs.normalise_plan(r"\mathrm{s}") == r"\mathrm{s}"


def test_plan_rule_two_makes_variant_capitals_plain() -> None:
    assert eqs.normalise_plan(r"\varDelta t + \varOmega") == r"\Delta t + \Omega"


def test_plus_rules_drop_tags_join_spaced_digits_and_make_unit_letters_text() -> None:
    assert eqs.normalise_plus(r"x = 1 5 0 \mathrm { s }\tag{2.5}") == r"x = 150 \text{s}"
    assert eqs.normalise_plus(r"\tag*{(3)} y") == "y"


def test_spaced_digits_rule_does_not_touch_digits_split_by_a_symbol() -> None:
    assert eqs.normalise_plus("1 + 2 3") == "1 + 23"
    assert eqs.normalise_plus(r"a_1 b_2") == r"a_1 b_2"


def test_normalisation_does_not_change_what_is_already_clean() -> None:
    clean = r"v = \frac{d}{t}"
    assert eqs.normalise_plus(clean) == clean
    assert eqs.normalise_plan(clean) == clean


# ------------------------------------------------------------------ conversion paths


def test_primary_refuses_a_command_the_allowlist_does_not_know() -> None:
    latex = eqs.prepare(r"x = y\tag{2.5}", "block", "primary")
    mathml, failed = eqs.convert(latex, "block", "primary")
    assert mathml is None
    assert "unknown_command" in failed


def test_normalised_plus_accepts_what_primary_refused() -> None:
    latex = eqs.prepare(r"x = y\tag{2.5}", "block", "normalised_plus")
    mathml, failed = eqs.convert(latex, "block", "normalised_plus")
    assert mathml is not None
    assert failed == []


def test_inline_trailing_punctuation_is_stripped_like_the_product_does() -> None:
    assert eqs.prepare("t = 5 ,", "inline", "primary") == "t = 5"
    assert eqs.prepare("t = 5 .", "block", "primary") == "t = 5 ."


# ------------------------------------------------------------------ scoring


def test_a_correct_transcription_scores_exact_end_to_end() -> None:
    gold = [gold_record("g0", r"v = \frac{d}{t}")]
    row = eqs.score_variant([eq(0, r"v = \frac{d}{t}")], gold, "primary", page_id="p")
    assert row["end_to_end_exact_lenient"] == 1.0
    assert row["detection_recall"] == 1.0
    assert row["unflagged_wrong_per_100"] == 0.0


def test_a_wrong_transcription_is_an_unflagged_wrong_equation() -> None:
    gold = [gold_record("g0", r"v = \frac{d}{t}")]
    row = eqs.score_variant([eq(0, r"v = \frac{d}{s}")], gold, "primary", page_id="p")
    assert row["end_to_end_exact_lenient"] == 0.0
    assert row["wrong_aligned"] == 1
    assert row["unflagged_wrong_per_100"] == 100.0


def test_a_tool_that_wrote_no_latex_has_nothing_to_score() -> None:
    gold = [gold_record("g0", r"v = \frac{d}{t}"), gold_record("g1", "a = b")]
    row = eqs.score_variant([eq(0, None, raw="v = d / t")], gold, "primary", page_id="p")
    assert row["transcribed"] == 0
    assert row["end_to_end_exact_lenient"] == 0.0
    assert row["detection_recall"] == 0.0
    assert row["unflagged_wrong_per_100"] == 0.0  # missed, not wrong


def test_an_allowlist_refusal_costs_primary_but_not_the_allowlist_free_row() -> None:
    gold = [gold_record("g0", r"x = y")]
    predicted = [eq(0, r"x = y \tag{2.5}")]
    primary = eqs.score_variant(predicted, gold, "primary", page_id="p")
    plus = eqs.score_variant(predicted, gold, "normalised_plus", page_id="p")
    assert primary["end_to_end_exact_lenient"] == 0.0
    assert primary["refused_or_failed"] == {"unknown_command": 1}
    assert plus["end_to_end_exact_lenient"] == 1.0


def test_g1_equations_are_left_out_of_the_headline_unflagged_rate() -> None:
    # A gold <mtext> that packs an operator in with letters is the known scorer gap G1.
    g1_mathml = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow><mtext>time =</mtext>'
        "<mi>x</mi></mrow></math>"
    )
    assert eqs.is_g1(g1_mathml) is True
    assert eqs.is_g1(safe_latex_to_mathml("a = b", "block")) is False
    assert eqs.is_g1("<not xml") is False


@pytest.mark.parametrize("variant", eqs.VARIANTS)
def test_every_variant_runs_on_an_empty_prediction(variant: str) -> None:
    gold = [gold_record("g0", "a = b")]
    row = eqs.score_variant([], gold, variant, page_id="p")
    assert row["end_to_end_exact_lenient"] == 0.0
    assert row["transcribed"] == 0
