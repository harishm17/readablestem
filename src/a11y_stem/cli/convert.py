"""CLI: a11y-stem convert FILE.pdf --out OUT.html (--max-usd X | --fake) ...

Runs the whole pipeline on one PDF and writes one HTML file. Model spend is capped twice
(docs/design/beta-architecture.md rule 3): `--max-usd` sets an in-memory spend guard
for this run, and `MAX_USD_PER_JOB` / `MAX_MODEL_CALLS_PER_JOB` cap the job. `--fake`
uses the offline fake model client instead (no API key, nothing billed).

Output on the terminal is content-free: counts, cost, time and check codes, plus the
paths given and written. Document text (title, paragraphs, alt text, LaTeX) is never
printed; it is only in the HTML and the optional JSON report.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from a11y_stem.config import ConfigError, Settings, get_settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import Check, ConversionOutput, Kind, Status
from a11y_stem.models.base import VisionClient


class _CliError(Exception):
    """Bad input or setup: printed as `error: ...`, exit code 2."""


def _positive_usd(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an amount in USD, got {value!r}") from None
    if not number > 0 or number == float("inf"):
        raise argparse.ArgumentTypeError(f"must be more than 0, got {value}")
    return number


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add this module's subcommands to `group` (top-level). Called by cli.main."""
    convert = group.add_parser(
        "convert",
        help="PDF -> accessible HTML (MathML, headings, alt text); needs --max-usd or --fake",
    )
    convert.add_argument("pdf", type=Path, help="input PDF")
    convert.add_argument("--out", type=Path, required=True, help="output HTML file")
    convert.add_argument("--pages", help="1-based page numbers, comma separated (default: all)")
    spend = convert.add_mutually_exclusive_group(required=True)
    spend.add_argument(
        "--max-usd",
        type=_positive_usd,
        help="spend cap for this run in USD (the job cap MAX_USD_PER_JOB also applies)",
    )
    spend.add_argument(
        "--fake", action="store_true", help="offline fake model client: no API calls, no cost"
    )
    convert.add_argument("--report", type=Path, help="also write the full result as JSON")
    convert.add_argument(
        "--axe", action="store_true", help="also run axe-core in headless Chromium"
    )
    convert.set_defaults(func=_cmd_convert)


# --------------------------------------------------------------------------- input


def _page_count(pdf_bytes: bytes, path: Path) -> int:
    import pymupdf

    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            return doc.page_count
    except Exception:  # pymupdf raises several types for unreadable files
        raise _CliError(f"cannot open {path} as a PDF") from None


def _parse_pages(spec: str | None, page_count: int, path: Path) -> list[int] | None:
    """--pages "1,3" -> [0, 2] (0-based), checked against the file's page count."""
    if spec is None:
        return None
    try:
        numbers = [int(p) for p in spec.split(",") if p.strip()]
    except ValueError:
        raise _CliError(f"--pages must be comma-separated page numbers, got {spec!r}") from None
    bad = [n for n in numbers if not 1 <= n <= page_count]
    if bad or not numbers:
        shown = ", ".join(map(str, bad)) or repr(spec)
        raise _CliError(f"page(s) {shown} out of range; {path} has pages 1-{page_count}")
    return [n - 1 for n in dict.fromkeys(numbers)]


def _make_client(settings: Settings, budget: JobBudget, *, fake: bool) -> VisionClient:
    """The model client from `models.make_vision_client` (WP1).

    Looked up at run time so this command works before the factory exists: --fake then
    falls back to a default `FakeVisionClient` bound to the same budget.
    """
    factory = getattr(importlib.import_module("a11y_stem.models"), "make_vision_client", None)
    if factory is None:
        if not fake:
            raise _CliError("no model client in this build (models.make_vision_client)")
        from a11y_stem.models.fake import FakeVisionClient

        return FakeVisionClient(budget=budget)
    try:
        return factory(settings, budget, fake=fake)
    except ConfigError as exc:
        raise _CliError(str(exc)) from None


# --------------------------------------------------------------------------- output


def _progress(done: int, total: int) -> None:
    print(f"page {done}/{total} done", file=sys.stderr, flush=True)


def _check_summary(checks: list[Check]) -> str:
    failed = [c for c in checks if not c.passed]
    text = f"{len(checks) - len(failed)}/{len(checks)} passed"
    if failed:
        text += " (failed: " + ", ".join(f"{c.code} [{c.severity}]" for c in failed) + ")"
    return text


def _axe_summary(checks: list[Check]) -> str:
    if any(c.code == "skipped" for c in checks):
        return (
            "skipped (needs the browser group: uv sync --group browser && "
            "uv run playwright install chromium)"
        )
    violations = [c for c in checks if not c.passed]
    if not violations:
        return "no violations"
    errors = sum(c.severity == "error" for c in violations)
    rules = ", ".join(c.code or c.name for c in violations)
    return f"{len(violations)} rule(s) violated, {errors} serious or critical: {rules}"


