"""The MathML shape read by the two screen-reader engines (docs/chemistry.md, "MathML shape";
docs/mathml-alttext.md). Needs the engines of `scripts/speech_oracles/build.sh`; skipped without.

What these pin: MathCAT recognises the shape as chemistry and says the state words, labelled
arrows no longer make it fail, and the degree sign and the overline are read by Speech Rule
Engine as degrees and a bar. The pure-tree rules are in `tests/test_chem_mathml.py` and
`tests/test_mathml_shape.py`.
"""

from __future__ import annotations

import pytest

from readablestem.eval import speech_oracle as so
from readablestem.pipeline.chem import ce_to_latex
from readablestem.pipeline.validate import safe_latex_to_mathml

needs_mathcat = pytest.mark.skipif(
    "mathcat" in so.missing_engines(), reason=f"MathCAT not built: run {so.BUILD_SCRIPT}"
)
needs_sre = pytest.mark.skipif(
    "sre" in so.missing_engines(), reason=f"SRE not installed: run {so.BUILD_SCRIPT}"
)

CLEAR = "mathcat:ClearSpeak"

# Correct chemistry as the equation step writes it (`\ce`), formulas, ions, states, reactions and
# nuclides MathCAT has a rule for. Each is a whole expression of chemistry, so it carries `intent`.
CORRECT = [
    "H2O",
    "CO2",
    "C6H12O6",
    "Ca(OH)2",
    "Al2(SO4)3",
    "NaCl(s)",
    "NaCl(aq)",
    "H2O(l)",
    "CO2(g)",
    "Na+",
    "Cl-",
    "OH-",
    "H3O+",
    "NH4+",
    "SO4^2-",
    "Fe^3+",
    "[Cu(NH3)4]^2+",
    "CuSO4.5H2O",
    "^{14}_{6}C",
    "^{235}_{92}U",
    "2 H2 + O2 -> 2 H2O",
    "H2 -> O2",
    "N2(g) + 3 H2(g) <=> 2 NH3(g)",
    "HCl(aq) + NaOH(aq) -> NaCl(aq) + H2O(l)",
    "Na+(aq) + Cl-(aq) -> NaCl(s)",
    "CaCO3(s) ->[\\Delta] CaO(s) + CO2(g)",
    "Fe^3+ + e- -> Fe^2+",
    "Ag+(aq) + Cl-(aq) -> AgCl(s)",
    "2 Na(s) + Cl2(g) -> 2 NaCl(s)",
    "CH4(g) + 2 O2(g) -> CO2(g) + 2 H2O(l)",
    "H2O(l) + H2O(l) <=> H3O+(aq) + OH-(aq)",
    "^{14}_{6}C -> ^{14}_{7}N + e-",
    "^{238}_{92}U -> ^{234}_{90}Th + ^{4}_{2}He",
    "Zn(s) + Cu^2+(aq) -> Zn^2+(aq) + Cu(s)",
    "Pb^2+(aq) + 2 I-(aq) -> PbI2(s)",
    "NH3(g) + HCl(g) -> NH4Cl(s)",
]


def reading(latex: str, engines: list[str]) -> so.Reading:
    mathml = safe_latex_to_mathml(latex, "inline")
    return so.read_all([mathml], engines)[mathml]


@needs_mathcat
def test_mathcat_recognises_the_shape_of_correct_chemistry_as_chemistry() -> None:
    mathml = [safe_latex_to_mathml(ce_to_latex(source), "inline") for source in CORRECT]
    readings = so.read_all(mathml, [CLEAR])
    missed = [s for s, m in zip(CORRECT, mathml, strict=True) if not readings[m].chemistry]
    assert len(missed) / len(CORRECT) <= 0.05, missed
    errors = [s for s, m in zip(CORRECT, mathml, strict=True) if readings[m].errors]
    assert errors == []


@needs_mathcat
def test_mathcat_reads_the_degree_sign_and_a_ring_operator_each_as_itself() -> None:
    assert "degrees" in reading(r"\theta = 30^\circ", [CLEAR]).speech[CLEAR]
    assert "composed" in reading(r"f \circ g", [CLEAR]).speech[CLEAR]


@needs_mathcat
@needs_sre
@pytest.mark.parametrize(
    ("latex", "mathcat_words", "sre_words"),
    [
        (
            r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}",
            ["2 by 2 matrix", "row 1"],
            ["2 By 2 Matrix"],
        ),
        (r"\begin{vmatrix} a & b \\ c & d \end{vmatrix}", ["determinant"], ["Determinant"]),
        (r"\begin{pmatrix} x \\ y \end{pmatrix}", ["column matrix"], ["Matrix"]),
        (r"f(x) = \begin{cases} x & x > 0 \\ -x & x \le 0 \end{cases}", ["2 cases"], ["Layout"]),
        (r"\lim_{x \to 0} \frac{\sin x}{x}", ["limit as x approaches 0"], ["limit"]),
        (r"\sum_{i=1}^{n} i", ["sum from"], ["summation"]),
    ],
)
def test_tables_and_limits_are_read_as_rows_columns_and_limits(
    latex: str, mathcat_words: list[str], sre_words: list[str]
) -> None:
    got = reading(latex, [CLEAR, "sre:mathspeak"])
    assert all(w in got.speech[CLEAR] for w in mathcat_words), got.speech[CLEAR]
    assert all(w in got.speech["sre:mathspeak"] for w in sre_words), got.speech["sre:mathspeak"]
