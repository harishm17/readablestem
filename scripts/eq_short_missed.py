"""Why short inline gold equations are not found by the layout alone, with no model and no cost.

    uv run python scripts/eq_short_missed.py PART [PAPER ...] [--tokens N] [--floats]

For the inline gold equations of a real-world part that `layout.analyze` does not find (aligned as
the document task does with no model answers), with at most `--tokens` symbols (3 by default) and
outside floats (`--floats` takes them too), finds where their text stands in the paragraph rows of
the PDF and says what the glyphs there are, so a rule for short formulas can be judged on evidence
before it is written:

- "inside a longer run": the layout found a formula there that holds the gold's text and more (a
  span edge, not a missed formula);
- "uncovered": no run covers it; then what the glyphs are: a maths-font glyph, an italic letter, a
  raised or lowered glyph, a number, a percent sign, or "plain text glyphs" (nothing on the page
  says the author set it in maths mode);
- "not in any paragraph row": a display, a caption, a heading or a cell, or text the layout never
  put in a paragraph.

Development parts only (docs/eval-equation-diagnostics.md, "Simple inline maths").
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"


def classify(window: list[int], row, runs, info) -> str:
    """What the glyphs at `window` (indexes into the row's characters) are."""
    covered = [any(a <= i < b for a, b, _ in runs) for i in window]
    chars = [row.chars[i] for i in window]
    if all(covered):
        return "inside a longer run"
    if any(covered):
        return "partly in a run"
    kinds = []
    if any(c.math for c in chars):
        kinds.append("math glyph")
    if any(c.italic and c.c.isalpha() for c in chars):
        kinds.append("italic letter")
    if all(c.c.isdigit() or c.c in ".,%" for c in chars):
        kinds.append("number")
    if any(c.c == "%" for c in chars):
        kinds.append("percent")
    if any(info.script[i] for i in window):
        kinds.append("script")
    return "uncovered: " + (", ".join(kinds) or "plain text glyphs")


def main() -> int:
    from readablestem.core.types import ConversionOptions
    from readablestem.eval import pipeline_eval as pe
    from readablestem.pipeline import layout
    from readablestem.pipeline import options as run_options

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("part")
    parser.add_argument("papers", nargs="*")
    parser.add_argument("--tokens", type=int, default=3)
    parser.add_argument("--floats", action="store_true")
    args = parser.parse_args()
    papers = sorted(p.stem for p in (ROOT / "gold" / args.part / "pdfs").glob("*.pdf"))
    original = layout._inline_runs
    seen: list = []

    def hook(row, info=None, kinds=None):
        runs = original(row, info, kinds)
        seen.append((row, runs, info or layout._CharInfo.of(row)))
        return runs

    layout._inline_runs = hook
    tally: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for paper in papers:
        if args.papers and paper not in args.papers:
            continue
        gold_path = ROOT / "gold" / args.part / f"{paper}.jsonl"
        records = [json.loads(line) for line in gold_path.read_text().splitlines()]
        records = [r for r in records if r.get("kind") == "equation"]
        pdf = (ROOT / "gold" / args.part / "pdfs" / f"{paper}.pdf").read_bytes()
        seen.clear()
        with run_options.using(ConversionOptions(tables=True, notation_memory=True)):
            found = layout.analyze(pdf)
        item = pe.document_page_item(f"arxiv/{paper}", records, found.elements, {}, [], [])
        missed = set(item["missed_gold_ids"])
        for record in records:
            wanted = (
                record["id"] in missed
                and record.get("display") == "inline"
                and record.get("tokens", 99) <= args.tokens
                and (args.floats or not record.get("in_float"))
            )
            signature = pe.math_signature(record["mathml"]) if wanted else ""
            if not signature:
                continue
            kind = "not in any paragraph row"
            for row, runs, info in seen:
                ink = [i for i, c in enumerate(row.chars) if not c.blank]
                pieces = [pe.text_signature(row.chars[i].c) for i in ink]
                at = "".join(pieces).find(signature)
                if at < 0:
                    continue
                window, position = [], 0
                for i, piece in zip(ink, pieces, strict=True):
                    if at <= position < at + len(signature):
                        window.append(i)
                    position += len(piece)
                if window:
                    kind = classify(window, row, runs, info)
                    break
            tally[kind] += 1
            examples.setdefault(kind, []).append(f"{paper} {record['latex'][:30]!r}")
    print(sum(tally.values()), "missed")
    for kind, n in tally.most_common():
        print(n, kind, "|", "; ".join(examples[kind][:4]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
