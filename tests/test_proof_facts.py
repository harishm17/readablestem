"""The proof numbers on the landing page and /limits come from PROOF.md once (web/proof_facts.py):
the committed snapshot equals what PROOF.md says now, each fact names its count and links to how
it was measured, and no fact says one tool beats another."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import proof_facts


def test_the_snapshot_is_what_proof_md_says_now():
    parsed = proof_facts.parse(proof_facts.PROOF_MD.read_text(encoding="utf-8"))
    snapshot = json.loads(proof_facts.SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot == asdict(parsed), (
        "PROOF.md changed: run `uv run python -m readablestem.web.proof_facts`"
    )


def test_every_fact_has_its_count_a_caveat_and_a_link():
    facts = proof_facts.load()
    assert facts.date == "2026-10-14" and "blind" in facts.blind
    assert len(facts.facts) == 4
    for fact in facts.facts:
        assert (
            any(ch.isdigit() for ch in fact.count)
            and fact.caveat
            and fact.href.startswith("https://")
        )
        assert "beat" not in (fact.label + fact.caveat).lower()
        assert "#" in fact.href  # a section of PROOF.md, not just the file


def test_where_a_chatbot_scored_higher_the_tile_says_so():
    exact = next(f for f in proof_facts.load().facts if "exactly right" in f.label)
    assert exact.value == "50%" and "62%" in exact.caveat


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    env = WebEnv(tmp_path_factory.mktemp("proof-strip"))
    with env.running() as anon:
        return {path: anon.get(path).text for path in ("/", "/limits")}


@pytest.mark.parametrize("path", ["/", "/limits"])
def test_the_pages_show_the_strip_with_set_date_and_how_measured_links(pages, path):
    page = " ".join(pages[path].split())
    assert 'class="proof-strip"' in page
    assert "Held-out set, 15 OpenStax sections, run recorded 2026-10-14" in page
    assert page.count("How it was measured") >= 4
    assert "89%" in page and "2,434 of 2,738" in page
