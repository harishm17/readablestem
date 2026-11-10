"""What the review screen says differs in an equation (web.compare), where it crops the
page, and the queue of decisions it walks through (web.queue)."""

from __future__ import annotations

import pytest

from readablestem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from readablestem.pipeline.validate import safe_latex_to_mathml
from readablestem.web.compare import (
    compare_equation,
    suggest_fix,
)

BOLD_D, BOLD_ZERO = "\U0001d41d", "\U0001d7ce"  # MATHEMATICAL BOLD SMALL D, DIGIT ZERO
ITALIC_V, ITALIC_D, ITALIC_T = "\U0001d463", "\U0001d451", "\U0001d461"  # math italic
BOLD_V, BOLD_X, BOLD_Y = "\U0001d42f", "\U0001d431", "\U0001d432"  # MATHEMATICAL BOLD SMALL
DOUBLE_R = "\u211d"  # DOUBLE-STRUCK CAPITAL R, the reals


def equation(text: str | None, latex: str, *, regular: str | None = None, display="inline"):
    element = Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(100, 100, 130, 112), order=1,
        text=text, font_size=10, display=display, regular_letters=regular,
    )  # fmt: skip
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(latex=latex, mathml=safe_latex_to_mathml(latex, display), speech=""),
    )
    return element, fix


# --------------------------------------------------------------------------- compare


def test_a_vector_read_as_a_scalar_is_a_meaning_level_style_row():
    c = compare_equation(*equation("𝛚", r"\omega"))
    assert c.summary == "The ω is bold in the PDF (a vector) but plain in the proposal."
    assert [(r.what, r.symbol, r.pdf, r.proposal, r.meaning) for r in c.rows] == [
        ("Style", "𝛚", "bold", "plain", True)
    ]
    assert c.pdf_marks == c.proposal_marks == {"ω"}


def test_matching_equations_and_missing_text_layers_have_no_rows():
    assert compare_equation(*equation("v = d/t", r"v = d / t")).rows == []
    assert not compare_equation(*equation(None, r"x")).has_text_layer
    assert not compare_equation(*equation("=", r"x")).has_text_layer
    element, fix = equation("x", "x")
    fix.equation = EquationFix(latex="x", mathml="<math><mi>x</mi>", speech="")  # broken
    assert compare_equation(element, fix).rows == []
    fix.equation = None
    assert compare_equation(element, fix).rows == []


@pytest.mark.parametrize(
    ("text", "latex", "regular", "label", "fixed"),
    [
        ("𝛚", r"\omega", None, "Use bold ω", r"\boldsymbol{\omega}"),
        (f"{BOLD_V} = 3", r"v = 3", None, "Use bold v", r"\mathbf{v} = 3"),
        (f"{BOLD_V} = {BOLD_V}0", r"v = v_0", None, "Use bold v", r"\mathbf{v} = \mathbf{v}_0"),
        (f"{ITALIC_V} = 3", r"\mathbf{v} = 3", None, "Use plain v", "v = 3"),
        (DOUBLE_R, "R", None, "Use double-struck R", r"\mathbb{R}"),
    ],
)
def test_a_meaning_level_style_difference_gets_a_one_click_fix(text, latex, regular, label, fixed):
    element, fix = equation(text, latex, regular=regular)
    c = compare_equation(element, fix)
    assert c.meaning
    suggestion = suggest_fix(element, fix, c)
    assert suggestion is not None
    assert (suggestion.label, suggestion.latex) == (label, fixed)
    # What it saves compares clean: no meaning-level difference left.
    after = Fix(
        element_id=fix.element_id,
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(latex=fixed, mathml=safe_latex_to_mathml(fixed, "inline"), speech=""),
    )
    assert not compare_equation(element, after).meaning


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("g = 9.80", r"g = 9.30"),  # a symbol, not a style: no rewrite can be sure
        (f"{BOLD_V} + v", r"v + v"),  # bold in one place, plain in another: which one?
        (f"{BOLD_D}0", r"\mathbf{d}_0"),  # cosmetic only
        ("𝛚", r"\text{ω}"),  # the letter is only inside words: nothing to wrap
        (f"{BOLD_X} = {BOLD_Y} \u2212 z", r"x = y + z"),  # more than one difference that matters
    ],
)
def test_no_one_click_fix_unless_it_is_one_style_and_it_checks_out(text, latex):
    element, fix = equation(text, latex)
    assert suggest_fix(element, fix, compare_equation(element, fix)) is None


# --------------------------------------------------------------------------- queue


def _result(fixes: list[Fix], pages: dict[str, int]) -> DocumentResult:
    elements = [
        Element(id=f.element_id, kind=f.kind, page=pages[f.element_id], bbox=(0, 0, 1, 1), order=i)
        for i, f in enumerate(fixes)
    ]
    stats = DocumentStats(pages=3, cost_usd=0, latency_ms=0, model_calls=0)
    return DocumentResult(pipeline_version="t", elements=elements, fixes=fixes, stats=stats)


def _heading(eid: str, status=Status.NEEDS_REVIEW, group: str | None = None, **kw) -> Fix:
    heading = HeadingFix(level=2, text="Teacher Support")
    return Fix(
        element_id=eid, kind=Kind.HEADING, status=status, confidence=0.6, heading=heading,
        group_key=group, **kw,
    )  # fmt: skip


def _equation(eid: str, status=Status.NEEDS_REVIEW, **kw) -> Fix:
    return Fix(element_id=eid, kind=Kind.EQUATION, status=status, **kw)


@pytest.fixture
def doc() -> DocumentResult:
    fixes = [
        _heading("p1-e0", group="hg-p1-e0", group_size=3),
        _equation("p2-e1"),
        _heading("p2-e2", group="hg-p1-e0", group_size=3),
        _equation("p1-e5", Status.ACCEPTED, review="optional"),
        _heading("p3-e0", group="hg-p1-e0", group_size=3),
        _equation("p3-e1", Status.FAILED, reason="model_timeout"),
        _equation("p3-e2", Status.ACCEPTED),
    ]
    pages = {"p1-e0": 0, "p2-e1": 1, "p2-e2": 1, "p1-e5": 0, "p3-e0": 2, "p3-e1": 2, "p3-e2": 2}
    return _result(fixes, pages)


def test_a_swapped_number_is_a_number_row_with_one_sentence():
    c = compare_equation(*equation("v = 36 m/s", r"v = 63\ \text{m/s}"))
    assert c.summary == "The PDF has the number 36 where the proposal has 63."
    assert [(r.what, r.symbol, r.pdf, r.proposal, r.meaning) for r in c.rows] == [
        ("Number", "36", "there", "not there", True),
        ("Number", "63", "not there", "there", True),
    ]
    assert c.said["number"] == [c.summary]
    assert c.pdf_marks == {"3", "6"} and c.proposal_marks == {"6", "3"}
