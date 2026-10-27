"""Real-world set: where each arm loses equations, by mechanism (docs/eval-equation-rootcause.md).

    uv run python scripts/rw_gap_table.py --part dev --rs rs-allow [--rs-b base-main]

Joins, for every gold equation of a part, its outcome in ReadableSTEM's recorded items and in each
chatbot arm's, and gives every failure one mechanism (the first rule that fits):

- caption: typed in a figure or table caption, no aligned prediction
- table cell / algorithm: typed in a table body or an algorithm, no aligned prediction
- short inline: one to three tokens in running text, no aligned prediction
- longer inline, display: more than three tokens, or a display equation, no aligned prediction
- span, by what differs at the edge: parentheses of the sentence, a unit or citation, a word
  of the sentence, other
- recognition, by what the author typed: bold italic (`\\bm`, `\\boldsymbol`), upright text
  (`\\rm`, `\\mathrm`, `\\text`), script letters, other
- scorer: the symbols are right, the primary scorer says no
- no proposal

It prints, per mechanism, the share of the gold each arm loses and ReadableSTEM minus the arm
(positive: ReadableSTEM loses more), and the cross-tabulation of who is right where the other is
wrong. Nothing here calls a model.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from readablestem.eval import equations_symbols as sym
from readablestem.eval import pipeline_eval as pe
from readablestem.gold import arxiv_tex as tex

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_CAPTION = re.compile(r"\\(?:caption|subcaption|captionof)\*?(?:\[[^\]]*\])?\s*(?:\{[^{}]*\})?\{")


def in_caption(body: str, offset: int) -> bool:
    """Is the maths at `offset` of the flattened body inside a `\\caption{...}` group?"""
    for match in reversed(list(_CAPTION.finditer(body[:offset]))):
        depth, i = 1, match.end()
        while i < offset and depth > 0:
            depth += {"{": 1, "}": -1}.get(body[i], 0)
            i += 1
        return depth > 0
    return False


def captions_of(part: str, gold: dict[str, dict]) -> set[str]:
    """Gold ids typed in a caption, from the cached sources (the extractor that made the gold)."""

    by_paper: dict[str, list[dict]] = defaultdict(list)
    for record in gold.values():
        by_paper[record["arxiv_id"]].append(record)
    found: set[str] = set()
    for paper, records in by_paper.items():
        base = ROOT / "papers" / paper.replace("/", "_")
        sources = tex.read_sources(base / "src")
        main = tex.find_main(sources)
        if main is None:
            continue
        table = tex.MacroTable()
        body = tex.body_of(table.expand(table.read(tex.flatten(sources, main))))
        items = tex.extract(body).items
        for record in records:
            item = items[record["tex_item"]]
            if item.float_kind and in_caption(body, item.offset):
                found.add(record["id"])
    return found


_BOLD_ITALIC = re.compile(r"\\(?:boldsymbol|bm)\b")
_UPRIGHT = re.compile(r"\\(?:rm|mathrm|text|textrm|operatorname)\b")
_SCRIPT = re.compile(r"\\(?:mathscr|mathcal)\b")


def edge_of(pred_latex: str, gold_mathml: str) -> str:
    a = sym.symbols_of_latex(pred_latex)
    b = sym.symbols_of(gold_mathml)
    ops = [
        op
        for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        if op[0] != "equal"
    ]
    extra = "".join("".join(a[o[1] : o[2]]) for o in ops if o[0] == "delete")
    if extra in {"(", ")"} or (len(ops) == 2 and extra in {"()", "(,)"}):
        return "parentheses of the sentence"
    if re.fullmatch(r"[a-zA-Z%°]{1,3}", extra) and ops[-1][2] == len(a):
        return "a unit after the number"
    if re.fullmatch(r"\[[\d,]+\]", extra):
        return "a citation"
    if re.fullmatch(r"[A-Za-z]{2,12}", extra) and ops[0][1] == 0:
        return "a word of the sentence"
    return "other"


def mechanism(arm: dict | None, record: dict, kind: str, caption: bool, buckets) -> str | None:
    """The mechanism of a failure, None when the equation is right."""
    if arm is None:
        return None
    pair = arm.get("pair")
    if pair is None:
        where = "caption" if caption else kind.split(">")[0]
        if where in ("figure", "table") and not caption:
            where = "table cell" if where == "table" else "figure panel"
        if where in ("caption", "table cell", "figure panel", "algorithm"):
            return (
                f"undetected, in a {where}"
                if where != "algorithm"
                else "undetected, in an algorithm"
            )
        if record["display"] == "block" or record["tokens"] > 3:
            return "undetected, longer inline or display"
        return "undetected, 1 to 3 tokens in running text"
    cls = buckets.classify_pair(pair, record["mathml"])
    if cls == "correct":
        return None
    if cls == "span":
        return "span: " + edge_of(pair["pred_latex"], record["mathml"])
    if cls == "recognition":
        latex = record["latex"]
        if _BOLD_ITALIC.search(latex):
            return "recognition: bold italic (\\bm, \\boldsymbol)"
        if _UPRIGHT.search(latex):
            return "recognition: upright text (\\rm, \\mathrm, \\text)"
        if _SCRIPT.search(latex):
            return "recognition: script letters (\\mathcal, \\mathscr)"
        return "recognition: other"
    return {"scorer": "scorer", "no_proposal": "no proposal"}[cls]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--part", choices=["dev", "dev2", "heldout", "fresh", "fresh2", "fresh3", "fresh4"], default="dev"
    )
    parser.add_argument("--rs", default="rs", help="ReadableSTEM's run folder under runs/PART")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--vs",
        choices=["Haiku", "Opus"],
        default="Haiku",
        help="the chatbot the cross-tabulation (who is right where the other is wrong) is against; "
        "Opus is scored on the papers it was run on only",
    )
    parser.add_argument("--reading", choices=["registered", "cells"], default="registered")
    parser.add_argument(
        "--rescore-chat",
        action="store_true",
        help="score the chatbot arms again from their recorded answers with the current code",
    )
    args = parser.parse_args()
    runs = ROOT / "runs" / args.part
    gold = {
        r["id"]: r for r in pe.load_gold(ROOT / "gold" / args.part) if r.get("kind") == "equation"
    }
    report = _load("realworld_report")
    buckets = report.load_buckets_module()
    kinds = report.float_kinds(args.part, gold)
    captions = captions_of(args.part, gold)
    arms = {
        "ReadableSTEM": runs / args.rs / "items.jsonl",
        "Haiku": runs / "chat" / "chatbot-haiku-purdue.items.jsonl",
        "Opus": runs / "chat" / "chatbot-opus-purdue.items.jsonl",
    }
    outcome: dict[str, dict[str, dict]] = {}
    for name, path in arms.items():
        if not path.is_file():
            continue
        outcome[name] = {}
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if args.rescore_chat and name in ("Haiku", "Opus"):
            rows = report.rescored_chat_items(args.part, f"Chatbot, {name}")
        for item in rows:
            item = report.apply_reading(item, args.reading) if name == "ReadableSTEM" else item
            pairs = {p["gold_id"]: p for p in item["pairs"]}
            for gid in [*pairs, *item["missed_gold_ids"]]:
                outcome[name][gid] = {"pair": pairs.get(gid)}
    result: dict[str, dict[str, int]] = {}
    for name, rows in outcome.items():
        count: Counter[str] = Counter()
        for gid, arm in rows.items():
            why = mechanism(arm, gold[gid], kinds.get(gid, "text"), gid in captions, buckets)
            if why:
                count[why] += 1
        result[name] = dict(count)
    common = set(outcome["ReadableSTEM"])
    for name in outcome:
        common &= set(outcome[name]) if name != "Opus" else set(outcome[name]) | common
    print(f"## {args.part}: {len(gold)} gold equations ({len(outcome['ReadableSTEM'])} scored)")
    names = list(outcome)
    mechanisms = sorted({m for r in result.values() for m in r})
    print("| mechanism | " + " | ".join(f"{n} (lost per gold)" for n in names) + " |")
    print("|" + "---|" * (len(names) + 1))
    n_gold = {n: len(outcome[n]) for n in names}
    for m in sorted(mechanisms, key=lambda m: -result["ReadableSTEM"].get(m, 0)):
        cells = [f"{result[n].get(m, 0)} ({result[n].get(m, 0) / n_gold[n]:.3f})" for n in names]
        print(f"| {m} | " + " | ".join(cells) + " |")
    totals = [
        f"{sum(result[n].values())} ({sum(result[n].values()) / n_gold[n]:.3f})" for n in names
    ]
    print("| all failures | " + " | ".join(totals) + " |")
    versus = args.vs
    if versus in outcome:
        crossed: Counter[tuple[str, bool]] = Counter()
        scored = [g for g in outcome["ReadableSTEM"] if g in outcome[versus]]
        for gid, arm in outcome["ReadableSTEM"].items():
            other = outcome[versus].get(gid)
            if other is None:
                continue
            mine = mechanism(arm, gold[gid], kinds.get(gid, "text"), gid in captions, buckets)
            theirs = mechanism(other, gold[gid], kinds.get(gid, "text"), gid in captions, buckets)
            if mine and not theirs:
                crossed[(mine, False)] += 1
            if theirs and not mine:
                crossed[(theirs, True)] += 1
        print()
        print(
            f"| mechanism | ReadableSTEM wrong, {versus} right | {versus} wrong, ReadableSTEM "
            f"right "
            f"| net points (of {len(scored)} gold) |"
        )
        print("|---|---|---|---|")
        keys = sorted({k for k, _ in crossed})
        for k in sorted(keys, key=lambda k: -(crossed[(k, False)] - crossed[(k, True)])):
            lost, won = crossed[(k, False)], crossed[(k, True)]
            print(f"| {k} | {lost} | {won} | {100 * (lost - won) / len(scored):+.2f} |")
    print()
    print(silent_errors(args, gold, buckets))
    if args.out:
        args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return 0


def silent_errors(args: argparse.Namespace, gold: dict[str, dict], buckets) -> str:
    """ReadableSTEM's wrong equations that nobody is asked about, by kind. A render-and-compare
    check can only help where the symbols are right and the structure is not: the symbol bag
    (letters, digits, operators and structure markers) is equal, the order or nesting differs."""
    report = _load("realworld_report")
    items = [
        report.apply_reading(json.loads(line), args.reading)
        for line in (ROOT / "runs" / args.part / args.rs / "items.jsonl").read_text().splitlines()
    ]
    count: Counter[str] = Counter()
    for item in items:
        for pair in item["pairs"]:
            if pair["exact_lenient"] or pair.get("review") == "required":
                continue
            record = gold[pair["gold_id"]]
            cls = buckets.classify_pair(pair, record["mathml"])
            if cls == "recognition":
                a = Counter(sym.symbols_of_latex(pair["pred_latex"]))
                b = Counter(sym.symbols_of(record["mathml"]))
                cls = (
                    "recognition, same symbols in another arrangement"
                    if a == b
                    else "recognition, other symbols"
                )
            count[cls] += 1
    total = sum(count.values())
    n_gold = sum(i["n_gold"] for i in items)
    rows = [
        f"Silent errors of ReadableSTEM ({args.rs}): {total} "
        f"({100 * total / n_gold:.2f} per 100 gold)"
    ]
    rows += [
        f"- {kind}: {n} ({100 * n / total:.0f}% of them, {100 * n / n_gold:.2f} per 100 gold)"
        for kind, n in count.most_common()
    ]
    return "\n".join(rows)


if __name__ == "__main__":
    sys.exit(main())
