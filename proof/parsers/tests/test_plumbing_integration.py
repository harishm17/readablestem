"""The real chain with the fastest real arm. Skipped where the arm is not set up
(`parsers-bench setup pymupdf4llm` creates it)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parsers_bench import cli
from parsers_bench.arms import ARMS

pytestmark = pytest.mark.skipif(
    not (ARMS["pymupdf4llm"].env_dir / "bin" / "python").is_file(),
    reason="the pymupdf4llm environment is not set up",
)


def test_pymupdf4llm_plumbing_end_to_end(tmp_path: Path) -> None:
    code = cli.main(["plumbing", "pymupdf4llm", "--repeat", "2", "--out", str(tmp_path)])
    assert code == 0
    summary = json.loads((tmp_path / "plumbing-summary.json").read_text())
    entry = summary["arms"]["pymupdf4llm"]
    assert entry["status"] == "ok"
    assert entry["runs"] == 2
    assert entry["deterministic_across_runs"] is True
    assert entry["versions"]["pymupdf4llm"] == "1.28.2"
    assert entry["equations"]["does_not_transcribe_by_design"] is True
    assert entry["equations"]["end_to_end_exact_lenient"]["primary"] == 0.0
    assert entry["headings"]["gold_headings"] == 6
    assert entry["cost_and_time"]["api_cost_usd"] == 0.0
    assert entry["cost_and_time"]["pages"] == 2
    assert summary["input"]["license"].startswith("CC BY 4.0")
    # Counts and codes only: no text of the page is in the summary.
    text = json.dumps(summary)
    assert "Teacher Support" not in text
    assert "meter stick" not in text
