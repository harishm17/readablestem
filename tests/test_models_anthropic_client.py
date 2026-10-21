"""AnthropicVisionClient against a scripted HTTP transport (no network, no key).

The real `anthropic` SDK builds and parses everything; only the bytes on the wire are
scripted (an `httpx2.MockTransport`), so these tests check the request the client sends and
the outcome it reads from each kind of answer.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, Field

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.models import anthropic_client
from readablestem.models.anthropic_client import (
    FALLBACK_BETA,
    WORKSPACE_HEADER,
    AnthropicVisionClient,
)
from readablestem.models.base import UNTRUSTED_NOTICE, VisionRequest
from readablestem.models.openai_client import PNG_SIGNATURE
from readablestem.models.pricing import (
    ANTHROPIC_MODELS,
    THINKING_ALLOWANCE_TOKENS,
    cost_usd,
    estimate_max_cost_usd,
)
from readablestem.pipeline.equation_step import EquationOutput
from readablestem.pipeline.figure_step import FigureOutput
from readablestem.pipeline.image_page import ImagePageOutput
from readablestem.pipeline.table_step import TableOutput

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


def test_request_carries_images_instructions_schema_and_cache_breakpoint():
    wire = Wire(message_body())
    request = make_request(context_text="near the equation", max_output_tokens=600)
    parsed, call = run(wire, request)
    assert parsed == Out(latex="x^2", confidence=0.9)
    assert call.outcome == "ok"

    body = wire.body()
    assert body["model"] == HAIKU
    assert body["max_tokens"] == 600 + THINKING_ALLOWANCE_TOKENS  # thinking counts toward it
    # The stable instructions, and the untrusted-text notice after them, are the cached prefix.
    assert body["system"] == [
        {
            "type": "text",
            "text": f"{INSTRUCTIONS}\n\n{UNTRUSTED_NOTICE}",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    (message,) = body["messages"]
    assert message["role"] == "user"
    image, text = message["content"]
    assert image == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(PNG).decode(),
        },
    }
    # Untrusted document text is fenced; its fixed notice is in the cached system block.
    assert text["type"] == "text"
    assert text["text"] == "<document_text>\nnear the equation\n</document_text>"
    assert body["system"][0]["text"].endswith(UNTRUSTED_NOTICE)
    # Structured output through output_config, with the effort set explicitly.
    config = body["output_config"]
    assert config["effort"] == "low"
    assert config["format"]["type"] == "json_schema"
    assert config["format"]["schema"]["required"] == ["latex", "confidence"]
    assert config["format"]["schema"]["additionalProperties"] is False


def test_request_leaves_out_what_the_newer_models_reject():
    for model in (HAIKU, SONNET, OPUS):
        wire = Wire(message_body(model=model))
        run(wire, make_request(model=model))
        body = wire.body()
        for name in ("temperature", "top_p", "top_k", "thinking", "tool_choice", "tools"):
            assert name not in body, f"{model}: {name}"
        assert body["messages"][-1]["role"] == "user"  # no assistant prefill


def test_effort_follows_the_role_of_the_model():
    settings = make_settings(anthropic_effort_main="high", anthropic_effort_cheap="low")
    efforts = {}
    for model in (HAIKU, OPUS):
        wire = Wire(message_body(model=model))
        run(wire, make_request(model=model), settings=settings)
        efforts[model] = wire.body()["output_config"]["effort"]
    assert efforts == {HAIKU: "low", OPUS: "high"}


def test_text_only_request_is_allowed_and_empty_request_is_not():
    wire = Wire(message_body())
    run(wire, make_request(images=[], context_text="only text"))
    assert [b["type"] for b in wire.body()["messages"][0]["content"]] == ["text"]
    with pytest.raises(ValueError, match="at least one image"):
        run(Wire(message_body()), make_request(images=[]))
    with pytest.raises(ValueError, match="not PNG"):
        run(Wire(message_body()), make_request(images=[b"JPEGDATA"]))


def test_a_missing_key_is_a_config_error_before_anything_is_reserved():
    budget = make_budget()
    client = AnthropicVisionClient(make_settings(anthropic_api_key=None), budget)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        client.run(make_request(), Out)
    assert budget.calls == 0


def test_key_is_sent_as_the_api_key_header_and_never_logged(caplog: pytest.LogCaptureFixture):
    wire = Wire(message_body())
    with caplog.at_level(logging.DEBUG):
        run(wire)
    assert wire.requests[0].headers["x-api-key"] == "sk-ant-test"
    assert "sk-ant-test" not in caplog.text


# --- the workspace header -------------------------------------------------------------


def test_workspace_header_is_sent_on_every_request_when_set(no_backoff):
    wire = Wire(httpx2.Response(500, json=error_body()), message_body())
    settings = make_settings(anthropic_workspace_id="wrkspc_012abc", model_max_retries=1)
    run(wire, settings=settings)
    assert len(wire.requests) == 2
    assert [r.headers[WORKSPACE_HEADER] for r in wire.requests] == ["wrkspc_012abc"] * 2


def test_workspace_header_is_not_sent_otherwise():
    wire = Wire(message_body())
    run(wire)
    assert WORKSPACE_HEADER not in wire.requests[0].headers
    # Blank means unset (the environment's `NAME=`), not an empty header.
    wire = Wire(message_body())
    run(wire, settings=make_settings(anthropic_workspace_id="  "))
    assert WORKSPACE_HEADER not in wire.requests[0].headers


def test_workspace_header_reaches_the_beta_endpoint_too():
    wire = Wire(message_body(model=OPUS))
    run(wire, make_request(model=OPUS), settings=make_settings(anthropic_workspace_id="wrkspc_1"))
    assert wire.requests[0].url.path == "/v1/messages"
    assert wire.requests[0].headers[WORKSPACE_HEADER] == "wrkspc_1"


# --- the refusal fallback -------------------------------------------------------------


def test_opus_and_sonnet_ask_for_the_server_side_refusal_fallback():
    for model in (OPUS, SONNET):
        wire = Wire(message_body(model=model))
        run(wire, make_request(model=model))
        assert wire.body()["fallbacks"] == "default", model
        assert FALLBACK_BETA in wire.requests[0].headers["anthropic-beta"]


def test_haiku_has_no_fallback_and_the_setting_turns_it_off():
    wire = Wire(message_body())
    run(wire, make_request(model=HAIKU))
    assert "fallbacks" not in wire.body()
    assert "anthropic-beta" not in wire.requests[0].headers

    wire = Wire(message_body(model=OPUS))
    run(wire, make_request(model=OPUS), settings=make_settings(anthropic_refusal_fallback=False))
    assert "fallbacks" not in wire.body()
    assert "anthropic-beta" not in wire.requests[0].headers


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


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (EquationOutput, {"is_equation": True, "latex": "x^2", "confidence": 0.93}),
        (
            FigureOutput,
            {"figure_type": "chart", "decorative": False, "alt": "A chart.", "confidence": 0.8},
        ),
        (
            TableOutput,
            {
                "is_data_table": True,
                "caption": "Table 1",
                "rows": [
                    {
                        "cells": [
                            {
                                "text": "T (K)",
                                "latex": "",
                                "header": "col",
                                "row_span": 1,
                                "col_span": 1,
                            }
                        ]
                    }
                ],
            },
        ),
        (
            ImagePageOutput,
            {
                "blocks": [
                    {
                        "kind": "paragraph",
                        "x0": 100,
                        "y0": 100,
                        "x1": 900,
                        "y1": 160,
                        "confidence": "high",
                        "level": 0,
                        "marker": "",
                        "runs": [{"kind": "text", "value": "Speed is distance over time."}],
                        "latex": "",
                        "label": "",
                        "description": "",
                        "caption": "",
                        "rows": [],
                    }
                ]
            },
        ),
    ],
)
def test_each_step_schema_goes_out_as_a_json_schema_and_comes_back_validated(schema, payload):
    wire = Wire(message_body(text=json.dumps(payload)))
    parsed, call = run(wire, make_request(model=HAIKU), schema)
    assert call.outcome == "ok"
    assert parsed == schema.model_validate(payload)
    sent = wire.body()["output_config"]["format"]["schema"]
    assert sent["type"] == "object" and sent["additionalProperties"] is False


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


def test_the_original_schema_still_checks_what_the_transformed_one_cannot():
    # The wire schema loses `ge`/`le` (they become description text); the client keeps them.
    wire = Wire(message_body(text='{"latex": "x", "confidence": 7}'))
    parsed, call = run(wire)
    assert parsed is None and call.outcome == "invalid_output"
    assert call.cost_usd > 0  # the call was billed


def test_invalid_json_or_no_text_is_invalid_output():
    for text in ("not json", '{"latex": "x"}', "", None):
        parsed, call = run(Wire(message_body(text=text)))
        assert parsed is None and call.outcome == "invalid_output", text


# --- stop reasons ---------------------------------------------------------------------


def test_refusal_is_refused_and_still_billed(caplog: pytest.LogCaptureFixture):
    details = {"type": "refusal", "category": "cyber", "explanation": SECRET}
    wire = Wire(message_body(text=None, stop_reason="refusal", stop_details=details))
    with caplog.at_level(logging.WARNING, logger="readablestem.models.anthropic_client"):
        parsed, call = run(wire)
    assert parsed is None and call.outcome == "refused"
    assert call.cost_usd > 0
    assert SECRET not in caplog.text
    record = next(r for r in caplog.records if r.getMessage() == "model_call")
    assert record.a11y_fields["code"] == "cyber"  # the category, never the explanation


def test_refusal_without_details_is_still_refused():
    wire = Wire(message_body(text=None, stop_reason="refusal", stop_details=None))
    parsed, call = run(wire)
    assert (parsed, call.outcome) == (None, "refused")


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


def test_a_non_message_200_is_an_api_error():
    for answer in (
        httpx2.Response(200, text="<html>gateway</html>"),
        httpx2.Response(200, json=None),
        httpx2.Response(200, json=[1, 2]),
        httpx2.Response(200, json=error_body("overloaded_error")),
    ):
        parsed, call = run(Wire(answer))
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


@pytest.mark.parametrize("status", [500, 502, 529])
def test_server_errors_are_retried_then_reported_as_api_error(status, no_backoff):
    budget = make_budget()
    wire = Wire(httpx2.Response(status, json=error_body("overloaded_error")))
    parsed, call = run(wire, settings=make_settings(model_max_retries=2), budget=budget)
    assert (parsed, call.outcome) == (None, "api_error")
    assert len(wire.requests) == 3 and len(no_backoff) == 2
    assert call.cost_usd == 0.0 and budget.spent_usd == 0.0  # an error status is not billed
    assert budget.calls == 3  # every resend reserved on its own


def test_a_bad_request_is_not_retried(no_backoff):
    wire = Wire(httpx2.Response(400, json=error_body("invalid_request_error")))
    parsed, call = run(wire, settings=make_settings(model_max_retries=2))
    assert (parsed, call.outcome) == (None, "api_error")
    assert len(wire.requests) == 1 and no_backoff == []


def test_a_long_retry_after_goes_to_the_fallback_model_not_a_wait(no_backoff):
    wire = Wire(
        httpx2.Response(429, json=error_body("rate_limit_error"), headers={"retry-after": "600"})
    )
    _, call = run(wire, settings=make_settings(model_max_retries=2))
    assert call.outcome == "api_error" and len(wire.requests) == 1


def test_a_connection_error_is_retried(no_backoff):
    wire = Wire(httpx2.ConnectError("refused"), message_body())
    parsed, _ = run(wire, settings=make_settings(model_max_retries=1))
    assert parsed is not None and len(wire.requests) == 2


def test_a_timeout_after_sending_is_charged_its_full_reservation():
    budget = make_budget()
    estimate = estimate_max_cost_usd(HAIKU, n_images=1, text_chars=0, max_output_tokens=1024)
    wire = Wire(httpx2.ReadTimeout("slow"))
    parsed, call = run(wire, budget=budget)
    assert (parsed, call.outcome) == (None, "timeout")
    assert call.cost_usd == pytest.approx(estimate)
    assert budget.spent_usd == pytest.approx(estimate)


def test_a_connect_failure_never_reached_the_provider_and_costs_nothing():
    budget = make_budget()
    parsed, call = run(Wire(httpx2.ConnectError("refused")), budget=budget)
    assert (parsed, call.outcome) == (None, "api_error")
    assert budget.spent_usd == 0.0


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


def test_usage_maps_onto_the_model_call_and_its_cost():
    usage = usage_block(
        input_tokens=200,
        read=600,
        write=200,
        output=50,
        output_tokens_details={"thinking_tokens": 30},
    )
    wire = Wire(message_body(model=OPUS, usage=usage))
    _, call = run(wire, make_request(model=OPUS))
    # input_tokens is the whole prompt; the cache read and write are inside it.
    assert call.input_tokens == 1000
    assert call.cached_input_tokens == 600
    assert call.cache_creation_input_tokens == 200
    assert (call.output_tokens, call.reasoning_tokens) == (50, 30)  # thinking is output
    # 200 uncached at $4, 600 read at $0.20, 200 written at $5, 50 out at $20 per 1M.
    assert call.cost_usd == pytest.approx((200 * 4 + 600 * 0.2 + 200 * 5 + 50 * 20) / 1e6)


def test_cache_creation_is_left_out_of_the_json_when_zero():
    _, call = run(Wire(message_body(usage=usage_block(100, 0, 0, 5))))
    assert "cache_creation_input_tokens" not in call.model_dump()
    _, call = run(Wire(message_body()))
    assert call.model_dump()["cache_creation_input_tokens"] == 200


def test_prices_of_the_three_models():
    assert cost_usd(OPUS, 1_000_000, 0, 0) == pytest.approx(4.0)
    assert cost_usd(OPUS, 1_000_000, 1_000_000, 0) == pytest.approx(0.20)
    assert cost_usd(OPUS, 0, 0, 1_000_000) == pytest.approx(20.0)
    assert cost_usd(SONNET, 1_000_000, 0, 1_000_000) == pytest.approx(12.0)
    assert cost_usd(SONNET, 1_000_000, 1_000_000, 0) == pytest.approx(0.20)
    assert cost_usd(HAIKU, 100_000, 0, 100_000) == pytest.approx(0.01 + 0.05)
    assert cost_usd(OPUS, 1_000_000, 0, 0, cache_write_tokens=1_000_000) == pytest.approx(5.0)


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


def test_reservation_covers_the_refusal_fallback_models_rates():
    estimate = estimate_max_cost_usd(OPUS, n_images=1, text_chars=0, max_output_tokens=1024)
    prompt = 3500 + 6500
    output = 1024 + THINKING_ALLOWANCE_TOKENS
    at_fallback_rates = (prompt * 6.25 + output * 25.0) / 1e6  # write 1.25x of $5, out $25
    assert estimate >= at_fallback_rates - 1e-12


def test_reservation_uses_haiku_long_card_when_the_prompt_could_pass_100k():
    chars = 250_000  # 125k tokens at the generous 0.5 per character
    short = estimate_max_cost_usd(HAIKU, n_images=0, text_chars=1000, max_output_tokens=100)
    long = estimate_max_cost_usd(HAIKU, n_images=0, text_chars=chars, max_output_tokens=100)
    assert long > 4 * short
    assert long >= ((3500 + chars // 2) * 0.625 + (100 + THINKING_ALLOWANCE_TOKENS) * 2.5) / 1e6


def test_a_charge_never_exceeds_the_reservation_for_a_big_but_legal_call(caplog):
    caplog.set_level(logging.WARNING)
    usage = usage_block(0, 0, 3500 + 6500, 1024 + THINKING_ALLOWANCE_TOKENS)
    wire = Wire(message_body(model=OPUS, usage=usage, stop_reason="max_tokens"))
    _, call = run(wire, make_request(model=OPUS))
    estimate = estimate_max_cost_usd(OPUS, n_images=1, text_chars=0, max_output_tokens=1024)
    assert call.cost_usd <= estimate
    assert "model_call_over_reservation" not in caplog.text
