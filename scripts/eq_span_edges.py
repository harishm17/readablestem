"""Inline-maths detection and span edges of the layout alone, with no model call and no cost.

    uv run python scripts/eq_span_edges.py --part dev2 [--out FILE.json] [--workers 6]
        [--compare FILE.json] [--examples N]

Runs `layout.analyze` (tables and notation memory on, as deployed) over the cut PDF of every paper
of a real-world part, aligns the equations it finds to the gold by their text layer as the document
task does with no model answers (`document_page_item` with no fixes), and prints, for the inline
gold equations and the inline predictions:

- detection recall by gold length (one symbol, two or three, longer) and in table cells;
- span edges: of the aligned pairs, how many have a text layer within `FIDELITY` of the gold
  equation (`text_similarity`), and for the others whether the span is cut short or runs on, at its
  start or its end (compared as text signatures);
- precision: aligned predictions over all inline predictions, and the unaligned ones by the reason
  `document_page_item` gives them.

`--compare` prints what changed against an earlier `--out` file, with the changed gold equations
(`--examples N` lists up to N of each kind). Development parts only: it is how a layout change is
judged before the models are asked again (docs/eval-equation-diagnostics.md).
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


def length_bucket(tokens: int) -> str:
    return "1" if tokens <= 1 else "2-3" if tokens <= 3 else "4+"


def _letters(signature: str) -> str:
    """The letters and digits of a signature, with the code points a text layer and a gold
    formula spell differently folded (increment sign and Delta, the Greek variants)."""
    fold = {"\u2206": "\u03b4", "\u03d5": "\u03c6", "\u03f5": "\u03b5", "\u03d1": "\u03b8"}
    return "".join(c for c in (fold.get(c, c) for c in signature) if c.isalnum())


def edge_class(gold_sig: str, pred_sig: str) -> str:
    """How a span with a text layer unlike the gold's is wrong, by the letters and digits alone
    (a text layer and a gold formula spell accents, scripts' order and operators differently):
    "ran on" when the span has letters or digits the gold has not, "cut short" when the gold
    has letters or digits the span lacks, "inside" when they are the same letters in another
    spelling, which is a different print of the same maths and not a wrong edge."""
    from collections import Counter

    if not pred_sig:
        return "empty"
    gold, pred = Counter(_letters(gold_sig)), Counter(_letters(pred_sig))
    extra, missing = pred - gold, gold - pred
    if extra and missing:
        return "ran on and cut short"
    if extra:
        return "ran on"
    if missing:
        return "cut short"
    return "inside"


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
    with run_options.using(ConversionOptions(tables=True, notation_memory=True)):
        lay = layout.analyze(pdf)
    item = pe.document_page_item(f"arxiv/{paper}", records, lay.elements, {}, [], [])
    gold = {r["id"]: r for r in records}
    paired = {p["gold_id"]: p for p in item["pairs"]}
    rows = []
    for r in records:
        p = paired.get(r["id"])
        sig = pe.math_signature(r["mathml"])
        row = {
            "id": r["id"],
            "display": r.get("display"),
            "in_float": bool(r.get("in_float")),
            "tokens": r.get("tokens", 0),
            "latex": r.get("latex", "")[:80],
            "found": p is not None,
        }
        if p is not None:
            pred_sig = pe.text_signature(p["pred_text"])
            row["sim"] = p["text_similarity"]
            row["pred"] = p["pred_text"][:120]
            row["pred_display"] = p["pred_display"]
            row["gold_sig"] = sig[:300]
            row["pred_sig"] = pred_sig[:300]
        rows.append(row)
    unmatched = [
        {
            "reason": u["reason"],
            "display": u["pred_display"],
            "text": u["pred_text"][:80],
            "piece_of": u["piece_of"],
        }
        for u in item["unmatched"]
    ]
    return {
        "paper": paper,
        "rows": rows,
        "unmatched": unmatched,
        "n_pred_inline": sum(e.display == "inline" for e in lay.elements if e.kind == "equation"),
        "gold_by_id": {g: gold[g].get("display") for g in gold},
    }


def summarise(papers: list[dict]) -> dict:
    rows = [r for p in papers for r in p["rows"]]
    inline = [r for r in rows if r["display"] == "inline"]
    found = [r for r in inline if r["found"]]
    wrong = [r for r in found if r["sim"] < FIDELITY]
    for r in wrong:
        r["edge"] = edge_class(r["gold_sig"], r["pred_sig"])
    edge = [r for r in wrong if r["edge"] not in ("inside", "empty")]
    by_len = {}
    for key in ("1", "2-3", "4+"):
        part = [r for r in inline if length_bucket(r["tokens"]) == key]
        by_len[key] = (sum(r["found"] for r in part), len(part))
    cells = [r for r in inline if r["in_float"]]
    unmatched = [u for p in papers for u in p["unmatched"] if u["display"] == "inline"]
    pred_inline = sum(p["n_pred_inline"] for p in papers)
    return {
        "gold_inline": len(inline),
        "found_inline": len(found),
        "span_wrong": len(wrong),
        "edge_wrong": len(edge),
        "span_edges": dict(Counter(r["edge"] for r in wrong)),
        "by_length": by_len,
        "float_found": (sum(r["found"] for r in cells), len(cells)),
        "pred_inline": pred_inline,
        "pred_inline_matched": len(found),
        "unmatched_by_reason": dict(Counter(u["reason"] for u in unmatched)),
        "exact_span": len(found) - len(wrong),
    }


def show(label: str, s: dict) -> None:
    g, f, w, e = s["gold_inline"], s["found_inline"], s["span_wrong"], s["edge_wrong"]
    print(
        f"{label}: inline gold {g}, found {f} (recall {f / g:.3f}), edge wrong {e} "
        f"({100 * e / g:.1f}% of gold, {100 * e / max(f, 1):.1f}% of found; text layer unlike "
        f"gold's {w}), "
        f"predicted inline {s['pred_inline']}, "
        f"precision {s['pred_inline_matched'] / max(s['pred_inline'], 1):.3f}"
    )
    print("  span edges:", dict(sorted(s["span_edges"].items())))
    print(
        "  found by gold length:",
        {k: f"{a}/{b}" for k, (a, b) in s["by_length"].items()},
        "in floats:",
        "{}/{}".format(*s["float_found"]),
    )
    print("  unaligned inline predictions by reason:", dict(s["unmatched_by_reason"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", default="dev2")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--examples", type=int, default=0)
    parser.add_argument("--from-json", type=Path, help="summarise an earlier --out file only")
    args = parser.parse_args()
    if args.from_json:
        show(args.from_json.stem, summarise(json.loads(args.from_json.read_text())["papers"]))
        return 0
    names = sorted(p.stem for p in (ROOT / "gold" / args.part / "pdfs").glob("*.pdf"))
    with ProcessPoolExecutor(args.workers) as pool:
        papers = list(pool.map(one, [(args.part, n) for n in names]))
    summary = summarise(papers)
    show(args.part, summary)
    if args.compare:
        before = json.loads(args.compare.read_text())
        show(args.part + " (before)", summarise(before["papers"]))
        old = {r["id"]: r for p in before["papers"] for r in p["rows"]}
        gained, lost, changed = [], [], []
        for p in papers:
            for r in p["rows"]:
                o = old.get(r["id"])
                if o is None:
                    continue
                if r["found"] and not o["found"]:
                    gained.append(r)
                elif o["found"] and not r["found"]:
                    lost.append(o)
                elif r["found"] and (r.get("pred") != o.get("pred")):
                    changed.append((o, r))
        print(
            f"gold equations: gained {len(gained)}, lost {len(lost)}, span changed {len(changed)}"
        )
        for name, group in (("gained", gained), ("lost", lost)):
            for r in group[: args.examples]:
                print(f"  {name}: {r['id']} gold={r['latex']!r} pred={r.get('pred')!r}")
        for o, r in changed[: args.examples]:
            print(
                f"  span: {r['id']} gold={r['latex']!r} {o.get('pred')!r} ({o['sim']}) -> "
                f"{r.get('pred')!r} ({r['sim']})"
            )
    if args.out:
        args.out.write_text(json.dumps({"part": args.part, "papers": papers}) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
