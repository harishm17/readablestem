"""Figure step (WP3 implements): classify the figure and write short alt text.

Short alt text only in the beta (long descriptions are out of 2026). Abstains rather than
guessing: a figure the model cannot read confidently becomes ABSTAINED and keeps a
visible "needs a description" flag in the output.
"""

from __future__ import annotations

from a11y_stem.config import Settings
from a11y_stem.core.types import Element, Fix
from a11y_stem.models.base import VisionClient

PROMPT_VERSION = "figure-v1"


def describe(
    element: Element,
    png: bytes,
    *,
    client: VisionClient,
    settings: Settings,
    context_text: str | None = None,
) -> Fix:
    raise NotImplementedError("WP3")
