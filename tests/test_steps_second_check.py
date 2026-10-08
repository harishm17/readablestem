"""The second automated check in the steps: an optional note on accepted figures, and
shadow answers on equations that change nothing (FakeVisionClient + FakeVerifier)."""

from __future__ import annotations

import json
import logging

import pytest

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import DocumentResult, Fix, Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.verifier import EQUATION_CHECK, FIGURE_CHECK, FakeVerifier
from a11y_stem.pipeline.equation_step import transcribe
from a11y_stem.pipeline.figure_step import describe
from a11y_stem.web.messages import explain_check
from test_steps_support import (
    equation_element,
    figure_element,
    make_settings,
    use_fallback_router,
)

PNG = b"\x89PNG crop"
SETTINGS = make_settings()
ALT = "Bar chart of votes: A has 2, B has 5 and C has 3."
NOTE = "A second automated check found a possible mismatch between the alt text and the image; worth a look."  # noqa: E501


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def figure_out(alt=ALT, confidence=0.95, *, decorative=False, figure_type="chart"):
    return {"figure_type": figure_type, "decorative": decorative, "alt": alt,
            "confidence": confidence}  # fmt: skip


def figure_client(answer, verifier=None) -> FakeVisionClient:
    return FakeVisionClient(responders={"figure": lambda req: answer}, verifier=verifier)


def scores(ok: float, fe: float) -> FakeVerifier:
    return FakeVerifier(figure={"ok": ok, "factual_error": fe})


def describe_with(answer, verifier=None, settings=SETTINGS, element=None) -> Fix:
    element = element or figure_element(caption="Figure 1. Votes.")
    return describe(element, PNG, client=figure_client(answer, verifier), settings=settings)


def without_check_fields(fix: Fix) -> dict:
    data = fix.model_dump()
    data.pop("verifications")
    data["checks"] = [c for c in data["checks"] if c["name"] != "figure.second_check"]
    data.pop("review")
    return data


# --------------------------------------------------------------------------- figures


def test_an_accepted_figure_with_no_contradiction_stays_as_it_was():
    verifier = scores(ok=0.95, fe=0.03)
    fix = describe_with(figure_out(), verifier)
    plain = describe_with(figure_out())
    assert fix.status is Status.ACCEPTED and fix.review == "none"
    assert fix.confidence == plain.confidence
    (check,) = [c for c in fix.checks if c.name == "figure.second_check"]
    assert check.passed and check.severity == "warning"
    (record,) = fix.verifications
    assert (record.check, record.stage, record.mode, record.outcome) == (
        FIGURE_CHECK, "final", "note", "ok")  # fmt: skip
    assert record.probabilities == {"ok": 0.95, "factual_error": 0.03}
    assert record.flagged is False
    assert verifier.requests == [(FIGURE_CHECK, ALT)]
    assert fix.cost_usd == pytest.approx(sum(c.cost_usd for c in fix.calls) + record.cost_usd)


@pytest.mark.parametrize(("ok", "fe"), [(0.4, 0.1), (0.9, 0.7), (0.49, 0.0)])
def test_a_possible_mismatch_is_an_optional_note_never_a_decision(ok, fe):
    fix = describe_with(figure_out(), scores(ok=ok, fe=fe))
    assert fix.status is Status.ACCEPTED and fix.review == "optional"
    assert fix.optional_note and not fix.pending
    assert fix.confidence == pytest.approx(0.95)
    (check,) = fix.failed_checks
    assert (check.name, check.code, check.severity) == (
        "figure.second_check", "second_check_mismatch", "warning")  # fmt: skip
    assert explain_check(check).text == NOTE
    assert fix.verifications[0].flagged is True
    # Everything but the note is what the figure would have been without the check.
    assert without_check_fields(fix) == without_check_fields(describe_with(figure_out()))


def test_the_pre_registered_threshold_is_strictly_below_one_half():
    at = describe_with(figure_out(), scores(ok=0.5, fe=0.5))
    assert at.review == "none" and not at.failed_checks
    stricter = make_settings(decisions_figure_flag_below=0.6)
    assert describe_with(figure_out(), scores(ok=0.55, fe=0.0), stricter).review == "optional"


def test_existing_notes_stay_and_the_flag_adds_one():
    long_alt = "Bar chart " + "x" * 160
    fix = describe_with(figure_out(long_alt), scores(ok=0.1, fe=0.9))
    assert fix.status is Status.ACCEPTED and fix.review == "optional"
    assert {c.code for c in fix.failed_checks} == {"too_long", "second_check_mismatch"}


