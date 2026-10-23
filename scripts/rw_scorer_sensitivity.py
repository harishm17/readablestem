"""Real-world set: how much the primary score moves if notation-equivalent answers were not wrong.

    uv run python scripts/rw_scorer_sensitivity.py --part dev --rs v1 [--rs-b base-main]

The registered scorer (`eval/equations.py`) is not changed and every number of the reports is
from it. Two kinds of answer that print the same are marked wrong by it, and this script counts
them for every arm so the size of the artefact, and whether it favours an arm, is on the page:

- styled letters: `\\mathcal R` (no braces) is `<mi mathvariant="script">R</mi>`, `\\mathcal{R}` is
  U+211B, a letter Unicode drew in Letterlike Symbols before it made the Mathematical Alphanumeric
  block; the scorer folds the block's letters to base letter plus `mathvariant` and not these;
- upright words: `\\text{in}` is one `<mtext>`, `\\mathrm{in}` and `\\rm in` are one `<mi>` per
  letter, `\\operatorname{in}` is another token.

For each arm: the aligned equations the registered scorer calls wrong that this variant calls
right, in points of the arm's gold. Nothing here calls a model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from readablestem.eval import equations as eq
from readablestem.eval import pipeline_eval as pe
from readablestem.gold import arxiv_gold as ag
from readablestem.pipeline import validate
from readablestem.pipeline.equation_step import clean_latex

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
LETTERLIKE = (
    ("SCRIPT CAPITAL ", "script"),
    ("SCRIPT SMALL ", "script"),
    ("DOUBLE-STRUCK CAPITAL ", "double-struck"),
    ("BLACK-LETTER CAPITAL ", "fraktur"),
)


def _char_variant_with_letterlike(ch: str) -> str | None:
    name = unicodedata.name(ch, "")
    for prefix, variant in LETTERLIKE:
        if name.startswith(prefix):
            return variant
    return _registered_char_variant(ch)


_registered_char_variant = eq._char_variant


def upright_words(latex: str) -> str:
    """`\\mathrm{in}`, `\\textrm`, `{\\rm in}` and `\\operatorname{in}` written `\\text{in}`."""
    latex = re.sub(r"\\mathrm\s*\{([^{}]*)\}", r"\\text{\1}", latex)
    latex = re.sub(r"\\textrm\s*\{([^{}]*)\}", r"\\text{\1}", latex)
    latex = re.sub(r"\{\\rm\s+([^{}]*)\}", r"\\text{\1}", latex)
    return re.sub(r"\\operatorname\s*\{([^{}]*)\}", r"\\text{\1}", latex)


def converted(latex: str) -> str:
    return validate.safe_latex_to_mathml(clean_latex(latex), "inline")


def right(pred: str, ref: str) -> bool:
    try:
        return eq.score_mathml(converted(pred), ag.to_mathml(ref, False)).exact_lenient
    except Exception:  # an unconvertible variant is not right
        return False


def flips(items: list[dict], gold: dict[str, dict]) -> tuple[int, int, int, int]:
    """(gold, styled letters, upright words, either): aligned equations the registered scorer
    calls wrong that become right. Both sides are converted afresh, so a pair the stored gold
    and the current converter disagree on (the gold was made by an older converter) is not
    counted as a flip of the variant."""
    n_gold = styled = upright = either = 0
    for item in items:
        n_gold += item["n_gold"]
        for pair in item["pairs"]:
            if pair["exact_lenient"] or not pair.get("pred_latex"):
                continue
            pred, ref = pair["pred_latex"], gold[pair["gold_id"]]["latex"]
            if right(pred, ref):
                continue
            eq._char_variant = _char_variant_with_letterlike
            try:
                by_letters = right(pred, ref)
            finally:
                eq._char_variant = _registered_char_variant
            by_words = right(upright_words(pred), upright_words(ref))
            styled += by_letters
            upright += by_words
            either += by_letters or by_words
    return n_gold, styled, upright, either


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", choices=["dev", "heldout", "fresh", "fresh2"], default="dev")
    parser.add_argument("--rs", default="rs")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    runs = ROOT / "runs" / args.part
    gold = {
        r["id"]: r for r in pe.load_gold(ROOT / "gold" / args.part) if r.get("kind") == "equation"
    }
    arms = {
        "ReadableSTEM": runs / args.rs / "items.jsonl",
        "Haiku": runs / "chat" / "chatbot-haiku-purdue.items.jsonl",
        "Opus": runs / "chat" / "chatbot-opus-purdue.items.jsonl",
    }
    result = {}
    print("| arm | gold | styled letters | upright words | either |")
    print("|---|---|---|---|---|")
    for name, path in arms.items():
        if not path.is_file():
            continue
        items = [json.loads(line) for line in path.read_text().splitlines()]
        n_gold, styled, upright, either = flips(items, gold)
        result[name] = {"gold": n_gold, "styled": styled, "upright": upright, "either": either}
        print(
            f"| {name} | {n_gold} | +{styled} ({100 * styled / n_gold:.2f} pts) "
            f"| +{upright} ({100 * upright / n_gold:.2f} pts) "
            f"| +{either} ({100 * either / n_gold:.2f} pts) |"
        )
    if args.out:
        args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
