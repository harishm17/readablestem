"""Course notation as evidence in the style check: how many flags it adds, and how many
of them are false (docs/eval.md "Course notation").

`equation_step.text_layer_style(course_bold=...)` flags a letter the course writes bold
when the PDF's text layer shows no bold at all and the transcription has it plain. The
OpenStax gold is printed by Chromium, whose text layer shows every weight (math bold and
math italic code points), so this case never comes up there as printed. To measure it,
the stored transcriptions of an equations run (`eval pipeline --items`) are checked again
against their text layers twice: as printed, and with every style removed (what a PDF
whose font does not mark bold gives). A flag on a transcription the scorer counts right
(`exact_lenient`) is a false flag; on a wrong one, a true flag. Offline: no model calls.

    uv run python -m a11y_stem.eval.notation_flags ITEMS.jsonl --notation "bold d" ...
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from a11y_stem.core.mathchars import base_char
from a11y_stem.core.notation import canonical_symbol
from a11y_stem.core.types import NotationEntry
from a11y_stem.pipeline.equation_step import text_layer_style
from a11y_stem.pipeline.notation_memory import course_bold_letters

REPO = Path(__file__).resolve().parents[3]


def unstyled(text: str) -> str:
    """A text layer with every math style removed: a font that marks no weight."""
    return "".join(base_char(ch) for ch in text)


@dataclass
class FlagCounts:
    """Style flags ("meaning") on stored transcriptions, with and without the course."""

    items: int = 0
    wrong: int = 0  # not exact_lenient
    flagged_before: int = 0  # without the course notation
    flagged_after: int = 0  # with it
    new_true: int = 0  # new flags on wrong transcriptions
    new_false: int = 0  # new flags on transcriptions the scorer counts right

    def add(self, before: str | None, after: str | None, wrong: bool) -> None:
        self.items += 1
        self.wrong += wrong
        self.flagged_before += before == "meaning"
        self.flagged_after += after == "meaning"
        if after == "meaning" and before != "meaning":
            if wrong:
                self.new_true += 1
            else:
                self.new_false += 1


def count_flags(
    rows: Iterable[tuple[str | None, str, bool]], course_bold: frozenset[str], *, strip: bool
) -> FlagCounts:
    """`rows`: (text layer, proposed MathML, wrong). `strip`: remove the text layer's
    styles first."""
    counts = FlagCounts()
    for layer, mathml, wrong in rows:
        text = unstyled(layer) if (strip and layer) else layer
        before = text_layer_style(text, mathml)
        after = text_layer_style(text, mathml, course_bold=course_bold)
        counts.add(before, after, wrong)
    return counts


def load_rows(items_path: Path, gold_dir: Path, cache: Path) -> list[tuple[str | None, str, bool]]:
    """(text layer, proposal, wrong) for every item with a proposal. Text layers come
    from the eval's render cache (printing a missing one needs Chromium)."""
    from a11y_stem.eval.pipeline_eval import load_gold
    from a11y_stem.eval.render import render_equation_text

    gold = {r["id"]: r for r in load_gold(gold_dir) if r.get("kind") == "equation"}
    rows = []
    for line in items_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        mathml = item.get("pred_mathml")
        record = gold.get(item.get("id"))
        if not mathml or record is None:
            continue
        layer = render_equation_text(record["mathml"], cache / "equations")
        rows.append((layer, mathml, not item.get("exact_lenient")))
    return rows


def _entries(symbols: Sequence[str]) -> list[NotationEntry]:
    out = []
    for symbol in symbols:
        key = canonical_symbol(symbol)
        if key is None:
            raise SystemExit(f"not a symbol this tool understands: {symbol!r}")
        out.append(NotationEntry(symbol=symbol, canonical=key, meaning="vector"))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m a11y_stem.eval.notation_flags")
    parser.add_argument("items", type=Path, help="per-item JSONL of an equations run")
    parser.add_argument("--gold", type=Path, default=REPO / "data" / "gold")
    parser.add_argument("--cache", type=Path, default=REPO / ".cache" / "eval")
    parser.add_argument("--notation", nargs="+", required=True, help='e.g. "bold d"')
    args = parser.parse_args(argv)
    course_bold = course_bold_letters(_entries(args.notation))
    rows = load_rows(args.items, args.gold, args.cache)
    print(
        "| Text layer | Items | Wrong | Style flags without / with the course "
        "| New, on wrong | New, on right |"
    )
    print("|---|---|---|---|---|---|")
    for label, strip in (("as printed", False), ("styles removed", True)):
        c = count_flags(rows, course_bold, strip=strip)
        print(
            f"| {label} | {c.items} | {c.wrong} | {c.flagged_before} / {c.flagged_after} "
            f"| {c.new_true} | {c.new_false} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