def _fix_table(output: ConversionOutput) -> list[str]:
    fixes = output.result.fixes
    statuses = [s for s in Status if any(f.status is s for f in fixes)]
    if not statuses:
        return ["  (no headings, equations or figures found)"]
    width = max(len(s.value) for s in statuses) + 2
    lines = ["  " + f"{'kind':<10}" + "".join(f"{s.value:>{width}}" for s in statuses)]
    for kind in (Kind.HEADING, Kind.EQUATION, Kind.FIGURE):
        row = [sum(1 for f in fixes if f.kind is kind and f.status is s) for s in statuses]
        if any(row):
            lines.append("  " + f"{kind.value:<10}" + "".join(f"{n:>{width}}" for n in row))
    return lines


def _print_summary(
    output: ConversionOutput,
    checks: list[Check],
    axe: list[Check] | None,
    *,
    fake: bool,
    cap_usd: float,
) -> None:
    result, stats = output.result, output.result.stats
    kinds = {k: sum(1 for e in result.elements if e.kind is k) for k in Kind}
    print(f"Pages:       {stats.pages}")
    print(
        f"Elements:    {len(result.elements)} ({kinds[Kind.HEADING]} headings, "
        f"{kinds[Kind.PARAGRAPH]} paragraphs, {kinds[Kind.EQUATION]} equations, "
        f"{kinds[Kind.FIGURE]} figures)"
    )
    print("Fixes:")
    for line in _fix_table(output):
        print(line)
    note = "fake client, nothing billed" if fake else f"cap ${cap_usd:.2f}"
    print(f"Model calls: {stats.model_calls}")
    print(f"Cost:        ${stats.cost_usd:.4f} ({note})")
    print(f"Time:        {stats.latency_ms / 1000:.1f} s")
    reasons = [f.reason or "" for f in result.fixes]
    budget = sorted({r for r in reasons if r.startswith("budget_")})
    if budget:
        stopped = sum(1 for r in reasons if r in budget)
        print(
            f"Budget:      stopped early ({', '.join(budget)}); {stopped} item(s) kept as originals"
        )
    print(f"Checks:      static {_check_summary(checks)}")
    if axe is not None:
        print(f"             axe-core {_axe_summary(axe)}")


def _write_report(path: Path, output: ConversionOutput, checks: list[Check], axe: Any) -> None:
    report = {
        "result": output.result.model_dump(mode="json", exclude={"html"}),
        "checks": [c.model_dump(mode="json") for c in checks],
        "axe": None if axe is None else [c.model_dump(mode="json") for c in axe],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- command


def _cmd_convert(args: argparse.Namespace) -> int:
    try:
        return _convert(args)
    except _CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _convert(args: argparse.Namespace) -> int:
    from a11y_stem.pipeline.html_checks import axe_checks, static_checks
    from a11y_stem.pipeline.layout import LayoutError
    from a11y_stem.pipeline.run import PipelineOutput, convert_pdf

    settings = get_settings()
    if not args.pdf.is_file():
        raise _CliError(f"PDF not found: {args.pdf}")
    for target in (args.out, args.report):
        if target is not None and target.is_dir():
            raise _CliError(f"{target} is a directory; give a file path")
    pdf_bytes = args.pdf.read_bytes()
    pages = _parse_pages(args.pages, _page_count(pdf_bytes, args.pdf), args.pdf)

    cap_usd = settings.max_usd_per_job if args.fake else args.max_usd
    budget = JobBudget(
        guard=InMemorySpendGuard(cap_usd=cap_usd),
        max_usd=settings.max_usd_per_job,
        max_calls=settings.max_model_calls_per_job,
    )
    client = _make_client(settings, budget, fake=args.fake)
    try:
        output = convert_pdf(
            pdf_bytes, settings=settings, client=client, pages=pages, progress=_progress
        )
    except LayoutError as exc:
        hint = (
            f" (more than MAX_PAGES_PER_JOB={settings.max_pages_per_job}; pick pages with --pages)"
            if exc.code == "too_many_pages"
            else ""
        )
        raise _CliError(f"cannot convert {args.pdf}: {exc.code}{hint}") from None

    html = output.result.html
    checks = output.checks if isinstance(output, PipelineOutput) else static_checks(html)
    axe = axe_checks(html) if args.axe else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    if args.report:
        _write_report(args.report, output, checks, axe)

    _print_summary(
        output, checks, axe, fake=args.fake, cap_usd=min(cap_usd, settings.max_usd_per_job)
    )
    print(f"wrote {args.out}")
    if args.report:
        print(f"wrote {args.report}")
    return 0
