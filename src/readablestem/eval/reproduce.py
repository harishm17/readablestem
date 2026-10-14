"""One command that shows what this repository can reproduce, and what it cannot.

    make reproduce
    uv run python -m readablestem.eval.reproduce [--rerun-offline]

By default it needs no API key, no network and no browser, and takes a few seconds. It

1. checks every gold file against the SHA-256 listed in its manifest;
2. checks that each committed eval report was scored against exactly this gold data
   (the report records the SHA-256 of the manifest it used);
3. runs the scorer on known-correct LaTeX for the gold equations and prints its ceiling:
   a perfect transcriber cannot score above it, because of known normalisation gaps;
4. prints the numbers in the committed reports, labelled as committed and not re-run.

It does not call a model. The committed model numbers cost money to make, and a second run
differs a little (the models are not deterministic); `docs/eval.md` gives the command and
its cap. With `--rerun-offline` it also re-runs the two tasks that call no model (headings
and tables in local mode) and compares them with the committed reports. That needs
network on the first run (OpenStax pages), Chromium and the fonts named in
`docs/development.md`.

Exit status: 0 when every check passed, 1 when one failed, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from readablestem.eval import pipeline_eval as pe
from readablestem.eval import tables as tables_eval
from readablestem.eval.equations import score_latex
from readablestem.eval.gold_scoring import iter_gold_records, load_predictions, score_equations

GOLD_DIR = Path("data/gold")
TABLE_GOLD_DIR = Path("data/gold/tables")
EVAL_DIR = Path("data/eval")
SCORER_CHECK = Path("tests/fixtures/sample_latex_predictions.jsonl")
RERUN_DIR = Path(".local/reproduce")
# Reports that `eval pipeline` writes as data/eval/latest-<task>.json; the tables task
# writes latest-tables-<mode>.json.
RERUN_TASKS = (
    # Headings call no model; the command still wants a cap, so it gets the smallest one.
    ("headings", ["--task", "headings", "--max-usd", "0.01"], "latest-headings.json"),
    ("tables (local)", ["--task", "tables", "--mode", "local"], "latest-tables-local.json"),
)
TOLERANCE = 1e-9


@dataclass
class Check:
    """One line of the output: what was checked, whether it held, and the evidence."""

    name: str
    ok: bool
    detail: str = ""
    extra: list[str] = field(default_factory=list)


def project_root() -> Path:
    from readablestem.cli.main import project_root as find_root

    return find_root()


# ------------------------------------------------------------------ gold integrity


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_gold_manifest(gold_dir: Path, label: str) -> Check:
    """Every file the manifest lists exists with its hash, and no gold file is unlisted."""
    manifest_path = gold_dir / "manifest.json"
    name = f"{label}: files match manifest.json"
    if not manifest_path.is_file():
        return Check(name, False, f"{manifest_path.name} not found in {label}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    listed = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    problems: list[str] = []
    for rel, digest in sorted(listed.items()):
        path = gold_dir / rel
        if not path.is_file():
            problems.append(f"missing: {rel}")
        elif _sha256(path) != digest:
            problems.append(f"changed: {rel}")
    on_disk = {p.relative_to(gold_dir).as_posix() for p in gold_dir.glob("openstax/*/*.jsonl")}
    problems += [f"not in manifest: {rel}" for rel in sorted(on_disk - set(listed))]
    if problems:
        return Check(name, False, f"{len(problems)} problem(s)", problems[:10])
    totals = ", ".join(f"{n} {kind}" for kind, n in sorted(manifest["totals"].items()))
    return Check(name, True, f"{len(listed)} files ({totals})")


def check_reports_use_this_gold(root: Path) -> Check:
    """Each committed report names the manifest hash it was scored against; it must be this one."""
    name = "committed reports were scored against this gold data"
    problems: list[str] = []
    seen: dict[str, str] = {}
    checked = 0
    for path in sorted((root / EVAL_DIR).glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        dataset = report.get("dataset") if isinstance(report, dict) else None
        if not isinstance(dataset, dict) or "manifest_sha256" not in dataset:
            continue  # tolerances.json and the like
        checked += 1
        gold_name = dataset.get("gold_dir", GOLD_DIR.as_posix())
        current = pe.manifest_sha256(root / gold_name)
        seen[gold_name] = current
        if dataset["manifest_sha256"] != current:
            problems.append(
                f"{path.name}: report has {dataset['manifest_sha256'][:12]}, "
                f"gold now has {current[:12]}"
            )
    if not checked:
        return Check(name, False, f"no eval reports found in {EVAL_DIR.as_posix()}")
    if problems:
        return Check(name, False, f"{len(problems)} report(s) are stale", problems)
    sets = ", ".join(f"{d} {h[:12]}" for d, h in sorted(seen.items()))
    return Check(name, True, f"{checked} reports; manifests {sets}")


# ------------------------------------------------------------------------- scorer


def check_scorer_ceiling(root: Path, predictions: Path | None = None) -> Check:
    """Score known-correct LaTeX against the gold equations and report the ceiling.

    Rows without `known_gap` must match leniently; rows with it must not (otherwise the
    fixture is stale). The ceiling is the share that matches.
    """
    path = predictions or root / SCORER_CHECK
    name = "scorer ceiling on known-correct LaTeX"
    gold = {r["id"]: r for r in iter_gold_records(root / GOLD_DIR, kind="equation")}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if {r["id"] for r in rows} != set(gold):
        return Check(name, False, "the predictions file and the gold equations differ")
    wrong: list[str] = []
    for row in rows:
        score = score_latex(row["latex"], gold[row["id"]]["mathml"])
        gap = "known_gap" in row
        if score.exact_lenient == gap:
            wrong.append(
                f"{row['id']}: marked as a known gap but matches"
                if gap
                else f"{row['id']}: correct LaTeX scored as wrong"
            )
    if wrong:
        return Check(name, False, f"{len(wrong)} row(s) disagree with the fixture", wrong[:10])
    summary = score_equations(list(gold.values()), load_predictions(path))["summary"]
    n = len(rows)
    matched = sum("known_gap" not in r for r in rows)
    ok = math.isclose(summary["exact_lenient"], matched / n, abs_tol=TOLERANCE)
    detail = (
        f"exact_lenient {summary['exact_lenient']:.3f} ({matched} of {n}), "
        f"exact_strict {summary['exact_strict']:.3f}, mean TEDS {summary['mean_teds']:.3f}; "
        f"{n - matched} known normalisation gaps, so equation scores understate quality "
        f"by up to {1 - matched / n:.0%}; the secondary stream scorer (T2-12) matches "
        f"{summary['secondary_stream_v2']['exact'] * n:.0f} of {n}"
    )
    return Check(name, ok, detail)


# ---------------------------------------------------------------- committed numbers


def _headline_keys(report: dict) -> list[str]:
    if report["task"] == "tables":
        return list(tables_eval.HEADLINE)
    return list(pe.HEADLINE[report["task"]])


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return str(value)


def committed_numbers(root: Path) -> list[str]:
    """The headline metrics of the committed reports, as printed. Metrics only, no text."""
    lines: list[str] = []
    for path in sorted((root / EVAL_DIR).glob("latest-*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        metrics, cost = report["metrics"], report.get("cost") or {}
        mode = f" ({report['mode']})" if report.get("mode") else ""
        status = "complete" if report["complete"] else f"PARTIAL ({report['stopped_reason']})"
        models = ", ".join(report.get("models_used") or []) or "no model"
        size = f"n = {report['n']} ({report['dataset']['unit']})"
        if cost.get("pdf_pages"):
            size += f", {cost['pdf_pages']} PDF pages"
        lines.append(
            f"{report['task']}{mode}: {size}, {status}; {report['date']}, "
            f"pipeline {report['pipeline_version']}, {models}, ${cost.get('total_usd', 0.0):.3f}"
        )
        shown = [k for k in _headline_keys(report) if k in metrics]
        lines += [f"    {key} = {_fmt(metrics[key])}" for key in shown]
    return lines


# ------------------------------------------------------------ offline re-run (no model)


def _run_cli(root: Path, args: Sequence[str]) -> int:
    """Run `ReadableSTEM <args>` in a child process; show its output only if it fails."""
    code = "import sys; from readablestem.cli.main import main; sys.exit(main())"
    done = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        tail = (done.stdout + done.stderr).strip().splitlines()[-8:]
        print("\n".join(tail), file=sys.stderr)
    return done.returncode


def compare_reports(committed: dict, fresh: dict) -> list[str]:
    """Headline metrics that differ between a committed report and a fresh one."""
    diffs: list[str] = []
    for key in _headline_keys(committed):
        a, b = committed["metrics"].get(key), fresh["metrics"].get(key)
        if isinstance(a, float) and isinstance(b, float):
            same = math.isclose(a, b, abs_tol=TOLERANCE)
        else:
            same = a == b
        if not same:
            diffs.append(f"{key}: committed {_fmt(a)}, now {_fmt(b)}")
    return diffs


def rerun_offline(
    root: Path, runner: Callable[[Path, Sequence[str]], int] = _run_cli
) -> list[Check]:
    """Re-run the tasks that call no model and compare them with the committed reports."""
    checks: list[Check] = []
    out_dir = root / RERUN_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, task_args, committed_name in RERUN_TASKS:
        name = f"re-run {label} (no model) and compare with {committed_name}"
        out = out_dir / committed_name
        out.unlink(missing_ok=True)
        status = runner(root, ["eval", "pipeline", *task_args, "--out", str(out)])
        if status != 0 or not out.is_file():
            checks.append(Check(name, False, f"the run failed (exit status {status})"))
            continue
        committed = json.loads((root / EVAL_DIR / committed_name).read_text(encoding="utf-8"))
        fresh = json.loads(out.read_text(encoding="utf-8"))
        diffs = compare_reports(committed, fresh)
        if diffs:
            checks.append(Check(name, False, f"{len(diffs)} headline metric(s) differ", diffs))
        else:
            checks.append(Check(name, True, "every headline metric is identical"))
    return checks


# ------------------------------------------------------------------------- main


LIVE_NOTE = """\
Not re-run here: the model numbers (equations, figures, document). They need an OpenAI key
and cost about $0.30 for all three on the sample (docs/eval.md, "How to rerun"):

    uv run --env-file .env readablestem eval pipeline --task equations --max-usd 0.50 \\
        --out .local/eval/equations.json
    uv run --env-file .env readablestem eval pipeline --task figures --max-usd 0.50 \\
        --out .local/eval/figures.json
    uv run --env-file .env readablestem eval pipeline --task document --max-usd 2.00 \\
        --out .local/eval/document.json

