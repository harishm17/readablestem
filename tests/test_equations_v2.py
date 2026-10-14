# ruff: noqa: RUF001
"""Scorer v2 (stream comparison, backlog T2-12): what it forgives, what it must not, and
that it sits beside the unchanged primary scorer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from a11y_stem.core.types import Element, Kind
from a11y_stem.eval import equations_v2 as v2
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval.equations import latex_to_mathml, score_latex
from a11y_stem.eval.gold_scoring import iter_gold_records, load_predictions, score_equations
from test_eval_support import eq_fix, equation

GOLD = Path(__file__).parent.parent / "data" / "gold"
PREDICTIONS = Path(__file__).parent / "fixtures" / "sample_latex_predictions.jsonl"


def mml(body: str) -> str:
    return f"<math>{body}</math>"


def exact(pred: str, gold: str) -> bool:
    return v2.score_stream(mml(pred), mml(gold)).exact


# ------------------------------------------------------------- what it forgives


def test_packed_mtext_is_split_into_text_and_operator():
    packed = "<mtext>time =</mtext><mfrac><mtext>distance</mtext><mi>v</mi></mfrac>"
    split = "<mtext>time</mtext><mo>=</mo><mfrac><mtext>distance</mtext><mi>v</mi></mfrac>"
    assert exact(split, packed)
    assert v2.stream_of(mml("<mtext>m/s)(46 s)</mtext>")) == [
        "m",
        "/",
        "s",
        ")",
        "(",
        "46",
        "s",
        ")",
    ]


def test_a_word_is_its_letters():
    """The answer key writes a subscript "avg" as three identifiers in one place and as
    the text "avg" in another (data/gold, Physics 2.2)."""
    assert exact(
        "<msub><mi>v</mi><mtext>avg</mtext></msub>",
        "<msub><mi>v</mi><mrow><mi>a</mi><mi>v</mi><mi>g</mi></mrow></msub>",
    )
    assert not exact(
        "<msub><mi>v</mi><mtext>avg</mtext></msub>", "<msub><mi>v</mi><mtext>avs</mtext></msub>"
    )


def test_a_sign_inside_mn_is_the_same_as_a_separate_sign():
    assert exact("<mo>−</mo><mn>5</mn>", "<mn>-5</mn>")
    assert exact("<mo>-</mo><mn>5</mn>", "<mn>−5</mn>")


def test_dash_micro_prime_and_degree_variants_are_one_atom():
    assert exact("<mo>−</mo><mi>x</mi>", "<mo>–</mo><mi>x</mi>")  # en dash
    assert exact("<mn>5</mn><mi>µ</mi><mi>m</mi>", "<mn>5</mn><mi>μ</mi><mi>m</mi>")
    assert exact("<mi>f</mi><mo>′</mo>", "<mi>f</mi><mo>'</mo>")
    assert exact("<mi>f</mi><mo>″</mo>", "<mi>f</mi><mo>′</mo><mo>′</mo>")
    assert exact("<mn>90</mn><mo>°</mo>", "<mn>90</mn><mo>∘</mo>")
    assert exact("<mn>90</mn><mo>°</mo>", "<mtext>90°</mtext>")


def test_a_degree_or_prime_written_as_a_superscript_is_the_inline_character():
    assert exact("<mn>90</mn><mo>°</mo>", "<msup><mn>90</mn><mo>∘</mo></msup>")
    assert exact("<mi>f</mi><mo>′</mo>", "<msup><mi>f</mi><mo>′</mo></msup>")
    assert score_latex(r"f^{\prime}", mml("<mi>f</mi><mo>′</mo>")).exact_lenient is False
    assert v2.score_stream_latex(r"f^{\prime}", mml("<mi>f</mi><mo>′</mo>")).exact


def test_a_decimal_point_token_joins_its_neighbours():
    assert exact("<mn>3</mn><mo>.</mo><mn>5</mn>", "<mn>3.5</mn>")


def test_one_token_or_several_in_a_script_slot_are_the_same_group():
    assert exact(
        "<msup><mn>10</mn><mrow><mo>−</mo><mn>3</mn></mrow></msup>",
        "<msup><mn>10</mn><mn>−3</mn></msup>",
    )


def test_everything_the_primary_lenient_scorer_calls_exact_is_exact_here():
    """v2 only forgives; on the sample's correct LaTeX it never loses a primary match."""
    gold = {r["id"]: r["mathml"] for r in iter_gold_records(GOLD, kind="equation")}
    for line in PREDICTIONS.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        primary = score_latex(row["latex"], gold[row["id"]])
        secondary = v2.score_stream_latex(row["latex"], gold[row["id"]])
        assert secondary.exact or not primary.exact_lenient, row["id"]


