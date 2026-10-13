"""Held-out selection, step 2: the seeded stratified draw (no network, no model).

    python3 draw.py features.json draw.json

Per book (fixed order), eligible sections sorted by (equations per 1,000 words, slug) are
split into terciles by index (low: [0, n//3), mid: [n//3, 2n//3), high: [2n//3, n)).
Each tercile is shuffled with one random.Random seeded from SEED_TEXT, consumed in the
fixed order book by book, tercile low -> mid -> high. The first section of each shuffled
tercile is drawn; the rest of the order is the replacement queue for the page rules.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys

SEED_TEXT = "a11y-stem held-out OpenStax set, pre-registered 2026-10-13"
BOOKS = [
    "college-physics",
    "chemistry",
    "precalculus",
    "introductory-statistics",
    "intermediate-algebra",
]
TERCILES = ["low", "mid", "high"]


def main() -> int:
    feats = json.load(open(sys.argv[1], encoding="utf-8"))
    seed = int(hashlib.sha256(SEED_TEXT.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    out = {"seed_text": SEED_TEXT, "seed": seed, "books": {}}
    for book in BOOKS:
        ok = [r for r in feats["books"][book]["sections"] if "excluded" not in r]
        ok.sort(key=lambda r: (r["eq_per_1000_words"], r["slug"]))
        n = len(ok)
        cuts = [0, n // 3, 2 * n // 3, n]
        out["books"][book] = {}
        for i, name in enumerate(TERCILES):
            part = ok[cuts[i] : cuts[i + 1]]
            order = rng.sample([r["slug"] for r in part], len(part))
            out["books"][book][name] = {
                "n": len(part),
                "density_range": [part[0]["eq_per_1000_words"], part[-1]["eq_per_1000_words"]],
                "order": order,
            }
            print(f"{book:24s} {name:4s} n={len(part):3d} "
                  f"density {part[0]['eq_per_1000_words']}-{part[-1]['eq_per_1000_words']} "
                  f"first: {order[0]}")
    json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
