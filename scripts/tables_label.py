"""Propose table labels for an arXiv paper from its LaTeX source (docs/tables.md, "Real documents").

    uv run python scripts/tables_label.py SRC_DIR PDF [--pages 1,2,3] [--overlay DIR]

Reads the `tabular` environments of the source (`gold/tabulars.py`), places each on a page of
the PDF by its words, and prints one JSON object `{"tables": [{"page", "bbox", ...}]}`: page
indexes are 0-based in the given PDF. With `--overlay DIR` each labelled page is drawn with its
boxes for a check by eye. The boxes are proposals: a person looks at every one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

from readablestem.gold import arxiv_tex as tex
from readablestem.gold import tabulars as tab


def label(src: Path, pdf: Path, only: set[int] | None = None) -> tuple[list[dict], list[dict]]:
    sources = tex.read_sources(src)
    main = tex.find_main(sources)
    if main is None:
        return [], [{"error": "no main tex"}]
    table = tex.MacroTable()
    flat = table.expand(table.read(tex.flatten(sources, main)))
    found = [t for t in tab.find_tabulars(tex.body_of(flat)) if tab.is_data_table(t)]
    doc = pymupdf.open(pdf)
    pages = []
    for number, page in enumerate(doc):
        if only is not None and number not in only:
            pages.append([])
            continue
        pages.append([(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")])
    placed, lost = [], []
    for t in found:
        where = tab.locate(t, pages)
        info = {"env": t.env, "cols": t.filled_columns, "rows": len(t.rows), "words": len(t.tokens)}
        if where is None:
            lost.append(info)
        else:
            placed.append(
                {
                    "page": where.page,
                    "bbox": [round(v, 1) for v in where.bbox],
                    "matched": round(where.matched, 2),
                    **info,
                }
            )
    return placed, lost


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", help="0-based pages to look at, comma separated")
    ap.add_argument("--overlay", type=Path)
    args = ap.parse_args()
    only = {int(p) for p in args.pages.split(",")} if args.pages else None
    placed, lost = label(args.src, args.pdf, only)
    json.dump({"tables": placed, "not_placed": len(lost)}, sys.stdout)
    sys.stdout.write("\n")
    if args.overlay and placed:
        args.overlay.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open(args.pdf)
        for number in sorted({t["page"] for t in placed}):
            page = doc[number]
            for t in placed:
                if t["page"] == number:
                    page.draw_rect(pymupdf.Rect(*t["bbox"]), color=(1, 0, 0), width=1)
            page.get_pixmap(dpi=70).save(args.overlay / f"{args.pdf.stem}_p{number}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
