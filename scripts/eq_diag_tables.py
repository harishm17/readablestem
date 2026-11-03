"""D1: why maths typed in a table cell is missed (docs/eval-equation-diagnostics.md).

    uv run python scripts/eq_diag_tables.py [--parts dev,fresh4] [--run fresh4=after]
        [--workers 6] [--json OUT.json]

For every gold equation the source types in a table (`float kinds`) and a recorded run did not
align to a prediction (`missed_gold_ids`, reading B: the maths cells of a rebuilt table count),
gives the first cause that fits:

1. predicted, order misaligned: the run did predict this formula (an unmatched prediction with
   its text) but not at the place the gold's order expects: a float printed away from where the
   source typed it;
2. not in the text layer: under half of its text is found in any line of the page (set as an
   image, or in a font with no Unicode); a text found in part (a drawn floor bracket, a missing
   star or hat) is located, and reported as a partial match;
3. rotated: the page is rotated or the line it is in does not run left to right (a sideways table);
4. table not detected: its text is in the page but no table region the layout finds holds it
   (`layout.analyze` with tables on, no model), and what does hold it (a paragraph, a figure);
5. table detected, no maths cell read: a table holds it, and no cell of that table became an
   equation in the recorded run;
6. table detected, other cells read: the same table has maths cells in the recorded run, this
   one stayed text.

A gold record has no coordinates, so it is found by its text (`eq_diag_common.locate`): where
several places in the page have the same text, a place inside a table region is taken (the source
typed it in a table). The number of places is reported. Reads recorded items, cached PDFs and the
layout's own rules; calls no model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import eq_diag_common as common
import pymupdf

from readablestem.eval import pipeline_eval as pe

CAUSES = (
    "predicted, order misaligned",
    "not in the text layer",
    "rotated",
    "table not detected",
    "table detected, no maths cell read",
    "table detected, other cells read",
)
_CELL_ID = re.compile(r"^(?P<table>p\d+-e\d+)-m\d+$")


def is_table(kind: str) -> bool:
    return "table" in kind.split(">")[0]


def layout_regions(
    pdf_path: str,
) -> dict[int, list[tuple[str, str, tuple[float, float, float, float]]]]:
    """Page number (1-based) -> (element id, kind, bbox) of the layout's elements, tables on,
    no model. Run in a worker process."""
    from readablestem.core.types import ConversionOptions
    from readablestem.pipeline import layout
    from readablestem.pipeline import options as run_options

    with run_options.using(ConversionOptions(tables=True, notation_memory=True)):
        lay = layout.analyze(Path(pdf_path).read_bytes())
    out: dict[int, list] = defaultdict(list)
    for element in lay.elements:
        out[element.page + 1].append((element.id, element.kind.value, tuple(element.bbox)))
    return dict(out)


def _inside(ch: common.Ch, bbox: tuple[float, float, float, float], slack: float = 2.0) -> bool:
    cx, cy = (ch.bbox[0] + ch.bbox[2]) / 2, (ch.bbox[1] + ch.bbox[3]) / 2
    return bbox[0] - slack <= cx <= bbox[2] + slack and bbox[1] - slack <= cy <= bbox[3] + slack


def _horizontal(ch: common.Ch) -> bool:
    return abs(ch.direction[0] - 1.0) < 0.1 and abs(ch.direction[1]) < 0.1


def cell_tables(item: dict) -> set[str]:
    """Ids of the tables that have a maths cell as an equation in the recorded item."""
    ids = {p["element_id"] for p in item["pairs"]} | {u["element_id"] for u in item["unmatched"]}
    return {m["table"] for i in ids if (m := _CELL_ID.match(i))}


def diagnose(
    split: common.Split, item: dict, regions: dict, gid: str, doc: pymupdf.Document, streams: dict
) -> dict:
    record = split.gold[gid]
    sig = common.gold_signature(record)
    out = {
        "id": gid,
        "paper": record["page_slug"],
        "length": common.length_class(record),
        "latex": record["latex"][:40],
    }
    best = max(
        (pe.similarity(sig, common.signature(u.get("pred_text") or "")) for u in item["unmatched"]),
        default=0.0,
    )
    if best >= pe.EQUATION_ALIGN_MIN:
        return out | {"cause": CAUSES[0], "detected": None, "places": None}
    number = record["pdf_page_in_extract"]
    if number not in streams:
        streams[number] = common.Stream.of(common.page_chars(doc[number - 1]))
    stream = streams[number]
    cands, how = common.locate(stream, sig)
    if not cands:
        return out | {"cause": CAUSES[1], "detected": None, "places": 0}
    tables = [(i, b) for i, k, b in regions.get(number, []) if k == "table"]

    def table_of(span: tuple[int, int]) -> str | None:
        chars = stream.window(*span)
        for table_id, bbox in tables:
            if chars and sum(_inside(c, bbox) for c in chars) * 2 >= len(chars):
                return table_id
        return None

    inside = [c for c in cands if table_of(c)]
    span = (inside or cands)[0]
    table_id = table_of(span)
    chars = stream.window(*span)
    sideways = doc[number - 1].rotation % 360 != 0 or not all(_horizontal(c) for c in chars)
    base = out | {"places": len(cands), "match": how, "detected": table_id is not None}
    if sideways:
        return base | {"cause": CAUSES[2]}
    if table_id is None:
        holders = Counter(
            k
            for _, k, b in regions.get(number, [])
            if k != "table" and chars and sum(_inside(c, b) for c in chars) * 2 >= len(chars)
        )
        return base | {
            "cause": CAUSES[3],
            "held_by": holders.most_common(1)[0][0] if holders else "no element",
        }
    return base | {
        "cause": CAUSES[5] if table_id in cell_tables(item) else CAUSES[4],
        "table": table_id,
    }


def run_split(split: common.Split, workers: int) -> dict:
    gold_in_tables = {g for g, k in split.kinds.items() if is_table(k)}
    papers = {
        i["id"]: i
        for i in split.items
        if gold_in_tables & {*i["missed_gold_ids"], *(p["gold_id"] for p in i["pairs"])}
    }
    missed = {
        i["id"]: [g for g in i["missed_gold_ids"] if g in gold_in_tables] for i in papers.values()
    }
    todo = {pid: split.pdfs / f"{pid.split('/')[-1]}.pdf" for pid, ids in missed.items() if ids}
    if workers > 1 and len(todo) > 1:
        with ProcessPoolExecutor(workers) as pool:
            layouts = dict(
                zip(todo, pool.map(layout_regions, map(str, todo.values())), strict=True)
            )
    else:
        layouts = {pid: layout_regions(str(path)) for pid, path in todo.items()}
    rows: list[dict] = []
    validated = [0, 0]
    for pid, ids in missed.items():
        if not ids:
            continue
        item = papers[pid]
        for table_id in cell_tables(item):  # does the replayed layout have the recorded tables?
            validated[1] += 1
            validated[0] += any(
                i == table_id and k == "table" for r in layouts[pid].values() for i, k, _ in r
            )
        doc = pymupdf.open(todo[pid])
        streams: dict = {}
        try:
            rows += [diagnose(split, item, layouts[pid], g, doc, streams) for g in ids]
        finally:
            doc.close()
    n_gold = len(gold_in_tables & set(split.gold))
    aligned = {p["gold_id"]: p for i in split.items for p in i["pairs"]}
    return {
        "part": split.part,
        "run": split.run,
        "gold": len(split.gold),
        "table_gold": n_gold,
        "table_detected": sum(g in aligned for g in gold_in_tables),
        "table_exact": sum(bool(aligned.get(g, {}).get("exact_lenient")) for g in gold_in_tables),
        "table_missed": len(rows),
        "replayed_tables_found": validated,
        "rows": rows,
    }


def report(results: list[dict]) -> str:
    out: list[str] = []
    pool = common.pooled(results)
    headers = ["split (run)", "gold", "table gold", "aligned", "exact", "missed", *CAUSES]
    body, total = [], Counter()
    for r in results:
        by = Counter(row["cause"] for row in r["rows"])
        if r in pool:
            total.update(by)
            for key in ("gold", "table_gold", "table_detected", "table_exact", "table_missed"):
                total[key] += r[key]
        body.append(
            [
                f"{r['part']} ({r['run']})",
                r["gold"],
                r["table_gold"],
                r["table_detected"],
                r["table_exact"],
                r["table_missed"],
                *(by[c] for c in CAUSES),
            ]
        )
    body.append(
        [
            "all (pooled)",
            total["gold"],
            total["table_gold"],
            total["table_detected"],
            total["table_exact"],
            total["table_missed"],
            *(total[c] for c in CAUSES),
        ]
    )
    out.append(
        "Table-typed gold equations and why the missed ones were missed (reading B); "
        + common.POOL_NOTE
        + ".\n"
    )
    out.append(common.table(headers, body))
    rows = [row for r in pool for row in r["rows"]]
    out.append(f"\nShare of the missed table-typed equations, all splits (n = {len(rows)}):\n")
    out.append(
        common.table(
            ["cause", "n", "share"],
            [[c, total[c], common.pct(total[c], len(rows))] for c in CAUSES],
        )
    )
    held = Counter(row.get("held_by") for row in rows if row["cause"] == CAUSES[3])
    out.append(
        "\nTable not detected: what holds the text instead: "
        + ", ".join(f"{k} {v}" for k, v in held.most_common())
        + "\n"
    )
    out.append("Missed table-typed equations by length:\n")
    lengths = ["bare number", "1 symbol", "2-3 symbols", "longer", "display"]
    out.append(
        common.table(
            ["cause", *lengths],
            [
                [
                    c,
                    *(
                        sum(1 for r in rows if r["cause"] == c and r["length"] == n)
                        for n in lengths
                    ),
                ]
                for c in CAUSES
            ],
        )
    )
    how = Counter(r.get("match") for r in rows if r.get("match"))
    out.append(
        "\nHow the text was found in the page (all located): "
        + ", ".join(f"{k} {v}" for k, v in how.most_common())
        + "."
    )
    ambiguous = sum(1 for r in rows if (r.get("places") or 0) > 1)
    located = sum(1 for r in rows if r.get("places"))
    out.append(
        f"\nPlaces in the page with the gold's text (located: {located}): more than one for "
        f"{ambiguous}.\n"
    )
    contents = Counter(r["latex"] for r in rows)
    out.append(
        "Most common gold LaTeX among them: "
        + ", ".join(f"`{k}` {v}" for k, v in contents.most_common(6))
        + ".\n"
    )
    papers = Counter(r["paper"] for r in rows)
    out.append(
        "Papers with the most missed table-typed equations: "
        + ", ".join(f"{p} {n}" for p, n in papers.most_common(5))
        + "\n"
    )
    found = [sum(r["replayed_tables_found"][i] for r in pool) for i in (0, 1)]
    out.append(
        f"Recorded tables with a maths cell found again as table elements by the replayed "
        f"layout: {found[0]} of {found[1]}."
    )
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
