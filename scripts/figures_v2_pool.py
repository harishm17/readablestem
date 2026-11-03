"""Figure gold v2, step 1: the OpenStax candidate pool (docs/eval-figures-long.md).

    uv run python scripts/figures_v2_pool.py openstax [--cache DIR]

Reads every numbered section of the books in `figures_v2.OPENSTAX_BOOKS` from the OpenStax
archive (cached, polite delay), applies the licence, exclusion and reference gates and writes
`data/gold-figures-v2/pool-openstax.json`: one row per candidate (id, stratum, book, page), no
text. Page records are kept under CACHE (outside the repository) for the build step. No model
is called.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from readablestem.gold import figures_v2 as fv
from readablestem.gold import openstax as ox
from readablestem.gold.heldout import REGISTERED

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "gold-figures-v2"
PAGE_PATH = re.compile(r"openstax/([^/\s\"]+)/([^/\s\"]+?)\.jsonl")


def known_page_slugs(data_dir: Path) -> set[str]:
    """Slugs of every OpenStax page some other gold, dev or held-out set under `data/` already
    uses (file names and checksum lists), plus the registered held-out sections."""
    slugs = {section.slug for section in REGISTERED.sections}
    for path in data_dir.rglob("*"):
        if path == OUT or OUT in path.parents or not path.is_file():
            continue
        match = PAGE_PATH.search(path.as_posix())
        if match:
            slugs.add(match.group(2))
        if path.name in ("SHA256SUMS", "manifest.json", "selection.json"):
            slugs.update(m.group(2) for m in PAGE_PATH.finditer(path.read_text("utf-8")))
    return slugs


def drop_duplicate_images(rows: list[dict], records_path: Path) -> tuple[list[dict], int]:
    """Keep the first candidate (by id) of each image; the same file appears in several editions."""
    image_of = {}
    for line in records_path.read_text("utf-8").splitlines():
        record = json.loads(line)
        image_of[record["id"]] = record["images"][0]["url"].rsplit("/", 1)[-1]
    seen: set[str] = set()
    kept = []
    for row in rows:
        key = image_of[row["id"]]
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept, len(rows) - len(kept)


def cmd_openstax(args: argparse.Namespace) -> int:
    cache: Path = args.cache
    cache.mkdir(parents=True, exist_ok=True)
    fetcher = ox.HttpFetcher(cache_dir=cache / "http", delay_seconds=args.delay)
    excluded = known_page_slugs(DATA)
    rows: list[dict] = []
    books_meta: dict[str, dict] = {}
    gate_counts: Counter[str] = Counter()
    records_out = cache / "pool-records.jsonl"
    with records_out.open("w", encoding="utf-8") as sink:
        for slug in fv.OPENSTAX_BOOKS:
            book = ox.resolve_book(slug, fetcher, pin=None)
            try:
                ox.check_public_license(book)
                ox.check_book_state(book, allow_retired=True)
            except ox.GoldError as exc:
                gate_counts[f"book refused: {slug}"] += 1
                print(f"{slug}: refused ({exc})", file=sys.stderr)
                continue
            books_meta[slug] = {
                "title": book.title,
                "archive_version": book.archive_version,
                "version": book.version,
                "uuid": book.uuid,
                "licence_name": book.license_name,
                "licence_url": book.license_url,
                "state": book.state,
                "authors": book.authors,
            }
            pages = [p for p in ox.iter_pages(book.tree) if p.section is not None]
            print(f"{slug}: {len(pages)} sections", flush=True)
            for page in pages:
                if page.slug in excluded:
                    gate_counts["page excluded (held-out or already in another set)"] += 1
                    continue
                payload = fetcher.get_json(book.content_url(page.uuid))
                recs = ox.build_page_records(book, page, payload["content"], "pool")
                recs = [r for r in recs if r["kind"] == "figure"]
                found = fv.openstax_candidates(
                    slug,
                    page.slug,
                    recs,
                    licence_url=book.license_url,
                    excluded_pages=excluded,
                )
                for cand, record in found:
                    rows.append(cand.to_json())
                    sink.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    rows.sort(key=lambda r: r["id"])
    rows, duplicates = drop_duplicate_images(rows, cache / "pool-records.jsonl")
    gate_counts["same image already in the pool (other edition of a book)"] += duplicates
    cands = [fv.Candidate(**r) for r in rows]
    pool = {
        "set": "figures-v2",
        "source": "openstax",
        "seed": fv.SEED,
        "books": books_meta,
        "excluded_page_slugs": len(excluded),
        "gates": dict(gate_counts),
        "pool_sha256": fv.pool_sha256(cands),
        "by_stratum": fv.strata_counts(rows),
        "candidates": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pool-openstax.json").write_text(
        json.dumps(pool, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(pool["by_stratum"]), "pool", len(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("openstax")
    p.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache" / "readablestem" / "figures-v2"
    )
    p.add_argument("--delay", type=float, default=0.3)
    p.set_defaults(func=cmd_openstax)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
