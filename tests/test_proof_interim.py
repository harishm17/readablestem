"""The interim held-out report (`proof/interim/report.py`): what it shows and what it refuses."""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INTERIM = ROOT / "proof" / "interim"
REPORT = INTERIM / "INTERIM-heldout-2026-10.md"


def _load():
    spec = importlib.util.spec_from_file_location("interim_report", INTERIM / "report.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report():
    return _load()


def _gold_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return Path(os.environ.get("HELDOUT_GOLD", root / "a11y-stem" / "heldout" / "gold"))


def test_a_missing_answer_key_says_how_to_get_it_instead_of_a_traceback(report, tmp_path):
    with pytest.raises(SystemExit) as caught:
        report.build(INTERIM / "heldout-2026-10", tmp_path / "nothing-here")
    assert "make heldout-gold" in str(caught.value) and "network" in str(caught.value)


def test_another_answer_key_is_refused(report, tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="not the registered held-out answer key"):
        report.build(INTERIM / "heldout-2026-10", tmp_path)


def test_the_makefile_reads_the_same_folder_the_fetch_writes():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    line = next(ln for ln in makefile.splitlines() if "proof/interim/report.py" in ln)
    assert "XDG_CACHE_HOME" in line, "proof-interim must honour the cache folder heldout-gold uses"


def test_recall_is_split_by_where_the_gold_heading_sits(report):
    items = [
        {
            "pairs": [
                {"gold_context": None},
                {"gold_context": "example"},
                {"gold_context": "note"},
            ],
            "missed": [{"context": None}, {"context": "solution"}, {"context": "solution"}],
        }
    ]
    assert report.heading_recall_split(items) == {"free": (1, 2), "boxed": (2, 4)}


def test_extra_headings_that_are_only_a_number_are_counted(report):
    items = [
        {
            "extra": [
                {"text": "142."},
                {"text": "7"},
                {"text": "(12)"},
                {"text": "Example 4"},
                {"text": "Section 1.5 Exercises"},
            ]
        }
    ]
    assert report.bare_number_extras(items) == (5, 3)


def test_the_committed_report_shows_the_review_screens_heading_flags():
    text = REPORT.read_text(encoding="utf-8")
    assert "Heading flags: recall" in text and "Heading flags: precision" in text
    row = next(ln for ln in text.splitlines() if "Wrong headings a required decision" in ln)
    assert "0 of 28" in row
    assert "Wrong headings not asked about" in text


def test_the_committed_report_says_what_was_not_run_and_how_it_was_run():
    text = REPORT.read_text(encoding="utf-8")
    assert "print" in text and "own stylesheet" in text
    assert "not carried out" in text and "formula recognition" in text
    assert "mineru-basic is not MinerU at its default settings" in text
    assert "unaudited" in text
    assert "What these numbers leave out" in text


def test_no_bold_better_verdict_rests_on_the_bootstrap_alone():
    text = REPORT.read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| heading F1 | docling")]
    rows += [ln for ln in text.splitlines() if ln.startswith("| heading F1 | m")]
    rows += [ln for ln in text.splitlines() if ln.startswith("| heading F1 | p")]
    assert len(rows) == 3
    for row in rows:
        if "mineru-basic" in row or "pymupdf4llm" in row:
            assert "higher by point estimate" in row and "is better" not in row


def test_the_development_numbers_the_report_cites_are_in_proof_md():
    text = (ROOT / "PROOF.md").read_text(encoding="utf-8")
    assert "| Heading flags: precision | 0.5556 |" in text
    assert "| Heading flags: recall | 0.7778 |" in text
    assert "precision 0.5556 and recall 0.7778" in REPORT.read_text(encoding="utf-8")


def test_the_readme_numbers_are_the_reports_numbers():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    report_text = REPORT.read_text(encoding="utf-8")
    for needle in ("0.9613", "0.9104", "0.9228", "0.8297", "0.1738", "0.0073", "0.0088", "0.0186"):
        assert needle in report_text, needle
    for shown in ("0.961", "0.910", "0.923", "0.830", "0.174", "p = 0.0073, 0.0088 and 0.0186"):
        assert shown in readme, shown
    assert re.search(r"recall 0 of 28", readme) and "precision 0 of 29" in readme
    assert "make heldout-gold" in readme


def test_the_report_is_what_the_committed_data_produce(report):
    gold = _gold_dir()
    if not (gold / "manifest.json").is_file():
        pytest.skip("the held-out answer key is not fetched (make heldout-gold)")
    built = report.build(INTERIM / "heldout-2026-10", gold)
    assert built == REPORT.read_text(encoding="utf-8")


def test_addendum_5_names_the_hash_of_the_committed_report():
    import hashlib

    digest = hashlib.sha256(REPORT.read_bytes()).hexdigest()
    addendum = (ROOT / "proof" / "PREREGISTRATION-ADDENDUM-5.md").read_text(encoding="utf-8")
    assert digest in addendum, "rebuild the report, then update Addendum 5 and its hash"
