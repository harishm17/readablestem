"""The answers file: a request is asked once, then replayed; what changes the answer asks again."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from readablestem.config import Settings
from readablestem.eval.answers import CachedClient, answer_key, legacy_key, load_rows
from readablestem.models.base import VisionRequest
from readablestem.models.fake import FakeVisionClient
from readablestem.models.openai_client import PNG_SIGNATURE
from readablestem.pipeline import run
from readablestem.pipeline.equation_step import EquationOutput
from readablestem.pipeline.figure_step import FigureOutput

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
    # Answered live, so not unanswered: a live held-out run once set every document apart.
    assert first.misses - first.answered_live == 0

    again = CachedClient(inner, path, settings())  # a later run, same file
    parsed2, call2 = again.run(request(), EquationOutput)
    assert parsed2 == parsed and call2.cost_usd == call.cost_usd
    assert len(inner.requests) == 1  # nothing was sent
    assert again.hits == 1 and again.replayed_cost_usd == call.cost_usd


def test_the_effort_the_client_will_ask_for_is_part_of_the_key() -> None:
    low = settings(anthropic_effort_main="low")
    high = settings(anthropic_effort_main="high")

    def key(s: Settings, **kw: Any) -> str:
        return answer_key(s, request(**kw), EquationOutput)

    assert key(low) != key(high)
    assert key(low) == key(low)
    assert key(low, effort="high") == key(high)


def test_the_key_is_the_question_as_sent_and_not_the_release(monkeypatch) -> None:
    s = settings()
    key = answer_key(s, request(context_text="near " * 40), EquationOutput)

    def asked(s: Settings = s, schema: type = EquationOutput, **kw: Any) -> str:
        kw.setdefault("context_text", "near " * 40)
        return answer_key(s, request(**kw), schema)

    # What the model is sent, or how its answer is read, is a new question...
    assert asked(instructions="Transcribe carefully.") != key  # an edited prompt, same version
    assert asked(schema=FigureOutput) != key  # another answer format
    assert asked(context_limit=20) != key  # less of the nearby text is sent
    # ...and a new release that sends the same thing is the same question.
    monkeypatch.setattr(run, "PIPELINE_VERSION", "99.0.0")
    assert asked() == key


def test_an_offline_miss_says_what_changed_since_the_recording(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    inner = FakeVisionClient(defaults={"equation": ANSWER})
    CachedClient(inner, path, settings()).run(request(), EquationOutput)

    offline = CachedClient(None, path, settings())
    offline.run(request(prompt_version="equation-next", instructions="New."), EquationOutput)
    offline.run(request(images=[PNG + b"another"]), EquationOutput)
    offline.run(request(step="figure"), EquationOutput)
    assert offline.summary()["answers_file_missed_because"] == {
        "prompt equation-test recorded, equation-next asked": 1,
        "no answer recorded for this picture": 1,
        "no answer recorded for this step": 1,
    }
    assert len(inner.requests) == 1


def test_a_recording_under_the_older_key_is_moved_once_and_then_found(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    s = settings()
    parsed, call = FakeVisionClient(defaults={"equation": ANSWER}).run(request(), EquationOutput)
    CachedClient(None, path, s).record(legacy_key(s, request()), "equation", parsed, call)

    plain = CachedClient(None, path, s)
    assert plain.run(request(), EquationOutput)[0] is None  # the older key is not looked up

    moving = CachedClient(None, path, s, rekey=True)
    assert moving.run(request(), EquationOutput)[0] == parsed
    assert moving.summary()["answers_file_rekeyed"] == 1
    assert len(load_rows(path)) == 2  # the row was written again under the new key

    after = CachedClient(None, path, s)
    assert after.run(request(), EquationOutput)[0] == parsed and after.misses == 0


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


def test_a_run_stops_paying_for_new_answers_at_its_limit(tmp_path: Path) -> None:
    path = tmp_path / "answers.jsonl"
    inner = FakeVisionClient(defaults={"equation": ANSWER})
    first = CachedClient(inner, path, settings())
    _, paid = first.run(request(), EquationOutput)
    assert paid.cost_usd > 0

    # The limit is for the process and the file: a second client finds it spent. What the
    # file already holds is still answered, and is not counted as spending.
    capped = CachedClient(inner, path, settings(), live_limit_usd=paid.cost_usd)
    parsed, call = capped.run(request(images=[PNG + b"2"]), EquationOutput)
    assert parsed is None and call.outcome == "refused" and call.cost_usd == 0.0
    assert capped.run(request(), EquationOutput)[0] is not None
    assert len(inner.requests) == 1 and len(load_rows(path)) == 1


def test_an_answer_lost_to_an_outage_is_not_kept(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    inner = FakeVisionClient(fail_models={"claude-opus-5-5": "api_error"})
    lost = CachedClient(inner, path, settings())
    lost.run(request(), EquationOutput)
    assert load_rows(path) == [] and lost.misses - lost.answered_live == 1
