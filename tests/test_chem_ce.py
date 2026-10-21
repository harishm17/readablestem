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
        ("H2O", r"\mathrm{H_2O}"),
        ("C6H12O6", r"\mathrm{C_6H_{12}O_6}"),
        ("Ca(OH)2", r"\mathrm{Ca(OH)_2}"),
        ("Al2(SO4)3", r"\mathrm{Al_2(SO_4)_3}"),
        ("NaCl", r"\mathrm{NaCl}"),
        ("CuSO4.5H2O", r"\mathrm{CuSO_4\cdot 5H_2O}"),
        # charges
        ("Na+", r"\mathrm{Na^+}"),
        ("OH-", r"\mathrm{OH^-}"),
        ("H3O+", r"\mathrm{H_3O^+}"),
        ("NH4+", r"\mathrm{NH_4^+}"),
        ("SO4^2-", r"\mathrm{SO_4^{2-}}"),
        ("Fe^3+", r"\mathrm{Fe^{3+}}"),
        ("Fe^{3+}", r"\mathrm{Fe^{3+}}"),
        ("Fe+++", r"\mathrm{Fe^{3+}}"),
        ("CH3CO2-", r"\mathrm{CH_3CO_2^-}"),
        ("Hg2^2+", r"\mathrm{Hg_2^{2+}}"),
        ("[Cu(NH3)4]^2+", r"\mathrm{[Cu(NH_3)_4]^{2+}}"),
        ("e-", r"\mathrm{e^-}"),
        # states
        ("NaCl(aq)", r"\mathrm{NaCl(aq)}"),
        ("H2O(l)", r"\mathrm{H_2O(l)}"),
        ("CO2(g)", r"\mathrm{CO_2(g)}"),
        ("AgCl(s)", r"\mathrm{AgCl(s)}"),
        ("Cl-(aq)", r"\mathrm{Cl^-(aq)}"),
        # coefficients and reactions
        ("2 H2 + O2 -> 2 H2O", r"2 \mathrm{H_2} + \mathrm{O_2} \longrightarrow 2 \mathrm{H_2O}"),
        ("2H2+O2->2H2O", r"2 \mathrm{H_2} + \mathrm{O_2} \longrightarrow 2 \mathrm{H_2O}"),
        ("1/2 O2", r"\frac{1}{2} \mathrm{O_2}"),
        ("0.5 O2", r"0.5 \mathrm{O_2}"),
        (
            "N2 + 3 H2 <=> 2 NH3",
            r"\mathrm{N_2} + 3 \mathrm{H_2} \rightleftharpoons 2 \mathrm{NH_3}",
        ),
        ("A <- B", None),  # A is not an element: rejected, below
        (
            "Fe^3+ + e- -> Fe^2+",
            r"\mathrm{Fe^{3+}} + \mathrm{e^-} \longrightarrow \mathrm{Fe^{2+}}",
        ),
        (
            "Ag+(aq) + Cl-(aq) -> AgCl(s)",
            r"\mathrm{Ag^+(aq)} + \mathrm{Cl^-(aq)} \longrightarrow \mathrm{AgCl(s)}",
        ),
        ("H+ + OH- -> H2O", r"\mathrm{H^+} + \mathrm{OH^-} \longrightarrow \mathrm{H_2O}"),
        ("H2 + I2 <-> 2 HI", r"\mathrm{H_2} + \mathrm{I_2} \longleftrightarrow 2 \mathrm{HI}"),
        ("CaO <-- CaCO3", r"\mathrm{CaO} \longleftarrow \mathrm{CaCO_3}"),
        (
            "CaCO3(s) ->[\\Delta] CaO(s) + CO2(g)",
            r"\mathrm{CaCO_3(s)} \xrightarrow{\Delta} \mathrm{CaO(s)} + \mathrm{CO_2(g)}",
        ),
        ("A ->[heat] B", None),
        (
            "H2 + O2 ->[heat] H2O",
            r"\mathrm{H_2} + \mathrm{O_2} \xrightarrow{\text{heat}} \mathrm{H_2O}",
        ),
        ("N2 <=>[$\\Delta$] NO", r"\mathrm{N_2} \overset{\Delta}{\rightleftharpoons} \mathrm{NO}"),
        # bonds
        ("H2C=CH2", r"\mathrm{H_2C=CH_2}"),
        ("HC#CH", r"\mathrm{HC\equiv CH}"),
        ("CH3-CH3", r"\mathrm{CH_3\text{-}CH_3}"),
        # nuclides
        ("^{14}_{6}C", r"{}^{14}_6\mathrm{C}"),
        ("^{0}_{-1}e", r"{}^0_{-1}\mathrm{e}"),
        ("_{6}^{14}C", r"{}^{14}_6\mathrm{C}"),
        (
            "^{238}_{92}U -> ^{234}_{90}Th + ^{4}_{2}He",
            r"{}^{238}_{92}\mathrm{U} \longrightarrow {}^{234}_{90}\mathrm{Th}"
            r" + {}^4_2\mathrm{He}",
        ),
        ("n", r"\mathrm{n}"),
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
        == r"K = \frac{[\mathrm{NH_3}]^2}{[\mathrm{N_2}][\mathrm{H_2}]^3}"
    )
    assert convert_ce_commands(r"x + 1") == r"x + 1"
    assert convert_ce_commands(r"\ce{H2O}\ce{Na+}") == r"\mathrm{H_2O}\mathrm{Na^+}"
    assert convert_ce_commands(r"\ce {H2O}") == r"\mathrm{H_2O}"  # a space before the brace


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


def test_a_charge_or_count_hangs_on_the_whole_element_symbol():
    # The converter hangs it on the lowercase letter: N, msup(a, +). It belongs to "Na".
    root = parse(ce_to_latex("Na+"))
    (msup,) = [n for n in root.iter() if local(n) == "msup"]
    assert local(msup[0]) == "mrow" and [local(c) for c in msup[0]] == ["mi", "mi"]
    assert "".join(msup[0].itertext()) == "Na" and "".join(msup[1].itertext()) == "+"
    root = parse(ce_to_latex("CaCl2"))
    (msub,) = [n for n in root.iter() if local(n) == "msub"]
    assert "".join(msub[0].itertext()) == "Cl" and "".join(msub[1].itertext()) == "2"
    # A symbol with no script stays two upright letters, as published MathML has it.
    tokens = ["".join(n.itertext()) for n in root.iter() if local(n) in {"mi", "mn"}]
    assert tokens == ["C", "a", "C", "l", "2"]


def test_symbols_that_are_not_elements_are_not_grouped():
    root = parse(r"\mathrm{Hz_2}\ \mathrm{km^2}")
    tokens = ["".join(n.itertext()) for n in root.iter() if local(n) == "mi"]
    assert tokens == ["H", "z", "k", "m"]


def test_a_long_arrow_is_an_operator():
    root = parse(ce_to_latex("A2 -> B2".replace("A2", "H2").replace("B2", "O2")))
    arrows = [n for n in root.iter() if len(n) == 0 and (n.text or "") == "⟶"]
    assert [local(n) for n in arrows] == ["mo"]
