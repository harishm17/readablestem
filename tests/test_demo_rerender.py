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


def test_the_demo_is_written_with_the_note_one_h1_and_the_dark_scheme(tmp_path):
    report, pdf, out = copy_of(DEMO, tmp_path)
    rerender_demo(report, pdf, out)
    html = out.read_text(encoding="utf-8")
    assert html.count("<h1") == 1
    assert 'aria-label="About this demo"' in html and "Precomputed demo." in html
    assert "@media screen and (prefers-color-scheme: dark)" in html
    assert 'id="about-conversion-title" class="about-title"' in html
    assert "https://github.com/harishm17/readablestem" in html  # the AGPL source link


def test_it_is_repeatable_and_leaves_the_stored_result_as_written(tmp_path):
    report, pdf, out = copy_of(DEMO, tmp_path)
    before = report.read_bytes()
    rerender_demo(report, pdf, out)
    first = out.read_bytes()
    rerender_demo(report, pdf, out)
    assert out.read_bytes() == first
    assert report.read_bytes() == before  # same answers, same checks: not rewritten


def test_an_example_keeps_its_compact_formatting_and_old_pipeline_version(tmp_path):
    report, pdf, out = copy_of(SITE / "physics-2-2", tmp_path)
    before = report.read_text(encoding="utf-8")
    assert before.startswith('{"demo":')
    rerender_demo(report, pdf, out)
    stored = json.loads(report.read_text(encoding="utf-8"))
    assert stored["result"]["pipeline_version"] == json.loads(before)["result"]["pipeline_version"]
    assert 'aria-label="About this demo"' in out.read_text(encoding="utf-8")


def test_the_checks_of_the_new_file_replace_the_stored_ones_and_nothing_else(tmp_path):
    report, pdf, out = copy_of(DEMO, tmp_path)
    data = json.loads(report.read_text(encoding="utf-8"))
    data["result"]["html_checks"] = data["result"]["html_checks"][:-1]  # a stale list
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rerender_demo(report, pdf, out)
    again = json.loads(report.read_text(encoding="utf-8"))
    assert len(again["result"]["html_checks"]) == len(data["result"]["html_checks"]) + 1
    assert again["result"]["elements"] == data["result"]["elements"]
    assert report.read_text(encoding="utf-8").startswith("{\n")  # its own formatting kept


def test_a_fixture_is_written_without_the_demo_note(tmp_path):
    report = tmp_path / "report.json"
    shutil.copy(SITE / "fixture-lecture-notes" / "report.json", report)
    pdf = Path(__file__).parent / "fixtures" / "layout" / "lecture_notes.pdf"
    out = tmp_path / "out.html"
    rerender_demo(report, pdf, out, note=False)
    assert "About this demo" not in out.read_text(encoding="utf-8")


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
