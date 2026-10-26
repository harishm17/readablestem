"""Build the second fresh real-document table set (docs/tables.md, "Second held-out measurement";
data/real-tables-heldout2/README.md has the rule, written before any detector was run on it).

    uv run python scripts/tables_heldout2.py pool   --cache DIR
    uv run python scripts/tables_heldout2.py select --cache DIR [--n 10] [--seed S]
    uv run python scripts/tables_heldout2.py build  --cache DIR --out data/real-tables-heldout2

`pool` harvests the OAI-PMH records (licence included) of the sets and windows in `WINDOWS`, as
`scripts/realworld_select.py` does for the real-world corpus, and writes `DIR/pool2.json`: records
under CC BY 4.0, CC BY 3.0 or CC0, in disciplines the first table sets lack (statistics, computer
science, economics, biology, engineering). `select` takes the existing real-world pool and this one,
leaves out every paper used before (`used_ids`), sorts what is left into teaching material
(`TEACHING` in the title, comments or abstract) and the rest, and walks the teaching material, then
the rest if it gave too few, taking the strata in turn by the order of SHA-256(seed:id), until `n`
papers pass the gates (a main TeX file, 6 to 250 PDF pages, at least three data tables found in the
PDF's words by `gold/tabulars.py`). It never runs the table detector. `build` is
`tables_heldout.py build`: the pages with a table and their neighbours, and `labels.json` with the
boxes (proposals, to check by eye).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import httpx
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realworld_build as rb
import tables_heldout as th

from readablestem.gold import arxiv_corpus as ac
from readablestem.gold import tabulars as tab

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEED = "tables-heldout2-2026-10-22"
MIN_TABLES, MIN_PAGES, MAX_PAGES = 3, 6, 250

# Sets and windows added to the real-world pool's (`gold/arxiv_corpus.SETS`): the low-volume sets of
# the disciplines the first table sets lack, with long windows so that lecture-style papers are not
# drowned out. Fixed here before any record was looked at.
WINDOWS: tuple[ac.Window, ...] = (
    ac.Window("stat:stat:AP", "2024-07-01", "2025-06-30"),
    ac.Window("stat:stat:CO", "2023-01-01", "2025-06-30"),
    ac.Window("stat:stat:OT", "2019-01-01", "2025-06-30"),
    ac.Window("cs:cs:CY", "2025-01-01", "2025-06-30"),
    ac.Window("cs:cs:GL", "2016-01-01", "2025-06-30"),
    ac.Window("cs:cs:DM", "2023-01-01", "2025-06-30"),
    ac.Window("cs:cs:SE", "2025-04-01", "2025-06-30"),
    ac.Window("econ:econ:GN", "2023-01-01", "2025-06-30"),
    ac.Window("econ:econ:TH", "2019-01-01", "2025-06-30"),
    ac.Window("q-bio:q-bio:GN", "2019-01-01", "2025-06-30"),
    ac.Window("q-bio:q-bio:PE", "2019-01-01", "2025-06-30"),
    ac.Window("q-bio:q-bio:BM", "2019-01-01", "2025-06-30"),
    ac.Window("q-bio:q-bio:OT", "2016-01-01", "2025-06-30"),
    ac.Window("eess:eess:SY", "2024-01-01", "2025-06-30"),
    ac.Window("eess:eess:SP", "2024-07-01", "2025-06-30"),
    ac.Window("physics:physics:app-ph", "2023-01-01", "2025-06-30"),
    ac.Window("physics:physics:med-ph", "2019-01-01", "2025-06-30"),
    ac.Window("physics:physics:bio-ph", "2023-01-01", "2025-06-30"),
    ac.Window("math:math:OC", "2025-04-01", "2025-06-30"),
    ac.Window("q-fin:q-fin:GN", "2019-01-01", "2025-06-30"),
)
# Teaching material: wording of lecture notes, course handouts and tutorials, in the title or
# comments, or (the first 800 characters) in the abstract.
TEACHING = re.compile(
    r"\b(lectures?|lecture notes?|course notes?|notes on|notes for|these notes|tutorials?|primer"
    r"|introduction to|an introduction|introductory|handouts?|textbook|handbook|for beginners"
    r"|first course|a guide|guide to|pedagogical|teaching|a gentle|this course|course on)\b",
    re.IGNORECASE,
)
# Strata in the order they are walked, one paper from each in turn. A paper's stratum is the
# first rule that matches its categories, the first category being the primary one.
STRATA = ("statistics", "computer science", "economics", "biology", "engineering", "mathematics")


def stratum(categories: str) -> str | None:
    cats = categories.split()
    prim = cats[0]
    if "physics.ed-ph" in cats:
        return None  # what the first set was made of
    if prim.startswith("stat") or prim.startswith("math.ST"):
        return "statistics"
    if prim.startswith("cs."):
        return "computer science"
    if prim.startswith(("econ", "q-fin")):
        return "economics"
    if prim.startswith("q-bio") or prim in ("physics.bio-ph", "physics.med-ph"):
        return "biology"
    if prim.startswith(("eess", "physics.app-ph", "physics.flu-dyn", "physics.class-ph")) or (
        prim.startswith("math.OC")
    ):
        return "engineering"
    if prim.startswith("math"):
        return "mathematics"
    return None


def used_ids() -> set[str]:
    """Every paper any earlier measurement looked at: the real-world manifests and candidate
    logs, the real-world cache, both table sets and the first table set's selection walk."""
    used = set(th.used_ids())
    for name in ("candidates.json", "candidates-fresh.json", "candidates-fresh2.json"):
        data = json.loads((DATA / "realworld" / name).read_text())
        items = data if isinstance(data, list) else data.get("candidates", data.get("visited", []))
        used |= {c["id"] for c in items}
    used |= {
        p["id"]
        for p in json.loads((DATA / "realworld" / "manifest-fresh2.json").read_text())["papers"]
    }
    for folder in ("real-tables", "real-tables-heldout"):
        labels = json.loads((DATA / folder / "labels.json").read_text())
        used |= {d["source"].split(":", 1)[1] for d in labels["documents"]}
    selection = json.loads((DATA / "real-tables-heldout" / "selection.json").read_text())
    used |= {x["id"] for x in selection["accepted"] + selection["skipped"]}
    return used


