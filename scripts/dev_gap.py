"""Where a chatbot run is right and the pipeline is not, on the same development sections.

    uv run python scripts/dev_gap.py --gold data/dev-v2/dev --other CHAT.jsonl[,MORE] \
        --ours LABEL=ITEMS.jsonl[,MORE] [--ours ...] [--examples 10]

For every gold equation the chatbot gets exactly right (primary `exact_lenient`), counts what the
pipeline did with it, in the buckets of `scripts/dev_buckets.py` (right; scorer: symbols right and
the primary says no; undetected; span; recognition; no proposal), and prints concrete examples
(section, gold symbols, the pipeline's LaTeX and the span it cut, the chatbot's LaTeX) for the
first run given. Development sets only.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

import dev_buckets as db

from readablestem.eval import equations_symbols as sym
from readablestem.eval import pipeline_eval as pe


def pairs_of(spec: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for part in spec.split(","):
        for item in db.rows_of(Path(part)):
            for pair in item["pairs"]:
                out[pair["gold_id"]] = pair
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--other", required=True, help="the chatbot run's items")
    parser.add_argument("--ours", action="append", required=True, metavar="LABEL=ITEMS")
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args()
    gold = {r["id"]: r for r in pe.load_gold(args.gold) if r.get("kind") == "equation"}
    section_of = {g: f"{r['book_slug']}/{r['page_slug']}" for g, r in gold.items()}
    other = pairs_of(args.other)
    ids = {g for g, p in other.items() if p["exact_lenient"]}
    for spec in args.ours:
        label, _, path = spec.partition("=")
        ours = pairs_of(path)
        sections = {i["id"] for part in path.split(",") for i in db.rows_of(Path(part))}
        mine = {g for g in ids if section_of[g] in sections}
        everyone = {g for g in gold if section_of[g] in sections}
        theirs_right = {g for g in everyone if other.get(g, {}).get("exact_lenient")}
        ours_right = {g for g in everyone if ours.get(g, {}).get("exact_lenient")}
        print(
            f"== {label}: of {len(everyone)} gold equations the chatbot is right on "
            f"{len(theirs_right)}, the pipeline on {len(ours_right)}; "
            f"both {len(theirs_right & ours_right)}, "
            f"only the chatbot {len(theirs_right - ours_right)}, only the pipeline "
            f"{len(ours_right - theirs_right)}"
        )
        buckets: collections.Counter[str] = collections.Counter()
        examples = []
        for g in sorted(mine):
            pair = ours.get(g)
            bucket = "undetected" if pair is None else db.classify_pair(pair, gold[g]["mathml"])
            buckets[bucket] += 1
            if bucket != "correct" and len(examples) < 400:
                examples.append((g, bucket, pair))
        wrong = sum(v for k, v in buckets.items() if k != "correct")
        print(f"== {label}: the chatbot is right on {len(mine)}; the pipeline is not on {wrong}")
        for name, n in buckets.most_common():
            print(f"   {name:12s} {n:4d}")
        # spread the examples over the buckets
        by_bucket: dict[str, list] = collections.defaultdict(list)
        for g, bucket, pair in examples:
            by_bucket[bucket].append((g, pair))
        order = [b for b, _ in buckets.most_common() if b != "correct"]
        picks: list[tuple[str, str, dict | None]] = []
        round_ = 0
        while len(picks) < args.examples and any(by_bucket[b][round_:] for b in order):
            for b in order:
                if len(picks) < args.examples and len(by_bucket[b]) > round_:
                    picks.append((b, *by_bucket[b][round_]))
            round_ += 1
        for bucket, g, pair in picks:
            theirs = other[g]
            print(f"-- [{bucket}] {g}")
            print("   gold  :", " ".join(sym.symbols_of(gold[g]["mathml"]))[:140])
            print("   ours  :", ((pair or {}).get("pred_latex") or "(nothing aligned)")[:140])
            if pair:
                print("   span  :", (pair.get("pred_text") or "")[:100])
            print("   chat  :", (theirs.get("pred_latex") or "")[:140])


if __name__ == "__main__":
    main()
