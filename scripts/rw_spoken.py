"""Real-world set: the spoken reading of the primary score (exploratory; not in a registration yet).

    uv run python scripts/realworld_report.py --part fresh4 --tag after --spoken

The primary, `exact_lenient`, compares MathML trees, so two transcriptions a screen-reader user
hears the same can score differently: `\\mathbf{x}` and `\\boldsymbol{x}` are both "bold x",
`\\text{in}` and `\\mathrm{in}` both "in". The spoken reading asks what the student hears: an
aligned equation is right in it when it is right in the primary, or when ReadableSTEM's speech
(`pipeline.speech.mathml_to_speech`, no course readings) says the prediction and the gold in the
same words. Applied to every arm alike, with the aligner and scorer of the primary; detection, span
and silent-error counts are not touched, and only `exact_lenient` of an aligned pair can change,
only from wrong to right. ReadableSTEM's speech stands in for a screen reader's: it is not NVDA
with MathCAT, and the two can word an expression differently.
"""

from __future__ import annotations

from functools import cache

from readablestem.gold import arxiv_gold as ag
from readablestem.pipeline import validate
from readablestem.pipeline.equation_step import clean_latex
from readablestem.pipeline.speech import mathml_to_speech


@cache
def _gold_speech(latex: str) -> str | None:
    try:
        return mathml_to_speech(ag.to_mathml(latex, False))
    except Exception:  # gold the speech cannot read never matches
        return None


def _pred_speech(latex: str) -> str | None:
    try:
        return mathml_to_speech(validate.safe_latex_to_mathml(clean_latex(latex), "inline"))
    except Exception:  # an unconvertible prediction is not right
        return None


def spoken_exact(pred_latex: str | None, gold_latex: str, registered: bool) -> bool:
    if registered or not pred_latex:
        return registered
    said = _pred_speech(pred_latex)
    return said is not None and said == _gold_speech(gold_latex)


def refold(item: dict, gold: dict[str, dict]) -> dict:
    """The item with `exact_lenient` of each aligned pair read by what is spoken."""
    pairs = [
        p
        | {
            "exact_lenient": spoken_exact(
                p.get("pred_latex"), gold[p["gold_id"]]["latex"], p["exact_lenient"]
            )
        }
        for p in item["pairs"]
    ]
    return item | {"pairs": pairs}
