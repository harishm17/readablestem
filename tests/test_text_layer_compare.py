# ruff: noqa: RUF001
"""Numbers as numbers and capitals as capitals against the PDF's text layer (T2-10, T2-9)."""

from __future__ import annotations

import pytest

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.types import Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import text_layer_compare as tl
from a11y_stem.pipeline.equation_step import text_layer_number_case_checks, transcribe
from a11y_stem.pipeline.validate import safe_latex_to_mathml
from test_steps_support import equation_element, make_settings, use_fallback_router

SETTINGS = make_settings()


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def ml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "inline")


def differ(text: str, latex: str) -> bool:
    diff = tl.numbers_differ(text, ml(latex))
    return diff is not None and diff.differ


# ---------------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("v = 36 m/s", r"v = 63\ \text{m/s}"),
        ("g = 9.81 m/s2", r"g = 98.1\ \text{m/s}^2"),
        ("8.5 cm", r"85\ \text{cm}"),
        ("N = 1023", r"N = 10^{32}"),
        ("t = 12 s", r"t = 21\ \text{s}"),
        ("x = 0.25", r"x = 0.52"),
    ],
)
def test_the_same_digits_in_another_order_are_a_different_number(text, latex):
    assert differ(text, latex)


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("v = 36 m/s", r"v = 36\ \text{m/s}"),
        ("g = 9.81 m/s2", r"g = 9.81\ \text{m/s}^2"),
        # the PDF glues a script to its base, in either order
        ("N = 1023", r"N = 10^{23}"),
        ("v20 + v0", r"v_0^2 + v_0"),
        ("1.1 × 102 m", r"1.1 \times 10^2\ \text{m}"),
        ("6.0 × 10−3", r"6.0 \times 10^{-3}"),
        # a numerator and a denominator side by side in a flattened fraction
        ("3 4 x", r"\frac{3}{4}x"),
        # a thin space in a long number
        ("1 000 m", r"1000\ \text{m}"),
    ],
)
def test_the_same_numbers_pass(text, latex):
    assert not differ(text, latex)


def test_no_number_in_the_text_layer_means_no_check():
    assert tl.numbers_differ("x = y", ml("x = y")) is None
    assert tl.numbers_differ(None, ml("x = 1")) is None


def test_the_leftovers_are_named():
    diff = tl.numbers_differ("v = 36 m/s", ml(r"v = 63\ \text{m/s}"))
    assert diff is not None and diff.pdf == ("36",) and diff.mathml == ("63",)


def test_a_point_before_the_digits_is_not_part_of_the_number():
    """ ".42" in a flattened fraction's text is a stray dot, not a number (the false flags
    on gold printed by Chromium)."""
    assert tl.pdf_numbers("1 −13 .42 + 4") == ("1", "13", "42", "4")
    assert not differ("1 −13 .42 + 4", r"1 - \frac{1}{3}\cdot\frac{4}{2} + 4")


def test_a_search_past_its_bound_says_it_cannot_tell(monkeypatch):
    monkeypatch.setattr(tl, "NUMBER_SEARCH_BUDGET", 3)
    assert tl.compare_numbers(("1111111112",), ("1",) * 9 + ("3",)) is None


# --------------------------------------------------------------------------- case


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("𝑤/𝑔", r"W/g"),
        ("𝑠 = 5", r"S = 5"),
        ("𝐴 + 𝑏", r"a + b"),
        ("Δ𝑥", r"\delta x"),
    ],
)
def test_a_changed_case_is_found(text, latex):
    assert tl.compare_case(text, ml(latex))


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("𝑤/𝑔", r"w/g"),
        ("𝑠 = 5", r"s = 5"),
        ("𝑥 ∈ ℝ", r"x \in \mathbb{R}"),
        ("∆𝑥", r"\Delta x"),  # TeX's capital Delta is U+2206 in the text layer
    ],
)
def test_the_same_case_passes(text, latex):
    assert not tl.compare_case(text, ml(latex))


def test_a_modifier_letter_is_an_accent_not_a_letter():
    assert not tl.compare_case("ATAˆ𝐱", ml(r"A^{T}A\hat{\mathbf{x}}"))


def test_the_case_rows_name_the_letter():
    (row,) = tl.compare_case("𝑤/𝑔", ml("W/g"))
    assert (row.letter, row.pdf_small, row.mathml_capital) == ("w", 1, 1)


# ------------------------------------------------------------ in the equation step


def out(latex: str, confidence: float = 0.95) -> dict:
    return {"is_equation": True, "latex": latex, "confidence": confidence}


def by_model(**answers) -> FakeVisionClient:
    table = {MAIN_MODEL: answers.get("main"), CHEAP_MODEL: answers.get("cheap")}
    return FakeVisionClient(responders={"equation": lambda req: table[req.model]})


def run(client, element):
    return transcribe(element, b"\x89PNG crop", client=client, settings=SETTINGS)


def test_a_swapped_number_needs_a_decision_and_lowers_confidence():
    fix = run(by_model(main=out(r"v = 63\ \text{m/s}")), equation_element(text="𝑣 = 36 m/s"))
    assert {c.code for c in fix.failed_checks} == {"number_mismatch"}
    assert fix.review == "required" and fix.status == Status.NEEDS_REVIEW
    assert fix.confidence == pytest.approx(0.95 * 0.6)
    check = next(c for c in fix.checks if c.name == "equation.text_layer_numbers")
    assert check.severity == "warning" and not check.passed


def test_a_changed_case_needs_a_decision():
    fix = run(by_model(main=out(r"W/g")), equation_element(text="𝑤/𝑔"))
    assert {c.code for c in fix.failed_checks} == {"case_mismatch"}
    assert fix.review == "required"
    assert fix.equation is not None  # the proposal is kept for the person's decision


def test_a_wrong_digit_is_still_the_symbol_check_and_the_new_checks_stay_out_of_it():
    fix = run(by_model(main=out(r"g = 9.30\ \text{m/s}^2")), equation_element(text="𝑔= 9.80m/s2"))
    assert {c.code for c in fix.failed_checks} == {"symbol_mismatch"}
    assert all(c.name != "equation.text_layer_numbers" for c in fix.checks)
    assert all(c.name != "equation.text_layer_case" for c in fix.checks)


def test_matching_numbers_and_case_pass_and_the_answer_is_accepted():
    fix = run(by_model(main=out(r"g = 9.80\ \text{m/s}^2")), equation_element(text="𝑔= 9.80m/s2"))
    assert fix.status == Status.ACCEPTED and fix.review == "none"
    names = {c.name for c in fix.checks if c.passed}
    assert {"equation.text_layer_numbers", "equation.text_layer_case"} <= names


def test_a_cheap_answer_with_a_swapped_number_escalates():
    client = by_model(cheap=out(r"x = 63", 0.99), main=out(r"x = 36", 0.95))
    fix = run(client, equation_element(display="inline", text="𝑥= 36"))
    assert [r.model for r in client.requests] == [CHEAP_MODEL, MAIN_MODEL]
    assert fix.equation is not None and fix.equation.latex == "x = 36"
    assert fix.status == Status.ACCEPTED


def test_the_checks_are_left_out_without_a_text_layer():
    assert text_layer_number_case_checks(None, ml("x = 1")) == []
