"""Equation step with the page math pass's second reading (ADR 0006): agreement lifts the
confidence, disagreement asks the main model once, and a reading nobody shares is a decision;
layout-only spans lose a little confidence; with no second reading nothing changes."""

from __future__ import annotations

import pytest

from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import Element, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step
from readablestem.pipeline.equation_step import transcribe
from test_steps_support import equation_element, make_settings, use_fallback_router

PNG = b"\x89PNG equation crop"
SETTINGS = make_settings()


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def out(latex: str, confidence: float = 0.95) -> dict:
    return {"is_equation": True, "latex": latex, "confidence": confidence}


def by_model(*, main=None, cheap=None) -> FakeVisionClient:
    table = {MAIN_MODEL: main, CHEAP_MODEL: cheap}
    return FakeVisionClient(responders={"equation": lambda req: table[req.model]})


def element(
    *, display="inline", text="x = 5", page_latex: str | None = None, status: str | None = "span"
) -> Element:
    return equation_element(display=display, text=text).model_copy(
        update={"page_latex": page_latex, "page_status": status}
    )


def run(client, el):
    return transcribe(el, PNG, client=client, settings=SETTINGS)


def models(client: FakeVisionClient) -> list[str]:
    return [r.model for r in client.requests]


def page_checks(fix) -> dict[str, bool]:
    return {c.name: c.passed for c in fix.checks if c.name.startswith("equation.page_reading")}


# --------------------------------------------------------------------------- no reading


# --------------------------------------------------------------------------- agreement


def test_two_readings_that_agree_lift_a_cheap_answer_over_the_bar_without_a_second_ask():
    """0.8 alone would be a decision (below the 0.85 accept bar); with the page's reading
    saying the same, it is accepted, and the main model is not asked."""
    client = by_model(cheap=out("x = 5", 0.8))
    fix = run(client, element(page_latex="x=5"))
    assert fix.status == Status.ACCEPTED
    assert models(client) == [CHEAP_MODEL]
    assert fix.confidence == pytest.approx(equation_step.PAGE_AGREE_CONFIDENCE)
    assert page_checks(fix) == {equation_step.PAGE_READING: True}


def test_agreement_never_overrides_a_failed_text_layer_check():
    """The page pass and the crop both read a 6 where the PDF's text layer has a 5."""
    client = by_model(cheap=out("x = 6", 0.95), main=out("x = 6", 0.95))
    fix = run(client, element(page_latex="x = 6", text="x = 5"))
    assert fix.status == Status.NEEDS_REVIEW
    assert {c.code for c in fix.failed_checks} & {"symbol_mismatch", "text_layer_mismatch"}


# ------------------------------------------------------------------------- disagreement


def test_a_disagreement_asks_the_main_model_once_and_its_agreement_with_the_page_stands():
    client = by_model(cheap=out("x = 6", 0.97), main=out("x = 5", 0.95))
    fix = run(client, element(page_latex="x = 5"))
    assert models(client) == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.equation is not None and fix.equation.latex == "x = 5"
    assert fix.status == Status.ACCEPTED
    assert page_checks(fix) == {equation_step.PAGE_READING: True}
    assert fix.escalation == "changed"


def test_when_the_main_model_reads_the_crop_as_the_cheap_one_did_the_page_was_the_odd_one_out():
    client = by_model(cheap=out("x = 5", 0.97), main=out("x = 5", 0.95))
    fix = run(client, element(page_latex="x = 7"))
    assert models(client) == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.status == Status.ACCEPTED
    assert page_checks(fix) == {equation_step.PAGE_READING_OVERRULED: True}
    assert fix.escalation == "same"


def test_a_reading_that_matches_neither_the_first_nor_the_page_is_a_required_decision():
    client = by_model(cheap=out("x = 5", 0.97), main=out("x = 8", 0.95))
    fix = run(client, element(page_latex="x = 7", text=None))
    assert fix.status == Status.NEEDS_REVIEW and fix.review == "required"
    assert page_checks(fix) == {equation_step.PAGE_READING: False}
    assert equation_step.PAGE_READING_CODE in {c.code for c in fix.failed_checks}


def test_a_main_first_reading_the_page_contradicts_has_no_one_left_to_ask():
    client = by_model(main=out("x = 5", 0.97))
    fix = run(client, element(display="block", page_latex="x = 7", text=None))
    assert models(client) == [MAIN_MODEL]
    assert fix.status == Status.NEEDS_REVIEW
    assert page_checks(fix) == {equation_step.PAGE_READING: False}


def test_a_page_reading_that_does_not_convert_is_no_reading():
    client = by_model(cheap=out("x = 5", 0.9))
    fix = run(client, element(page_latex=r"\begin{unknown"))
    assert fix.status == Status.ACCEPTED and models(client) == [CHEAP_MODEL]
    assert page_checks(fix) == {}


# ----------------------------------------------------------------------------- layout only


def test_a_letters_weight_is_not_part_of_the_comparison_of_two_readings():
    """The page pass often reads a bold vector as a plain letter; weight is the text-layer
    checks' business, so the readings still agree."""
    client = by_model(cheap=out(r"\mathbf{d}_0", 0.8))
    fix = run(client, element(page_latex="d_0", text="d0"))
    assert page_checks(fix) == {equation_step.PAGE_READING: True}
    assert models(client) == [CHEAP_MODEL]
