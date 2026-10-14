"""CLI: a11y-stem convert FILE.pdf --out OUT.html (--max-usd X | --fake) ...

Runs the whole pipeline on one PDF and writes one HTML file. Model spend is capped twice
(docs/design/beta-architecture.md rule 3): `--max-usd` sets an in-memory spend guard
for this run, and `MAX_USD_PER_JOB` / `MAX_MODEL_CALLS_PER_JOB` cap the job. `--fake`
uses the offline fake model client instead (no API key, nothing billed); its answers are
placeholders, so the HTML then opens with a note saying it is an offline test run.

`--concurrency` is how many model steps run at once. Each one reserves its worst-case
cost before its request is sent and settles to the real cost afterwards, so with a small
`--max-usd` the concurrent reservations, not the spend, fill the cap: at 4 steps of about
$0.02 worst case each, a $0.10 cap refused a call after $0.016 had been spent. Without the
option the number is `MODEL_CONCURRENCY` (8), lowered to what the cap can hold
(`ops.limits.affordable_concurrency`); given, it is used as it is (1 reserves one call at
a time).

A real run checks its setup before converting anything: no API key for the provider
(OPENAI_API_KEY, or ANTHROPIC_API_KEY with MODEL_PROVIDER=anthropic) or a model
without a price is `error: ...` and exit 2. (WP1's client only reads the key on its first
request; without this check, every equation and figure would just come back flagged.)

Output on the terminal is content-free: counts, cost, time and check codes, plus the
paths given and written. Document text (title, paragraphs, alt text, LaTeX) is never
printed; it is only in the HTML and the optional JSON report.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from a11y_stem.config import ConfigError, Settings, get_settings
from a11y_stem.core import triage
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import Check, ConversionOutput, Fix, Kind, Status
from a11y_stem.models.base import VisionClient
from a11y_stem.models.pricing import UnknownModelError, price_for

# Shown at the top of the HTML from a --fake run: the offline client's answers are
# placeholders (WP1's OFFLINE_DEFAULTS), and the CLI has no review step to catch them.
FAKE_NOTICE = (
    "Offline test run: equations, figure descriptions and tables in this file are placeholders "
    "from a fake model, not conversions. Do not publish it."
)


# The most `--concurrency` takes; the default is `Settings.model_concurrency`.
MAX_CONCURRENCY = 16


class _CliError(Exception):
    """Bad input or setup: printed as `error: ...`, exit code 2."""


def _setup_message(exc: Exception) -> str:
    # UnknownModelError is a KeyError, whose str() is the repr of its message.
    return str(exc.args[0]) if exc.args else type(exc).__name__


def _positive_usd(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an amount in USD, got {value!r}") from None
    if not number > 0 or number == float("inf"):
        raise argparse.ArgumentTypeError(f"must be more than 0, got {value}")
    return number


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
    if not 1 <= number <= MAX_CONCURRENCY:
        raise argparse.ArgumentTypeError(f"must be 1 to {MAX_CONCURRENCY}, got {value}")
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
    convert.add_argument(
        "--concurrency",
        type=_positive_int,
        default=None,
        help="model steps run at once (default: MODEL_CONCURRENCY, lowered to what --max-usd "
        "can hold); each reserves its worst-case cost from --max-usd until it settles, so a "
        "small cap fills sooner with more",
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
    return [n - 1 for n in sorted(set(numbers))]  # document order, each page once


def _check_model_setup(settings: Settings) -> None:
    """Fail before converting if a real run cannot make a single model call.

    The real client reads the API key lazily and prices each request as it goes, so
    neither problem would surface until the steps run, one element at a time.
    """
    try:
        settings.require_provider_key()  # the key itself is not kept or printed
        for model in dict.fromkeys([settings.main_model, settings.cheap_model]):
            price_for(model)
    except (ConfigError, UnknownModelError) as exc:
        raise _CliError(_setup_message(exc)) from None


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
    except (ConfigError, UnknownModelError) as exc:
        raise _CliError(_setup_message(exc)) from None


# --------------------------------------------------------------------------- output

# What each budget reason means in a CLI run. The run cap is an InMemorySpendGuard, which
# reports the scope "daily" (it stands in for the web app's daily cap).
_BUDGET_LIMITS = {
    "budget_daily": "the --max-usd cap for this run",
    "budget_job_usd": "MAX_USD_PER_JOB",
    "budget_job_calls": "MAX_MODEL_CALLS_PER_JOB",
}


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
    if any(c.code == "axe_error" for c in checks):
        return "could not run (browser error; the HTML was still written)"
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
        return ["  (no headings, equations, figures or tables found)"]
    width = max(len(s.value) for s in statuses) + 2
    lines = ["  " + f"{'kind':<10}" + "".join(f"{s.value:>{width}}" for s in statuses)]
    for kind in (Kind.HEADING, Kind.EQUATION, Kind.FIGURE, Kind.TABLE):
        row = [sum(1 for f in fixes if f.kind is kind and f.status is s) for s in statuses]
        if any(row):
            lines.append("  " + f"{kind.value:<10}" + "".join(f"{n:>{width}}" for n in row))
    return lines


def _review_summary(fixes: list[Fix]) -> str:
    """'3 decisions needed (1 equation, 2 headings), 18 optional checks': what the
    review screen will ask for (a group of repeated headings is one decision)."""
    load = triage.review_load(fixes)
    kinds = ", ".join(
        f"{n} {kind}{'' if n == 1 else 's'}" for kind, n in sorted(load.required_by_kind.items())
    )
    decisions = load.required_decisions
    text = f"{decisions} {'decision' if decisions == 1 else 'decisions'} needed"
    if kinds:
        text += f" ({kinds})"
    return text + f", {load.optional} optional {'check' if load.optional == 1 else 'checks'}"


def _print_summary(
    output: ConversionOutput,
    checks: list[Check],
    axe: list[Check] | None,
    *,
    fake: bool,
    cap_usd: float,
    concurrency: int,
) -> None:
    result, stats = output.result, output.result.stats
    kinds = {k: sum(1 for e in result.elements if e.kind is k) for k in Kind}
    print(f"Pages:       {stats.pages}")
    print(
        f"Elements:    {len(result.elements)} ({kinds[Kind.HEADING]} headings, "
        f"{kinds[Kind.PARAGRAPH]} paragraphs, {kinds[Kind.LIST_ITEM]} list items, "
        f"{kinds[Kind.EQUATION]} equations, {kinds[Kind.FIGURE]} figures"
        + (f", {kinds[Kind.TABLE]} tables)" if kinds[Kind.TABLE] else ")")
    )
    print("Fixes:")
    for line in _fix_table(output):
        print(line)
    print(f"Review:      {_review_summary(result.fixes)}")
    if result.notices:  # codes only (content-free), as the job page lists them
        counts = Counter(n.code for n in result.notices)
        print("Notices:     " + ", ".join(f"{code} {n}" for code, n in sorted(counts.items())))
    print(f"Language:    {result.lang} ({result.lang_source})")
    note = "fake client, nothing billed" if fake else f"cap ${cap_usd:.2f}"
    print(f"Model calls: {stats.model_calls}")
    print(f"Cost:        ${stats.cost_usd:.4f} ({note})")
    print(f"Time:        {stats.latency_ms / 1000:.1f} s")
    reasons = [f.reason or "" for f in result.fixes]
    budget = sorted({r for r in reasons if r.startswith("budget_")})
    if budget:
        stopped = sum(1 for r in reasons if r in budget)
        limits = ", ".join(f"{r}: {_BUDGET_LIMITS.get(r, 'spend limit')}" for r in budget)
        print(f"Budget:      stopped early ({limits}); {stopped} item(s) kept as originals")
        if "budget_daily" in budget and not fake and concurrency > 1:
            # Reservations of in-flight steps count against the cap until they settle.
            print(
                f"             {concurrency} steps at once each hold a worst-case "
                "reservation; with a small --max-usd try --concurrency 1"
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
    from a11y_stem.observability.tracing import make_tracer
    from a11y_stem.pipeline.html_checks import axe_checks, static_checks
    from a11y_stem.pipeline.layout import LayoutError
    from a11y_stem.pipeline.run import FATAL_ERRORS, PipelineOutput, convert_pdf

    settings = get_settings()
    if not args.pdf.is_file():
        raise _CliError(f"PDF not found: {args.pdf}")
    for target in (args.out, args.report):
        if target is None:
            continue
        if target.is_dir():
            raise _CliError(f"{target} is a directory; give a file path")
        if target.resolve() == args.pdf.resolve():
            raise _CliError(f"{target} is the input PDF; choose another output path")
    if args.report is not None and args.report.resolve() == args.out.resolve():
        raise _CliError("--report and --out must be different files")
    pdf_bytes = args.pdf.read_bytes()
    pages = _parse_pages(args.pages, _page_count(pdf_bytes, args.pdf), args.pdf)

    cap_usd = settings.max_usd_per_job if args.fake else args.max_usd
    concurrency = args.concurrency
    if concurrency is None:
        from a11y_stem.ops.limits import affordable_concurrency

        concurrency = affordable_concurrency(
            settings, budget_usd=min(cap_usd, settings.max_usd_per_job)
        )
    budget = JobBudget(
        guard=InMemorySpendGuard(cap_usd=cap_usd),
        max_usd=settings.max_usd_per_job,
        max_calls=settings.max_model_calls_per_job,
    )
    if not args.fake:
        _check_model_setup(settings)
    client = _make_client(settings, budget, fake=args.fake)
    # NoopTracer unless TRACING_ENABLED and the Langfuse keys are set (content-free spans).
    tracer = make_tracer(settings)
    try:
        output = convert_pdf(
            pdf_bytes,
            settings=settings,
            client=client,
            pages=pages,
            progress=_progress,
            tracer=tracer,
            concurrency=concurrency,
            notice=FAKE_NOTICE if args.fake else None,
        )
    except FATAL_ERRORS as exc:  # the client's own setup failed on its first request
        raise _CliError(_setup_message(exc)) from None
    except LayoutError as exc:
        hint = (
            f" (more than MAX_PAGES_PER_JOB={settings.max_pages_per_job}; pick pages with --pages)"
            if exc.code == "too_many_pages"
            else ""
        )
        raise _CliError(f"cannot convert {args.pdf}: {exc.code}{hint}") from None
    finally:
        tracer.flush()  # spans are exported in batches; send them before the process exits

    html = output.result.html
    checks = output.checks if isinstance(output, PipelineOutput) else static_checks(html)
    axe = axe_checks(html) if args.axe else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    if args.report:
        _write_report(args.report, output, checks, axe)

    _print_summary(
        output,
        checks,
        axe,
        fake=args.fake,
        cap_usd=min(cap_usd, settings.max_usd_per_job),
        concurrency=concurrency,
    )
    print(f"wrote {args.out}")
    if args.report:
        print(f"wrote {args.report}")
    return 0
