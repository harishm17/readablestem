"""Classify every handwriting error in the recorded answers of an evaluation run.

    uv run python scripts/handwriting_errors.py
    uv run python scripts/handwriting_errors.py --arm control --part heldout --json out.json
    uv run python scripts/handwriting_errors.py --arms-root .local/hw-accuracy/arms --examples 3

Reads `<arms-root>/<arm>/<part>/items.jsonl` (what `scripts/handwriting_accuracy.py run` records)
and the gold equations in `<gold-root>/<part>/*.jsonl` (default `~/.cache/readablestem/handwriting/
gold`). It costs nothing and calls no model. For each equation the run got wrong (not
`exact_lenient`, or left out) it reports:

* kinds: what differs, found by diffing the two symbol streams of
  `readablestem.eval.equations_symbols` (the stream the symbol score uses). One equation can have
  several kinds, so the kind counts add to more than the number of wrong equations.
* edit distance in symbols (Levenshtein over the streams) and whether it is one symbol.
* speech_equal: whether the gold and the reading are spoken identically by the product's own
  `readablestem.pipeline.speech.mathml_to_speech`. A wrong equation that is speech-equal sounds the
  same to a listener (a notation the label did not use).

Output: a short text table on stdout and, with `--json`, the machine-readable summary with up to
`--examples` examples per kind and arm. The examples carry gold and reading LaTeX, which comes
from the evaluation set (CC BY-NC-SA, `data/handwriting/ATTRIBUTION.md`): keep the JSON out of
the repo (the default location is under `.local/`, which is ignored).
"""

# ruff: noqa: RUF001  (look-alike characters are the point of this file)
from __future__ import annotations

import argparse
import collections
import difflib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ARMS = REPO / ".local" / "hw-accuracy" / "arms"
DEFAULT_GOLD = Path.home() / ".cache" / "readablestem" / "handwriting" / "gold"
DEFAULT_JSON = REPO / ".local" / "hw-accuracy" / "errors.json"

# Kinds, in the order the table lists them.
KINDS = (
    "omission",
    "case",
    "greek_latin",
    "greek_greek",
    "other_letter",
    "digit_letter",
    "z_2",
    "x_times",
    "minus_dash",
    "style",
    "script_lost",
    "script_added",
    "script_changed",
    "accent",
    "fraction",
    "root",
    "structure",
    "operator_bracket",
    "symbol_dropped",
    "symbol_added",
    "multi_symbol",
    "tree_only",
)

GREEK = set("αβγδεϵζηθϑικλμνξπρσςτυφϕχψωΓΔΘΛΞΠΣΥΦΨΩ∂")
STYLED = {"ℝ": "R", "ℤ": "Z", "ℚ": "Q", "ℕ": "N", "ℂ": "C", "ℙ": "P"}
DASHES = set("−-–—‐‑‒")
ACCENT_MARKS = set("^~˜¯−→˙¨ˆ‾ˇ`´")
LOOKALIKE_DIGIT = set("1l|Iı")  # 1 / l / | / I
FRACTION = {"<frac>", "</frac>"}
ROOT = {"<sqrt>", "</sqrt>", "<root>", "</root>"}
GROUPING = {"<g>", "</g>", "|"}
SCRIPT = {"^", "_"}


