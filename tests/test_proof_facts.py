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
        assert "#" in fact.href  # a section of the report, not just the file
        assert fact.count == "1,715 gold equations in 26 documents"


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


def test_the_landing_page_has_the_chart_and_one_evidence_link_not_the_tiles(pages):
    """The four tiles and their method are on /limits; the landing page draws the newest
    registered result as a chart and links there (ux2 lane D)."""
    page = " ".join(pages["/"].split())
    assert 'class="proof-strip"' not in page and "How it was measured" not in page
    assert "26 documents the tool had never seen" in page
    assert "4 November 2026, version 0.5.9" in page
    assert 'href="/limits#accuracy">Evidence: the four measures' in page
    assert "so it was not met" not in page  # the goals are on /limits with the tiles
    for number in ("0.840", "0.836", "0.793", "0.770", "5.0", "9.6", "1,715"):
        assert number in page, number
    assert "no difference" in page and "beat" not in page.lower()


def test_the_chart_is_the_registered_results_file_and_says_what_a_listener_hears():
    """The bars are `data/realworld/results-fresh5.json`'s numbers for ReadableSTEM, the Opus
    chatbot and the Haiku chatbot; the snapshot equals what the files say now."""
    results = json.loads(proof_facts.RESULTS.read_text(encoding="utf-8"))
    chart = proof_facts.load().chart
    shown = {row.measure: row for row in chart}
    names = [line.system for line in chart[0].lines]
    assert names == ["ReadableSTEM", "Chatbot on Opus", "Chatbot on Haiku"]
    by_key = {key: words for key, words, *_ in proof_facts.MEASURES}
    for key, words in by_key.items():
        for arm, line in zip(
            ("Candidate", "Chatbot, Opus", "Chatbot, Haiku"), shown[words].lines, strict=True
        ):
            point = results["arms"][arm][key]["point"]
            if key.startswith("flag recall") and point == 0:
                assert line.value == "never asks" and line.width == 0
            else:
                assert float(line.value.lstrip("$")) == pytest.approx(
                    point, abs=0.006 if point < 1 else 0.06
                )
    values = {row.measure: [line.value for line in row.lines] for row in chart}
    assert values["Reads the same to a listener as the original"] == ["0.840", "0.836", "0.816"]
    assert values["Exactly right, symbol for symbol"] == ["0.793", "0.770", "0.732"]
    assert values["Wrong, and nobody is asked, per 100 equations"] == ["5.0", "9.6", "11.9"]
    assert values["Wrong and not flagged, or not found at all, per 100 equations"] == [
        "11.0",
        "16.4",
        "18.4",
    ]
    assert values["Cost per page"] == ["$0.022", "$0.068", "$0.002"]
    assert values["Wrong equations the tool asks you about"] == ["0.46", "never asks", "never asks"]
    for row in chart:
        assert row.better in {"Higher is better", "Lower is better"}
        assert all(0 <= line.width <= 100 for line in row.lines)


def test_the_headings_and_figures_block_is_the_first_registered_tests_counts():
    proof = (ROOT / "PROOF.md").read_text(encoding="utf-8")
    first = proof_facts.load().first_test
    assert "| heading level_accuracy (among matched) | 497/497 | 320/939 |" in proof
    assert "| figure judge_factual_error_rate (among judged) | 0/138 | 3/134 |" in proof
    assert (first.levels_ours, first.levels_chatbot) == ("497 of 497 (100%)", "320 of 939 (34%)")
    assert (first.errors_ours, first.errors_chatbot) == ("0 of 138", "3 of 134")
    assert "October 2026" in first.set_label and "15 OpenStax sections" in first.set_label


def test_the_limits_page_shows_the_strip_with_set_date_and_how_measured_links(pages):
    page = " ".join(pages["/limits"].split())
    assert 'class="proof-strip"' in page
    assert "Fresh5 set, 26 documents, run recorded 2026-11-04 (version 0.5.9)" in page
    assert "spoken-equal, end to end" in page and "wrong equations nobody is asked about" in page
    assert page.count("How it was measured") >= 4
    for number in ("0.840", "0.793", "5.0", "0.46", "1,715 gold equations in 26 documents"):
        assert number in page, number
    assert "0.836" in page and "not met" in page
    assert "beat" not in page.lower().replace("beats the chatbot baseline on", "")
