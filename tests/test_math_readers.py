"""Reader B adapters (models/math_readers): fake transports only, nothing leaves the process."""

from __future__ import annotations

import base64
import logging
from typing import Any

import pytest

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget, Reservation
from readablestem.models.math_readers import (
    ReadRequest,
    build_readers,
    engine,
    prompt,
)
from readablestem.models.math_readers.claude_reader import ClaudeReader
from readablestem.models.math_readers.fake import FakeTransport, json_reply
from readablestem.models.math_readers.openai_vision import OpenAIReader
from readablestem.models.math_readers.transport import HttpReply, TransportError
from readablestem.models.pricing import UnknownModelError
from readablestem.observability.logging import FIELDS_ATTR

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SECRET_LATEX = "\\frac{SECRETCONTENT}{2}"


def make_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "anthropic_api_key": "sk-ant-test",
        "openai_api_key": "sk-openai-test",
        "model_max_retries": 2,
    }
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[call-arg]


class RecordingBudget(JobBudget):
    def __init__(self, cap: float = 1.0, max_calls: int = 1) -> None:
        super().__init__(guard=InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=max_calls)
        self.reserved: list[float] = []
        self.settled: list[float] = []

    def reserve(self, amount_usd: float, *, count_call: bool = True) -> Reservation:
        reservation = super().reserve(amount_usd, count_call=count_call)
        self.reserved.append(amount_usd)
        return reservation

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        self.settled.append(actual_usd)
        super().settle(reservation, actual_usd)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(engine, "_sleep", delays.append)
    return delays


ANSWER = {
    "candidates": [
        {"latex": "$\\frac{1}{2}$", "confidence": 0.9},
        {"latex": "\\frac{1}{2}", "confidence": 0.4},  # same after cleaning: dropped
        {"latex": "\\frac{7}{2}", "confidence": 3},  # clamped
    ]
}


def claude_message(text: str | None = None, **over: Any) -> HttpReply:
    body: dict[str, Any] = {
        "type": "message",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text if text is not None else _dump(ANSWER)}],
        "usage": {"input_tokens": 1000, "output_tokens": 100},
    }
    body.update(over)
    return json_reply(body)


def openai_response(text: str | None = None, **over: Any) -> HttpReply:
    body: dict[str, Any] = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": text if text is not None else _dump(ANSWER)}
                ],
            }
        ],
        "usage": {"input_tokens": 1000, "output_tokens": 100},
    }
    body.update(over)
    return json_reply(body)


def _dump(obj: object) -> str:
    import json

    return json.dumps(obj)


def claude(script: list, budget: JobBudget | None = None, **kw: Any):
    transport = FakeTransport(script)
    budget = budget or RecordingBudget()
    return ClaudeReader(make_settings(**kw), budget, transport=transport), transport, budget


def openai(script: list, budget: JobBudget | None = None, **kw: Any):
    transport = FakeTransport(script)
    budget = budget or RecordingBudget()
    return OpenAIReader(make_settings(**kw), budget, transport=transport), transport, budget


READERS = [claude, openai]


# ------------------------------------------------------------------ request and answer


def test_request_must_be_a_png_of_bounded_size():
    with pytest.raises(ValueError):
        ReadRequest(b"not a png")
    with pytest.raises(ValueError):
        ReadRequest(b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024))


@pytest.mark.parametrize("make,ok", [(claude, claude_message), (openai, openai_response)])
def test_candidates_are_cleaned_deduplicated_and_clamped(make, ok):
    reader, _, _budget = make([ok()])
    result = reader.read(ReadRequest(PNG))
    assert result.call.outcome == "ok"
    assert [(c.latex, c.confidence) for c in result.candidates] == [
        ("\\frac{1}{2}", 0.9),
        ("\\frac{7}{2}", 1.0),
    ]
    assert result.call.sends == 1
    assert result.call.cost_usd > 0
    assert (result.call.input_tokens, result.call.output_tokens) == (1000, 100)


@pytest.mark.parametrize("make,ok", [(claude, claude_message), (openai, openai_response)])
@pytest.mark.parametrize("text", ["not json", '{"candidates": "x"}', "[]", ""])
def test_unusable_output_is_invalid_output(make, ok, text):
    reader, _, _ = make([ok(text)])
    result = reader.read(ReadRequest(PNG))
    assert (result.candidates, result.call.outcome) == ([], "invalid_output")


