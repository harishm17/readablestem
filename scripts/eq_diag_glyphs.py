"""D3: do the equations a run missed, or cut wrongly, carry glyph evidence the rules ignore?
(docs/eval-equation-diagnostics.md)

    uv run python scripts/eq_diag_glyphs.py [--parts dev,fresh4] [--run fresh4=after]
        [--workers 6] [--json OUT.json]

For the gold equations of a recorded run that are

- undetected (no aligned prediction), by where the source typed them: running text, a table
  cell, a figure or algorithm;
- aligned with the symbols wrong only at an end (span wrong): the predicted span was cut short
  (the gold has more symbols) or ran on (the prediction has extra ones);
- detected and exactly right (the control),

finds the gold equation's characters in the PDF page by their text (a gold record has no
coordinates) and counts which glyph evidence they carry, from PyMuPDF's `rawdict`:

- a maths font: CMMI (maths italic, includes CMMIB), CMSY (symbols, includes CMBSY), CMEX
  (extension), MSBM (double-struck), or another font `parse.MATH_FONT` counts as a maths font;
- an italic text font (a variable set in the text family's italic: mathptmx, newtx, Times);
- a raised or lowered glyph: a script, set smaller than 85% of the page's body size and at least
  12% of that size above or below the baseline of the body text next to it (so a limit's
  subscript, which the text layer puts on a line of its own, counts).

A text found in several places of the page (a lone plus-or-minus sign) cannot be told apart, so
every place is looked at: the evidence is given as present at some place (upper bound) and at
every place (lower bound). The span-edge test takes the first place after the previous gold
equation.
Beside the rates, the background: the share of all other characters of the same pages carrying
each kind, so a kind that is common everywhere is not mistaken for evidence. For span-wrong
equations the evidence is looked at in the characters of the edge that is wrong: those the span
lacks (do they carry evidence a rule could have extended over?) or those it has too many (do
they carry none, so a rule could have trimmed them?). Reads recorded items and cached PDFs;
calls no model.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import eq_diag_common as common
import pymupdf

KINDS = common.EVIDENCE[1:]
ANY = common.ANY
MAX_PLACES = 40  # candidate places looked at for one equation
GROUPS = (
    "undetected, running text",
    "undetected, table cell",
    "undetected, figure or algorithm",
    "span wrong, cut short",
    "span wrong, ran on",
    "detected and right (control)",
)


def paper_task(task: dict) -> dict:
    """One paper: evidence of its target equations and the background of its pages."""
    doc = pymupdf.open(task["pdf"])
    by_page: dict[int, list[dict]] = defaultdict(list)
    for record in task["records"]:
        by_page[record["pdf_page_in_extract"]].append(record)
    rows: list[dict] = []
    background: Counter[str] = Counter()
    try:
        for number, records in by_page.items():
            wanted = [r for r in records if r["id"] in task["targets"]]
            if not wanted:
                continue
            chars = common.page_chars(doc[number - 1])
            stream = common.Stream.of(chars)
            frame = common.page_frame(chars)
            placed = common.locate_in_order(stream, records)
            used: set[int] = set()
            for _, _, chosen in placed.values():
                if chosen:
                    used.update(stream.owner[chosen[0] : chosen[1]])
            rest = [c for i, c in enumerate(chars) if i not in used]
            background["chars"] += len(rest)
            background.update(common.evidence_of(rest, frame))
            for record in wanted:
                cands, how, chosen = placed[record["id"]]
                target = task["targets"][record["id"]]
                row = {
                    "id": record["id"],
                    "group": target["group"],
                    "how": how,
                    "places": len(cands),
                    "located": chosen is not None,
                }
                if chosen is not None:
                    sets = [
                        set(common.evidence_of(stream.window(*c), frame))
                        for c in cands[:MAX_PLACES]
                    ]
                    row["possible"] = sorted(set.union(*sets))
                    row["certain"] = sorted(set.intersection(*sets))
                    if target.get("pred_text"):
                        row |= edge(stream, frame, chosen, target)
                rows.append(row)
    finally:
        doc.close()
    return {"rows": rows, "background": dict(background)}


def edge(stream: common.Stream, frame: common.Frame, gold: tuple[int, int], target: dict) -> dict:
    """The evidence in the characters of the wrong edge of a span-wrong prediction."""
    cands, _ = common.locate(stream, common.signature(target["pred_text"]))
    if not cands:
        return {"edge_located": False}
    pred = min(cands, key=lambda c: abs(c[0] - gold[0]) + abs(c[1] - gold[1]))
    if target["group"] == GROUPS[3]:  # cut short: what the gold has and the span does not
        lacking = [*range(gold[0], min(pred[0], gold[1])), *range(max(pred[1], gold[0]), gold[1])]
        chars = _chars(stream, lacking)
    else:  # ran on: what the span has and the gold does not
        extra = [*range(pred[0], min(gold[0], pred[1])), *range(max(gold[1], pred[0]), pred[1])]
        chars = _chars(stream, extra)
    return {
        "edge_located": True,
        "edge_chars": len(chars),
        "edge_evidence": sorted(common.evidence_of(chars, frame)) if chars else [],
    }


def _chars(stream: common.Stream, positions: list[int]) -> list[common.Ch]:
    seen = dict.fromkeys(stream.owner[i] for i in positions if 0 <= i < len(stream.owner))
    return [stream.chars[i] for i in seen]


def group_of(split: common.Split, gid: str) -> str:
    kind = split.kinds.get(gid, "text").split(">")[0]
    if kind == "table":
        return GROUPS[1]
    return GROUPS[0] if kind == "text" else GROUPS[2]


def targets_of(split: common.Split) -> dict[str, dict[str, dict]]:
    """paper id -> gold id -> {group, pred_text}."""
    buckets = common._load_script("dev_buckets")
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for item in split.items:
        paper = item["id"].split("/")[-1]
        for gid in item["missed_gold_ids"]:
            out[paper][gid] = {"group": group_of(split, gid)}
        for pair in item["pairs"]:
            record = split.gold[pair["gold_id"]]
            if pair["exact_lenient"]:
                out[paper][record["id"]] = {"group": GROUPS[5]}
            elif buckets.classify_pair(pair, record["mathml"]) == "span":
                ops = _span_ops(pair, record)
                if ops == {"delete"} and pair.get("pred_text"):
                    group = GROUPS[4]
                elif ops == {"insert"} and pair.get("pred_text"):
                    group = GROUPS[3]
                else:
                    continue  # cut at both ends: not one direction
                out[paper][record["id"]] = {"group": group, "pred_text": pair["pred_text"]}
    return out


def _span_ops(pair: dict, record: dict) -> set[str]:
    from readablestem.eval import equations_symbols as sym

    ops = common.difflib_ops(
        sym.symbols_of_latex(pair["pred_latex"]), sym.symbols_of(record["mathml"])
    )
    return {op[0] for op in ops}


def run_split(split: common.Split, workers: int) -> dict:
    targets = targets_of(split)
    by_paper: dict[str, list[dict]] = defaultdict(list)
    for record in split.gold.values():
        by_paper[record["page_slug"]].append(record)
    tasks = [
        {"pdf": str(split.pdfs / f"{paper}.pdf"), "records": by_paper[paper], "targets": tg}
        for paper, tg in targets.items()
    ]
    if workers > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(workers) as pool:
            parts = list(pool.map(paper_task, tasks))
    else:
        parts = [paper_task(t) for t in tasks]
    background: Counter[str] = Counter()
    for part in parts:
        background.update(part["background"])
    return {
        "part": split.part,
        "run": split.run,
        "rows": [r for part in parts for r in part["rows"]],
        "background": dict(background),
        "gold": len(split.gold),
    }


def has(row: dict, key: str, field: str) -> bool:
    return key in row.get(field, [])


def report(results: list[dict]) -> str:
    splits = ", ".join(f"{r['part']} ({r['run']})" for r in results)
    results = common.pooled(results)
    rows = [r for res in results for r in res["rows"]]
    out: list[str] = []
    n_gold = sum(r["gold"] for r in results)
    out.append(
        f"Gold equations in the splits: {n_gold}. Splits: "
        + splits
        + " ("
        + common.POOL_NOTE
        + ")"
        + ".\n"
    )
    headers = ["group", "n", "located", "unique place", "partial match"]
    body = []
    for g in GROUPS:
        sel = [r for r in rows if r["group"] == g]
        body.append(
            [
                g,
                len(sel),
                sum(r["located"] for r in sel),
                sum(bool(r["located"] and r["places"] == 1) for r in sel),
                sum(r.get("how") == "partial" for r in sel),
            ]
        )
    out.append(common.table(headers, body))
    for field, label in (
        ("possible", "at some place the text is found at"),
        ("certain", "at every place the text is found at"),
    ):
        out.append(
            f"\nShare of the located equations with at least one character carrying each "
            f"kind of evidence, {label} (a text found in several places of the page, such "
            "as a lone plus-or-minus sign, cannot be told apart, so both bounds are given):\n"
        )
        body = []
        for g in GROUPS:
            sel = [r for r in rows if r["group"] == g and r["located"]]
            body.append(
                [
                    g,
                    len(sel),
                    *(
                        common.pct(sum(has(r, k, field) for r in sel), len(sel))
                        for k in (ANY, *KINDS)
                    ),
                ]
            )
        out.append(common.table(["group", "n", *(ANY, *KINDS)], body))
    chars = sum(r["background"].get("chars", 0) for r in results)
    bg = Counter()
    for r in results:
        bg.update(r["background"])
    out.append(
        f"\nBackground: share of the {chars} characters of the same pages outside every "
        "gold equation carrying each kind (any evidence is the share with a maths font, "
        "an italic text font or a script offset):\n"
    )
    out.append(common.table([ANY, *KINDS], [[common.pct(bg[k], chars) for k in (ANY, *KINDS)]]))
    out.append("\nSpan wrong, in the characters of the wrong edge:\n")
    body = []
    for g in (GROUPS[3], GROUPS[4]):
        sel = [r for r in rows if r["group"] == g]
        edged = [r for r in sel if r.get("edge_located") and r.get("edge_chars")]
        if g == GROUPS[3]:
            ok = sum(ANY in r["edge_evidence"] for r in edged)
            what = "the missing characters carry maths-font, italic or script evidence"
        else:
            ok = sum(ANY not in r["edge_evidence"] for r in edged)
            what = "the extra characters carry no evidence at all"
        body.append([g, len(sel), len(edged), ok, common.pct(ok, len(edged)), what])
    out.append(common.table(["group", "n", "edge found", "yes", "share", "meaning of yes"], body))
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--parts", default=",".join(common.PARTS))
    parser.add_argument("--run", action="append", default=[], metavar="PART=NAME")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    runs = common.parse_runs(args.run)
    results = [
        run_split(common.load_split(part, runs[part]), args.workers)
        for part in args.parts.split(",")
    ]
    print(report(results))
    if args.json:
        args.json.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
