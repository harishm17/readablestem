"""docs/eval.md names the pipeline version and run date of every committed report.

The table "Versions of the committed reports" is checked against the files, so a report that
is re-run (or a pipeline bump) cannot leave the docs naming another version than the one
measured. Older results are not rewritten: they stay labelled with their own version.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import readablestem
from readablestem.pipeline import run

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "eval"
DOC = (ROOT / "docs" / "eval.md").read_text(encoding="utf-8")


def _section() -> str:
    start = DOC.index("## Versions of the committed reports")
    return DOC[start : DOC.index("\n## ", start + 1)]


def _documented() -> dict[str, tuple[str, str]]:
    """File name -> (run date, pipeline version) from the section's table."""
    rows: dict[str, tuple[str, str]] = {}
    for line in _section().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[1]):
            for name in re.findall(r"`([\w.-]+\.json)`", cells[0]):
                rows[name] = (cells[1], cells[2])
    return rows


def _committed() -> dict[str, dict]:
    files = [*EVAL_DIR.glob("latest-*.json"), *EVAL_DIR.glob("baseline-*.json")]
    return {f.name: json.loads(f.read_text(encoding="utf-8")) for f in files}


def test_every_committed_report_is_in_the_docs_with_its_version_and_date() -> None:
    documented, committed = _documented(), _committed()
    assert committed, "no committed reports found"
    assert set(documented) == set(committed)
    for name, report in committed.items():
        assert documented[name] == (report["date"], report["pipeline_version"]), name


def test_every_report_records_a_pipeline_version() -> None:
    for name, report in _committed().items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", report.get("pipeline_version", "")), name


def test_the_two_version_numbers_are_told_apart() -> None:
    section = _section()
    assert "PIPELINE_VERSION" in section and "__version__" in section
    assert readablestem.__version__ in section
    assert run.PIPELINE_VERSION  # the number reports are measured under, not the package's
