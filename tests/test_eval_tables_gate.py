"""The tables gate: absolute floors (by mode), the separate real-document part, and the
regression check against a baseline of the same mode. Plus the CLI wiring of the task."""

from __future__ import annotations

import copy
import json

import pytest

from readablestem.cli import eval_cli
from readablestem.cli.main import main
from readablestem.eval import gate
from readablestem.eval.gate import DEFAULT_FLOORS, DEFAULT_TOLERANCES, GateRefused
from test_eval_support import settings


def tables_report(mode: str = "local", real: dict | None = None, **metrics) -> dict:
    base = {
        "task": "tables",
        "mode": mode,
        "pipeline_version": "0.4.0",
        "prompt_versions": {},
        "models": {"main": "gpt-6.1-sol", "cheap": "gpt-6-luna"},
        "client": "none" if mode == "local" else "openai",
        "dataset": {
            "manifest_sha256": "m" * 64,
            "authored_sha256": "a" * 64,
            "selection_sha256": "s" * 64,
        },
        "n": 45,
        "n_planned": 45,
        "complete": True,
        "stopped_reason": None,
        "metrics": {
            "detection_recall": 0.91,
            "detection_precision": 0.95,
            "teds": 0.87 if mode == "local" else 0.90,
            "column_header_exact": 0.78,
            "text_fidelity": {"wrong_cells_not_flagged": 0},
            "real_documents": real,
        },
        "cost": {"per_table_usd": 0.0 if mode == "local" else 0.0007},
    }
    base["metrics"].update(metrics)
    return base


def floors(result) -> dict[str, str]:
    return {r.name: r.status for r in result.floors}


def run_gate(current, baseline=None):
    return gate.gate(current, baseline, list(DEFAULT_TOLERANCES), list(DEFAULT_FLOORS))


def test_a_baseline_adds_the_regression_check_and_must_have_the_same_mode():
    base = tables_report()
    current = tables_report(teds=0.845)  # above the floor, 0.025 below the baseline
    result = run_gate(current, base)
    assert {r.name: r.status for r in result.rows}["table_teds"] == "regressed"
    assert not result.passed
    with pytest.raises(GateRefused) as refused:
        run_gate(tables_report("model"), base)
    assert refused.value.code == "mode_mismatch"
    other = copy.deepcopy(base)
    other["dataset"]["authored_sha256"] = "b" * 64
    with pytest.raises(GateRefused):
        run_gate(current, other)


def test_gate_cli_exit_codes_and_table(tmp_path, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(tables_report()))
    bad.write_text(json.dumps(tables_report(teds=0.5)))
    assert main(["eval", "gate", "--current", str(good)]) == 0
    out = capsys.readouterr().out
    assert "| table_teds_local | 0.8700 | >= 0.84 | gold | ok |" in out
    assert "| real_table_precision | n/a | >= 0.95 | real documents | SKIPPED |" in out
    assert main(["eval", "gate", "--current", str(bad)]) == 1
    assert "BELOW_FLOOR" in capsys.readouterr().out
    equations = tmp_path / "eq.json"
    equations.write_text(json.dumps({"task": "equations", "metrics": {}}))
    assert main(["eval", "gate", "--current", str(equations)]) == 2


# ------------------------------------------------------------------------- CLI wiring


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings())
