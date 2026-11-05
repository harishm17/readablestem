"""The public pages' numbers and licence wording agree with PROOF.md, the README and the eval
reports.

PROOF.md is written by `make proof` from the recorded held-out run (CI replays it and fails on
any difference), so it is the source of the registered numbers. The re-measurement numbers
are the README's; the real-world, development, chemistry and handwriting numbers are their own
reports' (docs/eval-*.md). /limits states them all in prose and the landing page a short
summary; these tests fail when a page and those files drift, when a number that is not blind
is shown without saying so, and when a development number is not labelled as one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv

ROOT = Path(__file__).resolve().parent.parent
PROOF = (ROOT / "PROOF.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
REALWORLD = (ROOT / "docs/eval-realworld.md").read_text(encoding="utf-8")
DEV_BASELINE = (ROOT / "docs/eval-dev-baseline-2026-11-03.md").read_text(encoding="utf-8")
CHEMISTRY = (ROOT / "docs/eval-chemistry-heldout.md").read_text(encoding="utf-8")
FRESH5 = (ROOT / "docs/eval-equation-fresh5.md").read_text(encoding="utf-8")
HANDWRITING = (ROOT / "docs/eval-handwriting.md").read_text(encoding="utf-8")

LICENCE_SENTENCE = (
    "The code is MIT. The hosted service links PyMuPDF, which is AGPL-3.0, so the service is "
    "offered under AGPL-3.0 terms"
)


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text))


def proof_cell(row_label: str) -> float:
    """First number after the label in a PROOF.md table row."""
    match = re.search(rf"^\|[^|]*{re.escape(row_label)}[^|]*\|\s*([0-9.]+)", PROOF, re.M)
    assert match, row_label
    return float(match.group(1))


def proof_field(name: str) -> str:
    match = re.search(rf"^\| {re.escape(name)} \| ([^|]+) \|", PROOF, re.M)
    assert match, name
    return match.group(1).strip()


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, str]:
    env = WebEnv(tmp_path_factory.mktemp("landing"))
    with env.running() as anon:
        return {path: anon.get(path).text for path in ("/", "/terms", "/limits")}


def test_limits_latest_registered_numbers_match_the_fresh5_report(pages):
    text = squash(pages["/limits"])
    assert "run once on 2026-11-04 with version 0.5.9" in text
    for number in ("0.840", "0.836", "+0.004", "−0.023 to +0.036", "0.793", "0.770"):
        assert number in text, number
    for report_cell in (
        "0.840** [0.791, 0.876]",
        "0.836 [0.778, 0.878]",
        "+0.004 | [-0.023, +0.036]",
        "5.02 [3.55, 6.39]",
        "9.56 [5.86, 14.55]",
        "0.461 [0.309, 0.613]",
        "0.333 (41/123)",
    ):
        assert report_cell in FRESH5, report_cell
    assert "no difference this set can detect" in text
    assert "so it was not met" in text and "do not claim that ReadableSTEM does better" in text
    assert "26 / 1,715 / 131" in FRESH5 and "26 documents" in text and "1,715" in text
    assert "0.33 right as chemistry" in text and "not measured" in text
    # PROOF.md is the replayed report of the first registered result and says nothing of fresh5.
    assert "fresh5" not in PROOF


def test_limits_first_registered_result_is_history_and_matches_proof_md(pages):
    text = squash(pages["/limits"])
    assert proof_field("Pipeline version") == "0.4.0"
    assert "version 0.4.0, October 2026" in text and "Earlier results, kept as history" in text
    assert proof_field("Run recorded on").startswith("2026-10")
    assert f"it found {round(proof_cell('detection_recall') * 100)}% of them" in text
    assert 0.45 <= proof_cell("end_to_end_exact_lenient (all gold equations)") <= 0.55
    # Earlier results are ReadableSTEM's own scores, no chatbot comparison (Harish, 2026-11-04).
    assert "about half came out exactly right end to end (0.50)" in text
    assert "against 0.62 for the chatbot" not in text
    equations_alone = float(re.search(r"^\| exact_lenient \| ([0-9.]+)", PROOF, re.M).group(1))  # type: ignore[union-attr]
    assert 0.62 <= equations_alone <= 0.71 and "about two in three were right (0.68)" in text
    assert f"Headings: F1 {proof_cell('heading F1'):.2f}" in text
    assert f"{round(proof_cell('judge_pass_rate') * 100)}% passed a model judge" in text


def test_limits_remeasurement_matches_readme_and_says_not_blind(pages):
    row = re.search(
        r"^\| Equations right, end to end \(primary\) \| ([0-9.]+) [^|]*\| \*\*([0-9.]+)\*\*",
        README,
        re.M,
    )
    assert row, "the README's re-measurement table changed shape"
    registered, remeasured = row.groups()
    assert registered == f"{proof_cell('end_to_end_exact_lenient (all gold equations)'):.3f}"
    text = squash(pages["/limits"])
    assert f"{remeasured} of equations exactly right end to end, against {registered}" in text
    assert "not a blind result" in text
    assert "not a blind result" in README.lower()
    assert "#re-measurement-on-the-same-sections-2026-10-21-not-a-blind-result" in pages["/limits"]
    assert "### Re-measurement on the same sections, 2026-10-21 (not a blind result)" in README


def test_the_other_results_match_their_reports_and_development_is_labelled(pages):
    text = squash(pages["/limits"])
    # Real-world held-out part (docs/eval-realworld.md, "The short version").
    assert "15 held-out papers (71 pages, 1,826 gold equations" in REALWORLD
    assert "0.583" in REALWORLD and "on Opus 0.641" in REALWORLD and "on Haiku 0.641" in REALWORLD
    assert "15 arXiv papers, 71 pages, 1,826 equations" in text
    assert "0.58 of equations exactly right end to end." in text and "against 0.64" not in text
    # Development baseline: labelled as development, never as held out.
    assert "0.735" in DEV_BASELINE and "0.675" in DEV_BASELINE
    assert "0.764" in DEV_BASELINE and "Opus 0.784" in DEV_BASELINE
    assert "Development set, not held out (25 arXiv papers, 114 pages, 2,350 equations" in text
    assert "0.735 exactly right end to end." in text
    assert "against 0.675" not in text and "0.764 against 0.784" not in text
    # Chemistry and handwriting.
    assert "**211 (0.560)**" in CHEMISTRY and "377 gold equations, 5 sections" in CHEMISTRY
    assert "5 OpenStax chemistry sections, 377 equations): 0.56 right as chemistry" in text
    assert "24 pages of real human handwriting (205 expressions, held-out)" in HANDWRITING
    assert "**0.566**" in HANDWRITING
    assert "Handwriting, held out (24 pages, 205 expressions): 0.57" in text


def test_the_landing_page_summarises_without_overclaiming(pages):
    landing = squash(pages["/"])
    # The newest registered held-out result is the one framing: level on what a listener hears,
    # drawn as two compact charts (web/proof_facts.py). The four measures and how each was
    # measured are on /limits and the comparison guide, behind one link; the earlier test
    # (chatbot ahead on exactly right) is history there, in PROOF.md and in the README, not on
    # the landing page.
    assert 'class="proof-strip"' not in pages["/"] and 'class="proof-strip"' in pages["/limits"]
    assert 'class="proof-chart proof-chart-compact"' in pages["/"]
    assert "All results and other tools" in landing
    assert (
        "ReadableSTEM is level with the Opus chatbot on the main score (0.840 and 0.836)" in landing
    )
    assert "1,715 equations in 26 documents it had never seen" in landing
    assert "0.62 against 0.50" not in landing and "chatbot ahead" not in landing
    assert "beat" not in landing.lower().replace("beats", "")
    limits = squash(pages["/limits"])
    # The history stays on /limits as ReadableSTEM's own scores (Harish, 2026-11-04).
    assert "about half came out exactly right end to end (0.50)" in limits
    assert "against 0.62 for the chatbot" not in limits
    assert 'href="/guides/readablestem-mathpix-latexml">All results and other tools' in pages["/"]
    assert "about 5.6 things per page" in REALWORLD and "5.7 decisions per page" in HANDWRITING
    assert "**242**" in PROOF
    # Development numbers stay on /limits, labelled there.
    assert "0.735" not in landing and "development set" not in landing.lower()
    for claim in ("compliant", "conforms", "guarantee"):
        assert claim not in landing.lower(), claim


def test_limits_keeps_one_decisions_per_page_figure_per_kind_of_document(pages):
    limits = squash(pages["/limits"])
    assert (
        "textbook sections, about 1.8 decisions a page (15 OpenStax sections, 242 pages)" in limits
    )
    assert "about 5.6 a page (15 arXiv papers, 71 pages)" in limits


def test_licence_is_worded_the_same_way_everywhere(pages):
    assert "The code is MIT (" in README and "the service is offered under AGPL-3.0 terms" in README
    # The sentence is on the Terms page only; every footer carries a plain "Open source" link
    # to it, and the demo notes link the source without repeating it (ADR 0003, addendum).
    footer = squash(pages["/"].split("<footer", 1)[1])
    assert "AGPL" not in footer
    assert '<a href="/terms#open-source">Open source</a>' in pages["/"]
    assert LICENCE_SENTENCE in squash(pages["/terms"])
    assert 'id="open-source"' in pages["/terms"]
    for committed in (
        "src/readablestem/web/static/demo/index.html",
        "site/examples/physics-2-1/converted.html",
        "site/examples/physics-2-2/converted.html",
    ):
        note = squash((ROOT / committed).read_text(encoding="utf-8"))
        assert "The code is open source: source code on GitHub" in note, committed
        assert "AGPL" not in note, committed
    seo = (ROOT / "src/readablestem/web/seo.py").read_text(encoding="utf-8")
    assert "the code is MIT; the hosted service is offered" in seo
    assert '"under AGPL-3.0 terms"' in seo
