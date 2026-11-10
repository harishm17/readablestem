"""The shape fixes for all maths (`pipeline.mathml_shape`) and the consumers of the shaped
MathML: the product's own speech, the scorers and the HTML writer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from readablestem.pipeline.chem import ce_to_latex
from readablestem.pipeline.speech import mathml_to_speech
from readablestem.pipeline.validate import check_mathml, safe_latex_to_mathml

RING = "∘"
DEGREE = "°"


def convert(latex: str, display: str = "inline") -> str:
    return safe_latex_to_mathml(latex, display)  # type: ignore[arg-type]


def tokens(mathml: str) -> list[str]:
    return [n.text or "" for n in ET.fromstring(mathml).iter() if len(n) == 0]


def speech(latex: str) -> str:
    return mathml_to_speech(convert(latex))


# --------------------------------------------------------------------------- the degree sign


@pytest.mark.parametrize(
    "latex", [r"30^\circ", r"\theta = 90^{\circ}", r"E^\circ_\text{cell}", r"x^\circ + y^{\circ}"]
)
def test_a_ring_raised_on_a_symbol_is_the_degree_sign(latex: str) -> None:
    out = tokens(convert(latex))
    assert DEGREE in out and RING not in out


def test_the_product_speech_says_degrees_and_composed_with() -> None:
    assert speech(r"\theta = 30^\circ").endswith("30 degrees")
    assert "composed with" in speech(r"f \circ g")


# ------------------------------------------------------------------- overline and underline


# ----------------------------------------------------------------------------- labelled arrows


def test_a_labelled_arrow_is_an_arrow_with_its_label_and_nothing_around_them() -> None:
    root = ET.fromstring(convert(r"x \xrightarrow{f} y"))
    (over,) = [n for n in root.iter() if n.tag.endswith("mover")]
    assert [c.tag.rsplit("}", 1)[-1] for c in over] == ["mo", "mi"]
    assert not [n for n in root.iter() if n.tag.endswith(("mpadded", "mstyle", "mspace"))]
    assert not [c for c in check_mathml(convert(r"x \xrightarrow{f} y")) if not c.passed]


def test_a_labelled_arrow_still_reads_with_its_label_in_the_product_speech() -> None:
    assert "with heat" in mathml_to_speech(convert(ce_to_latex("CaCO3(s) ->[\\Delta] CaO(s)")))
    assert "f" in speech(r"x \xrightarrow{f} y")


# --------------------------------------------------------------- the product's own speech


def test_the_speech_of_the_old_shape_is_unchanged() -> None:
    old = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow><msub><mtext>H</mtext>'
        "<mn>2</mn></msub><mtext>O</mtext><mtext>(l)</mtext></mrow></math>"
    )
    assert mathml_to_speech(old) == "H 2 O liquid"


# -------------------------------------------------------------------- the HTML writer and scorers


def test_the_primary_scorer_reads_mmultiscripts_as_the_answer_keys_shape() -> None:
    from readablestem.eval.equations import score_mathml

    key = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><msubsup><mrow></mrow><mn>6</mn>'
        "<mn>14</mn></msubsup><mtext>C</mtext></math>"
    )
    assert score_mathml(convert(ce_to_latex("^{14}_{6}C")), key).exact_lenient


@pytest.mark.parametrize(
    ("source", "words"),
    [
        ("Zn^2+(aq, 0.10 M)", "Z n 2 plus aqueous, 0.10 molar"),
        ("Zn(aq, 0.10 M)", "Z n aqueous, 0.10 molar"),
        ("^{60}_{28}Ni^*", "nickel 60, atomic number 28, excited state"),
        (
            "CaCO3(s) -> CaO(s) + CO2 ^",
            "C a C O 3 solid, reacts to form C a O solid, plus C O 2 gas given off",
        ),
        (
            "Ag+(aq) + Cl-(aq) -> AgCl v",
            "A g plus aqueous, plus C l minus aqueous, reacts to form A g C l precipitate",
        ),
    ],
)
def test_the_speech_rules_for_conditions_marks_and_excited_nuclides_read_the_new_shape(
    source: str, words: str
) -> None:
    assert mathml_to_speech(convert(ce_to_latex(source))) == words
