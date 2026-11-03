"""Real-world set: the notation-neutral reading of the primary score (defined in the registration).

    uv run python scripts/realworld_report.py --part fresh3 --tag after --neutral

The registered primary, `exact_lenient`, reads `\\text{in}` (one `mtext`), `\\mathrm{in}` (one `mi`
per letter) and `\\operatorname{in}` (an `mo`) as three different trees, and authors write all
three. The neutral reading folds them: an aligned equation is exact in it when it is exact in the
primary, or when it is exact once the upright-word commands of both sides, the prediction's LaTeX
and the gold's, are written alike. Folding rule, applied to every arm:

    `\\mathrm{w}`, `\\textrm{w}`, `\\mbox{w}`, `\\textnormal{w}`, `\\operatorname{w}`,
    `\\operatorname*{w}` and `{\\rm w}`, with no brace inside `w`, become `\\text{w}`.

Nothing else is folded (not styled letters, not bold, not spacing), the aligner and the scorer are
those of the primary, and the detection, span and silent-error counts are not touched; only
`exact_lenient` of an aligned pair can change, and only from wrong to right.
"""

from __future__ import annotations

import re

from readablestem.eval import equations as eq
from readablestem.gold import arxiv_gold as ag
from readablestem.pipeline import validate
from readablestem.pipeline.equation_step import clean_latex

_WORD = re.compile(
    r"\\(?:mathrm|textrm|mbox|textnormal|operatorname\*?|text)\s*\{([^{}]*)\}|\{\\rm\s+([^{}]*)\}"
)


def fold(latex: str) -> str:
    """The folding rule of the module docstring."""
    return _WORD.sub(
        lambda m: "\\text{" + (m.group(1) if m.group(1) is not None else m.group(2)) + "}", latex
    )


def _converted(latex: str) -> str:
    return validate.safe_latex_to_mathml(clean_latex(latex), "inline")


def neutral_exact(pred_latex: str | None, gold_latex: str, registered: bool) -> bool:
    if registered or not pred_latex:
        return registered
    try:
        reference = ag.to_mathml(fold(gold_latex), False)
        return eq.score_mathml(_converted(fold(pred_latex)), reference).exact_lenient
    except Exception:  # an unconvertible fold is not right
        return False


def refold(item: dict, gold: dict[str, dict]) -> dict:
    """The item with `exact_lenient` of each aligned pair read neutrally."""
    pairs = [
        p
        | {
            "exact_lenient": neutral_exact(
                p.get("pred_latex"), gold[p["gold_id"]]["latex"], p["exact_lenient"]
            )
        }
        for p in item["pairs"]
    ]
    return item | {"pairs": pairs}
