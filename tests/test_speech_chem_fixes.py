r"""Chemistry speech defects found by reading what a listener hears: a redox couple read as
division, a state with conditions spelled out, an arrow in its own table cell, an excited
nuclide, and units read inconsistently. Every case below was wrong before; the last tests
pin what must read as it did (maths, physics, a coefficient that looks like a unit)."""

# ruff: noqa: E501

from __future__ import annotations

import pytest

from readablestem.pipeline.speech import mathml_to_speech
from readablestem.pipeline.validate import safe_latex_to_mathml


def say(latex: str) -> str:
    return mathml_to_speech(safe_latex_to_mathml(latex, "inline"))


def say_mathml(inner: str) -> str:
    return mathml_to_speech(f'<math xmlns="http://www.w3.org/1998/Math/MathML">{inner}</math>')


# ------------------------------------------------------------------ redox couples


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        (r"\mathrm{Cu^{2+}/Cu}", "C u 2 plus slash C u"),
        (r"\text{Zn}^{2+}/\text{Zn}", "Z n 2 plus slash Z n"),
        (r"\text{Cl}_2 / \text{Cl}^-", "C l 2 slash C l minus"),
        (r"\text{Fe}^{3+}/\text{Fe}^{2+}", "F e 3 plus slash F e 2 plus"),
        (r"\text{Cu}^{2+}(\text{aq})/\text{Cu}(\text{s})", "C u 2 plus aqueous, slash C u solid"),
        (
            r"E^\circ(\mathrm{Cu^{2+}/Cu}) = 0.34\ \text{V}",
            "capital E degrees open parenthesis C u 2 plus slash C u close parenthesis "
            "equals 0.34 volts",
        ),
        (
            r"E^\circ_{\text{cell}} = E^\circ(\text{Cu}^{2+}/\text{Cu}) - E^\circ(\text{Zn}^{2+}/\text{Zn})",
            "capital E sub cell degrees equals capital E degrees open parenthesis C u 2 plus "
            "slash C u close parenthesis minus capital E degrees open parenthesis Z n 2 plus "
            "slash Z n close parenthesis",
        ),
    ],
)
def test_a_couple_is_not_a_quotient(latex: str, speech: str) -> None:
    assert say(latex) == speech


def test_openstax_couple_shape() -> None:
    # OpenStax: one mtext per symbol, the charge on an msup.
    inner = "<mi>E</mi><mo>(</mo><msup><mtext>Cu</mtext><mrow><mn>2</mn><mo>+</mo></mrow></msup>"
    inner += "<mo>/</mo><mtext>Cu</mtext><mo>)</mo>"
    assert say_mathml(inner) == (
        "capital E open parenthesis C u 2 plus slash C u close parenthesis"
    )


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        (r"x/y", "x divided by y"),
        (r"a/b = 3", "a divided by b equals 3"),
        (
            r"[\text{A}]/[\text{B}]",
            "open bracket A close bracket divided by the concentration of B",
        ),
        (r"\text{mol/L}", "mol/L"),
        # italic letters are variables, not species: no evidence of a couple
        (r"Cu^{2+}/Cu", "capital C u to the power 2 plus divided by capital C u"),
    ],
)
def test_division_still_reads_as_division(latex: str, speech: str) -> None:
    assert say(latex) == speech


# ------------------------------------------------------- states with conditions


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        (r"\mathrm{Zn^{2+}(aq,\ 0.10\ M)}", "Z n 2 plus aqueous, 0.10 molar"),
        (r"\text{Zn}^{2+}\text{(aq, 0.10 M)}", "Z n 2 plus aqueous, 0.10 molar"),
        (r"\text{Zn}^{2+}(\text{aq}, 1\ \text{M})", "Z n 2 plus aqueous, 1 molar"),
        (r"\text{Zn}^{2+}(aq, 0.10\ M)", "Z n 2 plus aqueous, 0.10 molar"),
        (r"\text{Ag}^+(\text{aq}, 0.50\ M)", "A g plus aqueous, 0.50 molar"),
        (
            r"\text{Zn}^{2+}(\text{aq}, 1.0\ \text{atm}) + \text{H}_2(\text{g}, 1\ \text{atm}, 298\ \text{K})",
            "Z n 2 plus aqueous, 1.0 atmospheres, plus H 2 gas, 1 atmosphere, 298 kelvin",
        ),
        (r"\text{Zn}^{2+}(\text{aq}) + \text{OH}^-", "Z n 2 plus aqueous, plus O H minus"),
    ],
)
def test_state_with_conditions(latex: str, speech: str) -> None:
    assert say(latex) == speech


def test_state_with_conditions_openstax_flat_shape() -> None:
    inner = (
        "<mtext>Zn</mtext><mo>(</mo><mi>a</mi><mi>q</mi><mo>,</mo><mn>0.10</mn><mi>M</mi>"
        "<mo>)</mo><mo>+</mo><mtext>Cu</mtext>"
    )
    assert say_mathml(inner) == "Z n aqueous, 0.10 molar, plus C u"


def test_a_condition_that_is_not_a_quantity_is_not_read_as_one() -> None:
    # nothing is invented: the markup reads as before
    assert say(r"\text{Zn}^{2+}(\text{aq}, \text{conc})") == (
        "Z n 2 plus open parenthesis aq comma conc close parenthesis"
    )


# --------------------------------------------------------- arrows in table cells


