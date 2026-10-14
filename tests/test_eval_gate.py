"""Eval gate: pass, fail, refusals, tolerance files, CLI exit codes."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from readablestem.cli.main import main
from readablestem.eval import gate
from readablestem.eval.gate import DEFAULT_TOLERANCES, GateRefused, compare, load_tolerances

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


def test_within_tolerance_passes():
    current = report(exact_lenient=0.79, mean_teds=0.885)  # -0.01, -0.015
    current["cost"]["per_item_usd"] = 0.0049  # +22.5%
    result = compare(current, report(), list(DEFAULT_TOLERANCES))
    assert result.passed
    assert statuses(result) == {
        "exact_lenient": "ok",
        "mean_teds": "ok",
        "cost_per_equation": "ok",
    }


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


def test_cost_against_a_zero_baseline():
    free = report()
    free["cost"]["per_item_usd"] = 0.0
    assert compare(free, copy.deepcopy(free), list(DEFAULT_TOLERANCES)).passed
    paid = copy.deepcopy(free)
    paid["cost"]["per_item_usd"] = 0.001
    assert not compare(paid, free, list(DEFAULT_TOLERANCES)).passed


def test_a_metric_missing_from_the_current_report_fails():
    current = report()
    current["metrics"]["mean_teds"] = None
    assert statuses(compare(current, report(), list(DEFAULT_TOLERANCES)))["mean_teds"] == "missing"
    baseline = report()
    del baseline["metrics"]["mean_teds"]
    result = compare(report(), baseline, list(DEFAULT_TOLERANCES))
    assert result.passed and statuses(result)["mean_teds"] == "no_baseline"


def test_only_the_reports_task_is_checked():
    result = compare(report("headings", recall=0.86), report("headings"), list(DEFAULT_TOLERANCES))
    assert statuses(result) == {
        "heading_recall": "regressed",  # -0.04 > 0.03
        "heading_precision": "no_baseline",  # these fixtures carry no precision
    }
    result = compare(
        report("figures", judge_pass_rate=0.66), report("figures"), list(DEFAULT_TOLERANCES)
    )
    assert result.passed  # -0.04 <= 0.05


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


def test_changes_between_runs_are_listed():
    current = report()
    current["prompt_versions"] = {"equation": "equation-bbbb2222"}
    result = compare(current, report(), list(DEFAULT_TOLERANCES))
    assert result.notes == [
        "prompt_versions changed: {'equation': 'equation-aaaa1111'} -> "
        "{'equation': 'equation-bbbb2222'}"
    ]
    table = gate.format_table(result)
    assert "| exact_lenient | 0.8000 | 0.8000 | +0.0000 | -0.020 | ok |" in table
    assert "| cost_per_equation | 0.0040 | 0.0040 | +0.0% | +25% | ok |" in table
    assert table.rstrip().endswith("PASS")


def test_tolerance_file_overrides_and_adds(tmp_path):
    path = tmp_path / "tol.json"
    path.write_text(
        json.dumps(
            {
                "tolerances": [
                    {"name": "exact_lenient", "limit": 0.05},
                    {
                        "name": "doc_recall",
                        "task": "document",
                        "path": "metrics.detection_recall",
                        "rule": "max_drop",
                        "limit": 0.0,
                    },
                ]
            }
        )
    )
    tolerances = {t.name: t for t in load_tolerances(path)}
    assert tolerances["exact_lenient"].limit == 0.05
    assert tolerances["exact_lenient"].path == "metrics.exact_lenient"  # rest from default
    assert tolerances["doc_recall"].task == "document"
    assert compare(report(exact_lenient=0.77), report(), list(tolerances.values())).passed


def test_bad_tolerance_entries_are_refused(tmp_path):
    path = tmp_path / "tol.json"
    path.write_text(json.dumps({"tolerances": [{"name": "new_one", "limit": 0.1}]}))
    with pytest.raises(ValueError, match="missing"):
        load_tolerances(path)
    path.write_text(json.dumps({"tolerances": [{"name": "exact_lenient", "rule": "max_rise"}]}))
    with pytest.raises(ValueError, match="unknown rule"):
        load_tolerances(path)


def test_committed_tolerances_match_the_defaults():
    committed = load_tolerances(REPO / "data" / "eval" / "tolerances.json")
    assert sorted(committed, key=lambda t: t.name) == sorted(
        DEFAULT_TOLERANCES, key=lambda t: t.name
    )


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


def test_cli_uses_a_tolerance_file(tmp_path, capsys):
    baseline = write(tmp_path, "base.json", report())
    bad = write(tmp_path, "bad.json", report(exact_lenient=0.77))
    tol = write(tmp_path, "tol.json", {"tolerances": [{"name": "exact_lenient", "limit": 0.05}]})
    args = ["eval", "gate", "--current", str(bad), "--baseline", str(baseline)]
    assert main([*args, "--tolerance-file", str(tol)]) == 0
    assert main(args) == 1  # data/eval/tolerances.json (= defaults) applies otherwise


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


def test_document_metrics_and_heading_precision_are_tracked():
    base = report("document", detection_recall=1.0, detection_precision=0.47)
    base["metrics"] |= {"matched_exact_lenient": 0.567, "axe_violations": 0}
    worse = copy.deepcopy(base)
    worse["metrics"] |= {"axe_violations": 1, "detection_precision": 0.40}
    result = compare(worse, base, list(DEFAULT_TOLERANCES))
    rows = statuses(result)
    assert rows["document_axe_violations"] == "regressed"  # any new violation fails
    assert rows["document_detection_precision"] == "regressed"
    assert rows["document_matched_exact_lenient"] == "ok"
    headings = compare(
        report("headings", precision=0.80),
        report("headings", precision=0.877),
        list(DEFAULT_TOLERANCES),
    )
    assert statuses(headings)["heading_precision"] == "regressed"


def test_committed_tolerances_track_the_same_metrics_as_the_defaults():
    names = {t.name for t in load_tolerances(Path("data/eval/tolerances.json"))}
    assert names == {t.name for t in DEFAULT_TOLERANCES}


def test_cli_reports_missing_and_malformed_files(tmp_path, capsys):
    baseline = write(tmp_path, "base.json", report())
    assert main(["eval", "gate", "--current", "nope.json", "--baseline", str(baseline)]) == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert main(["eval", "gate", "--current", str(broken), "--baseline", str(baseline)]) == 2
    assert "cannot read" in capsys.readouterr().err
