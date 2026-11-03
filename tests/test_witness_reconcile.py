"""The page witness against layout's elements (`pipeline/witness_reconcile.py`): which
formulas match an element within one glyph, which only overlap it, which the witness found
alone (and need glyph evidence and clear space), and how two readings are adjudicated. Rows
and characters are built by hand so the geometry and the fonts are exact."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline import layout, math_canonical, witness_reconcile
from readablestem.pipeline.page_math import PageRow, Segment, Span
from readablestem.pipeline.page_witness import Formula, PageWitness, WitnessPlan

MATH = "CMMI10"
TEXT = "Times-Roman"
W, H = 6.0, 10.0  # one glyph


def drawn(
    row_id: str, text: str, x: float, y: float, *, font: str = MATH
) -> tuple[PageRow, SimpleNamespace]:
    """A row of `text` starting at (x, y), one glyph W by H per character."""
    chars = [
        layout._Char(c, x + i * W, y, x + (i + 1) * W, y + H, y + 8, 10.0, font, 0)
        for i, c in enumerate(text)
    ]
    return PageRow(row_id, text, tuple(range(len(text)))), SimpleNamespace(chars=chars)


def formula(
    row_id: str, start: int, end: int, latex: str = "x", *, display="inline", where="text"
) -> Formula:
    return Formula(Span((Segment(row_id, start, end),), display, "math", latex), where)


def plan(formulas: list[Formula]) -> WitnessPlan:
    return WitnessPlan({0: PageWitness([], formulas)})


def equation(box, *, element_id="p1-e1", order=1, display="block", text="x = 5") -> Element:
    return Element(
        id=element_id, kind=Kind.EQUATION, page=0, bbox=box, order=order, display=display, text=text
    )


def paragraph(box, *, element_id="p1-e0", order=0) -> Element:
    return Element(
        id=element_id, kind=Kind.PARAGRAPH, page=0, bbox=box, order=order, text="Some prose here."
    )


def reconcile(elements, formulas, *rows):
    by_id = {r.id: r for r, _ in rows}
    objects = {r.id: o for r, o in rows}
    return witness_reconcile.reconcile(
        elements, plan(formulas), {0: list(by_id.values())}, {0: objects}
    )


# ------------------------------------------------------------------------------- extents


def test_the_extent_is_the_box_of_the_quoted_characters_and_counts_maths_glyphs():
    row = drawn("L0", "x = 5 here", 100, 200)
    found = witness_reconcile.extent_of(formula("L0", 0, 5), {"L0": row[0]}, {"L0": row[1]})
    assert found is not None
    assert found.bbox == (100, 200, 100 + 5 * W, 200 + H)
    assert (found.glyph_w, found.glyph_h) == (W, H)
    assert found.chars == 3 and found.math_chars == 3  # the blanks are not counted
    assert found.text == "x = 5"


def test_an_unknown_row_has_no_extent():
    row = drawn("L0", "x = 5", 100, 200)
    assert witness_reconcile.extent_of(formula("L7", 0, 1), {"L0": row[0]}, {"L0": row[1]}) is None


# ------------------------------------------------------------------------------- matching


def test_an_equation_whose_edges_are_within_one_glyph_of_the_witness_matches():
    row = drawn("L0", "x = 5", 100, 200)
    el = equation((100 + W * 0.9, 200 - H * 0.5, 100 + 5 * W - W * 0.9, 200 + H * 1.4))
    out = reconcile([el], [formula("L0", 0, 5, "x=5")], row)
    (done,) = out.elements
    assert (done.witness_status, done.witness_latex) == ("match", "x=5")
    assert out.stats.matched == 1 and out.stats.added == 0
    assert witness_reconcile.reading(done) == "x=5"


def test_an_equation_that_reaches_a_glyph_further_than_the_witness_only_overlaps_it():
    row = drawn("L0", "x = 5 + 1", 100, 200)
    el = equation((100, 200, 100 + 9 * W, 200 + H))  # includes "+ 1"
    out = reconcile([el], [formula("L0", 0, 5, "x=5")], row)
    assert out.elements[0].witness_status == "extent"
    assert out.stats.extent_differs == 1
    assert witness_reconcile.reading(out.elements[0]) is None  # only a match can agree


def test_the_witness_formula_goes_to_the_equation_it_overlaps_most_one_to_one():
    row = drawn("L0", "x = 5", 100, 200)
    near = equation((100, 200, 130, 210), element_id="p1-e1", order=1)
    wide = equation((100, 200, 160, 230), element_id="p1-e2", order=2)
    out = reconcile([near, wide], [formula("L0", 0, 5)], row)
    by_id = {e.id: e for e in out.elements}
    assert by_id["p1-e1"].witness_status == "match"
    assert by_id["p1-e2"].witness_status is None


def test_elements_of_pages_the_witness_did_not_answer_are_returned_as_they_were():
    el = equation((100, 200, 130, 210))
    failed = WitnessPlan({0: PageWitness([], [], failed="refused")})
    out = witness_reconcile.reconcile([el], failed, {}, {})
    assert out.elements == [el]


# ---------------------------------------------------------------------------- witness-only


def test_a_formula_only_the_witness_read_becomes_a_new_element_when_its_glyphs_are_maths():
    above = paragraph((72, 100, 400, 150), element_id="p1-e0", order=0)
    below = paragraph((72, 300, 400, 350), element_id="p1-e1", order=1)
    row = drawn("L0", "E = m c", 150, 200)
    out = reconcile([above, below], [formula("L0", 0, 7, "E=mc", display="block")], row)
    assert out.stats.added == 1
    assert [e.id for e in out.elements] == ["p1-e0", "p1-e2", "p1-e1"]  # in reading order
    assert [e.order for e in out.elements] == [0, 1, 2]
    new = out.elements[1]
    assert new.kind is Kind.EQUATION and new.display == "block"
    assert new.witness_status == "added" and new.witness_latex == "E=mc"
    assert witness_reconcile.WITNESS_ADDED_REASON in new.reasons
    assert new.text == "E = m c" and new.bbox == (150, 200, 150 + 7 * W, 200 + H)


def test_a_formula_without_maths_glyphs_is_left_out():
    """A page number and a year are digits in a text font: no evidence they are maths."""
    row = drawn("L0", "2026", 100, 700, font=TEXT)
    out = reconcile([], [formula("L0", 0, 4, "2026")], row)
    assert out.elements == [] and out.stats.no_glyph_evidence == 1 and out.stats.added == 0


def test_a_formula_inside_a_paragraph_or_another_element_is_left_to_it():
    row = drawn("L0", "x = 5", 100, 200)
    prose = paragraph((72, 190, 500, 220))
    out = reconcile([prose], [formula("L0", 0, 5)], row)
    assert out.stats.inside_text == 1 and out.stats.added == 0
    assert [e.id for e in out.elements] == [prose.id]


def test_a_new_element_takes_the_next_free_id_and_a_column_of_its_own():
    left = paragraph((72, 100, 250, 400), element_id="p1-e0", order=0)
    right = paragraph((300, 100, 500, 400), element_id="p1-e1", order=1)
    row = drawn("L0", "a = b", 320, 450)  # under the right column
    out = reconcile([left, right], [formula("L0", 0, 5, "a=b", display="block")], row)
    assert [e.id for e in out.elements] == ["p1-e0", "p1-e1", "p1-e2"]


# ------------------------------------------------------------------------- agree / tie


@pytest.mark.parametrize(
    ("witness", "crop", "agree"),
    [
        (r"v_{0}", "<math><msub><mi>v</mi><mn>0</mn></msub></math>", True),
        (r"\left( x \right)", math_canonical.latex_to_mathml("(x)"), True),
        (r"v_1", "<math><msub><mi>v</mi><mn>0</mn></msub></math>", False),
        (r"x = 5", "<math><mi>x</mi><mo>=</mo><mn>6</mn></math>", False),
    ],
)
def test_agreement_is_canonical_equality_not_spelling(witness, crop, agree):
    assert witness_reconcile.agrees(witness, crop) is agree


def test_a_witness_reading_that_does_not_convert_never_agrees():
    assert witness_reconcile.agrees(r"\frac{", "<math><mi>x</mi></math>") is False


def mathml(*tokens: str) -> str:
    return "<math>" + "".join(f"<mi>{t}</mi>" for t in tokens) + "</math>"


def test_the_reading_whose_symbols_are_closer_to_the_glyphs_wins_and_a_near_tie_is_a_tie():
    # the glyphs say "a b c d"
    assert (
        witness_reconcile.adjudicate(
            "a b c d", mathml("a", "b", "x", "d"), mathml("a", "b", "c", "d")
        )
        == "witness"
    )
    assert (
        witness_reconcile.adjudicate(
            "a b c d", mathml("a", "b", "c", "d"), mathml("a", "b", "x", "d")
        )
        == "crop"
    )
    assert witness_reconcile.adjudicate("a b", mathml("a", "c"), mathml("a", "d")) == "tie"
    assert witness_reconcile.adjudicate(None, mathml("a"), mathml("b")) == "tie"
    assert witness_reconcile.adjudicate("  ", mathml("a"), mathml("b")) == "tie"


def test_a_tie_on_the_letters_is_broken_by_a_slash_the_glyphs_show():
    # "-2/3" written on the page: the text layer has the slash, a fraction bar would not
    fraction = "<math><mo>-</mo><mfrac><mn>2</mn><mn>3</mn></mfrac></math>"
    written = "<math><mo>-</mo><mn>2</mn><mo>/</mo><mn>3</mn></math>"
    assert witness_reconcile.adjudicate("−2/3", fraction, written) == "witness"
    assert witness_reconcile.adjudicate("−2/3", written, fraction) == "crop"
    # a drawn fraction (no slash in the text layer) and a reading that wrote a slash
    assert witness_reconcile.adjudicate("−2 3", written, fraction) == "witness"
    assert witness_reconcile.adjudicate("−2 3", fraction, written) == "crop"
    # no slash anywhere: still a tie, and the crop stays
    assert witness_reconcile.adjudicate("a b", mathml("a", "b"), mathml("a", "b")) == "tie"


def test_the_slash_never_overrules_the_letters_and_brackets_are_not_marks():
    # the letters decide first, whatever the slashes say
    assert (
        witness_reconcile.adjudicate(
            "a b c/d",
            "<math><mi>a</mi><mi>b</mi><mi>c</mi><mi>x</mi></math>",
            "<math><mi>a</mi><mi>b</mi><mi>c</mi><mo>/</mo><mi>d</mi></math>",
        )
        == "witness"
    )
    # an element's text often holds a neighbour's bracket: it does not pick a reading
    bare = "<math><mi>R</mi></math>"
    bracketed = "<math><mo>(</mo><mi>R</mi><mo>)</mo></math>"
    assert witness_reconcile.adjudicate("(R)", bare, bracketed) == "tie"
    assert witness_reconcile.marks_dice("a/b", "<math><mi>a</mi><mi>b</mi></math>") == 0.0
    assert witness_reconcile.marks_dice("a b", "<math><mi>a</mi><mi>b</mi></math>") == 1.0