def test_equilibrium_arrow_cell() -> None:
    latex = (
        r"\begin{array}{ccc} \mathrm{H_2O(l)} & \rightleftharpoons &"
        r" \mathrm{H^+(aq)} + \mathrm{OH^-(aq)} \end{array}"
    )
    assert (
        say(latex) == "H 2 O liquid, is in equilibrium with H plus aqueous, plus O H minus aqueous"
    )


def test_label_cell_stays_apart_from_the_reaction() -> None:
    latex = (
        r"\begin{array}{llcl} \text{Anode:} & \mathrm{Zn(s)} & \to & \mathrm{Zn^{2+}(aq)} + 2e^- \\"
        r" \text{Cathode:} & \mathrm{Cu^{2+}(aq)} + 2e^- & \to & \mathrm{Cu(s)} \end{array}"
    )
    assert say(latex) == (
        "row 1: Anode: Z n solid, reacts to form Z n 2 plus aqueous, plus 2 e to the power minus; "
        "row 2: Cathode: C u 2 plus aqueous, plus 2 e to the power minus reacts to form C u solid"
    )


def test_arrow_cell_outside_chemistry_reads_as_before() -> None:
    assert say(r"\begin{array}{ccc} x & \to & y \end{array}") == "x right arrow y"
    assert say(r"\begin{array}{ccc} A & \rightleftharpoons & B \end{array}") == (
        "capital A, equilibrium arrow, capital B"
    )


def test_reaction_in_one_cell_keeps_its_neighbour_cell_apart() -> None:
    latex = r"\begin{array}{cc} \mathrm{NO_2 + CO \to NO + CO_2} & \text{rate} = k[\mathrm{NO_2}]^2 \end{array}"
    assert say(latex) == (
        "N O 2 plus C O reacts to form N O plus C O 2, rate equals k the concentration of N O 2 squared"
    )


# ------------------------------------------------------------- excited nuclides


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        (
            r"{}^{60}_{28}\text{Ni}^* \longrightarrow {}^{60}_{28}\text{Ni} + \gamma",
            "nickel 60, atomic number 28, excited state reacts to form nickel 60, "
            "atomic number 28 plus gamma",
        ),
        (
            r"{}^{87}_{38}\text{Sr}^* \longrightarrow {}^{87}_{38}\text{Sr} + \gamma",
            "strontium 87, atomic number 38, excited state reacts to form strontium 87, "
            "atomic number 38 plus gamma",
        ),
        (
            r"{}^{4}_{2}\text{He}^* \to {}^{4}_{2}\text{He}",
            "helium 4, atomic number 2, excited state reacts to form helium 4, atomic number 2",
        ),
        (r"{}^{60}_{28}\text{Ni}", "nickel 60, atomic number 28"),
    ],
)
def test_excited_nuclide(latex: str, speech: str) -> None:
    assert say(latex) == speech


def test_a_star_outside_a_nuclide_reads_as_before() -> None:
    # `O*` on its own could be a set or an operator, so it is not guessed to be excited
    assert say(r"\text{O}^*") == "O to the power times"


# ------------------------------------------------------------------------ units


def test_unit_after_a_decimal_in_a_chemistry_row() -> None:
    # V is vanadium to the element check: both volts must still read as volts
    assert say(r"0.000\ \text{V} - 0.0592\ \text{V}") == "0.000 volts minus 0.0592 volts"
    assert say(r"E = 0.000\ \text{V} - 0.0592\ \text{V} \log 10") == (
        "capital E equals 0.000 volts minus 0.0592 volts log 10"
    )


def test_whole_number_before_an_element_symbol_stays_a_coefficient() -> None:
    assert say(r"2\ \text{K} + 2\ \text{H}_2\text{O} \to 2\ \text{KOH} + \text{H}_2") == (
        "2 K plus 2 H 2 O reacts to form 2 K O H plus H 2"
    )
    assert say(r"0.5\ \text{H}_2 + 0.5\ \text{Cl}_2 \to \text{HCl}") == (
        "0.5 H 2 plus 0.5 C l 2 reacts to form H C l"
    )


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        (
            r"0.00300\ \text{mol}\ \text{L}^{-1}\ \text{s}^{-1}",
            "0.00300 moles per liter second",
        ),
        (r"8.314\ \text{J}\ \text{mol}^{-1}\ \text{K}^{-1}", "8.314 joules per mole kelvin"),
        (
            r"k = 5.75 \times 10^4\ \text{mol}^{-1}\ \text{L}\ \text{s}^{-1}",
            "k equals 5.75 times 10 to the power 4 liters per mole second",
        ),
    ],
)
def test_rate_and_molar_units(latex: str, speech: str) -> None:
    assert say(latex) == speech


def test_units_outside_chemistry_read_as_before() -> None:
    assert (
        say(r"3\ \text{kg m}^{-1}\ \text{s}^{-2}") == "3 kilograms per meter s to the power minus 2"
    )
    assert say(r"2\ \text{m s}^{-1}") == "2 meters per second"
    assert say(r"9.8\ \text{m/s}^2") == "9.8 meters per second squared"


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        (r"\text{H}_2\text{O} + \text{CO}_2 \uparrow", "H 2 O plus C O 2 gas given off"),
        (
            r"\text{Ag}^+ + \text{Cl}^- \to \text{AgCl} \downarrow",
            "A g plus, plus C l minus reacts to form A g C l precipitate",
        ),
        (r"\text{AgCl}(\text{s}) \downarrow", "A g C l solid, precipitate"),
        # outside chemistry the arrows read as arrows
        (r"x \uparrow", "x up arrow"),
        (r"f \downarrow", "f down arrow"),
    ],
)
def test_gas_and_precipitate_marks(latex: str, speech: str) -> None:
    assert say(latex) == speech
