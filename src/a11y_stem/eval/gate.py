"""Regression gate: compare an eval report with a baseline and fail on tracked drops.

`a11y-stem eval gate --current X.json --baseline Y.json` exits

- 0 when every tracked metric is within its tolerance,
- 1 when at least one tracked metric regressed (or vanished from the current report),
- 2 when the two reports cannot be compared fairly: different task, different gold
  manifest, different item selection, a partial run on either side, a different judge
  (prompt or model: the measuring instrument changed), or, with `--require-current`, a
  current report produced with other prompts or models than the code under test has.

Why refuse instead of comparing anyway: a score on a different gold set, or on the 12 of
30 equations a run reached before its budget ran out, is a different measurement. So is
a figure pass rate graded by a different judge: when the judge started seeing the image
the rate went from 0.46 to 0.92 with the same alt texts. A gate that compared them would
pass or fail for reasons unrelated to the change under test. And a committed
`latest-<task>.json` that no longer matches the prompts in the code would let a prompt
change through without anyone re-running the eval; `--require-current` (used in CI)
refuses it.

Tolerances are absolute drops for rates (0.02 = two percentage points), a relative
increase for cost, and an absolute increase for counts (axe violations).
`data/eval/tolerances.json` overrides the defaults by name. With 30 equations one item
is 0.033, so the default 0.02 tolerance fails on any single lost item:
on purpose, while the gold set is this small.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

Rule = Literal["max_drop", "max_rel_increase", "max_increase"]
RULES = ("max_drop", "max_rel_increase", "max_increase")
# Report prompt_versions keys -> prompt file names (models/prompts/<name>.md).
PROMPT_FILES = {"equation": "equation", "figure": "figure", "judge": "judge_figure"}


class GateRefused(ValueError):
    """The reports are not comparable. `code` is a category for scripts and CI logs."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Tolerance:
    name: str
    task: str  # report["task"] this applies to
    path: str  # dotted path into the report, e.g. "metrics.exact_lenient"
    rule: Rule
    # max_drop: absolute drop; max_rel_increase: fraction (0.25 = +25%); max_increase:
    # absolute rise of a count (0 = any increase fails)
    limit: float


DEFAULT_TOLERANCES: tuple[Tolerance, ...] = (
    Tolerance("exact_lenient", "equations", "metrics.exact_lenient", "max_drop", 0.02),
    Tolerance("mean_teds", "equations", "metrics.mean_teds", "max_drop", 0.02),
    Tolerance("cost_per_equation", "equations", "cost.per_item_usd", "max_rel_increase", 0.25),
    Tolerance("heading_recall", "headings", "metrics.recall", "max_drop", 0.03),
    Tolerance("heading_precision", "headings", "metrics.precision", "max_drop", 0.03),
    Tolerance("figure_judge_pass_rate", "figures", "metrics.judge_pass_rate", "max_drop", 0.05),
    Tolerance(
        "document_detection_recall", "document", "metrics.detection_recall", "max_drop", 0.02
    ),
    Tolerance(
        "document_detection_precision",
        "document",
        "metrics.detection_precision",
        "max_drop",
        0.03,
    ),
    Tolerance(
        "document_matched_exact_lenient",
        "document",
        "metrics.matched_exact_lenient",
        "max_drop",
        0.03,
    ),
    Tolerance("document_axe_violations", "document", "metrics.axe_violations", "max_increase", 0),
    Tolerance("document_cost", "document", "cost.per_item_usd", "max_rel_increase", 0.25),
)


def load_tolerances(path: Path | None) -> list[Tolerance]:
    """Defaults, with entries from `path` replacing (by name) or adding to them.

    File format: {"tolerances": [{"name", "task", "path", "rule", "limit"}, ...]}. An
    entry with only "name" and "limit" adjusts a default's limit.
    """
    by_name = {t.name: t for t in DEFAULT_TOLERANCES}
    if path is None:
        return list(by_name.values())
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("tolerances", []):
        name = entry["name"]
        base = asdict(by_name[name]) if name in by_name else {}
        merged = base | entry
        missing = {"task", "path", "rule", "limit"} - merged.keys()
        if missing:
            raise ValueError(f"tolerance {name!r} is missing {', '.join(sorted(missing))}")
        if merged["rule"] not in RULES:
            raise ValueError(f"tolerance {name!r}: unknown rule {merged['rule']!r}")
        by_name[name] = Tolerance(
            name=name,
            task=merged["task"],
            path=merged["path"],
            rule=merged["rule"],
            limit=float(merged["limit"]),
        )
    return list(by_name.values())