@pytest.mark.parametrize("outcome", ["timeout", "api_error", "refused", "invalid_output",
                                     "skipped"])  # fmt: skip
def test_no_answer_leaves_the_figure_exactly_as_today(outcome):
    verifier = FakeVerifier(outcomes={FIGURE_CHECK: outcome})
    fix = describe_with(figure_out(), verifier)
    plain = describe_with(figure_out())
    assert fix.model_dump(exclude={"verifications"}) == plain.model_dump(exclude={"verifications"})
    (record,) = fix.verifications
    assert (record.outcome, record.flagged, record.probabilities) == (outcome, None, {})


def test_no_budget_left_for_the_check_is_fail_open_too():
    job = JobBudget(InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=1)
    client = FakeVisionClient(
        responders={"figure": lambda req: figure_out()}, budget=job,
        verifier=FakeVerifier(budget=job),
    )  # fmt: skip
    fix = describe(figure_element(), PNG, client=client, settings=SETTINGS)
    assert fix.status is Status.ACCEPTED and fix.review == "none"
    assert fix.verifications[0].outcome == "budget_exceeded"


@pytest.mark.parametrize(
    "answer",
    [
        figure_out(confidence=0.7),  # needs review already
        figure_out(confidence=0.2),  # abstained
        figure_out("", decorative=True, figure_type="photo", confidence=0.99),  # decorative
        figure_out("image.png"),  # failed check
        None,  # refused
    ],
)
def test_only_accepted_descriptions_are_checked(answer):
    verifier = FakeVerifier()
    fix = describe_with(answer, verifier, element=figure_element())
    assert verifier.requests == [] and fix.verifications == []


def test_the_setting_turns_it_off():
    verifier = FakeVerifier()
    off = make_settings(decisions_figure_verify=False)
    fix = describe_with(figure_out(), verifier, off)
    assert verifier.requests == [] and fix.verifications == []


def test_no_document_text_in_the_record_or_the_logs(caplog):
    secret = "SECRET-ALT-TEXT figure of votes"
    element = figure_element(caption="SECRET-CAPTION Figure 1.")
    with caplog.at_level(logging.DEBUG):
        fix = describe_with(figure_out(secret), scores(ok=0.1, fe=0.9), element=element)
    stored = json.dumps([v.model_dump() for v in fix.verifications])
    stored += json.dumps([c.model_dump() for c in fix.checks])
    for marker in ("SECRET-ALT-TEXT", "SECRET-CAPTION"):
        assert marker not in stored and marker not in caplog.text


# ------------------------------------------------------------------------- equations


def eq_out(latex=r"F = m a", confidence=0.95):
    return {"is_equation": True, "latex": latex, "confidence": confidence}


def eq_client(verifier=None, **answers) -> FakeVisionClient:
    table = {MAIN_MODEL: answers.get("main"), CHEAP_MODEL: answers.get("cheap")}
    return FakeVisionClient(responders={"equation": lambda r: table[r.model]}, verifier=verifier)


def transcribe_with(client, element=None, settings=SETTINGS) -> Fix:
    return transcribe(element or equation_element(), PNG, client=client, settings=settings)


ALARM = {"match": 0.01, "cosmetic": 0.01, "meaning_changing": 0.98}

SCENARIOS = {
    # name: (element, answers)
    "main accepted": (equation_element(text="F = ma"), {"main": eq_out()}),
    "main needs review": (equation_element(), {"main": eq_out(confidence=0.7)}),
    "cheap accepted": (
        equation_element(display="inline", text="F = ma"), {"cheap": eq_out()}),
    "cheap escalated": (
        equation_element(display="inline", text="F = ma"),
        {"cheap": eq_out(confidence=0.6), "main": eq_out(r"F = m \cdot a")},
    ),
    "invalid latex": (equation_element(), {"main": eq_out(r"\input{x}")}),
    "refused": (equation_element(), {}),
}  # fmt: skip


@pytest.mark.parametrize("name", SCENARIOS)
def test_shadow_answers_change_nothing_but_the_record(name):
    element, answers = SCENARIOS[name]
    alarmed = FakeVerifier(equation=ALARM)  # says "meaning changing" with 0.98 every time
    with_shadow = transcribe_with(eq_client(alarmed, **answers), element)
    without = transcribe_with(eq_client(None, **answers), element)
    # Status, confidence, tier, checks, calls (so escalation) and the proposal: identical.
    assert with_shadow.model_dump(exclude={"verifications"}) == without.model_dump(
        exclude={"verifications"}
    )
    for record in with_shadow.verifications:
        assert record.mode == "shadow" and record.check == EQUATION_CHECK
        assert record.flagged is True  # would flag: recorded only


