"""Real-world set: how authors write upright words and identifiers in maths, and what a
convention is worth.

    uv run python scripts/rw_notation_survey.py survey --part dev2
    uv run python scripts/rw_notation_survey.py simulate --part dev2 --rs m1 [--part heldout
    --rs rs ...]

`survey`: for the gold of a part, the count of each upright construct of the author's LaTeX
(`\\mathrm`, `\\text`, `\\textrm`, `\\operatorname`, `{\\rm ..}`, `\\mbox`), by what it holds
(one letter,
a word, a phrase) and whether it is in a script. `simulate`: rewrites the `\\text{..}` of
ReadableSTEM's recorded LaTeX to `\\mathrm{..}` (everywhere, outside scripts only, in scripts
only), converts again
and scores against the stored gold with the registered scorer, so the points a convention would
be worth are
measured on answers already recorded. Nothing here calls a model; the scorer is not changed.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from readablestem.eval import equations as eq
from readablestem.eval import pipeline_eval as pe
from readablestem.pipeline import validate
from readablestem.pipeline.equation_step import clean_latex

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
UPRIGHT = re.compile(r"\\(mathrm|text|textrm|operatorname\*?|mbox|rm|textnormal)\s*\{([^{}]*)\}")
TEXT = re.compile(r"\\text\s*\{([^{}]*)\}")


def survey(part: str) -> None:
    counts: Counter = Counter()
    seen: dict = defaultdict(Counter)
    total = with_construct = 0
    for path in sorted(glob.glob(str(ROOT / "gold" / part / "*.jsonl"))):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("kind") != "equation":
                continue
            total += 1
            latex = record["latex"]
            found = False
            for match in UPRIGHT.finditer(latex):
                command, body = match.group(1).rstrip("*"), match.group(2)
                before = latex[: match.start()]
                in_script = bool(re.search(r"[_^]\s*\{?\s*$", before)) or bool(
                    re.search(r"[_^]\s*\{[^{}]*$", before)
                )
                if re.fullmatch(r"[A-Za-z]", body):
                    what = "one letter"
                elif re.fullmatch(r"[A-Za-z]{2,}", body):
                    what = "a word"
                elif re.fullmatch(r"[A-Za-z][A-Za-z ]*", body):
                    what = "a phrase"
                else:
                    what = "other"
                key = (what, command, "in a script" if in_script else "not in a script")
                counts[key] += 1
                seen[key][body] += 1
                found = True
            with_construct += found
    print(f"{part}: {total} gold equations, {with_construct} with an upright construct")
    print("| holds | command | where | count | examples |")
    print("|---|---|---|---|---|")
    for (what, command, where), n in sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1])):
        examples = ", ".join(b for b, _ in seen[(what, command, where)].most_common(4))
        print(f"| {what} | {command} | {where} | {n} | {examples} |")


def in_script(text: str, pos: int) -> bool:
    stack: list[bool] = []
    for i, ch in enumerate(text[:pos]):
        if ch == "{":
            j = i - 1
            while j >= 0 and text[j] == " ":
                j -= 1
            stack.append(j >= 0 and text[j] in "_^")
        elif ch == "}" and stack:
            stack.pop()
    if any(stack):
        return True
    j = pos - 1
    while j >= 0 and text[j] == " ":
        j -= 1
    return j >= 0 and text[j] in "_^"


def rewrite(latex: str, mode: str) -> str:
    def replace(match: re.Match[str]) -> str:
        scripted = in_script(latex, match.start())
        if mode == "all" or (mode == "main" and not scripted) or (mode == "script" and scripted):
            return rf"\mathrm{{{match.group(1)}}}"
        return match.group(0)

    return TEXT.sub(replace, latex)


def convert(latex: str) -> str:
    return validate.safe_latex_to_mathml(clean_latex(latex), "inline")


def simulate(part: str, rs: str) -> None:
    gold = {r["id"]: r for r in pe.load_gold(ROOT / "gold" / part) if r.get("kind") == "equation"}
    for mode, label in (
        ("all", "every \\text -> \\mathrm"),
        ("main", "outside scripts only"),
        ("script", "in scripts only"),
    ):
        pairs = base = new = up = down = n_gold = 0
        for line in (
            (ROOT / "runs" / part / rs / "items.jsonl").read_text(encoding="utf-8").splitlines()
        ):
            item = json.loads(line)
            item = item | item.get("table_maths_reading", {})
            n_gold += item["n_gold"]
            for pair in item["pairs"]:
                latex = pair.get("pred_latex")
                if not latex or not TEXT.search(latex):
                    continue
                reference = gold[pair["gold_id"]]["mathml"]
                try:
                    before = eq.score_mathml(convert(latex), reference).exact_lenient
                    after = eq.score_mathml(convert(rewrite(latex, mode)), reference).exact_lenient
                except Exception:
                    continue
                pairs += 1
                base += before
                new += after
                up += after and not before
                down += before and not after
        print(
            f"{part} {rs}: {label}: {pairs} aligned equations with \\text, exact {base} -> {new} "
            f"(+{up} -{down}), net {100 * (up - down) / n_gold:+.2f} points of {n_gold}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["survey", "simulate"])
    parser.add_argument("--part", default="dev2")
    parser.add_argument("--rs", default="m1")
    args = parser.parse_args()
    if args.command == "survey":
        survey(args.part)
    else:
        simulate(args.part, args.rs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
