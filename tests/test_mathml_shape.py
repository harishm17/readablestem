"""The shape fixes for all maths (`pipeline.mathml_shape`) and the consumers of the shaped
MathML: the product's own speech, the scorers and the HTML writer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from readablestem.eval import equations_chem as chem
from readablestem.eval import equations_symbols as sym
from readablestem.pipeline.chem import ce_to_latex
from readablestem.pipeline.safe_html import sanitize_mathml
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


@pytest.mark.parametrize("latex", [r"f \circ g", r"(f \circ g)(x)", r"a \circ b \circ c"])
def test_a_ring_between_two_things_stays_composition(latex: str) -> None:
    out = tokens(convert(latex))
    assert RING in out and DEGREE not in out


def test_the_product_speech_says_degrees_and_composed_with() -> None:
    assert speech(r"\theta = 30^\circ").endswith("30 degrees")
    assert "composed with" in speech(r"f \circ g")


# ------------------------------------------------------------------- overline and underline


def test_overline_and_underline_use_the_accents_mathml_names() -> None:
    assert tokens(convert(r"\overline{AB}")) == ["A", "B", "¯"]
    assert tokens(convert(r"\underline{AB}")) == ["A", "B", "_"]
    assert "bar" in speech(r"\overline{AB}") and "underlined" in speech(r"\underline{AB}")


def test_a_bar_that_is_not_the_horizontal_bar_is_left_alone() -> None:
    assert tokens(convert(r"\bar{x}")) == ["x", "¯"]
    assert tokens(convert(r"\widehat{ABC}")) == ["A", "B", "C", "^"]


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


@pytest.mark.parametrize(
    ("source", "words"),
    [
        ("H2O", "H 2 O"),
        ("2 H2 + O2 -> 2 H2O", "2 H 2 plus O 2 reacts to form 2 H 2 O"),
        ("SO4^2-", "S O 4 2 minus"),
        (
            "Na+(aq) + Cl-(aq) <=> NaCl(s)",
            "N a plus aqueous, plus C l minus aqueous, is in equilibrium with N a C l solid",
        ),
        ("^{14}_{6}C", "carbon 14, atomic number 6"),
        (
            "[Cu(NH3)4]^2+",
            "open bracket C u open parenthesis N H 3 close parenthesis 4 close bracket 2 plus",
        ),
        ("Fe^3+ + e- -> Fe^2+", "F e 3 plus, plus e minus reacts to form F e 2 plus"),
        (
            "CaCO3(s) ->[\\Delta] CaO(s) + CO2(g)",
            "C a C O 3 solid, reacts, with heat, to form C a O solid, plus C O 2 gas",
        ),
    ],
)
def test_the_product_speech_reads_the_shaped_chemistry_as_before(source: str, words: str) -> None:
    assert mathml_to_speech(convert(ce_to_latex(source))) == words


def test_the_speech_reads_plain_mi_symbols_only_where_intent_says_they_are_symbols() -> None:
    plain = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"{}><mrow><msub><mi>H</mi><mn>2</mn></msub>'
        "<mi>O</mi></mrow></math>"
    )
    assert mathml_to_speech(plain.format(' intent=":chemical-formula"')) == "H 2 O"
    assert mathml_to_speech(plain.format("")) != "H 2 O"  # an italic H is a variable


def test_the_speech_of_the_old_shape_is_unchanged() -> None:
    old = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow><msub><mtext>H</mtext>'
        "<mn>2</mn></msub><mtext>O</mtext><mtext>(l)</mtext></mrow></math>"
    )
    assert mathml_to_speech(old) == "H 2 O liquid"


# -------------------------------------------------------------------- the HTML writer and scorers


def test_the_html_writer_keeps_the_intent_of_a_formula_on_the_math_element() -> None:
    html = sanitize_mathml(convert(ce_to_latex("2 H2 + O2 -> 2 H2O")), display="inline")
    assert html is not None and html.startswith(
        '<math display="inline" intent=":chemical-equation">'
    )
    plain = sanitize_mathml(convert(r"x^2"), display="inline")
    assert plain is not None and "intent" not in plain


def test_the_scorers_read_mmultiscripts_as_the_answer_keys_prefix_scripts() -> None:
    key = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><msubsup><mrow></mrow><mn>6</mn>'
        "<mn>14</mn></msubsup><mtext>C</mtext></math>"
    )
    ours = convert(ce_to_latex("^{14}_{6}C"))
    assert "mmultiscripts" in ours
    # The symbols scorer keeps the style of a letter (ours is an upright `mi`, the key an
    # `mtext`); the order and the scripts are the same.
    stream = [a.removeprefix("normal:") for a in sym.symbols_of(ours)]
    assert stream == sym.symbols_of(key) == ["_", "6", "^", "14", "C"]
    assert chem.chem_of(ours) == chem.chem_of(key)
    assert chem.score_chem(ours, key).exact


def test_the_chemistry_scorer_calls_the_new_shape_the_same_chemistry_as_the_old() -> None:
    old = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><msub><mtext>H</mtext><mn>2</mn></msub>'
        "<mtext>O</mtext><mtext>(l)</mtext></math>"
    )
    new = convert(ce_to_latex("H2O(l)"))
    assert chem.score_chem(new, old).exact
    assert not chem.score_chem(convert(ce_to_latex("H2O(g)")), old).exact


def test_the_primary_scorer_reads_mmultiscripts_as_the_answer_keys_shape() -> None:
    from readablestem.eval.equations import score_mathml

    key = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><msubsup><mrow></mrow><mn>6</mn>'
        "<mn>14</mn></msubsup><mtext>C</mtext></math>"
    )
    assert score_mathml(convert(ce_to_latex("^{14}_{6}C")), key).exact_lenient


def test_the_symbols_scorer_calls_an_upright_symbol_and_an_mtext_one_alike_only_for_symbols() -> (
    None
):
    ns = 'xmlns="http://www.w3.org/1998/Math/MathML"'
    upright = f"<math {ns}><mi mathvariant='normal'>H</mi><mi mathvariant='normal'>e</mi></math>"
    text = f"<math {ns}><mtext>H</mtext><mtext>e</mtext></math>"
    assert sym.score_symbols(upright, text).exact
    d_up = f"<math {ns}><mi mathvariant='normal'>d</mi></math>"
    d_text = f"<math {ns}><mi>d</mi></math>"
    assert not sym.score_symbols(d_up, d_text).exact  # an upright d is still not a plain d
