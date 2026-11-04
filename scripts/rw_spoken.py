"""Real-world set: the spoken reading, the primary score of fresh5 (exploratory on earlier sets).

    uv run python scripts/realworld_report.py --part fresh4 --tag after --spoken

The registered metric `exact_lenient` compares MathML trees, so two transcriptions a screen-reader
user hears the same can score differently: `\\mathbf{x}` and `\\boldsymbol{x}` are both "bold x",
`\\text{in}` and `\\mathrm{in}` both "in". The spoken reading asks what the student hears: an
aligned equation is right in it when it is right in the primary, or when ReadableSTEM's speech
(`pipeline.speech.mathml_to_speech`, no course readings) says the prediction and the gold in the
same words. Applied to every arm alike, with the aligner and scorer of the primary; detection, span
and silent-error counts are not touched, and only `exact_lenient` of an aligned pair can change,
only from wrong to right. ReadableSTEM's speech stands in for a screen reader's: it is not NVDA
with MathCAT, and the two can word an expression differently.

Two LaTeX strings that print alike must be spoken alike. The converter and the speech rules read
some spellings of one print differently (`\\mathrm{in}` letter by letter against `\\text{in}` as a
word, `\\min` "minimum" against `\\text{min}` "min", gold `\\textbf{R}` without its bold, `\\bigl(`
not converted, `...` as periods), so a pair is also spoken-equal when the speech is the same after
`math_canonical.speakable_latex` has written each print one way on both sides. The as-written
reading first repairs what the converter reads wrongly
(`math_canonical.repair_converter_gaps`: `\\textbf` bold, sized delimiters). The folded reading
only adds to the as-written one. The folds never join two different prints
(`tests/test_math_canonical_spoken.py` holds pairs that must stay different).

Two sides have no LaTeX to fold. OpenStax gold (strata B and D of fresh5) is MathML only: its
speech is `mathml_to_speech` of the gold MathML, compared with the prediction as written. A
prediction written as MathML (a chatbot that answers in `<math>`) is spoken from its MathML and
compared with the gold as written. Neither side is folded then, so such a pair can only be
credited when the two are said in the same words without `speakable_latex`.
"""

from __future__ import annotations

import re
from functools import cache

from readablestem.gold import arxiv_gold as ag
from readablestem.pipeline import validate
from readablestem.pipeline.equation_step import clean_latex
from readablestem.pipeline.math_canonical import repair_converter_gaps, speakable_latex
from readablestem.pipeline.speech import mathml_to_speech


@cache
def _gold_speech(latex: str) -> str | None:
    try:
        return mathml_to_speech(ag.to_mathml(repair_converter_gaps(latex), False))
    except Exception:  # gold the speech cannot read never matches
        return None


_MATH_ELEMENT = re.compile(r"\s*<(?:[A-Za-z][\w.-]*:)?math\b", re.IGNORECASE)


def _is_markup(pred: str) -> bool:
    """A prediction written as a MathML element (`<math>...`, `<m:math>...`) rather than LaTeX
    (`<x` is LaTeX: "less than x")."""
    return _MATH_ELEMENT.match(pred) is not None


@cache
def _mathml_speech(mathml: str) -> str | None:
    try:
        return mathml_to_speech(mathml)
    except Exception:  # markup the speech cannot read never matches
        return None


def _pred_speech(latex: str) -> str | None:
    if _is_markup(latex):
        return _mathml_speech(latex)
    try:
        return mathml_to_speech(
            validate.safe_latex_to_mathml(clean_latex(repair_converter_gaps(latex)), "inline")
        )
    except Exception:  # an unconvertible prediction is not right
        return None


def _alike_speech(latex: str) -> str | None:
    """The speech of LaTeX by one path for the gold and every arm: `speakable_latex`, then the
    gold's own converter step (author-command preparation, a command it does not know refused)
    and the product's speech."""
    try:
        return mathml_to_speech(ag.to_mathml(speakable_latex(latex), False))
    except Exception:  # whatever cannot be read never matches
        return None


@cache
def _gold_alike(latex: str) -> str | None:
    return _alike_speech(latex)


def spoken_exact(pred_latex: str | None, gold_latex: str, registered: bool) -> bool:
    if registered or not pred_latex:
        return registered
    said = _pred_speech(pred_latex)
    if said is not None and said == _gold_speech(gold_latex):
        return True
    if _is_markup(pred_latex):  # MathML has no LaTeX to fold
        return False
    alike = _alike_speech(pred_latex)
    return alike is not None and alike == _gold_alike(gold_latex)


def spoken_exact_mathml_gold(pred_latex: str | None, gold_mathml: str, registered: bool) -> bool:
    """`spoken_exact` for gold that is MathML only (OpenStax): the prediction as written
    against the gold's MathML spoken as it is, with no fold on either side."""
    if registered or not pred_latex:
        return registered
    said = _pred_speech(pred_latex)
    return said is not None and said == _mathml_speech(gold_mathml)


def _right(pair: dict, gold: dict) -> bool:
    if gold.get("latex"):
        return spoken_exact(pair.get("pred_latex"), gold["latex"], pair["exact_lenient"])
    return spoken_exact_mathml_gold(
        pair.get("pred_latex"), gold.get("mathml") or "", pair["exact_lenient"]
    )


def refold(item: dict, gold: dict[str, dict]) -> dict:
    """The item with `exact_lenient` of each aligned pair read by what is spoken."""
    pairs = [p | {"exact_lenient": _right(p, gold[p["gold_id"]])} for p in item["pairs"]]
    return item | {"pairs": pairs}
