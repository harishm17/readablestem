# ruff: noqa: RUF001
# (look-alike symbols are what this module sorts)
"""D4: why were silent "other symbols" errors not flagged? (docs/eval-equation-diagnostics.md)

    uv run python scripts/eq_diag_unflagged.py [--parts dev,fresh4] [--run fresh4=after]
        [--json OUT.json]

A silent error is an aligned equation that is wrong (not `exact_lenient`) and that no required
decision asks about (`review` is not `required`). Those whose symbols differ from the gold's
(the letters and digits, as in `scripts/rw_gap_table.py`) are the "other symbols" errors. For
each, the check that should have caught it, `text_layer_compare` and
`equation_step.text_layer_symbols_differ` (the symbol bag of the span's text layer against the
transcription's), is replayed on the recorded span text (`pred_text`, at most 300 characters) and
the recorded MathML, and the first reason that fits is given:

1. no text layer: the span has no text (a picture, or nothing found);
2. no Unicode: the text layer is private-use code points, replacement characters or `(cid:N)`
   glyph references, or has no letter, digit or Greek letter at all (the bag is empty and the
   check declines);
3. the bag differs and the check fires on replay: it should have flagged it; the review level
   actually recorded (and whether a symbol-mismatch reason was recorded) says where it went;
4. the bag agrees with the transcription, and the symbols differ only in style (the same letters
   bold or plain, script or not, upright text or italic: notation the bag cannot see);
5. the bag agrees, and the wrong symbols are ones the bag does not count (brackets, primes and
   accents, arrows, relations, operators, structure such as scripts and fractions): which kind;
6. the bag agrees and the wrong symbols are letters or digits: the text layer of the span itself
   lacks (or shares) the error, so the span is wrong at its edge or the text layer is.

Reads recorded items only; calls no model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import eq_diag_common as common

from readablestem.eval import equations_symbols as sym
from readablestem.pipeline import equation_step as es
from readablestem.pipeline import validate

REASONS = (
    "no text layer",
    "no Unicode (private-use, cid or no letter, digit or Greek)",
    "bag differs and the check fires on replay",
    "bag agrees; same letters in another style (bold, script, upright): notation",
    "bag agrees; wrong symbols are brackets",
    "bag agrees; wrong symbols are primes or accents",
    "bag agrees; wrong symbols are arrows or relations",
    "bag agrees; wrong symbols are operators or punctuation",
    "bag agrees; wrong symbols are structure (scripts, fractions, groups)",
    "bag agrees; wrong symbols are letters or digits (the text layer shares the error)",
    "bag agrees; other symbols",
)
_CID = re.compile(r"\(cid:\d+\)")
_STRUCTURE = re.compile(r"^(<.*>|[_^])$")
_BRACKETS = set("()[]{}⟨⟩⌈⌉⌊⌋|‖")
_PRIMES = set("′″‴'`´ˆˇ˜¯˙¨")
_RELATIONS = set("=<>≤≥≠≈≡∼≃≅∝→←↔↦⇒⇐⇔⟶⟵⊂⊃⊆⊇∈∉")
_OPERATORS = set("+-−±∓×·⋅∗∘÷/∂∇∑∏∫√,.;:!?%°")


def symbol_bag_ignores(token: str) -> str:
    """Which kind of symbol the bag (letters, digits, Greek letters) does not count `token` is."""
    if _STRUCTURE.match(token):
        return "structure"
    if token in _BRACKETS:
        return "brackets"
    if token in _PRIMES:
        return "primes"
    if token in _RELATIONS:
        return "relations"
    if token in _OPERATORS:
        return "operators"
    if all(ch.isalnum() and unicodedata.category(ch) != "Lm" for ch in token):
        return "alnum"
    return "other"


def no_unicode(text: str) -> bool:
    private = any(unicodedata.category(ch) in ("Co", "Cn", "Cs") or ch == "�" for ch in text)
    return private or bool(_CID.search(text)) or not es._symbol_bag(text)


_STYLE_PREFIX = re.compile(r"^[a-z][a-z-]*:(?=.)")


def _base(token: str) -> str:
    """A token without its style prefix (`bold:u`, `script:E`), as a folded base letter."""
    return unicodedata.normalize("NFKC", _STYLE_PREFIX.sub("", token))


def _style(token: str) -> str:
    match = re.match(r"^([a-z][a-z-]*):(?=.)", token)
    if match:
        return match.group(1)
    return "styled glyph" if unicodedata.normalize("NFKC", token) != token else "plain"


def style_pairs(pred_latex: str, gold_mathml: str) -> list[str]:
    """The (prediction's style -> gold's style) of each token that differs in style only."""
    a, b = sym.symbols_of_latex(pred_latex), sym.symbols_of(gold_mathml)
    return sorted({f"{_style(x)} -> {_style(y)}" for x, y in zip(a, b, strict=False) if x != y})


def wrong_kind(pred_latex: str, gold_mathml: str) -> str:
    """The kind of symbol the bag does not count that differs (the first rule that fits over the
    differing tokens of both sides)."""
    a, b = sym.symbols_of_latex(pred_latex), sym.symbols_of(gold_mathml)
    if [_base(t) for t in a] == [_base(t) for t in b]:
        return "style"
    a, b = [_base(t) for t in a], [_base(t) for t in b]
    tokens = [t for op in common.difflib_ops(a, b) for t in (*a[op[1] : op[2]], *b[op[3] : op[4]])]
    kinds = Counter(symbol_bag_ignores(t) for t in tokens)
    if kinds["alnum"]:
        return "alnum"
    for kind in ("brackets", "primes", "relations", "operators", "structure"):
        if kinds[kind]:
            return kind
    return "other"


_BY_KIND = {
    "style": REASONS[3],
    "brackets": REASONS[4],
    "primes": REASONS[5],
    "relations": REASONS[6],
    "operators": REASONS[7],
    "structure": REASONS[8],
    "alnum": REASONS[9],
    "other": REASONS[10],
}


def reason_for(pair: dict, gold_mathml: str) -> str:
    text = pair.get("pred_text") or ""
    if not text.strip():
        return REASONS[0]
    if no_unicode(text):
        return REASONS[1]
    mathml = pair.get("pred_mathml") or validate.safe_latex_to_mathml(
        es.clean_latex(pair["pred_latex"]), pair.get("pred_display") or "inline"
    )  # runs recorded before the items kept the MathML
    differ = es.text_layer_symbols_differ(text, mathml)
    if differ:
        return REASONS[2]
    return _BY_KIND[wrong_kind(pair["pred_latex"], gold_mathml)]


def silent_other_symbols(split: common.Split) -> tuple[list[dict], Counter]:
    """The silent errors with other symbols (rows), and every silent error by bucket."""
    buckets = common._load_script("dev_buckets")
    rows: list[dict] = []
    classes: Counter[str] = Counter()
    for item in split.items:
        for pair in item["pairs"]:
            if pair["exact_lenient"] or pair.get("review") == "required":
                continue
            record = split.gold[pair["gold_id"]]
            cls = buckets.classify_pair(pair, record["mathml"])
            if cls == "recognition":
                same = Counter(sym.symbols_of_latex(pair["pred_latex"])) == Counter(
                    sym.symbols_of(record["mathml"])
                )
                cls = (
                    "recognition, same symbols in another arrangement" if same else "other symbols"
                )
            classes[cls] += 1
            if cls != "other symbols":
                continue
            row_reason = reason_for(pair, record["mathml"])
            rows.append(
                {
                    "id": pair["gold_id"],
                    "reason": row_reason,
                    "review": pair.get("review"),
                    "recorded_reason": "symbol_mismatch"
                    in " ".join(pair.get("tier_reasons") or []),
                    "length": common.length_class(record),
                    "style_pairs": style_pairs(pair["pred_latex"], record["mathml"])
                    if row_reason == REASONS[3]
                    else [],
                }
            )
    return rows, classes


def run_split(split: common.Split) -> dict:
    rows, classes = silent_other_symbols(split)
    return {
        "part": split.part,
        "run": split.run,
        "gold": len(split.gold),
        "silent": sum(classes.values()),
        "silent_by_bucket": dict(classes),
        "rows": rows,
    }


def report(results: list[dict]) -> str:
    out: list[str] = []
    pool = common.pooled(results)
    headers = [
        "split (run)",
        "gold",
        "silent errors",
        "other symbols",
        *(f"R{i + 1}" for i in range(len(REASONS))),
    ]
    body = []
    total: Counter[str] = Counter()
    for r in results:
        by = Counter(row["reason"] for row in r["rows"])
        if r in pool:
            total.update(by)
            total["gold"] += r["gold"]
            total["silent"] += r["silent"]
            total["other"] += len(r["rows"])
        body.append(
            [
                f"{r['part']} ({r['run']})",
                r["gold"],
                r["silent"],
                len(r["rows"]),
                *(by[x] for x in REASONS),
            ]
        )
    body.append(
        [
            "all (pooled)",
            total["gold"],
            total["silent"],
            total["other"],
            *(total[x] for x in REASONS),
        ]
    )
    out.append(
        "Silent errors (wrong, no required decision) with other symbols, and the first "
        "reason each was not flagged (R1 to R11 as numbered below).\n"
    )
    out.append(common.table(headers, body))
    out.append("")
    out.append(
        common.table(
            ["", "reason", "n", "share"],
            [
                [f"R{i + 1}", x, total[x], common.pct(total[x], total["other"])]
                for i, x in enumerate(REASONS)
            ],
        )
    )
    rows = [row for r in pool for row in r["rows"]]
    fires = [row for row in rows if row["reason"] == REASONS[2]]
    out.append(
        "\nWhere the replayed check fires but nothing was required (n = "
        f"{len(fires)}): recorded review level "
        + (
            ", ".join(f"{k} {v}" for k, v in Counter(r["review"] for r in fires).most_common())
            or "none"
        )
        + f"; a symbol_mismatch reason recorded for {sum(r['recorded_reason'] for r in fires)}."
    )
    styles = Counter(p for r in rows for p in r["style_pairs"])
    out.append(
        "\nStyle-only differences (R4), prediction's style -> gold's style, per token kind "
        "(an equation may have several): "
        + ", ".join(f"{k} {v}" for k, v in styles.most_common(8))
        + "."
    )
    every: Counter[str] = Counter()
    for r in pool:
        every.update(r["silent_by_bucket"])
    out.append(
        "\nEvery silent error by bucket, all splits: "
        + ", ".join(f"{k} {v}" for k, v in every.most_common())
        + "."
    )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--parts", default=",".join(common.PARTS))
    parser.add_argument("--run", action="append", default=[], metavar="PART=NAME")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    runs = common.parse_runs(args.run)
    results = [run_split(common.load_split(part, runs[part])) for part in args.parts.split(",")]
    print(report(results))
    if args.json:
        args.json.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
