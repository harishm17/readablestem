"""Bold letters in running text (FEATURE_NOTATION_MEMORY): a lone bold letter becomes an
inline equation only with evidence; labels, bold words, sentence-initial capitals and
letters without evidence stay text exactly as before (docs/layout.md "Bold letters in
prose"). Measured on the LaTeX fixtures by `eval/bold_letters.py`."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.core.notation import canonical_symbol
from a11y_stem.core.types import ConversionOptions, Element, Kind, NotationEntry
from a11y_stem.eval import bold_letters
from a11y_stem.pipeline import layout, options
from a11y_stem.pipeline.notation_memory import (
    BOLD_LETTER_REASON,
    EVIDENCE_COURSE,
    EVIDENCE_DOCUMENT,
    EVIDENCE_MATHS_FONT,
    EVIDENCE_NEARBY,
)

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex"
BOLD_A = "\U0001d41a"
BOLD_B = "\U0001d41b"
BOLD_D = "\U0001d41d"


def html_pdf(html: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_htmlbox(pymupdf.Rect(72, 72, 540, 500), html)
    data = doc.tobytes()
    doc.close()
    return data


def analyze(pdf: bytes, *, on: bool = True, course: tuple[str, ...] = ()) -> list[Element]:
    notation = [
        NotationEntry(symbol=s, canonical=canonical_symbol(s) or "", meaning="vector")
        for s in course
    ]
    with options.using(ConversionOptions(notation_memory=on, notation=notation)):
        return layout.analyze(pdf).elements


def bold_letters_of(elements: list[Element]) -> list[Element]:
    return [e for e in elements if e.kind is Kind.EQUATION and BOLD_LETTER_REASON in e.reasons]


def text_blocks(elements: list[Element]) -> list[str]:
    return [e.text or "" for e in elements if e.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM)]


VECTORS = (
    "<p>The projection of a vector <b>b</b> onto a nonzero vector <b>a</b> is the multiple "
    "of <b>a</b> closest to <b>b</b>.</p><p>So <b>b</b> = 2 here.</p>"
)
NEGATIVES = (
    "<p><b>a)</b> Find the speed. <b>A</b> car moves. See part <b>(b)</b> and <b>Part A</b> "
    "or <b>Step 1</b>, the <b>x</b>-axis and a grade of <b>B</b> here.</p>"
    "<p><b>This is a bold sentence with a letter a in it.</b></p>"
    "<p>A lone <b>q</b> with nothing else bold about it.</p>"
)


def test_a_lone_bold_letter_with_the_same_letter_in_maths_becomes_maths():
    elements = analyze(html_pdf(VECTORS))
    found = bold_letters_of(elements)
    assert [e.text for e in found] == [BOLD_B, BOLD_A, BOLD_A, BOLD_B]
    by_letter = {e.text: e.reasons[-1] for e in found}
    assert by_letter[BOLD_B] == EVIDENCE_DOCUMENT  # "b = 2" is maths with a bold b
    assert by_letter[BOLD_A] == EVIDENCE_NEARBY  # a small letter beside a bold b
    for element in found:
        assert element.display == "inline" and element.parent_id == "p1-e0"
    paragraph = elements[0]  # the four letters, then "b = 2" (one paragraph here)
    assert paragraph.text is not None and paragraph.text.count("⟦") == 5


def test_labels_bold_words_capitals_and_letters_without_evidence_stay_text():
    pdf = html_pdf(NEGATIVES)
    on, off = analyze(pdf), analyze(pdf, on=False)
    assert bold_letters_of(on) == []
    # Not a character moves: the text is the same as with the feature off.
    assert [(e.kind, e.text) for e in on] == [(e.kind, e.text) for e in off]


def test_with_the_feature_off_nothing_changes():
    pdf = html_pdf(VECTORS)
    off = analyze(pdf, on=False)
    assert bold_letters_of(off) == []
    plain = layout.analyze(pdf).elements  # outside a conversion: the defaults
    assert [(e.id, e.text) for e in off] == [(e.id, e.text) for e in plain]


def test_the_course_notation_is_evidence_on_its_own():
    pdf = html_pdf(NEGATIVES)
    found = bold_letters_of(analyze(pdf, course=("bold q",)))
    assert [(e.text, e.reasons[-1]) for e in found] == [("\U0001d42a", EVIDENCE_COURSE)]


def test_a_sentence_initial_capital_is_maths_only_when_the_course_lists_it():
    pdf = html_pdf("<p>Solve it. <b>F</b> is the net force on the cart.</p>")
    assert bold_letters_of(analyze(pdf)) == []
    found = bold_letters_of(analyze(pdf, course=("bold F",)))
    assert [e.reasons[-1] for e in found] == [EVIDENCE_COURSE]


def test_the_handout_loses_no_bold_vector():
    """MF2: the two-column handout's 10 bold letters in prose, and its bold omega."""
    elements = analyze((LATEX / "linear_algebra_twocol.pdf").read_bytes())
    found = bold_letters_of(elements)
    letters = sorted(e.text or "" for e in found)
    assert letters == sorted(
        [BOLD_A] * 5 + [BOLD_B] * 3 + ["\U0001d42b", "\U0001d431", "\U0001d6da"]
    )  # a x5, b x3, r, x, omega
    evidence = {e.text: e.reasons[-1] for e in found}
    assert evidence["\U0001d6da"] == EVIDENCE_MATHS_FONT  # CMMIB: already maths before
    assert evidence[BOLD_A] == EVIDENCE_DOCUMENT
    headings = [e.text for e in elements if e.kind is Kind.HEADING]
    assert headings and all("⟦" not in (h or "") for h in headings)


@pytest.mark.parametrize("name", sorted(p.stem for p in LATEX.glob("*.tex")))
def test_latex_fixtures_have_no_misses_and_no_false_positives(name):
    tex = (LATEX / f"{name}.tex").read_text(encoding="utf-8")
    result = bold_letters.score(
        name, (LATEX / f"{name}.pdf").read_bytes(), bold_letters.expected_from_latex(tex)
    )
    assert result.missed == 0, result.missed_letters
    assert result.false_positives == 0, result.extra_letters


# --------------------------------------------------------------------------- the measure


def test_expected_letters_from_a_latex_source():
    tex = r"""\begin{document}
    A vector $\mathbf{v}$ and $\bm{\omega}$, but not $\mathbf{v} \cdot \mathbf{u}$.
    % a comment $\mathbf{c}$
    \[ \mathbf{d} \]
    \end{document}"""
    assert bold_letters.expected_from_latex(tex) == {"\U0001d42f": 1, "\U0001d6da": 1}


def test_expected_letters_from_openstax_xhtml():
    xhtml = (
        "<h3>The <strong>d</strong> heading</h3><p>We use <strong>d</strong> for it, "
        "<strong>d</strong><sub>0</sub> first, <strong>v</strong> = 3, and "
        "<b>x</b> or <strong>s</strong>.</p><math><mi>q</mi></math>"
    )
    expected = bold_letters.expected_from_xhtml(xhtml)
    assert expected == {BOLD_D: 1, "\U0001d431": 1, "\U0001d42c": 1}


def test_the_table_reports_totals():
    scores = [
        bold_letters.Score(
            "a", expected=Counter({BOLD_A: 2}), found=Counter({BOLD_A: 1, BOLD_B: 1})
        ),
    ]
    text = bold_letters.table(scores)
    assert "| a | 2 | 1 | 1 (𝐚) | 1 (𝐛) |" in text  # noqa: RUF001
    assert "recall 0.500, precision 0.500" in text
