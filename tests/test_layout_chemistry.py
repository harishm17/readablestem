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


# ------------------------------------------------------------------------- negatives


# ------------------------------------------------- merged lists, half-reactions, labels (C8)

NOTO_MATH = Path("/usr/share/fonts/truetype/noto/NotoSansMath-Regular.ttf")
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
