"""Notation found in a converted document (T3-12): what is suggested, what is guessed,
what is left out."""

from __future__ import annotations

from readablestem.core.notation import canonical_symbol
from readablestem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.pipeline import notation_suggest as ns

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
