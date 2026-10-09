"""Letter styles that live in the font, not the text layer (layout `_LETTER_STYLE_FONTS`).

pdfTeX draws \\mathbb{R} with MSBM's "R", \\mathcal{L} with CMSY's "L", \\mathscr{H}
with RSFS's "H" and \\mathfrak{g} with EUFM's "g": the text layer has plain letters. The
layout step writes them as Unicode maths letters (ℝ, ℒ, ℋ, 𝔤), so the equation step
can tell the reals read as a variable R (meaning-level) from a right answer, which it
used to call a cosmetic difference.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from latex2mathml.converter import convert

from a11y_stem.core.types import Kind
from a11y_stem.pipeline.equation_step import text_layer_style
from a11y_stem.pipeline.layout import _Char, analyze

PDF = Path(__file__).parent / "fixtures" / "layout" / "latex" / "letter_styles.pdf"
RIGHT = (
    r"f:\mathbb{R}^n \to \mathbb{C}, \quad \mathcal{L} = T - V, \quad "
    r"\mathscr{H} \in \mathfrak{g}, \quad \mathbf{F} = m\mathbf{a}, \quad R = 5"
)


@pytest.fixture(scope="module")
def display():
    elements = analyze(PDF.read_bytes()).elements
    (equation,) = [e for e in elements if e.kind is Kind.EQUATION and e.display == "block"]
    return equation


def test_the_text_layer_keeps_the_font_styles(display):
    assert "ℝ" in display.text and "ℂ" in display.text
    assert "ℒ" in display.text  # CMSY capital: \mathcal
    assert "ℋ" in display.text  # RSFS: \mathscr
    assert "𝔤" in display.text  # EUFM, lower case too
    assert "𝐅" in display.text and "𝐚" in display.text  # bold, as before
    # The variable R (CMMI) stays a plain letter known to be plain.
    assert display.text.count("R") == 1
    assert "R" in (display.regular_letters or "")


@pytest.mark.parametrize(
    ("latex", "verdict"),
    [
        (RIGHT, "match"),
        (RIGHT.replace(r"\mathbb{R}", "R"), "meaning"),  # the reals read as a variable
        (RIGHT.replace(r"\mathcal{L}", "L"), "meaning"),  # a Lagrangian read as L
        (RIGHT.replace(r"\mathfrak{g}", "g"), "meaning"),
        (RIGHT.replace(r"\mathscr{H}", "H"), "meaning"),
        (RIGHT.replace("R = 5", r"\mathbb{R} = 5"), "meaning"),  # a variable read as the reals
        # \mathcal and \mathscr are one Unicode style (script): the same symbol
        (RIGHT.replace(r"\mathscr{H}", r"\mathcal{H}"), "match"),
    ],
    ids=["right", "reals-lost", "cal-lost", "frak-lost", "scr-lost", "var-as-reals", "scr-as-cal"],
)
def test_style_verdicts_on_the_real_text_layer(display, latex, verdict):
    got = text_layer_style(display.text, convert(latex), regular_letters=display.regular_letters)
    assert got == verdict


def test_only_letters_take_the_font_style():
    def char(c: str, font: str) -> _Char:
        return _Char(c, 0, 0, 1, 1, 1, 10, font, 0)

    assert char("R", "ABCDEF+MSBM10").styled == "ℝ"
    assert char("k", "MSBM10").styled == "k"  # MSBM's capitals only (\Bbbk is not "k")
    assert char("L", "CMSY10").styled == "ℒ"
    assert char("→", "CMSY10").styled == "→"  # CMSY's symbols are untouched
    assert char("x", "CMSY10").styled == "x"
    assert char("g", "EUFM10").styled == "𝔤"
    assert char("G", "EUFB10").styled == "𝔊"
    assert char("R", "CMMI10").styled == "R"
    assert char("R", "CMBX10").styled == "𝐑"
