# ruff: noqa: RUF001, E501
"""A chemistry reading's species against the PDF's text layer (`pipeline.chem_check`):
what it catches (a neighbour word taken into a formula, a reaction cut mid-way, a subscript
read as a coefficient, a flipped charge sign, a state, an arrow, an element), what it leaves
alone (right readings, however the text layer breaks a formula, and everything that is not
chemistry), and its place in the equation step and a reviewer's edit."""

from __future__ import annotations

import pytest

from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import Element, EquationFix, Fix, Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import chem_check as cc
from readablestem.pipeline.equation_step import transcribe
from readablestem.pipeline.validate import safe_latex_to_mathml
from readablestem.web import compare, edit_check
from test_steps_support import equation_element, make_settings, use_fallback_router

SETTINGS = make_settings()


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


def ml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "block")


def kinds(text: str, latex: str) -> tuple[str, ...] | None:
    """None: the check does not apply; () : it agrees; else the kinds of difference."""
    diff = cc.compare_species(text, ml(latex))
    return None if diff is None else diff.kinds


AGCL = "Ag+(aq) + Cl−(aq) −→AgCl(s)"
AGCL_TEX = r"\text{Ag}^+\text{(aq)} + \text{Cl}^-\text{(aq)} \longrightarrow \text{AgCl}\text{(s)}"
PBCRO4 = "Pb2+(aq) + CrO4 2−(aq) −→PbCrO4(s)"
PBCRO4_TEX = (
    r"\text{Pb}^{2+}\text{(aq)} + \text{CrO}_4^{2-}\text{(aq)} \longrightarrow "
    r"\text{PbCrO}_4\text{(s)}"
)
WATER = "H2 O(𝑔) −→H2 O(𝑙)"
WATER_TEX = r"\text{H}_2\text{O}\text{(g)} \longrightarrow \text{H}_2\text{O}\text{(l)}"


# ------------------------------------------------------------------ right readings


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        (AGCL, AGCL_TEX),
        (PBCRO4, PBCRO4_TEX),
        (WATER, WATER_TEX),
        # the text layer breaks a formula where the script changes
        (
            "Ba2+(aq) + SO42− (aq) −→BaSO4(s)",
            r"\text{Ba}^{2+}\text{(aq)} + \text{SO}_4^{2-}\text{(aq)} \longrightarrow "
            r"\text{BaSO}_4\text{(s)}",
        ),
        (
            "HCO3 −(𝑎𝑞) + H2 O(𝑙) ⇌CO3 2−(𝑎𝑞) + H3 O+ (𝑎𝑞)",
            r"\text{HCO}_3^-\text{(aq)} + \text{H}_2\text{O}\text{(l)} \rightleftharpoons "
            r"\text{CO}_3^{2-}\text{(aq)} + \text{H}_3\text{O}^+\text{(aq)}",
        ),
        # a coefficient, with and without a space, and a bare formula inside a reaction
        (
            "2H2 (𝑔) + O2 (𝑔) −→2 H2 O(𝑙)",
            r"2\text{H}_2\text{(g)} + \text{O}_2\text{(g)} \rightarrow 2\text{H}_2\text{O}\text{(l)}",
        ),
        (
            "HCl(aq) + NaOH(aq) −→NaCl(aq) + H2 O(l)",
            r"\text{HCl}\text{(aq)} + \text{NaOH}\text{(aq)} \rightarrow "
            r"\text{NaCl}\text{(aq)} + \text{H}_2\text{O}\text{(l)}",
        ),
        # a state with its conditions, and an electron
        (
            "2H+ (𝑎𝑞, 1 𝑀) + 2e−⇌H2 (𝑔, 1 atm)",
            r"2\text{H}^+\text{(aq, 1 M)} + 2\text{e}^- \rightleftharpoons \text{H}_2\text{(g, 1 atm)}",
        ),
        # cell notation: the bars are not part of a species
        (
            "Pt(𝑠) │H2 (𝑔, 1 atm) │H+ (𝑎𝑞, 1 𝑀) ║Cu2+ (𝑎𝑞, 1 𝑀) │Cu(𝑠)",
            r"\text{Pt}\text{(s)} | \text{H}_2\text{(g, 1 atm)} | \text{H}^+\text{(aq, 1 M)} "
            r"\| \text{Cu}^{2+}\text{(aq, 1 M)} | \text{Cu}\text{(s)}",
        ),
        # a concentration, a rate law's variable and exponent
        (
            "𝐾w = [H3 O+ ][OH−] = [H3 O+ ]2 = 1.0 × 10−14",
            r"K_w = [\text{H}_3\text{O}^+][\text{OH}^-] = [\text{H}_3\text{O}^+]^2 = 1.0 \times 10^{-14}",
        ),
        (
            "rate = 𝑘[NO2 ]𝑚[CO]𝑛",
            r"\text{rate} = k[\text{NO}_2]^m[\text{CO}]^n",
        ),
        # a list, with the comma and a label the text layer keeps
        (
            "(a) H2 O(𝑙) ⇌H+ (𝑎𝑞) + OH−(𝑎𝑞); (b) CO3 2−, F−",
            r"\text{(a) }\text{H}_2\text{O}\text{(l)} \rightleftharpoons \text{H}^+\text{(aq)} + "
            r"\text{OH}^-\text{(aq)}; \text{ (b) }\text{CO}_3^{2-}, \text{F}^-",
        ),
    ],
)
def test_a_right_reading_agrees(text, latex):
    assert kinds(text, latex) == ()


