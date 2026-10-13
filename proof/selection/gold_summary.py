"""Held-out gold summary (gold only; no system output exists or is read).

    uv run python gold_summary.py GOLD_DIR PDF_DIR OUT.json

Per section: equations (block, inline), equations matching the known scorer-gap pattern
G1, figures (with a non-empty OpenStax alt), tables, headings (and how many sit in
boxes), printed Letter pages. G1 = a gold <mtext> token holding an operator or bracket
character next to other characters (OpenStax packs operators into text, e.g.
"time =", "m/s)(46 s)"); latex2mathml writes those as text plus <mo>, so a correct
transcription can still fail exact match. Prints counts only.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pymupdf

from a11y_stem.eval.gold_scoring import iter_gold_records

OPS = set("=+−-×·÷/()[]<>≤≥≈±")


def g1(mathml: str) -> bool:
    try:
        root = ET.fromstring(mathml)
    except ET.ParseError:
        return False
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "mtext":
            text = "".join(el.itertext()).strip()
            if len(text) > 1 and any(c in OPS for c in text):
                return True
    return False


def main() -> int:
    gold, pdfs, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    rows: dict[str, dict] = {}
    for r in iter_gold_records(gold):
        key = f"{r['book_slug']}/{r['page_slug']}"
        row = rows.setdefault(key, {"equations": 0, "block": 0, "inline": 0, "g1": 0,
                                    "figures": 0, "figures_with_alt": 0, "tables": 0,
                                    "headings": 0, "headings_in_boxes": 0})
        kind = r["kind"]
        if kind == "equation":
            row["equations"] += 1
            row["block" if r.get("display") == "block" else "inline"] += 1
            row["g1"] += g1(r["mathml"])
        elif kind == "figure":
            row["figures"] += 1
            alts = [i.get("alt") or "" for i in r.get("images", [])]
            row["figures_with_alt"] += any(a.strip() for a in alts)
        elif kind == "table":
            row["tables"] += 1
        elif kind == "heading":
            row["headings"] += 1
            row["headings_in_boxes"] += r.get("context") is not None
    for key, row in rows.items():
        book, slug = key.split("/", 1)
        with pymupdf.open(pdfs / f"{book}--{slug}.pdf") as doc:
            row["pdf_pages"] = doc.page_count
    totals = {k: sum(r[k] for r in rows.values()) for k in next(iter(rows.values()))}
    json.dump({"sections": rows, "totals": totals}, open(out, "w"), indent=1)
    for key, row in rows.items():
        print(key, row)
    print("TOTAL", totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
