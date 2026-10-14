"""Escalation of a cheap equation answer (T1-10) and the record of what it did.

On the 2026-10 runs the main model's second reading gave the same LaTeX for most escalated
answers, and the cheap model's confidence between 0.80 and the accept bar (0.85) with every
check passing was the commonest reason to ask again. That reason alone no longer asks the
main model; `Fix.escalation` says what happened ("skipped", "same", "changed", "failed").
"""

from __future__ import annotations

import pytest

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.types import Fix, Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import equation_step
from a11y_stem.pipeline.equation_step import SKIP_ESCALATION_FROM, transcribe
from test_steps_support import equation_element, make_settings, use_fallback_router

PNG = b"\x89PNG equation crop"
SETTINGS = make_settings()


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def out(latex: str = "x_0", confidence: float = 0.95, is_eq=True):
    return {"is_equation": is_eq, "latex": latex, "confidence": confidence}


def by_model(**answers) -> FakeVisionClient:
    table = {MAIN_MODEL: answers.get("main"), CHEAP_MODEL: answers.get("cheap")}
    return FakeVisionClient(responders={"equation": lambda req: table[req.model]})


def run(client, **element):
    element = {"display": "inline", "text": "x0"} | element
    return transcribe(equation_element(**element), PNG, client=client, settings=SETTINGS)


def models(client: FakeVisionClient) -> list[str]:
    return [r.model for r in client.requests]


def test_the_band_is_from_0_80_up_to_the_accept_bar():
    assert SKIP_ESCALATION_FROM == 0.80 < SETTINGS.auto_accept_confidence == 0.85


@pytest.mark.parametrize("confidence", [0.80, 0.82, 0.849])
def test_a_confidence_in_the_band_with_every_check_passing_is_not_asked_again(confidence):
    client = by_model(cheap=out("x_0", confidence), main=out("x_0", 0.99))
    fix = run(client)
    assert models(client) == [CHEAP_MODEL]
    assert fix.escalation == "skipped"
    assert [c.model for c in fix.calls] == [CHEAP_MODEL]
    # Accepted as it stands, with a note that it was read once: not a required decision.
    assert fix.status is Status.ACCEPTED and fix.review == "optional"
    assert [c.code for c in fix.failed_checks] == ["single_reading"]
    assert fix.equation is not None and fix.equation.latex == "x_0"
    assert fix.confidence == pytest.approx(confidence)


def test_without_accepting_the_single_reading_it_needs_a_decision(monkeypatch):
    monkeypatch.setattr(equation_step, "ACCEPT_SINGLE_READING", False)
    fix = run(by_model(cheap=out("x_0", 0.82), main=out("x_0", 0.99)))
    assert fix.escalation == "skipped"
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.equation is not None and fix.equation.latex == "x_0"


def test_the_single_reading_note_does_not_lift_a_low_confidence():
    # 0.79 is below the band: asked again; and the note is only added to a skipped answer.
    fix = run(by_model(cheap=out("x_0", 0.79), main=out("x_0", 0.9)))
    assert fix.escalation == "same" and "single_reading" not in {c.code for c in fix.checks}


@pytest.mark.parametrize("confidence", [0.79, 0.6, 0.0])
def test_below_the_band_the_main_model_is_still_asked(confidence):
    client = by_model(cheap=out("x_0", confidence), main=out("x_0", 0.96))
    fix = run(client)
    assert models(client) == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.escalation == "same"
    assert fix.status is Status.ACCEPTED


def test_a_confident_cheap_answer_has_no_escalation_record():
    client = by_model(cheap=out("x_0", 0.9))
    fix = run(client)
    assert models(client) == [CHEAP_MODEL] and fix.escalation is None
    assert "escalation" not in fix.model_dump(mode="json")  # a record without it is unchanged


def test_a_main_model_start_has_no_escalation_record():
    client = by_model(main=out(r"\Delta t = 5", 0.99))
    fix = run(client, display="block", text=None)
    assert models(client) == [MAIN_MODEL] and fix.escalation is None


