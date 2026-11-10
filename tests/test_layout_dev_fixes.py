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


# --------------------------------------------------------------------------------- nuclides


def test_a_mass_number_raised_before_the_symbol_is_a_nuclide_in_running_text():
    texts = [e.text for e in equations("latex-chemistry-nuclides-prose", "inline")]
    for expected in ("14C", "60Co", "131I", "137Cs", "226Ra", "210Po", "238U", "239U"):
        assert expected in texts
