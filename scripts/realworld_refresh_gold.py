"""Real-world set: the answer key of a part made again by the converter as it is now, as a part
of its own.

    uv run python scripts/realworld_refresh_gold.py --part dev --as dev2

The gold's MathML is a function of the author's LaTeX and the equation converter
(`arxiv_gold.to_mathml`). The converter changed after the development gold was made (a script
after a
bracket groups the bracket, docs/chemistry.md "Side effect"), so a ReadableSTEM run of today is
scored against a gold made by the older converter: `(X^\\top X)^{-1}` written identically on both
sides is marked wrong. This writes a copy of the part with the same equations, ids, PDFs and
LaTeX and the MathML converted again, under another part name, with its own manifest
(`data/realworld/manifest-NAME.json`); the registered manifests and gold are not touched.

Prints how many equations of each paper changed. Nothing here calls a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from readablestem.gold import arxiv_gold as ag

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO_DATA = Path(__file__).resolve().parent.parent / "data" / "realworld"


def gold_text(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", required=True)
    parser.add_argument("--as", dest="name", required=True)
    args = parser.parse_args()
    source = ROOT / "gold" / args.part
    target = ROOT / "gold" / args.name
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    (target / "pdfs").mkdir(parents=True, exist_ok=True)
    changed_total = total = 0
    for paper in manifest["papers"]:
        slug = paper["id"].replace("/", "_")
        rows = [
            json.loads(line)
            for line in (source / f"{slug}.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        changed = 0
        for row in rows:
            if row.get("kind") != "equation":
                continue
            fresh = ag.to_mathml(row["latex"], row["display"] == "block")
            if fresh != row["mathml"]:
                changed += 1
            row["mathml"] = fresh
        text = gold_text(rows)
        (target / f"{slug}.jsonl").write_text(text, encoding="utf-8")
        shutil.copyfile(source / "pdfs" / f"{slug}.pdf", target / "pdfs" / f"{slug}.pdf")
        paper["gold_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        changed_total += changed
        total += len(rows)
        print(f"{paper['id']}: {changed} of {len(rows)} changed")
    manifest["part"] = args.name
    manifest["refreshed_from"] = args.part
    out = json.dumps(manifest, indent=1, ensure_ascii=False) + "\n"
    (target / "manifest.json").write_text(out, encoding="utf-8")
    (REPO_DATA / f"manifest-{args.name}.json").write_text(out, encoding="utf-8")
    print(f"{args.name}: {changed_total} of {total} equations changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
