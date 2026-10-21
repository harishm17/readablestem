"""Paired comparison of document-task runs on the same development sections.

    uv run python scripts/dev_paired.py --ref REF.jsonl[,MORE] LABEL=ITEMS.jsonl[,MORE] ...

Each run is the per-page rows of `eval pipeline --task document --items` (or
`scripts/dev_chatbot.py score --out`). For every run it prints, on the sections it shares with
the reference, the primary `exact_lenient`, the symbols score with split/merge credit and the
wrong-in-symbols equations nobody was asked about per 100 gold, each as a difference from the
reference with a cluster bootstrap interval over sections (10,000 resamples of the sections,
ratio estimator, seed 11). A handful of sections gives wide and approximate intervals; the
registered analysis (PROOF.md) says the same of its 15. Development sets only.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def load(spec: str) -> dict[str, dict]:
    items: dict[str, dict] = {}
    for part in spec.split(","):
        for line in Path(part).read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                items[row["id"]] = row
    return items


def counts(item: dict) -> dict[str, float]:
    pairs = item["pairs"]
    credited = set(item["symbols_credited"]["split"]) | set(item["symbols_credited"]["merged"])
    symbols = {p["gold_id"] for p in pairs if p["exact_symbols"]}
    return {
        "gold": item["n_gold"],
        "primary": sum(p["exact_lenient"] for p in pairs),
        "symbols": len(symbols | credited),
        "unflagged": sum((not p["exact_symbols"]) and p.get("review") != "required" for p in pairs),
        "pages": item.get("pdf_pages", 0),
        "cost": item.get("cost_usd", 0.0),
    }


def rate(table: dict[str, dict], sections: list[str], key: str, per: str = "gold") -> float:
    return sum(table[s][key] for s in sections) / max(1, sum(table[s][per] for s in sections))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ref", required=True)
    parser.add_argument("runs", nargs="+", metavar="LABEL=ITEMS.jsonl")
    args = parser.parse_args()
    ref = {k: counts(v) for k, v in load(args.ref).items()}
    metrics = (
        ("primary", "gold", 1.0, "exact_lenient"),
        ("symbols", "gold", 1.0, "symbols credited"),
        ("unflagged", "gold", 100.0, "unflagged wrong per 100"),
    )
    for spec in args.runs:
        label, _, path = spec.partition("=")
        run = {k: counts(v) for k, v in load(path).items()}
        sections = sorted(set(ref) & set(run))
        rng = random.Random(11)
        print(f"== {label} against the reference, {len(sections)} sections")
        for key, per, scale, name in metrics:
            mine, base = (
                rate(run, sections, key, per) * scale,
                rate(ref, sections, key, per) * scale,
            )
            diffs = []
            for _ in range(10_000):
                pick = [rng.choice(sections) for _ in sections]
                diffs.append((rate(run, pick, key, per) - rate(ref, pick, key, per)) * scale)
            diffs.sort()
            print(
                f"  {name:26s} {mine:8.3f} vs {base:8.3f}  diff {mine - base:+8.3f}  "
                f"95% [{diffs[250]:+.3f}, {diffs[9750]:+.3f}]"
            )


if __name__ == "__main__":
    main()
