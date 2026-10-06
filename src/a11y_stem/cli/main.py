"""Command line: `a11y-stem gold ...`, `a11y-stem eval ...`, `a11y-stem parse ...`.

Default paths (data/gold, .cache/openstax) are resolved against the project root: the
nearest parent of the working directory whose pyproject.toml names this project. Outside
a checkout they fall back to the working directory.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

from a11y_stem import __version__

GOLD_DIR = Path("data/gold")
CACHE_DIR = Path(".cache/openstax")
_PROJECT_NAME_LINE = 'name = "a11y-stem"'


def project_root(start: Path | None = None) -> Path:
    """Nearest directory at or above `start` (default: cwd) holding this project's pyproject."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file() and _PROJECT_NAME_LINE in pyproject.read_text(encoding="utf-8"):
            return directory
    return here


def _default(path: Path) -> Path:
    return project_root() / path


def _shown(path: Path) -> str:
    """Path relative to the working directory when possible (shorter output)."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _cmd_gold_fetch(args: argparse.Namespace) -> int:
    from a11y_stem.gold.openstax import GoldError, HttpFetcher, fetch_gold

    out = args.out or _default(GOLD_DIR)
    cache_dir = None if args.no_cache else _default(CACHE_DIR)
    fetcher = HttpFetcher(cache_dir=cache_dir)
    try:
        book, results = fetch_gold(
            args.book,
            args.pages,
            out,
            fetcher,
            chapter=args.chapter,
            page_slugs=args.page or None,
            retrieved_at=args.date,
            allow_retired=args.allow_retired,
        )
    except GoldError as exc:
        return _error(str(exc))

    cache_note = f"archive responses cached in {_shown(cache_dir)}" if cache_dir else "no cache"
    print(f"Book:     {book.title} ({book.slug}) {book.uuid}@{book.version}")
    print(f"State:    {book.state}")
    print(f"Archive:  {book.archive_version}")
    print(f"License:  {book.license_name} <{book.license_url}>")
    print(f"Requests: {fetcher.requests_made} ({cache_note})")
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
        print(f"wrote {_shown(result.path)}")
    print(f"wrote {_shown(out / 'manifest.json')}")
    print(f"wrote {_shown(out / 'ATTRIBUTION.md')}")
    return 0


def _cmd_gold_licenses(args: argparse.Namespace) -> int:
    from a11y_stem.gold.openstax import GoldError, HttpFetcher, license_table

    try:
        rows = license_table(HttpFetcher())
    except GoldError as exc:
        return _error(str(exc))
    if args.cc_by_only:
        rows = [r for r in rows if r["cc_by"]]
    retrieved_at = dt.date.today().isoformat()
    rows = [r | {"retrieved_at": retrieved_at} for r in rows]
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

    gold_dir = args.out or _default(GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"{gold_dir} is not a directory")
    manifest = write_manifest(gold_dir)
    print(json.dumps(manifest["totals"]))
    return 0


def _cmd_eval_equations(args: argparse.Namespace) -> int:
    from a11y_stem.eval.gold_scoring import (
        iter_gold_records,
        load_predictions,
        score_equations,
    )

    gold_path = args.gold or _default(GOLD_DIR)
    if not gold_path.exists():
        return _error(f"gold path not found: {gold_path}")
    try:
        gold = list(iter_gold_records(gold_path, kind="equation"))
    except ValueError as exc:  # malformed JSON, reported as file:line
        return _error(str(exc))
    if not gold:
        return _error(f"no equation records under {gold_path}")
    if args.self_check:
        preds = None
    else:
        try:
            preds = load_predictions(args.pred)
        except FileNotFoundError:
            return _error(f"predictions file not found: {args.pred}")
        except ValueError as exc:  # malformed JSON or missing fields, reported as file:line
            return _error(str(exc))
    result = score_equations(gold, preds, display=args.display)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(f"wrote {args.report}")
    print(json.dumps(result["summary"], indent=2))
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    import pymupdf

    from a11y_stem.pipeline.parse import parse_pdf

    if not args.pdf.is_file():
        return _error(f"PDF not found: {args.pdf}")
    try:
        with pymupdf.open(args.pdf) as doc:
            page_count = doc.page_count
    except Exception as exc:  # pymupdf raises several types for unreadable files
        return _error(f"cannot open {args.pdf} as a PDF: {exc}")
    pages = None
    if args.pages:
        try:
            numbers = [int(p) for p in args.pages.split(",") if p.strip()]
        except ValueError:
            return _error(f"--pages must be comma-separated page numbers, got {args.pages!r}")
        bad = [n for n in numbers if not 1 <= n <= page_count]
        if bad or not numbers:
            shown = ", ".join(map(str, bad)) or repr(args.pages)
            return _error(f"page(s) {shown} out of range; {args.pdf} has pages 1-{page_count}")
        pages = [n - 1 for n in numbers]
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
    fetch.add_argument(
        "--pages", type=_positive_int, default=3, help="first N numbered sections (N >= 1)"
    )
    fetch.add_argument("--chapter", type=int, help="only sections of this chapter")
    fetch.add_argument(
        "--page", action="append", help="exact page slug (repeatable); overrides --pages"
    )
    fetch.add_argument("--out", type=Path, help="output dir (default: <project>/data/gold)")
    fetch.add_argument("--date", help="retrieval date to record (default: today)")
    fetch.add_argument("--no-cache", action="store_true", help="ignore the local HTTP cache")
    fetch.add_argument(
        "--allow-retired",
        action="store_true",
        help="also accept books whose OpenStax state is not 'live' (retired, deprecated, "
        "unlisted); open decision, see docs/decisions/0001-scope.md question 2",
    )
    fetch.set_defaults(func=_cmd_gold_fetch)

    licenses = gold.add_parser("licenses", help="list OpenStax books and their licenses")
    licenses.add_argument("--cc-by-only", action="store_true")
    licenses.add_argument("--csv", type=Path, help="also write the table as CSV")
    licenses.set_defaults(func=_cmd_gold_licenses)

    manifest = gold.add_parser("manifest", help="rebuild manifest.json and ATTRIBUTION.md")
    manifest.add_argument("--out", type=Path, help="gold dir (default: <project>/data/gold)")
    manifest.set_defaults(func=_cmd_gold_manifest)

    evaluate = sub.add_parser("eval", help="score predictions").add_subparsers(
        dest="eval_command", required=True
    )
    eq = evaluate.add_parser("equations", help="score equations against the gold set")
    eq.add_argument("--gold", type=Path, help="gold JSONL file or dir (default: data/gold)")
    group = eq.add_mutually_exclusive_group(required=True)
    group.add_argument("--pred", type=Path, help="JSONL with id + latex or mathml")
    group.add_argument(
        "--self-check",
        action="store_true",
        help="plumbing check only: scores gold against itself (always 1.0); "
        "does not test normalisation",
    )
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