@pytest.mark.parametrize("make,ok", [(claude, claude_message), (openai, openai_response)])
def test_an_empty_list_is_invalid_output_and_still_billed(make, ok):
    reader, _, _budget = make([ok('{"candidates": []}')])
    result = reader.read(ReadRequest(PNG))
    assert result.call.outcome == "invalid_output"
    assert result.call.cost_usd > 0


# ------------------------------------------------------------------ what is sent


def test_claude_request_has_its_own_prompt_model_effort_and_only_the_crop():
    reader, transport, _ = claude([claude_message()], math_reader_claude_model="claude-sonnet-5-5")
    reader.read(ReadRequest(PNG))
    (sent,) = transport.sent
    body = sent.json()
    assert sent.url == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == "sk-ant-test"
    assert "anthropic-workspace-id" not in sent.headers
    assert body["model"] == "claude-sonnet-5-5"
    assert body["system"] == prompt.CLAUDE_INSTRUCTIONS != prompt.INSTRUCTIONS
    assert body["output_config"]["effort"] == "medium"
    content = body["messages"][0]["content"]
    assert [part["type"] for part in content] == ["image", "text"]
    assert content[0]["source"]["data"] == base64.b64encode(PNG).decode()
    assert content[1]["text"] == prompt.USER_TEXT  # no document text, ever


def test_claude_sends_the_workspace_header_when_configured():
    reader, transport, _ = claude([claude_message()], anthropic_workspace_id="wrkspc_1")
    reader.read(ReadRequest(PNG))
    assert transport.sent[0].headers["anthropic-workspace-id"] == "wrkspc_1"


def test_openai_request_is_not_stored_and_carries_the_key_in_a_header():
    reader, transport, _ = openai([openai_response()])
    reader.read(ReadRequest(PNG))
    (sent,) = transport.sent
    body = sent.json()
    assert sent.url == "https://api.openai.com/v1/responses"
    assert body["store"] is False
    assert sent.headers["authorization"] == "Bearer sk-openai-test"
    assert "sk-openai-test" not in sent.url
    assert body["text"]["format"]["strict"] is True
    assert body["instructions"] == prompt.INSTRUCTIONS


def test_claude_reader_differs_from_the_page_pass_by_default():
    settings = make_settings()
    assert settings.math_reader_claude_model not in {
        settings.anthropic_main_model,
        settings.anthropic_cheap_model,
    }
    assert settings.math_reader_claude_model != settings.image_pages_model


# ------------------------------------------------------------------ budget


@pytest.mark.parametrize("make,ok", [(claude, claude_message), (openai, openai_response)])
def test_a_worst_case_is_reserved_before_the_send_and_settled_at_the_usage(make, ok):
    reader, _transport, budget = make([ok()])
    reader.read(ReadRequest(PNG))
    assert len(budget.reserved) == 1
    assert 0 < budget.settled[0] < budget.reserved[0]
    assert budget.calls == 0  # an optional second reading does not use the job's call cap
    assert budget.spent_usd == pytest.approx(budget.settled[0])


@pytest.mark.parametrize("make", READERS)
def test_a_refused_reservation_sends_nothing(make):
    reader, transport, _ = make([], budget=RecordingBudget(cap=0.0))
    with pytest.raises(BudgetExceeded):
        reader.read(ReadRequest(PNG))
    assert transport.sent == []


@pytest.mark.parametrize("make", READERS)
def test_a_refused_resend_returns_the_failure_already_had(make):
    probe, _, probe_budget = make([TransportError("timeout")], model_max_retries=0)
    probe.read(ReadRequest(PNG))
    one = probe_budget.reserved[0]
    # A timeout is charged in full, so a second reservation does not fit under 1.5x.
    reader, transport, _ = make([TransportError("timeout")], budget=RecordingBudget(cap=one * 1.5))
    result = reader.read(ReadRequest(PNG))
    assert (result.call.outcome, result.call.sends, len(transport.sent)) == ("timeout", 1, 1)


# ------------------------------------------------------------------ errors and retries


