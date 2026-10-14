"""The tables gate: absolute floors (by mode), the separate real-document part, and the
regression check against a baseline of the same mode. Plus the CLI wiring of the task."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from readablestem.cli import eval_cli
from readablestem.cli import main as cli_main
from readablestem.cli.main import build_parser, main
from readablestem.eval import gate
from readablestem.eval import tables as T
from readablestem.eval.gate import DEFAULT_FLOORS, DEFAULT_TOLERANCES, GateRefused
from readablestem.pipeline import layout
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


def test_a_good_local_report_passes_its_floors_without_a_baseline():
    result = run_gate(tables_report())
    assert result.passed and result.rows == []
    assert floors(result) == {
        "table_detection_recall": "ok",
        "table_detection_precision": "ok",
        "table_teds_local": "ok",
        "table_column_header_exact": "ok",
        "table_wrong_cells_not_flagged": "ok",
        "real_table_precision": "skipped",
        "real_table_full_extent_recall": "skipped",
    }
    assert any("real-document gate skipped" in n for n in result.notes)


def test_teds_floors_depend_on_the_mode():
    assert run_gate(tables_report("local", teds=0.85)).passed  # local floor 0.84
    model = run_gate(tables_report("model", teds=0.85))  # model floor 0.88
    assert not model.passed and floors(model)["table_teds_model"] == "below_floor"
    assert "table_teds_local" not in floors(model)


@pytest.mark.parametrize(
    ("change", "floor"),
    [
        ({"detection_recall": 0.85}, "table_detection_recall"),
        ({"detection_precision": 0.92}, "table_detection_precision"),
        ({"column_header_exact": 0.74}, "table_column_header_exact"),
        ({"text_fidelity": {"wrong_cells_not_flagged": 1}}, "table_wrong_cells_not_flagged"),
    ],
)
def test_each_floor_fails_on_its_own(change, floor):
    result = run_gate(tables_report("model", **change))
    assert not result.passed and floors(result)[floor] == "below_floor"


def test_a_metric_missing_from_the_report_fails():
    report = tables_report()
    del report["metrics"]["column_header_exact"]
    assert floors(run_gate(report))["table_column_header_exact"] == "missing"


def test_the_real_document_part_applies_only_when_real_documents_were_measured():
    real = {"precision": 0.68, "full_extent_recall": 0.72}
    result = run_gate(tables_report(real=real))
    assert not result.passed
    assert floors(result)["real_table_precision"] == "below_floor"
    assert floors(result)["real_table_full_extent_recall"] == "below_floor"
    good = run_gate(tables_report(real={"precision": 0.97, "full_extent_recall": 0.86}))
    assert good.passed and not good.notes


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


def test_tasks_without_floors_still_need_a_baseline():
    with pytest.raises(GateRefused) as refused:
        run_gate({"task": "equations", "metrics": {}})
    assert refused.value.code == "no_baseline"


def test_floors_move_with_the_tolerances_file(tmp_path):
    path = tmp_path / "tolerances.json"
    path.write_text(json.dumps({"floors": [{"name": "table_teds_local", "minimum": 0.9}]}))
    loaded = {f.name: f for f in gate.load_floors(path)}
    assert loaded["table_teds_local"].minimum == 0.9
    assert loaded["table_teds_local"].mode == "local"
    path.write_text(json.dumps({"floors": [{"name": "new_floor", "task": "tables"}]}))
    with pytest.raises(ValueError):
        gate.load_floors(path)


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


def test_code_versions_skip_a_step_prompt_that_does_not_exist_yet():
    versions = gate.code_versions()
    assert {"equation", "figure", "judge"} <= set(versions)


# ------------------------------------------------------------------------- CLI wiring


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings())


@pytest.fixture
def table_cli(monkeypatch, tmp_path):
    """The task's media and layout without a browser or a detector."""
    from test_eval_support import base_record, write_gold
    from test_eval_tables_run import (
        PAGE_TEXT,
        SPEEDS_HTML,
        FakeTableMedia,
        fake_layout,
    )

    gold = write_gold(tmp_path, [base_record("speeds", "table", 0, html=SPEEDS_HTML)])
    assert "speeds" in PAGE_TEXT

    class Factory(FakeTableMedia):
        def __init__(self, gold_dir, cache_dir, fetcher, *, paper="Letter") -> None:
            super().__init__()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(T, "CachedTableMedia", Factory)
    monkeypatch.setattr(layout, "analyze", fake_layout({}))
    monkeypatch.setattr(layout, "release", lambda pdf=None: None)
    return gold


