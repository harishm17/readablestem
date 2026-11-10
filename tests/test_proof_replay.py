"""Recording and replaying model answers: keys, ordering, misses, stand-in pictures."""

from __future__ import annotations

import io

from PIL import Image
from pydantic import BaseModel

from readablestem.core.types import ModelCall
from readablestem.models.base import VisionRequest
from readablestem.proof import replay
from readablestem.proof.replay import AnswerSet, AnswerStore, ReplayClient


class Answer(BaseModel):
    value: str


def png(color: int = 255, size: tuple[int, int] = (6, 4)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (color, color, color)).save(out, format="PNG")
    return out.getvalue()


def request(**changes) -> VisionRequest:
    base = {
        "step": "equation",
        "model": "m",
        "prompt_version": "v1",
        "instructions": "Read it.",
        "images": [png()],
    }
    return VisionRequest(**(base | changes))


def call(outcome: str = "ok") -> dict:
    return ModelCall(
        step="equation", model="m", prompt_version="v1", outcome=outcome, cost_usd=0.01
    ).model_dump(mode="json")


def model_row(req: VisionRequest, value: str | None, *, outcome: str = "ok") -> dict:
    return {
        "t": "model",
        "k": replay.request_key(req),
        "step": req.step,
        "payload": None if value is None else {"value": value},
        "call": call(outcome),
    }


# --------------------------------------------------------------------------- images


def test_an_image_is_keyed_by_its_pixels_not_by_how_the_png_was_written():
    plain = png()
    out = io.BytesIO()
    Image.open(io.BytesIO(plain)).save(out, format="PNG", optimize=True, compress_level=1)
    assert out.getvalue() != plain
    assert replay.image_identity(out.getvalue()) == replay.image_identity(plain)
    assert replay.image_identity(png(0)) != replay.image_identity(plain)
    assert replay.image_identity(png(size=(7, 4))) != replay.image_identity(plain)
    assert replay.image_identity(plain).startswith("px:")


# ----------------------------------------------------------------------------- keys


def test_a_key_changes_with_anything_the_request_carried():
    base = replay.request_key(request())
    assert base == replay.request_key(request())  # stable
    for change in (
        {"model": "m2"},
        {"prompt_version": "v2"},
        {"instructions": "Read it twice."},
        {"images": [png(1)]},
        {"images": [png(), png()]},
        {"context_text": "near the equation"},
        {"max_output_tokens": 2048},
        {"image_detail": "low"},
        {"step": "figure"},
    ):
        assert replay.request_key(request(**change)) != base, change


# ------------------------------------------------------------------- answer storage


def test_rows_are_stored_in_a_fixed_order_whatever_order_they_arrive():
    a, b = AnswerStore(), AnswerStore()
    rows = [
        {"t": "verify", "k": "b", "verdict": {}},
        {"t": "model", "k": "z"},
        {"t": "model", "k": "a"},
    ]
    for row in rows:
        a.add(row)
    for row in reversed(rows):
        b.add(row)
    assert a.rows() == b.rows()
    assert [r["k"] for r in a.rows()] == ["a", "z", "b"]
    assert a.requests() == 2


def test_the_same_request_is_answered_in_recorded_order_then_cycles():
    req = request()
    answers = AnswerSet([model_row(req, "first"), model_row(req, "second")])
    client = ReplayClient(answers, second_check=False)
    got = [client.run(req, Answer)[0] for _ in range(3)]
    assert [g.value for g in got if g] == ["first", "second", "first"]
    assert answers.stats.requests == 3 and answers.stats.reused == 1
    assert answers.stats.misses == []


def test_a_recorded_failure_is_replayed_as_the_failure_it_was():
    req = request()
    answers = AnswerSet([model_row(req, None, outcome="refused")])
    parsed, made = ReplayClient(answers, second_check=False).run(req, Answer)
    assert parsed is None and made.outcome == "refused" and made.cost_usd == 0.01
    assert answers.stats.misses == []


def test_the_second_check_replays_a_recorded_verdict_and_skips_what_it_lacks():
    verdict = {
        "check": "figure_alt",
        "probabilities": {"yes": 0.9},
        "reason_code": "ok",
        "model": "m",
        "version": "v",
        "input_tokens": 3,
        "cost_usd": 0.001,
        "latency_ms": 5,
    }
    key = replay.verify_key(replay.FIGURE_CHECK, replay.FIGURE_VERSION, png(), "alt", "cap")
    answers = AnswerSet([{"t": "verify", "k": key, "verdict": verdict}])
    verifier = replay.ReplayVerifier(answers)
    got = verifier.figure_alt(png(), alt="alt", caption="cap")
    assert got.probabilities == {"yes": 0.9} and got.cost_usd == 0.001
    missing = verifier.equation_match(png(), latex="x")
    assert missing.reason_code == "skipped"
    assert answers.stats.verify_requests == 2 and len(answers.stats.misses) == 1
