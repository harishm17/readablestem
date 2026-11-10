"""The Claude spend reservation: sized from the real crop and a bounded thinking allowance.

It used to be a flat 6500 image tokens plus an 8,192-token thinking allowance: about $0.32
for one claude-opus-5-5 call, above the $0.30 a one-page job was allowed, so no call could be
made on a one-page upload and every element failed with budget_job_usd.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.models.base import VisionRequest
from readablestem.models.pricing import (
    ANTHROPIC_MODELS,
    IMAGE_TOKEN_MARGIN,
    THINKING_ALLOWANCE_TOKENS,
    cost_usd,
    estimate_max_cost_usd,
)
from readablestem.pipeline import equation_step
from readablestem.web.worker import job_caps

OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-5-5"

# Measured with the free token-counting endpoint on claude-opus-5-5, 2026-10-14: the tokens
# one grey PNG of this size adds to a prompt.
MEASURED = [
    (50, 50, 7),
    (200, 100, 35),
    (400, 300, 168),
    (800, 600, 641),
    (1200, 900, 1422),
    (1600, 1200, 2497),
    (2048, 2048, 4764),
    (2576, 2576, 4764),
    (3000, 1000, 2855),
    (4000, 4000, 4764),
]


def png(width: int, height: int) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def test_a_one_page_claude_job_can_hold_four_worst_case_calls_by_default():
    claude = Settings(_env_file=None, model_provider="anthropic")  # type: ignore[call-arg]
    usd, _ = job_caps(claude, 1)
    worst = estimate_max_cost_usd(OPUS, n_images=1, text_chars=700, max_output_tokens=1024)
    assert 4 * worst <= usd


@pytest.mark.parametrize("model", sorted(ANTHROPIC_MODELS))
@pytest.mark.parametrize(("width", "height", "_"), MEASURED[::3])
@pytest.mark.parametrize("limit", [512, 1024, 4096])
def test_the_reservation_is_still_an_upper_bound(model, width, height, _, limit):
    """Whatever the crop, the most a call can bill is below the reservation: the measured
    image tokens with the tokenizer margin, the instructions and schema (3500), the text
    at a dense 0.5 token per character, all written to the cache, and the output limit
    plus the whole thinking allowance used."""
    image = png(width, height)
    text_chars = 1500
    estimate = estimate_max_cost_usd(
        model, n_images=1, text_chars=text_chars, max_output_tokens=limit, images=[image]
    )
    measured = next(m for w, h, m in MEASURED if (w, h) == (width, height))
    prompt = 3500 + int(measured * IMAGE_TOKEN_MARGIN) + text_chars // 2
    worst = cost_usd(model, prompt, 0, limit + THINKING_ALLOWANCE_TOKENS, prompt)
    assert estimate >= worst - 1e-12


def test_the_client_reserves_for_the_image_it_sends():
    reserved: list[float] = []

    class Sdk:
        def with_options(self, **_: object):
            return self

    class Spy:
        def reserve(self, amount: float, **_: object):
            reserved.append(amount)
            raise RuntimeError("stop before sending")

    request = VisionRequest(
        step="equation",
        model=OPUS,
        prompt_version="p",
        instructions="read it",
        images=[png(400, 300)],
        max_output_tokens=1024,
    )
    from readablestem.models.anthropic_client import AnthropicVisionClient

    settings = Settings(_env_file=None, model_provider="anthropic", anthropic_api_key="sk-a")  # type: ignore[call-arg]
    client = AnthropicVisionClient(settings, Spy(), client=Sdk())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="stop before sending"):
        client.run(request, equation_step.EquationOutput)
    flat = estimate_max_cost_usd(OPUS, n_images=1, text_chars=0, max_output_tokens=1024)
    assert 0 < reserved[0] < flat - 0.02  # the flat image maximum costs 6,000 more tokens