def test_the_order_of_species_on_one_side_of_an_arrow_is_not_compared():
    # a fraction's numerator and denominator come out of the text layer in an order of
    # their own
    text = "[H3O+][CH3CO2 −] [H3O+] 2 𝐾a = [CH3CO2H] ≈ [CH3CO2H]0"
    latex = (
        r"K_a = \frac{[\text{H}_3\text{O}^+][\text{CH}_3\text{CO}_2^-]}{[\text{CH}_3\text{CO}_2\text{H}]}"
        r" \approx \frac{[\text{H}_3\text{O}^+]^2}{[\text{CH}_3\text{CO}_2\text{H}]_0}"
    )
    assert kinds(text, latex) == ()


def test_a_charge_the_text_layer_put_before_its_formula_is_not_a_difference():
    assert (
        kinds("−CH3 CO2 + H2 O(l)", r"\text{CH}_3\text{CO}_2^- + \text{H}_2\text{O}\text{(l)}")
        == ()
    )


# ---------------------------------------------------------------------------- errors


def test_a_neighbour_word_swallowed_into_a_formula():
    # CrO4 2− read as PbCrO4 2−: the lead is the next ion's
    latex = (
        r"\text{Pb}^{2+}\text{(aq)} + \text{PbCrO}_4^{2-}\text{(aq)} \longrightarrow "
        r"\text{PbCrO}_4\text{(s)}"
    )
    diff = cc.compare_species(PBCRO4, ml(latex))
    assert diff is not None and diff.kinds == ("elements",)
    assert diff.rows == (("elements", "CrO42−(aq)", "PbCrO42−(aq)"),)


def test_a_neighbour_word_glued_to_a_formula_is_no_longer_a_formula():
    latex = (
        r"\text{Pb}^{2+}\text{(aq)} + \text{the}\text{CrO}_4^{2-}\text{(aq)} \longrightarrow "
        r"\text{PbCrO}_4\text{(s)}"
    )
    assert kinds(PBCRO4, latex) == ("missing",)


def test_a_neighbour_species_taken_into_the_reading():
    diff = cc.compare_species(AGCL, ml(AGCL_TEX + r" + \text{NaNO}_3\text{(aq)}"))
    assert diff is not None and diff.kinds == ("extra",)


def test_a_reaction_cut_mid_way():
    diff = cc.compare_species(WATER, ml(r"\text{H}_2\text{O}\text{(g)}"))
    assert diff is not None
    assert diff.kinds == ("arrow", "missing")
    assert ("arrow", "→", "") in diff.rows


def test_a_reaction_cut_after_its_arrow():
    latex = r"\text{Ag}^+\text{(aq)} + \text{Cl}^-\text{(aq)} \longrightarrow"
    diff = cc.compare_species(AGCL, ml(latex))
    assert diff is not None and "missing" in diff.kinds


def test_a_reaction_that_starts_late():
    latex = r"\text{Cl}^-\text{(aq)} \longrightarrow \text{AgCl}\text{(s)}"
    diff = cc.compare_species(AGCL, ml(latex))
    assert diff is not None and diff.kinds == ("missing",)


