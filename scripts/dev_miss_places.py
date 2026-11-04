"""Where each arm's misses fall on a real-world part, by where the equation was typed.

    uv run python scripts/dev_miss_places.py --part dev --tag base-0503 [--reading cells]

Scores the arms as `scripts/realworld_report.py --rescore-chat` does (reading B with
`--reading cells`, the default here), then places every gold equation that is not exactly right
by the first rule that fits: typed in a table or a figure (any failure); a span cut at the wrong
edge in running text; one to three symbols in running text not found; other. Also prints, per
place, the pair buckets of `scripts/dev_buckets.py`. Counts are gold equations; the rate is per
100 gold equations of that arm's papers.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import realworld_report as rr

from readablestem.eval import pipeline_eval as pe

PLACES = ("running text and algorithm", "table", "figure (caption or panel)")


def place_of(kind: str) -> str:
    if kind.startswith("table"):
        return "table"
    if kind.startswith("figure"):
        return "figure (caption or panel)"
    return "running text and algorithm"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--part", default="dev")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--reading", choices=("registered", "cells"), default="cells")
    args = parser.parse_args()
    runs = rr.ROOT / "runs" / args.part
    gold = {
        r["id"]: r
        for r in pe.load_gold(rr.ROOT / "gold" / args.part)
        if r.get("kind") == "equation"
    }
    buckets = rr.load_buckets_module()
    kinds = rr.float_kinds(args.part, gold)
    arms: dict[str, list[dict]] = {}
    for label, rel in rr.ARMS.items():
        if label in rr.CHAT_ARM_IDS:
            rows = rr.rescored_chat_items(args.part, label)
        else:
            path = runs / rel.replace("rs/", f"{args.tag}/")
            rows = [json.loads(x) for x in path.read_text().splitlines() if x]
        arms[label] = [rr.apply_reading(r, args.reading) for r in rows]
    for label, items in arms.items():
        n_gold = sum(i["n_gold"] for i in items)
        print(f"### {label}: {len(items)} papers, {n_gold} gold")
        by_place: dict[str, Counter[str]] = {p: Counter() for p in PLACES}
        summary: Counter[str] = Counter()
        for item in items:
            for pair in item["pairs"]:
                g = gold[pair["gold_id"]]
                place = place_of(kinds.get(g["id"], "text"))
                bucket = buckets.classify_pair(pair, g["mathml"])
                by_place[place]["gold"] += 1
                by_place[place]["right" if bucket == "correct" else bucket] += 1
                if bucket == "correct":
                    continue
                if place != PLACES[0]:
                    summary["tables and figures"] += 1
                elif bucket == "span":
                    summary["inline span edges"] += 1
                else:
                    summary["other"] += 1
            for gid in item["missed_gold_ids"]:
                g = gold[gid]
                place = place_of(kinds.get(gid, "text"))
                by_place[place]["gold"] += 1
                by_place[place]["not_found"] += 1
                if place != PLACES[0]:
                    summary["tables and figures"] += 1
                elif g["display"] != "block" and g["tokens"] <= 3:
                    summary["short inline not found"] += 1
                else:
                    summary["other"] += 1
        misses = sum(summary.values())
        print("| place | gold | right | scorer | span | recognition | no proposal | not found |")
        print("|---|---|---|---|---|---|---|---|")
        for p in PLACES:
            c = by_place[p]
            print(
                f"| {p} | {c['gold']} | {c['right']} | {c['scorer']} | {c['span']} | "
                f"{c['recognition']} | {c['no_proposal']} | {c['not_found']} |"
            )
        print()
        print("| miss bucket | count | per 100 gold |")
        print("|---|---|---|")
        for name in ("tables and figures", "inline span edges", "short inline not found", "other"):
            print(f"| {name} | {summary[name]} | {100 * summary[name] / n_gold:.1f} |")
        print(f"| misses, total | {misses} | {100 * misses / n_gold:.1f} |")
        print(f"| right | {n_gold - misses} | {100 * (n_gold - misses) / n_gold:.1f} |")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
