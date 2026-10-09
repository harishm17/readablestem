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

Floors (the tables task): absolute bars a report must meet with or without a baseline,
so `eval gate --current latest-tables-local.json` alone gives a verdict. A floor can be
for one mode only (TEDS: 0.84 without the model, 0.88 with it). The real-document floors
are a second, separate part: they apply only when the report measured the private real
documents (`metrics.real_documents`), and are reported as skipped otherwise. The starting
floors sit a little under table-bench's measurements (docs/eval.md "tables"); the
tolerances file can move them (`{"floors": [{"name": ..., "minimum": ...}]}`).
"""

from __future__ import annotations

import json
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Rule = Literal["max_drop", "max_rel_increase", "max_increase", "minimum", "maximum"]
RULES = ("max_drop", "max_rel_increase", "max_increase")
# Report prompt_versions keys -> prompt file names (models/prompts/<name>.md). A prompt
# file that does not exist yet (the table step's, until it sends prompts) is skipped.
PROMPT_FILES = {
    "equation": "equation",
    "figure": "figure",
    "judge": "judge_figure",
    "table": "table",
}


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
    Tolerance("table_detection_recall", "tables", "metrics.detection_recall", "max_drop", 0.02),
    Tolerance(
        "table_detection_precision", "tables", "metrics.detection_precision", "max_drop", 0.03
    ),
    Tolerance("table_teds", "tables", "metrics.teds", "max_drop", 0.02),
    Tolerance(
        "table_column_header_exact", "tables", "metrics.column_header_exact", "max_drop", 0.03
    ),
    Tolerance("table_cost", "tables", "cost.per_table_usd", "max_rel_increase", 0.25),
)


@dataclass(frozen=True)
class Floor:
    """An absolute bar: the value at `path` must be >= `minimum` (or <= `maximum`).

    `mode`: only for reports of that mode (tables: "local" or "model"). `part`: "main",
    or "real" for the real-document gate, which only applies when the report measured
    real documents (`metrics.real_documents` is not null).
    """

    name: str
    task: str
    path: str
    minimum: float | None = None
    maximum: float | None = None
    mode: str | None = None
    part: Literal["main", "real"] = "main"


# Starting floors for tables (PDF-RECOMMENDATION.md 4.6 and its challenge): a little
# under table-bench's measurements. Replace them with the 95% CI lower bounds of the first
# full in-repo run (TODO(Harish): after the integrator's first full run).
DEFAULT_FLOORS: tuple[Floor, ...] = (
    Floor("table_detection_recall", "tables", "metrics.detection_recall", minimum=0.86),
    Floor("table_detection_precision", "tables", "metrics.detection_precision", minimum=0.93),
    Floor("table_teds_local", "tables", "metrics.teds", minimum=0.84, mode="local"),
    Floor("table_teds_model", "tables", "metrics.teds", minimum=0.88, mode="model"),
    Floor("table_column_header_exact", "tables", "metrics.column_header_exact", minimum=0.75),
    Floor(
        "table_wrong_cells_not_flagged",
        "tables",
        "metrics.text_fidelity.wrong_cells_not_flagged",
        maximum=0,
    ),
    Floor(
        "real_table_precision",
        "tables",
        "metrics.real_documents.precision",
        minimum=0.95,
        part="real",
    ),
    Floor(
        "real_table_full_extent_recall",
        "tables",
        "metrics.real_documents.full_extent_recall",
        minimum=0.85,
        part="real",
    ),
)


def load_floors(path: Path | None) -> list[Floor]:
    """Default floors, with entries of the tolerances file's "floors" list replacing (by
    name; an entry may give only `minimum` or `maximum`) or adding to them."""
    by_name = {f.name: f for f in DEFAULT_FLOORS}
    if path is None:
        return list(by_name.values())
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("floors", []):
        name = entry["name"]
        merged = (asdict(by_name[name]) if name in by_name else {}) | entry
        missing = {"task", "path"} - merged.keys()
        if missing or (merged.get("minimum") is None and merged.get("maximum") is None):
            raise ValueError(f"floor {name!r} needs task, path and a minimum or maximum")
        by_name[name] = Floor(**merged)
    return list(by_name.values())


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
    # "ok" | "regressed" | "missing" | "no_baseline"; floors: "ok" | "below_floor" |
    # "missing" | "skipped" (a real-document floor when no real documents were measured)
    status: str


@dataclass
class GateResult:
    task: str
    rows: list[GateRow]
    notes: list[str]
    floors: list[GateRow] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        failing = {"regressed", "missing", "below_floor"}
        return not any(r.status in failing for r in [*self.rows, *self.floors])


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
    if cur_ds.get("authored_sha256") != base_ds.get("authored_sha256"):
        raise GateRefused(
            "manifest_mismatch",
            "reports were produced from different authored documents (tables); re-run the "
            "baseline on the current set",
        )
    if current.get("mode") != baseline.get("mode"):
        raise GateRefused(
            "mode_mismatch",
            f"different modes: current {current.get('mode')!r}, baseline "
            f"{baseline.get('mode')!r} (local and model runs measure different things)",
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

    versions = {}
    for key, name in PROMPT_FILES.items():
        with suppress(FileNotFoundError):  # a step that sends no prompt yet
            versions[key] = load_prompt(name).version
    return versions


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


def floor_rows(report: dict, floors: list[Floor]) -> list[GateRow]:
    """Every floor of this report's task (and mode), checked against the report.

    A real-document floor is "skipped" when the report did not measure real documents
    (no private input directory); a missing value is "missing", which fails.
    """
    task, mode = report.get("task"), report.get("mode")
    rows = []
    for f in floors:
        if f.task != task or (f.mode is not None and f.mode != mode):
            continue
        rule: Rule = "minimum" if f.minimum is not None else "maximum"
        limit = float(f.minimum if f.minimum is not None else f.maximum or 0.0)
        value = lookup(report, f.path)
        if f.part == "real" and (report.get("metrics") or {}).get("real_documents") is None:
            status = "skipped"
        elif value is None:
            status = "missing"
        elif rule == "minimum":
            status = "ok" if value >= limit - 1e-9 else "below_floor"
        else:
            status = "ok" if value <= limit + 1e-9 else "below_floor"
        rows.append(GateRow(f.name, None, value, None, rule, limit, status))
    return rows


def has_floors(task: str | None, floors: list[Floor]) -> bool:
    return any(f.task == task for f in floors)


def gate(
    current: dict,
    baseline: dict | None,
    tolerances: list[Tolerance],
    floors: list[Floor],
) -> GateResult:
    """Floors of the current report's task, plus the regression check when there is a
    baseline. Raises GateRefused when neither applies, or the reports are not comparable."""
    task = current.get("task")
    if baseline is None:
        if not has_floors(task, floors):
            raise GateRefused(
                "no_baseline", f"task {task!r} has no floors: give --baseline to compare"
            )
        result = GateResult(task=str(task), rows=[], notes=[])
    else:
        result = compare(current, baseline, tolerances)
    result.floors = floor_rows(current, floors)
    if any(r.status == "skipped" for r in result.floors):
        result.notes.append(
            "real-document gate skipped: the report measured no private real documents "
            "(private-inputs/tables/ with labels.json)"
        )
    return result


def _num(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    if pct:
        return "+inf%" if value == float("inf") else f"{value * 100:+.1f}%"
    return f"{value:.4f}"


def format_table(result: GateResult) -> str:
    lines = [f"Eval gate: {result.task}", ""]
    if result.rows or not result.floors:
        lines += [
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
    if not result.rows and not result.floors:
        lines.append("| (no tracked metrics for this task) | | | | | |")
    if result.floors:
        if result.rows:
            lines.append("")
        lines += ["| floor | current | bar | part | status |", "|---|---|---|---|---|"]
        for r in result.floors:
            bar = f">= {r.limit:g}" if r.rule == "minimum" else f"<= {r.limit:g}"
            part = "real documents" if r.name.startswith("real_") else "gold"
            status = r.status.upper() if r.status != "ok" else "ok"
            lines.append(f"| {r.name} | {_num(r.current)} | {bar} | {part} | {status} |")
    if result.notes:
        lines += ["", *[f"- {n}" for n in result.notes]]
    verdict = "FAIL: tracked metric(s) regressed or below a floor"
    lines += ["", "PASS" if result.passed else verdict]
    return "\n".join(lines) + "\n"


def run_gate(
    current_path: Path,
    baseline_path: Path | None,
    tolerance_path: Path | None,
    *,
    require_current: bool = False,
) -> int:
    """Print the diff table; return the exit code (0 pass, 1 regression or a metric
    below its floor, 2 refused).

    Without a baseline only the floors are checked (tasks that have them: tables).
    `require_current`: also refuse a current report whose prompts or models differ from
    the code under test (CI sets it; see the module docstring).
    """
    current = json.loads(current_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path else None
    try:
        if require_current:
            from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL

            models = {"main": MAIN_MODEL, "cheap": CHEAP_MODEL, "judge": MAIN_MODEL}
            check_current(current, code_versions(), models)
        result = gate(
            current, baseline, load_tolerances(tolerance_path), load_floors(tolerance_path)
        )
    except GateRefused as exc:
        print(f"error: cannot compare ({exc.code}): {exc}", file=sys.stderr)
        return 2
    print(format_table(result), end="")
    return 0 if result.passed else 1
