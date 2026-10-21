"""Compare document-task runs on a development set: headline numbers and the bucket breakdown.

    uv run python scripts/dev_buckets.py --gold data/dev-v2/dev LABEL=items.jsonl [LABEL=...]

Reads the per-page rows `eval pipeline --task document --items FILE` writes (development
sets only: the held-out set is not for this) and the set's gold, and prints one column per run:

- primary `exact_lenient` end to end (unchanged, the headline) and the secondary scorers
  (stream v2, symbols, with split and merge credit);
- detection recall and precision, span fidelity (the predicted span's own text layer within
  `FIDELITY` of the gold equation's), unmatched predictions by reason and how many the PDF's
  text does not support (invented or unsupported);
- review load, equation flag precision and recall, and the wrong equations nobody is asked
  about, per 100 gold equations;
- cost per PDF page, split into the page pass, the cheap model's reads, the main model's first
  reads and its escalations (figures apart);
- the buckets of the 2026-10-20 analysis, per gold equation: correct, scorer (the symbols are
  right, the primary scorer says no), undetected (no aligned prediction; credited split or
  merged are kept apart), span (aligned, and the symbols differ only at an end: the span was cut
  or extended), recognition (aligned, and the symbols differ inside), no proposal.
"""

from __future__ import annotations

import argparse
import difflib
import json
from collections import Counter
from pathlib import Path

from readablestem.eval import equations_symbols as sym
from readablestem.eval import pipeline_eval as pe
from readablestem.eval.equations import MathMLError

FIDELITY = 0.99


