r"""The MathML shape of chemistry (`pipeline.chem_mathml`) and the one `intent` it writes.

The shape is what MathCAT's chemistry rules recognise (docs/chemistry.md, "MathML shape"); the
tests with the real engines are in `tests/test_screenreader_shape.py`. Here: the tree, what is
left alone, and the allowlists that accept `intent` for exactly two values.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from readablestem.core import mathml_intent
from readablestem.pipeline import safe_html, validate
from readablestem.pipeline.chem import ce_to_latex
from readablestem.pipeline.validate import check_mathml, safe_latex_to_mathml
from readablestem.web import mathml as web_mathml

NS = "{http://www.w3.org/1998/Math/MathML}"


def convert(latex: str) -> str:
    return safe_latex_to_mathml(latex, "inline")


def tree(latex: str) -> ET.Element:
    return ET.fromstring(convert(latex))


def ce(source: str) -> ET.Element:
    return tree(ce_to_latex(source))


def local(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def words(node: ET.Element) -> list[str]:
    """The text of each token, in order."""
    return ["".join(n.itertext()) for n in node.iter() if len(n) == 0 and local(n) != "mprescripts"]


def intent_of(root: ET.Element) -> str | None:
    found = {n.get("intent") for n in root.iter() if n.get("intent")}
    assert len(found) <= 1
    return found.pop() if found else None


def test_the_intent_values_are_exactly_the_two_chemistry_ones() -> None:
    assert {":chemical-formula", ":chemical-equation"} == mathml_intent.ALLOWED_INTENTS
    assert mathml_intent.intent_allowed(":chemical-formula")
    for refused in (
        ":chemical-formula ",
        ":Chemical-Formula",
        "chemical-formula",
        ":chemical-formula(x)",
        ":literal",
        ":function($a)",
        "",
        "javascript:alert(1)",
    ):
        assert not mathml_intent.intent_allowed(refused)


# ------------------------------------------------------------------------------- the shape


def test_water_is_one_mi_per_symbol_with_the_count_on_its_symbol() -> None:
    root = ce("H2O")
    (msub,) = [n for n in root.iter() if local(n) == "msub"]
    assert local(msub[0]) == "mi" and msub[0].get("mathvariant") is None
    assert words(msub) == ["H", "2"]
    tokens = [(local(n), "".join(n.itertext())) for n in root.iter() if len(n) == 0]
    assert tokens == [("mi", "H"), ("mn", "2"), ("mi", "O")]
    assert not [n for n in root.iter() if local(n) == "mtext"]


def test_a_multi_letter_word_is_split_into_its_element_symbols() -> None:
    root = ce("NaCl")
    assert words(root) == ["Na", "Cl"]
    symbols = [n for n in root.iter() if local(n) == "mi"]
    assert len(symbols) == 2 and all(n.get("mathvariant") is None for n in symbols)


def test_an_ion_has_its_charge_on_the_last_symbol() -> None:
    root = ce("SO4^2-")
    (msubsup,) = [n for n in root.iter() if local(n) == "msubsup"]
    assert words(msubsup)[:2] == ["O", "4"]
    assert "".join(msubsup[2].itertext()) == "2−"
    assert words(root)[0] == "S"  # the sulfur stays outside the scripts
    (msup,) = [n for n in ce("Na+").iter() if local(n) == "msup"]
    assert words(msup) == ["Na", "+"]


@pytest.mark.parametrize("state", ["aq", "s", "l", "g"])
def test_a_state_is_a_paren_a_symbol_and_a_paren(state: str) -> None:
    root = ce(f"NaCl({state})")
    kids = list(root.find(f"{NS}mrow") if root.find(f"{NS}mrow") is not None else root)
    texts = ["".join(k.itertext()) for k in kids]
    assert texts[-3:] == ["(", state, ")"]
    assert [local(k) for k in kids[-3:]] == ["mo", "mi", "mo"]


def test_a_state_is_only_a_state_after_a_species() -> None:
    root = tree(r"f \text{(s)}")
    assert any(local(n) == "mtext" and n.text == "(s)" for n in root.iter())


def test_a_nuclide_is_mmultiscripts_with_the_numbers_in_front() -> None:
    root = ce("^{14}_{6}C")
    (multi,) = [n for n in root.iter() if local(n) == "mmultiscripts"]
    assert [local(k) for k in multi] == ["mi", "mprescripts", "mn", "mn"]
    assert [k.text for k in multi if local(k) in {"mi", "mn"}] == ["C", "6", "14"]
    assert not [n for n in root.iter() if local(n) == "msubsup"]


def test_a_nuclide_with_a_charge_keeps_it_after_the_symbol() -> None:
    root = tree(r"{}^{23}_{11}\text{Na}^+")
    (multi,) = [n for n in root.iter() if local(n) == "mmultiscripts"]
    assert [local(k) for k in multi] == ["mi", "none", "mo", "mprescripts", "mn", "mn"]
    assert multi[2].text == "+"


def test_an_arrow_is_an_operator() -> None:
    root = ce("H2 + O2 -> H2O")
    arrows = [n for n in root.iter() if len(n) == 0 and (n.text or "") == "⟶"]
    assert [local(n) for n in arrows] == ["mo"]


def test_flat_upright_letters_of_a_mathrm_formula_are_paired() -> None:
    root = tree(r"\mathrm{NaCl} \to \mathrm{Na^+} + \mathrm{Cl^-}")
    assert [w for w in words(root) if w.isalpha()] == ["Na", "Cl", "Na", "Cl"]


def test_a_charge_on_a_group_stays_on_the_group() -> None:
    root = ce("[Cu(NH3)4]^2+")
    (outer,) = [n for n in root.iter() if local(n) == "msup"]
    assert local(outer[0]) == "mrow" and "".join(outer[1].itertext()) == "2+"
    assert words(outer[0])[1] == "Cu"


def test_an_electron_is_a_plain_mi_in_a_reaction() -> None:
    root = tree(r"\text{Fe}^{3+} + 3\text{e}^- \to \text{Fe}")
    assert [n.text for n in root.iter() if local(n) == "mi" and n.text == "e"] == ["e"]
    (e,) = [n for n in root.iter() if local(n) == "mi" and n.text == "e"]
    assert e.get("mathvariant") is None  # all chemistry: `intent` is on the root


def test_a_symbol_has_no_explicit_style_when_the_equation_is_all_chemistry() -> None:
    """`mathvariant="normal"` is read aloud by Speech Rule Engine ("normal H"); `intent` already
    says these are symbols, so the attribute is not written. Beside other maths it is kept, to
    tell a symbol from an italic variable."""
    whole = tree(ce_to_latex("2 H2 + O2 -> 2 H2O"))
    assert not [n for n in whole.iter() if n.get("mathvariant")]
    mixed = tree(r"\text{rate} = k[\text{NO}_2]^m")
    symbols = [n for n in mixed.iter() if local(n) == "mi" and n.text in {"N", "O"}]
    assert len(symbols) == 2 and all(n.get("mathvariant") == "normal" for n in symbols)
    assert not [n for n in mixed.iter() if n.get("data-chem-symbol")]  # the working mark is gone


def test_a_neutron_a_positron_and_the_greek_particles_keep_the_converters_shape() -> None:
    """MathCAT has no nuclide rule for them (it reads `mmultiscripts` of them no better)."""
    for latex in (
        r"{}^1_0\text{n}",
        r"{}^1_1\text{p}",
        r"{}^0_{+1}\text{e}",
        r"{}^4_2\alpha",
        r"{}^0_{-1}\beta",
        r"{}^0_0\gamma",
    ):
        root = tree(latex)
        assert not [n for n in root.iter() if local(n) == "mmultiscripts"], latex
        assert [local(n) for n in root.iter() if local(n) == "msubsup"], latex
    electron = tree(r"{}^0_{-1}\text{e}")  # the one particle MathCAT reads as a nuclide
    assert [n for n in electron.iter() if local(n) == "mmultiscripts"]
    helium = tree(r"{}^4_2\text{He} + {}^1_0\text{n}")
    assert len([n for n in helium.iter() if local(n) == "mmultiscripts"]) == 1


def test_the_shaped_output_passes_the_mathml_check_and_is_stable() -> None:
    for source in ("2 H2 + O2 -> 2 H2O", "Na+(aq) + Cl-(aq) <=> NaCl(s)", "^{14}_{6}C", "SO4^2-"):
        mathml = convert(ce_to_latex(source))
        assert not [c for c in check_mathml(mathml) if not c.passed], source
        again = ET.fromstring(mathml)
        from readablestem.pipeline.chem_mathml import shape_chemistry

        shape_chemistry(again)  # a second pass changes nothing
        assert [("".join(n.itertext()), n.get("intent")) for n in again.iter()] == [
            ("".join(n.itertext()), n.get("intent")) for n in ET.fromstring(mathml).iter()
        ], source


# ---------------------------------------------------------------------------- what is left alone


@pytest.mark.parametrize(
    "latex",
    [
        r"x^2 + y^2 = 1",
        r"\text{No}",  # nobelium or a word
        r"\text{Pa}",  # pascal
        r"5\ \text{N}",  # newton
        r"\mathrm{d}x",
        r"\mathrm{Re}(z)",
        r"\text{As in the text, } x",
        r"25\,^{\circ}\text{C}",
        r"\text{H}",  # a lone symbol is not evidence
        r"\mathrm{Cl}",
        r"f \text{(s)}",
        r"\text{rate} = k",
    ],
)
def test_maths_without_chemistry_in_it_is_not_reshaped(latex: str) -> None:
    root = tree(latex)
    assert intent_of(root) is None
    assert not [n for n in root.iter() if local(n) == "mmultiscripts"]
    assert not any(
        local(n) == "mi" and n.get("mathvariant") == "normal" and len(n.text or "") == 2
        for n in root.iter()
    )


def test_a_sentence_word_next_to_a_formula_stays_text() -> None:
    root = tree(r"\text{rate} = k[\text{NO}_2]^m")
    assert any(local(n) == "mtext" and n.text == "rate" for n in root.iter())
    assert words(root).count("O") == 1


# ----------------------------------------------------------------------------------- intent


@pytest.mark.parametrize(
    ("source", "intent"),
    [
        ("H2O", ":chemical-formula"),
        ("SO4^2-", ":chemical-formula"),
        ("[Cu(NH3)4]^2+", ":chemical-formula"),
        ("CuSO4.5H2O", ":chemical-formula"),
        ("^{14}_{6}C", ":chemical-formula"),
        ("2 H2 + O2 -> 2 H2O", ":chemical-equation"),
        ("Na+(aq) + Cl-(aq) <=> NaCl(s)", ":chemical-equation"),
        ("CaCO3(s) ->[\\Delta] CaO(s) + CO2(g)", ":chemical-equation"),
        ("Fe^3+ + e- -> Fe^2+", ":chemical-equation"),
    ],
)
def test_a_formula_or_a_reaction_on_its_own_carries_the_intent(source: str, intent: str) -> None:
    root = ce(source)
    assert intent_of(root) == intent
    assert root.get("intent") == intent  # on the `math` element, where MathCAT reads it


@pytest.mark.parametrize(
    "latex",
    [
        r"\text{rate} = k[\text{NO}_2]^m",
        r"\text{H}_2\text{O}\text{(l)} \rightleftharpoons \text{H}^+ \qquad K_\text{w} = 1.0",
        r"\Delta G^\circ = -RT\ln K",
        r"{}^{235}_{92}\text{U} + \bar{\nu}_e",
    ],
)
def test_chemistry_beside_other_maths_gets_no_intent(latex: str) -> None:
    assert intent_of(tree(latex)) is None


def test_a_state_in_a_mixed_expression_is_still_shaped() -> None:
    root = tree(r"\text{H}_2\text{O}\text{(l)} \rightleftharpoons \text{H}^+ \qquad K = 1")
    texts = ["".join(n.itertext()) for n in root.iter() if len(n) == 0]
    assert texts[:3] == ["H", "2", "O"] and "(" in texts and "l" in texts


# --------------------------------------------------------------------- the allowlists (security)

MATH = '<math xmlns="http://www.w3.org/1998/Math/MathML">{}</math>'


def with_intent(value: str) -> str:
    return f'<math xmlns="http://www.w3.org/1998/Math/MathML" intent="{value}"><mi>x</mi></math>'


def codes(mathml: str) -> list[str]:
    return [c.code for c in check_mathml(mathml) if not c.passed and c.code]


@pytest.mark.parametrize("value", sorted(mathml_intent.ALLOWED_INTENTS))
def test_the_check_accepts_the_two_chemistry_values(value: str) -> None:
    assert codes(with_intent(value)) == []
    root = validate._parse_xml(with_intent(value))
    assert validate.serialize_mathml(root).count(f'intent="{value}"') == 1


@pytest.mark.parametrize(
    "value",
    [
        ":literal",
        ":function",
        "x",
        "",
        ":chemical-formula ",
        " :chemical-formula",
        ":CHEMICAL-FORMULA",
        ":chemical-formulae",
        ":chemical-formula(x)",
        "javascript:alert(1)",
        ":chemical-equation:chemical-formula",
    ],
)
def test_any_other_intent_is_refused_everywhere(value: str) -> None:
    mathml = with_intent(value)
    # 1. the MathML check and the serialiser (what safe_latex_to_mathml ends in)
    assert "unsafe_attribute" in codes(mathml)
    with pytest.raises(validate.UnsafeMathMLError):
        validate.serialize_mathml(validate._parse_xml(mathml))
    # 2. the HTML writer drops the attribute
    out = safe_html.sanitize_mathml(mathml, display="inline")
    assert out is not None and "intent" not in out
    # 3. the review screen's sanitiser drops it too
    shown = web_mathml.sanitize_mathml(mathml)
    assert "intent" not in str(shown)


def test_intent_is_only_accepted_on_the_math_element() -> None:
    nested = MATH.format('<mrow intent=":chemical-formula"><mi>x</mi></mrow>')
    assert "unsafe_attribute" in codes(nested)
    out = safe_html.sanitize_mathml(nested, display="inline")
    assert out is not None and "intent" not in out
    assert "intent" not in str(web_mathml.sanitize_mathml(nested))


def test_other_speech_and_script_attributes_are_still_refused() -> None:
    for attr in ("arg", "href", "onclick", "style"):
        mathml = MATH.format(f'<mrow {attr}=":chemical-formula"><mi>x</mi></mrow>')
        assert codes(mathml), attr


def test_the_two_values_survive_the_html_writer_and_the_review_sanitiser() -> None:
    mathml = convert(ce_to_latex("H2O"))
    html = safe_html.sanitize_mathml(mathml, display="inline")
    assert html is not None and 'intent=":chemical-formula"' in html
    shown = str(web_mathml.sanitize_mathml(mathml))
    assert 'intent=":chemical-formula"' in shown


def test_the_converter_never_writes_an_intent_a_check_would_refuse() -> None:
    for latex in (ce_to_latex("H2O"), r"\text{rate} = k", r"\mathrm{Na^+}", r"\frac{1}{2}"):
        assert codes(convert(latex)) == [], latex


def test_a_hostile_nesting_is_refused_by_the_converter_not_by_the_interpreter() -> None:
    """The shaping walks the tree; deep input must end in the converter's own refusal."""
    for depth in (300, 900):
        latex = "{" * depth + ce_to_latex("H2O") + "}" * depth
        with pytest.raises(validate.LatexConversionError):
            safe_latex_to_mathml(latex, "inline")
    assert not [
        c for c in check_mathml(convert("{" * 30 + ce_to_latex("H2O") + "}" * 30)) if not c.passed
    ]


