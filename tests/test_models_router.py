"""call_with_fallback: one fallback attempt on a retryable outcome, every call kept."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from pydantic import BaseModel

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL, Settings
from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from a11y_stem.core.types import ModelCall, Outcome
from a11y_stem.models.base import T, VisionRequest
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.pricing import cost_usd, estimate_max_cost_usd
from a11y_stem.models.router import (
    RETRYABLE,
    FallbackBudgetExceeded,
    call_with_fallback,
    calls_made,
)

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
ANSWER = {"latex": "x", "confidence": 0.9}


class Out(BaseModel):
    latex: str
    confidence: float


def make_request(model: str = MAIN_MODEL) -> VisionRequest:
    return VisionRequest(
        step="equation",
        model=model,
        prompt_version="equation-test",
        instructions="Transcribe.",
        images=[b"\x89PNG\r\n\x1a\nimg"],
        context_text="nearby text",
    )


def fake(fail: dict[str, Outcome] | None = None, budget: JobBudget | None = None):
    return FakeVisionClient(defaults={"equation": ANSWER}, fail_models=fail or {}, budget=budget)


def summary(calls: list[ModelCall]) -> list[tuple[str, str, bool, int]]:
    return [(c.model, c.outcome, c.is_fallback, c.attempt) for c in calls]


def test_main_model_ok_makes_one_call():
    client = fake()
    parsed, calls = call_with_fallback(client, make_request(), Out, settings=SETTINGS)
    assert parsed == Out.model_validate(ANSWER)
    assert summary(calls) == [(MAIN_MODEL, "ok", False, 1)]
    assert len(client.requests) == 1


@pytest.mark.parametrize("outcome", sorted(RETRYABLE))
def test_retryable_failure_falls_back_once(outcome: Outcome):
    client = fake({MAIN_MODEL: outcome})
    request = make_request()
    parsed, calls = call_with_fallback(client, request, Out, settings=SETTINGS)
    assert parsed == Out.model_validate(ANSWER)
    assert summary(calls) == [(MAIN_MODEL, outcome, False, 1), (CHEAP_MODEL, "ok", True, 2)]
    # The fallback request is the same request with only the model swapped.
    assert client.requests[1] == dataclasses.replace(request, model=CHEAP_MODEL)


def test_cheap_model_falls_back_to_the_main_model():
    client = fake({CHEAP_MODEL: "timeout"})
    parsed, calls = call_with_fallback(client, make_request(CHEAP_MODEL), Out, settings=SETTINGS)
    assert parsed is not None
    assert summary(calls) == [(CHEAP_MODEL, "timeout", False, 1), (MAIN_MODEL, "ok", True, 2)]


def test_both_models_fail_returns_none_and_both_calls():
    client = fake({MAIN_MODEL: "api_error", CHEAP_MODEL: "refused"})
    parsed, calls = call_with_fallback(client, make_request(), Out, settings=SETTINGS)
    assert parsed is None
    assert summary(calls) == [
        (MAIN_MODEL, "api_error", False, 1),
        (CHEAP_MODEL, "refused", True, 2),
    ]
    assert len(client.requests) == 2  # exactly one fallback, no chain


class ScriptedClient:
    """Returns a fixed outcome per model; no pricing, so any model name works."""

    def __init__(self, outcomes: dict[str, Outcome]) -> None:
        self.outcomes = outcomes
        self.requests: list[VisionRequest] = []

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        self.requests.append(request)
        outcome = self.outcomes[request.model]
        call = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            is_fallback=is_fallback,
            outcome=outcome,
        )
        return (schema.model_validate(ANSWER) if outcome == "ok" else None), call


def test_non_retryable_outcome_does_not_fall_back():
    client = ScriptedClient({MAIN_MODEL: "skipped", CHEAP_MODEL: "ok"})
    parsed, calls = call_with_fallback(client, make_request(), Out, settings=SETTINGS)
    assert parsed is None and summary(calls) == [(MAIN_MODEL, "skipped", False, 1)]


def test_model_without_a_configured_fallback_gets_one_attempt():
    client = ScriptedClient({"gpt-other": "api_error"})
    parsed, calls = call_with_fallback(client, make_request("gpt-other"), Out, settings=SETTINGS)
    assert parsed is None and len(calls) == 1 and len(client.requests) == 1


def test_budget_exceeded_on_the_first_call_propagates_unchanged():
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1), max_usd=1, max_calls=0)
    with pytest.raises(BudgetExceeded) as exc:
        call_with_fallback(fake(budget=budget), make_request(), Out, settings=SETTINGS)
    assert type(exc.value) is BudgetExceeded
    assert exc.value.scope == "job_calls"
    assert calls_made(exc.value) == []


def test_budget_exceeded_on_the_fallback_keeps_the_first_call():
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1), max_usd=1, max_calls=1)
    client = fake({MAIN_MODEL: "timeout"}, budget=budget)
    with pytest.raises(BudgetExceeded) as exc:
        call_with_fallback(client, make_request(), Out, settings=SETTINGS)
    assert isinstance(exc.value, FallbackBudgetExceeded)
    assert exc.value.scope == "job_calls"
    (first,) = calls_made(exc.value)
    assert (first.model, first.outcome, first.attempt) == (MAIN_MODEL, "timeout", 1)
    assert first.cost_usd > 0 and budget.spent_usd == pytest.approx(first.cost_usd)
    assert len(client.requests) == 1  # the fallback was never sent


def test_daily_cap_reached_on_the_fallback_keeps_the_first_call():
    request = make_request()

    def estimate(model: str) -> float:
        return estimate_max_cost_usd(
            model,
            n_images=len(request.images),
            text_chars=len(request.context_text or ""),
            max_output_tokens=request.max_output_tokens,
        )

    # The daily cap fits the main model's reservation. The failed main call then really costs
    # 10k input tokens, which leaves less than the cheap model's reservation for the day.
    budget = JobBudget(
        guard=InMemorySpendGuard(cap_usd=estimate(MAIN_MODEL)), max_usd=1, max_calls=9
    )
    client = fake({MAIN_MODEL: "api_error"}, budget=budget)
    client.input_tokens, client.output_tokens = 10_000, 0
    assert estimate(MAIN_MODEL) - cost_usd(MAIN_MODEL, 10_000, 0, 0) < estimate(CHEAP_MODEL)

    with pytest.raises(FallbackBudgetExceeded) as exc:
        call_with_fallback(client, request, Out, settings=SETTINGS)
    assert exc.value.scope == "daily"
    assert [(c.model, c.outcome) for c in calls_made(exc.value)] == [(MAIN_MODEL, "api_error")]


def test_calls_made_is_a_copy():
    error = FallbackBudgetExceeded(BudgetExceeded("job_usd", "cap"), [ModelCall(**_call())])
    calls_made(error).clear()
    assert len(error.calls) == 1
    assert str(error) == "cap"


def _call() -> dict[str, Any]:
    return {"step": "equation", "model": MAIN_MODEL, "prompt_version": "v"}
