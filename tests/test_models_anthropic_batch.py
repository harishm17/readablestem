"""Staged runs on the Message Batches API, against a scripted HTTP transport.

The real SDK builds and parses everything; only the wire is scripted. The checks: a staged
run sends the same parameters a live call would (less the refusal fallback), reserves and
settles at the batch price, records list-price calls, goes on to the request that depends
on an answer, and never collects a fallback for a placeholder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from a11y_stem.config import Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.eval import staged
from a11y_stem.eval.answers import CachedClient, load_rows
from a11y_stem.models import router
from a11y_stem.models.anthropic_batch import BatchRunner, PendingPool, StagingClient
from a11y_stem.models.anthropic_client import AnthropicVisionClient
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.openai_client import PNG_SIGNATURE
from a11y_stem.models.pricing import BATCH_DISCOUNT, cost_usd
from a11y_stem.pipeline.equation_step import EquationOutput

PNG = PNG_SIGNATURE + b"crop"
RESULTS = "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"
OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-5-5"


def message(text: str, model: str = OPUS) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "stop_details": None,
        "usage": {"input_tokens": 1000, "output_tokens": 200},
    }


def answer(latex: str, confidence: float = 0.9) -> str:
    return json.dumps({"is_equation": True, "latex": latex, "confidence": confidence})


class Anthropic:
    """The batch endpoints, scripted: `answers[request index]` is the text each gets."""

    def __init__(self, replies: dict[str, str], *, fail: set[str] = frozenset()) -> None:
        self.replies = replies  # latex marker in the request's model -> reply text
        self.fail = fail
        self.batches: list[list[dict[str, Any]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/batches":
            body = json.loads(request.content)
            self.batches.append(body["requests"])
            return httpx2.Response(200, json=self._batch("in_progress"))
        if path.endswith("/results"):
            lines = []
            for entry in self.batches[-1]:
                model = entry["params"]["model"]
                if model in self.fail:
                    result = {
                        "type": "errored",
                        "error": {"type": "error", "error": {"type": "api_error", "message": "x"}},
                    }
                else:
                    result = {"type": "succeeded", "message": message(self.replies[model], model)}
                lines.append(json.dumps({"custom_id": entry["custom_id"], "result": result}))
            return httpx2.Response(200, text="\n".join(lines))
        return httpx2.Response(200, json=self._batch("ended"))

    def _batch(self, status: str) -> dict[str, Any]:
        n = len(self.batches[-1]) if self.batches else 0
        counts = {"processing": 0, "succeeded": n, "errored": 0, "canceled": 0, "expired": 0}
        return {
            "id": "msgbatch_1",
            "type": "message_batch",
            "processing_status": status,
            "request_counts": counts,
            "created_at": "2026-10-14T00:00:00Z",
            "expires_at": "2026-10-15T00:00:00Z",
            "ended_at": None,
            "archived_at": None,
            "cancel_initiated_at": None,
            "results_url": RESULTS if status == "ended" else None,
        }


def build(tmp_path: Path, wire: Anthropic, cap: float = 5.0):
    settings = Settings(_env_file=None, model_provider="anthropic", anthropic_api_key="sk-ant-x")  # type: ignore[call-arg]
    budget = JobBudget(InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=100)
    http = httpx2.Client(transport=httpx2.MockTransport(wire))
    live = AnthropicVisionClient(settings, budget, http_client=http)
    pool = PendingPool()
    staging = StagingClient(live, pool)
    cached = CachedClient(staging, tmp_path / "answers.jsonl", settings)
    staging.owner = cached
    runner = BatchRunner(budget, say=lambda _: None, poll_s=0, sleep=lambda _: None)
    return settings, budget, cached, pool, runner


def request(model: str = OPUS, **kw: Any) -> VisionRequest:
    fields: dict[str, Any] = {
        "step": "equation",
        "model": model,
        "prompt_version": "equation-test",
        "instructions": "Transcribe.",
        "images": [PNG],
        "context_text": "near x",
    }
    fields.update(kw)
    return VisionRequest(**fields)


def test_a_staged_run_goes_on_to_the_request_that_depends_on_an_answer(tmp_path: Path) -> None:
    wire = Anthropic({HAIKU: answer("x", 0.5), OPUS: answer("x^2")})
    _, budget, cached, pool, runner = build(tmp_path, wire)

    def one_pass() -> list[str]:
        # The cheap model reads first; an unsure answer is asked again of the main model.
        first, _ = cached.run(request(HAIKU), EquationOutput)
        if first is None:
            return []
        second, _ = cached.run(request(OPUS), EquationOutput)
        return [first.latex] + ([second.latex] if second else [])

    result = staged.drive(one_pass, pool, runner, say=lambda _: None)
    assert result == ["x", "x^2"]
    assert [len(b) for b in wire.batches] == [1, 1]  # two stages, one request each
    sent = wire.batches[0][0]["params"]
    assert sent["model"] == HAIKU and sent["output_config"]["effort"] == "low"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "betas" not in sent and "fallbacks" not in sent
    # list-price calls were recorded, the budget was charged at half of them
    listed = cost_usd(HAIKU, 1000, 0, 200) + cost_usd(OPUS, 1000, 0, 200)
    assert budget.spent_usd == pytest.approx(BATCH_DISCOUNT * listed)
    rows = load_rows(tmp_path / "answers.jsonl")
    assert sum(r["call"]["cost_usd"] for r in rows) == pytest.approx(listed)


def test_a_placeholder_is_not_a_failure_that_asks_the_other_model(tmp_path: Path) -> None:
    wire = Anthropic({OPUS: answer("a"), HAIKU: answer("b")})
    settings, _, cached, pool, _runner = build(tmp_path, wire)
    parsed, calls = router.call_with_fallback(
        cached, request(OPUS), EquationOutput, settings=settings
    )
    assert parsed is None and len(calls) == 2
    pending = pool.take()
    assert [p.request.model for p in pending] == [OPUS]  # the fallback was not collected


def test_a_request_that_keeps_failing_is_recorded_as_a_failure_and_the_run_ends(
    tmp_path: Path,
) -> None:
    wire = Anthropic({OPUS: answer("a")}, fail={OPUS})
    _, _, cached, pool, runner = build(tmp_path, wire)

    def one_pass() -> str:
        parsed, call = cached.run(request(OPUS), EquationOutput)
        return call.outcome if parsed is None else "ok"

    assert staged.drive(one_pass, pool, runner, say=lambda _: None) == "api_error"
    assert len(wire.batches) == 3


def test_a_cap_that_cannot_hold_the_requests_stops_the_run_with_the_answers_kept(
    tmp_path: Path,
) -> None:
    wire = Anthropic({OPUS: answer("a")})
    _, _, cached, pool, runner = build(tmp_path, wire, cap=0.001)
    with pytest.raises(staged.StageError, match="spend cap"):
        staged.drive(
            lambda: cached.run(request(OPUS), EquationOutput), pool, runner, say=lambda _: None
        )
    assert wire.batches == []
