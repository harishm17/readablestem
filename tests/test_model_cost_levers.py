"""The model-cost settings (T3-22 to T3-24, T3-29, T2-19): each is off by default, changes
only what it names, and leaves the full-size picture to review and the second check."""

from __future__ import annotations

import io
from typing import Any

import httpx2
import pytest
from PIL import Image

from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.models.anthropic_client import AnthropicVisionClient
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.openai_client import PNG_SIGNATURE
from a11y_stem.pipeline.equation_step import EquationOutput, transcribe
from a11y_stem.pipeline.figure_step import describe
from test_steps_support import (
    equation_element,
    figure_element,
    make_settings,
    use_fallback_router,
)

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"
PNG = b"\x89PNG crop"
ALT = "Line graph of position versus time: position rises steadily, then levels off at 5 km."


def claude(**kw: Any):
    return make_settings(model_provider="anthropic", anthropic_api_key="sk-ant-x", **kw)


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def eq(latex: str = "x", confidence: float = 0.95) -> dict:
    return {"is_equation": True, "latex": latex, "confidence": confidence}


def fig(confidence: float = 0.95) -> dict:
    return {"figure_type": "chart", "decorative": False, "alt": ALT, "confidence": confidence}


def png_of(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


# ------------------------------------------------------------------ T3-22 effort


def test_the_step_effort_reaches_the_request_and_is_unset_by_default():
    asked = FakeVisionClient(defaults={"equation": eq()})
    transcribe(equation_element(), PNG, client=asked, settings=claude())
    assert asked.requests[0].effort is None
    low = FakeVisionClient(defaults={"equation": eq()})
    transcribe(
        equation_element(), PNG, client=low, settings=claude(anthropic_effort_equation="low")
    )
    assert low.requests[0].effort == "low"
    figs = FakeVisionClient(defaults={"figure": fig()})
    describe(figure_element(), PNG, client=figs, settings=claude(anthropic_effort_figure="low"))
    assert figs.requests[0].effort == "low"


def sdk_body(settings, request: VisionRequest) -> dict[str, Any]:
    budget = JobBudget(InMemorySpendGuard(cap_usd=1), max_usd=1, max_calls=10)
    client = AnthropicVisionClient(settings, budget, http_client=httpx2.Client())
    return client.batch_params(request, EquationOutput)


def request(**kw: Any) -> VisionRequest:
    fields: dict[str, Any] = {
        "step": "equation",
        "model": OPUS,
        "prompt_version": "equation-test",
        "instructions": "Transcribe.",
        "images": [PNG_SIGNATURE + b"x"],
        "context_text": "the text near it",
    }
    fields.update(kw)
    return VisionRequest(**fields)


def test_the_request_effort_wins_over_the_model_effort():
    assert sdk_body(claude(), request())["output_config"]["effort"] == "medium"
    assert sdk_body(claude(), request(model=HAIKU))["output_config"]["effort"] == "low"
    assert sdk_body(claude(), request(effort="low"))["output_config"]["effort"] == "low"
