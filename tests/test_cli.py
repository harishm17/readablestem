"""CLI smoke tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.cli.main import build_parser, main

GOLD_LINE = {
    "id": "openstax/physics/x/eq-000",
    "kind": "equation",
    "display": "block",
    "mathml": "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>",
    "retrieved_at": "2026-10-06",
}


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for command in ("gold", "eval", "parse"):
        assert command in out


def test_gold_fetch_requires_book():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["gold", "fetch", "--pages", "2"])
    args = build_parser().parse_args(["gold", "fetch", "--book", "physics", "--pages", "2"])
    assert (args.book, args.pages) == ("physics", 2)


def test_eval_equations_with_latex_predictions(tmp_path: Path, capsys):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(GOLD_LINE) + "\n", encoding="utf-8")
    pred = tmp_path / "pred.jsonl"
    pred.write_text(json.dumps({"id": GOLD_LINE["id"], "latex": r"v = \frac{d}{t}"}) + "\n")
    report = tmp_path / "report.json"
    code = main(
        ["eval", "equations", "--gold", str(gold), "--pred", str(pred), "--report", str(report)]
    )
    assert code == 0
    summary = json.loads(report.read_text())["summary"]
    assert summary == {
        "n": 1,
        "exact_strict": 1.0,
        "exact_lenient": 1.0,
        "mean_teds": 1.0,
        "errors": 0,
    }


def test_eval_equations_missing_prediction_counts_as_failure(tmp_path: Path, capsys):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(GOLD_LINE) + "\n", encoding="utf-8")
    pred = tmp_path / "pred.jsonl"
    pred.write_text(json.dumps({"id": "other", "latex": "x"}) + "\n")
    assert main(["eval", "equations", "--gold", str(gold), "--pred", str(pred)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["exact_strict"] == 0.0 and summary["errors"] == 1


def test_parse_command_prints_elements(tmp_path: Path, capsys):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "a = b + c", fontsize=12)
    pdf = tmp_path / "one.pdf"
    doc.save(pdf)
    doc.close()
    assert main(["parse", str(pdf), "--json"]) == 0
    elements = json.loads(capsys.readouterr().out)
    assert elements[0]["kind"] == "equation_candidate"