@pytest.mark.parametrize("make,ok", [(claude, claude_message), (openai, openai_response)])
def test_transient_statuses_are_resent_each_under_its_own_reservation(make, ok, no_backoff):
    reader, _transport, budget = make(
        [json_reply({}, 429, **{"retry-after": "3"}), json_reply({}, 500), ok()]
    )
    result = reader.read(ReadRequest(PNG))
    assert result.call.outcome == "ok" and result.call.sends == 3
    assert len(budget.reserved) == 3
    assert budget.settled[:2] == [0.0, 0.0]  # an HTTP error is not billed
    assert no_backoff[0] == 3.0 and 0 < no_backoff[1] <= 1.0


@pytest.mark.parametrize("make", READERS)
def test_retries_stop_at_the_configured_limit(make):
    reader, transport, _ = make([json_reply({}, 503)] * 3, model_max_retries=2)
    result = reader.read(ReadRequest(PNG))
    assert (result.call.outcome, result.call.sends, len(transport.sent)) == ("api_error", 3, 3)
    reader, transport, _ = make([json_reply({}, 503)], model_max_retries=0)
    assert reader.read(ReadRequest(PNG)).call.sends == 1


@pytest.mark.parametrize("make", READERS)
def test_a_client_error_is_not_resent_or_billed(make):
    reader, _transport, _budget = make([json_reply({"error": "bad"}, 400)])
    result = reader.read(ReadRequest(PNG))
    assert (result.call.outcome, result.call.sends, result.call.cost_usd) == ("api_error", 1, 0.0)


@pytest.mark.parametrize("make", READERS)
def test_a_timeout_is_charged_in_full_a_failed_connect_is_free(make):
    reader, _, budget = make([TransportError("timeout")] * 3)
    result = reader.read(ReadRequest(PNG))
    assert result.call.outcome == "timeout"
    assert budget.settled == budget.reserved  # the provider may have billed all three
    reader, _, budget = make([TransportError("not_sent")] * 3)
    result = reader.read(ReadRequest(PNG))
    assert (result.call.outcome, result.call.cost_usd) == ("api_error", 0.0)


@pytest.mark.parametrize("make", READERS)
def test_each_send_is_bounded_by_the_model_timeout(make):
    reader, transport, _ = make([json_reply({}, 400)], model_timeout_s=7.5)
    reader.read(ReadRequest(PNG))
    assert transport.sent[0].timeout_s == 7.5


@pytest.mark.parametrize("make", READERS)
def test_a_non_json_body_is_an_api_error(make):
    reader, _, _ = make([HttpReply(200, b"<html>gateway</html>")])
    assert reader.read(ReadRequest(PNG)).call.outcome == "api_error"