def test_a_subscript_read_as_a_coefficient():
    text = "H2 O(𝑙) ⇌H+ (𝑎𝑞) + OH−(𝑎𝑞)"
    latex = (
        r"2\text{HO}\text{(l)} \rightleftharpoons \text{H}^+\text{(aq)} + \text{OH}^-\text{(aq)}"
    )
    diff = cc.compare_species(text, ml(latex))
    assert diff is not None and diff.kinds == ("coefficient",)
    assert diff.rows == (("coefficient", "H2O(l)", "2 HO(l)"),)


def test_a_coefficient_read_as_a_subscript():
    text = "2 H2 O(𝑙) −→2 H2 (𝑔) + O2 (𝑔)"
    latex = r"\text{H}_2\text{O}\text{(l)} \rightarrow 2\text{H}_2\text{(g)} + \text{O}_2\text{(g)}"
    diff = cc.compare_species(text, ml(latex))
    assert diff is not None and diff.kinds == ("coefficient",)


def test_a_flipped_charge_sign():
    latex = AGCL_TEX.replace("^+", "^-", 1)
    diff = cc.compare_species(AGCL, ml(latex))
    assert diff is not None and diff.kinds == ("charge",)
    assert diff.rows == (("charge", "Ag+(aq)", "Ag−(aq)"),)


def test_a_flipped_sign_on_a_charge_with_a_number():
    latex = PBCRO4_TEX.replace("CrO}_4^{2-}", "CrO}_4^{2+}")
    diff = cc.compare_species(PBCRO4, ml(latex))
    assert diff is not None and diff.kinds == ("charge",)


def test_a_dropped_charge():
    latex = AGCL_TEX.replace("^-", "", 1)
    diff = cc.compare_species(AGCL, ml(latex))
    assert diff is not None and diff.kinds == ("charge",)


def test_a_changed_state():
    diff = cc.compare_species(AGCL, ml(AGCL_TEX.replace("(aq)", "(s)", 1)))
    assert diff is not None and diff.kinds == ("state",)


def test_a_dropped_state():
    diff = cc.compare_species(AGCL, ml(AGCL_TEX.replace(r"\text{(s)}", "")))
    assert diff is not None and diff.kinds == ("state",)


def test_a_reaction_arrow_read_as_an_equilibrium_arrow_and_back():
    swapped = cc.compare_species(
        AGCL, ml(AGCL_TEX.replace(r"\longrightarrow", r"\rightleftharpoons"))
    )
    assert swapped is not None and swapped.kinds == ("arrow",)
    text = "HF(aq) ⇌H+ (aq) + F−(aq)"
    latex = r"\text{HF}\text{(aq)} \rightarrow \text{H}^+\text{(aq)} + \text{F}^-\text{(aq)}"
    other = cc.compare_species(text, ml(latex))
    assert other is not None and other.kinds == ("arrow",)


def test_a_reversed_reaction():
    latex = r"\text{AgCl}\text{(s)} \longrightarrow \text{Ag}^+\text{(aq)} + \text{Cl}^-\text{(aq)}"
    assert kinds(AGCL, latex) not in (None, ())


def test_another_element_and_a_moved_count():
    swapped = cc.compare_species(AGCL, ml(AGCL_TEX.replace("Cl", "Br")))
    assert swapped is not None and swapped.kinds == ("elements",)
    text = "H2 O2 (l) −→H2 O(l) + O2 (g)"
    latex = r"\text{H}_2\text{O}\text{(l)} \rightarrow \text{H}_2\text{O}\text{(l)} + \text{O}_2\text{(g)}"
    moved = cc.compare_species(text, ml(latex))
    assert moved is not None and moved.kinds == ("elements",)


# ------------------------------------------------------ where the check stays out


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("x2 + y2 = r2", "x^2+y^2=r^2"),
        ("𝐴+ 𝐵= 8 𝐴+ 3𝐵+ 𝐶= 12", r"A+B=8 \quad A+3B+C=12"),
        # letters of the periodic table in a gas law: T1 and T2 are variables
        ("P1V1 = P2V2 T1 T2", r"\frac{P_1V_1}{T_1}=\frac{P_2V_2}{T_2}"),
        ("𝑣 = 36 m/s", r"v = 36\ \text{m/s}"),
        # one charged symbol alone is not evidence: "B+" is algebra as often as boron
        ("B+", "B^+"),
        ("Na+", r"\text{Na}^+"),
        ("sin2 𝜃+ cos2 𝜃= 1", r"\sin^2\theta+\cos^2\theta=1"),
    ],
)
def test_text_that_is_not_chemistry_is_not_checked(text, latex):
    assert kinds(text, latex) is None


