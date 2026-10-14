"""The answers file: a request is asked once, then replayed; what changes the answer asks again."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from a11y_stem.config import Settings
from a11y_stem.eval.answers import CachedClient, answer_key, load_rows
from a11y_stem.eval.pipeline_eval import select_units
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.openai_client import PNG_SIGNATURE
from a11y_stem.pipeline.equation_step import EquationOutput

PNG = PNG_SIGNATURE + b"one crop"
ANSWER = {"is_equation": True, "latex": "x^2", "confidence": 0.9}


def settings(**kw: Any) -> Settings:
    kw.setdefault("model_provider", "anthropic")
    kw.setdefault("anthropic_api_key", "sk-ant-test")
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def request(**kw: Any) -> VisionRequest:
    fields: dict[str, Any] = {
        "step": "equation",
        "model": "claude-opus-5-5",
        "prompt_version": "equation-test",
        "instructions": "Transcribe.",
        "images": [PNG],
    }
    fields.update(kw)
    return VisionRequest(**fields)


def test_a_request_is_asked_once_and_replayed_with_its_recorded_cost(tmp_path: Path) -> None:
    path = tmp_path / "answers.jsonl"
    inner = FakeVisionClient(defaults={"equation": ANSWER})
    first = CachedClient(inner, path, settings())
    parsed, call = first.run(request(), EquationOutput)
    assert parsed is not None and parsed.latex == "x^2"
    assert len(inner.requests) == 1 and first.misses == 1

    again = CachedClient(inner, path, settings())  # a later run, same file
    parsed2, call2 = again.run(request(), EquationOutput)
    assert parsed2 == parsed and call2.cost_usd == call.cost_usd
    assert len(inner.requests) == 1  # nothing was sent
    assert again.hits == 1 and again.replayed_cost_usd == call.cost_usd


def test_the_effort_the_client_will_ask_for_is_part_of_the_key() -> None:
    low = settings(anthropic_effort_main="low")
    high = settings(anthropic_effort_main="high")
    assert answer_key(low, request()) != answer_key(high, request())
    assert answer_key(low, request()) == answer_key(low, request())
    assert answer_key(low, request(effort="high")) == answer_key(high, request())


def test_a_changed_picture_or_context_asks_again(tmp_path: Path) -> None:
    inner = FakeVisionClient(defaults={"equation": ANSWER})
    client = CachedClient(inner, tmp_path / "a.jsonl", settings())
    client.run(request(), EquationOutput)
    client.run(request(images=[PNG + b"!"]), EquationOutput)
    client.run(request(context_text="near the equation"), EquationOutput)
    client.run(request(), EquationOutput)
    assert len(inner.requests) == 3 and client.hits == 1


def test_offline_refuses_a_missing_answer_and_sends_nothing(tmp_path: Path) -> None:
    client = CachedClient(None, tmp_path / "none.jsonl", settings())
    parsed, call = client.run(request(), EquationOutput)
    assert parsed is None and call.outcome == "refused" and call.cost_usd == 0.0
    assert not (tmp_path / "none.jsonl").exists()


def test_an_answer_lost_to_an_outage_is_not_kept(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    inner = FakeVisionClient(fail_models={"claude-opus-5-5": "api_error"})
    CachedClient(inner, path, settings()).run(request(), EquationOutput)
    assert load_rows(path) == []


def test_every_keeps_each_nth_unit_before_the_limit() -> None:
    records = [{"kind": "equation", "id": f"e{i}"} for i in range(10)]
    ids = [u["id"] for u in select_units("equations", records, None, 3)]
    assert ids == ["e0", "e3", "e6", "e9"]
    assert [u["id"] for u in select_units("equations", records, 2, 3)] == ["e0", "e3"]
