# ruff: noqa: RUF001
"""Two layout fixes found on the development set (pipeline 0.5.x), with single pages of dev-v2 as
fixtures (tests/fixtures/layout_dev: Elementary Algebra 5.2, OpenStax CC BY 4.0, and the authored
LaTeX chemistry document, MIT):

- a braced system whose opening brace is the last glyph of the sentence's row ("Solve the system
  by substitution. {") with the equations in rows of their own to its right: the brace goes with
  the system, so the crop shows it (it was cut off, and the equation step wrote no brace);
- nuclide notation in prose, the mass number raised and the atomic number lowered before the
  symbol (14C, 235U, 4 2 He): one inline run, so it is read as an equation (it was raised text).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from readablestem.core.types import Kind
from readablestem.pipeline import layout

FIXTURES = Path(__file__).parent / "fixtures" / "layout_dev"


def elements(name: str):
    return layout.analyze((FIXTURES / f"{name}.pdf").read_bytes()).elements


def equations(name: str, display: str):
    return [e for e in elements(name) if e.kind == Kind.EQUATION and e.display == display]


# ------------------------------------------------------------------------------ the brace


def test_a_stray_brace_at_the_end_of_the_sentence_goes_with_the_system_to_its_right():
    found = elements("elementary-algebra-5-2-brace")
    sentences = [
        e for e in found if e.kind == Kind.PARAGRAPH and "Solve the system" in (e.text or "")
    ]
    systems = [e for e in found if e.kind == Kind.EQUATION and e.display == "block" and e.text]
    assert len(sentences) == 2 and "{" not in "".join(s.text or "" for s in sentences)
    second = next(e for e in systems if "4𝑦" in (e.text or "") and "−10" in (e.text or ""))
    assert (second.text or "").startswith("{")
    brace_x = 190.0  # the glyph sits at x = 190..193; the crop's left edge now holds it
    assert second.bbox[0] <= brace_x + 0.5
    # the sentence's box ends before the brace
    assert sentences[1].bbox[2] <= brace_x + 0.5


def test_a_brace_that_already_belongs_to_its_rows_is_as_it_was():
    found = elements("elementary-algebra-5-2-brace")
    first = next(e for e in found if e.kind == Kind.EQUATION and "−3𝑥−6" in (e.text or ""))
    assert (first.text or "").startswith("{") and first.bbox[0] <= 190.5


def test_a_set_with_a_brace_at_the_end_of_a_sentence_is_not_taken_for_a_system():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Consider the set {", fontsize=11)
    page.insert_text(
        (72, 140), "A long paragraph follows here that has nothing to do with it.", fontsize=11
    )
    found = layout.analyze(doc.tobytes()).elements
    assert not any(e.kind == Kind.EQUATION and e.display == "block" for e in found)
    assert any("Consider the set {" in (e.text or "") for e in found if e.kind == Kind.PARAGRAPH)


# --------------------------------------------------------------------------------- nuclides


def test_nuclides_before_their_symbols_are_inline_equations():
    inline = [e.text for e in equations("latex-chemistry-nuclides", "inline")]
    for symbol in ("C", "U", "Co", "I", "Fe", "O"):
        assert any(t and t.replace(" ", "").endswith(symbol) and t[0].isdigit() for t in inline), (
            symbol
        )
    found = elements("latex-chemistry-nuclides")
    paragraph = next(
        e for e in found if e.kind == Kind.PARAGRAPH and "In nuclear notation" in (e.text or "")
    )
    assert (paragraph.text or "").count("⟦") == 6  # six inline equations in the paragraph
    assert not paragraph.script_runs  # the numbers are no longer raised text


def test_a_mass_number_raised_before_the_symbol_is_a_nuclide_in_running_text():
    texts = [e.text for e in equations("latex-chemistry-nuclides-prose", "inline")]
    for expected in ("14C", "60Co", "131I", "137Cs", "226Ra", "210Po", "238U", "239U"):
        assert expected in texts


def prose_with_prefix(numbers: str, symbol: str) -> list:
    """ "The nucleus <numbers raised><symbol> decays here." drawn with a raised small number."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The nucleus", fontsize=11)
    x = 72 + pymupdf.get_text_length("The nucleus ", fontsize=11)
    page.insert_text((x, 96), numbers, fontsize=7.5)
    x += pymupdf.get_text_length(numbers, fontsize=7.5)
    page.insert_text((x, 100), symbol, fontsize=11)
    x += pymupdf.get_text_length(symbol + " ", fontsize=11)
    page.insert_text((x, 100), "decays here.", fontsize=11)
    return layout.analyze(doc.tobytes()).elements


def test_a_raised_mass_number_before_an_element_symbol_is_one_run():
    found = prose_with_prefix("235", "U")
    assert [e.text for e in found if e.kind == Kind.EQUATION] == ["235U"]


def test_a_raised_number_before_a_capital_that_is_no_element_is_not_a_nuclide():
    found = prose_with_prefix("235", "Q")
    assert not [e for e in found if e.kind == Kind.EQUATION]


def test_a_raised_number_before_a_longer_word_is_not_a_nuclide():
    found = prose_with_prefix("235", "Uranium")
    assert not [e for e in found if e.kind == Kind.EQUATION]
