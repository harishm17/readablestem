"""The instructions and answer shape of the two readers (Claude and OpenAI).

The text is constant: the crop is the only thing that varies, so no document text is ever
sent with it (there is nothing to inject through). The answer is a JSON object, read with
`candidates_from_json`; anything else is `invalid_output`. The two instruction texts are
files under `models/prompts/` (`math_reader.md`, `math_reader_claude.md`), so a change to one
is a prompt change for the eval guard (`scripts/eval_guard.py`) now that the pipeline uses a
reader (`pipeline.image_page_reconcile`).
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
from readablestem.models.prompts import load_prompt

PROMPT_VERSION: Final = "math_reader_v1"

# Output limit of one read: the JSON answer (Claude's thinking is allowed on top).
REPLY_TOKENS: Final = 1024

INSTRUCTIONS: Final = load_prompt("math_reader").text.rstrip("\n")

# The Claude reader's own instructions. They differ from the page pass's and from the
# OpenAI reader's on purpose, so that its mistakes are not the same mistakes: it sees one
# expression and nothing else (no page, no neighbouring text), it is told to settle each
# symbol before it writes the whole, and it is told which pairs handwriting confuses.
CLAUDE_INSTRUCTIONS: Final = load_prompt("math_reader_claude").text.rstrip("\n")

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
