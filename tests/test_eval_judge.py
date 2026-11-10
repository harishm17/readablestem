"""The figure judge: request shape (blind, fenced data), schema and pass rule."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.eval import judge
from readablestem.models.fake import FakeVisionClient
from readablestem.models.prompts import load_prompt
from test_eval_support import settings

VERDICT = {"conveys_key_information": True, "factual_error": False, "score": 4, "confidence": 0.7}


def test_prompt_is_versioned_and_never_says_compliant():
    prompt = load_prompt("judge_figure")
    assert prompt.version.startswith("judge_figure-") and len(prompt.version) == 21
    assert judge.judge_prompt_version() == prompt.version
    assert "compliant" not in prompt.text.lower()
    assert "Length is not quality" in prompt.text


def test_request_puts_every_text_in_untrusted_context_only():
    request = judge.judge_request(
        "Candidate text.", "Reference text.", "Caption text.", settings=settings()
    )
    assert (request.step, request.model, request.images) == ("judge", "gpt-6.1-sol", [])
    assert request.instructions == load_prompt("judge_figure").text  # constant per version
    for text in ("Candidate text.", "Reference text.", "Caption text."):
        assert text not in request.instructions
        assert text in (request.context_text or "")
    context = request.context_text or ""
    assert context.index("<candidate>") < context.index("<reference>") < context.index("<caption>")


def test_verdict_schema_and_pass_rule():
    assert judge.JudgeVerdict(**VERDICT).passed is True
    assert judge.JudgeVerdict(**VERDICT | {"factual_error": True}).passed is False
    assert judge.JudgeVerdict(**VERDICT | {"conveys_key_information": False}).passed is False
    with pytest.raises(ValidationError):
        judge.JudgeVerdict(**VERDICT | {"score": 6})
    with pytest.raises(ValidationError):
        judge.JudgeVerdict(**VERDICT | {"confidence": 1.5})
    with pytest.raises(ValidationError):
        judge.JudgeVerdict(**VERDICT | {"rationale": "extra fields are refused"})


def test_refused_reservation_raises_and_sends_nothing():
    budget = JobBudget(InMemorySpendGuard(cap_usd=0.001), max_usd=0.001, max_calls=10)
    client = FakeVisionClient(defaults={"judge": VERDICT}, budget=budget)
    with pytest.raises(BudgetExceeded):
        judge.judge_alt("x", "y", None, client=client, settings=settings())
    assert client.requests == []
