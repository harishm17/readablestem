"""CLI: a11y-stem eval invented score|summary|judge|selfcheck|audit-sheet|audit-score|fingerprint.

`score`, `summary`, the audit commands and `fingerprint` make no model call and need no
key. `judge` calls the image-aware figure judge on a seeded sample of descriptions and
needs `--max-usd` (or `--fake`), like the other eval commands that can spend.

Nothing here prints document text: tables show counts and rates; the text of flagged
units goes only to the files `--units` and the audit commands write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive amount, got {value}")
    return number


def _pages(value: str | None) -> list[str] | None:
    return [part.strip() for part in value.split(",") if part.strip()] if value else None


def _write_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True), "utf-8")


# ------------------------------------------------------------------ score


def _cmd_score(args: argparse.Namespace) -> int:
    from a11y_stem.eval.invented import read, report, score, text

    for path in (args.pdf, args.output):
        if not path.is_file():
            return _error(f"file not found: {path}")
    reference = text.Reference.from_pdf(args.pdf.read_bytes())
    output = read.read_output(
        args.output.read_text("utf-8", errors="replace"), args.format, name=args.output.name
    )
    gold = None
    if args.gold:
        try:
            gold = score.gold_equation_mathml(args.gold, _pages(args.pages), book=args.book)
        except ValueError as exc:
            return _error(str(exc))
    result = score.score_output(reference, output, gold_equations=gold)
    data = report.build_report(
        result,
        system=args.system,
        section=args.section or args.pdf.stem,
        run=args.run,
        pdf_name=args.pdf.name,
        output_name=args.output.name,
        units=args.units,
        extra={"gold_equations": len(gold) if gold is not None else None},
    )
    if args.out:
        report.write_report(data, args.out)
        print(f"wrote {args.out}")
    entries = report.entries_by_system([data])
    print(report.format_markdown(report.summarize_reports([data])) if entries else "")
    return 0


# ------------------------------------------------------------------ summary


def _cmd_summary(args: argparse.Namespace) -> int:
    from a11y_stem.eval.invented import pool, report

    try:
        reports = [report.read_report(path) for path in args.reports]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _error(str(exc))
    summary = report.summarize_reports(reports)
    if args.compare:
        grouped = report.entries_by_system(reports)
        first, second = args.compare
        for name in (first, second):
            if name not in grouped:
                return _error(f"no reports for system {name!r} (have {', '.join(grouped)})")
        views = {
            view: {
                kind: pool.compare(
                    grouped[first], grouped[second], kind, content_only=content, level=args.level
                )
                for kind in pool.RATE_TYPES
            }
            for view, content in (("all_text", False), ("content_only", True))
        }
        summary["comparison"] = {
            "difference": f"{first} minus {second}",
            "level": args.level,
            **views,
        }
    if args.out:
        _write_json(summary, args.out)
        print(f"wrote {args.out}")
    print(report.format_markdown(summary))
    return 0


# ------------------------------------------------------------------ judge


def _judge_client(args: argparse.Namespace, settings: Any) -> tuple[Any, Any] | str:
    """(judge, budget) or an error message. The judge is `eval.judge.make_judge`'s: the
    run has no model of its own here, so `--judge-provider` defaults to MODEL_PROVIDER."""
    from pydantic import ValidationError

    from a11y_stem.config import ConfigError
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
    from a11y_stem.eval import judge as judge_mod

    if not args.fake and args.max_usd is None:
        return "--max-usd is required for real model calls (or pass --fake)"
    cap = args.max_usd if args.max_usd is not None else float("inf")
    budget = JobBudget(InMemorySpendGuard(cap_usd=cap), max_calls=args.max_calls, max_usd=cap)
    try:
        # No second check: the judge alone. The judge's own client is built on `budget`.
        judge = judge_mod.make_judge(
            settings,
            budget,
            run_client=None,
            provider=args.judge_provider,
            model=args.judge_model,
            fake=args.fake,
        )
    except ValidationError as exc:
        reasons = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        return f"invalid judge settings: {reasons}"
    except ConfigError as exc:
        return str(exc)
    return judge, budget


def _cmd_judge(args: argparse.Namespace) -> int:
    from a11y_stem.config import get_settings
    from a11y_stem.core.budget import BudgetExceeded
    from a11y_stem.eval import render
    from a11y_stem.eval.invented import figures, read

    if not args.output.is_file():
        return _error(f"file not found: {args.output}")
    output = read.read_output(
        args.output.read_text("utf-8", errors="replace"), args.format, name=args.output.name
    )
    references: list[figures.FigureReference] = []
    bindings: list[figures.Binding] = []
    if args.gold:
        image_for = render.ImageCache(args.images).png if args.images else None
        references = figures.load_references(args.gold, _pages(args.pages), image_for=image_for)
        bindings = figures.bind(output.descriptions, references)
    items, skipped = figures.judge_items(
        args.output.stem, output.descriptions, bindings, references
    )
    settings = get_settings()
    made = _judge_client(args, settings)
    if isinstance(made, str):
        return _error(made)
    judge, budget = made
    try:
        result = figures.judge_sample(
            items,
            population=len(output.descriptions),
            skipped=skipped,
            client=judge.client,
            settings=judge.settings,
            k=args.sample,
            seed=args.seed,
        )
    except BudgetExceeded as exc:
        return _error(f"stopped by the spend cap: {exc}")
    data = result.to_dict(units=args.units)
    data["spent_usd"] = round(getattr(budget, "spent_usd", 0.0), 6)
    if args.out:
        _write_json(data, args.out)
        print(f"wrote {args.out}")
    shown = {k: v for k, v in data.items() if k not in {"units", "by_source", "skipped"}}
    print(json.dumps(shown, indent=1, sort_keys=True))
    return 0


# ------------------------------------------------------------------ audit


def _cmd_audit_sheet(args: argparse.Namespace) -> int:
    from a11y_stem.eval.invented import audit, report, score, text

    sources = []
    for path in args.reports:
        data = report.read_report(path)
        units = data["score"].get("units")
        if units is None:
            return _error(f"{path.name} has no units: score with --units")
        pdf = args.pdf_dir / data["meta"]["pdf"]
        if not pdf.is_file():
            return _error(f"PDF not found: {pdf}")
        verdicts = [
            score.Verdict(
                kind=u["kind"],
                index=u["index"],
                text=u["text"],
                unsupported=u["unsupported"],
                authored=u["authored"],
            )
            for u in units
        ]
        sources.append(
            audit.AuditSource(
                data["meta"]["system"],
                data["meta"]["section"],
                data["meta"]["run"],
                verdicts,
                text.Reference.from_pdf(pdf.read_bytes()),
            )
        )
    items, meta = audit.build_sample(sources, per_stratum=args.per_stratum, seed=args.seed)
    audit.write_sheet(items, args.sheet)
    audit.write_key(items, meta, args.key)
    print(f"wrote {args.sheet} ({len(items)} units, no system names) and {args.key}")
    for stratum, sizes in sorted(meta["strata"].items()):
        print(f"  {stratum}: {sizes}")
    return 0


def _cmd_audit_score(args: argparse.Namespace) -> int:
    from a11y_stem.eval.invented import audit

    try:
        key = json.loads(args.key.read_text("utf-8"))
        labels = audit.read_labels(args.sheet)
    except (OSError, ValueError) as exc:
        return _error(str(exc))
    result = audit.score_audit(key, labels)
    if args.out:
        _write_json(result, args.out)
        print(f"wrote {args.out}")
    for stratum, row in sorted(result["strata"].items()):
        print(
            f"{stratum}: precision {row['precision']} recall {row['recall']} "
            f"miss rate {row['miss_rate']} raw rate {row['raw_rate']} "
            f"audited rate {row['audited_rate']} (unsure {row['unsure']})"
        )
    if result["unlabelled_items"]:
        print(f"{result['unlabelled_items']} units are not labelled yet")
    return 0


def _cmd_selfcheck(args: argparse.Namespace) -> int:
    from a11y_stem.eval.invented import plant, score, text

    for path in (args.pdf, args.output, args.plan):
        if not path.is_file():
            return _error(f"file not found: {path}")
    reference = text.Reference.from_pdf(args.pdf.read_bytes())
    gold = None
    if args.gold:
        try:
            gold = score.gold_equation_mathml(args.gold, _pages(args.pages), book=args.book)
        except ValueError as exc:
            return _error(str(exc))
    try:
        result = plant.self_check(
            reference,
            args.output.read_text("utf-8", errors="replace"),
            plant.load_plan(args.plan),
            gold_equations=gold,
            name=args.output.name,
        )
    except ValueError as exc:
        return _error(str(exc))
    if args.out:
        _write_json(result, args.out)
        print(f"wrote {args.out}")
    print("| Planted | Number | Found | Recall |")
    print("|---|---|---|---|")
    for kind, number in result["planted"].items():
        found = result["found"][kind]
        print(f"| {kind} | {number} | {found} | {100 * result['recall'][kind]:.0f}% |")
    print(
        f"Newly flagged units no plant explains: {result['newly_flagged_unexplained']}; "
        f"flagged before planting: {result['baseline_flagged']}."
    )
    return 0


def _cmd_fingerprint(args: argparse.Namespace) -> int:
    from a11y_stem.eval.invented import report, score

    print(json.dumps({"code": report.code_fingerprint(), "rules": score.rule_summary()}, indent=1))
    return 0


# ------------------------------------------------------------------ register


def register(group: argparse._SubParsersAction) -> None:
    """Add `eval invented ...` to the `eval` subparsers (cheap: nothing heavy is imported)."""
    invented = group.add_parser(
        "invented",
        help="invented-content rate: output the PDF does not support (docs/eval-invented.md)",
    )
    sub = invented.add_subparsers(dest="invented_command", required=True)

    score = sub.add_parser("score", help="apply the rules to one output (no model call)")
    score.add_argument("--pdf", type=Path, required=True, help="the source PDF")
    score.add_argument("--output", type=Path, required=True, help="a system's HTML or Markdown")
    score.add_argument("--format", choices=["html", "markdown"], help="default: from the name")
    score.add_argument("--system", required=True, help="name of the system that made the output")
    score.add_argument("--section", help="section id (default: the PDF's name)")
    score.add_argument("--run", default="1", help="run id (default 1)")
    score.add_argument("--gold", type=Path, help="gold dir: aligns equations to gold equations")
    score.add_argument("--pages", help="gold page slugs in the PDF's order, comma-separated")
    score.add_argument("--book", help="limit the gold equations to this book slug")
    score.add_argument("--out", type=Path, help="write the report as JSON")
    score.add_argument(
        "--units", action="store_true", help="put every unit and its verdict (with text) in it"
    )
    score.set_defaults(func=_cmd_score)

    summary = sub.add_parser("summary", help="pool reports into rates with intervals")
    summary.add_argument("reports", nargs="+", type=Path, help="reports from `score`")
    summary.add_argument("--out", type=Path, help="write the summary as JSON")
    summary.add_argument(
        "--compare", nargs=2, metavar=("A", "B"), help="paired difference A minus B"
    )
    summary.add_argument("--level", type=float, default=0.95, help="interval level of --compare")
    summary.set_defaults(func=_cmd_summary)

    judge = sub.add_parser(
        "judge", help="judge a seeded sample of figure descriptions against their images"
    )
    judge.add_argument("--output", type=Path, required=True, help="a system's HTML or Markdown")
    judge.add_argument("--format", choices=["html", "markdown"])
    judge.add_argument("--gold", type=Path, help="gold dir: figures an output has no image for")
    judge.add_argument("--pages", help="gold page slugs in the PDF's order, comma-separated")
    judge.add_argument("--images", type=Path, help="figure image cache (render.ImageCache dir)")
    judge.add_argument("--sample", type=_positive_int, default=30, help="descriptions to judge")
    judge.add_argument("--seed", default="a11y-stem invented-content figure sample")
    judge.add_argument("--max-usd", type=_positive_float, help="hard spend cap in USD")
    judge.add_argument("--max-calls", type=_positive_int, default=500)
    judge.add_argument("--fake", action="store_true", help="offline FakeVisionClient")
    judge.add_argument(
        "--judge-provider",
        choices=["openai", "anthropic"],
        help="provider of the judge (default: MODEL_PROVIDER); the result records it",
    )
    judge.add_argument(
        "--judge-model",
        metavar="ID",
        help="model of the judge (default: the main model on openai, claude-opus-5-5 on anthropic)",
    )
    judge.add_argument("--out", type=Path, help="write the result as JSON")
    judge.add_argument("--units", action="store_true", help="also write each judged unit's row")
    judge.set_defaults(func=_cmd_judge)

    sheet = sub.add_parser("audit-sheet", help="the blind audit sheet and its key")
    sheet.add_argument("--reports", nargs="+", type=Path, required=True, help="with --units")
    sheet.add_argument("--pdf-dir", type=Path, required=True, help="folder with the PDFs")
    sheet.add_argument("--sheet", type=Path, required=True, help="CSV to label (no system names)")
    sheet.add_argument("--key", type=Path, required=True, help="JSON key (keep from labeller)")
    sheet.add_argument("--per-stratum", type=_positive_int, default=50)
    sheet.add_argument("--seed", default="a11y-stem invented-content audit sample 2026-10-13")
    sheet.set_defaults(func=_cmd_audit_sheet)

    audit = sub.add_parser("audit-score", help="precision and recall from a labelled sheet")
    audit.add_argument("--key", type=Path, required=True)
    audit.add_argument("--sheet", type=Path, required=True, help="the sheet with labels filled in")
    audit.add_argument("--out", type=Path)
    audit.set_defaults(func=_cmd_audit_score)

    selfcheck = sub.add_parser(
        "selfcheck", help="plant inventions by hand in an output and see whether they are found"
    )
    selfcheck.add_argument("--pdf", type=Path, required=True, help="the source PDF")
    selfcheck.add_argument("--output", type=Path, required=True, help="a system's HTML")
    selfcheck.add_argument("--plan", type=Path, required=True, help="the planted inventions (JSON)")
    selfcheck.add_argument("--gold", type=Path, help="gold dir: aligns equations")
    selfcheck.add_argument("--pages", help="gold page slugs in the PDF's order")
    selfcheck.add_argument("--book", help="limit the gold equations to this book slug")
    selfcheck.add_argument("--out", type=Path, help="write the result as JSON")
    selfcheck.set_defaults(func=_cmd_selfcheck)

    fingerprint = sub.add_parser("fingerprint", help="SHA-256 of the rules' code and thresholds")
    fingerprint.set_defaults(func=_cmd_fingerprint)
