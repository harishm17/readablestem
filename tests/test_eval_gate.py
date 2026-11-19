"""Eval gate: pass, fail, refusals, tolerance files, CLI exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readablestem.cli.main import main
from readablestem.eval import gate
from readablestem.eval.gate import DEFAULT_TOLERANCES, GateRefused, compare

REPO = Path(__file__).resolve().parents[1]


def report(task="equations", **metrics) -> dict:
    base = {
        "task": task,
        "pipeline_version": "0.2.0",
        "prompt_versions": {"equation": "equation-aaaa1111"},
        "models": {"main": "gpt-6.1-sol", "cheap": "gpt-6-luna"},
        "client": "openai",
        "dataset": {"manifest_sha256": "m" * 64, "selection_sha256": "s" * 64, "limit": None},
        "n": 30,
        "n_planned": 30,
        "complete": True,
        "stopped_reason": None,
        "metrics": {
            "exact_lenient": 0.80,
            "mean_teds": 0.90,
            "recall": 0.9,
            "judge_pass_rate": 0.7,
        },
        "cost": {"per_item_usd": 0.004},
    }
    base["metrics"].update(metrics)
    return base


def statuses(result) -> dict[str, str]:
    return {r.name: r.status for r in result.rows}


def test_drop_beyond_tolerance_fails():
    result = compare(report(exact_lenient=0.77), report(), list(DEFAULT_TOLERANCES))
    assert not result.passed
    assert statuses(result)["exact_lenient"] == "regressed"
    row = next(r for r in result.rows if r.name == "exact_lenient")
    assert row.delta == pytest.approx(-0.03)


def test_exactly_at_the_tolerance_passes():
    result = compare(report(exact_lenient=0.78), report(), list(DEFAULT_TOLERANCES))
    assert statuses(result)["exact_lenient"] == "ok"


def test_cost_increase_is_relative():
    current = report()
    current["cost"]["per_item_usd"] = 0.0051  # +27.5%
    result = compare(current, report(), list(DEFAULT_TOLERANCES))
    assert statuses(result)["cost_per_equation"] == "regressed"
    cheaper = report()
    cheaper["cost"]["per_item_usd"] = 0.001
    assert compare(cheaper, report(), list(DEFAULT_TOLERANCES)).passed


def test_a_metric_missing_from_the_current_report_fails():
    current = report()
    current["metrics"]["mean_teds"] = None
    assert statuses(compare(current, report(), list(DEFAULT_TOLERANCES)))["mean_teds"] == "missing"
    baseline = report()
    del baseline["metrics"]["mean_teds"]
    result = compare(report(), baseline, list(DEFAULT_TOLERANCES))
    assert result.passed and statuses(result)["mean_teds"] == "no_baseline"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda r: r.update(task="headings"), "task_mismatch"),
        (lambda r: r["dataset"].update(manifest_sha256="x" * 64), "manifest_mismatch"),
        (lambda r: r["dataset"].update(selection_sha256="x" * 64), "selection_mismatch"),
        (lambda r: r.update(complete=False, n=12, stopped_reason="budget_job_usd"), "partial_run"),
    ],
)
def test_incomparable_reports_are_refused(change, code):
    current = report()
    change(current)
    with pytest.raises(GateRefused) as exc:
        compare(current, report(), list(DEFAULT_TOLERANCES))
    assert exc.value.code == code


# ----------------------------------------------------------------------------- CLI


def write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_cli_exit_codes(tmp_path, capsys):
    baseline = write(tmp_path, "base.json", report())
    ok = write(tmp_path, "ok.json", report(exact_lenient=0.79))
    bad = write(tmp_path, "bad.json", report(exact_lenient=0.70))
    other = report()
    other["dataset"]["manifest_sha256"] = "x" * 64
    other_gold = write(tmp_path, "other.json", other)

    assert main(["eval", "gate", "--current", str(ok), "--baseline", str(baseline)]) == 0
    assert "PASS" in capsys.readouterr().out
    assert main(["eval", "gate", "--current", str(bad), "--baseline", str(baseline)]) == 1
    assert "REGRESSED" in capsys.readouterr().out
    assert main(["eval", "gate", "--current", str(other_gold), "--baseline", str(baseline)]) == 2
    err = capsys.readouterr().err
    assert "manifest_mismatch" in err and "different gold manifests" in err


def judged(judge: str, rate: float) -> dict:
    data = report("figures", judge_pass_rate=rate)
    data["prompt_versions"] = {"figure": "figure-ac51f42d", "judge": judge}
    data["models"] = {**data["models"], "judge": "gpt-6.1-sol"}
    return data


def test_a_changed_judge_is_refused_not_compared():
    """figures-run2 vs figures-run1 passed with the judge prompt changed: the pass rate
    went from 0.46 to 0.92 because the instrument changed."""
    with pytest.raises(GateRefused) as refused:
        compare(
            judged("judge_figure-137e2607", 0.92),
            judged("judge_figure-d52d54f1", 0.46),
            list(DEFAULT_TOLERANCES),
        )
    assert refused.value.code == "judge_changed"
    other_model = judged("judge_figure-137e2607", 0.92)
    other_model["models"]["judge"] = "gpt-6-luna"
    with pytest.raises(GateRefused, match="different judges"):
        compare(other_model, judged("judge_figure-137e2607", 0.9), list(DEFAULT_TOLERANCES))
    assert compare(
        judged("judge_figure-137e2607", 0.92),
        judged("judge_figure-137e2607", 0.90),
        list(DEFAULT_TOLERANCES),
    ).passed


def test_require_current_refuses_a_report_made_with_other_prompts(tmp_path, capsys):
    """A committed latest-<task>.json that predates a prompt change would let the change
    through without anyone re-running the eval; so would one made by an older pipeline,
    whose rules and constants are not in any prompt."""
    from readablestem.pipeline.run import PIPELINE_VERSION

    versions = gate.code_versions()
    fresh = report()
    fresh["prompt_versions"] = {"equation": versions["equation"]}
    fresh["pipeline_version"] = PIPELINE_VERSION
    stale = report()  # equation-aaaa1111, not the code's prompt
    stale["pipeline_version"] = PIPELINE_VERSION
    older = {**fresh, "pipeline_version": "0.5.0"}
    baseline = write(tmp_path, "base.json", fresh)
    args = ["eval", "gate", "--require-current", "--baseline", str(baseline), "--current"]
    assert main([*args, str(write(tmp_path, "fresh.json", fresh))]) == 0
    capsys.readouterr()
    assert main([*args, str(write(tmp_path, "stale.json", stale))]) == 2
    err = capsys.readouterr().err
    assert "stale_report" in err and "equation prompt equation-aaaa1111" in err
    assert main([*args, str(write(tmp_path, "older.json", older))]) == 2
    assert "pipeline 0.5.0" in capsys.readouterr().err
    # Without the flag (comparing two historical runs) it is only a note.
    plain = ["eval", "gate", "--baseline", str(baseline), "--current"]
    assert main([*plain, str(tmp_path / "stale.json")]) == 0


def test_require_current_refuses_a_report_made_with_other_switches(tmp_path, capsys):
    """A switch that decides what a model is asked or what a step accepts moves what a report
    measures without moving a prompt, a model or the pipeline version: the report records the
    switches' values and the gate names the one that differs. A no-model report without them is
    refused; a report of a model run made before they were recorded is left as it was."""
    from readablestem.pipeline.run import PIPELINE_VERSION

    fresh = report()
    fresh["prompt_versions"] = {"equation": gate.code_versions()["equation"]}
    fresh["pipeline_version"] = PIPELINE_VERSION
    fresh["flags"] = gate.code_flags(fresh)
    moved = {
        **fresh,
        "flags": {**fresh["flags"], "pipeline.equation_step.SKIP_ESCALATION_FROM": 0.7},
    }
    unrecorded = {k: v for k, v in fresh.items() if k != "flags"}
    baseline = write(tmp_path, "base.json", fresh)
    args = ["eval", "gate", "--require-current", "--baseline", str(baseline), "--current"]
    assert main([*args, str(write(tmp_path, "fresh.json", fresh))]) == 0
    capsys.readouterr()
    assert main([*args, str(write(tmp_path, "moved.json", moved))]) == 2
    err = capsys.readouterr().err
    assert "stale_report" in err and "pipeline.equation_step.SKIP_ESCALATION_FROM 0.7" in err
    assert "(code: 0.8)" in err
    # A model run made before flags were recorded is refused only for its other differences.
    assert main([*args, str(write(tmp_path, "old.json", unrecorded))]) == 0
    # A run that called no model has nothing to wait for: it must carry them.
    assert (
        main([*args, str(write(tmp_path, "nomodel.json", {**unrecorded, "client": "fake"}))]) == 2
    )
    assert "flags not recorded" in capsys.readouterr().err
    # Without the flag a difference is listed, with what production runs for each setting.
    settings = {**fresh["flags"], "REGION_TREE": "on"}
    plain = ["eval", "gate", "--baseline", str(baseline), "--current"]
    assert main([*plain, str(write(tmp_path, "dev.json", {**fresh, "flags": settings}))]) == 0
    out = capsys.readouterr().out
    assert "flag REGION_TREE changed" in out and "production 'off'" in out


# A numeric constant of a module that holds a bar, or what a request holds, is guarded
# (`GUARDED_CONSTANTS`) or left out with its reason (`UNGUARDED_CONSTANTS`): a new bar is a
# decision when it is added, not a report that silently stops meaning what it did. (The three
# modules named here hold the page's geometry and the parsers' safety limits; the gate's
# comment on `GUARDED_CONSTANTS` leaves them out.)
_NOT_SCANNED = {
    "readablestem.pipeline.tables",
    "readablestem.pipeline.validate",
    "readablestem.pipeline.layout.access",
}


def _unlisted_numeric_constants() -> list[str]:
    import importlib

    steps = {
        f"readablestem.pipeline.{path.stem}"
        for path in (REPO / "src/readablestem/pipeline").glob("*_step.py")
    }
    guarded = {m for m in gate.GUARDED_CONSTANTS if m.startswith("readablestem.pipeline.")}
    found = []
    for name in sorted((steps | guarded) - _NOT_SCANNED):
        module = importlib.import_module(name)
        listed = set(gate.GUARDED_CONSTANTS.get(name, ())) | set(
            gate.UNGUARDED_CONSTANTS.get(name, {})
        )
        found += [
            f"{name}.{const}"
            for const, value in vars(module).items()
            if const.isupper()
            and not const.startswith("_")
            and isinstance(value, int | float)
            and not isinstance(value, bool)
            and const not in listed
        ]
    return found


def test_every_numeric_constant_of_a_step_is_guarded_or_left_out_with_a_reason(monkeypatch):
    from readablestem.pipeline import heading_step

    assert _unlisted_numeric_constants() == []
    monkeypatch.setattr(heading_step, "ACCEPT_AT_NEW_BAR", 0.8, raising=False)
    assert _unlisted_numeric_constants() == ["readablestem.pipeline.heading_step.ACCEPT_AT_NEW_BAR"]
    assert all(reason for names in gate.UNGUARDED_CONSTANTS.values() for reason in names.values())