def cmd_pool(args: argparse.Namespace) -> int:
    records: dict[str, ac.Record] = {}
    summary = []
    for window in WINDOWS:
        kept, total = ac.harvest(window, args.cache / "oai", pause=args.pause)
        new = 0
        for r in kept:
            if r.id not in records:
                records[r.id] = ac.annotate(r)
                new += 1
        summary.append(
            {
                "set": window.set_spec,
                "from": window.start,
                "until": window.end,
                "records": total,
                "accepted_licence": len(kept),
                "new": new,
            }
        )
    pool = {
        "windows": summary,
        "candidates": [r.to_json() for r in sorted(records.values(), key=lambda r: r.id)],
    }
    (args.cache / "pool2.json").write_text(json.dumps(pool, indent=1, ensure_ascii=False))
    print(json.dumps({"windows": len(summary), "records": len(records)}))
    return 0


def candidates(seed: str, pool_files: list[Path]) -> dict[str, list[dict]]:
    """The walk order: for each tier (teaching material, then the rest) each stratum's papers
    in the order of SHA-256(seed:id)."""
    taken = used_ids()
    seen: dict[str, dict] = {}
    for path in pool_files:
        for c in json.loads(path.read_text())["candidates"]:
            if c["id"] not in taken and c["id"] not in seen:
                seen[c["id"]] = c
    out: dict[str, list[dict]] = {}
    for c in seen.values():
        s = stratum(c["categories"])
        if s is None:
            continue
        pages = c.get("pages")
        if pages is not None and not MIN_PAGES <= pages <= MAX_PAGES:
            continue
        text = f"{c['title']}. {c.get('comments') or ''}. {(c.get('abstract') or '')[:800]}"
        tier = "teaching" if TEACHING.search(text) else "other"
        out.setdefault(f"{tier}/{s}", []).append(c)
    for items in out.values():
        items.sort(key=lambda c: hashlib.sha256(f"{seed}:{c['id']}".encode()).hexdigest())
    return out


