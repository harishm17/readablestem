r"""Chemistry in the speech reading (`pipeline.speech_chem`): formulas, ions, state symbols,
arrows and concentrations as a teacher says them, from the converter's MathML (the model's
`\ce` written out) and from OpenStax's (one `mtext` for "HSO", empty-based charges). What is
not chemistry reads as it did."""

from __future__ import annotations

import pytest

from readablestem.pipeline.chem import convert_ce_commands
from readablestem.pipeline.speech import mathml_to_speech
from readablestem.pipeline.validate import safe_latex_to_mathml


def say(latex: str) -> str:
    return mathml_to_speech(safe_latex_to_mathml(convert_ce_commands(latex), "inline"))


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        # formulas: counts are numbers, element symbols are spelled
        (r"\ce{H2O}", "H 2 O"),
        (r"\ce{CO2}", "C O 2"),
        (r"\ce{C6H12O6}", "C 6 H 12 O 6"),
        (r"\ce{NaCl}", "N a C l"),
        (r"\ce{Al2(SO4)3}", "A l 2 open parenthesis S O 4 close parenthesis 3"),
        (r"\ce{Ca(OH)2}", "C a open parenthesis O H close parenthesis 2"),
        # ions: the charge is a sign, with its number first
        (r"\ce{Na+}", "N a plus"),
        (r"\ce{Cl-}", "C l minus"),
        (r"\ce{H3O+}", "H 3 O plus"),
        (r"\ce{NH4+}", "N H 4 plus"),
        (r"\ce{SO4^2-}", "S O 4 2 minus"),
        (r"\ce{Fe^3+}", "F e 3 plus"),
        (r"\ce{CH3CO2-}", "C H 3 C O 2 minus"),
        (r"\ce{e-}", "e minus"),
        (
            r"\ce{[Cu(NH3)4]^2+}",
            "open bracket C u open parenthesis N H 3 close parenthesis 4 close bracket 2 plus",
        ),
        # state symbols
        (r"\ce{NaCl(aq)}", "N a C l aqueous"),
        (r"\ce{H2O(l)}", "H 2 O liquid"),
        (r"\ce{CO2(g)}", "C O 2 gas"),
        (r"\ce{SO4^2-(aq)}", "S O 4 2 minus aqueous"),
        # reactions: the arrow is a verb, a plus after a state or a charge is set off
        (r"\ce{2 H2 + O2 -> 2 H2O}", "2 H 2 plus O 2 reacts to form 2 H 2 O"),
        (r"\ce{N2 + 3 H2 <=> 2 NH3}", "N 2 plus 3 H 2 is in equilibrium with 2 N H 3"),
        (
            r"\ce{Ag+(aq) + Cl-(aq) -> AgCl(s)}",
            "A g plus aqueous, plus C l minus aqueous, reacts to form A g C l solid",
        ),
        (
            r"\ce{CaCO3(s) ->[\Delta] CaO(s) + CO2(g)}",
            "C a C O 3 solid, reacts, with heat, to form C a O solid, plus C O 2 gas",
        ),
        (r"\ce{Na+ + Cl- -> NaCl}", "N a plus, plus C l minus reacts to form N a C l"),
        (r"\ce{H2 + I2 <-> 2 HI}", "H 2 plus I 2 left right arrow 2 H I"),
        # concentrations
        (
            r"[\ce{H3O+}] = [\ce{OH-}]",
            "the concentration of H 3 O plus equals the concentration of O H minus",
        ),
        (r"[\ce{NH3}]^2", "the concentration of N H 3 squared"),
        (
            r"K = \frac{[\ce{NH3}]^2}{[\ce{N2}][\ce{H2}]^3}",
            "capital K equals the fraction the concentration of N H 3 squared over the"
            " concentration of N 2 the concentration of H 2 cubed end fraction",
        ),
        # nuclides keep their reading
        (r"\ce{^{14}_{6}C}", "carbon 14, atomic number 6"),
        (
            r"\ce{^{14}_{6}C -> ^{14}_{7}N + e-}",
            "carbon 14, atomic number 6 reacts to form nitrogen 14, atomic number 7 plus e minus",
        ),
    ],
)
def test_chemistry_is_read_as_a_teacher_says_it(latex, speech):
    assert say(latex) == speech


MATHML = "<math xmlns='http://www.w3.org/1998/Math/MathML'>{}</math>"


@pytest.mark.parametrize(
    ("body", "speech"),
    [
        # OpenStax: an ion whose species has a count carries its charge on an empty base
        (
            "<msub><mtext>HSO</mtext><mn>3</mn></msub><msup><mrow></mrow><mtext>−</mtext></msup>"
            "<mo>(</mo><mi>a</mi><mi>q</mi><mo>)</mo>",
            "H S O 3 minus aqueous",
        ),
        # a coefficient and the electron in one token, a charge as text
        (
            "<msup><mtext>Cu</mtext><mn>2+</mn></msup><mo>(</mo><mi>a</mi><mi>q</mi><mo>)</mo>"
            "<mo>+</mo><msup><mrow><mtext>2e</mtext></mrow><mtext>−</mtext></msup>"
            "<mo>⟶</mo><mtext>Cu</mtext><mo>(</mo><mi>s</mi><mo>)</mo>",
            "C u 2 plus aqueous, plus 2 e minus reacts to form C u solid",
        ),
        # the charge of a bracketed species is on the row that holds the opening bracket
        (
            "<mfrac><mrow><msup><mrow><mo>[</mo><mtext>Co</mtext></mrow><mn>2+</mn></msup><mo>]</mo></mrow>"
            "<mrow><msup><mrow><mo>[</mo><mtext>Fe</mtext></mrow><mn>2+</mn></msup><mo>]</mo></mrow></mfrac>",
            "the fraction the concentration of C o 2 plus over the concentration of F e 2 plus"
            " end fraction",
        ),
        # a bracket around one mtext species
        (
            "<mi>k</mi><msup><mrow><mo>[</mo><mtext>NO</mtext><mo>]</mo></mrow><mn>2</mn></msup>",
            "k the concentration of N O squared",
        ),
        # an ion saved by an earlier version: the charge on the lowercase letter of the symbol
        (
            '<mn>2</mn><mi mathvariant="normal">A</mi><msup><mi mathvariant="normal">g</mi>'
            "<mo>+</mo></msup><mo>(</mo><mi>a</mi><mi>q</mi><mo>)</mo>",
            "2 A g plus aqueous",
        ),
        # the cell notation bars
        ("<mtext>Pt</mtext><mo>│</mo><mtext>Cu</mtext>", "vertical bar"),
    ],
)
def test_openstax_shaped_mathml_is_read_the_same_way(body, speech):
    said = mathml_to_speech(MATHML.format(body))
    assert speech in said


@pytest.mark.parametrize(
    ("latex", "speech"),
    [
        (r"\mathrm{H}", "capital H"),  # one upright letter is not chemistry
        (r"\mathrm{Hz}", "capital H z"),
        (r"e^{-kt}", "e to the power minus k t end power"),
        (r"x^2 + 1", "x squared plus 1"),
        (r"[x]^2", "open bracket x close bracket squared"),
        (r"[A]_0", "open bracket capital A close bracket sub 0"),
        (r"2^{3+}", "2 to the power 3 plus"),
        (r"5\ \mathrm{m/s}", "5 meters per second"),
        (r"a \to b", "a right arrow b"),
        (r"{}^{14}_{6}\mathrm{C}", "carbon 14, atomic number 6"),
    ],
)
def test_what_is_not_chemistry_reads_as_before(latex, speech):
    assert say(latex) == speech
