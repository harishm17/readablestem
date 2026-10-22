"""Chemistry in running text and on display lines, through `layout.analyze` (docs/chemistry.md):
formulas, ions and reactions are inline or display equations; units, variables and words
are not. Pages are built glyph by glyph (the sizes and offsets are the ones the test says);
the committed development page of the authored chemistry document covers the display lines."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout

REPO = Path(__file__).resolve().parents[1]
BODY = 11.0
SMALL = 7.0
UP, DOWN = 3.5, -1.8
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

Piece = tuple[str, str | None]


def build(lines: list[list[Piece]], *, font: str = "helv") -> bytes:
    """Each line is a list of (text, kind); kind None is body text, "sup" or "sub" a
    script, "italic" an italic body-size piece."""
    doc = pymupdf.open()
    page = doc.new_page()
    face = None
    if font != "helv":
        page.insert_font(fontname=font, fontfile=str(DEJAVU))
        face = pymupdf.Font(fontfile=str(DEJAVU))
    y = 100.0
    for pieces in lines:
        x = 72.0
        for text, kind in pieces:
            script = kind in ("sup", "sub")
            size = SMALL if script else BODY
            rise = {"sup": UP, "sub": DOWN}.get(kind or "", 0.0)
            name = "heit" if kind == "italic" else font
            page.insert_text((x, y - rise), text, fontsize=size, fontname=name)
            if face is not None and name == font:
                x += face.text_length(text, fontsize=size)
            else:
                x += pymupdf.get_text_length(text, fontname=name, fontsize=size)
        y += 16.0
    data = doc.tobytes()
    doc.close()
    return data


def equations(pdf: bytes, **kw) -> list[Element]:
    return [e for e in layout.analyze(pdf, **kw).elements if e.kind is Kind.EQUATION]


def texts(pdf: bytes) -> list[str]:
    return [e.text or "" for e in equations(pdf)]


# ------------------------------------------------------------------------- positives


def test_formulas_with_lowered_counts_in_a_sentence_are_inline_equations():
    pdf = build(
        [
            [
                ("Water is H", None),
                ("2", "sub"),
                ("O and glucose is C", None),
                ("6", "sub"),
                ("H", None),
                ("12", "sub"),
                ("O", None),
                ("6", "sub"),
                (".", None),
            ]
        ]
    )
    assert texts(pdf) == ["H2O", "C6H12O6"]
    assert {e.display for e in equations(pdf)} == {"inline"}
    assert all(e.reasons == [layout.FORMULA_REASON] for e in equations(pdf))


def test_ions_with_raised_charges_are_inline_equations():
    pdf = build(
        [
            [
                ("The ions Na", None),
                ("+", "sup"),
                (", Ca", None),
                ("2+", "sup"),
                (" and Fe", None),
                ("3+", "sup"),
                (" are common.", None),
            ],
            [
                ("The acetate ion CH", None),
                ("3", "sub"),
                ("CO", None),
                ("2", "sub"),
                ("-", "sup"),
                (" is a base.", None),
            ],
        ]
    )
    assert texts(pdf) == ["Na+", "Ca2+", "Fe3+", "CH3CO2-"]


def test_a_formula_in_brackets_is_a_concentration_run_with_its_brackets():
    pdf = build(
        [[("Here [H", None), ("3", "sub"), ("O", None), ("+", "sup"), ("] is large.", None)]]
    )
    assert texts(pdf) == ["[H3O+]"]


def test_a_state_symbol_goes_with_its_formula():
    pdf = build([[("We mix NaCl(aq) with Cu", None), ("2+", "sup"), ("(aq) in a beaker.", None)]])
    assert texts(pdf) == ["NaCl(aq)", "Cu2+(aq)"]  # a state symbol is evidence by itself


def test_the_text_after_a_formula_is_separated_from_it():
    # TeX sets no space glyph after a script: the gap is there, the character is not.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "releases Cu", fontsize=BODY, fontname="helv")
    x = 72 + pymupdf.get_text_length("releases Cu", fontname="helv", fontsize=BODY)
    page.insert_text((x, 100 - UP), "2+", fontsize=SMALL, fontname="helv")
    x += pymupdf.get_text_length("2+", fontname="helv", fontsize=SMALL) + 4
    page.insert_text((x, 100), "ions here.", fontsize=BODY, fontname="helv")
    elements = layout.analyze(doc.tobytes()).elements
    (paragraph,) = [e for e in elements if e.kind is Kind.PARAGRAPH]
    assert paragraph.text == "releases ⟦p1-e1⟧ ions here."


@pytest.mark.skipif(not DEJAVU.exists(), reason="needs DejaVu Sans for the arrow glyphs")
def test_a_reaction_on_its_own_line_is_a_display_equation():
    pdf = build(
        [
            [("When two gases react the mass of the products equals the mass of the", None)],
            [("reactants, and the net change is written as one balanced line below:", None)],
            [],
            [],
            [
                ("2 H", None),
                ("2", "sub"),
                ("(g) + O", None),
                ("2", "sub"),
                ("(g) → 2 H", None),
                ("2", "sub"),
                ("O(l)", None),
            ],
        ],
        font="dv",
    )
    found = equations(pdf)
    assert [(e.display, e.text) for e in found] == [("block", "2 H2(g) + O2(g) → 2 H2O(l)")]


@pytest.mark.skipif(not DEJAVU.exists(), reason="needs DejaVu Sans for the arrow glyphs")
def test_a_reaction_in_the_middle_of_a_sentence_is_one_inline_equation():
    pdf = build(
        [
            [
                ("Then Mg(s) + 2 HCl(aq) → MgCl", None),
                ("2", "sub"),
                ("(aq) + H", None),
                ("2", "sub"),
                ("(g) occurs in the flask.", None),
            ]
        ],
        font="dv",
    )
    assert [(e.display, e.text) for e in equations(pdf)] == [
        ("inline", "Mg(s) + 2 HCl(aq) → MgCl2(aq) + H2(g)")
    ]


def test_the_authored_chemistry_page_has_every_net_ionic_equation_as_a_display_line():
    pdf = (REPO / "data/dev-v2/dev/authored/latex-chemistry/latex-chemistry.pdf").read_bytes()
    found = equations(pdf, pages=[1])
    blocks = [e.text or "" for e in found if e.display == "block"]
    assert "Ag+(aq) + Cl−(aq) −→AgCl(s)" in blocks
    assert "Pb2+(aq) + 2 I−(aq) −→PbI2(s)" in blocks
    assert "Ba2+(aq) + SO42− (aq) −→BaSO4(s)" in blocks
    assert len(blocks) == 13  # nine net ionic equations and four acid-base ones
    # No lone arrow is left as an inline equation of its own.
    assert not [e for e in found if e.display == "inline" and (e.text or "").strip("−→ ") == ""]


def test_the_authored_chemistry_page_has_its_prose_formulas_as_inline_equations():
    pdf = (REPO / "data/dev-v2/dev/authored/latex-chemistry/latex-chemistry.pdf").read_bytes()
    found = [e.text or "" for e in equations(pdf, pages=[0]) if e.display == "inline"]
    for expected in ["H2O", "Ca(OH)2", "C6H12O6", "SO42−", "Fe3+", "NH4+", "Al2(SO4)3"]:
        assert expected in found


def test_a_reaction_with_a_label_over_its_arrow_is_one_display_line():
    """TeX sets `\\xrightarrow{\\Delta}` with the label raised over the arrow: the text layer
    has the line end at the label and a second line start at the arrow, which overlap. The
    reaction was cut in three (`CaCO3(s) ∆`, `−→Na2CO3(s) + ...`)."""
    pdf = (REPO / "data/dev-v2/dev/authored/latex-chemistry/latex-chemistry.pdf").read_bytes()
    blocks = [e.text or "" for e in equations(pdf, pages=[3]) if e.display == "block"]
    heated = [t for t in blocks if "∆" in t or "Δ" in t]
    assert len(heated) == 3  # CaCO3, KClO3 and NaHCO3
    for text in heated:
        assert text.count("→") == 1
    assert any(t.startswith("CaCO3(s)") and t.endswith("CaO(s) + CO2(g)") for t in heated)
    assert any(t.startswith("2 NaHCO3(s)") and t.endswith("CO2(g)") for t in heated)
    # No reaction is left in pieces: no inline equation is the label or the start of the arrow
    pieces = [e for e in equations(pdf, pages=[3]) if e.display == "inline"]
    assert not [e for e in pieces if (e.text or "").strip().endswith(("∆", "Δ"))]


# ------------------------------------------------------------------------- negatives


def test_units_with_exponents_stay_raised_text():
    pdf = build(
        [
            [
                ("A speed of 3 m s", None),
                ("-1", "sup"),
                (" and a density of 19 g/cm", None),
                ("3", "sup"),
            ]
        ]
    )
    elements = layout.analyze(pdf).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH]
    assert [r.kind for r in elements[0].script_runs] == ["sup", "sup"]


def test_an_exponent_on_kelvin_or_a_footnote_mark_is_not_an_ion():
    pdf = build(
        [
            [("The heat capacity is 4.18 J K", None), ("−1", "sup"), (" per gram.", None)],
            [("This was reported in Nature", None), ("1", "sup"), (" last year.", None)],
        ]
    )
    assert equations(pdf) == []


def test_an_equilibrium_constant_with_a_letter_subscript_is_not_a_formula():
    pdf = build([[("The value of K", None), ("w", "sub"), (" at 25 degrees is small.", None)]])
    assert equations(pdf) == []


def test_formulas_set_at_one_size_are_left_alone():
    # No scripts: "H2O" in the text layer cannot be told from a word, so nothing is marked.
    pdf = build([[("Water is H2O, the gas is CO2 and the salt is NaCl in this sentence.", None)]])
    assert equations(pdf) == []


def test_ordinary_prose_with_an_arrow_and_a_variable_is_not_chemistry():
    pdf = build(
        [[("As we go from the left to the right the curve rises quickly to the top.", None)]]
    )
    assert equations(pdf) == []


def test_a_variable_with_a_subscript_is_not_a_formula():
    pdf = build(
        [[("The distance ", None), ("x", "italic"), ("2", "sub"), (" is small enough.", None)]]
    )
    found = [e for e in layout.analyze(pdf).elements if e.kind is Kind.EQUATION]
    assert [e.reasons for e in found if layout.FORMULA_REASON in e.reasons] == []
