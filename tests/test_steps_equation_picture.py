"""An equation that is a picture (T2-3) has no text layer to check the reading against, so a
usable reading is always a decision (`equation.no_text_layer`), without a second reading."""

from __future__ import annotations

import pytest

from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_images, equation_step
from readablestem.pipeline.equation_step import NO_TEXT_LAYER_CODE, transcribe
from test_steps_support import equation_element, make_settings, use_fallback_router

PNG = b"\x89PNG picture of an equation"
SETTINGS = make_settings()


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def client(answer: dict) -> FakeVisionClient:
    return FakeVisionClient(responders={"equation": lambda req: answer})


def picture(**kw):
    element = equation_element(display="inline", text=None, **kw)
    return element.model_copy(update={"reasons": [equation_images.REASON]})


def test_a_confident_reading_of_a_picture_is_still_a_required_decision():
    fake = client({"is_equation": True, "latex": r"\frac{T}{18}", "confidence": 0.98})
    fix = transcribe(picture(), PNG, client=fake, settings=SETTINGS)
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert [c.code for c in fix.failed_checks] == [NO_TEXT_LAYER_CODE]
    assert fix.equation is not None and fix.equation.latex == r"\frac{T}{18}"
    # The check is no reason to read it again: one call, on the cheap model.
    assert [r.model for r in fake.requests] == [CHEAP_MODEL]
    assert MAIN_MODEL not in [r.model for r in fake.requests]


def test_an_equation_with_a_text_layer_is_accepted_as_before():
    fake = client({"is_equation": True, "latex": "x_0", "confidence": 0.98})
    element = equation_element(display="inline", text="x0")
    fix = transcribe(element, PNG, client=fake, settings=SETTINGS)
    assert fix.status is Status.ACCEPTED and fix.review == "none"


def test_a_picture_that_is_not_an_equation_is_abstained_not_checked():
    fake = client({"is_equation": False, "latex": "", "confidence": 0.9})
    fix = transcribe(picture(), PNG, client=fake, settings=SETTINGS)
    assert fix.status is Status.ABSTAINED and fix.reason == "not_equation"
    assert NO_TEXT_LAYER_CODE not in {c.code for c in fix.checks}


def test_the_code_has_a_sentence_for_the_review_screen():
    from readablestem.web.messages import CHECK_MESSAGES

    assert ("equation.no_text_layer", equation_step.NO_TEXT_LAYER_CODE) in CHECK_MESSAGES
