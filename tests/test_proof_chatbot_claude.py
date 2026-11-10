"""The chatbot recipe on Claude: the request, the cost, the statuses, the arms and the number
of runs a recording says it made. No network: the Messages client is a scripted stand-in."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from readablestem.proof import chatbot, comparison


class FakeMessages:
    """`client.messages` of the Anthropic SDK: `count_tokens` and a streaming `stream`."""

    def __init__(
        self,
        text: str = "```html\n<h2>Speed</h2>\n```",
        stop: str = "end_turn",
        fail: list[str] | None = None,
        input_tokens: int = 1000,
        output_tokens: int = 2000,
    ):
        self.text = text
        self.stop = stop
        self.fail = fail or []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls = 0
        self.sent: list[dict] = []
        self.counted: list[dict] = []

    def count_tokens(self, **kwargs):
        self.counted.append(kwargs)
        return SimpleNamespace(input_tokens=self.input_tokens)

    def stream(self, **kwargs):
        self.sent.append(kwargs)
        self.calls += 1
        if self.fail:
            name = self.fail.pop(0)
            raise type(name, (Exception,), {})("x")
        usage = SimpleNamespace(
            input_tokens=self.input_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=self.output_tokens,
            output_tokens_details=SimpleNamespace(thinking_tokens=500),
            iterations=None,
        )
        blocks = [SimpleNamespace(type="thinking", thinking="hmm")]
        if self.text:
            blocks.append(SimpleNamespace(type="text", text=self.text))
        message = SimpleNamespace(
            content=blocks, stop_reason=self.stop, usage=usage, model=kwargs["model"]
        )

        class Stream:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_exc):
                return False

            def get_final_message(self_inner):
                return message

        return Stream()


def _client(**kwargs):
    return SimpleNamespace(messages=FakeMessages(**kwargs))


def _pdf(path: Path) -> Path:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "A short test page about velocity.")
        doc.save(path)
    return path


@pytest.fixture
def one_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(chatbot, "page_pngs", lambda _p: [b"png-1", b"png-2"])
    return {"book/page": _pdf(tmp_path / "book__page.pdf")}


OPUS = chatbot.ARM_BY_ID["chatbot-opus-purdue"]
HAIKU = chatbot.ARM_BY_ID["chatbot-haiku-purdue"]


def _meta(out: Path, arm: chatbot.ChatArm, stem: str = "book__page.run1") -> dict:
    return json.loads((out / arm.id / f"{stem}.json").read_text(encoding="utf-8"))


def test_a_prompt_over_100k_tokens_is_priced_on_haikus_long_rate_card(one_pdf, tmp_path):
    client = _client(input_tokens=150_000, output_tokens=10_000)
    chatbot.record_arms(
        one_pdf, tmp_path / "o", arms=[HAIKU], max_usd=20, client=client, runs=1, say=print
    )
    meta = _meta(tmp_path / "o", HAIKU)
    assert meta["cost_usd"] == pytest.approx((150_000 * 0.50 + 10_000 * 2.50) / 1e6)


def test_an_infrastructure_error_is_retried_and_the_input_it_may_have_cost_is_charged(
    one_pdf, tmp_path
):
    client = _client(text="<p>x</p>", fail=["InternalServerError", "APITimeoutError"])
    chatbot.record_arms(
        one_pdf,
        tmp_path / "a",
        arms=[OPUS],
        max_usd=20,
        client=client,
        runs=1,
        say=print,
        sleep=lambda _s: None,
    )
    meta = _meta(tmp_path / "a", OPUS)
    assert meta["status"] == "ok" and meta["attempts"] == ["InternalServerError", "APITimeoutError"]
    reply = (1000 * 4.00 + 2000 * 20.00) / 1e6
    assert meta["cost_usd"] == pytest.approx(reply + 2 * 1000 * 4.00 / 1e6)
    assert meta["cost_estimated"] is True


def test_a_run_the_cap_cannot_hold_is_refused_before_any_call(one_pdf, tmp_path):
    client = _client()  # 1,000 input tokens: worst case 1000 * 4 + 64,000 * 20 = $1.284
    summary = chatbot.record_arms(
        one_pdf, tmp_path / "o", arms=[OPUS], max_usd=1.0, client=client, say=print
    )
    assert client.messages.calls == 0 and len(summary["refused_by_cap"]) == 2
    assert list((tmp_path / "o" / OPUS.id).glob("*.json")) == []


def test_the_sdk_client_is_built_without_retries_and_with_the_long_timeout(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    client = chatbot.make_client("anthropic")
    assert client.max_retries == 0
    assert client.timeout.read == chatbot.TIMEOUT_S


# ------------------------------------------------------------ scoring and the report text


def _scored(*arm_ids: str, runs: dict[str, int] | None = None):
    arms = {
        a: SimpleNamespace(expected_runs=(runs or {}).get(a, chatbot.ARM_BY_ID[a].runs))
        for a in arm_ids
    }
    return SimpleNamespace(arms=arms)


def test_the_report_names_the_arms_the_recording_holds():
    scored = _scored("chatbot-opus-purdue", "chatbot-haiku-purdue", runs={"chatbot-opus-purdue": 1})
    assert comparison.family(scored) is chatbot.CLAUDE_ARMS
    ctx = SimpleNamespace(pset=SimpleNamespace(repeats={"document": 2}))
    text = comparison.arms_text(ctx, scored)
    assert "`chatbot-opus-purdue` is the primary arm (claude-opus-5-5, one run)" in text
    assert "(claude-haiku-5-5, two runs)" in text and "expert-prompt arm is not run" in text
    assert "no system prompt" in comparison.send_text(scored)
    note = comparison.pooled_note(ctx, scored, "chatbot-opus-purdue")
    assert note.startswith("The chatbot's one run is pooled; ReadableSTEM's document task has two")
    luna = _scored("chatbot-luna-purdue")
    assert comparison.family(luna) is chatbot.ARMS
    assert "`store=False`" in comparison.send_text(luna)
