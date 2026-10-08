"""Equation step (WP3 implements): crop image -> LaTeX -> validated MathML -> speech.

Routing: simple inline candidates start on the cheap model; display equations and
anything the cheap model is unsure about (confidence < settings.auto_accept_confidence or
a failed error-level check) escalate to the main model. Each model call goes through
`models.router.call_with_fallback`, so an outage on one model falls back to the other.
"""

from __future__ import annotations

from a11y_stem.config import Settings
from a11y_stem.core.types import Element, Fix
from a11y_stem.models.base import VisionClient

PROMPT_VERSION = "equation-v1"


def transcribe(
    element: Element,
    png: bytes,
    *,
    client: VisionClient,
    settings: Settings,
    context_text: str | None = None,
) -> Fix:
    raise NotImplementedError("WP3")