A new run will differ a little (the models are not deterministic); compare it with the
committed confidence intervals, not the last digit. The inputs are three sections of one
OpenStax book on which the checks were developed, so none of these is a held-out result."""


def run_checks(root: Path, *, offline_rerun: bool = False) -> list[Check]:
    checks = [
        check_gold_manifest(root / GOLD_DIR, "data/gold"),
        check_gold_manifest(root / TABLE_GOLD_DIR, "data/gold/tables"),
        check_reports_use_this_gold(root),
        check_scorer_ceiling(root),
    ]
    if offline_rerun:
        checks += rerun_offline(root)
    return checks


def render(checks: Sequence[Check], numbers: Sequence[str]) -> str:
    out = ["ReadableSTEM: offline reproduction", ""]
    for check in checks:
        out.append(f"[{'ok' if check.ok else 'FAIL'}] {check.name}")
        if check.detail:
            out.append(f"       {check.detail}")
        out += [f"       - {line}" for line in check.extra]
    out += ["", "Committed numbers (read from data/eval, not re-run here):", ""]
    out += [f"  {line}" for line in numbers]
    out += ["", LIVE_NOTE, ""]
    failed = [c for c in checks if not c.ok]
    out.append(
        "All checks passed." if not failed else f"{len(failed)} of {len(checks)} checks FAILED."
    )
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m readablestem.eval.reproduce",
        description=(__doc__ or "").split("\n\n")[0],
    )
    parser.add_argument(
        "--rerun-offline",
        action="store_true",
        help="also re-run the headings and tables tasks (no model) and compare with the "
        "committed reports; needs network on the first run, Chromium and fonts",
    )
    args = parser.parse_args(argv)
    root = project_root()
    checks = run_checks(root, offline_rerun=args.rerun_offline)
    print(render(checks, committed_numbers(root)))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
