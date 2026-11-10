"""call_with_fallback: one fallback attempt on a retryable outcome, every call kept."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import BaseModel

from readablestem.config import CHEAP_MODEL, MAIN_MODEL, Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.core.types import ModelCall, Outcome
from readablestem.models.base import VisionRequest
from readablestem.models.fake import FakeVisionClient
from readablestem.models.router import (
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


@pytest.mark.parametrize("outcome", sorted(RETRYABLE))
def test_retryable_failure_falls_back_once(outcome: Outcome):
    client = fake({MAIN_MODEL: outcome})
    request = make_request()
    parsed, calls = call_with_fallback(client, request, Out, settings=SETTINGS)
    assert parsed == Out.model_validate(ANSWER)
    assert summary(calls) == [(MAIN_MODEL, outcome, False, 1), (CHEAP_MODEL, "ok", True, 2)]
    # The fallback request is the same request with only the model swapped.
    assert client.requests[1] == dataclasses.replace(request, model=CHEAP_MODEL)


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
