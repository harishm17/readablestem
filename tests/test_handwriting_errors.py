"""scripts/handwriting_errors.py: error kinds, edit distance and speech-equal on fixture pairs."""

# ruff: noqa: RUF001  (look-alike characters are the point of these fixtures)
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "handwriting_errors", ROOT / "scripts" / "handwriting_errors.py"
)
assert _spec and _spec.loader
hwe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hwe)

# (gold, reading, kind that must be reported, edit distance)
PAIRS = [
    ("x+1", "X+1", "case", 1),
    ("p=1", "\\rho=1", "greek_latin", 1),
    ("\\omega t", "w t", "greek_latin", 1),
    ("k+1", "h+1", "other_letter", 1),
    ("1+y", "l+y", "digit_letter", 1),
    ("a=1", "a=7", "digit_digit", 1),
    ("0+b", "6+b", "digit_digit", 1),
    ("U(z)", "U(2)", "z_2", 1),
    ("a x b", "a \\times b", "x_times", 1),
    ("a-b", "a–b", "minus_dash", 1),
    ("R", "\\mathbb{R}", "style", 1),
    ("f^{-1}(y)", "f(y)", "script_lost", 5),
    ("f(y)", "f^{2}(y)", "script_added", 2),
    ("a_{i}", "a^{i}", "script_changed", 1),
    ("\\frac{a}{b}", "a b", "fraction", 3),
    ("\\sqrt{x}+1", "x+1", "root", 2),
    ("(a)+b", "[a)+b", "operator_bracket", 1),
]


@pytest.mark.parametrize(("gold", "pred", "kind", "distance"), PAIRS)
def test_kind_and_distance(gold: str, pred: str, kind: str, distance: int) -> None:
    record = hwe.classify_pair(gold, pred)
    assert kind in record["kinds"], record
    assert record["edit_distance"] == distance, record
    assert record["one_symbol"] is (distance == 1)


def test_speech_equal_when_notation_differs_but_speech_does_not() -> None:
    # \bar and \overline are spoken alike; x and X are not
    assert hwe.classify_pair("\\overline{A}", "\\bar{A}")["speech_equal"] is True
    assert hwe.classify_pair("x", "X")["speech_equal"] is False


def _write_run(tmp_path: Path) -> tuple[Path, Path]:
    gold_root = tmp_path / "gold"
    (gold_root / "dev").mkdir(parents=True)
    rows = [
        {"id": "u/eq-000", "latex": "x+1"},
        {"id": "u/eq-001", "latex": "a+b"},
        {"id": "u/eq-002", "latex": "p=2"},
    ]
    (gold_root / "dev" / "u.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    item = {
        "id": "u",
        "n_gold": 4,
        "missed_gold_ids": ["u/eq-002"],
        "pairs": [
            {"gold_id": "u/eq-000", "exact_lenient": False, "pred_latex": "X+1"},
            {"gold_id": "u/eq-001", "exact_lenient": True, "pred_latex": "a+b"},
        ],
    }
    arms = tmp_path / "arms"
    (arms / "armA" / "dev").mkdir(parents=True)
    (arms / "armA" / "dev" / "items.jsonl").write_text(json.dumps(item) + "\n")
    return arms, gold_root


def test_main_counts_wrong_and_missed_and_writes_json(tmp_path: Path, capsys) -> None:
    arms, gold_root = _write_run(tmp_path)
    out = tmp_path / "out.json"
    code = hwe.main(["--arms-root", str(arms), "--gold-root", str(gold_root), "--json", str(out)])
    assert code == 0
    summary = json.loads(out.read_text())["armA/dev"]
    assert summary["n_gold"] == 4
    assert summary["n_wrong"] == 2
    assert summary["kinds"] == {"omission": 1, "case": 1}
    assert summary["n_one_symbol"] == 1
    assert summary["examples"]["case"][0]["gold"] == "x+1"
    table = capsys.readouterr().out
    assert "armA/dev" in table
    assert "omission" in table
