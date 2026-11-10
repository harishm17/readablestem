"""The proof numbers on the landing page and /limits come from the registered fresh5 report once
(web/proof_facts.py): the committed snapshot equals what the report says now, each fact names
its count and links to how it was measured, and no fact says one tool beats another."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import proof_facts

ROOT = Path(__file__).resolve().parent.parent


def test_the_snapshot_is_what_the_report_says_now():
    parsed = proof_facts.parse(proof_facts.REPORT.read_text(encoding="utf-8"))
    snapshot = json.loads(proof_facts.SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot == asdict(parsed), (
        "the fresh5 report changed: run `uv run python -m readablestem.web.proof_facts`"
    )


def test_the_tiles_are_the_registered_fresh5_numbers_and_say_where_goals_were_missed():
    """docs/eval-equation-fresh5.md is the report; the tiles quote it."""
    report = (ROOT / "docs/eval-equation-fresh5.md").read_text(encoding="utf-8")
    primary, exact, silent, recall = proof_facts.load().facts
    assert primary.value == "0.840" and "0.840** [0.791, 0.876]" in report
    assert "0.836" in primary.caveat and "no difference" in primary.caveat
    assert exact.value == "0.793" and "0.770" in exact.caveat and "| 0.793 [0.736, 0.834]" in report
    assert silent.value == "5.0" and "9.6" in silent.caveat and "4.0" in silent.caveat
    assert "5.02 [3.55, 6.39]" in report and "9.56 [5.86, 14.55]" in report
    assert recall.value == "0.46" and "0.60" in recall.caveat and "not met" in recall.caveat
    assert "0.461 [0.309, 0.613]" in report
    assert "no difference this set can detect" in report
    assert "is not made" in report  # the registered verdict: no claim of beating the chatbot


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    env = WebEnv(tmp_path_factory.mktemp("proof-strip"))
    with env.running() as anon:
        return {path: anon.get(path).text for path in ("/", "/limits")}
