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


# ------------------------------------------------- merged lists, half-reactions, labels (C8)

NOTO_MATH = Path("/usr/share/fonts/truetype/noto/NotoSansMath-Regular.ttf")
needs_dejavu = pytest.mark.skipif(not DEJAVU.exists(), reason="needs DejaVu Sans for the arrows")
needs_noto = pytest.mark.skipif(not NOTO_MATH.exists(), reason="needs Noto Sans Math")
FONTS = {"dv": DEJAVU, "nm": NOTO_MATH}
SENTENCE = "The next line of this paragraph says what happens in the flask here."


class Page:
    """A page written piece by piece at chosen places: the way TeX leaves a stack or a label."""

    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page = self.doc.new_page()
        self.faces: dict[str, pymupdf.Font] = {}

    def width(self, text: str, size: float, font: str) -> float:
        if font == "helv":
            return pymupdf.get_text_length(text, fontname="helv", fontsize=size)
        if font not in self.faces:
            self.page.insert_font(fontname=font, fontfile=str(FONTS[font]))
            self.faces[font] = pymupdf.Font(fontfile=str(FONTS[font]))
        return self.faces[font].text_length(text, fontsize=size)

    def put(self, x: float, y: float, text: str, size: float = BODY, font: str = "dv") -> float:
        self.width(text, size, font)  # registers the face
        self.page.insert_text((x, y), text, fontsize=size, fontname=font)
        return x + self.width(text, size, font)

    def sentence(self, y: float) -> None:
        self.put(72, y, SENTENCE, BODY, "helv")

    def data(self) -> bytes:
        data = self.doc.tobytes()
        self.doc.close()
        return data


def labelled_reaction(above: str, below: str | None = None, *, arrow_first: bool = False) -> bytes:
    """CaCO3 -label-> CaO + CO2 between two lines of prose, the label set over (and under) a
    long arrow, in the order TeX writes it: the label, then the arrow."""
    pg = Page()
    pg.sentence(150)
    pg.sentence(166)
    y = 200.0
    x = pg.put(72, y, "CaCO")
    x = pg.put(x, y - DOWN, "3", SMALL)
    x = pg.put(x, y, "  ")
    width_above = pg.width(above, SMALL, "dv")
    width_below = pg.width(below, SMALL, "dv") if below else 0.0
    dash, head = pg.width("−", BODY, "dv"), pg.width("→", BODY, "dv")
    dashes = max(1, int((max(width_above, width_below) + 4 - head) / dash + 0.999))
    arrow = "−" * dashes + "→"
    arrow_width = dashes * dash + head

    def labels() -> None:
        pg.put(x + (arrow_width - width_above) / 2, y - 8, above, SMALL)
        if below:
            pg.put(x + (arrow_width - width_below) / 2, y + 9, below, SMALL)

    if arrow_first:
        pg.put(x, y, arrow)
        labels()
    else:
        labels()
        pg.put(x, y, arrow)
    after = pg.put(x + arrow_width + 4, y, "CaO + CO")
    pg.put(after, y - DOWN, "2", SMALL)
    pg.sentence(240)
    return pg.data()


@needs_dejavu
@pytest.mark.parametrize(
    ("above", "below"),
    [
        ("Δ", None),
        ("heat", None),
        ("Pt", None),
        ("high temperature", None),
        ("500 °C, Fe", None),
        ("electrolysis", "600 °C"),
    ],
)
def test_a_label_set_over_an_arrow_keeps_the_reaction_whole(above, below):
    """Any label over (and under) an arrow, of any length and with a long arrow under it, is
    part of the reaction: one display line, not a formula, an arrow and a caption."""
    found = equations(labelled_reaction(above, below))
    assert [e.display for e in found] == ["block"]
    text = found[0].text or ""
    assert text.startswith("CaCO3") and text.endswith("CaO + CO2")
    for word in (above + " " + (below or "")).split():
        assert word in text


@needs_dejavu
def test_the_label_over_an_arrow_also_joins_when_the_arrow_is_written_first():
    found = equations(labelled_reaction("heat", arrow_first=True))
    assert [e.display for e in found] == ["block"]
    assert (found[0].text or "").endswith("CaO + CO2")


@needs_dejavu
def test_the_label_is_read_after_the_arrow_in_words_of_its_own():
    # Not inside the shaft where its position would put it, and not glued to either side.
    (found,) = equations(labelled_reaction("electrolysis", "600 °C"))
    assert "→ electrolysis 600 °C CaO" in (found.text or "")


@needs_dejavu
def test_a_script_beside_an_arrow_is_not_its_label():
    # The charge before an arrow sits at the arrow's height: a script, not a label.
    pg = Page()
    pg.sentence(150)
    pg.sentence(166)
    x = pg.put(72, 200, "Na")
    x = pg.put(x, 200 - UP, "+", SMALL)
    x = pg.put(x + 2, 200, " + Cl")
    x = pg.put(x, 200 - UP, "−", SMALL)
    pg.put(x + 2, 200, " → NaCl")
    pg.sentence(240)
    (found,) = equations(pg.data())
    assert (found.display, found.text) == ("block", "Na+ + Cl− → NaCl")


