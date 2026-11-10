"""`readablestem demo rerender`: today's output for a stored demo or example result, with no
model, no network and no cost; the stored result is left as it was written."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import readablestem
from readablestem.cli.eval_cli import rerender_demo

DEMO = Path(readablestem.__file__).parent / "web" / "static" / "demo"
SITE = Path(__file__).resolve().parent.parent / "site" / "examples"


def copy_of(source: Path, tmp_path: Path) -> tuple[Path, Path, Path]:
    report = tmp_path / "report.json"
    pdf = tmp_path / "source.pdf"
    shutil.copy(source / "report.json", report)
    shutil.copy(source / "source.pdf", pdf)
    return report, pdf, tmp_path / "out.html"


def test_stale_heading_fixes_are_decided_again_and_the_counts_follow(tmp_path):
    """The heading step needs no model, so a stored result follows its rules: a doubled box
    title stored as two headings is written as one heading and one bold line."""
    report, pdf, out = copy_of(DEMO, tmp_path)
    data = json.loads(report.read_text(encoding="utf-8"))
    fixes = data["result"]["fixes"]
    bold = [f for f in fixes if f.get("reason") == "repeats_heading_above"]
    assert bold, "the committed demo already has its doubled titles as bold text"
    stale = bold[0]
    stale["reason"], stale["heading"] = None, {"level": 3, "text": "Teacher Support"}
    data["result"]["stats"]["by_review"] = {"none": 1}
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rerender_demo(report, pdf, out)
    again = json.loads(report.read_text(encoding="utf-8"))
    fixed = next(f for f in again["result"]["fixes"] if f["element_id"] == stale["element_id"])
    assert fixed["reason"] == "repeats_heading_above" and fixed["heading"] is None
    assert sum(again["result"]["stats"]["by_review"].values()) == len(fixes)
    rerender_demo(report, pdf, out)  # decided once: the second run changes nothing
    assert json.loads(report.read_text(encoding="utf-8")) == again