def table_args(gold: Path, tmp_path: Path, *extra: str) -> list[str]:
    return [
        "eval",
        "pipeline",
        "--task",
        "tables",
        "--gold",
        str(gold),
        "--no-authored",
        "--out",
        str(tmp_path / "tables.json"),
        "--date",
        "2026-10-09",
        *extra,
    ]


def test_tables_task_runs_from_the_cli_in_local_mode(table_cli, tmp_path, capsys):
    assert main(table_args(table_cli, tmp_path, "--styles", "grid,norules")) == 0
    report = json.loads((tmp_path / "tables.json").read_text())
    assert (report["task"], report["mode"], report["client"], report["n"]) == (
        "tables",
        "local",
        "none",
        2,
    )
    assert "## Eval: tables, local mode" in capsys.readouterr().out


def test_tables_model_mode_needs_a_cap_and_styles_are_checked(table_cli, tmp_path, capsys):
    assert main(table_args(table_cli, tmp_path, "--mode", "model")) == 2
    assert "--max-usd is required" in capsys.readouterr().err
    assert main(table_args(table_cli, tmp_path, "--styles", "grid,fancy")) == 2
    assert "--styles takes" in capsys.readouterr().err
    assert main(table_args(table_cli, tmp_path, "--real-dir", str(tmp_path))) == 2
    assert "no labels.json" in capsys.readouterr().err
    assert main(table_args(table_cli, tmp_path, "--mode", "model", "--fake")) == 0


def test_promote_takes_a_local_tables_report_and_names_it_by_mode(tmp_path):
    path = tmp_path / "local.json"
    path.write_text(json.dumps(tables_report()))
    target = tmp_path / "data"
    assert main(["eval", "promote", str(path), "--as", "baseline", "--out-dir", str(target)]) == 0
    assert (target / "baseline-tables-local.json").is_file()
    fake = tables_report("model") | {"client": "fake"}
    path.write_text(json.dumps(fake))
    assert main(["eval", "promote", str(path), "--as", "baseline", "--out-dir", str(target)]) == 2


def test_gold_fetch_set_tables_fetches_the_13_pages_table_records_only(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(book, n, out, fetcher, **kw):
        calls.append((book, n, out, kw))
        return type(
            "B",
            (),
            {
                "title": book,
                "slug": book,
                "uuid": "u",
                "version": "v",
                "state": "live",
                "archive_version": "a",
                "license_name": "CC BY",
                "license_url": "x",
            },
        )(), []

    monkeypatch.setattr("readablestem.gold.openstax.fetch_gold", fake_fetch)
    argv = ["gold", "fetch", "--set", "tables", "--out", str(tmp_path), "--date", "2026-10-08"]
    assert main(argv) == 0
    assert [c[0] for c in calls] == ["physics", "statistics"]
    assert sum(c[1] for c in calls) == 13
    assert all(c[3]["kinds"] == ("table",) and c[3]["retrieved_at"] == "2026-10-08" for c in calls)
    assert calls[1][3]["page_slugs"] == list(T.TABLE_GOLD_PAGES["statistics"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["gold", "fetch", "--set", "tables", "--book", "physics"])
    assert cli_main.main(["gold", "fetch", "--set", "tables", "--page", "x"]) == 2