def lookup(report: dict, path: str) -> float | None:
    """Value at a dotted path, or None when absent or not a number."""
    node: Any = report
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


@dataclass
class GateRow:
    name: str
    baseline: float | None
    current: float | None
    delta: float | None
    rule: Rule
    limit: float
    status: str  # "ok" | "regressed" | "missing" | "no_baseline"


@dataclass
class GateResult:
    task: str
    rows: list[GateRow]
    notes: list[str]

    @property
    def passed(self) -> bool:
        return not any(r.status in {"regressed", "missing"} for r in self.rows)


def check_comparable(current: dict, baseline: dict) -> None:
    """Raise GateRefused unless the two reports measure the same thing on the same data."""
    if current.get("task") != baseline.get("task"):
        raise GateRefused(
            "task_mismatch",
            f"different tasks: current {current.get('task')!r}, baseline {baseline.get('task')!r}",
        )
    cur_ds, base_ds = current.get("dataset", {}), baseline.get("dataset", {})
    if cur_ds.get("manifest_sha256") != base_ds.get("manifest_sha256"):
        raise GateRefused(
            "manifest_mismatch",
            "reports were produced from different gold manifests "
            f"(current {str(cur_ds.get('manifest_sha256'))[:12]}, "
            f"baseline {str(base_ds.get('manifest_sha256'))[:12]}); re-run the baseline "
            "on the current gold set",
        )
    if cur_ds.get("selection_sha256") != base_ds.get("selection_sha256"):
        raise GateRefused(
            "selection_mismatch",
            "reports cover different items (different --limit or gold subset); "
            f"current n_planned={current.get('n_planned')}, "
            f"baseline n_planned={baseline.get('n_planned')}",
        )
    for label, report in (("current", current), ("baseline", baseline)):
        if not report.get("complete", False):
            raise GateRefused(
                "partial_run",
                f"the {label} report is partial (n={report.get('n')} of "
                f"{report.get('n_planned')}, stopped: {report.get('stopped_reason')}); "
                "raise --max-usd and re-run",
            )
    judges = [_judge(report) for report in (current, baseline)]
    if judges[0] != judges[1]:
        raise GateRefused(
            "judge_changed",
            f"the reports were graded by different judges (baseline {judges[1]}, current "
            f"{judges[0]}); the instrument changed, so re-run the baseline with the current "
            "judge before comparing",
        )


def _judge(report: dict) -> tuple[str | None, str | None]:
    """(judge prompt version, judge model) of a report; (None, None) when unjudged."""
    prompts = report.get("prompt_versions") or {}
    models = report.get("models") or {}
    return prompts.get("judge"), models.get("judge")


def code_versions() -> dict[str, str]:
    """Prompt versions of the code under test, keyed like a report's `prompt_versions`."""
    from a11y_stem.models.prompts import load_prompt

    return {key: load_prompt(name).version for key, name in PROMPT_FILES.items()}


def check_current(report: dict, versions: dict[str, str], models: dict[str, str]) -> None:
    """Raise GateRefused("stale_report") when `report` was produced with other prompts
    or models than `versions` / `models` (the code under test)."""
    stale = []
    for key, version in sorted((report.get("prompt_versions") or {}).items()):
        if key in versions and version != versions[key]:
            stale.append(f"{key} prompt {version} (code: {versions[key]})")
    for key, model in sorted((report.get("models") or {}).items()):
        if key in models and model != models[key]:
            stale.append(f"{key} model {model} (code: {models[key]})")
    if stale:
        raise GateRefused(
            "stale_report",
            "the current report was not produced by this code: "
            + "; ".join(stale)
            + ". Re-run `a11y-stem eval pipeline` for this task and commit the new report",
        )


