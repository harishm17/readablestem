"""A reviewer's edit gets the number and capital checks too (T2-10, T2-9): the same
codes as the equation step, said in plain words, and a meaning-level difference needs
"Use my edit anyway"."""

from __future__ import annotations

from readablestem.core.types import Element, EquationFix, Fix, Kind, Status
from readablestem.pipeline.validate import safe_latex_to_mathml
from readablestem.web import edit_check


def case(text: str, stored: str):
    element = Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 10, 10), order=1,
        text=text, font_size=10, display="inline",
    )  # fmt: skip
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(
            latex=stored, mathml=safe_latex_to_mathml(stored, "inline"), speech=""
        ),
    )
    return element, fix


def test_an_edit_with_swapped_digits_is_a_meaning_level_difference():
    element, fix = case("v = 36 m/s", r"v = 36\ \text{m/s}")
    found = edit_check.check_equation_edit(element, fix, r"v = 63\ \text{m/s}")
    assert {c.code for c in found.failed} == {"number_mismatch"}
    assert found.meaning == ["The PDF has the number 36 where your edit has 63."]


def test_an_edit_with_a_changed_capital_is_a_meaning_level_difference():
    element, fix = case("\U0001d464/\U0001d454", "w/g")
    found = edit_check.check_equation_edit(element, fix, "W/g")
    assert {c.code for c in found.failed} == {"case_mismatch"}
    assert found.meaning == ["The PDF has a small w; your edit has a capital W."]


def test_an_edit_that_keeps_the_numbers_and_case_has_no_difference():
    element, fix = case("v = 36 m/s", r"v = 63\ \text{m/s}")
    found = edit_check.check_equation_edit(element, fix, r"v = 36\ \text{m/s}")
    assert not found.failed and not found.meaning
    names = {c.name for c in found.checks}
    assert {"equation.text_layer_numbers", "equation.text_layer_case"} <= names