# ------------------------------------------------- the `\ce` forms of the subset, in this shape


def test_a_state_with_a_condition_stays_one_mtext_and_is_evidence() -> None:
    """MathCAT has no rule for `(aq, 0.10 M)`; our own reading takes it as one token."""
    root = ce("Zn(aq, 0.10 M)")
    assert [(local(n), "".join(n.itertext())) for n in root.iter() if len(n) == 0] == [
        ("mi", "Zn"),
        ("mtext", "(aq," + chr(0xA0) + "0.10" + chr(0xA0) + "M)"),
    ]
    assert root.get("intent") == ":chemical-formula"


def test_an_excited_nuclide_keeps_the_converters_shape() -> None:
    """MathCAT reads a star on a nuclide as "super times" and no longer as a nuclide at all."""
    root = ce("^{60}_{28}Ni^*")
    assert not [n for n in root.iter() if local(n) == "mmultiscripts"]
    assert [local(n) for n in root.iter() if local(n) == "msubsup"]
    assert any(local(n) == "mtext" and n.text == "Ni" for n in root.iter())


def test_the_gas_and_precipitate_marks_are_operators_after_the_species() -> None:
    gas = ce("CO2 ^")
    assert [n.text for n in gas.iter() if local(n) == "mo"] == ["↑"]
    precipitate = ce("Ag+(aq) + Cl-(aq) -> AgCl v")
    assert "↓" in [n.text for n in precipitate.iter() if local(n) == "mo"]
    assert precipitate.get("intent") == ":chemical-equation"