@pytest.mark.parametrize("make", READERS)
def test_an_interrupt_releases_the_reservation(make, monkeypatch):
    reader, _transport, budget = make([json_reply({}, 503), json_reply({}, 503)])

    def interrupted(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "_sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        reader.read(ReadRequest(PNG))
    assert budget._held == 0.0


# ------------------------------------------------------------------ refusals


def test_claude_refusal_is_refused_and_billed_at_its_usage():
    reader, _, _ = claude([claude_message("", stop_reason="refusal")])
    result = reader.read(ReadRequest(PNG))
    assert (result.candidates, result.call.outcome) == ([], "refused")
    assert result.call.cost_usd > 0


@pytest.mark.parametrize(
    "stop,outcome", [("max_tokens", "invalid_output"), ("tool_use", "api_error")]
)
def test_claude_other_stop_reasons(stop, outcome):
    reader, _, _ = claude([claude_message(stop_reason=stop)])
    assert reader.read(ReadRequest(PNG)).call.outcome == outcome


def test_claude_error_object_is_an_api_error():
    reader, _, _ = claude([json_reply({"type": "error", "error": {"type": "overloaded"}})])
    assert reader.read(ReadRequest(PNG)).call.outcome == "api_error"


def test_openai_refusal_part_is_refused():
    body = {
        "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
        "usage": {"input_tokens": 900, "output_tokens": 5},
    }
    reader, _, _ = openai([json_reply(body)])
    result = reader.read(ReadRequest(PNG))
    assert (result.candidates, result.call.outcome) == ([], "refused")


def test_openai_incomplete_answer_is_invalid_output():
    reader, _, _ = openai([openai_response("{", status="incomplete")])
    assert reader.read(ReadRequest(PNG)).call.outcome == "invalid_output"


# ------------------------------------------------------------------ logs


@pytest.mark.parametrize("make,ok", [(claude, claude_message), (openai, openai_response)])
def test_logs_carry_no_content_and_no_key(make, ok, caplog):
    answer = _dump({"candidates": [{"latex": SECRET_LATEX, "confidence": 0.5}]})
    reader, _, _ = make([json_reply({"x": SECRET_LATEX}, 500), ok(answer)])
    with caplog.at_level(logging.DEBUG):
        reader.read(ReadRequest(PNG))
    lines = [
        f"{r.getMessage()} {getattr(r, FIELDS_ATTR, {})}"
        for r in caplog.records
        if r.name.startswith("readablestem")
    ]
    assert lines
    text = "\n".join(lines)
    assert "SECRETCONTENT" not in text
    assert "sk-ant-test" not in text and "sk-openai-test" not in text
    assert "math_reader_call" in text


# ------------------------------------------------------------------ construction


def test_everything_is_off_by_default():
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.math_readers_on == []
    assert build_readers(settings, RecordingBudget()) == {}


def test_enabled_readers_are_built_and_need_their_keys():
    settings = make_settings(math_reader_claude_enabled=True, math_reader_openai_enabled=True)
    assert set(build_readers(settings, RecordingBudget(), transport=FakeTransport([]))) == {
        "claude",
        "openai",
    }
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_readers(
            make_settings(math_reader_claude_enabled=True, anthropic_api_key=None),
            RecordingBudget(),
        )
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        build_readers(
            make_settings(math_reader_openai_enabled=True, openai_api_key=None), RecordingBudget()
        )


def test_an_unpriced_model_is_refused_at_construction():
    with pytest.raises(UnknownModelError):
        build_readers(
            make_settings(math_reader_claude_enabled=True, math_reader_claude_model="nope"),
            RecordingBudget(),
        )


# ------------------------------------------------------------------ sub-cap, gates, filter


def test_the_reader_sub_cap_stops_readers_without_touching_the_job_cap():
    job = RecordingBudget(cap=10.0)
    settings = make_settings(math_reader_claude_enabled=True, math_reader_max_usd_per_job=0.01)
    transport = FakeTransport([claude_message()] * 20)
    reader = build_readers(settings, job, transport=transport)["claude"]
    reads = 0
    with pytest.raises(BudgetExceeded) as caught:
        for _ in range(20):
            reader.read(ReadRequest(PNG))
            reads += 1
    assert caught.value.scope == "reader_usd"
    assert 0 <= reads < 20
    assert job.spent_usd <= 0.01 + 1e-9 and job.calls == 0  # the job keeps the rest


def test_the_readers_share_one_sub_cap():
    settings = make_settings(
        math_reader_claude_enabled=True,
        math_reader_openai_enabled=True,
        math_reader_max_usd_per_job=0.5,
    )
    readers = build_readers(settings, RecordingBudget(cap=10.0), transport=FakeTransport([]))
    assert readers["claude"]._budget is readers["openai"]._budget


def test_a_resend_the_sub_cap_refuses_returns_the_failure_already_had():
    probe, _, probe_budget = claude([TransportError("timeout")], model_max_retries=0)
    probe.read(ReadRequest(PNG))
    one = probe_budget.reserved[0]
    settings = make_settings(math_reader_claude_enabled=True, math_reader_max_usd_per_job=one * 1.5)
    transport = FakeTransport([TransportError("timeout")] * 3)
    reader = build_readers(settings, RecordingBudget(cap=10.0), transport=transport)["claude"]
    result = reader.read(ReadRequest(PNG))
    assert (result.call.outcome, result.call.sends) == ("timeout", 1)


def test_rate_gates_are_the_providers_shared_with_the_step_clients():
    from readablestem.models.base import rate_gate

    assert ClaudeReader.provider == "anthropic" and OpenAIReader.provider == "openai"
    reader, _, _ = claude([json_reply({}, 429, **{"retry-after": "3"}), claude_message()])
    reader.read(ReadRequest(PNG))
    assert rate_gate("anthropic").remaining() > 0
    assert rate_gate("claude").remaining() == 0


def test_openai_content_filter_is_refused():
    reader, _, _ = openai(
        [openai_response("{", status="incomplete", incomplete_details={"reason": "content_filter"})]
    )
    result = reader.read(ReadRequest(PNG))
    assert (result.candidates, result.call.outcome) == ([], "refused")