def test_a_failed_check_in_the_band_still_asks_again():
    # The text layer says "x0 + 5" and the cheap answer is "x_0": a symbol mismatch.
    client = by_model(cheap=out("x_0", 0.82), main=out("x_0 + 5", 0.97))
    fix = run(client, text="x0 + 5")
    assert models(client) == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.escalation == "changed"
    assert fix.equation is not None and fix.equation.latex == "x_0 + 5"


def test_an_invalid_cheap_answer_in_the_band_still_asks_again():
    client = by_model(cheap=out(r"\foo{x}", 0.82), main=out("x_0", 0.95))
    fix = run(client)
    assert models(client) == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.escalation == "changed" and fix.status is Status.ACCEPTED


def test_a_failed_speech_note_in_the_band_keeps_the_old_rule(monkeypatch):
    from a11y_stem.pipeline.speech import SpeechError

    def broken(*args, **kwargs):
        raise SpeechError("no")

    monkeypatch.setattr(equation_step, "mathml_to_speech", broken)
    client = by_model(cheap=out("x_0", 0.82), main=out("x_0", 0.96))
    run(client)
    assert models(client) == [CHEAP_MODEL, MAIN_MODEL]  # "no failed check" means none at all


def test_the_record_says_whether_the_second_reading_changed_the_answer():
    same = run(by_model(cheap=out("x_0", 0.6), main=out("x_0", 0.96)))
    spaced = run(by_model(cheap=out("x_0", 0.6), main=out("x _ 0", 0.96)))
    braced = run(by_model(cheap=out("x_{0}", 0.6), main=out("x_0", 0.96)))  # one MathML
    changed = run(by_model(cheap=out("x_0", 0.6), main=out("x_1", 0.96)))
    assert [f.escalation for f in (same, spaced, braced, changed)] == [
        "same", "same", "same", "changed",
    ]  # fmt: skip
    assert changed.equation is not None and changed.equation.latex == "x_1"


def test_a_second_reading_with_no_usable_answer_is_recorded_and_the_first_stands():
    client = by_model(cheap=out("x_0", 0.6), main=out(r"\input{x}", 0.99))
    fix = run(client)
    assert fix.escalation == "failed"
    assert fix.equation is not None and fix.equation.latex == "x_0"


def test_a_cheap_answer_that_was_not_usable_and_a_good_second_reading_is_changed():
    fix = run(by_model(cheap=out(r"\foo{x}", 0.99), main=out("x_0", 0.95)))
    assert fix.escalation == "changed"


def test_the_record_survives_the_json_round_trip_and_old_records_load():
    fix = run(by_model(cheap=out("x_0", 0.82), main=out("x_0", 0.99)))
    again = Fix.model_validate(fix.model_dump(mode="json"))
    assert again.escalation == "skipped"
    old = fix.model_dump(mode="json")
    old.pop("escalation")
    assert Fix.model_validate(old).escalation is None


def test_the_second_check_shadow_record_is_kept_with_the_escalation_record():
    fix = run(by_model(cheap=out("x_0", 0.6), main=out("x_0", 0.96)))
    assert fix.escalation == "same" and fix.verifications == []  # offline: no verifier


def test_half_a_fraction_is_never_accepted_without_a_decision():
    # Layout found a bar against the region with glyphs on its other side: the crop shows
    # half of a fraction. A confident, passing answer still waits for a decision, with the
    # reason as a failed check, and its confidence is held under the accept bar.
    client = by_model(cheap=out("Q(x)", 0.99), main=out("Q(x)", 0.99))
    fix = run(client, text="Q(x)", reasons=["half a fraction"])
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert [c.code for c in fix.failed_checks] == [equation_step.HALF_FRACTION_CODE]
    assert fix.confidence is not None and fix.confidence < SETTINGS.auto_accept_confidence
    assert models(client) == [CHEAP_MODEL]  # a second reading cannot show the other half


def test_an_equation_without_the_reason_is_accepted_as_before():
    fix = run(by_model(cheap=out("x_0", 0.99), main=out("x_0", 0.99)))
    assert fix.status is Status.ACCEPTED