@needs_noto
def test_a_half_reaction_label_far_from_its_reaction_stays_with_it():
    """`anode (oxidation):` is set apart from `Fe(s) -> Fe2+ + 2e-` by more than the gap that
    joins the pieces of a line; the block was cut after the label."""
    pg = Page()
    pg.sentence(150)
    pg.sentence(166)
    pg.put(72, 200, "anode (oxidation):", BODY, "nm")
    x = pg.put(240, 200, "Fe(s) → Fe", BODY, "nm")
    x = pg.put(x, 200 - UP, "2+", SMALL, "nm")
    pg.put(x, 200, "(aq) + 2e", BODY, "nm")
    pg.sentence(240)
    found = equations(pg.data())
    assert len(found) == 1
    assert (found[0].text or "").startswith("anode (oxidation):")
    assert "Fe(s)" in (found[0].text or "")


@needs_dejavu
def test_a_colon_label_that_names_no_half_reaction_is_not_joined_across_a_gap():
    pg = Page()
    pg.sentence(150)
    pg.sentence(166)
    pg.put(72, 200, "Result:")
    pg.put(240, 200, "A → B")
    pg.sentence(240)
    assert not any("Result:" in (e.text or "") for e in equations(pg.data()))


@needs_noto
def test_the_degree_sign_of_a_standard_potential_does_not_cut_its_label():
    """E°cell: TeX sets the degree over the start of `cell`. Read by position the text layer
    gave `E c°ell`, and the inline equation was cut in two (`E c` and `ell`)."""
    pg = Page()
    pg.put(72, 140, "We relate the work to the potential", BODY, "helv")
    x = pg.put(72, 166, "that gives the value", BODY, "helv")
    x = pg.put(x + 4, 166, "E", BODY, "nm")
    pg.put(x, 166 - DOWN, "cell", SMALL, "nm")
    pg.put(x, 166 - UP - 1.5, "°", SMALL, "helv")
    pg.put(72, 192, "and then the next line carries on with plain words here.", BODY, "helv")
    assert [e.text for e in equations(pg.data())] == ["Ecell°"]


@needs_noto
def test_a_list_of_ions_is_one_equation_per_ion():
    """`CO3 2-, F-` in the running text is separate ions, as the author wrote them."""
    pg = Page()
    pg.put(72, 150, "Which of these are bases among the anions such as", BODY, "helv")
    x = pg.put(72, 166, "OH", BODY, "nm")
    x = pg.put(x, 166 - UP, "−", SMALL, "nm")
    x = pg.put(x, 166, ", CO", BODY, "nm")
    x = pg.put(x, 166 - DOWN, "3", SMALL, "nm")
    x = pg.put(x, 166 - UP, "2−", SMALL, "nm")
    x = pg.put(x, 166, ", F", BODY, "nm")
    x = pg.put(x, 166 - UP, "−", SMALL, "nm")
    pg.put(x + 3, 166, "and the like are all listed", BODY, "helv")
    found = [(e.text or "").replace(" ", "") for e in equations(pg.data())]
    assert found == ["OH−", "CO32−", "F−"]


@needs_noto
def test_a_list_of_reactions_run_together_is_one_equation_per_reaction():
    """`(a) A <=> B; (b) C <=> D` is not one display line: each reaction is an equation of its
    own, without its list marker."""
    pg = Page()
    pg.put(72, 150, "Write the conjugate base and the acid for each of these.", BODY, "helv")
    x = pg.put(72, 166, "(a) HF + OH", BODY, "nm")
    x = pg.put(x, 166 - UP, "−", SMALL, "nm")
    x = pg.put(x, 166, " ⇌ F", BODY, "nm")
    x = pg.put(x, 166 - UP, "−", SMALL, "nm")
    x = pg.put(x, 166, " + H", BODY, "nm")
    x = pg.put(x, 166 - DOWN, "2", SMALL, "nm")
    x = pg.put(x, 166, "O; (b) NH", BODY, "nm")
    x = pg.put(x, 166 - DOWN, "3", SMALL, "nm")
    x = pg.put(x, 166, " + H", BODY, "nm")
    x = pg.put(x, 166 - UP, "+", SMALL, "nm")
    x = pg.put(x, 166, " ⇌ NH", BODY, "nm")
    x = pg.put(x, 166 - DOWN, "4", SMALL, "nm")
    pg.put(x, 166 - UP, "+", SMALL, "nm")
    found = equations(pg.data())
    assert [e.display for e in found] == ["inline", "inline"]
    assert [(e.text or "").replace(" ", "")[:4] for e in found] == ["HF+O", "NH3+"]
    assert all("(a)" not in (e.text or "") and "(b)" not in (e.text or "") for e in found)


@needs_noto
def test_a_comma_list_of_assignments_is_not_cut_into_species():
    # "A = 2, B = 3" has capitals and a script ("A2"), but its pieces are not species.
    pg = Page()
    pg.put(72, 150, "The constants of the problem are given as follows here:", BODY, "helv")
    x = pg.put(72, 166, "A", BODY, "nm")
    x = pg.put(x, 166 - DOWN, "1", SMALL, "nm")
    pg.put(x, 166, " = 2, B = 3", BODY, "nm")
    pg.put(72, 192, "and then the next line carries on with plain words here.", BODY, "helv")
    (found,) = equations(pg.data())
    assert "= 2, B = 3" in (found.text or "")