@pytest.mark.parametrize("text", [None, "", "   "])
def test_no_text_layer_is_not_checked(text):
    assert cc.compare_species(text, ml(AGCL_TEX)) is None


def test_a_very_long_text_is_left_alone():
    assert cc.compare_species(" ".join(["H2O(l)"] * 500), ml(AGCL_TEX)) is None


def test_a_reading_the_parser_cannot_use_is_not_a_failure():
    assert cc.compare_species(AGCL, "<math><mrow") is None


def test_an_arrow_with_nothing_after_it_is_not_a_reaction():
    # a caption's arrow: "→ a few ligands ..."
    text = "H2O(l) → a few ligands of the series"
    assert kinds(text, r"\text{H}_2\text{O}\text{(l)}") == ()


def test_italic_letters_are_variables_not_elements():
    # math italic K, w and k are what TeX and Chromium print for variables
    assert [str(t) for t in cc.tokens_of("𝐾w = 𝑘[H3 O+ ]")] == ["H3O+"]


# --------------------------------------------------------------------- the parser


def test_the_flat_mathml_matches_what_a_pdf_prints():
    flat = cc.mathml_flat(ml(PBCRO4_TEX))
    assert [str(t) for t in cc.tokens_of(flat)] == [str(t) for t in cc.tokens_of(PBCRO4)]


def test_species_are_read_from_one_word():
    assert cc.species_of("2Ca3(PO4)2(s)") == cc.Species("2", "Ca3(PO4)2", "s", True)
    assert cc.species_of("[Cu(NH3)4]2+") == cc.Species("", "[Cu(NH3)4]2+", "", True)
    assert cc.species_of("Na[Al(OH)4](aq)") == cc.Species("", "Na[Al(OH)4]", "aq", True)
    assert cc.species_of("HCl") == cc.Species("", "HCl", "", False)
    assert cc.species_of("water") is None
    assert cc.species_of("Xx2") is None


def test_the_doubled_prescripts_of_a_nuclide_keep_the_symbol():
    # the text layer prints a nuclide's mass number apart from its atomic number, so the
    # numbers in front of a bare symbol are not compared
    text = "125 I + 𝑒− ⟶ 125 Te"
    latex = r"{}^{125}_{53}\text{I} + \text{e}^- \longrightarrow {}^{125}_{52}\text{Te}"
    assert kinds(text, latex) in (None, ())


# ------------------------------------------------------------ in the equation step


def out(latex: str, confidence: float = 0.95) -> dict:
    return {"is_equation": True, "latex": latex, "confidence": confidence}


def by_model(**answers) -> FakeVisionClient:
    table = {MAIN_MODEL: answers.get("main"), CHEAP_MODEL: answers.get("cheap")}
    return FakeVisionClient(responders={"equation": lambda req: table[req.model]})


def run(client, element):
    return transcribe(element, b"\x89PNG crop", client=client, settings=SETTINGS)


def test_a_right_reading_is_accepted_and_the_check_passed():
    fix = run(by_model(main=out(AGCL_TEX)), equation_element(text=AGCL))
    assert fix.status == Status.ACCEPTED and fix.review == "none"
    check = next(c for c in fix.checks if c.name == "equation.text_layer_species")
    assert check.passed and check.severity == "warning" and check.code is None


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        (PBCRO4, PBCRO4_TEX.replace(r"\text{CrO}_4^{2-}", r"\text{PbCrO}_4^{2-}")),
        (WATER, r"\text{H}_2\text{O}\text{(g)}"),
        (
            "H2 O(𝑙) ⇌H+ (𝑎𝑞) + OH−(𝑎𝑞)",
            r"2\text{HO}\text{(l)} \rightleftharpoons \text{H}^+\text{(aq)} + \text{OH}^-\text{(aq)}",
        ),
        (AGCL, AGCL_TEX.replace("^+", "^-", 1)),
    ],
    ids=["neighbour", "cut", "subscript", "charge sign"],
)
def test_a_wrong_species_reading_is_a_required_decision(text, latex):
    fix = run(by_model(main=out(latex)), equation_element(text=text))
    assert fix.status == Status.NEEDS_REVIEW and fix.review == "required"
    assert {c.code for c in fix.failed_checks} >= {"species_mismatch"}
    assert fix.equation is not None  # the proposal is kept for the person's decision
    assert fix.confidence == pytest.approx(0.95 * 0.6)


