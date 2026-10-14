"""The chatbot arms and the figure judge as Message Batches: the same request, half the price.

No network: the Messages client is a scripted stand-in with the batch calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.eval.judge import JudgeVerdict
from readablestem.models.anthropic_client import AnthropicVisionClient
from readablestem.models.base import VisionRequest
from readablestem.proof import chatbot, chatbot_batch, judging
from readablestem.proof.chatbot_batch import record_arms_batch

OPUS = chatbot.ARM_BY_ID["chatbot-opus-purdue"]
HAIKU = chatbot.ARM_BY_ID["chatbot-haiku-purdue"]
HTML = "```html\n<h2>Speed</h2>\n```"


def message(model: str, text: str = HTML, stop: str = "end_turn") -> Any:
    usage = SimpleNamespace(
        input_tokens=1000,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        output_tokens=2000,
        output_tokens_details=SimpleNamespace(thinking_tokens=500),
        iterations=None,
    )
    blocks = [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop, usage=usage, model=model)


class Messages:
    def __init__(self, fate: list[str] | None = None, text: str = HTML) -> None:
        self.fate = fate or []  # per request, in order: "ok" | an error type | "expired"
        self.text = text
        self.created: list[list[dict[str, Any]]] = []
        self.counted: list[dict[str, Any]] = []
        self.batches = SimpleNamespace(
            create=self._create, retrieve=self._retrieve, results=self._results, cancel=None
        )

    def count_tokens(self, **kwargs: Any) -> Any:
        self.counted.append(kwargs)
        return SimpleNamespace(input_tokens=1000)

    def stream(self, **_kw: Any) -> Any:
        raise AssertionError("a batch run never streams")

    def _create(self, requests: list[dict[str, Any]]) -> Any:
        self.created.append(requests)
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}")

    def _retrieve(self, batch_id: str) -> Any:
        counts = SimpleNamespace(succeeded=0, errored=0, expired=0)
        return SimpleNamespace(processing_status="ended", request_counts=counts)

    def _results(self, batch_id: str) -> list[Any]:
        out = []
        for entry in self.created[int(batch_id.split("_")[1]) - 1]:
            fate = self.fate.pop(0) if self.fate else "ok"
            if fate == "ok":
                result = SimpleNamespace(
                    type="succeeded", message=message(entry["params"]["model"], self.text)
                )
            elif fate == "expired":
                result = SimpleNamespace(type="expired")
            else:
                error = SimpleNamespace(error=SimpleNamespace(type=fate))
                result = SimpleNamespace(type="errored", error=error)
            out.append(SimpleNamespace(custom_id=entry["custom_id"], result=result))
        return out


def client(**kw: Any) -> Any:
    return SimpleNamespace(messages=Messages(**kw))


@pytest.fixture
def one_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(chatbot, "page_pngs", lambda _p: [b"png-1", b"png-2"])
    monkeypatch.setattr(chatbot_batch, "page_pngs", lambda _p: [b"png-1", b"png-2"])
    path = tmp_path / "book__page.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "A short test page about velocity.")
        doc.save(path)
    return {"book/page": path}


def meta(out: Path, arm: chatbot.ChatArm, stem: str = "book__page.run1") -> dict:
    return json.loads((out / arm.id / f"{stem}.json").read_text(encoding="utf-8"))


def test_a_batch_sends_what_a_live_call_streams_and_records_list_price(one_pdf, tmp_path):
    c = client()
    live_cost = chatbot._claude_reply(message(OPUS.model), OPUS.model).cost
    summary = record_arms_batch(
        one_pdf, tmp_path, arms=[OPUS], max_usd=5, client=c, runs=1, say=lambda _m: None
    )
    (request,) = c.messages.created[0]
    assert request["params"].keys() == {"model", "max_tokens", "messages"}
    assert request["params"]["model"] == OPUS.model
    assert request["params"]["max_tokens"] == chatbot.MAX_OUTPUT_TOKENS
    assert request["params"]["messages"] == chatbot.build_claude_input(
        one_pdf["book/page"], OPUS.prompt_text, [b"png-1", b"png-2"]
    )
    record = meta(tmp_path, OPUS)
    assert record["status"] == "ok" and record["batch"] is True
    assert record["cost_usd"] == pytest.approx(live_cost, rel=1e-3)
    assert record["billed_usd"] == pytest.approx(live_cost / 2, rel=1e-3)
    assert summary["spent_usd"] == pytest.approx(live_cost / 2, rel=1e-3)
    assert (tmp_path / OPUS.id / "book__page.run1.html").read_text() == "<h2>Speed</h2>\n"


def test_a_recorded_job_is_not_sent_and_the_cap_refuses_what_it_cannot_hold(one_pdf, tmp_path):
    c = client()
    done = tmp_path / OPUS.id
    done.mkdir()
    (done / "book__page.run1.json").write_text("{}")
    record_arms_batch(one_pdf, tmp_path, arms=[OPUS], max_usd=5, client=c, runs=1, say=print)
    assert c.messages.created == []
    tiny = record_arms_batch(
        one_pdf, tmp_path / "b", arms=[OPUS], max_usd=0.001, client=c, runs=1, say=print
    )
    assert tiny["refused_by_cap"] == [f"{OPUS.id}/book__page.run1"] and c.messages.created == []


def test_an_api_error_or_an_expired_request_is_sent_again_and_other_errors_end_the_job(
    one_pdf, tmp_path
):
    c = client(fate=["api_error", "expired"])
    record_arms_batch(
        one_pdf, tmp_path, arms=[OPUS], max_usd=5, client=c, runs=1, say=lambda _m: None
    )
    assert len(c.messages.created) == 3 and meta(tmp_path, OPUS)["status"] == "ok"
    assert meta(tmp_path, OPUS)["attempts"] == ["api_error", "expired"]

    bad = client(fate=["invalid_request_error"])
    record_arms_batch(
        one_pdf, tmp_path / "x", arms=[HAIKU], max_usd=5, client=bad, runs=1, say=lambda _m: None
    )
    record = meta(tmp_path / "x", HAIKU)
    assert record["status"] == "crashed" and record["error"] == "invalid_request_error"
    assert len(bad.messages.created) == 1


def test_only_the_claude_arms_can_be_batched(one_pdf, tmp_path):
    with pytest.raises(ValueError):
        record_arms_batch(
            one_pdf, tmp_path, arms=[chatbot.ARMS[0]], max_usd=1, client=client(), say=print
        )


def test_the_judge_collects_its_requests_then_writes_once(monkeypatch, tmp_path):
    """Passes that only collect never write; the last one does, with every answer in hand."""
    settings = Settings(_env_file=None, model_provider="anthropic", anthropic_api_key="sk-ant-x")  # type: ignore[call-arg]
    budget = JobBudget(InMemorySpendGuard(cap_usd=5), max_usd=5, max_calls=50)
    live = AnthropicVisionClient(settings, budget, client=SimpleNamespace())  # never sends
    verdict = {
        "conveys_key_information": True,
        "factual_error": False,
        "score": 5,
        "confidence": 0.9,
    }
    sdk = client(text=json.dumps(verdict))
    writes: list[bool] = []

    def one_request(arms, golds, *, client, settings, image_for, say, write=True):
        writes.append(write)
        request = VisionRequest(
            step="judge",
            model="claude-opus-5-5",
            prompt_version="judge-x",
            instructions="J",
            images=[b"\x89PNG\r\n\x1a\nx"],
        )
        parsed, _ = client.run(request, JudgeVerdict)
        return 0.0 if parsed is None else 1.0

    monkeypatch.setattr(judging, "judge_arms", one_request)
    monkeypatch.setattr(AnthropicVisionClient, "sdk_client", lambda _self: sdk)
    total = judging.judge_arms_batched(
        {},
        {},
        client=live,
        settings=settings,
        budget=budget,
        answers=tmp_path / "judge.jsonl",
        image_for=lambda _i: b"",
        say=lambda _m: None,
    )
    assert writes == [False, False, True] and total == 1.0
    assert len(sdk.messages.created) == 1
