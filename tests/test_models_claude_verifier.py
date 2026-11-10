"""The second automated check on Claude (T2-20): `ClaudeVerifier` against a scripted HTTP
transport (no network, no key), and its place in the factories and the steps."""

from __future__ import annotations

import json
import logging

import httpx2
import pytest

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Status
from readablestem.models.anthropic_client import AnthropicVisionClient
from readablestem.models.claude_verifier import (
    ClaudeVerifier,
    make_claude_verifier,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.models.verifier import (
    EQUATION_CHECK,
    FakeVerifier,
)
from readablestem.pipeline.equation_step import transcribe
from readablestem.pipeline.figure_step import describe
from test_models_anthropic_client import PNG, SECRET, Wire, error_body, message_body
from test_steps_support import equation_element, figure_element, use_fallback_router

SONNET = "claude-sonnet-5-5"
ALT = "Bar chart of votes: A has 2, B has 5 and C has 3."
GOOD_FIGURE = '{"conveys_key_information": true, "contradicts_image": false, "problem": "none"}'
BAD_FIGURE = (
    '{"conveys_key_information": true, "contradicts_image": true, "problem": "wrong_value"}'
)
MATCH = '{"verdict": "match", "difference": "none"}'
CHANGED = '{"verdict": "meaning_changing", "difference": "exponent"}'


def make_settings(**kw) -> Settings:
    kw.setdefault("model_provider", "anthropic")
    kw.setdefault("anthropic_api_key", "sk-ant-test")
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def make_budget(cap: float = 1.0, max_calls: int = 100) -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=max_calls)


def verifier(wire: Wire, budget: JobBudget | None = None, **kw) -> ClaudeVerifier:
    settings = make_settings(**kw)
    own = settings.model_copy(update={"model_max_retries": 0})
    client = AnthropicVisionClient(
        own, budget or make_budget(), http_client=wire.client(), count_calls=False
    )
    return ClaudeVerifier(client, settings.claude_check_model)


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


# ---------------------------------------------------------------- independence


# ---------------------------------------------------------------- the request


# ---------------------------------------------------------------- the answers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (MATCH, {"match": 1.0, "cosmetic": 0.0, "meaning_changing": 0.0}),
        (CHANGED, {"match": 0.0, "cosmetic": 0.0, "meaning_changing": 1.0}),
        (
            '{"verdict": "cosmetic", "difference": "bold_or_style"}',
            {"match": 0.0, "cosmetic": 1.0, "meaning_changing": 0.0},
        ),
    ],
)
def test_equation_answers_become_the_decisions_check_answer_codes(text, expected):
    verdict = verifier(Wire(message_body(text=text))).equation_match(PNG, latex="x")
    assert verdict.check == EQUATION_CHECK and verdict.probabilities == expected


# ---------------------------------------------------------------- fail-open


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (message_body(text=None, stop_reason="refusal"), "refused"),
        (message_body(text="not json"), "invalid_output"),
        (message_body(text='{"verdict": "unsure"}'), "invalid_output"),
        (httpx2.Response(500, json=error_body()), "api_error"),
        (httpx2.Response(400, json=error_body("invalid_request_error")), "api_error"),
    ],
)
def test_no_answer_is_a_reason_code_never_an_exception(answer, reason, caplog):
    caplog.set_level(logging.DEBUG)
    verdict = verifier(Wire(answer)).figure_alt(PNG, alt=SECRET, caption=None)
    assert not verdict.answered and verdict.probabilities == {}
    assert verdict.reason_code == reason
    assert SECRET not in caplog.text and SECRET not in json.dumps(
        verdict.record(stage="final", mode="shadow", flagged=None).model_dump()
    )


def test_a_refused_reservation_sends_nothing():
    wire = Wire(message_body(text=GOOD_FIGURE))
    verdict = verifier(wire, make_budget(cap=0.0001)).figure_alt(PNG, alt=ALT, caption=None)
    assert verdict.reason_code == "budget_exceeded" and wire.requests == []


def test_a_missing_key_is_skipped_not_raised():
    settings = make_settings(anthropic_api_key=None)
    check = make_claude_verifier(settings, make_budget())
    verdict = check.figure_alt(PNG, alt=ALT, caption=None)
    assert verdict.reason_code == "skipped"


# ---------------------------------------------------------------- the factories


# ---------------------------------------------------------------- in the steps

FIGURE_ANSWER = {"figure_type": "chart", "decorative": False, "alt": ALT, "confidence": 0.95}
EQUATION_ANSWER = {"is_equation": True, "latex": "F = m a", "confidence": 0.95}


def fake_check(**kw) -> FakeVerifier:
    return FakeVerifier(reported_model=SONNET, **kw)


def describe_with(settings: Settings, check: FakeVerifier):
    client = FakeVisionClient(responders={"figure": lambda r: FIGURE_ANSWER}, verifier=check)
    return describe(figure_element(caption="Figure 1."), PNG, client=client, settings=settings)


BAD = {"ok": 1.0, "factual_error": 1.0}


def test_equations_are_shadow_even_when_enforcing():
    alarm = {"match": 0.0, "cosmetic": 0.0, "meaning_changing": 1.0}
    for mode in ("shadow", "enforce"):
        client = FakeVisionClient(
            responders={"equation": lambda r: EQUATION_ANSWER}, verifier=fake_check(equation=alarm)
        )
        fix = transcribe(
            equation_element(), PNG, client=client, settings=make_settings(claude_second_check=mode)
        )
        assert fix.status is Status.ACCEPTED and not fix.failed_checks
        assert [(v.mode, v.flagged) for v in fix.verifications] == [("shadow", True)]


def test_equations_waiting_for_review_are_checked_only_when_the_scope_is_all():
    unsure = {"is_equation": True, "latex": "F = m a", "confidence": 0.7}  # needs review
    for scope, asked in (("accepted", 0), ("all", 1)):
        check = fake_check()
        client = FakeVisionClient(responders={"equation": lambda r: unsure}, verifier=check)
        fix = transcribe(
            equation_element(),
            PNG,
            client=client,
            settings=make_settings(claude_check_scope=scope),
        )
        assert fix.status is Status.NEEDS_REVIEW
        assert len(fix.verifications) == len(check.requests) == asked