def edit_distance(a: list[str], b: list[str]) -> int:
    """Levenshtein distance between two symbol streams."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _is_letter(sym: str) -> bool:
    return len(sym) == 1 and sym.isalpha()


def kind_of_replacement(x: str, y: str) -> str:
    """The kind of one symbol read as another (x is gold, y is the reading)."""
    if x.lower() == y.lower() and x != y and _is_letter(x) and _is_letter(y):
        return "case"
    if STYLED.get(x) == y or STYLED.get(y) == x:
        return "style"
    if {x, y} & set("×") and {x, y} - {"×"} <= set("xX"):
        return "x_times"
    if x in DASHES and y in DASHES:
        return "minus_dash"
    if x in DASHES or y in DASHES:
        return "operator_bracket"
    if {x.lower(), y.lower()} == {"z", "2"}:
        return "z_2"
    digit_letter = (x.isdigit() and (_is_letter(y) or y in "|")) or (
        y.isdigit() and (_is_letter(x) or x in "|")
    )
    if digit_letter or (x in LOOKALIKE_DIGIT and y in LOOKALIKE_DIGIT):
        return "digit_letter"
    if (x in GREEK) != (y in GREEK) and (_is_letter(x) or x == "∂") and (_is_letter(y) or y == "∂"):
        return "greek_latin"
    if x in GREEK and y in GREEK:
        return "greek_greek"
    if x.isalnum() and y.isalnum():
        return "other_letter"
    return "operator_bracket"


def kinds_of_opcode(gold: list[str], pred: list[str]) -> set[str]:
    """Kinds of one difference (a run of gold symbols read as a run of other symbols)."""
    both = gold + pred
    if any(t in FRACTION for t in both):
        return {"fraction"}
    if any(t in ROOT for t in both):
        return {"root"}
    if any(t in SCRIPT for t in both):
        marks = [t for t in both if t not in SCRIPT and t not in GROUPING]
        if both and all(t in SCRIPT or t in ACCENT_MARKS for t in both) and marks:
            return {"accent"}
        if gold and not pred:
            return {"script_lost"}
        if pred and not gold:
            return {"script_added"}
        return {"script_changed"}
    if all(t in GROUPING for t in both):
        return {"structure"}
    if len(both) <= 2 and all(t in ACCENT_MARKS for t in both):
        return {"accent"}  # a bare accent mark swapped for another (a bar for a tilde)
    if not gold:
        return {"operator_bracket"} if all(not t.isalnum() for t in pred) else {"symbol_added"}
    if not pred:
        return {"operator_bracket"} if all(not t.isalnum() for t in gold) else {"symbol_dropped"}
    if len(gold) == len(pred):
        return {kind_of_replacement(x, y) for x, y in zip(gold, pred, strict=True)}
    if all(not t.isalnum() for t in both):
        return {"operator_bracket"}
    return {"multi_symbol"}


def classify(gold_symbols: list[str], pred_symbols: list[str]) -> tuple[list[str], int]:
    """(sorted kinds, edit distance) for a gold stream read as a prediction stream."""
    kinds: set[str] = set()
    matcher = difflib.SequenceMatcher(a=gold_symbols, b=pred_symbols, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            kinds |= kinds_of_opcode(gold_symbols[i1:i2], pred_symbols[j1:j2])
    return sorted(kinds, key=KINDS.index), edit_distance(gold_symbols, pred_symbols)


def _dashes(latex: str) -> list[str]:
    return [c for c in latex if c in DASHES]


def speech_of(latex: str) -> str | None:
    """What the product says for LaTeX; None when the converter or the speech code refuses it."""
    from readablestem.eval.equations import latex_to_mathml
    from readablestem.pipeline.speech import mathml_to_speech

    try:
        return mathml_to_speech(latex_to_mathml(latex))
    except Exception:
        return None


def symbols_of(latex: str) -> list[str] | None:
    from readablestem.eval.equations_symbols import symbols_of_latex

    try:
        return symbols_of_latex(latex)
    except Exception:
        return None


def classify_pair(gold_latex: str, pred_latex: str, *, gold_id: str = "") -> dict:
    """One wrong equation: kinds, edit distance, speech_equal. An empty reading is an omission."""
    record: dict = {"id": gold_id, "gold": gold_latex, "pred": pred_latex}
    gold_symbols = symbols_of(gold_latex)
    gold_speech = speech_of(gold_latex)
    if not pred_latex.strip():
        record.update(
            kinds=["omission"],
            edit_distance=len(gold_symbols or []),
            one_symbol=False,
            speech_equal=False,
            speech_gold=gold_speech,
            speech_pred="",
        )
        return record
    pred_symbols = symbols_of(pred_latex)
    pred_speech = speech_of(pred_latex)
    if gold_symbols is None or pred_symbols is None:
        kinds, distance = ["multi_symbol"], -1
    else:
        kinds, distance = classify(gold_symbols, pred_symbols)
        if not kinds and _dashes(gold_latex) != _dashes(pred_latex):
            # the symbol stream folds every dash into one minus; the speech code does not
            kinds, distance = ["minus_dash"], 1
        elif not kinds:
            kinds = ["tree_only"]  # same symbols, different tree
    same = None if gold_speech is None or pred_speech is None else gold_speech == pred_speech
    record.update(
        kinds=kinds,
        edit_distance=distance,
        one_symbol=distance == 1,
        speech_equal=same,
        speech_gold=gold_speech,
        speech_pred=pred_speech,
    )
    return record


# -------------------------------------------------------------------------------- recordings


def load_gold(gold_root: Path) -> dict[str, dict]:
    gold: dict[str, dict] = {}
    for path in sorted(gold_root.glob("*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                gold[row["id"]] = row
    return gold


def arm_dirs(arms_root: Path, arm: str | None, part: str | None) -> list[tuple[str, str, Path]]:
    found = []
    for items in sorted(arms_root.glob("*/*/items.jsonl")):
        a, p = items.parent.parent.name, items.parent.name
        if (arm is None or a == arm) and (part is None or p == part):
            found.append((a, p, items))
    return found


def analyse_arm(items_path: Path, gold: dict[str, dict]) -> dict:
    """Every wrong equation of one recorded arm and part, classified."""
    wrong: list[dict] = []
    n_gold = 0
    n_missing_gold = 0
    for line in items_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        n_gold += int(item.get("n_gold") or len(item.get("pairs", [])))
        for pair in item.get("pairs", []):
            if pair.get("exact_lenient"):
                continue
            row = gold.get(pair["gold_id"])
            if row is None:
                n_missing_gold += 1
                continue
            wrong.append(
                classify_pair(row["latex"], pair.get("pred_latex") or "", gold_id=pair["gold_id"])
            )
        for gold_id in item.get("missed_gold_ids") or []:
            row = gold.get(gold_id)
            if row is None:
                n_missing_gold += 1
                continue
            wrong.append(classify_pair(row["latex"], "", gold_id=gold_id))
    return {"n_gold": n_gold, "wrong": wrong, "gold_not_found": n_missing_gold}


def summarise(analysis: dict, examples: int) -> dict:
    wrong = analysis["wrong"]
    kinds = collections.Counter(k for r in wrong for k in r["kinds"])
    spoken = [r for r in wrong if r["speech_equal"] is True]
    distances = [r["edit_distance"] for r in wrong if r["edit_distance"] >= 0]
    by_kind: dict[str, list[dict]] = collections.defaultdict(list)
    for r in wrong:
        for k in r["kinds"]:
            if len(by_kind[k]) < examples:
                by_kind[k].append(
                    {key: r[key] for key in ("id", "gold", "pred", "edit_distance", "speech_equal")}
                )
    return {
        "n_gold": analysis["n_gold"],
        "n_wrong": len(wrong),
        "n_speech_equal": len(spoken),
        "n_one_symbol": sum(1 for r in wrong if r["one_symbol"]),
        "mean_edit_distance": round(sum(distances) / len(distances), 2) if distances else None,
        "gold_not_found": analysis["gold_not_found"],
        "kinds": {k: kinds[k] for k in KINDS if kinds[k]},
        "examples": dict(by_kind),
    }


def render_table(summaries: dict[str, dict]) -> str:
    names = list(summaries)
    width = max([len(n) for n in names] + [12])
    shown = [k for k in KINDS if any(s["kinds"].get(k) for s in summaries.values())]
    header = f"{'arm/part':<{width}}  gold  wrong  1-sym  spoken-eq  mean-ed"
    lines = [header + "".join(f"  {k[:9]:>9}" for k in shown)]
    for name, s in summaries.items():
        mean = "-" if s["mean_edit_distance"] is None else f"{s['mean_edit_distance']:.2f}"
        row = (
            f"{name:<{width}}  {s['n_gold']:>4}  {s['n_wrong']:>5}  {s['n_one_symbol']:>5}"
            f"  {s['n_speech_equal']:>9}  {mean:>7}"
        )
        lines.append(row + "".join(f"  {s['kinds'].get(k, 0):>9}" for k in shown))
    lines.append("kinds overlap: one wrong equation can have several, so columns do not add up.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms-root", type=Path, default=DEFAULT_ARMS)
    ap.add_argument("--gold-root", type=Path, default=DEFAULT_GOLD)
    ap.add_argument("--arm", help="only this arm")
    ap.add_argument("--part", help="only this part (dev, heldout, fresh)")
    ap.add_argument("--examples", type=int, default=3, help="examples per kind in the JSON")
    ap.add_argument("--json", type=Path, nargs="?", const=DEFAULT_JSON, help="write the summary")
    args = ap.parse_args(argv)

    gold = load_gold(args.gold_root)
    if not gold:
        print(f"no gold found under {args.gold_root}", file=sys.stderr)
        return 2
    arms = arm_dirs(args.arms_root, args.arm, args.part)
    if not arms:
        print(f"no recorded arms under {args.arms_root}", file=sys.stderr)
        return 2
    summaries = {
        f"{arm}/{part}": summarise(analyse_arm(items, gold), args.examples)
        for arm, part, items in arms
    }
    print(render_table(summaries))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(summaries, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
