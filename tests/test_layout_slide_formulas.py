"""A formula set large or bold on a slide is an equation, not a heading (T1-14).

A slide shows its main formula in a large font, alone on a line, and a heading rule that
only looked at size and weight took "PE = mgh" for a title. A heading candidate that is
mostly maths goes to the equation step instead; real titles stay headings. The pages are
made here with PyMuPDF and base-14 fonts, 16:9 like a slide.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout
from readablestem.pipeline.layout import analyze


def slide(lines: list[tuple[str, float, str]]) -> list[Element]:
    """A slide with a title, two lines of body text, then the given lines (text, size, font)."""
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=540)
    page.insert_text((60, 70), "Kinetic energy", fontsize=36, fontname="hebo")
    page.insert_text((60, 130), "The work done on a body equals its change in energy.", fontsize=18)
    page.insert_text(
        (60, 170), "Remember the definition of work for a constant force.", fontsize=18
    )
    y = 240.0
    for text, size, font in lines:
        page.insert_text((60, y), text, fontsize=size, fontname=font)
        y += 60
    return analyze(doc.tobytes()).elements


def by_text(elements: list[Element], text: str) -> Element:
    return next(e for e in elements if (e.text or "").replace(" ", "") == text.replace(" ", ""))


def test_a_bold_formula_is_an_equation_and_the_title_stays_a_heading():
    elements = slide([("PE = mgh", 34, "hebo")])
    assert elements[0].kind == Kind.HEADING and elements[0].text == "Kinetic energy"
    formula = by_text(elements, "PE = mgh")
    assert formula.kind == Kind.EQUATION and formula.display == "block"
    assert "heading candidate that is mostly maths" in formula.reasons
    # A line set bold as a whole says nothing about vectors: no bold letters in its text layer.
    assert formula.text == "PE = mgh"


@pytest.mark.parametrize(
    "text",
    ["PE = mgh", "KE = m v", "F = m a", "T = 2 p r", "p = m v", "A = B"],
)
def test_a_large_formula_is_never_a_heading(text):
    elements = slide([(text, 34, "helv")])
    formula = by_text(elements, text)
    assert formula.kind == Kind.EQUATION
    assert [e.text for e in elements if e.kind == Kind.HEADING] == ["Kinetic energy"]


@pytest.mark.parametrize("text", ["Summary of results", "2.3 Units", "Newton's second law", "Q&A"])
def test_a_real_title_stays_a_heading(text):
    elements = slide([(text, 34, "hebo")])
    assert by_text(elements, text).kind == Kind.HEADING


def test_a_title_that_mentions_a_formula_in_words_stays_a_heading():
    elements = slide([("Newton's law: F = ma", 34, "hebo")])
    assert by_text(elements, "Newton's law: F = ma").kind == Kind.HEADING


def test_mostly_maths_needs_a_relation_and_no_long_word():
    def rows(text: str, font: str = "Helvetica") -> list[layout._Row]:
        x, chars = 60.0, []
        for ch in text:
            width = pymupdf.get_text_length(ch, fontname="helv", fontsize=30)
            chars.append(layout._Char(ch, x, 100, x + width, 130, 125, 30.0, font, 0))
            x += width
        return [layout._Row(chars)]

    assert layout._mostly_maths(rows("PE = mgh"))
    assert layout._mostly_maths(rows("x = 5 + 2"))
    assert not layout._mostly_maths(rows("2.3 + 5"))  # no relation sign
    assert not layout._mostly_maths(rows("Speed = distance / time"))  # a long word
    assert not layout._mostly_maths(rows("Summary"))
