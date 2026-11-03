"""Write the gold file beside each beamer_*.tex fixture.

Usage:
    uv run python tests/fixtures/layout/latex/make_beamer_gold.py

The gold is read from the author's source, as for the arXiv gold: the equations come from
`readablestem.gold.arxiv_tex` (what the author typed, one entry per numbered row or aligned
group), and the frame titles come from the `\\begin{frame}{...}` lines. The number of PDF
pages each frame prints (more than one when overlays are used) is the only hand-written
part, in FRAME_PAGES, and the script checks it against the committed PDF so a rebuilt deck
cannot drift from its gold. Output: beamer_<name>.gold.json.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from pathlib import Path

import pymupdf

from readablestem.gold import arxiv_tex as tex

HERE = Path(__file__).resolve().parent

# Pages printed per frame, in frame order (overlay steps make a frame print several pages).
FRAME_PAGES: dict[str, list[int]] = {
    "beamer_basic": [1, 1, 1],
    "beamer_overlay": [3, 3],
    "beamer_columns_table": [1, 1],
    "beamer_theme_bars": [1, 1],
}
FRAME = re.compile(r"\\begin\{frame\}\{([^}]*)\}")


def gold_for(name: str) -> dict:
    source = (HERE / f"{name}.tex").read_text(encoding="utf-8")
    body = tex.body_of(tex.strip_comments(source))
    extraction = tex.extract(body)
    starts = [m.start() for m in re.finditer(r"\\begin\{frame\}", body)]
    offsets = {item.index: item.offset for item in extraction.items}
    equations = [
        {
            "latex": eq.latex,
            "display": eq.display,
            "env": eq.env,
            "frame": bisect_right(starts, offsets[eq.item]) - 1,
        }
        for eq in tex.equations_of(extraction.items)
    ]
    titles = FRAME.findall(source)
    pages = FRAME_PAGES[name]
    assert len(titles) == len(pages), name
    with pymupdf.open(HERE / f"{name}.pdf") as doc:
        assert doc.page_count == sum(pages), (name, doc.page_count, sum(pages))
    frames, first = [], 0
    for title, count in zip(titles, pages, strict=True):
        frames.append({"title": title, "pages": list(range(first, first + count))})
        first += count
    return {"source": f"{name}.tex", "frames": frames, "equations": equations}


def main() -> None:
    for name in FRAME_PAGES:
        target = HERE / f"{name}.gold.json"
        target.write_text(json.dumps(gold_for(name), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {target.name}")


if __name__ == "__main__":
    main()
