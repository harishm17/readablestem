"""AnthropicVisionClient against a scripted HTTP transport (no network, no key).

The real `anthropic` SDK builds and parses everything; only the bytes on the wire are
scripted (an `httpx2.MockTransport`), so these tests check the request the client sends and
the outcome it reads from each kind of answer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, Field

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.models import anthropic_client
from readablestem.models.anthropic_client import (
    AnthropicVisionClient,
)
from readablestem.models.base import VisionRequest
from readablestem.models.openai_client import PNG_SIGNATURE
from readablestem.models.pricing import (
    ANTHROPIC_MODELS,
    THINKING_ALLOWANCE_TOKENS,
    cost_usd,
    estimate_max_cost_usd,
)

PNG = PNG_SIGNATURE + b"tiny test image"
INSTRUCTIONS = "Transcribe the equation in the image as LaTeX."
SECRET = "SECRET-DOCUMENT-TEXT"
HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"


class Out(BaseModel):
    latex: str
    confidence: float = Field(ge=0, le=1)


OK_TEXT = '{"latex": "x^2", "confidence": 0.9}'


def make_settings(**kw: Any) -> Settings:
    kw.setdefault("model_provider", "anthropic")
    kw.setdefault("anthropic_api_key", "sk-ant-test")
    kw.setdefault("model_max_retries", 0)
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def make_budget(cap: float = 1.0, max_calls: int = 100) -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=max_calls)


def make_request(**kw: Any) -> VisionRequest:
    fields: dict[str, Any] = {
        "step": "equation",
        "model": HAIKU,
        "prompt_version": "equation-test",
        "instructions": INSTRUCTIONS,
        "images": [PNG],
    }
    fields.update(kw)
    return VisionRequest(**fields)


def usage_block(
    input_tokens: int = 200, read: int = 600, write: int = 200, output: int = 50, **extra: Any
) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
        "output_tokens": output,
        **extra,
    }


def message_body(
    *,
    text: str | None = OK_TEXT,
    stop_reason: str = "end_turn",
    stop_details: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    model: str = HAIKU,
    content: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A Messages API body as the server sends it."""
    blocks: list[dict[str, Any]] = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text is not None:
        blocks.append({"type": "text", "text": text})
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content if content is not None else blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage or usage_block(),
    }


def error_body(kind: str = "api_error", message: str = SECRET) -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message}}


Handler = Callable[[httpx2.Request], httpx2.Response]


class Wire:
    """Scripted transport: plays the given answers in order and keeps what was sent."""

    def __init__(self, *answers: httpx2.Response | Exception | dict[str, Any]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, dict):
            return httpx2.Response(200, json=answer)
        return answer

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))


def run(
    wire: Wire,
    request: VisionRequest | None = None,
    schema: type[BaseModel] = Out,
    *,
    settings: Settings | None = None,
    budget: JobBudget | None = None,
):
    client = AnthropicVisionClient(
        settings or make_settings(), budget or make_budget(), http_client=wire.client()
    )
    return client.run(request or make_request(), schema)


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(anthropic_client, "_sleep", delays.append)
    return delays


# --- the request ----------------------------------------------------------------------


def test_key_is_sent_as_the_api_key_header_and_never_logged(caplog: pytest.LogCaptureFixture):
    wire = Wire(message_body())
    with caplog.at_level(logging.DEBUG):
        run(wire)
    assert wire.requests[0].headers["x-api-key"] == "sk-ant-test"
    assert "sk-ant-test" not in caplog.text


# --- the workspace header -------------------------------------------------------------


# --- the refusal fallback -------------------------------------------------------------


