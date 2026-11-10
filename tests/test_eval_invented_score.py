"""The invented-content rules, one by one, and on an output with inventions planted in it."""

from __future__ import annotations

from readablestem.eval.invented import read, score
from readablestem.eval.invented import text as T

PAGE_ONE = """2.1 Speed and Velocity
Average speed is the distance traveled divided by the time it takes to travel it.
A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.
The jogger's displacement was 304 m north in 2 min and 30 s, a drop of -5 degrees.
v = d / t
Newton's second law says that the net force on an object equals its mass times acceleration.
Position vs. Time Graphs and the
Meaning of Slope
A straight line on this graph means the velocity of the object stays constant over time.
"""
PAGE_TWO = """Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 2 of 2
Temperature (K) 273.15 and 373.15 are the freezing and boiling points of water here.
"""

REFERENCE = T.Reference.from_pages([PAGE_ONE, PAGE_TWO])


def html(body: str) -> str:
    return f"<!doctype html><html lang='en'><body>{body}</body></html>"


def scored(body: str, gold: list[str] | None = None) -> score.Score:
    return score.score_output(REFERENCE, read.read_html(html(body)), gold_equations=gold)


def flagged(result: score.Score, kind: str) -> list[str]:
    return [v.text for v in result.verdicts if v.kind == kind and v.unsupported]


FAITHFUL = (
    "<h2>2.1 Speed and Velocity</h2>"
    "<p>Average speed is the distance traveled divided by the time it takes to travel it.</p>"
    "<p>A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.</p>"
    "<p>The jogger's displacement was 304 m north in 2 min and 30 s, a drop of -5 degrees.</p>"
    "<h3>Position vs. Time Graphs and the Meaning of Slope</h3>"
    "<p>A straight line on this graph means the velocity of the object stays constant over "
    "time.</p>"
    "<p>Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 2 of 2</p>"
)


# ------------------------------------------------------------------ prose


def test_a_faithful_copy_has_nothing_unsupported():
    result = scored(FAITHFUL)
    for kind in ("sentence", "number", "heading"):
        assert result.counts[kind].n > 0
        assert result.counts[kind].unsupported == 0, kind
    # four paragraphs of 7 words or more; the footer's sentences are shorter
    assert result.counts["sentence"].n == 4
    assert result.counts["heading"].n == 2


def test_an_invented_sentence_is_flagged_and_a_short_one_is_not_a_unit():
    result = scored(
        FAITHFUL + "<p>Remember that students often confuse speed with velocity on tests.</p>"
        "<p>Hint: think carefully.</p>"
    )
    assert flagged(result, "sentence") == [
        "Remember that students often confuse speed with velocity on tests."
    ]
    assert result.counts["sentence"].n == 5  # "Hint: think carefully." has 3 words


# ------------------------------------------------------------------ numbers


def test_a_number_the_pdf_does_not_print_is_flagged_with_its_sign():
    result = scored(
        "<p>The car went 151 km, then 3.2 h, then -47 km/h and 304 m, 1,000 m, 0.50 s.</p>"
        "<p>A drop of 5 degrees.</p><h3>Chapter 99</h3>"
    )
    assert flagged(result, "number") == ["151", "-47", "1000", "0.5", "5", "99"]
    assert result.counts["number"].n == 8


# ------------------------------------------------------------------ headings


def test_a_heading_not_in_the_pdf_is_flagged_and_a_wrapped_one_is_found():
    result = scored(
        "<h2>2.1 Speed and Velocity</h2><h3>Position vs. Time Graphs and the Meaning of Slope</h3>"
        "<h3>Safety</h3><h3>Summary and Key Takeaways</h3>"
    )
    assert flagged(result, "heading") == ["Safety", "Summary and Key Takeaways"]


# ------------------------------------------------------------------ equations

GOLD_V = "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"
GOLD_F = "<math><mi>F</mi><mo>=</mo><mi>m</mi><mi>a</mi></math>"


def test_an_aligned_equation_with_an_added_symbol_is_flagged():
    body = (
        "<p><math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math></p>"
        "<p><math><mi>F</mi><mo>=</mo><mi>m</mi><mi>a</mi><mo>+</mo><mi>b</mi></math></p>"
    )
    result = scored(body, gold=[GOLD_V, GOLD_F])
    assert result.counts["equation_aligned"].n == 2
    assert result.counts["equation_aligned"].unsupported == 1
    [bad] = [v for v in result.verdicts if v.kind == "equation_aligned" and v.unsupported]
    assert bad.detail["added"] == "b"
    assert (result.added_symbols, result.aligned_pairs) == (1, 2)
    assert result.aligned_symbols == 3 + 4


def test_a_wrong_digit_or_dropped_symbol_in_an_aligned_equation():
    # a swapped symbol adds one the gold does not hold; a dropped one adds nothing
    gold = ["<math><mi>x</mi><mo>=</mo><mn>2</mn><mi>a</mi></math>"]
    swapped = scored("<p><math><mi>x</mi><mo>=</mo><mn>3</mn><mi>a</mi></math></p>", gold=gold)
    dropped = scored("<p><math><mi>x</mi><mo>=</mo><mn>2</mn></math></p>", gold=gold)
    assert swapped.counts["equation_aligned"].unsupported == 1
    assert dropped.counts["equation_aligned"].unsupported == 0


def test_without_gold_an_equation_is_checked_against_the_symbols_of_the_text_layer():
    body = (
        "<p><math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math></p>"  # in R
        "<p><math><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></math></p>"
        "<p><math><mi>x</mi><mo>=</mo><mn>5</mn></math></p>"  # 2 symbols: too short
    )
    result = scored(body)
    unaligned = result.counts["equation_unaligned"]
    assert (unaligned.n, unaligned.unsupported, unaligned.too_short) == (2, 1, 1)
    assert result.counts["equation_aligned"].n == 0


# ------------------------------------------------------------------ tables


def test_cells_of_a_figures_data_table_are_a_type_of_their_own():
    body = (
        "<figure><img src='x.png' alt='A graph.'></figure>"
        "<details><summary>Data</summary><table><tr><td>3.5</td></tr></table></details>"
    )
    result = scored(body)
    assert result.counts["description_cell"].n == 1 and result.counts["table_cell"].n == 0


# ------------------------------------------------------------------ descriptions, report


# ------------------------------------------------------------------ gold equations


# ------------------------------------------------------------------ planted inventions


# ------------------------------------------------------------------ a kept equation number


def _gold_with_tag(extra: str) -> score.Score:
    body = "<p><math><mi>F</mi><mo>=</mo><mi>m</mi><mi>a</mi>" + extra + "</math></p>"
    return scored(body, gold=[GOLD_F])


def test_a_printed_equation_number_kept_inside_the_maths_is_flagged_and_marked_as_a_tag():
    result = _gold_with_tag("<mn>2.1</mn>")  # "2.1" is printed in the PDF
    assert result.counts["equation_aligned"].unsupported == 1
    assert result.counts["equation_aligned"].tag_only == 1
    [bad] = [v for v in result.verdicts if v.kind == "equation_aligned"]
    assert bad.detail["added"] == "12" and bad.detail["tag_only"] is True
    assert (result.added_symbols, result.added_tag_symbols) == (2, 2)
