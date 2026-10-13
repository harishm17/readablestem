"""Recording and replaying model answers: keys, ordering, misses, stand-in pictures."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from pydantic import BaseModel

from a11y_stem.core.types import Check, ModelCall
from a11y_stem.models.base import VisionRequest
from a11y_stem.proof import replay
from a11y_stem.proof.replay import AnswerSet, AnswerStore, ReplayClient


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


def test_bytes_that_are_not_an_image_are_keyed_by_the_bytes():
    assert replay.image_identity(b"not a picture").startswith("raw:")


def test_a_standin_declares_the_recorded_identity_and_size():
    identity = replay.image_identity(png(17, (30, 12)))
    standin = replay.standin_png(identity, 30, 12)
    assert replay.declared_identity(standin) == identity
    assert replay.image_identity(standin) == identity
    assert replay.png_size(standin) == (30, 12)
    assert replay.declared_identity(png()) is None
    assert replay.declared_identity(b"short") is None


def test_standin_sizes_are_clamped():
    big = replay.standin_png("px:x", 10**6, 0)
    assert replay.png_size(big) == (replay.MAX_STANDIN_SIDE, 1)


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


def test_a_verify_key_depends_on_the_check_version_picture_and_texts():
    base = replay.verify_key("figure", "v1", png(), "alt", "caption")
    assert base == replay.verify_key("figure", "v1", png(), "alt", "caption")
    for args in (
        ("equation", "v1", png(), "alt", "caption"),
        ("figure", "v2", png(), "alt", "caption"),
        ("figure", "v1", png(9), "alt", "caption"),
        ("figure", "v1", png(), "other", "caption"),
        ("figure", "v1", png(), "alt", None),
    ):
        assert replay.verify_key(*args) != base


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


def test_a_request_with_no_recorded_answer_is_a_counted_miss_and_never_an_invention():
    answers = AnswerSet([model_row(request(), "x")])
    client = ReplayClient(answers, second_check=False)
    parsed, made = client.run(request(instructions="Something else."), Answer)
    assert parsed is None and made.outcome == "refused"
    assert len(answers.stats.misses) == 1 and answers.stats.misses[0].startswith("model equation ")
    assert answers.stats.as_dict()["misses"] == 1  # content-free: a count, not the text


def test_a_recorded_failure_is_replayed_as_the_failure_it_was():
    req = request()
    answers = AnswerSet([model_row(req, None, outcome="refused")])
    parsed, made = ReplayClient(answers, second_check=False).run(req, Answer)
    assert parsed is None and made.outcome == "refused" and made.cost_usd == 0.01
    assert answers.stats.misses == []


def test_an_answer_the_schema_no_longer_accepts_counts_as_a_miss():
    req = request()
    row = model_row(req, "x")
    row["payload"] = {"unexpected": 1}
    answers = AnswerSet([row])
    parsed, made = ReplayClient(answers, second_check=False).run(req, Answer)
    assert parsed is None and made.outcome == "invalid_output"
    assert answers.stats.misses and answers.stats.misses[0].startswith("schema equation")


def test_an_unknown_row_type_is_refused():
    with pytest.raises(replay.BundleError, match="unknown type 'odd'"):
        AnswerSet([{"t": "odd", "k": "x"}])


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


def test_axe_results_replay_by_html_and_an_unknown_page_is_skipped_and_counted():
    html = "<main>x</main>"
    check = Check(name="axe", passed=True)
    answers = AnswerSet(
        [{"t": "axe", "k": replay.axe_key(html), "checks": [check.model_dump(mode="json")]}]
    )
    axe = replay.AxeReplay(answers)
    assert axe(html) == [check]
    assert axe("<main>other</main>") == [replay.html_checks.AXE_SKIPPED]
    assert answers.stats.axe_requests == 2 and len(answers.stats.misses) == 1


def test_axe_is_recorded_as_it_ran():
    store = AnswerStore()
    check = Check(name="axe", passed=False)
    recorder = replay.AxeRecorder(store, lambda html: [check])
    assert recorder("<p>x</p>") == [check]
    (row,) = store.rows()
    assert row["t"] == "axe" and row["k"] == replay.axe_key("<p>x</p>")


def test_patched_axe_replaces_the_checker_only_inside_the_block():
    original = replay.html_checks.axe_checks
    with replay.patched_axe(lambda html: []):
        assert replay.html_checks.axe_checks("x") == []
    assert replay.html_checks.axe_checks is original
