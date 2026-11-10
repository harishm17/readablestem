"""The row-wise reading of an unaligned equation (`score.row_support`), a secondary count beside
the rule the registered numbers use. Fixtures are two equations from the 2026-10-21
re-measurement audit that are printed as text, and the matrix pictures that must not be rescued."""

from __future__ import annotations

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
# 4-6 Determinants: a 3 x 3 determinant read from a picture, three digits to a row.
MATRIX = (
    "<math display='block'><mo>|</mo><mtable>"
    "<mtr><mtd><mn>3</mn></mtd><mtd><mo>-</mo><mn>5</mn></mtd><mtd><mn>4</mn></mtd></mtr>"
    "<mtr><mtd><mn>5</mn></mtd><mtd><mn>2</mn></mtd><mtd><mn>1</mn></mtd></mtr>"
    "<mtr><mtd><mn>2</mn></mtd><mtd><mn>3</mn></mtd><mtd><mo>-</mo><mn>2</mn></mtd></mtr>"
    "</mtable><mo>|</mo></math>"
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


def test_a_matrix_of_short_rows_is_not_rescued_by_digits_found_by_chance():
    # each 3-symbol row of MATRIX shows up in PAGE, none of them is long enough to count
    assert [len(r) for r in score.rows_of(MATRIX)] == [3, 3, 3]
    ok, share = score.row_support(REFERENCE, MATRIX)
    assert not ok and share < score.ROW_SUPPORT_MIN
