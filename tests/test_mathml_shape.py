# ruff: noqa: RUF001
"""The shape fixes for all maths (`pipeline.mathml_shape`) and the consumers of the shaped
MathML: the product's own speech, the scorers and the HTML writer."""

from __future__ import annotations

import unicodedata
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


# ------------------------------------------------------------------------------ the normal form


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _nodes(mathml: str, tag: str) -> list[ET.Element]:
    return [n for n in ET.fromstring(mathml).iter() if _tag(n) == tag]


def _spelled(mathml: str) -> bool:
    return any(len(n.text or "") == 1 for n in _nodes(mathml, "mi") if (n.text or "") in "sinco")


def _prime_is_operator(mathml: str) -> bool:
    primes = [n for n in ET.fromstring(mathml).iter() if (n.text or "") in ("′", "″")]
    return bool(primes) and all(_tag(n) == "mo" for n in primes)


def _styled_digits(mathml: str) -> bool:
    return any(
        n.get("mathvariant")
        or any(unicodedata.name(c, "").startswith("MATHEMATICAL") for c in n.text or "")
        for n in _nodes(mathml, "mn")
    )


def _variant_on_operator(mathml: str) -> bool:
    return any(n.get("mathvariant") for n in _nodes(mathml, "mo"))


def _marked(mathml: str) -> list[bool]:
    return [n.get("data-aligned-rows") == "true" for n in _nodes(mathml, "mtable")]


@pytest.mark.parametrize(
    ("rule", "latex", "clean"),
    [
        ("function name", r"cos \theta + \mathrm{sin} x", lambda m: not _spelled(m)),
        ("digits", r"\mathbf{3.3}\cdot\mathbf{10}^{18}", lambda m: not _styled_digits(m)),
        ("converter attribute", r"\mathbf{0.29\pm0.07}", lambda m: not _variant_on_operator(m)),
        ("prime", r"f'(x) + g^{\prime\prime}", _prime_is_operator),
        (
            "aligned rows",
            r"\begin{aligned} a &= b \\ c &= d \end{aligned}",
            lambda m: _marked(m) == [True],
        ),
        (
            "matrix is not rows",
            r"\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}",
            lambda m: _marked(m) == [False],
        ),
    ],
)
def test_the_normal_form_has_one_rule_per_fault(rule: str, latex: str, clean) -> None:  # type: ignore[no-untyped-def]
    assert clean(convert(latex)), rule


def test_a_function_name_is_one_token_and_is_spoken_as_the_function() -> None:
    assert "sine" in speech(r"\mathrm{sin} x") and "s i n" not in speech(r"\mathrm{sin} x")
    assert "cosine theta" in speech(r"cos \theta")
    assert "m a x" not in speech(r"{\rm max}_x f")
    assert "r" in [n.text for n in _nodes(convert(r"\mathrm{ran}"), "mi")]  # not a function: kept
    assert [n.text for n in _nodes(convert(r"Pr = 2"), "mi")] == ["P", "r"]  # the Prandtl number
    assert [n.text for n in _nodes(convert(r"ln = 6"), "mi")] == ["l", "n"]  # may be a product
    assert "natural log" in speech(r"\mathrm{ln} x")


def test_mathml_stored_before_the_rules_is_written_in_the_normal_form_on_the_page() -> None:
    from readablestem.pipeline.safe_html import sanitize_mathml

    old = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow><mi>s</mi><mo>⁢</mo><mi>i</mi>'
        '<mo>⁢</mo><mi>n</mi></mrow><mn mathvariant="bold">3</mn><mn>𝟏𝟎</mn>'
        '<mo mathvariant="bold">⋅</mo><msup><mi>f</mi><mi>′</mi></msup></math>'
    )
    page = sanitize_mathml(old, display="inline")
    assert page is not None
    assert "<mi>sin</mi>" in page and "𝟏" not in page and 'mathvariant="bold"' not in page
    assert "<mo>′</mo>" in page
    assert sanitize_mathml(page, display="inline") == page  # writing it again changes nothing


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
