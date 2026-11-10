"""Escalation of a cheap equation answer (T1-10) and the record of what it did.

On the 2026-10 runs the main model's second reading gave the same LaTeX for most escalated
answers, and the cheap model's confidence between 0.80 and the accept bar (0.85) with every
check passing was the commonest reason to ask again. That reason alone no longer asks the
main model; `Fix.escalation` says what happened ("skipped", "same", "changed", "failed").
"""

from __future__ import annotations

import pytest

from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step
from readablestem.pipeline.equation_step import transcribe
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


def test_a_failed_check_in_the_band_still_asks_again():
    # The text layer says "x0 + 5" and the cheap answer is "x_0": a symbol mismatch.
    client = by_model(cheap=out("x_0", 0.82), main=out("x_0 + 5", 0.97))
    fix = run(client, text="x0 + 5")
    assert models(client) == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.escalation == "changed"
    assert fix.equation is not None and fix.equation.latex == "x_0 + 5"


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
