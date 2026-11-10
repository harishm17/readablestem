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
    through without anyone re-running the eval."""
    versions = gate.code_versions()
    fresh = report()
    fresh["prompt_versions"] = {"equation": versions["equation"]}
    stale = report()  # equation-aaaa1111, not the code's prompt
    baseline = write(tmp_path, "base.json", fresh)
    args = ["eval", "gate", "--require-current", "--baseline", str(baseline), "--current"]
    assert main([*args, str(write(tmp_path, "fresh.json", fresh))]) == 0
    capsys.readouterr()
    assert main([*args, str(write(tmp_path, "stale.json", stale))]) == 2
    err = capsys.readouterr().err
    assert "stale_report" in err and "equation prompt equation-aaaa1111" in err
    # Without the flag (comparing two historical runs) it is only a note.
    plain = ["eval", "gate", "--baseline", str(baseline), "--current"]
    assert main([*plain, str(tmp_path / "stale.json")]) == 0
