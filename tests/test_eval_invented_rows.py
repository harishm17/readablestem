"""The row-wise reading of an unaligned equation (`score.row_support`), a secondary count beside
the rule the registered numbers use. Fixtures are two equations from the 2026-10-21
re-measurement audit that are printed as text, and the matrix pictures that must not be rescued."""

from __future__ import annotations

import pytest

from readablestem.eval.invented import read, score
from readablestem.eval.invented import text as T

# 1.5 Properties of Real Numbers: two steps of a derivation, the text layer puts the lesson's
# sentences between them (and the second step comes before the first on the page).
DERIVATION = (
    "<math display='block'><mtable>"
    "<mtr><mtd><mn>18</mn><mi>p</mi><mo>+</mo><mn>6</mn><mi>q</mi><mo>+</mo><mn>15</mn>"
    "<mi>p</mi><mo>+</mo><mn>5</mn><mi>q</mi></mtd></mtr>"
    "<mtr><mtd><mn>18</mn><mi>p</mi><mo>+</mo><mn>15</mn><mi>p</mi><mo>+</mo><mn>6</mn>"
    "<mi>q</mi><mo>+</mo><mn>5</mn><mi>q</mi></mtd></mtr>"
    "</mtable></math>"
)
# Precalculus 3-1: stacked fractions, one per row.
FRACTIONS = (
    "<math display='block'><mtable>"
    "<mtr><mtd><mfrac><mrow><mn>106</mn><mo>+</mo><mn>10</mn><mi>i</mi></mrow>"
    "<mrow><mn>109</mn></mrow></mfrac></mtd></mtr>"
    "<mtr><mtd><mfrac><mn>106</mn><mn>109</mn></mfrac><mo>+</mo>"
    "<mfrac><mn>10</mn><mn>109</mn></mfrac><mi>i</mi></mtd></mtr>"
    "</mtable></math>"
)
# 4-6 Determinants: a 3 x 3 determinant read from a picture, three digits to a row.
MATRIX = (
    "<math display='block'><mo>|</mo><mtable>"
    "<mtr><mtd><mn>3</mn></mtd><mtd><mo>-</mo><mn>5</mn></mtd><mtd><mn>4</mn></mtd></mtr>"
    "<mtr><mtd><mn>5</mn></mtd><mtd><mn>2</mn></mtd><mtd><mn>1</mn></mtd></mtr>"
    "<mtr><mtd><mn>2</mn></mtd><mtd><mn>3</mn></mtd><mtd><mo>-</mo><mn>2</mn></mtd></mtr>"
    "</mtable><mo>|</mo></math>"
)
# the same shape, a row that is not on the page
INVENTED_ROWS = (
    "<math display='block'><mtable>"
    "<mtr><mtd><mn>7</mn><mi>k</mi><mo>+</mo><mn>9</mn><mi>m</mi><mo>=</mo><mn>31</mn></mtd></mtr>"
    "<mtr><mtd><mn>2</mn><mi>k</mi><mo>-</mo><mn>8</mn><mi>m</mi><mo>=</mo><mn>14</mn></mtd></mtr>"
    "</mtable></math>"
)

PAGE = """Combine like terms. Add the p terms together and the q terms together.
18p + 15p + 6q + 5q
Now check the order of the steps with the commutative property of addition.
18p + 6q + 15p + 5q
A determinant such as 3 5 4 or 5 2 1 or 2 3 2 appears in the table, a row at a time.
Rewrite each fraction by writing 106/109 + 10/109 i on one line.
(106 + 10i)/109
That is the sum of two simpler fractions with the same denominator 109.
"""

REFERENCE = T.Reference.from_pages([PAGE])


def scored(mathml: str) -> score.Score:
    html = f"<!doctype html><html lang='en'><body><p>{mathml}</p></body></html>"
    return score.score_output(REFERENCE, read.read_html(html))


def test_a_derivation_whose_steps_are_not_one_run_of_the_text_is_unsupported_by_the_rule():
    unaligned = scored(DERIVATION).counts["equation_unaligned"]
    assert (unaligned.n, unaligned.unsupported) == (1, 1)


def test_the_same_derivation_is_supported_row_by_row_in_whatever_order_the_rows_come():
    ok, share = score.row_support(REFERENCE, DERIVATION)
    assert ok and share == 1.0
    swapped = DERIVATION.replace("<mtr>", "<mtr>@", 2)  # rows kept, only their order differs
    first, second = swapped.split("<mtr>@")[1:]
    reordered = (
        "<math><mtable><mtr>"
        + second.split("</mtable>")[0]
        + "<mtr>"
        + first
        + ("</mtable></math>")
    )
    assert score.row_support(REFERENCE, reordered)[0]


def test_stacked_fractions_are_supported_row_by_row():
    assert score.row_support(REFERENCE, FRACTIONS)[0]
    assert scored(FRACTIONS).counts["equation_unaligned"].unsupported_by_rows == 0


def test_a_matrix_of_short_rows_is_not_rescued_by_digits_found_by_chance():
    # each 3-symbol row of MATRIX shows up in PAGE, none of them is long enough to count
    assert [len(r) for r in score.rows_of(MATRIX)] == [3, 3, 3]
    ok, share = score.row_support(REFERENCE, MATRIX)
    assert not ok and share < score.ROW_SUPPORT_MIN


def test_rows_that_are_not_in_the_text_are_unsupported_row_by_row_too():
    ok, share = score.row_support(REFERENCE, INVENTED_ROWS)
    assert not ok and share == 0.0


def test_the_count_beside_the_rule_never_changes_the_rule():
    for mathml, rows_ok in ((DERIVATION, True), (FRACTIONS, True), (MATRIX, False)):
        unaligned = scored(mathml).counts["equation_unaligned"]
        assert unaligned.unsupported == 1
        assert unaligned.unsupported_by_rows == (0 if rows_ok else 1)
    # an equation the rule supports adds to neither count
    supported = scored("<math><mi>a</mi></math>").counts["equation_unaligned"]
    assert (supported.unsupported, supported.unsupported_by_rows) == (0, 0)


def test_an_equation_without_a_table_is_one_row():
    mathml = (
        "<math><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup>"
        "<mo>+</mo><mi>z</mi><mi>w</mi></math>"
    )
    assert len(score.rows_of(mathml)) == 1


def test_text_outside_the_table_is_a_row_of_its_own():
    mathml = (
        "<math><mi>D</mi><mo>=</mo><mtable><mtr><mtd><mn>1</mn></mtd></mtr></mtable>"
        "<mtext>minor</mtext></math>"
    )
    rows = score.rows_of(mathml)
    assert "d" in rows[-1] and "minor" in rows[-1]


def test_rows_of_a_mathml_that_does_not_parse_is_empty():
    assert score.rows_of("<math><mi>") == []
    assert score.row_support(REFERENCE, "<math><mi>")[0]


def test_the_thresholds_are_recorded():
    rules = score.rule_summary()
    assert rules["row_min_symbols"] == 4 and rules["row_support_min"] == pytest.approx(0.8)
