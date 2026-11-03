"""What the chemistry species check (`pipeline.chem_check`) says about recorded answers, with
no model call.

    uv run python scripts/chem_check_replay.py [--list] LABEL=items.jsonl [...]

Reads per-page items (`eval pipeline --task document --items FILE`; development sets only) and
runs `compare_species` on every aligned pair's recorded text layer (`pred_text`) and MathML
(`pred_mathml`). Per set it prints how many pairs the check applied to (the text layer had
chemistry evidence), how many it flagged, and the flags against the pair's own outcome:

- a *real error* is a pair the chemistry scorer calls wrong (`exact_chem` false, and
  `exact_lenient` false);
- a *false flag* is a flag on a pair the chemistry scorer calls right;
- of the real errors, how many were already a required decision (`review == "required"`).

`--list` prints every flag with the text layer, the species rows and the kind. Pairs with no
`pred_mathml` (a failed fix) are skipped. Nothing here changes a report.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from readablestem.pipeline.chem_check import compare_species


def replay(path: Path, *, listing: bool, by_doc: bool = False) -> dict[str, int]:
    count: Counter[str] = Counter()
    for line in path.read_text().splitlines():
        item = json.loads(line)
        if by_doc:
            applied = flagged = 0
            for pair in item.get("pairs", []):
                if pair.get("pred_mathml") and pair.get("pred_text"):
                    diff = compare_species(pair["pred_text"], pair["pred_mathml"])
                    applied += diff is not None
                    flagged += diff is not None and diff.differ
            count[f"{item['id']}|pairs"] += len(item.get("pairs", []))
            count[f"{item['id']}|applied"] += applied
            count[f"{item['id']}|flagged"] += flagged
            continue
        for pair in item.get("pairs", []):
            mathml, text = pair.get("pred_mathml"), pair.get("pred_text")
            if not mathml or not text:
                continue
            count["pairs"] += 1
            diff = compare_species(text, mathml)
            if diff is None:
                continue
            count["applied"] += 1
            wrong = not pair.get("exact_chem") and not pair.get("exact_lenient")
            count["applied_wrong"] += wrong
            if not diff.differ:
                continue
            count["flagged"] += 1
            count["flag_real" if wrong else "flag_false"] += 1
            required = pair.get("review") == "required"
            if wrong:
                count["real_already_required" if required else "real_newly_required"] += 1
            elif not required:
                count["false_newly_required"] += 1
            if listing:
                tag = "REAL " if wrong else "FALSE"
                print(
                    f"  {tag} {item['id']} {pair['element_id']} {diff.kinds} "
                    f"review={pair.get('review')}\n        text={text!r}\n        rows={diff.rows}"
                )
    return dict(count)


def replay_gold(media: Path, gold: Path, *, listing: bool) -> dict[str, dict[str, int]]:
    """The gold reading of each equation against the text layer of its own print, per book:
    a right reading by construction, so every flag is a false one. `media` is a recorded
    run's `equations.jsonl` (id, text); `gold` the folder of the gold's per-section JSONL."""
    texts = {}
    for line in media.read_text().splitlines():
        row = json.loads(line)
        texts[row["id"]] = row.get("text") or ""
    out: dict[str, dict[str, int]] = {}
    for file in sorted(gold.rglob("*.jsonl")):
        for line in file.read_text().splitlines():
            row = json.loads(line)
            text = texts.get(row["id"])
            if not text or not row.get("mathml"):
                continue
            book = out.setdefault(row["book_slug"], {"equations": 0, "applied": 0, "flagged": 0})
            book["equations"] += 1
            diff = compare_species(text, row["mathml"])
            if diff is None:
                continue
            book["applied"] += 1
            if diff.differ:
                book["flagged"] += 1
                if listing:
                    print(f"  FALSE {row['id']} {diff.kinds}")
                    print(f"        text={text!r}\n        rows={diff.rows}")
    return out


# Errors written into a right reading's LaTeX, each the way a model gets chemistry wrong
# (docs/chemistry.md). A mutation returns None when its pattern is not in the LaTeX.
_ARROWS = r"\\(?:longrightarrow|rightarrow|rightleftharpoons|leftrightarrow)"


def _sign_flip(latex: str) -> str | None:
    found = re.search(r"\^\{?\d*([+-])\}?", latex)
    if not found:
        return None
    flipped = "+" if found.group(1) == "-" else "-"
    return latex[: found.start(1)] + flipped + latex[found.end(1) :]


def _sub_to_coefficient(latex: str) -> str | None:
    found = re.search(r"(\\text\{[A-Z][a-z]?\})_\{?(\d)\}?", latex)
    return (
        latex[: found.start()] + f"{found.group(2)} {found.group(1)}" + latex[found.end() :]
        if found
        else None
    )


def _neighbour(latex: str) -> str | None:
    found = re.search(r"\\text\{[A-Z]", latex)
    return latex[: found.start()] + r"\text{Pb}" + latex[found.start() :] if found else None


def _cut(latex: str) -> str | None:
    found = re.search(_ARROWS, latex)
    return latex[: found.start()] if found and found.start() > 0 else None


def _arrow_swap(latex: str) -> str | None:
    if r"\rightleftharpoons" in latex:
        return latex.replace(r"\rightleftharpoons", r"\longrightarrow", 1)
    found = re.search(r"\\(?:longrightarrow|rightarrow)", latex)
    return latex[: found.start()] + r"\rightleftharpoons" + latex[found.end() :] if found else None


def _state_change(latex: str) -> str | None:
    found = re.search(r"\\text\{\((aq|s|l|g)\)\}", latex)
    if not found:
        return None
    other = "s" if found.group(1) != "s" else "g"
    return latex[: found.start(1)] + other + latex[found.end(1) :]


def _drop_charge(latex: str) -> str | None:
    found = re.search(r"\^\{?\d*[+-]\}?", latex)
    return latex[: found.start()] + latex[found.end() :] if found else None


def _element_swap(latex: str) -> str | None:
    found = re.search(r"\\text\{(O|H|C|N)\}", latex)
    if not found:
        return None
    return (
        latex[: found.start(1)]
        + {"O": "S", "H": "F", "C": "N", "N": "P"}[found.group(1)]
        + latex[found.end(1) :]
    )


MUTATIONS = {
    "charge sign flipped": _sign_flip,
    "charge dropped": _drop_charge,
    "subscript read as coefficient": _sub_to_coefficient,
    "neighbour formula swallowed": _neighbour,
    "reaction cut mid-way": _cut,
    "arrow changed": _arrow_swap,
    "state changed": _state_change,
    "element changed": _element_swap,
}


def mutate(path: Path) -> None:
    """Write each error into every right, aligned pair the check applies to, and count how
    many the species check finds, and how many the step's other text-layer checks (symbols,
    numbers, case, signs) would have found already."""
    from readablestem.pipeline import equation_step, signs
    from readablestem.pipeline.validate import safe_latex_to_mathml

    found: Counter[str] = Counter()
    for line in path.read_text().splitlines():
        for pair in json.loads(line).get("pairs", []):
            text, latex = pair.get("pred_text"), pair.get("pred_latex")
            if not (text and latex and pair.get("exact_chem")):
                continue
            if compare_species(text, pair["pred_mathml"]) is None:
                continue
            for name, fn in MUTATIONS.items():
                changed = fn(latex)
                if changed is None or changed == latex:
                    continue
                try:
                    mathml = safe_latex_to_mathml(changed, "block")
                except Exception:
                    continue
                found[f"{name}|made"] += 1
                diff = compare_species(text, mathml)
                species = diff is not None and diff.differ
                compared = signs.compare_signs(text, mathml)
                other = bool(equation_step.text_layer_symbols_differ(text, mathml)) or bool(
                    compared and compared.mismatch
                )
                found[f"{name}|species"] += species
                found[f"{name}|other"] += other
                found[f"{name}|species_only"] += species and not other
    print(f"{'error':32} {'made':>5} {'species':>8} {'existing':>9} {'species only':>13}")
    for name in MUTATIONS:
        made = found[f"{name}|made"]
        print(
            f"{name:32} {made:>5} {found[f'{name}|species']:>8} {found[f'{name}|other']:>9} "
            f"{found[f'{name}|species_only']:>13}"
        )


def step_diff(path: Path) -> None:
    """Run the equation step's own evaluation (`equation_step._evaluate`) on every recorded
    pair with the species check on and off, and print what changes per document: the
    checks (name, outcome, code) and the confidence, with a model confidence of 0.95. A
    document whose chemistry the check never applies to must show 0 changed pairs."""
    from unittest import mock

    from readablestem.core.types import Element, Kind
    from readablestem.pipeline import equation_step

    def evaluate(pair: dict) -> tuple:
        element = Element(
            id=pair["element_id"], kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0,
            text=pair["pred_text"], display=pair.get("pred_display") or "block",
        )  # fmt: skip
        output = equation_step.EquationOutput(
            is_equation=True, latex=pair["pred_latex"], confidence=0.95
        )
        got = equation_step._evaluate(output, [], element, element.display or "block")
        return (
            round(got.confidence or 0, 6),
            tuple((c.name, c.passed, c.code) for c in got.checks),
        )

    rows: dict[str, list[int]] = {}
    for line in path.read_text().splitlines():
        item = json.loads(line)
        done = rows.setdefault(item["id"], [0, 0])
        for pair in item.get("pairs", []):
            if not (pair.get("pred_text") and pair.get("pred_latex")):
                continue
            after = evaluate(pair)
            with mock.patch.object(equation_step, "compare_species", lambda *_a: None):
                before = evaluate(pair)
            done[0] += 1
            done[1] += before != after
    for doc, (pairs, changed) in sorted(rows.items()):
        print(f"  {doc:90} pairs={pairs:4} changed={changed:3}")
    print(
        f"  total pairs={sum(r[0] for r in rows.values())} "
        f"changed={sum(r[1] for r in rows.values())}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sets", nargs="*", metavar="LABEL=FILE")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--gold-media", nargs=2, metavar=("EQUATIONS_JSONL", "GOLD_DIR"))
    parser.add_argument("--by-doc", action="store_true", help="applied and flagged per document")
    parser.add_argument("--step-diff", metavar="ITEMS_JSONL", help="step checks on vs off")
    parser.add_argument("--mutate", metavar="ITEMS_JSONL", help="write errors into right pairs")
    args = parser.parse_args()
    if args.step_diff:
        step_diff(Path(args.step_diff))
    if args.mutate:
        mutate(Path(args.mutate))
    for spec in args.sets:
        label, _, file = spec.partition("=")
        counts = replay(Path(file), listing=args.list, by_doc=args.by_doc)
        if args.by_doc:
            docs = sorted({k.rsplit("|", 1)[0] for k in counts})
            print(label)
            for doc in docs:
                print(
                    f"  {doc:90} pairs={counts[doc + '|pairs']:4} "
                    f"applied={counts[doc + '|applied']:4} flagged={counts[doc + '|flagged']:3}"
                )
        else:
            print(label, counts)
    if args.gold_media:
        media, gold = args.gold_media
        for book, counts in replay_gold(Path(media), Path(gold), listing=args.list).items():
            print(book, counts)


if __name__ == "__main__":
    main()