def test_a_fallback_that_served_the_request_is_charged_for_every_attempt():
    usage = usage_block(
        input_tokens=100,
        read=0,
        write=0,
        output=10,
        iterations=[
            {
                "type": "message",
                "model": OPUS,
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            {
                "type": "fallback_message",
                "model": "claude-opus-4-8",  # not in the price table
                "input_tokens": 100,
                "output_tokens": 40,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        ],
    )
    body = message_body(
        model="claude-opus-4-8",
        usage=usage,
        content=[
            {"type": "text", "text": '{"latex": "declined partial", "confidence": 0.1}'},
            {
                "type": "fallback",
                "from": {"model": OPUS},
                "to": {"model": "claude-opus-4-8"},
            },
            {"type": "text", "text": OK_TEXT},
        ],
    )
    parsed, call = run(Wire(body), make_request(model=OPUS))
    assert parsed == Out(latex="x^2", confidence=0.9)  # the text after the switch point
    assert (call.input_tokens, call.output_tokens) == (200, 50)
    # The declined attempt at claude-opus-5-5 rates, the unpriced fallback model at the
    # fallback ceiling ($5 / $25 per 1M tokens).
    expected = (100 * 4 + 10 * 20) / 1e6 + (100 * 5 + 40 * 25) / 1e6
    assert call.cost_usd == pytest.approx(expected)


# --- structured output with each step's schema ----------------------------------------


def test_a_request_can_ask_for_a_longer_timeout_than_the_clients():
    # The image page pass writes a whole page (ADR 0007): its sends are given more time.
    seen: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx2.Response(200, json=message_body())

    settings = make_settings(model_timeout_s=60)
    client = AnthropicVisionClient(
        settings,
        make_budget(),
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    client.run(make_request(), Out)
    client.run(make_request(timeout_s=240.0), Out)
    assert seen == [60.0, 240.0]


def test_invalid_json_or_no_text_is_invalid_output():
    for text in ("not json", '{"latex": "x"}', "", None):
        parsed, call = run(Wire(message_body(text=text)))
        assert parsed is None and call.outcome == "invalid_output", text


# --- stop reasons ---------------------------------------------------------------------


def test_max_tokens_is_invalid_output_with_its_usage_recorded():
    wire = Wire(
        message_body(
            text='{"latex": "x^2", "conf',
            stop_reason="max_tokens",
            usage=usage_block(100, 0, 0, 9000, output_tokens_details={"thinking_tokens": 8500}),
        )
    )
    parsed, call = run(wire)
    assert parsed is None and call.outcome == "invalid_output"
    assert (call.input_tokens, call.output_tokens, call.reasoning_tokens) == (100, 9000, 8500)
    assert call.cost_usd == pytest.approx(cost_usd(HAIKU, 100, 0, 9000))


def test_a_reply_that_is_not_a_finished_answer_is_an_api_error():
    wire = Wire(message_body(text=None, stop_reason="pause_turn"))
    parsed, call = run(wire)
    assert (parsed, call.outcome) == (None, "api_error")


# --- errors, retries, charges ---------------------------------------------------------


def test_rate_limit_is_retried_after_the_wait_the_server_asked_for(no_backoff):
    wire = Wire(
        httpx2.Response(429, json=error_body("rate_limit_error"), headers={"retry-after": "3"}),
        message_body(),
    )
    parsed, call = run(wire, settings=make_settings(model_max_retries=2))
    assert parsed is not None and call.outcome == "ok"
    assert no_backoff == [3.0]
    assert len(wire.requests) == 2


def test_a_timeout_after_sending_is_charged_its_full_reservation():
    budget = make_budget()
    estimate = estimate_max_cost_usd(HAIKU, n_images=1, text_chars=0, max_output_tokens=1024)
    wire = Wire(httpx2.ReadTimeout("slow"))
    parsed, call = run(wire, budget=budget)
    assert (parsed, call.outcome) == (None, "timeout")
    assert call.cost_usd == pytest.approx(estimate)
    assert budget.spent_usd == pytest.approx(estimate)


def test_errors_never_log_the_provider_message(caplog: pytest.LogCaptureFixture, no_backoff):
    wire = Wire(httpx2.Response(500, json=error_body("api_error", SECRET)))
    with caplog.at_level(logging.DEBUG):
        run(wire, settings=make_settings(model_max_retries=1))
    assert SECRET not in caplog.text
    lines = [r for r in caplog.records if r.getMessage() == "model_call"]
    assert lines[0].a11y_fields["status"] == 500
    assert lines[0].a11y_fields["code"] == "api_error"


def test_budget_refusal_sends_nothing():
    wire = Wire(message_body())
    with pytest.raises(BudgetExceeded):
        run(wire, budget=make_budget(cap=1e-9))
    assert wire.requests == []


# --- usage to cost --------------------------------------------------------------------


def test_haiku_switches_rate_card_above_100k_prompt_tokens():
    short = cost_usd(HAIKU, 100_000, 0, 1000)
    assert short == pytest.approx((100_000 * 0.10 + 1000 * 0.50) / 1e6)
    long = cost_usd(HAIKU, 100_001, 0, 1000)
    assert long == pytest.approx((100_001 * 0.50 + 1000 * 2.50) / 1e6)
    # The cached part counts toward the prompt length, and reads/writes use the long card.
    assert cost_usd(HAIKU, 150_000, 100_000, 0, 10_000) == pytest.approx(
        (40_000 * 0.50 + 100_000 * 0.05 + 10_000 * 0.625) / 1e6
    )


# --- the reservation is an upper bound ------------------------------------------------


@pytest.mark.parametrize("model", sorted(ANTHROPIC_MODELS))
@pytest.mark.parametrize("n_images", [0, 1, 3])
def test_reservation_covers_the_worst_billable_call(model, n_images):
    max_output = 1024
    text_chars = 4000
    estimate = estimate_max_cost_usd(
        model, n_images=n_images, text_chars=text_chars, max_output_tokens=max_output
    )
    # The most a call can bill: every image at its largest (4784 tokens at 2576 px, with
    # the tokenizer's 1.35x), instructions plus schema of 3500 tokens, the text at a
    # dense 0.5 token per character, all of it written to the cache, and the output
    # limit (thinking included) used up.
    prompt = 3500 + n_images * 6500 + text_chars // 2
    worst = cost_usd(model, prompt, 0, max_output + THINKING_ALLOWANCE_TOKENS, prompt)
    assert estimate >= worst - 1e-12
