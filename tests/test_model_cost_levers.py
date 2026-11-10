"""The model-cost settings (T3-22 to T3-24, T3-29, T2-19): each is off by default, changes
only what it names, and leaves the full-size picture to review and the second check."""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from readablestem.models.base import VisionRequest
from readablestem.models.fake import FakeVisionClient
from readablestem.models.images import shrink_png
from readablestem.models.openai_client import PNG_SIGNATURE
from readablestem.pipeline.equation_step import transcribe
from test_steps_support import (
    equation_element,
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


# ------------------------------------------------------------- T3-23 notice, context


def test_context_goes_only_with_the_escalation_when_asked():
    cheap = {OPUS: eq("x", 0.99), HAIKU: eq("x", 0.5)}
    client = FakeVisionClient(responders={"equation": lambda req: cheap[req.model]})
    settings = claude(equation_context_on_escalation=True)
    transcribe(
        equation_element(display="inline", text="x"),
        PNG,
        client=client,
        settings=settings,
        context_text="the sentence around it",
    )
    assert [(r.model, r.context_text) for r in client.requests] == [
        (HAIKU, None),
        (OPUS, "the sentence around it"),
    ]
    # off by default: the cheap model's first reading has it too
    plain = FakeVisionClient(responders={"equation": lambda req: cheap[req.model]})
    transcribe(
        equation_element(display="inline", text="x"),
        PNG,
        client=plain,
        settings=claude(),
        context_text="the sentence around it",
    )
    assert [r.context_text for r in plain.requests] == ["the sentence around it"] * 2


# ------------------------------------------------------------------ T3-24 downscale


def test_shrink_png_scales_the_long_side_and_never_up():
    big = png_of(2000, 1000)
    small = shrink_png(big, 800)
    assert Image.open(io.BytesIO(small)).size == (800, 400)
    assert shrink_png(big, None) is big
    assert shrink_png(small, 800) is small
    assert shrink_png(b"not a png", 800) == b"not a png"


# -------------------------------------------------------- T3-29 display equations


def test_an_unsure_cheap_answer_to_a_display_equation_goes_to_the_main_model():
    answers = {OPUS: eq("x^2", 0.99), HAIKU: eq("x", 0.5)}
    client = FakeVisionClient(responders={"equation": lambda req: answers[req.model]})
    fix = transcribe(
        equation_element(display="block", text="x2"),
        PNG,
        client=client,
        settings=claude(equation_display_first_cheap=True),
    )
    assert [r.model for r in client.requests] == [HAIKU, OPUS]
    assert fix.equation is not None and fix.equation.latex == "x^2"


# ------------------------------------------------------------ T2-19 accept per model
