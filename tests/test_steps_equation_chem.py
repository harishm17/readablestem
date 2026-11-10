r"""The equation step with chemistry: the model may answer in mhchem (`\ce{...}`), which is
written out as plain LaTeX before the checks; what cannot be converted is refused, not
guessed; the text-layer cross-check works on the converted reading; the prompt asks for it."""

from __future__ import annotations

import pytest

from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline.equation_step import transcribe
from test_steps_support import equation_element, make_settings, use_fallback_router

PNG = b"\x89PNG equation crop"
SETTINGS = make_settings()


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def out(latex: str, confidence: float = 0.95) -> dict:
    return {"is_equation": True, "latex": latex, "confidence": confidence}


def by_model(**answers) -> FakeVisionClient:
    table = {MAIN_MODEL: answers.get("main"), CHEAP_MODEL: answers.get("cheap")}
    return FakeVisionClient(responders={"equation": lambda req: table[req.model]})


def run(client, element):
    return transcribe(element, PNG, client=client, settings=SETTINGS)


def codes(fix) -> set[str | None]:
    return {c.code for c in fix.failed_checks}


def test_a_ce_answer_is_converted_checked_and_spoken():
    client = by_model(cheap=out(r"\ce{H3O+}"))
    fix = run(client, equation_element(display="inline", text="H3O+"))
    assert fix.status == Status.ACCEPTED
    assert fix.equation is not None
    assert fix.equation.latex == r"\text{H}_3\text{O}^+"
    assert fix.equation.speech == "H 3 O plus"
    assert "<mi>H</mi>" in fix.equation.mathml
    assert 'intent=":chemical-formula"' in fix.equation.mathml
    assert [r.model for r in client.requests] == [CHEAP_MODEL]


def test_ce_inside_ordinary_latex_is_converted_too():
    fix = run(
        by_model(main=out(r"K = \frac{[\ce{NH3}]^2}{[\ce{N2}][\ce{H2}]^3}")),
        equation_element(text="K = [NH3]2 [N2][H2]3"),
    )
    assert fix.equation is not None
    assert fix.equation.latex == r"K = \frac{[\text{NH}_3]^2}{[\text{N}_2][\text{H}_2]^3}"
    assert fix.status == Status.ACCEPTED


def test_ce_outside_the_subset_is_refused_and_never_accepted():
    client = by_model(cheap=out(r"\ce{N2 + 3H2 <=>> 2NH3}"), main=out(r"\ce{N2 + 3H2 <=>> 2NH3}"))
    fix = run(client, equation_element(display="inline", text="N2 + 3H2"))
    assert fix.status == Status.FAILED and fix.reason == "invalid_latex"
    assert "unsupported_ce" in codes(fix)
    assert fix.equation is None
    # The cheap model's refused answer sent the crop to the main model.
    assert [r.model for r in client.requests] == [CHEAP_MODEL, MAIN_MODEL]


def test_the_text_layer_still_checks_a_ce_reading():
    # The model reads SO3 where the page says SO4: a symbol mismatch, so a decision.
    client = by_model(cheap=out(r"\ce{SO3^2-}"), main=out(r"\ce{SO3^2-}"))
    fix = run(client, equation_element(display="inline", text="SO42−"))
    assert fix.status == Status.NEEDS_REVIEW
    assert "symbol_mismatch" in codes(fix) or "text_layer_mismatch" in codes(fix)