def walk(queues: dict[str, list[dict]], tier: str):
    """One paper from each stratum in turn (the order of `STRATA`) until every queue is empty."""
    lines = {s: list(queues.get(f"{tier}/{s}", [])) for s in STRATA}
    while any(lines.values()):
        for s in STRATA:
            if lines[s]:
                yield s, lines[s].pop(0)


def cmd_select(args: argparse.Namespace) -> int:
    pools = [Path.home() / ".cache/readablestem/realworld/pool.json", args.cache / "pool2.json"]
    queues = candidates(args.seed, pools)
    sizes = {k: len(v) for k, v in sorted(queues.items())}
    print("candidates per tier/stratum:", json.dumps(sizes), flush=True)
    accepted, skipped = [], []
    headers = {"User-Agent": "readablestem-eval/1.0 (research; CC BY corpus)"}
    with httpx.Client(headers=headers) as client:
        for tier in ("teaching", "other"):
            if len(accepted) >= args.n:
                break  # the second tier is only walked when the first gave too few
            for strat, record in walk(queues, tier):
                if len(accepted) >= args.n:
                    break
                folder = args.cache / record["id"]
                try:
                    rb.download(
                        client, f"https://arxiv.org/pdf/{record['id']}", folder / "paper.pdf",
                        args.pause,
                    )  # fmt: skip
                    eprint = rb.download(
                        client, f"https://arxiv.org/e-print/{record['id']}",
                        folder / "e-print.bin", args.pause,
                    )  # fmt: skip
                    if not (folder / "src").is_dir():
                        rb.unpack(eprint, folder / "src")
                    doc = pymupdf.open(folder / "paper.pdf")
                    if not MIN_PAGES <= len(doc) <= MAX_PAGES:
                        raise rb.Skip("pdf_page_count", str(len(doc)))
                    found, words = th.read_tables(folder)
                    placed = [t for t in found if tab.locate(t, words)]
                    if len(placed) < MIN_TABLES:
                        raise rb.Skip("fewer_than_3_tables", f"{len(placed)} of {len(found)}")
                except rb.Skip as skip:
                    skipped.append(
                        {"id": record["id"], "tier": tier, "stratum": strat,
                         "reason": skip.code, "detail": skip.detail}
                    )  # fmt: skip
                    print("skip", record["id"], tier, strat, skip.code, skip.detail, flush=True)
                    continue
                except Exception as exc:  # a broken archive or PDF is a skip, never a choice
                    skipped.append(
                        {"id": record["id"], "tier": tier, "stratum": strat,
                         "reason": type(exc).__name__, "detail": ""}
                    )  # fmt: skip
                    print("skip", record["id"], tier, strat, type(exc).__name__, flush=True)
                    continue
                digest = hashlib.sha256((folder / "paper.pdf").read_bytes()).hexdigest()
                accepted.append(
                    {
                        "id": record["id"],
                        "title": record["title"],
                        "licence": record["licence_name"],
                        "categories": record["categories"],
                        "tier": tier,
                        "stratum": strat,
                        "pdf_pages": len(doc),
                        "tables": len(placed),
                        "pdf_sha256": digest,
                    }
                )
                print("accept", record["id"], tier, strat, len(doc), "pages", len(placed),
                      "tables", flush=True)  # fmt: skip
    out = {"seed": args.seed, "candidates": sizes, "accepted": accepted, "skipped": skipped}
    (args.cache / "selection.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"accepted": len(accepted), "skipped": len(skipped)}))
    return 0 if len(accepted) >= 8 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pool")
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--pause", type=float, default=3.0)
    p.set_defaults(run=cmd_pool)
    s = sub.add_parser("select")
    s.add_argument("--cache", type=Path, required=True)
    s.add_argument("--n", type=int, default=10)
    s.add_argument("--seed", default=SEED)
    s.add_argument("--pause", type=float, default=3.0)
    s.set_defaults(run=cmd_select)
    b = sub.add_parser("build")
    b.add_argument("--cache", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--max-pages", type=int, default=30)
    b.set_defaults(run=th.cmd_build)
    args = ap.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
