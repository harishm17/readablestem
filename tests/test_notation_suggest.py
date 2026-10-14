"""Notation found in a converted document (T3-12): what is suggested, what is guessed,
what is left out."""

from __future__ import annotations

import pytest

from a11y_stem.core.notation import canonical_symbol
from a11y_stem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from a11y_stem.pipeline import notation_suggest as ns

BOLD_D = "\U0001d41d"


def equation(eid: str, page: int, latex: str, *, status=Status.ACCEPTED, text=None):
    element = Element(
        id=eid,
        kind=Kind.EQUATION,
        page=page,
        bbox=(0, 0, 10, 10),
        order=int(eid.split("e")[1]),
        text=text,
        display="block",
    )
    fix = Fix(
        element_id=eid,
        kind=Kind.EQUATION,
        status=status,
        equation=EquationFix(latex=latex, mathml="<math/>", speech="x"),
    )
    return element, fix


def result_of(*pairs, paragraphs=()) -> DocumentResult:
    elements = [e for e, _ in pairs]
    for index, text in enumerate(paragraphs, start=50):
        elements.append(
            Element(
                id=f"p1-e{index}",
                kind=Kind.PARAGRAPH,
                page=0,
                bbox=(0, 0, 1, 1),
                order=index,
                text=text,
            )
        )
    return DocumentResult(
        pipeline_version="test",
        title="T",
        elements=elements,
        fixes=[f for _, f in pairs],
        stats=DocumentStats(
            pages=2, elements=len(elements), cost_usd=0.0, latency_ms=0, model_calls=0
        ),
    )


def keys(found: list[ns.Suggestion]) -> list[str]:
    return [s.canonical for s in found]


def test_styled_letters_accents_and_operators_in_equations_are_found():
    result = result_of(
        equation("p1-e1", 0, r"\mathbf{F} = m \mathbf{a}"),
        equation("p1-e2", 0, r"\vec{v} = \hat{x} + \boldsymbol{\omega}"),
        equation("p2-e3", 1, r"x \in \mathbb{R}, \quad \nabla \cdot \mathbf{F} = 0"),
    )
    found = {s.canonical: s for s in ns.suggest([result])}
    for text in (
        r"\mathbf{F}",
        r"\mathbf{a}",
        r"\vec{v}",
        r"\hat{x}",
        r"\boldsymbol{\omega}",
        r"\mathbb{R}",
        r"\nabla",
    ):
        assert canonical_symbol(text) in found, text
    bold_f = found[canonical_symbol(r"\mathbf{F}")]
    assert bold_f.count == 2 and bold_f.page == 1  # two equations; the first is on page 1
    assert found[canonical_symbol(r"\mathbb{R}")].page == 2
    assert found[canonical_symbol(r"\mathbf{a}")].count == 1


def test_the_most_used_come_first_then_by_page():
    result = result_of(
        equation("p1-e1", 0, r"\mathbf{b}"),
        equation("p1-e2", 0, r"\mathbf{a} \mathbf{b}"),
        equation("p2-e3", 1, r"\mathbf{b} \mathbf{c}"),
    )
    order = [ns.notation_memory.describe(k) for k in keys(ns.suggest([result]))]
    assert order == ["bold b", "bold a", "bold c"]


@pytest.mark.parametrize(
    ("symbol", "meaning"),
    [
        (r"\mathbf{d}", "vector"),
        (r"\boldsymbol{\omega}", "vector"),
        (r"\vec{v}", "vector"),
        (r"\hat{x}", "vector"),
        (r"\mathbb{R}", "set"),
        (r"\mathbb{E}", "operator"),
        (r"\mathbb{P}", "operator"),
        (r"\nabla", "operator"),
        (r"\hbar", "constant"),
        (r"\mathcal{L}", "other"),
        (r"\mathfrak{g}", "other"),
    ],
)
def test_the_meaning_is_a_guess_from_the_style(symbol, meaning):
    key = canonical_symbol(symbol)
    assert key is not None
    assert ns.guess_meaning(key) == meaning


def test_what_the_course_has_is_left_out():
    result = result_of(equation("p1-e1", 0, r"\mathbf{d} + \mathbf{v}"))
    have = canonical_symbol(r"\mathbf{d}")
    assert have is not None
    assert [s.canonical for s in ns.suggest([result], known={have})] == [
        canonical_symbol(r"\mathbf{v}")
    ]


def test_equations_not_used_in_the_file_give_no_suggestions():
    result = result_of(
        equation("p1-e1", 0, r"\mathbf{d}", status=Status.REJECTED),
        equation("p1-e2", 0, r"\mathbf{v}", status=Status.FAILED),
        equation("p1-e3", 0, r"\mathbf{w}", status=Status.NEEDS_REVIEW),
    )
    assert keys(ns.suggest([result])) == [canonical_symbol(r"\mathbf{w}")]


def test_plain_letters_italic_letters_and_other_commands_are_not_notation():
    result = result_of(
        equation("p1-e1", 0, r"x = \frac{a}{b} + \mathrm{d}t + \operatorname{sin} \alpha"),
        paragraphs=("The letter \U0001d44e (italic a) is a plain variable.",),
    )
    assert ns.suggest([result]) == []


def test_styled_letters_in_the_pdf_text_are_found_too():
    result = result_of(
        equation("p1-e1", 0, "d = 1", text=f"{BOLD_D} = 1"),
        paragraphs=(f"the displacement {BOLD_D} and the velocity \U0001d42f",),
    )
    found = {s.canonical: s for s in ns.suggest([result])}
    assert found[canonical_symbol("bold d")].count == 2  # the equation and the paragraph
    assert canonical_symbol("bold v") in found


def test_the_documents_of_a_course_add_up():
    one = result_of(equation("p1-e1", 0, r"\mathbf{d}"))
    two = result_of(equation("p2-e1", 1, r"\mathbf{d}"), equation("p2-e2", 1, r"\mathbf{v}"))
    found = {s.canonical: s for s in ns.suggest([one, two])}
    assert found[canonical_symbol("bold d")].count == 2
    assert found[canonical_symbol("bold v")].count == 1


def test_at_most_the_limit_are_offered():
    many = result_of(*[equation(f"p1-e{i}", 0, rf"\mathbf{{{chr(97 + i)}}}") for i in range(1, 20)])
    assert len(ns.suggest([many], limit=5)) == 5


def test_a_key_comes_with_its_words_and_latex():
    result = result_of(equation("p1-e1", 0, r"\mathbb{R}"))
    (only,) = ns.suggest([result])
    assert only.words == "double-struck R" and only.latex == r"\mathbb{R}"
