"""Model routing and fallback (WP1 implements)."""

from __future__ import annotations

from a11y_stem.config import Settings
from a11y_stem.core.types import ModelCall
from a11y_stem.models.base import T, VisionClient, VisionRequest

RETRYABLE = {"api_error", "timeout", "invalid_output", "refused"}


def call_with_fallback(
    client: VisionClient, request: VisionRequest, schema: type[T], *, settings: Settings
) -> tuple[T | None, list[ModelCall]]:
    """Try request.model; on a RETRYABLE outcome try settings.fallback_for[model] once.

    Returns the parsed result (or None) and every attempt's ModelCall. BudgetExceeded
    propagates unchanged.
    """
    raise NotImplementedError("WP1")