# ----------------------------------------------------- what it must not forgive


@pytest.mark.parametrize(
    ("pred", "gold"),
    [
        ("<mn>36</mn>", "<mn>63</mn>"),
        ("<mn>9.81</mn>", "<mn>98.1</mn>"),
        ("<mi>x</mi><mo>+</mo><mi>y</mi>", "<mi>x</mi><mo>−</mo><mi>y</mi>"),
        ("<mi>x</mi><mo>≤</mo><mi>y</mi>", "<mi>x</mi><mo>&lt;</mo><mi>y</mi>"),
        ("<mi>w</mi>", "<mi>W</mi>"),  # letter case
        ('<mi mathvariant="bold">d</mi>', "<mi>d</mi>"),  # a vector is not a scalar
        ("<mi>a</mi><mi>b</mi>", "<mi>a</mi><mo>+</mo><mi>b</mi>"),
        # structure is kept: a fraction is not a row
        ("<mfrac><mi>a</mi><mi>b</mi></mfrac>", "<mi>a</mi><mo>/</mo><mi>b</mi>"),
        ("<mfrac><mi>a</mi><mi>b</mi></mfrac>", "<mfrac><mi>b</mi><mi>a</mi></mfrac>"),
        ("<msup><mi>x</mi><mn>2</mn></msup>", "<msub><mi>x</mi><mn>2</mn></msub>"),
        ("<mn>5</mn><mo>°</mo>", "<mn>5</mn>"),
    ],
)
def test_real_differences_stay_different(pred, gold):
    assert not exact(pred, gold)


def test_a_wrong_number_is_not_close_to_a_right_one_in_similarity_terms_only():
    score = v2.score_stream(mml("<mn>36</mn><mi>m</mi>"), mml("<mn>63</mn><mi>m</mi>"))
    assert not score.exact and 0 < score.similarity < 1


def test_an_unparseable_prediction_scores_zero_with_the_error():
    score = v2.score_stream("<math><mi>x</mi>", mml("<mi>x</mi>"))
    assert (score.exact, score.similarity) == (False, 0.0) and score.error
    assert v2.score_stream_latex(r"\frac{", mml("<mi>x</mi>")).error


# ----------------------------------------------------------- the known gaps


def test_the_sample_set_gaps_close_under_v2_and_the_primary_numbers_do_not_move():
    gold = list(iter_gold_records(GOLD, kind="equation"))
    result = score_equations(gold, load_predictions(PREDICTIONS))
    summary = result["summary"]
    assert summary["exact_lenient"] == pytest.approx(25 / 30)  # primary, unchanged
    secondary = summary["secondary_stream_v2"]
    assert secondary["exact"] == 1.0 and secondary["n"] == 30
    assert "secondary" in secondary["label"]
    assert all("exact_stream_v2" in row for row in result["items"])


def test_self_check_scores_one_on_both_scorers():
    gold = list(iter_gold_records(GOLD, kind="equation"))
    summary = score_equations(gold, None)["summary"]
    assert summary["exact_lenient"] == 1.0 and summary["secondary_stream_v2"]["exact"] == 1.0


# ------------------------------------------------------------ split and merged


def test_a_gold_equation_in_pieces_is_credited_when_the_pieces_make_it():
    gold = v2.stream_of(latex_to_mathml(r"x = 3, \quad y = 4"))
    assert v2.credit_split([v2.stream_of_latex("x = 3"), v2.stream_of_latex("y = 4")], gold)
    assert not v2.credit_split([v2.stream_of_latex("x = 3")], gold)  # one piece is no split
    assert not v2.credit_split([v2.stream_of_latex("x = 3"), v2.stream_of_latex("y = 5")], gold)


def test_a_prediction_that_merges_gold_equations_is_credited_from_the_first():
    golds = [v2.stream_of_latex("x = 3"), v2.stream_of_latex("y = 4"), v2.stream_of_latex("z")]
    merged = v2.stream_of_latex("x = 3, y = 4")
    assert v2.credit_merged(merged, golds) == 2
    assert v2.credit_merged(v2.stream_of_latex("y = 4, z"), golds) == 0  # not from the first
    assert v2.credit_merged(v2.stream_of_latex("x = 3, y = 5"), golds) == 0
    assert v2.credit_merged(golds[0], golds) == 0  # an exact match is not a merge