def test_a_species_difference_alone_is_what_fails():
    # the symbol bag of 2HO + ... equals H2O + ...: only the species check sees it
    text = "H2 O(𝑙) ⇌H+ (𝑎𝑞) + OH−(𝑎𝑞)"
    latex = (
        r"2\text{HO}\text{(l)} \rightleftharpoons \text{H}^+\text{(aq)} + \text{OH}^-\text{(aq)}"
    )
    fix = run(by_model(main=out(latex)), equation_element(text=text))
    assert {c.code for c in fix.failed_checks} == {"species_mismatch"}


def test_a_cheap_answer_with_a_wrong_species_is_asked_again():
    short = r"\text{Ag}^+\text{(aq)} + \text{Cl}^-\text{(aq)}"
    client = by_model(cheap=out(short.replace("(aq)", "(s)", 1)), main=out(short))
    element = equation_element(text="Ag+(aq) + Cl−(aq)", display="inline")
    fix = run(client, element)
    assert fix.status == Status.ACCEPTED
    assert [c.model for c in fix.calls] == [CHEAP_MODEL, MAIN_MODEL]


def test_mathematics_gets_no_species_check():
    fix = run(by_model(main=out(r"v = 36\ \text{m/s}")), equation_element(text="𝑣 = 36 m/s"))
    assert all(c.name != "equation.text_layer_species" for c in fix.checks)


def test_a_picture_without_a_text_layer_gets_no_species_check():
    fix = run(by_model(main=out(AGCL_TEX)), equation_element(text=None))
    assert all(c.name != "equation.text_layer_species" for c in fix.checks)


# ------------------------------------------------------------ a reviewer's edit


def edit_case(text: str, stored: str):
    element = Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 10, 10), order=1,
        text=text, font_size=10, display="block",
    )  # fmt: skip
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(latex=stored, mathml=ml(stored), speech=""),
    )
    return element, fix


def test_an_edit_with_a_subscript_read_as_a_coefficient_is_a_meaning_level_difference():
    text = "H2 O(𝑙) ⇌H+ (𝑎𝑞) + OH−(𝑎𝑞)"
    stored = r"\text{H}_2\text{O}\text{(l)} \rightleftharpoons \text{H}^+\text{(aq)} + \text{OH}^-\text{(aq)}"
    element, fix = edit_case(text, stored)
    found = edit_check.check_equation_edit(
        element, fix, stored.replace(r"\text{H}_2", "2 \\text{H}", 1)
    )
    assert {c.code for c in found.failed} == {"species_mismatch"}
    assert found.meaning == ["The PDF has H2O(l) where your edit has 2 HO(l)."]


def test_an_edit_that_keeps_the_species_has_no_difference():
    element, fix = edit_case(AGCL, AGCL_TEX.replace("^+", "^-", 1))
    found = edit_check.check_equation_edit(element, fix, AGCL_TEX)
    assert not found.failed and not found.meaning
    assert "equation.text_layer_species" in {c.name for c in found.checks}


def test_the_compare_table_names_the_species():
    element, fix = edit_case(WATER, WATER_TEX)
    wrong = fix.model_copy(
        update={
            "equation": EquationFix(
                latex=r"\text{H}_2\text{O}\text{(g)}",
                mathml=ml(r"\text{H}_2\text{O}\text{(g)}"),
                speech="",
            )
        }
    )
    result = compare.compare_equation(element, wrong)
    assert result.meaning
    species = [row for row in result.rows if row.what == "Species"]
    assert [(row.pdf, row.proposal) for row in species] == [
        ("→", "not there"),
        ("H2O(l)", "not there"),
    ]
    assert result.said["species"][0] == "The PDF has →, which the proposal does not."


def test_a_table_cell_edit_is_not_given_the_species_check():
    found = edit_check.check_cell_edit(AGCL, AGCL_TEX.replace("^+", "^-", 1))
    assert all(c.name != "equation.text_layer_species" for c in found.checks)
