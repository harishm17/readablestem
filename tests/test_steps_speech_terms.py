"""Speech for matrices, cases, nuclide notation and unit tokens (T2-23), with the exact
expected strings. The rules are in `pipeline.speech`, `speech_nuclides` and `speech_units`."""

from __future__ import annotations

import pytest

from a11y_stem.pipeline.speech import mathml_to_speech
from a11y_stem.pipeline.speech_units import unit_words
from a11y_stem.pipeline.validate import safe_latex_to_mathml

NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def say(latex: str) -> str:
    return mathml_to_speech(safe_latex_to_mathml(latex))


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (
            r"\begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}",
            "the 2 by 2 matrix; row 1: 1, 2; row 2: 3, 4; end matrix",
        ),
        (
            r"\begin{pmatrix} a & b & c \\ d & e & f \end{pmatrix}",
            "the 2 by 3 matrix; row 1: a, b, c; row 2: d, e, f; end matrix",
        ),
        (
            r"\begin{bmatrix} 1 \\ 2 \\ 3 \end{bmatrix}",
            "the 3 by 1 column matrix; row 1: 1; row 2: 2; row 3: 3; end matrix",
        ),
        (
            r"\begin{bmatrix} 1 & 2 & 3 \end{bmatrix}",
            "the 1 by 3 row matrix; row 1: 1, 2, 3; end matrix",
        ),
        (
            r"A = \begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix}",
            "capital A equals the 2 by 2 matrix; row 1: 1, 0; row 2: 0, 1; end matrix",
        ),
        (
            r"\begin{vmatrix} a & b \\ c & d \end{vmatrix} = -2",
            "the 2 by 2 determinant; row 1: a, b; row 2: c, d; end determinant equals minus 2",
        ),
        (
            r"\left(\begin{array}{c} x \\ y \end{array}\right)",
            "the 2 by 1 column matrix; row 1: x; row 2: y; end matrix",
        ),
        # a fenced single cell is not a matrix
        (r"\begin{pmatrix} a \end{pmatrix}", "open parenthesis a close parenthesis"),
    ],
)
def test_matrices_read_with_their_size(latex, expected):
    assert say(latex) == expected


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (
            r"f(x) = \begin{cases} x^2 & x > 0 \\ 0 & \text{otherwise} \end{cases}",
            "f open parenthesis x close parenthesis equals cases: case 1: x squared, "
            "if x greater than 0; case 2: 0, otherwise; end cases",
        ),
        (
            r"|x| = \begin{cases} x & \text{if } x \geq 0 \\ -x & \text{if } x < 0 \end{cases}",
            "vertical bar x vertical bar equals cases: case 1: x, if x greater than or equal "
            "to 0; case 2: minus x, if x less than 0; end cases",
        ),
    ],
)
def test_cases_read_as_a_piecewise_definition(latex, expected):
    assert say(latex) == expected


def test_aligned_equations_and_brace_matrices_stay_plain_tables():
    assert say(r"\begin{aligned} a &= b \\ &= c \end{aligned}") == (
        "row 1: a equals b; row 2: equals c"
    )
    assert "cases" not in say(r"\begin{Bmatrix} a & b \\ c & d \end{Bmatrix}")


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"{}^{14}_{6}\mathrm{C}", "carbon 14, atomic number 6"),
        (r"{}^{235}_{92}\mathrm{U}", "uranium 235, atomic number 92"),
        (r"{}^{4}_{2}\mathrm{He}", "helium 4, atomic number 2"),
        (r"{}^{238}\mathrm{U}", "uranium 238"),
        (
            r"{}^{14}_{7}\mathrm{N} + {}^{4}_{2}\mathrm{He} \to {}^{17}_{8}\mathrm{O} "
            r"+ {}^{1}_{1}\mathrm{p}",
            "nitrogen 14, atomic number 7 plus helium 4, atomic number 2 right arrow oxygen 17, "
            "atomic number 8 plus proton, mass number 1, atomic number 1",
        ),
        (r"{}^{1}_{0}n", "neutron, mass number 1, atomic number 0"),
        (r"{}^{0}_{-1}e", "electron, mass number 0, atomic number minus 1"),
        (r"{}^{0}_{+1}e", "positron, mass number 0, atomic number plus 1"),
        (r"{}^{4}_{2}\alpha", "alpha particle, mass number 4, atomic number 2"),
        # not an element or a particle: the scripts before the symbol, as written
        (r"{}^{a}_{b}X", "superscript a subscript b capital X"),
    ],
)
def test_nuclide_notation_reads_as_the_element(latex, expected):
    assert say(latex) == expected


def test_multiscripts_nuclide():
    mathml = (
        f"<math {NS}><mmultiscripts><mi>C</mi><mprescripts/><mn>6</mn><mn>14</mn>"
        "</mmultiscripts></math>"
    )
    assert mathml_to_speech(mathml) == "carbon 14, atomic number 6"


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"5\,\text{m/s}", "5 meters per second"),
        (r"1\,\text{m}", "1 meter"),
        (r"9.8\ \mathrm{m/s^2}", "9.8 meters per second squared"),
        (r"v = 3\ \mathrm{kg\cdot m/s}", "v equals 3 kilogram meters per second"),
        (r"2\,\text{kg}", "2 kilograms"),
        (r"12\,\text{mm}", "12 millimeters"),
        (r"50\,\text{Hz}", "50 hertz"),
        (r"300\,\text{K}", "300 kelvin"),
        (r"101\,\text{kPa}", "101 kilopascals"),
        (r"3\times10^8\,\text{m/s}", "3 times 10 to the power 8 meters per second"),
        (r"5\,\mathrm{J/(kg\cdot K)}", "5 joules per kilogram kelvin"),
        (r"25^\circ\mathrm{C}", "25 degrees Celsius"),
        (r"25\,\text{°C}", "25 degrees Celsius"),
        (r"30^\circ", "30 degrees"),
        (r"\frac{150\ \text{km}}{3\ \text{h}}", "150 kilometers over 3 hours"),
        # not units: a variable, a differential, an ambiguous division, a word
        (r"2 m", "2 m"),
        (r"2x", "2 x"),
        (r"2\mathrm{d}x", "2 d x"),
        (r"5\,\mathrm{J/kg\ K}", "5 joules per kilogram capital K"),
        (r"3\ \text{apples}", "3 apples"),
    ],
)
def test_units_after_a_number_read_as_unit_names(latex, expected):
    assert say(latex) == expected


def test_openstax_unit_text_reads_as_unit_names():
    mathml = f"<math {NS}><mn>2.9</mn><mspace width='0.3em'/><mtext> m/s</mtext></math>"
    assert mathml_to_speech(mathml) == "2.9 meters per second"


@pytest.mark.parametrize(
    ("text", "count", "expected"),
    [
        ("m", "1", "meter"),
        ("m", "2", "meters"),
        ("ft", "3", "feet"),
        ("m*s", "2", "meter seconds"),
        ("s^-1", "5", "per second"),
        ("m^3", "2", "meters cubed"),
        ("kg*m/s^2", "1", "kilogram meter per second squared"),
        ("W/(m^2*K)", "4", "watts per meter squared kelvin"),
        ("m/s/s", "2", None),
        ("xyz", "2", None),
        ("", "2", None),
        ("m/", "2", None),
    ],
)
def test_unit_words(text, count, expected):
    assert unit_words(text, count) == expected
