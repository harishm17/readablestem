"""Fixes from the chemistry accuracy investigation (docs/chemistry.md, "Accuracy: what the
low primary score was"): each is a form the recorded development run got wrong."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from readablestem.pipeline import chem
from readablestem.pipeline import equation_step as es
from readablestem.pipeline.validate import check_latex, has_errors


def _element():
    from readablestem.core.types import Element, Kind

    return Element(
        id="p1-e0",
        kind=Kind.EQUATION,
        page=1,
        bbox=(0, 0, 10, 10),
        order=0,
        text="x",
        display="block",
    )


def _tokens(mathml: str) -> list[str]:
    root = ET.fromstring(mathml)
    return [
        (node.text or "")
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] in {"mi", "mo", "mn", "mtext"}
    ]


# ------------------------------------------------------------- two-part arrow labels


def test_a_label_above_and_below_the_arrow_is_written_out():
    r"""The model's answer for OpenStax 18.2 (electrolysis of NaCl): a text over the arrow and a
    temperature under it. mhchem's `->[above][below]` is `\xrightarrow[below]{above}`."""
    source = r"\ce{2 NaCl(l) ->[\text{electrolysis}][600^\circ C] 2 Na(l) + Cl2(g)}"
    latex = es.expand_ce(source)
    assert "\\ce" not in latex
    assert r"\xrightarrow[600^\circ \text{C}]{\text{electrolysis}}" in latex
    mathml, checks = es._convert(es.clean_latex(latex), "block")
    assert mathml is not None and not has_errors(checks)
    assert "munderover" in mathml


def test_a_plain_two_part_label():
    latex = chem.ce_to_latex("CaCO3 ->[above][below] CaO")
    assert r"\xrightarrow[\text{below}]{\text{above}}" in latex


def test_an_equilibrium_arrow_with_two_labels():
    latex = chem.ce_to_latex("N2 <=>[above][below] CaO")
    assert r"\overset{\text{above}}{\underset{\text{below}}{\rightleftharpoons}}" in latex
    assert not has_errors(check_latex(latex))


@pytest.mark.parametrize(
    "bad",
    [
        "CaCO3 ->[a][b][c] CaO",  # three parts
        r"CaCO3 ->[\frac{1}{2}][b] CaO",  # a command that is not a label
        r"CaCO3 ->[\text{a}{b}][c] CaO",
        "CaCO3 ->[][b] CaO",
    ],
)
def test_a_label_outside_the_subset_is_still_refused(bad):
    with pytest.raises(chem.CeError):
        chem.ce_to_latex(bad)


def test_the_one_part_label_is_unchanged():
    assert (
        chem.ce_to_latex("CaCO3 ->[\\Delta] CaO")
        == r"\text{CaCO}_3 \xrightarrow{\Delta} \text{CaO}"
    )
    assert (
        chem.ce_to_latex("CaCO3 ->[heat] CaO")
        == r"\text{CaCO}_3 \xrightarrow{\text{heat}} \text{CaO}"
    )


# ----------------------------------------------------------- cell notation (galvanic cells)

CELL = (
    r"\mathrm{Pt}(s)\ \middle|\ \mathrm{H_2}(g,\ 1\ \text{atm})\ \middle|\ "
    r"\mathrm{H^+}(aq,\ 1\ M)\ \|\ \mathrm{Cu^{2+}}(aq,\ 1\ M)\ \middle|\ \mathrm{Cu}(s)"
)


def test_cell_notation_with_a_bare_middle_bar_converts():
    r"""`\middle|` is only valid after `\left`; the model uses it for the phase boundary of a
    cell (OpenStax 16.3) and the converter refuses the whole equation. A bare `\middle` is a
    bar."""
    latex = chem.tidy_cell_notation(CELL)
    assert "\\middle" not in latex
    mathml, checks = es._convert(es.clean_latex(latex), "block")
    assert mathml is not None and not has_errors(checks)
    tokens = _tokens(mathml)
    assert tokens.count("|") == 3 and "‖" in tokens


def test_a_middle_bar_inside_left_and_right_is_left_alone():
    latex = r"\left( \frac{a}{b} \middle| c \right)"
    assert chem.tidy_cell_notation(latex) == latex


def test_the_equation_step_applies_it():
    candidate = es._evaluate(
        es.EquationOutput(is_equation=True, latex=CELL, confidence=0.5),
        [],
        _element(),
        "block",
    )
    assert candidate.mathml is not None