def test_final_proposal_is_checked_once_on_the_main_route():
    verifier = FakeVerifier()
    fix = transcribe_with(eq_client(verifier, main=eq_out()))
    (record,) = fix.verifications
    assert (record.stage, record.outcome) == ("final", "ok")
    assert record.probabilities["meaning_changing"] == 0.01 and record.flagged is False
    assert verifier.requests == [(EQUATION_CHECK, "F = m a")]


def test_an_escalated_equation_records_the_cheap_answer_and_the_final_one():
    verifier = FakeVerifier(responder=lambda check, latex: ALARM if "0" in latex else None)
    element = equation_element(display="inline", text="F = ma")
    client = eq_client(verifier, cheap=eq_out("F = m a_0", 0.6), main=eq_out("F = m a"))
    fix = transcribe_with(client, element)
    assert [c.model for c in fix.calls] == [CHEAP_MODEL, MAIN_MODEL]
    cheap, final = fix.verifications
    assert (cheap.stage, cheap.flagged) == ("cheap", True)
    assert (final.stage, final.flagged) == ("final", False)
    assert sorted(t for _, t in verifier.requests) == ["F = m a", "F = m a_0"]


def test_the_same_latex_after_escalation_is_asked_once():
    verifier = FakeVerifier()
    element = equation_element(display="inline", text="F = ma")
    fix = transcribe_with(eq_client(verifier, cheap=eq_out(confidence=0.6), main=eq_out()), element)
    cheap, final = fix.verifications
    assert cheap.probabilities == final.probabilities
    assert (cheap.input_tokens, cheap.cost_usd, cheap.latency_ms) == (0, 0.0, 0)
    assert final.input_tokens > 0
    assert len(verifier.requests) == 1


def test_no_proposal_no_final_check():
    verifier = FakeVerifier()
    fix = transcribe_with(eq_client(verifier, main=eq_out(r"\input{x}")))
    assert fix.status is Status.FAILED and fix.verifications == [] and verifier.requests == []


def test_shadow_fail_open_and_off_switch():
    verifier = FakeVerifier(outcomes={EQUATION_CHECK: "timeout"})
    fix = transcribe_with(eq_client(verifier, main=eq_out()))
    assert fix.status is Status.ACCEPTED
    assert (fix.verifications[0].outcome, fix.verifications[0].flagged) == ("timeout", None)
    off = make_settings(decisions_equation_shadow=False)
    quiet = FakeVerifier()
    assert transcribe_with(eq_client(quiet, main=eq_out()), settings=off).verifications == []
    assert quiet.requests == []


# ------------------------------------------------------------------- stored results


def test_results_stored_before_the_check_still_load():
    old = {
        "element_id": "p1-e1",
        "kind": "figure",
        "status": "accepted",
        "confidence": 0.9,
        "figure": {"figure_type": "chart", "alt": "A chart."},
        "calls": [{"step": "figure", "model": MAIN_MODEL, "prompt_version": "f", "cost_usd": 0.01}],
    }
    fix = Fix.model_validate(old)
    assert fix.verifications == [] and fix.cost_usd == pytest.approx(0.01)
    assert fix.verifier_cost_usd == 0


def test_the_committed_demo_report_still_loads():
    from pathlib import Path

    import a11y_stem

    report = Path(a11y_stem.__file__).parent / "web" / "static" / "demo" / "report.json"
    if not report.is_file():
        pytest.skip("no demo report in this checkout")
    stored = json.loads(report.read_text(encoding="utf-8"))["result"]
    result = DocumentResult.model_validate(stored)  # written before the check existed
    assert result.fixes and all(f.verifications == [] for f in result.fixes)


def test_a_result_with_an_unknown_future_check_still_loads():
    fix = Fix.model_validate(
        {"element_id": "p1-e1", "kind": "equation", "status": "accepted",
         "verifications": [{"check": "future_check", "stage": "later", "mode": "gate",
                            "outcome": "new_outcome", "probabilities": {"x": 0.5}}]}
    )  # fmt: skip
    assert fix.verifications[0].check == "future_check"
