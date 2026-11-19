"""The proof numbers on the landing page and /limits come from the registered fresh5 report once
(web/proof_facts.py): the committed snapshot equals what the report says now, each fact names
its count and links to how it was measured, no fact says one tool beats another, a result file is
shown as run under the live service's settings only when its stamp says so, and the figures the
retired scorer made appear on no public page."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import offices, proof_facts

ROOT = Path(__file__).resolve().parent.parent


def test_the_snapshot_is_what_the_report_says_now():
    parsed = proof_facts.parse(proof_facts.REPORT.read_text(encoding="utf-8"))
    snapshot = json.loads(proof_facts.SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot == asdict(parsed), (
        "the fresh5 report changed: run `uv run python -m readablestem.web.proof_facts`"
    )


def test_the_tiles_are_the_registered_fresh5_numbers_and_the_retired_ones_are_gone():
    """docs/eval-equation-fresh5.md is the report; the tiles quote it."""
    report = (ROOT / "docs/eval-equation-fresh5.md").read_text(encoding="utf-8")
    primary, exact, decisions = proof_facts.load().facts
    assert primary.value == "0.840" and "0.840** [0.791, 0.876]" in report
    assert "0.836" in primary.caveat and "no difference" in primary.caveat
    assert exact.value == "0.793" and "0.770" in exact.caveat and "| 0.793 [0.736, 0.834]" in report
    assert decisions.value == "2.9" and "2.87 [1.74, 4.57]" in report
    assert "no difference this set can detect" in report
    assert "is not made" in report  # the registered verdict: no claim of beating the chatbot
    chart = {row.measure for row in proof_facts.load().chart}
    assert not [m for m in chart if "nobody is asked" in m or "not flagged" in m or "asks you" in m]


def test_a_result_file_is_called_the_live_settings_only_when_its_stamp_is_the_profile():
    # The fresh5 file predates the stamp: it says its settings were not recorded.
    unstamped = proof_facts.load().settings_line
    assert "did not record its settings" in unstamped and "live service uses" not in unstamped
    # A stamp equal to the profile's hash earns the sentence; a different one is refused.
    stamped = proof_facts.settings_line({"profile_sha256": "abc"}, "0.6.0", "2026-12-01", "abc")
    assert "the settings the live service uses" in stamped and "0.6.0" in stamped
    with pytest.raises(ValueError, match="not the production profile"):
        proof_facts.settings_line({"profile_sha256": "other"}, "0.6.0", "2026-12-01", "abc")


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    env = WebEnv(tmp_path_factory.mktemp("proof-strip"))
    with env.running() as anon:
        paths = ("/", "/limits", "/security", "/for-accessibility-offices", offices.SUMMARY_PATH)
        return {path: anon.get(path).text for path in paths}


def test_no_public_page_or_readme_summary_states_a_retired_figure(pages):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    summary = readme[readme.index("### Latest registered result: fresh5") :]
    summary = summary[: summary.index("### ", 10)]
    texts = [*pages.values(), summary, (ROOT / "docs/vendor-information.md").read_text("utf-8")]
    for text in texts:
        for figure in proof_facts.RETIRED:
            assert figure not in text, figure
        assert " of 78" not in text and "42 of 78" not in text
    assert "settings the live service uses" not in pages["/limits"]
