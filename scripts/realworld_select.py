"""Real-world set, step 1: the candidate pool (arXiv papers under CC BY or CC0).

    uv run python scripts/realworld_select.py harvest [--root DIR] [--pause 3]
    uv run python scripts/realworld_select.py order --seed SEED

`harvest` reads the OAI-PMH records of the sets and windows in `gold/arxiv_corpus.SETS`, keeps those
whose licence is CC BY (3.0 or 4.0) or CC0, and writes `ROOT/pool.json`; the same list without
abstracts goes to `data/realworld/pool.json` (ids, titles, licences, style) for the repository.
`order` writes the seeded candidate order to `data/realworld/order-SEED.json`. Neither downloads a
paper. Raw OAI answers are cached under ROOT/oai, so a rerun is offline. ROOT defaults to
~/.cache/readablestem/realworld (outside the repository).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from readablestem.gold import arxiv_corpus as ac

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO_DATA = Path(__file__).resolve().parent.parent / "data" / "realworld"


def cmd_harvest(args: argparse.Namespace) -> int:
    root: Path = args.root
    seen_ids: set[str] = set()
    records: list[ac.Record] = []
    windows = []
    for window in ac.SETS:
        kept, total = ac.harvest(window, root / "oai", pause=args.pause)
        fresh = [r for r in kept if r.id not in seen_ids]
        seen_ids.update(r.id for r in fresh)
        records += [ac.annotate(r) for r in fresh]
        windows.append(
            {
                "set": window.set_spec,
                "from": window.start,
                "until": window.end,
                "records": total,
                "accepted_licence": len(kept),
                "new_after_dedup": len(fresh),
            }
        )
    eligible = [r for r in records if ac.eligible(r)]
    pool = {
        "windows": windows,
        "accepted_licences": ac.ACCEPTED_LICENCES,
        "page_range": [ac.MIN_PAGES, ac.MAX_PAGES],
        "n_records": len(records),
        "n_eligible": len(eligible),
        "candidates": [r.to_json() for r in sorted(eligible, key=lambda r: r.id)],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "pool.json").write_text(
        json.dumps(pool, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    slim = dict(pool)
    slim["candidates"] = [
        {k: v for k, v in c.items() if k not in ("abstract", "comments", "authors")}
        for c in pool["candidates"]
    ]
    REPO_DATA.mkdir(parents=True, exist_ok=True)
    (REPO_DATA / "pool.json").write_text(
        json.dumps(slim, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    styles = {s: sum(1 for r in eligible if r.style == s) for s in ("tutorial", "research")}
    print(json.dumps({"records": len(records), "eligible": len(eligible), "styles": styles}))
    return 0


def cmd_order(args: argparse.Namespace) -> int:
    pool = json.loads((args.root / "pool.json").read_text(encoding="utf-8"))
    records = [ac.Record(**c) for c in pool["candidates"]]
    skip: set[str] = set()
    for name in args.exclude or ():
        data = json.loads(Path(name).read_text(encoding="utf-8"))
        # a manifest (papers) or a candidates log (candidates): the ids it names
        skip |= {p["id"] for p in data.get("papers", data.get("candidates", []))}
    records = [r for r in records if r.id not in skip]
    ordered = ac.order_candidates(records, args.seed)
    out = {
        "seed": args.seed,
        "pool_sha256": ac.pool_sha256(pool),
        "excluded": len(skip),
        "order": [r.id for r in ordered],
    }
    path = REPO_DATA / f"order-{args.seed}.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(path, len(ordered), out["pool_sha256"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("harvest")
    h.add_argument("--pause", type=float, default=3.0)
    h.set_defaults(func=cmd_harvest)
    o = sub.add_parser("order")
    o.add_argument("--seed", required=True)
    o.add_argument(
        "--exclude", nargs="*", help="manifests or candidate logs whose papers are left out"
    )
    o.set_defaults(func=cmd_order)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
