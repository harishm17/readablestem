"""Equation step with the page witness's reading (`FEATURE_PAGE_WITNESS`): agreement is a note
only (no lift, no lower review level, no skipped escalation: P(correct | agree) was 94.8% on the
D2 oracle, under 97%); a disagreement is a required decision that shows both readings, the one
nearer the extent's glyphs first; a witness-only element and a differing extent are required
decisions; with no witness reading nothing changes."""

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
    *, text="x = 5", witness: str | None = None, status: str | None = "match", display="inline"
) -> Element:
    return equation_element(display=display, text=text).model_copy(
        update={"witness_latex": witness, "witness_status": status if witness else None}
    )


def run(client, el):
    return transcribe(el, PNG, client=client, settings=SETTINGS)


def models(client: FakeVisionClient) -> list[str]:
    return [r.model for r in client.requests]


def witness_checks(fix) -> dict[str, bool]:
    return {c.name: c.passed for c in fix.checks if c.name.startswith("equation.witness")}


# ------------------------------------------------------------------------------ no witness


# --------------------------------------------------------------------------------- agree


def test_agreement_lifts_nothing_and_lowers_no_review_level():
    """The same answers with and without the witness: same status, review, confidence, calls."""
    for cheap in (out("x = 5", 0.7), out("x = 5", 0.82), out("x = 5", 0.9)):
        plain = by_model(cheap=cheap, main=out("x = 5", 0.6))
        witnessed = by_model(cheap=cheap, main=out("x = 5", 0.6))
        a = run(plain, element())
        b = run(witnessed, element(witness="x = 5"))
        assert (b.status, b.review, b.confidence) == (a.status, a.review, a.confidence)
        assert models(witnessed) == models(plain) and b.escalation == a.escalation


# ------------------------------------------------------------------------------ disagree


def test_a_disagreement_keeps_the_reading_nearer_the_glyphs_and_shows_the_other():
    """The text layer has 5; the crop (twice) read 6 and the witness read 5: the witness wins,
    the crop's reading is the other one, and a person decides."""
    client = by_model(cheap=out("x = 6", 0.97), main=out("x = 6", 0.97))
    fix = run(client, element(text="x = 5", witness="x = 5"))
    assert fix.status == Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.equation is not None
    assert fix.equation.latex == "x = 5"
    assert (fix.equation.other_latex, fix.equation.other_source) == ("x = 6", "crop")
    assert witness_checks(fix)[equation_step.WITNESS_READING] is False
    assert equation_step.WITNESS_MISMATCH_CODE in {c.code for c in fix.failed_checks}


def test_a_glyph_tie_keeps_the_crop_and_is_still_a_required_decision_with_both_readings():
    """Against the text layer "a b" the crop's "a c" and the witness's "a d" are equally far."""
    client = by_model(cheap=out("a c", 0.97), main=out("a c", 0.97))
    fix = run(client, element(text="a b", witness="a d"))
    assert fix.status == Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.equation is not None and fix.equation.latex == "a c"
    assert (fix.equation.other_latex, fix.equation.other_source) == ("a d", "page_witness")


def test_a_slash_in_the_glyphs_breaks_a_tie_on_the_letters_for_the_witness():
    """The page writes "-2/3". The crop read a fraction, the witness the slash: the letters
    tie, the slash decides for the witness, and the crop's reading is the other one (still a
    required decision). E1 dev: 2 equations of 914."""
    client = by_model(cheap=out(r"-\frac{2}{3}", 0.97), main=out(r"-\frac{2}{3}", 0.97))
    fix = run(client, element(text="\u22122/3", witness="-2/3"))
    assert fix.status == Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.equation is not None and fix.equation.latex == "-2/3"
    assert (fix.equation.other_latex, fix.equation.other_source) == (r"-\frac{2}{3}", "crop")


def test_a_witness_reading_that_does_not_convert_is_the_other_reading_never_the_proposal():
    client = by_model(cheap=out("x = 5", 0.97))
    fix = run(client, element(text="x = 5", witness=r"\frac{x"))
    assert fix.equation is not None and fix.equation.latex == "x = 5"
    assert fix.status == Status.NEEDS_REVIEW
    assert fix.equation.other_latex == r"\frac{x"


# ------------------------------------------------------------------ extent, witness-only


def test_an_extent_that_differs_cannot_agree_and_is_a_decision_when_the_readings_differ():
    client = by_model(cheap=out("x = 5", 0.9))
    fix = run(client, element(text="x = 5", witness="x = 5 + 1", status="extent"))
    assert fix.status == Status.NEEDS_REVIEW
    assert witness_checks(fix) == {equation_step.WITNESS_EXTENT: False}
    assert fix.equation is not None and fix.equation.other_latex == "x = 5 + 1"
    assert models(client) == [CHEAP_MODEL]  # 0.9 is above the accept bar: no second reading anyway


def test_a_formula_only_the_witness_found_is_always_a_required_decision():
    client = by_model(cheap=out("x = 5", 0.97))
    fix = run(client, element(text="x = 5", witness="x = 5", status="added"))
    assert fix.status == Status.NEEDS_REVIEW and fix.review == "required"
    assert equation_step.WITNESS_ADDED_CODE in {c.code for c in fix.failed_checks}
    assert witness_checks(fix)[equation_step.WITNESS_READING] is True  # the crop agrees: no other
    assert fix.equation is not None and fix.equation.other_latex is None
