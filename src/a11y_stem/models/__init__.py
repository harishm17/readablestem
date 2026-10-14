"""Model layer: everything that talks to a vision model.

- `base`: the contract (`VisionRequest`, `VisionClient`, `fence_untrusted`)
- `openai_client`: `OpenAIVisionClient` (Responses API, spend reservation, outcomes)
- `anthropic_client`: `AnthropicVisionClient` (Messages API, same contract and accounting)
- `fake`: `FakeVisionClient` (deterministic, offline)
- `router`: `call_with_fallback` (main model, then the other one once)
- `verifier`, `decisions_client`: the second automated check (OpenAI Decisions API)
- `pricing`, `prompts`: prices and versioned prompt text

`make_vision_client` is the one place that picks the real or the offline client, so the
web app, the CLI and the eval harness build clients the same way. The OpenAI SDK is
imported only when a real client is built: importing this package for its types stays
cheap and needs no key.
"""

from __future__ import annotations

import copy

from a11y_stem.config import Settings
from a11y_stem.core.budget import JobBudget
from a11y_stem.models.base import VisionClient, VisionRequest, fence_untrusted
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.router import (
    RETRYABLE,
    FallbackBudgetExceeded,
    call_with_fallback,
    calls_made,
)
from a11y_stem.models.verifier import any_check_enabled, make_verifier

__all__ = [
    "OFFLINE_DEFAULTS",
    "RETRYABLE",
    "FakeVisionClient",
    "FallbackBudgetExceeded",
    "VisionClient",
    "VisionRequest",
    "call_with_fallback",
    "calls_made",
    "fence_untrusted",
    "make_live_client",
    "make_vision_client",
]

# What the offline client answers for each step. Confidence 0.5 sits between the abstain
# and auto-accept thresholds, so every offline fix lands on the review screen rather than
# passing as a real answer. A table has no confidence: its one placeholder cell is not
# the PDF's words, so the table step's checks make it a decision too. Steps without an
# entry (e.g. "judge") come back "refused".
OFFLINE_DEFAULTS: dict[str, dict | None] = {  # same shape as FakeVisionClient.defaults
    "equation": {"is_equation": True, "latex": "x", "confidence": 0.5},
    "figure": {
        "figure_type": "other",
        "decorative": False,
        "alt": "Figure (offline placeholder)",
        "confidence": 0.5,
    },
    "table": {
        "is_data_table": True,
        "caption": "",
        "rows": [
            {
                "cells": [
                    {
                        "text": "Table (offline placeholder)",
                        "latex": "",
                        "header": "col",
                        "row_span": 1,
                        "col_span": 1,
                    }
                ]
            }
        ],
    },
}
# With long descriptions on, the figure step asks for more fields (`figure_long.md`).
_OFFLINE_LONG_FIGURE = {
    "long_description": "",
    "long_description_unsure": False,
    "data_table": None,
    "values_printed": False,
    "colour_only": False,
}


def _offline_figure(defaults: dict[str, dict | None]):
    """The offline figure answer for either figure prompt: the default, plus the long
    prompt's fields (empty) when that prompt was sent."""

    def answer(request: VisionRequest) -> dict | None:
        base = defaults.get("figure")
        if base is None or not request.prompt_version.startswith("figure_long-"):
            return base
        return {**base, **_OFFLINE_LONG_FIGURE}

    return answer


def make_vision_client(
    settings: Settings, budget: JobBudget, *, fake: bool = False
) -> VisionClient:
    """The client for one job, charging `budget`.

    `fake=True` gives an offline `FakeVisionClient` with `OFFLINE_DEFAULTS` that still
    reserves and settles against `budget`, so spend limits behave as they would for real.
    Otherwise the client of `settings.model_provider`: an `OpenAIVisionClient` or an
    `AnthropicVisionClient`; it reads the API key on its first call, not here.

    The OpenAI client carries the job's second automated check (`models.verifier`, the
    Decisions API; offline, the fake) on the same budget, unless both uses of it are
    switched off in the settings. The check is OpenAI's, so with the Anthropic provider
    there is none (`any_check_enabled` is False).
    """
    verifier = make_verifier(settings, budget, fake=fake) if any_check_enabled(settings) else None
    if fake:
        defaults = copy.deepcopy(OFFLINE_DEFAULTS)
        return FakeVisionClient(
            defaults=defaults,
            responders={"figure": _offline_figure(defaults)},
            budget=budget,
            verifier=verifier,
        )
    if settings.model_provider == "anthropic":
        from a11y_stem.models.anthropic_client import AnthropicVisionClient

        return AnthropicVisionClient(settings, budget)
    from a11y_stem.models.openai_client import OpenAIVisionClient

    return OpenAIVisionClient(settings, budget, verifier=verifier)


def make_live_client(
    settings: Settings, budget: JobBudget, *, second_check: bool = True
) -> VisionClient:
    """The real client of `settings.model_provider` for an eval, proof or CLI run.

    Unlike `make_vision_client` this checks the provider's API key now, so a run fails
    before any work rather than on its first call (`config.ConfigError`). `second_check`
    False leaves the OpenAI client without the Decisions API check (a judge alone).
    """
    settings.require_provider_key()
    if settings.model_provider == "anthropic":
        from a11y_stem.models.anthropic_client import AnthropicVisionClient

        return AnthropicVisionClient(settings, budget)
    from a11y_stem.models.openai_client import OpenAIVisionClient

    verifier = (
        make_verifier(settings, budget) if second_check and any_check_enabled(settings) else None
    )
    return OpenAIVisionClient(settings, budget, verifier=verifier)
