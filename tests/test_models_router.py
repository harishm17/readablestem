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
# The page witness is a Claude step: it has a model to fall back to only on the Claude pair.
CLAUDE = Settings(_env_file=None, model_provider="anthropic")  # type: ignore[call-arg]
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


class Scripted:
    """Answers each call with the next outcome of `outcomes` (an empty answer when ok)."""

    def __init__(self, outcomes: list[Outcome]):
        self.outcomes = list(outcomes)
        self.requests: list[VisionRequest] = []

    def run(self, request: VisionRequest, schema: type[BaseModel], *, is_fallback: bool = False):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        call = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            cost_usd=0.001,
            outcome=outcome,
            is_fallback=is_fallback,
        )
        empty = {"page_math": "expressions", "page_witness": "formulas", "image_page": "blocks"}
        return (schema.model_validate({empty[request.step]: []}) if outcome == "ok" else None), call


def _page_math(client):
    from readablestem.pipeline import page_math

    rows = [page_math.PageRow("L0", "x = 5 and more", tuple(range(14)))]
    plan = page_math.run_page(client, CLAUDE, rows, b"png")
    return plan.failed, plan.calls


def _page_witness(client):
    from readablestem.pipeline import page_math, page_witness

    rows = [page_math.PageRow("L0", "x = 5 and more", tuple(range(14)))]
    page = page_witness.run_page(client, CLAUDE, rows, b"png")
    return page.failed, page.calls


def _image_page(client):
    from readablestem.pipeline import image_page

    read = image_page.read_page(client, CLAUDE, 0, b"png")
    failed = None if read.outcome == "ok" else read.outcome
    return failed, read.calls


@pytest.mark.parametrize("step", [_page_math, _page_witness, _image_page])
@pytest.mark.parametrize("first", ["api_error", "refused"])
def test_a_page_step_asks_once_more_after_a_service_error_or_a_refusal(step, first: Outcome):
    # One retry answers; a second failure ends the step with its reason and sends nothing more.
    client = Scripted([first])
    failed, calls = step(client)
    assert failed is None and [c.outcome for c in calls] == [first, "ok"]
    assert len(client.requests) == 2 and client.requests[0].model != client.requests[1].model
    client = Scripted([first, "refused"])
    failed, calls = step(client)
    assert failed == "refused" and len(calls) == 2 and len(client.requests) == 2