def rows_of(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def classify_pair(pair: dict, gold_mathml: str) -> str:
    """`correct`, `scorer`, `span`, `recognition` or `no_proposal` for an aligned pair."""
    if pair["exact_lenient"]:
        return "correct"
    if pair.get("exact_symbols"):
        return "scorer"
    latex = pair.get("pred_latex")
    if not latex:
        return "no_proposal"
    try:
        pred = sym.symbols_of_latex(latex)
    except MathMLError:
        return "no_proposal"
    gold = sym.symbols_of(gold_mathml)
    ops = [
        op
        for op in difflib.SequenceMatcher(None, pred, gold, autojunk=False).get_opcodes()
        if op[0] != "equal"
    ]
    if not ops:
        return "scorer"
    at_ends = all(
        op[1] == 0 or op[2] == len(pred) or op[3] == 0 or op[4] == len(gold) for op in ops
    )
    # A pure insertion or deletion at the start or the end is a span cut or extended; any
    # edit inside is a reading that differs.
    ends_only = at_ends and all(op[0] in ("insert", "delete") for op in ops)
    return "span" if ends_only else "recognition"


def summarize(items: list[dict], gold: dict[str, dict]) -> dict:
    n_gold = sum(i["n_gold"] for i in items)
    pairs = [(i, p) for i in items for p in i["pairs"]]
    pages = sum(i.get("pdf_pages", 0) for i in items) or 1
    buckets: Counter[str] = Counter()
    for _, pair in pairs:
        buckets[classify_pair(pair, gold[pair["gold_id"]]["mathml"])] += 1
    credited: set[str] = set()
    for item in items:
        credited |= set(item["symbols_credited"]["split"]) | set(item["symbols_credited"]["merged"])
    matched_ids = {p["gold_id"] for _, p in pairs}
    undetected_ids = {g for i in items for g in i["missed_gold_ids"]}
    out: dict[str, float | int | str] = {"gold": n_gold, "pdf_pages": pages}
    exact = sum(p["exact_lenient"] for _, p in pairs)
    out["primary exact_lenient"] = exact / n_gold
    v2_ids = {p["gold_id"] for _, p in pairs if p["exact_stream_v2"]}
    for item in items:
        v2_ids |= set(item["stream_credited"]["split"]) | set(item["stream_credited"]["merged"])
    out["stream v2 (credit)"] = len(v2_ids) / n_gold
    symbol_ids = {p["gold_id"] for _, p in pairs if p["exact_symbols"]}
    out["symbols"] = len(symbol_ids) / n_gold
    out["symbols (credit)"] = len(symbol_ids | credited) / n_gold
    out["detection recall"] = len(matched_ids) / n_gold
    out["detection precision"] = len(matched_ids) / max(1, sum(i["n_pred"] for i in items))
    out[f"span fidelity (>={FIDELITY})"] = (
        sum(p.get("text_similarity", 0.0) >= FIDELITY for _, p in pairs) / n_gold
    )
    for name in ("correct", "scorer", "span", "recognition", "no_proposal"):
        out[f"bucket {name}"] = buckets[name] / n_gold
    out["bucket undetected"] = len(undetected_ids) / n_gold
    out["  of which credited split/merged"] = len(undetected_ids & credited) / n_gold
    reasons = Counter()
    unsupported = 0
    for item in items:
        for u in item["unmatched"]:
            reasons[u["reason"]] += 1
            unsupported += not u["text_layer_supported"] and not u["piece_of"]
    out["unmatched predictions"] = sum(reasons.values())
    out["  prose_math / fragment / other"] = "{} / {} / {}".format(
        reasons["prose_math"], reasons["fragment"], reasons["other"]
    )
    out["  unsupported (invented?)"] = unsupported
    load = pe._sum_loads(i["review"] for i in items)
    out["required decisions / pdf page"] = load["required_decisions"] / pages
    out["  of which equations"] = load["required_by_kind"].get("equation", 0) / pages
    wrong_unflagged = sum(
        (not p["exact_lenient"]) and p.get("review") != "required" for _, p in pairs
    )
    out["wrong, not asked, per 100 gold (primary)"] = 100 * wrong_unflagged / n_gold
    sym_wrong_unflagged = sum(
        (not p["exact_symbols"]) and p.get("review") != "required" for _, p in pairs
    )
    out["wrong, not asked, per 100 gold (symbols)"] = 100 * sym_wrong_unflagged / n_gold
    flagged = [p for _, p in pairs if p.get("review") == "required"]
    wrong_sym = [p for _, p in pairs if not p["exact_symbols"]]
    out["flag precision (symbols-wrong among flagged)"] = (
        sum(not p["exact_symbols"] for p in flagged) / len(flagged) if flagged else float("nan")
    )
    out["flag recall (symbols-wrong that were flagged)"] = (
        sum(p.get("review") == "required" for p in wrong_sym) / len(wrong_sym)
        if wrong_sym
        else float("nan")
    )
    split = Counter()
    for item in items:
        for key, value in item.get("cost_split", {}).items():
            split[key] += value
    page_math = sum(i.get("page_math_cost_usd", 0.0) for i in items)
    total = sum(i.get("cost_usd", 0.0) for i in items)
    out["cost / pdf page, all"] = total / pages
    out["  page pass"] = page_math / pages
    out["  cheap reads"] = split["cheap"] / pages
    out["  main, first read"] = split["main_first"] / pages
    out["  main, escalation"] = split["escalation"] / pages
    out["  figures and other"] = (total - page_math - sum(split.values())) / pages
    agree = Counter(p.get("page_reading") for _, p in pairs)
    out["page reading agree/overruled/mismatch"] = "{} / {} / {}".format(
        agree["agree"], agree["overruled"], agree["mismatch"]
    )
    status = Counter(p.get("page_status") for _, p in pairs)
    out["aligned pairs by page status span/layout/none"] = "{} / {} / {}".format(
        status["span"], status["layout"], status[None]
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("runs", nargs="+", metavar="LABEL=ITEMS.jsonl")
    parser.add_argument("--sections", nargs="*", default=(), help="only these book/page ids")
    args = parser.parse_args()
    gold = {r["id"]: r for r in pe.load_gold(args.gold) if r.get("kind") == "equation"}
    columns: dict[str, dict] = {}
    for spec in args.runs:
        label, _, path = spec.partition("=")
        items = rows_of(Path(path))
        if args.sections:
            items = [i for i in items if any(s in i["id"] for s in args.sections)]
        columns[label] = summarize(items, gold)
    labels = list(columns)
    keys = list(columns[labels[0]])
    width = max(len(k) for k in keys)
    print(f"{'':{width}s} | " + " | ".join(f"{label:>16s}" for label in labels))
    for key in keys:
        cells = []
        for label in labels:
            value = columns[label][key]
            cells.append(f"{value:16.4f}" if isinstance(value, float) else f"{value!s:>16}")
        print(f"{key:{width}s} | " + " | ".join(cells))


if __name__ == "__main__":
    main()
