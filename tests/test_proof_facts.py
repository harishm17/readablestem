"""The proof numbers on the landing page and /limits come from PROOF.md once (web/proof_facts.py):
the committed snapshot equals what PROOF.md says now, each fact names its count and links to how
it was measured, and no fact says one tool beats another."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import proof_facts

ROOT = Path(__file__).resolve().parent.parent


def test_the_snapshot_is_what_proof_md_says_now():
    parsed = proof_facts.parse(proof_facts.PROOF_MD.read_text(encoding="utf-8"))
    snapshot = json.loads(proof_facts.SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot == asdict(parsed), (
        "PROOF.md changed: run `uv run python -m readablestem.web.proof_facts`"
    )


def test_every_fact_has_its_count_a_caveat_and_a_link():
    facts = proof_facts.load()
    assert facts.date == "2026-11-04" and "Registered" in facts.blind
    assert facts.pipeline == "version 0.5.9"
    assert len(facts.facts) == 4
    for fact in facts.facts:
        assert (
            any(ch.isdigit() for ch in fact.count)
            and fact.caveat
            and fact.href.startswith("https://")
        )
        assert "beat" not in (fact.label + fact.caveat).lower()
        assert "#" in fact.href  # a section of PROOF.md, not just the file
        assert fact.count == "1,715 gold equations in 26 documents"


def test_the_tiles_are_the_registered_fresh5_numbers_and_say_where_goals_were_missed():
    """docs/eval-equation-fresh5.md is the report; PROOF.md and the tiles quote it."""
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


@pytest.mark.parametrize("path", ["/", "/limits"])
def test_the_pages_show_the_strip_with_set_date_and_how_measured_links(pages, path):
    page = " ".join(pages[path].split())
    assert 'class="proof-strip"' in page
    if path == "/limits":
        assert "Fresh5 set, 26 documents, run recorded 2026-11-04 (version 0.5.9)" in page
        assert (
            "spoken-equal, end to end" in page and "wrong equations nobody is asked about" in page
        )
    else:  # the landing page says the same set and numbers in plain words
        assert "26 documents the tool had never seen" in page
        assert "4 November 2026, version 0.5.9" in page
        assert "spoken-equal" not in page
    assert page.count("How it was measured") >= 4
    for number in ("0.840", "0.793", "5.0", "0.46", "1,715 gold equations in 26 documents"):
        assert number in page, number
    assert "0.836" in page and "not met" in page
    assert "beat" not in page.lower().replace("beats the chatbot baseline on", "")
