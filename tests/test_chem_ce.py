r"""`\ce{...}` (mhchem) turned into plain LaTeX for the strict converter (`pipeline.chem`),
the bracket-script grouping, and the MathML a converted species ends up as."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from readablestem.core.latex_groups import group_delimited_scripts
from readablestem.pipeline.chem import CeError, ce_to_latex, convert_ce_commands
from readablestem.pipeline.validate import check_latex, has_errors, safe_latex_to_mathml

NS = "{http://www.w3.org/1998/Math/MathML}"


@pytest.mark.parametrize(
    ("ce", "latex"),
    [
        # formulas
        ("H2O", r"\text{H}_2\text{O}"),
        ("C6H12O6", r"\text{C}_6\text{H}_{12}\text{O}_6"),
        ("Ca(OH)2", r"\text{Ca}(\text{OH})_2"),
        ("Al2(SO4)3", r"\text{Al}_2(\text{SO}_4)_3"),
        ("NaCl", r"\text{NaCl}"),
        ("CuSO4.5H2O", r"\text{CuSO}_4\cdot 5\text{H}_2\text{O}"),
        # charges
        ("Na+", r"\text{Na}^+"),
        ("OH-", r"\text{OH}^-"),
        ("H3O+", r"\text{H}_3\text{O}^+"),
        ("NH4+", r"\text{NH}_4^+"),
        ("SO4^2-", r"\text{SO}_4^{2-}"),
        ("Fe^3+", r"\text{Fe}^{3+}"),
        ("Fe^{3+}", r"\text{Fe}^{3+}"),
        ("Fe+++", r"\text{Fe}^{3+}"),
        ("CH3CO2-", r"\text{CH}_3\text{CO}_2^-"),
        ("Hg2^2+", r"\text{Hg}_2^{2+}"),
        ("[Cu(NH3)4]^2+", r"[\text{Cu}(\text{NH}_3)_4]^{2+}"),
        ("e-", r"\text{e}^-"),
        # states
        ("NaCl(aq)", r"\text{NaCl}\text{(aq)}"),
        ("H2O(l)", r"\text{H}_2\text{O}\text{(l)}"),
        ("CO2(g)", r"\text{CO}_2\text{(g)}"),
        ("AgCl(s)", r"\text{AgCl}\text{(s)}"),
        ("Cl-(aq)", r"\text{Cl}^-\text{(aq)}"),
        # coefficients and reactions
        ("2 H2 + O2 -> 2 H2O", r"2 \text{H}_2 + \text{O}_2 \longrightarrow 2 \text{H}_2\text{O}"),
        ("2H2+O2->2H2O", r"2 \text{H}_2 + \text{O}_2 \longrightarrow 2 \text{H}_2\text{O}"),
        ("1/2 O2", r"\frac{1}{2} \text{O}_2"),
        ("0.5 O2", r"0.5 \text{O}_2"),
        ("N2 + 3 H2 <=> 2 NH3", r"\text{N}_2 + 3 \text{H}_2 \rightleftharpoons 2 \text{NH}_3"),
        ("A <- B", None),  # refused
        ("Fe^3+ + e- -> Fe^2+", r"\text{Fe}^{3+} + \text{e}^- \longrightarrow \text{Fe}^{2+}"),
        (
            "Ag+(aq) + Cl-(aq) -> AgCl(s)",
            r"\text{Ag}^+\text{(aq)} + \text{Cl}^-\text{(aq)}"
            r" \longrightarrow \text{AgCl}\text{(s)}",
        ),
        ("H+ + OH- -> H2O", r"\text{H}^+ + \text{OH}^- \longrightarrow \text{H}_2\text{O}"),
        ("H2 + I2 <-> 2 HI", r"\text{H}_2 + \text{I}_2 \longleftrightarrow 2 \text{HI}"),
        ("CaO <-- CaCO3", r"\text{CaO} \longleftarrow \text{CaCO}_3"),
        (
            r"CaCO3(s) ->[\Delta] CaO(s) + CO2(g)",
            r"\text{CaCO}_3\text{(s)} \xrightarrow{\Delta}"
            r" \text{CaO}\text{(s)} + \text{CO}_2\text{(g)}",
        ),
        ("A ->[heat] B", None),  # refused
        (
            "H2 + O2 ->[heat] H2O",
            r"\text{H}_2 + \text{O}_2 \xrightarrow{\text{heat}} \text{H}_2\text{O}",
        ),
        (r"N2 <=>[$\Delta$] NO", r"\text{N}_2 \overset{\Delta}{\rightleftharpoons} \text{NO}"),
        # bonds
        ("H2C=CH2", r"\text{H}_2\text{C}=\text{CH}_2"),
        ("HC#CH", r"\text{HC}\equiv \text{CH}"),
        ("CH3-CH3", r"\text{CH}_3\text{-}\text{CH}_3"),
        # nuclides
        ("^{14}_{6}C", r"{}^{14}_6\text{C}"),
        ("^{0}_{-1}e", r"{}^0_{-1}\text{e}"),
        ("_{6}^{14}C", r"{}^{14}_6\text{C}"),
        (
            "^{238}_{92}U -> ^{234}_{90}Th + ^{4}_{2}He",
            r"{}^{238}_{92}\text{U} \longrightarrow {}^{234}_{90}\text{Th} + {}^4_2\text{He}",
        ),
        ("n", r"\text{n}"),
        # particles, generic nuclides, an excited mark, a coefficient before a nuclide
        (r"^{4}_{2}\alpha", r"{}^4_2\alpha"),
        (r"^{0}_{0}\gamma", r"{}^0_0\gamma"),
        (r"\gamma + \gamma", r"\gamma + \gamma"),
        (r"^{A}_{Z}X", r"{}^A_Z\text{X}"),
        (
            r"^{87}_{38}Sr^{*} -> ^{87}_{38}Sr + \gamma",
            r"{}^{87}_{38}\text{Sr}^* \longrightarrow {}^{87}_{38}\text{Sr} + \gamma",
        ),
        (
            r"^{235}_{92}U + ^{1}_{0}n -> 3^{1}_{0}n",
            r"{}^{235}_{92}\text{U} + {}^1_0\text{n} \longrightarrow 3 {}^1_0\text{n}",
        ),
        # a state symbol after a space belongs to the species before it
        (r"H2PO4- (aq) + OH-(aq)", r"\text{H}_2\text{PO}_4^-\text{(aq)} + \text{OH}^-\text{(aq)}"),
    ],
)
def test_ce_subset(ce, latex):
    if latex is None:
        with pytest.raises(CeError):
            ce_to_latex(ce)
        return
    assert ce_to_latex(ce) == latex
    # What comes out passes the allowlist and converts.
    assert not has_errors(check_latex(latex))
    safe_latex_to_mathml(latex, "inline")


@pytest.mark.parametrize(
    "ce",
    [
        "",
        "   ",
        "N2 + 3 H2 <=>> 2 NH3",  # an unequal equilibrium: not guessed
        "N2 + 3 H2 <<=> 2 NH3",
        "A ->[x][y] B",  # a two-part arrow label
        "H2O ^ ",  # a mark for gas, with no number
        "$H2O$",  # LaTeX math inside
        "H2O v",  # a precipitate mark
        "Zn^2+(aq, 0.10 M)",  # a concentration in a state symbol
        "hello world",  # text is not a species
        "Xx2",  # not an element
        "H2O \\text{liquid}",  # LaTeX commands inside
        "^+H",  # a charge with nothing before it
        "-H",  # a sign with nothing before it
        "2^{x y}Fe",
        "{H2O",
    ],
)
def test_ce_outside_the_subset_is_refused(ce):
    with pytest.raises(CeError):
        ce_to_latex(ce)


def test_convert_ce_commands_in_a_longer_expression():
    assert (
        convert_ce_commands(r"K = \frac{[\ce{NH3}]^2}{[\ce{N2}][\ce{H2}]^3}")
        == r"K = \frac{[\text{NH}_3]^2}{[\text{N}_2][\text{H}_2]^3}"
    )
    assert convert_ce_commands(r"x + 1") == r"x + 1"
    assert convert_ce_commands(r"\ce{H2O}\ce{Na+}") == r"\text{H}_2\text{O}\text{Na}^+"
    assert convert_ce_commands(r"\ce {H2O}") == r"\text{H}_2\text{O}"  # a space before the brace


def test_a_name_that_only_starts_with_ce_is_not_converted():
    assert convert_ce_commands(r"\cell{x}") == r"\cell{x}"


def test_convert_ce_commands_refuses_what_ce_refuses():
    with pytest.raises(CeError):
        convert_ce_commands(r"x = \ce{N2 <=>> NO}")
    with pytest.raises(CeError):
        convert_ce_commands(r"\ce H2O")
    with pytest.raises(CeError):
        convert_ce_commands(r"\ce{H2O")


def test_an_unconverted_ce_does_not_pass_the_latex_check():
    assert has_errors(check_latex(r"\ce{H2O}"))


# ------------------------------------------------------------- bracket-script grouping


@pytest.mark.parametrize(
    ("latex", "grouped"),
    [
        (r"[A]_0", r"{[A]}_0"),
        (r"k[A]^2[B]", r"k{[A]}^2[B]"),
        (r"[A]_0 = [A]_t e^{-kt}", r"{[A]}_0 = {[A]}_t e^{-kt}"),
        (r"\mathrm{Al_2(SO_4)_3}", r"\mathrm{Al_2{(SO_4)}_3}"),
        (r"(a+b)^2", r"{(a+b)}^2"),
        (r"[(A)_1]_2", r"{[{(A)}_1]}_2"),
        (r"[a]^{2}_{1}", r"{[a]}^{2}_{1}"),
        (r"[A] _0", r"{[A]} _0"),  # a space before the script
        # nothing to group
        (r"[A]", r"[A]"),
        (r"f(x)", r"f(x)"),
        (r"\left[A\right]_0", r"\left[A\right]_0"),
        (r"\left(a+b\right)^2", r"\left(a+b\right)^2"),
        (r"\sqrt[3]{x}", r"\sqrt[3]{x}"),
        (r"\sqrt[n]{x}^2", r"\sqrt[n]{x}^2"),
        (r"[0,1)^2", r"[0,1)^2"),  # half-open: the brackets do not match
        (r"\{a\}_1", r"\{a\}_1"),  # escaped
        (r"x_{[a]}", r"x_{[a]}"),
        (r"{(a)}_1", r"{(a)}_1"),  # already a group
        (r"a_1 b", r"a_1 b"),
    ],
)
def test_bracket_scripts_are_grouped(latex, grouped):
    assert group_delimited_scripts(latex) == grouped


def local(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def parse(latex: str) -> ET.Element:
    return ET.fromstring(safe_latex_to_mathml(latex, "inline"))


def test_a_subscript_after_a_bracket_belongs_to_the_group_in_the_mathml():
    root = parse(r"[A]_0")
    (msub,) = [n for n in root.iter() if local(n) == "msub"]
    base, sub = list(msub)
    assert local(base) == "mrow" and [local(c) for c in base] == ["mo", "mi", "mo"]
    assert "".join(sub.itertext()) == "0"
    # Not the old shape: a subscript on the closing bracket alone.
    assert not any(local(n) == "msub" and "".join(n[0].itertext()) == "]" for n in root.iter())


def test_a_subscript_after_a_parenthesis_in_a_formula_belongs_to_the_group():
    root = parse(r"\mathrm{Al_2(SO_4)_3}")
    groups = [n for n in root.iter() if local(n) == "msub" and "".join(n[0].itertext()) == "(SO4)"]
    assert len(groups) == 1 and local(groups[0][0]) == "mrow"
    assert "".join(groups[0][1].itertext()) == "3"


def test_a_converted_species_has_its_element_symbols_in_one_token_each():
    # \text groups: "Na" is one token and the charge hangs on it, as OpenStax writes it.
    root = parse(ce_to_latex("Na+"))
    (msup,) = [n for n in root.iter() if local(n) == "msup"]
    assert local(msup[0]) == "mtext" and "".join(msup[0].itertext()) == "Na"
    assert "".join(msup[1].itertext()) == "+"
    root = parse(ce_to_latex("CaCl2"))
    tokens = [(local(n), "".join(n.itertext())) for n in root.iter() if local(n) in {"mtext", "mn"}]
    assert tokens == [("mtext", "CaCl"), ("mn", "2")]


def test_a_charge_on_a_mathrm_symbol_hangs_on_the_whole_symbol():
    # What a model writes without \ce: the converter hangs the charge on the lowercase letter
    # (N, msup(a, +)); it belongs to "Na".
    root = parse(r"\mathrm{Na^+}")
    (msup,) = [n for n in root.iter() if local(n) == "msup"]
    assert local(msup[0]) == "mrow" and [local(c) for c in msup[0]] == ["mi", "mi"]
    assert "".join(msup[0].itertext()) == "Na" and "".join(msup[1].itertext()) == "+"
    root = parse(r"\mathrm{Cl_2}")
    (msub,) = [n for n in root.iter() if local(n) == "msub"]
    assert "".join(msub[0].itertext()) == "Cl" and "".join(msub[1].itertext()) == "2"
    # A symbol with no script stays two upright letters.
    root = parse(r"\mathrm{CaO}")
    assert ["".join(n.itertext()) for n in root.iter() if local(n) == "mi"] == ["C", "a", "O"]


def test_symbols_that_are_not_elements_are_not_grouped():
    root = parse(r"\mathrm{Hz_2}\ \mathrm{km^2}")
    tokens = ["".join(n.itertext()) for n in root.iter() if local(n) == "mi"]
    assert tokens == ["H", "z", "k", "m"]


def test_a_long_arrow_is_an_operator():
    root = parse(ce_to_latex("A2 -> B2".replace("A2", "H2").replace("B2", "O2")))
    arrows = [n for n in root.iter() if len(n) == 0 and (n.text or "") == "⟶"]
    assert [local(n) for n in arrows] == ["mo"]
