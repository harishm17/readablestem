"""Model layer: everything that talks to a vision model.

- `base`: the contract (`VisionRequest`, `VisionClient`, `fence_untrusted`)
- `openai_client`: `OpenAIVisionClient` (Responses API, spend reservation, outcomes)
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
    "make_vision_client",
]

# What the offline client answers for each step. Confidence 0.5 sits between the abstain
# and auto-accept thresholds, so every offline fix lands on the review screen rather than
# passing as a real answer. Steps without an entry (e.g. "judge") come back "refused".
OFFLINE_DEFAULTS: dict[str, dict | None] = {  # same shape as FakeVisionClient.defaults
    "equation": {"is_equation": True, "latex": "x", "confidence": 0.5},
    "figure": {
        "figure_type": "other",
        "decorative": False,
        "alt": "Figure (offline placeholder)",
        "confidence": 0.5,
    },
}


def make_vision_client(
    settings: Settings, budget: JobBudget, *, fake: bool = False
) -> VisionClient:
    """The client for one job, charging `budget`.

    `fake=True` gives an offline `FakeVisionClient` with `OFFLINE_DEFAULTS` that still
    reserves and settles against `budget`, so spend limits behave as they would for real.
    Otherwise an `OpenAIVisionClient`; it reads the API key on its first call, not here.

    The client carries the job's second automated check (`models.verifier`, the
    Decisions API; offline, the fake) on the same budget, unless both uses of it are
    switched off in the settings.
    """
    verifier = make_verifier(settings, budget, fake=fake) if any_check_enabled(settings) else None
    if fake:
        return FakeVisionClient(
            defaults=copy.deepcopy(OFFLINE_DEFAULTS), budget=budget, verifier=verifier
        )
    from a11y_stem.models.openai_client import OpenAIVisionClient

    return OpenAIVisionClient(settings, budget, verifier=verifier)
