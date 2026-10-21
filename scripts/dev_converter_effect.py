"""What a change of the LaTeX converter does to a recorded run's scores, with no model call.

    uv run python scripts/dev_converter_effect.py --gold DIR LABEL=items.jsonl [...]

The pipeline's MathML is made from the model's LaTeX by `validate.safe_latex_to_mathml`. When
that converter changes (2026-10-21: a script after `(...)` or `[...]` belongs to the group, a
charge hangs on its element symbol, a long arrow is an operator), the recorded answers are
unchanged and their MathML is not. This converts every aligned pair's recorded `pred_latex` with
the converter as it was (the three repairs off) and as it is, and scores both against the gold:

- `stored gold`: the gold's MathML as the gold file has it. For a gold made by people (OpenStax)
  that is the answer key. For a gold made *by this converter* (the authored documents, the arXiv
  set of the real-world evaluation: `gold/arxiv_gold.to_mathml`) it is the old converter's shape,
  so any converter change reads as a loss;
- `gold re-converted`: the same, with the gold's LaTeX converted by the same converter as the
  prediction (only for gold that has `latex`): the two sides of a comparison are made alike.

Development sets only. Primary `exact_lenient` and the symbols scorer's `exact`, over every gold
equation (an equation with no aligned pair is wrong in both).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from readablestem.eval import equations_symbols as sym
from readablestem.eval.equations import MathMLError, score_mathml
from readablestem.pipeline import validate


class _Converter:
    """`safe_latex_to_mathml` as it is, or with the 2026-10-21 repairs switched off."""

    def __init__(self, repaired: bool) -> None:
        self.repaired = repaired

    def __call__(self, latex: str) -> str:
        names = ("group_delimited_scripts", "group_element_symbols", "arrows_as_operators")
        saved = {n: getattr(validate, n) for n in names}
        try:
            if not self.repaired:
                validate.group_delimited_scripts = lambda s: s
                validate.group_element_symbols = lambda root: None
                validate.arrows_as_operators = lambda root: None
            return validate.safe_latex_to_mathml(latex, "inline")
        finally:
            for n, f in saved.items():
                setattr(validate, n, f)


def load_gold(root: Path) -> dict[str, dict]:
    gold: dict[str, dict] = {}
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("kind") == "equation":
                gold[record["id"]] = record
    return gold


def score(rows: list[dict], gold: dict[str, dict], convert: _Converter, reconvert: bool) -> dict:
    out = {"gold": 0, "primary": 0, "symbols": 0}
    for row in rows:
        out["gold"] += row["n_gold"]
        for pair in row["pairs"]:
            record = gold.get(pair["gold_id"])
            if record is None or not pair.get("pred_latex"):
                continue
            try:
                predicted = convert(pair["pred_latex"])
                answer = record["mathml"]
                if reconvert:
                    if not record.get("latex"):
                        return {}
                    answer = convert(record["latex"])
                out["primary"] += bool(score_mathml(predicted, answer).exact_lenient)
                out["symbols"] += bool(sym.score_symbols(predicted, answer).exact)
            except (MathMLError, validate.LatexConversionError):
                continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("runs", nargs="+", metavar="LABEL=items.jsonl")
    args = parser.parse_args()
    gold = load_gold(args.gold)
    has_latex = any(r.get("latex") for r in gold.values())
    old, new = _Converter(False), _Converter(True)
    for spec in args.runs:
        label, _, path = spec.partition("=")
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        print(f"== {label}")
        for title, reconvert in (("stored gold", False), ("gold re-converted", True)):
            if reconvert and not has_latex:
                continue
            a = score(rows, gold, old, reconvert)
            b = score(rows, gold, new, reconvert)
            if not a or not b:
                continue
            n = a["gold"] or 1
            for key in ("primary", "symbols"):
                print(
                    f"  {title:18} {key:8} {a[key] / n:7.4f} -> {b[key] / n:7.4f}  "
                    f"({a[key]} -> {b[key]} of {a['gold']}, "
                    f"{100 * (b[key] - a[key]) / n:+.2f} points)"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
