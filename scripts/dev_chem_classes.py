# ruff: noqa: RUF001
"""Every chemistry gold equation of a development run, put in one outcome class.

    uv run python scripts/dev_chem_classes.py --gold data/dev-v2/dev LABEL=items.jsonl [...]
        [--sections SUBSTRING ...] [--list CLASS ...]

Reads per-page items (`eval pipeline --task document --items FILE`, or `scripts/dev_chatbot.py
score --out FILE`; development sets only) and the set's gold. The gold equations counted are
the ones of the chemistry sections (the OpenStax Chemistry sections and the authored chemistry
document), every class of equation in them. Each is put in the first class that fits:

  right                  the primary scorer (`exact_lenient`) says right
  artefact: tokens       wrong on the primary, right on stream v2 (how tokens are packed)
  artefact: scripts      ... right on the symbols scorer (script grouping, empty cells)
  artefact: chem         ... right on the chemistry scorer (`eval.equations_chem`: style,
                         arrow length, look-alike bars, split numbers, a bracket the span
                         does not own)
  input: page lacks it   wrong, and equal to the gold once the atomic number of a prefix
                         nuclide symbol is taken out: the printed page does not show it
  not read               a predicted equation is aligned, no reading was kept (failed fix)
  span: extra            the reading holds the gold's symbols and more (a neighbour merged)
  span: partial          the reading is a part of the gold's symbols (the span was cut)
  wrong reading          same span, other symbols: the first changed atoms say how
  undetected: table      no predicted equation; the gold is a cell of a table of the section
  undetected: x sign     no predicted equation; the gold is a lone multiplication sign
  undetected: other      no predicted equation

The first four are about the scorer (the reading says what the gold says); the rest are about
what the system wrote or found. Nothing here changes a scorer or a committed report.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from readablestem.eval import equations_chem as chem
from readablestem.eval.equations import MathMLError
from readablestem.pipeline.validate import safe_latex_to_mathml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_chem  # noqa: E402  (the same directory)

ORDER = [
    "right",
    "artefact: tokens",
    "artefact: scripts",
    "artefact: chem",
    "input: page lacks it",
    "not read",
    "span: extra",
    "span: partial",
    "wrong reading",
    "undetected: table",
    "undetected: x sign",
    "undetected: other",
]
_PREFIX_SUB = re.compile(r"_ (?:<g> )?[−+]?\d+(?: </g>)? \^")


def pred_mathml(pair: dict) -> str | None:
    if pair.get("pred_mathml"):
        return pair["pred_mathml"]
    if pair.get("pred_latex"):
        try:
            return safe_latex_to_mathml(pair["pred_latex"], "inline")
        except (MathMLError, ValueError):
            return None
    return None


def _contains(big: list[str], small: list[str]) -> bool:
    n = len(small)
    return n > 0 and any(big[i : i + n] == small for i in range(len(big) - n + 1))


def _without_prefix_subscripts(stream: list[str]) -> list[str]:
    return _PREFIX_SUB.sub("^", " ".join(stream)).split()


def how_wrong(pred: list[str], gold: list[str]) -> str:
    """The first changed atoms of a reading that spans the gold: what kind of change."""
    kinds: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, pred, gold, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        changed = " ".join([*pred[i1:i2], *gold[j1:j2]])
        if re.search(r"[⇌→←↔]", changed):
            kinds.append("arrow")
        elif re.search(r"\b[aqslg]\b", changed) and "(" in " ".join(pred + gold):
            kinds.append("state symbol")
        elif re.fullmatch(r"[\d.,+−\s<>/g^_]+", changed):
            kinds.append("count, charge or number")
        elif tag == "delete":
            kinds.append("part dropped by the reading")
        elif tag == "insert":
            kinds.append("part added by the reading")
        else:
            kinds.append("letters differ")
    return kinds[0] if kinds else "other"


def table_pools(gold_dir: Path) -> dict[str, Counter]:
    pools: dict[str, Counter] = defaultdict(Counter)
    for path in gold_dir.rglob("*.jsonl"):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("kind") == "table" and "chemistry" in record.get("id", ""):
                for m in re.findall(r"<math\b.*?</math>", record.get("html", ""), re.S):
                    pools[record["page_slug"]][dev_chem.content(m)] += 1
    return pools


def classify(
    rows: list[dict], gold: dict[str, dict], pools: dict[str, Counter], sections: list[str]
):
    """gold id -> (class, detail), for the chemistry gold of the sections the rows cover."""
    out: dict[str, tuple[str, str]] = {}
    for row in rows:
        if sections and not any(s in row["id"] for s in sections):
            continue
        by_gold = {p["gold_id"]: p for p in row["pairs"]}
        pool = Counter(pools.get(row["id"].split("/")[-1], {}))
        for gold_id in [*by_gold, *row["missed_gold_ids"]]:
            if gold_id not in gold or not dev_chem.is_chemistry(gold_id):
                continue
            g_stream = chem.chem_of(gold[gold_id]["mathml"])
            pair = by_gold.get(gold_id)
            if pair is None:
                key = dev_chem.content(gold[gold_id]["mathml"])
                if pool[key] > 0:
                    pool[key] -= 1
                    out[gold_id] = ("undetected: table", "")
                elif g_stream == ["×"]:
                    out[gold_id] = ("undetected: x sign", "")
                else:
                    out[gold_id] = ("undetected: other", "")
                continue
            if pair["exact_lenient"]:
                out[gold_id] = ("right", "")
                continue
            mml = pred_mathml(pair)
            if mml is None:
                out[gold_id] = ("not read", pair.get("status") or "")
                continue
            if pair.get("exact_stream_v2"):
                out[gold_id] = ("artefact: tokens", "")
                continue
            if pair["exact_symbols"]:
                out[gold_id] = ("artefact: scripts", "")
                continue
            p_stream = chem.chem_of(mml)
            if p_stream == g_stream:
                out[gold_id] = ("artefact: chem", "")
            elif p_stream == _without_prefix_subscripts(g_stream) != g_stream:
                out[gold_id] = ("input: page lacks it", "")
            elif _contains(p_stream, g_stream):
                out[gold_id] = ("span: extra", "")
            elif _contains(g_stream, p_stream):
                out[gold_id] = ("span: partial", "")
            else:
                out[gold_id] = ("wrong reading", how_wrong(p_stream, g_stream))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--sections", nargs="*", default=[], help="only rows whose id has one")
    parser.add_argument("--list", nargs="*", default=[], metavar="CLASS", help="print these")
    parser.add_argument("runs", nargs="+", metavar="LABEL=items.jsonl")
    args = parser.parse_args()
    gold = dev_chem.load_gold(args.gold)
    pools = table_pools(args.gold)
    results = {}
    for spec in args.runs:
        label, _, path = spec.partition("=")
        results[label] = classify(dev_chem.rows_of(Path(path)), gold, pools, args.sections)
    labels = list(results)
    print(f"{'':24}" + "".join(f"| {label:>14} " for label in labels))
    for source in ("openstax", "authored", "all"):
        print(f"-- {source}")
        for cls in ORDER:
            cells = []
            for label in labels:
                chosen = [
                    g
                    for g, (c, _) in results[label].items()
                    if c == cls and (source == "all" or dev_chem.source_of(g) == source)
                ]
                total = [
                    g for g in results[label] if source == "all" or dev_chem.source_of(g) == source
                ]
                cells.append(f"{len(chosen):>5} {len(chosen) / (len(total) or 1):>6.1%}")
            print(f"{cls:24}" + "".join(f"| {cell:>14} " for cell in cells))
        totals = [
            len([g for g in results[label] if source == "all" or dev_chem.source_of(g) == source])
            for label in labels
        ]
        print(f"{'gold':24}" + "".join(f"| {t:>14} " for t in totals))
    for label in labels:
        details = Counter(d for c, d in results[label].values() if c == "wrong reading")
        print(f"wrong reading by change, {label}:", dict(details))
    for cls in args.list:
        for label in labels:
            for g, (c, d) in sorted(results[label].items()):
                if c == cls:
                    print(f"[{label}] {cls} {g} {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
