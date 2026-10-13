"""Command line: `a11y-stem gold ...`, `a11y-stem eval ...`, `a11y-stem parse ...`,
`a11y-stem proof ...`.

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
from typing import Any

from a11y_stem import __version__
from a11y_stem.cli import convert, eval_cli, ops, serve
from a11y_stem.cli import heldout as heldout_cli
from a11y_stem.cli import proof as proof_cli

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

    if args.set:
        return _gold_fetch_set(args)
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
    _print_fetch(book, results, fetcher, cache_dir, out)
    return 0


def _gold_fetch_set(args: argparse.Namespace) -> int:
    """A named gold set: `tables` is the 13 OpenStax pages of the tables eval (table
    records only), written to data/gold/tables by default."""
    from a11y_stem.eval.tables import TABLE_GOLD_DIR, TABLE_GOLD_PAGES
    from a11y_stem.gold.openstax import GoldError, HttpFetcher, fetch_gold

    if args.page or args.chapter is not None:
        return _error("--set chooses its pages itself; drop --page and --chapter")
    if args.set == "heldout":
        return _gold_fetch_heldout(args)
    out = args.out or _default(TABLE_GOLD_DIR)
    cache_dir = None if args.no_cache else _default(CACHE_DIR)
    fetcher = HttpFetcher(cache_dir=cache_dir)
    for book_slug, slugs in TABLE_GOLD_PAGES.items():
        try:
            book, results = fetch_gold(
                book_slug,
                len(slugs),
                out,
                fetcher,
                page_slugs=list(slugs),
                retrieved_at=args.date,
                allow_retired=args.allow_retired,
                kinds=("table",),
            )
        except GoldError as exc:
            return _error(str(exc))
        _print_fetch(book, results, fetcher, cache_dir, out)
        print()
    return 0


def _gold_fetch_heldout(args: argparse.Namespace) -> int:
    """The pre-registered held-out set: 15 sections of five retired CC BY first editions,
    every record kind, each book read from its pinned archive release. Everything is kept
    outside the repository (cli.heldout); the written files are then checked against the
    registered hashes, which also names a different `--date` as the reason they differ."""
    from a11y_stem.gold.heldout import REGISTERED, fetch_set, verify_gold
    from a11y_stem.gold.openstax import GoldError, HttpFetcher

    loc = heldout_cli.locations(args.heldout_dir)
    out = args.out or loc.gold
    for what, path in (("--heldout-dir", loc.root), ("--out", out)):
        message = heldout_cli.outside_project(path, what)
        if message:
            return _error(message)
    cache_dir = None if args.no_cache else loc.openstax
    fetcher = HttpFetcher(cache_dir=cache_dir)
    try:
        fetched = fetch_set(
            out,
            fetcher,
            retrieved_at=args.date or dt.date.today().isoformat(),
            allow_retired=args.allow_retired,
        )
    except GoldError as exc:
        return _error(str(exc))
    for book, results in fetched:
        _print_fetch(book, results, fetcher, cache_dir, out)
        print()
    problems = verify_gold(out)
    if problems:
        print("the files were written, but they are not the registered held-out gold:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"matches the pre-registered held-out gold (manifest sha256 {REGISTERED.manifest_sha256})"
    )
    return 0


def _cmd_gold_verify(args: argparse.Namespace) -> int:
    from a11y_stem.gold.heldout import REGISTERED, verify_gold

    gold_dir = args.gold or heldout_cli.locations(args.heldout_dir).gold
    problems = verify_gold(gold_dir)
    if problems:
        print(heldout_cli.gold_problem(problems, gold_dir), file=sys.stderr)
        return 1
    print(
        f"{gold_dir} is the pre-registered held-out gold: {len(REGISTERED.sections)} sections, "
        f"manifest sha256 {REGISTERED.manifest_sha256}"
    )
    return 0


def _print_fetch(book: Any, results: list, fetcher: Any, cache_dir: Path | None, out: Path) -> None:
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
    if args.gold_set:
        resolved = heldout_cli.resolve(
            args.heldout_dir, args.gold, writes=(("--report", args.report),)
        )
        if isinstance(resolved, str):
            return _error(resolved)
        gold_path = resolved[1]
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
    which = fetch.add_mutually_exclusive_group(required=True)
    which.add_argument("--book", help="OpenStax slug, e.g. physics")
    which.add_argument(
        "--set",
        choices=["tables", "heldout"],
        help="a named gold set instead of --book: tables = the 13 OpenStax pages of the "
        "tables eval, table records only (default out: data/gold/tables); heldout = the 15 "
        "pre-registered held-out sections (needs --allow-retired; use --date "
        "2026-10-13 to rebuild the registered files byte for byte; default out: the "
        "held-out folder outside the repository)",
    )
    fetch.add_argument(
        "--pages", type=_positive_int, default=3, help="first N numbered sections (N >= 1)"
    )
    fetch.add_argument("--chapter", type=int, help="only sections of this chapter")
    fetch.add_argument(
        "--page", action="append", help="exact page slug (repeatable); overrides --pages"
    )
    fetch.add_argument("--out", type=Path, help="output dir (default: <project>/data/gold)")
    fetch.add_argument(
        "--heldout-dir",
        type=Path,
        help="with --set heldout: the folder for its gold, caches and reports "
        f"(default: {heldout_cli.default_root()})",
    )
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

    verify = gold.add_parser(
        "verify", help="check a gold folder against the pre-registered held-out set"
    )
    verify.add_argument("--set", choices=["heldout"], required=True)
    verify.add_argument("--gold", type=Path, help="gold dir (default: <heldout dir>/gold)")
    verify.add_argument(
        "--heldout-dir",
        type=Path,
        help=f"the held-out folder (default: {heldout_cli.default_root()})",
    )
    verify.set_defaults(func=_cmd_gold_verify)

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
    heldout_cli.add_set_arguments(eq)
    eq.set_defaults(func=_cmd_eval_equations)
    eval_cli.register(evaluate, top=sub)

    parse = sub.add_parser("parse", help="PDF -> elements (stub, no model calls)")
    parse.add_argument("pdf", type=Path)
    parse.add_argument("--pages", help="1-based page numbers, comma separated")
    parse.add_argument("--json", action="store_true")
    parse.set_defaults(func=_cmd_parse)

    convert.register(sub)
    serve.register(sub)
    ops.register(sub)
    proof_cli.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