def _row(t: Tolerance, baseline: float | None, current: float | None) -> GateRow:
    if baseline is None:
        return GateRow(t.name, baseline, current, None, t.rule, t.limit, "no_baseline")
    if current is None:
        return GateRow(t.name, baseline, current, None, t.rule, t.limit, "missing")
    if t.rule == "max_drop":
        delta = current - baseline
        regressed = -delta > t.limit + 1e-9
    elif t.rule == "max_increase":
        delta = current - baseline
        regressed = delta > t.limit + 1e-9
    else:  # relative increase (cost): a zero baseline only tolerates zero
        if baseline == 0:
            delta = 0.0 if current == 0 else float("inf")
        else:
            delta = (current - baseline) / baseline
        regressed = delta > t.limit + 1e-9
    return GateRow(
        t.name, baseline, current, delta, t.rule, t.limit, "regressed" if regressed else "ok"
    )


def _notes(current: dict, baseline: dict) -> list[str]:
    """What changed between the runs (expected: that is why the gate runs)."""
    notes = []
    for key in ("pipeline_version", "prompt_versions", "models", "client"):
        if current.get(key) != baseline.get(key):
            notes.append(f"{key} changed: {baseline.get(key)} -> {current.get(key)}")
    return notes


def compare(current: dict, baseline: dict, tolerances: list[Tolerance]) -> GateResult:
    """Check comparability, then every tolerance for this task."""
    check_comparable(current, baseline)
    task = current["task"]
    rows = [
        _row(t, lookup(baseline, t.path), lookup(current, t.path))
        for t in tolerances
        if t.task == task
    ]
    return GateResult(task=task, rows=rows, notes=_notes(current, baseline))


def _num(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    if pct:
        return "+inf%" if value == float("inf") else f"{value * 100:+.1f}%"
    return f"{value:.4f}"


def format_table(result: GateResult) -> str:
    lines = [
        f"Eval gate: {result.task}",
        "",
        "| metric | baseline | current | change | allowed | status |",
        "|---|---|---|---|---|---|",
    ]
    for r in result.rows:
        relative = r.rule == "max_rel_increase"
        if relative:
            allowed = f"+{r.limit * 100:.0f}%"
        elif r.rule == "max_increase":
            allowed = f"+{r.limit:g}"
        else:
            allowed = f"-{r.limit:.3f}"
        change = (
            _num(r.delta, pct=True)
            if relative
            else ("n/a" if r.delta is None else f"{r.delta:+.4f}")
        )
        lines.append(
            f"| {r.name} | {_num(r.baseline)} | {_num(r.current)} | {change} | {allowed} "
            f"| {r.status.upper() if r.status != 'ok' else 'ok'} |"
        )
    if not result.rows:
        lines.append("| (no tracked metrics for this task) | | | | | |")
    if result.notes:
        lines += ["", *[f"- {n}" for n in result.notes]]
    lines += ["", "PASS" if result.passed else "FAIL: tracked metric(s) regressed"]
    return "\n".join(lines) + "\n"


def run_gate(
    current_path: Path,
    baseline_path: Path,
    tolerance_path: Path | None,
    *,
    require_current: bool = False,
) -> int:
    """Print the diff table; return the exit code (0 pass, 1 regression, 2 refused).

    `require_current`: also refuse a current report whose prompts or models differ from
    the code under test (CI sets it; see the module docstring).
    """
    current = json.loads(current_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    try:
        if require_current:
            from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL

            models = {"main": MAIN_MODEL, "cheap": CHEAP_MODEL, "judge": MAIN_MODEL}
            check_current(current, code_versions(), models)
        result = compare(current, baseline, load_tolerances(tolerance_path))
    except GateRefused as exc:
        print(f"error: cannot compare ({exc.code}): {exc}", file=sys.stderr)
        return 2
    print(format_table(result), end="")
    return 0 if result.passed else 1
