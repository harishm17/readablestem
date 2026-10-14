"""The model-cost settings (T3-22 to T3-24, T3-29, T2-19): each is off by default, changes
only what it names, and leaves the full-size picture to review and the second check."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx2
import pytest
from PIL import Image

from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import Status
from a11y_stem.models.anthropic_client import AnthropicVisionClient
from a11y_stem.models.base import UNTRUSTED_NOTICE, VisionRequest
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.images import shrink_png
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


# ------------------------------------------------------------- T3-23 notice, context


def test_the_notice_moves_to_the_cached_system_block_when_asked():
    before = sdk_body(claude(), request())
    assert before["system"][0]["text"] == "Transcribe."
    assert before["messages"][0]["content"][-1]["text"].startswith(UNTRUSTED_NOTICE)

    after = sdk_body(claude(anthropic_notice_in_system=True), request())
    assert after["system"][0]["text"] == f"Transcribe.\n\n{UNTRUSTED_NOTICE}"
    assert after["system"][0]["cache_control"] == {"type": "ephemeral"}
    sent = after["messages"][0]["content"][-1]["text"]
    assert UNTRUSTED_NOTICE not in sent
    assert sent == "<document_text>\nthe text near it\n</document_text>"


def test_the_system_block_is_the_same_with_and_without_document_text():
    settings = claude(anthropic_notice_in_system=True)
    with_text = sdk_body(settings, request())["system"]
    without = sdk_body(settings, request(context_text=None))["system"]
    assert with_text == without


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


def test_the_model_gets_the_reduced_figure_and_the_second_check_the_full_one():
    big = png_of(1600, 900)
    client = FakeVisionClient(defaults={"figure": fig()})
    describe(
        figure_element(),
        big,
        client=client,
        settings=claude(figure_model_max_edge_px=800),
    )
    sent = client.requests[0].images[0]
    assert Image.open(io.BytesIO(sent)).size == (800, 450)
    untouched = FakeVisionClient(defaults={"figure": fig()})
    describe(figure_element(), big, client=untouched, settings=claude())
    assert untouched.requests[0].images[0] == big


# -------------------------------------------------------- T3-29 display equations


def test_display_equations_start_on_the_cheap_model_only_when_asked():
    answers = {OPUS: eq("x", 0.99), HAIKU: eq("x", 0.97)}

    def run(**kw: Any) -> FakeVisionClient:
        client = FakeVisionClient(responders={"equation": lambda req: answers[req.model]})
        transcribe(
            equation_element(display="block", text="x"), PNG, client=client, settings=claude(**kw)
        )
        return client

    assert [r.model for r in run().requests] == [OPUS]
    assert [r.model for r in run(equation_display_first_cheap=True).requests] == [HAIKU]


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


def test_the_accept_bar_is_per_model_and_defaults_to_the_common_one():
    settings = claude(accept_confidence_by_model={SONNET: 0.92})
    assert settings.accept_confidence_for(SONNET) == 0.92
    assert settings.accept_confidence_for(OPUS) == settings.auto_accept_confidence == 0.85
    assert settings.accept_confidence_for(None) == 0.85


def test_a_sonnet_answer_below_its_own_bar_needs_review_while_opus_is_accepted():
    for model, status in ((SONNET, Status.NEEDS_REVIEW), (OPUS, Status.ACCEPTED)):
        settings = claude(anthropic_main_model=model, accept_confidence_by_model={SONNET: 0.92})
        client = FakeVisionClient(defaults={"equation": eq("x", 0.90)})
        fix = transcribe(
            equation_element(display="block", text="x"), PNG, client=client, settings=settings
        )
        assert fix.status is status, model
    for model, status in ((SONNET, Status.NEEDS_REVIEW), (OPUS, Status.ACCEPTED)):
        settings = claude(anthropic_main_model=model, accept_confidence_by_model={SONNET: 0.97})
        client = FakeVisionClient(defaults={"figure": fig(0.95)})
        assert describe(figure_element(), PNG, client=client, settings=settings).status is status


def test_the_request_body_is_valid_json_for_the_batch_api():
    body = sdk_body(claude(), request())
    json.dumps(body)