# ------------------------------------------------------ pipeline integration


def element(index: int, text: str, display: str = "block", parent: str | None = None) -> Element:
    return Element(
        id=f"p1-e{index}",
        kind=Kind.EQUATION,
        page=0,
        bbox=(0, 0, 1, 1),
        order=index,
        text=text,
        display=display,  # type: ignore[arg-type]
        parent_id=parent,
    )


PACKED = (
    '<math display="block"><mtext>time =</mtext><mfrac><mtext>distance</mtext>'
    "<mtext>speed</mtext></mfrac></math>"
)


def test_a_matched_pair_is_wrong_for_the_primary_scorer_and_right_for_v2():
    gold = [equation("p-1", 0, PACKED, "block")]
    fixes = {"p1-e0": eq_fix("p1-e0", r"\text{time} = \frac{\text{distance}}{\text{speed}}")}
    item = pe.document_page_item("p", gold, [element(0, "time = distance speed")], fixes, [], [])
    pair = item["pairs"][0]
    assert pair["exact_lenient"] is False and pair["exact_stream_v2"] is True
    metrics = pe.document_metrics([item])
    assert metrics["matched_exact_lenient"] == 0.0  # primary, unchanged
    secondary = metrics["secondary_stream_v2"]
    assert secondary["matched_exact"] == 1.0 and secondary["end_to_end_exact"] == 1.0
    assert "secondary" in secondary["label"]


def test_a_split_gold_equation_is_credited_apart_from_the_exact_pairs():
    gold = [
        equation(
            "p-1",
            0,
            '<math display="inline"><mi>x</mi><mo>=</mo><mn>2</mn><mo>+</mo><mi>y</mi></math>',
            "inline",
        )
    ]
    predicted = [element(0, "x = 2", "inline", "p1-e5"), element(1, "+ y", "inline", "p1-e5")]
    fixes = {"p1-e0": eq_fix("p1-e0", "x = 2"), "p1-e1": eq_fix("p1-e1", "+ y")}
    item = pe.document_page_item("p", gold, predicted, fixes, [], [])
    assert item["split_gold_ids"] == [gold[0]["id"]]
    assert item["stream_credited"]["split"] == [gold[0]["id"]]
    secondary = pe.document_metrics([item])["secondary_stream_v2"]
    assert secondary["end_to_end_exact"] == 0.0
    assert secondary["end_to_end_exact_with_credit"] == 1.0
    assert secondary["credited_split"] == 1 and secondary["credited_merged"] == 0


def test_a_merged_prediction_is_credited_for_every_gold_equation_it_covers():
    gold = [
        equation("p-1", 0, '<math display="block"><mi>x</mi><mo>=</mo><mn>3</mn></math>', "block"),
        equation("p-1", 1, '<math display="block"><mi>y</mi><mo>=</mo><mn>4</mn></math>', "block"),
    ]
    fixes = {"p1-e0": eq_fix("p1-e0", r"x = 3, \quad y = 4")}
    item = pe.document_page_item("p", gold, [element(0, "x = 3, y = 4")], fixes, [], [])
    assert item["matched"] == 1 and len(item["missed_gold_ids"]) == 1
    assert item["stream_credited"]["merged"] == [gold[0]["id"], gold[1]["id"]]
    secondary = pe.document_metrics([item])["secondary_stream_v2"]
    assert secondary["end_to_end_exact_with_credit"] == 1.0 and secondary["credited_merged"] == 2


def test_equation_items_carry_the_secondary_fields_and_wrong_stays_primary():
    from test_eval_support import settings

    record = equation("p-1", 0, PACKED, "block")
    fix = eq_fix("p1-e0", r"\text{time} = \frac{\text{distance}}{\text{speed}}")
    item = pe.equation_item(record, fix, settings())
    assert item["wrong"] is True  # primary: not exact_lenient
    assert item["exact_stream_v2"] is True and item["stream_similarity"] == 1.0
    metrics = pe.equation_metrics([item])
    assert metrics["exact_lenient"] == 0.0
    assert metrics["secondary_stream_v2"]["exact"] == 1.0
