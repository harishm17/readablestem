"""Command line: `a11y-stem gold ...`, `a11y-stem eval ...`, `a11y-stem parse ...`."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from a11y_stem import __version__

DEFAULT_GOLD_DIR = Path("data/gold")
DEFAULT_CACHE_DIR = Path(".cache/openstax")


def _cmd_gold_fetch(args: argparse.Namespace) -> int:
    from a11y_stem.gold.openstax import GoldError, HttpFetcher, fetch_gold

    fetcher = HttpFetcher(cache_dir=None if args.no_cache else DEFAULT_CACHE_DIR)
    try:
        book, results = fetch_gold(
            args.book,
            args.pages,
            args.out,
            fetcher,
            chapter=args.chapter,
            page_slugs=args.page or None,
            retrieved_at=args.date,
        )
    except GoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Book:     {book.title} ({book.slug}) {book.uuid}@{book.version}")
    print(f"Archive:  {book.archive_version}")
    print(f"License:  {book.license_name} <{book.license_url}>")
    print(f"Requests: {fetcher.requests_made} (archive responses cached in {DEFAULT_CACHE_DIR})")
    print()
    width = max([len("TOTAL")] + [len(r.page.slug) for r in results]) + 2
    print(f"{'page':<{width}} {'equations':>9} {'figures':>8} {'tables':>7}")
    totals = {"equation": 0, "figure": 0, "table": 0}
    for result in results:
        for kind in totals:
            totals[kind] += result.counts.get(kind, 0)
        print(
            f"{result.page.slug:<{width}} {result.counts.get('equation', 0):>9} "
            f"{result.counts.get('figure', 0):>8} {result.counts.get('table', 0):>7}"
        )
    print(f"{'TOTAL':<{width}} {totals['equation']:>9} {totals['figure']:>8} {totals['table']:>7}")
    print()
    for result in results:
        print(f"wrote {result.path}")
    print(f"wrote {args.out / 'manifest.json'}")
    print(f"wrote {args.out / 'ATTRIBUTION.md'}")
    return 0


def _cmd_gold_licenses(args: argparse.Namespace) -> int:
    from a11y_stem.gold.openstax import GoldError, HttpFetcher, license_table

    try:
        rows = license_table(HttpFetcher())
    except GoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.cc_by_only:
        rows = [r for r in rows if r["cc_by"]]
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["slug"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} rows to {args.csv}")
    print(f"{'slug':<45} {'state':<11} {'CC BY':<6} {'archive':<8} license")
    for r in rows:
        print(
            f"{r['slug']:<45} {r['state'] or '':<11} {'yes' if r['cc_by'] else 'no':<6} "
            f"{'yes' if r['in_archive_release'] else 'no':<8} {r['license_url'] or ''}"
        )
    return 0


def _cmd_gold_manifest(args: argparse.Namespace) -> int:
    from a11y_stem.gold.openstax import write_manifest

    manifest = write_manifest(args.out)
    print(json.dumps(manifest["totals"]))
    return 0


def _cmd_eval_equations(args: argparse.Namespace) -> int:
    from a11y_stem.eval.gold_scoring import (
        iter_gold_records,
        load_predictions,
        score_equations,
    )

    gold = list(iter_gold_records(args.gold, kind="equation"))
    if not gold:
        print(f"error: no equation records under {args.gold}", file=sys.stderr)
        return 2
    preds = None if args.self_check else load_predictions(args.pred)
    result = score_equations(gold, preds, display=args.display)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(f"wrote {args.report}")
    print(json.dumps(result["summary"], indent=2))
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    from a11y_stem.pipeline.parse import parse_pdf

    pages = [int(p) - 1 for p in args.pages.split(",")] if args.pages else None
    elements = parse_pdf(args.pdf, pages=pages)
    if args.json:
        print(json.dumps([e.to_dict() for e in elements], ensure_ascii=False, indent=2))
        return 0
    for e in elements:
        text = (e.text or "").replace("\n", " / ")
        extra = f" score={e.score} ({', '.join(e.reasons)})" if e.score is not None else ""
        size = f" {e.width_px}x{e.height_px}px" if e.kind == "image" else ""
        print(f"p{e.page + 1} {e.kind:<18} {e.bbox}{size}{extra} {text[:80]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a11y-stem", description=__doc__)
    parser.add_argument("--version", action="version", version=f"a11y-stem {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    gold = sub.add_parser("gold", help="OpenStax gold set").add_subparsers(
        dest="gold_command", required=True
    )
    fetch = gold.add_parser("fetch", help="fetch pages of a CC BY OpenStax book into JSONL")
    fetch.add_argument("--book", required=True, help="OpenStax slug, e.g. physics")
    fetch.add_argument("--pages", type=int, default=3, help="first N numbered sections")
    fetch.add_argument("--chapter", type=int, help="only sections of this chapter")
    fetch.add_argument(
        "--page", action="append", help="exact page slug (repeatable); overrides --pages"
    )
    fetch.add_argument("--out", type=Path, default=DEFAULT_GOLD_DIR)
    fetch.add_argument("--date", help="retrieval date to record (default: today)")
    fetch.add_argument("--no-cache", action="store_true", help="ignore the local HTTP cache")
    fetch.set_defaults(func=_cmd_gold_fetch)

    licenses = gold.add_parser("licenses", help="list OpenStax books and their licenses")
    licenses.add_argument("--cc-by-only", action="store_true")
    licenses.add_argument("--csv", type=Path, help="also write the table as CSV")
    licenses.set_defaults(func=_cmd_gold_licenses)

    manifest = gold.add_parser("manifest", help="rebuild manifest.json and ATTRIBUTION.md")
    manifest.add_argument("--out", type=Path, default=DEFAULT_GOLD_DIR)
    manifest.set_defaults(func=_cmd_gold_manifest)

    evaluate = sub.add_parser("eval", help="score predictions").add_subparsers(
        dest="eval_command", required=True
    )
    eq = evaluate.add_parser("equations", help="score equations against the gold set")
    eq.add_argument("--gold", type=Path, default=DEFAULT_GOLD_DIR)
    group = eq.add_mutually_exclusive_group(required=True)
    group.add_argument("--pred", type=Path, help="JSONL with id + latex or mathml")
    group.add_argument("--self-check", action="store_true", help="score gold against itself")
    eq.add_argument("--display", choices=["all", "block", "inline"], default="all")
    eq.add_argument("--report", type=Path, help="write per-item scores as JSON")
    eq.set_defaults(func=_cmd_eval_equations)

    parse = sub.add_parser("parse", help="PDF -> elements (stub, no model calls)")
    parse.add_argument("pdf", type=Path)
    parse.add_argument("--pages", help="1-based page numbers, comma separated")
    parse.add_argument("--json", action="store_true")
    parse.set_defaults(func=_cmd_parse)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
