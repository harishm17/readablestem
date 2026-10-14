import hashlib
import json
import random
import sys
from pathlib import Path

gold = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / ".cache" / "ReadableSTEM" / "heldout"
seed = int(hashlib.sha256(b"a11y-stem held-out scorer sample 2026-10-13").hexdigest()[:16], 16)
rng = random.Random(seed)
books = ["college-physics", "chemistry", "precalculus", "introductory-statistics", "intermediate-algebra"]
recs = {b: [] for b in books}
for f in sorted(gold.rglob("*.jsonl")):
    for line in f.read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") == "equation":
            recs[r["book_slug"]].append(r)
out = []
for b in books:
    out += rng.sample(recs[b], 8)
Path(sys.argv[1]).write_text(
    json.dumps(
        [
            {"id": r["id"], "book": r["book_slug"], "display": r.get("display"), "mathml": r["mathml"]}
            for r in out
        ],
        indent=1,
    )
)
print(seed, len(out), {b: len(recs[b]) for b in books})
