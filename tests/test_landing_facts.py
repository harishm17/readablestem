"""The landing page's numbers and licence wording agree with PROOF.md and the README.

PROOF.md is written by `make proof` from the recorded held-out run (CI replays it and fails on
any difference), so it is the source of the registered numbers. The re-measurement numbers
are the README's. The landing page states them in prose; these tests fail when the page
and those files drift, and when a number that is not blind is shown without saying so.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv

ROOT = Path(__file__).resolve().parent.parent
PROOF = (ROOT / "PROOF.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")

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
        return {path: anon.get(path).text for path in ("/", "/terms")}


def test_landing_registered_numbers_match_proof_md(pages):
    text = squash(pages["/"])
    assert (
        f"run on {proof_field('Run recorded on')}, version {proof_field('Pipeline version')}"
        in text
    )
    assert f"({round(proof_cell('detection_recall') * 100)}%)" in text
    assert 0.45 <= proof_cell("end_to_end_exact_lenient (all gold equations)") <= 0.55
    assert "about half" in text
    equations_alone = float(re.search(r"^\| exact_lenient \| ([0-9.]+)", PROOF, re.M).group(1))  # type: ignore[union-attr]
    assert 0.62 <= equations_alone <= 0.71 and "about two in three" in text
    assert f"Headings scored {proof_cell('heading F1'):.2f}" in text
    assert f"passed a model judge {round(proof_cell('judge_pass_rate') * 100)}%" in text


def test_landing_remeasurement_matches_readme_and_says_not_blind(pages):
    row = re.search(
        r"^\| Equations right, end to end \(primary\) \| ([0-9.]+) [^|]*\| \*\*([0-9.]+)\*\*",
        README,
        re.M,
    )
    assert row, "the README's re-measurement table changed shape"
    registered, remeasured = row.groups()
    assert registered == f"{proof_cell('end_to_end_exact_lenient (all gold equations)'):.3f}"
    text = squash(pages["/"])
    assert f"{remeasured} of equations exactly right end to end, against {registered}" in text
    assert "not a blind result" in text
    assert "not a blind result" in README.lower()
    assert "#re-measurement-on-the-same-sections-2026-10-21-not-a-blind-result" in pages["/"]
    assert "### Re-measurement on the same sections, 2026-10-21 (not a blind result)" in README


def test_licence_is_worded_the_same_way_everywhere(pages):
    assert "The code is MIT (" in README and "the service is offered under AGPL-3.0 terms" in README
    footer = squash(pages["/"].split("<footer", 1)[1])
    assert LICENCE_SENTENCE in footer
    assert LICENCE_SENTENCE in squash(pages["/terms"])
    for committed in (
        "src/readablestem/web/static/demo/index.html",
        "site/examples/physics-2-1/converted.html",
        "site/examples/physics-2-2/converted.html",
    ):
        assert LICENCE_SENTENCE in squash((ROOT / committed).read_text(encoding="utf-8")), committed
    seo = (ROOT / "src/readablestem/web/seo.py").read_text(encoding="utf-8")
    assert "the code is MIT; the hosted service is offered" in seo
    assert '"under AGPL-3.0 terms"' in seo
