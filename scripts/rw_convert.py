"""Real-world set: convert one paper again from recorded answers and show what came out.

    uv run python scripts/rw_convert.py --part dev2 --paper 2404.05746 --answers TAG [--live]
        [--tables] [--notation-memory] [--html OUT.html] [--grids]

Reads the cut PDF of the paper, converts it with the production settings (provider anthropic, the
tables and notation-memory flags as given) behind the answers file of the run `TAG`
(`runs/PART/TAG/answers.jsonl`): a request already answered replays for free, and one that is not is
refused (no spend) unless `--live`. `--html` writes the page; `--grids` prints every rebuilt table's
cells (text, LaTeX when the cell is maths) and the equations the layout found. A debugging tool for
the cells of a table and the spans of an equation, development parts only.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", default="dev2")
    parser.add_argument("--paper", required=True)
    parser.add_argument("--answers", required=True, help="tag of the run whose answers to replay")
    parser.add_argument("--live", action="store_true", help="ask the model for a missing answer")
    parser.add_argument("--tables", action="store_true")
    parser.add_argument("--notation-memory", action="store_true")
    parser.add_argument("--html", type=Path)
    parser.add_argument("--grids", action="store_true")
    args = parser.parse_args()
    os.environ["CLAUDE_SECOND_CHECK"] = "off"
    if args.tables:
        os.environ["FEATURE_TABLES"] = "true"
    if args.notation_memory:
        os.environ["FEATURE_NOTATION_MEMORY"] = "true"
    from readablestem.config import get_settings, with_provider
    from readablestem.core.types import Kind
    from readablestem.eval.answers import CachedClient
    from readablestem.models import make_live_client
    from readablestem.pipeline import run

    settings = with_provider(get_settings(), "anthropic")
    path = ROOT / "runs" / args.part / args.answers / "answers.jsonl"
    inner = make_live_client(settings) if args.live else None
    client = CachedClient(inner, path, settings)
    pdf = (ROOT / "gold" / args.part / "pdfs" / f"{args.paper}.pdf").read_bytes()
    output = run.convert_pdf(pdf, settings=settings, client=client, concurrency=3)
    result = output.result
    if args.html:
        args.html.write_text(result.html, encoding="utf-8")
    fixes = {f.element_id: f for f in result.fixes}
    for element in result.elements:
        fix = fixes.get(element.id)
        if element.kind == Kind.TABLE and args.grids and fix is not None and fix.table is not None:
            grid = fix.table.grid
            print(f"--- table {element.id} page {element.page + 1} status {fix.status.value}")
            for cell in sorted(grid.cells, key=lambda c: (c.row, c.col)):
                maths = f"   LaTeX: {cell.latex}" if cell.latex else ""
                print(f"  r{cell.row} c{cell.col}: {cell.text!r}{maths}")
    print(f"hits {client.hits} misses {client.misses}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
