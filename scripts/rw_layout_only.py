"""Real-world set: what the layout alone finds, with no model and no cost
(docs/eval-equation-round2.md).

    uv run python scripts/rw_layout_only.py --part dev2 [--out FILE.json] [--workers 6]
        [--compare FILE.json] [--list-lost]

Runs `layout.analyze` (the tables and notation-memory flags on, as deployed) over the cut PDF
of every
paper of a part and aligns the equations it finds to the gold by their text layer, as the document
task does with no model answers (`document_page_item` with no fixes). Prints

- detection recall and precision, and span fidelity (the span's text layer within 0.99 of the gold
  equation's own text), pooled over the part;
- the gold equations the alignment lost although the layout found the same formula elsewhere in the
  page order (an unmatched prediction with the same text): a block read in another order than the
  source typed it (a float printed at the top of a page, a column read first), per paper.

`--out` keeps the numbers; `--compare` prints the change against an earlier file. Development parts
only: it is how a layout change is judged before the models are asked again.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
FIDELITY = 0.99


def one(args: tuple[str, str]) -> dict:
    part, paper = args
    from readablestem.core.types import ConversionOptions
    from readablestem.eval import pipeline_eval as pe
    from readablestem.pipeline import layout
    from readablestem.pipeline import options as run_options

    gold_dir = ROOT / "gold" / part
    records = [
        r
        for r in (
            json.loads(line)
            for line in (gold_dir / f"{paper}.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if r.get("kind") == "equation"
    ]
    pdf = (gold_dir / "pdfs" / f"{paper}.pdf").read_bytes()
    opts = ConversionOptions(tables=True, notation_memory=True)
    with run_options.using(opts):
        lay = layout.analyze(pdf)
    item = pe.document_page_item(f"arxiv/{paper}", records, lay.elements, {}, [], [])
    gold = {r["id"]: r for r in records}
    lost = []
    used: set[int] = set()
    for gid in item["missed_gold_ids"]:
        sig = pe.math_signature(gold[gid]["mathml"])
        best, at = 0.0, None
        for j, u in enumerate(item["unmatched"]):
            if j in used:
                continue
            s = pe.similarity(sig, pe.text_signature(u.get("pred_text")))
            if s > best:
                best, at = s, j
        if at is not None and best >= pe.EQUATION_ALIGN_MIN:
            used.add(at)
            lost.append(gid)
    fidelity = sum(p["text_similarity"] >= FIDELITY for p in item["pairs"])
    return {
        "paper": paper,
        "gold": item["n_gold"],
        "pred": item["n_pred"],
        "matched": item["matched"],
        "fidelity": fidelity,
        "lost_to_order": lost,
        "pairs": {p["gold_id"]: round(p["text_similarity"], 3) for p in item["pairs"]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", default="dev2")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--list-lost", action="store_true")
    args = parser.parse_args()
    papers = sorted(p.stem for p in (ROOT / "gold" / args.part / "pdfs").glob("*.pdf"))
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(one, [(args.part, p) for p in papers]))
    gold = sum(r["gold"] for r in rows)
    pred = sum(r["pred"] for r in rows)
    matched = sum(r["matched"] for r in rows)
    fidelity = sum(r["fidelity"] for r in rows)
    lost = sum(len(r["lost_to_order"]) for r in rows)
    print(
        f"{args.part}: gold {gold}, found {matched} (recall {matched / gold:.3f}, "
        f"precision {matched / pred:.3f}), span fidelity {fidelity / gold:.3f}, "
        f"lost to order {lost} ({100 * lost / gold:.2f} points)"
    )
    per = Counter({r["paper"]: len(r["lost_to_order"]) for r in rows if r["lost_to_order"]})
    print("lost to order by paper:", dict(per.most_common()))
    if args.list_lost:
        for r in rows:
            for gid in r["lost_to_order"]:
                print(" ", gid)
    result = {"part": args.part, "rows": rows}
    if args.compare:
        before = {r["paper"]: r for r in json.loads(args.compare.read_text())["rows"]}
        for r in rows:
            b = before.get(r["paper"])
            if b and (b["matched"] != r["matched"] or b["fidelity"] != r["fidelity"]):
                print(
                    f"  {r['paper']}: found {b['matched']} -> {r['matched']}, "
                    f"fidelity {b['fidelity']} -> {r['fidelity']}, "
                    f"lost to order {len(b['lost_to_order'])} -> {len(r['lost_to_order'])}"
                )
    if args.out:
        args.out.write_text(json.dumps(result) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
