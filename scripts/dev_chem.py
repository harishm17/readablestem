# ruff: noqa: RUF001
"""Chemistry on a development set: what a document run found and how it reads, per source of
the gold, with a score that does not depend on how the gold writes a formula.

    uv run python scripts/dev_chem.py --gold data/dev-v2/dev LABEL=items.jsonl [LABEL=...]

Reads the per-page rows `eval pipeline --task document --items FILE` writes (development sets
only) and the set's gold, and prints one column per run, for the gold equations of the chemistry
sections, split into the OpenStax sections (MathML written by people; formulas are `mtext`,
charges are `mtext` or `mn`) and the authored document (MathML made by the same converter the
pipeline uses, one upright `mi` per letter). The primary score and the symbols scorer compare
the shape of the MathML, so a formula written another way is wrong in both, whatever it says;
`content` does not: it compares what is written, token text in order, after the spellings that
are one thing (a hyphen, a minus sign and an en dash; `->`, a long arrow and a short one; spaces,
invisible operators), and ignores which element holds it (`mi`, `mtext`, `mo`, `mn`), the
upright style, and how scripts are grouped.

- `aligned`: gold equations a predicted equation is aligned to (detection recall);
- `primary`: `exact_lenient`, `symbols`: `exact_symbols` (the repository's scorers);
- `content`: aligned and the same text;
- `symbols or content`: the share a reader would find right under either reading;
- `needs a decision`: of the aligned equations that are not `content`-right, the share the
  review screen asks a person about (a required decision): wrong and not asked is the harm.

Nothing here changes a scorer or a committed report; `scripts/dev_buckets.py` has the scorers'
own numbers.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from readablestem.eval.equations import MathMLError, normalize
from readablestem.pipeline.validate import safe_latex_to_mathml

_MINUS = str.maketrans({c: "-" for c in "−–—‐‑"})
_ARROWS = str.maketrans({c: ">" for c in "→⟶➔➜⟹⇒"})
_DOTS = str.maketrans({c: "*" for c in "·⋅∙•"})
_DEGREE = str.maketrans({"∘": "°"})
_DROP = re.compile(r"[\s⁡⁢⁣⁤​‌‍﻿]")
_TRAILING = ",.;:"


def content(source: str | ET.Element) -> str:
    """The text of a MathML tree, token by token, in the spellings `content` calls one thing."""
    root = normalize(source, lenient=True)  # annotations and spacing gone
    text = "".join(
        "".join(node.itertext())
        for node in root.iter()
        if node.tag in {"mi", "mn", "mo", "mtext", "tok"}
    )
    text = (
        unicodedata.normalize("NFKC", text)
        .translate(_MINUS)
        .translate(_ARROWS)
        .translate(_DOTS)
        .translate(_DEGREE)
    )
    text = _DROP.sub("", text)
    return text.rstrip(_TRAILING)


def load_gold(root: Path) -> dict[str, dict]:
    gold: dict[str, dict] = {}
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("kind") == "equation":
                gold[record["id"]] = record
    return gold


def rows_of(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def is_chemistry(gold_id: str) -> bool:
    return "chemistry" in gold_id  # the chemistry book and the authored chemistry document


def source_of(gold_id: str) -> str:
    return "authored" if gold_id.startswith("authored/") else "openstax"


def score(rows: list[dict], gold: dict[str, dict]) -> dict[str, Counter]:
    out: dict[str, Counter] = {"openstax": Counter(), "authored": Counter()}
    for row in rows:
        by_gold = {p["gold_id"]: p for p in row["pairs"]}
        for gold_id in [*by_gold, *row["missed_gold_ids"]]:
            if gold_id not in gold or not is_chemistry(gold_id):
                continue
            record = gold[gold_id]
            c = out[source_of(gold_id)]
            c["gold"] += 1
            pair = by_gold.get(gold_id)
            if pair is None:
                continue
            c["aligned"] += 1
            c["primary"] += bool(pair["exact_lenient"])
            c["symbols"] += bool(pair["exact_symbols"])
            right = False
            if pair.get("pred_latex"):
                try:
                    right = content(safe_latex_to_mathml(pair["pred_latex"], "inline")) == content(
                        record["mathml"]
                    )
                except (MathMLError, ValueError):
                    right = False
            c["content"] += right
            c["symbols_or_content"] += bool(pair["exact_symbols"] or right)
            if not right:
                c["not_content"] += 1
                c["not_content_asked"] += pair.get("review") == "required"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("runs", nargs="+", metavar="LABEL=items.jsonl")
    args = parser.parse_args()
    gold = load_gold(args.gold)
    results = {}
    for spec in args.runs:
        label, _, path = spec.partition("=")
        results[label] = score(rows_of(Path(path)), gold)
    labels = list(results)
    print(f"{'':44}" + "".join(f"| {label:>12} " for label in labels))
    for source in ("openstax", "authored"):
        print(f"-- {source}")
        for key in (
            "gold",
            "aligned",
            "primary",
            "symbols",
            "content",
            "symbols_or_content",
            "not_content",
            "not_content_asked",
        ):
            cells = []
            for label in labels:
                c = results[label][source]
                gold_n = c["gold"] or 1
                value = c[key]
                cells.append(
                    f"{value:>5} {value / gold_n:>6.1%}" if key != "gold" else f"{value:>12}"
                )
            print(f"{key:44}" + "".join(f"| {cell:>12} " for cell in cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
