"""Scorer check on the committed gold sample with known-correct LaTeX predictions.

`make eval-plumbing` (gold scored against itself) only proves the plumbing works: it
would print 1.0 even if normalisation were broken. This test scores a committed set of
correct LaTeX transcriptions (tests/fixtures/sample_latex_predictions.jsonl) against the
OpenStax MathML and requires a lenient exact match for every equation. A failure means
the scorer would mark a correct answer wrong: a normalisation gap.

Rows marked `known_gap` are gaps already found and left open on purpose (see the
equations.py docstring and ADR 0001 question 3). They are strict xfails: if a change
makes one pass, the test fails so the fixture gets updated.

The LaTeX in the fixture is a scorer test input written from the gold MathML, not a gold
label. It is worth spot-checking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readablestem.eval.equations import score_latex
from readablestem.eval.gold_scoring import iter_gold_records

REPO = Path(__file__).resolve().parents[1]
GOLD_DIR = REPO / "data" / "gold"
PREDICTIONS = Path(__file__).parent / "fixtures" / "sample_latex_predictions.jsonl"

GOLD = {r["id"]: r for r in iter_gold_records(GOLD_DIR, kind="equation")}
PREDS = [json.loads(line) for line in PREDICTIONS.read_text(encoding="utf-8").splitlines()]


def _params():
    for pred in PREDS:
        marks = []
        if "known_gap" in pred:
            marks.append(pytest.mark.xfail(strict=True, reason=pred["known_gap"]))
        yield pytest.param(pred, id=pred["id"].removeprefix("openstax/physics/"), marks=marks)


def test_every_sample_equation_has_one_prediction():
    ids = [p["id"] for p in PREDS]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(GOLD), "add or remove fixture rows when the sample changes"


@pytest.mark.parametrize("pred", list(_params()))
def test_correct_latex_matches_gold_leniently(pred):
    score = score_latex(pred["latex"], GOLD[pred["id"]]["mathml"])
    assert score.error is None
    assert score.exact_lenient, f"normalisation gap: {pred['latex']}"


@pytest.mark.parametrize("pred", [pytest.param(p, id=p["id"].split("/", 3)[-1]) for p in PREDS])
def test_correct_latex_matches_gold_under_the_secondary_stream_scorer(pred):
    """The known gaps above are packing gaps; the labelled secondary scorer
    (`eval.equations_v2`, backlog T2-12) closes all of them. The primary stays strict."""
    from readablestem.eval.equations_v2 import score_stream_latex

    assert score_stream_latex(pred["latex"], GOLD[pred["id"]]["mathml"]).exact
