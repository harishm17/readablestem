"""The instructions and answer shape of the two readers (Claude and OpenAI).

The text is constant: the crop is the only thing that varies, so no document text is ever
sent with it (there is nothing to inject through). The answer is a JSON object, read with
`candidates_from_json`; anything else is `invalid_output`. Not under `models/prompts/`:
those files are measured by the eval gate, and no step uses this one yet. The probe that
measures the readers decides whether a reader is used at all.
"""

from __future__ import annotations

import json
from typing import Any, Final

from readablestem.models.math_readers.base import (
    Candidate,
    clamp_confidence,
    clean_latex,
    unique,
)

PROMPT_VERSION: Final = "math_reader_v1"

# Output limit of one read: the JSON answer (Claude's thinking is allowed on top).
REPLY_TOKENS: Final = 1024

INSTRUCTIONS: Final = """\
You read one handwritten or printed mathematical expression from an image and write it as \
LaTeX, for a tool that helps instructors make course material accessible to screen readers.

Write exactly what is on the page. Do not solve, simplify, correct or complete the \
expression, and keep the author's own notation. Write the expression only: no dollar signs, \
no \\begin{equation}, no words of your own. If a symbol is unclear, give your best reading \
first and the other plausible readings after it.

Answer with a JSON object with one key, "candidates": a list of one to three objects, best \
reading first, each with "latex" (the LaTeX string) and "confidence" (your confidence that \
this reading is exactly right, from 0 to 1). If the image holds no mathematical expression, \
answer with an empty list."""

# The Claude reader's own instructions. They differ from the page pass's and from the
# OpenAI reader's on purpose, so that its mistakes are not the same mistakes: it sees one
# expression and nothing else (no page, no neighbouring text), it is told to settle each
# symbol before it writes the whole, and it is told which pairs handwriting confuses.
CLAUDE_INSTRUCTIONS: Final = """\
You are given a tight, high-resolution picture of one handwritten mathematical expression. \
The picture is all you have: nothing around it on the page is shown, so do not guess from \
what the expression might be part of. Your reading is compared with an independent reading \
of the same expression, and the comparison only helps if yours is made from the marks alone.

Work from the structure outward. First decide what the layout is (a fraction, a root, a \
sum or integral with limits, a matrix, superscripts and subscripts, a plain line). Then read \
each symbol, left to right and top to bottom within a structure, and decide each one before \
you write the whole. Handwriting confuses some pairs, so look twice at them: l, 1 and |; \
0, o and O; x and \\times; v and \\nu; u and \\mu; a and \\alpha; e and \\epsilon; \
the Greek letters beta, gamma and the letter B, y; a dot, a comma and a prime; a minus sign \
and the bar of a fraction. Keep the author's own notation and write what is there: do not \
solve, simplify, correct or complete the expression.

Give the reading you judge most likely first, then up to two other readings that a careful \
person could also see, each one a full expression, not a fragment. Write each as LaTeX only: \
no dollar signs, no \\begin{equation}, no words of your own. Give "confidence", from 0 to 1, \
as your chance that the reading is exactly right. If the picture holds no mathematical \
expression, answer with an empty list.

Answer with a JSON object with one key, "candidates", a list of objects with "latex" and \
"confidence"."""

USER_TEXT: Final = "Read the expression in this image."

ANSWER_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["latex", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def candidates_from_json(text: object) -> list[Candidate] | None:
    """The candidates in a model's JSON answer; None when it is not the expected shape."""
    if not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    items = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    found: list[Candidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        latex = clean_latex(item.get("latex"))
        if latex is not None:
            found.append(Candidate(latex, clamp_confidence(item.get("confidence"))))
    return unique(found)
