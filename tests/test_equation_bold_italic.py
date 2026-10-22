"""`\\bm`, `\\boldsymbol` and `\\mathbf` look alike in a crop and every reading says `\\mathbf`.

The real-world development set has 62 equations in three papers (CMMIB fonts) where the author's
`\\bm x_t` was read as `\\mathbf{x}_t`, 2.6% of its gold. The face is in the PDF's font name."""

from __future__ import annotations

from readablestem.core.mathchars import char_variant
from readablestem.pipeline.equation_step import restyle_bold_italic
from readablestem.pipeline.layout import _Char

BI_X = "\U0001d499"  # MATHEMATICAL BOLD ITALIC SMALL X
B_Y = "\U0001d432"  # MATHEMATICAL BOLD SMALL Y


def glyph(c: str, font: str, flags: int = 0) -> _Char:
    return _Char(c, 0, 0, 5, 10, 8, 10, font, flags)


def test_a_cmmib_letter_is_a_bold_italic_code_point_in_the_text_layer():
    styled = glyph("x", "ABCDEF+CMMIB10").styled
    assert styled == BI_X and char_variant(styled) == "bold-italic"


def test_a_cmbx_letter_stays_a_bold_code_point():
    styled = glyph("y", "ABCDEF+CMBX10").styled
    assert char_variant(styled) == "bold"


def test_an_italic_text_font_bold_italic_letter_is_bold_italic():
    assert char_variant(glyph("x", "NimbusRomNo9L-MediItal").styled) == "bold-italic"


def test_mathbf_becomes_boldsymbol_for_a_bold_italic_letter():
    assert restyle_bold_italic(r"\mathbf{x}_{t}", f"{BI_X}t") == r"\boldsymbol{x}_{t}"
    assert restyle_bold_italic(r"f(\mathbf{x})", f"f({BI_X})") == r"f(\boldsymbol{x})"


def test_mathbf_stays_for_an_upright_bold_letter_or_without_evidence():
    assert restyle_bold_italic(r"\mathbf{y}", f"{B_Y}") == r"\mathbf{y}"
    assert restyle_bold_italic(r"\mathbf{x}", "x") == r"\mathbf{x}"
    assert restyle_bold_italic(r"\mathbf{x}", None) == r"\mathbf{x}"


def test_a_letter_drawn_in_both_faces_is_left_as_read():
    assert restyle_bold_italic(r"\mathbf{x}", f"{BI_X} \U0001d431") == r"\mathbf{x}"


def test_other_letters_are_not_touched():
    assert restyle_bold_italic(r"\mathbf{x} + \mathbf{y}", f"{BI_X}+{B_Y}") == (
        r"\boldsymbol{x} + \mathbf{y}"
    )
