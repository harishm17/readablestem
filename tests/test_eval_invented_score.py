"""The invented-content rules, one by one, and on an output with inventions planted in it."""

from __future__ import annotations

import json

import pytest

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


def test_a_faithful_paraphrase_counts_as_unsupported_a_known_false_positive():
    result = scored("<p>Speed is distance divided by elapsed time, nothing more than that.</p>")
    assert result.counts["sentence"].unsupported == 1


def test_a_changed_word_inside_a_sentence_that_keeps_a_quarter_of_its_grams_passes():
    # The limit named in the pre-registration: one word of nine changed, 3 of 6 four-grams
    # remain, so the sentence is "supported" although it now says something else.
    changed = "Average speed is the distance traveled multiplied by the time it takes."
    result = scored(f"<p>{changed}</p>")
    assert result.counts["sentence"].n == 1 and result.counts["sentence"].unsupported == 0


def test_maths_is_cut_out_and_no_gram_spans_the_gap():
    body = (
        "<p>Average speed is the distance <math><mi>d</mi></math> traveled divided by the "
        "time <math><mi>t</mi></math> it takes to travel it.</p>"
    )
    result = scored(body)
    [verdict] = [v for v in result.verdicts if v.kind == "sentence"]
    assert not verdict.unsupported and verdict.detail["overlap4"] > 0.5


def test_the_tools_own_sentences_count_apart():
    result = scored(
        FAITHFUL + "<footer class='about readablestem'><h2>About this version</h2>"
        "<p>This is a web version of a PDF from your course, made with a tool.</p></footer>"
    )
    sentences = result.counts["sentence"]
    assert (sentences.n, sentences.unsupported) == (5, 1)
    assert (sentences.n_authored, sentences.unsupported_authored) == (1, 1)
    headings = result.counts["heading"]
    assert (headings.unsupported, headings.unsupported_authored) == (1, 1)


# ------------------------------------------------------------------ numbers


def test_a_number_the_pdf_does_not_print_is_flagged_with_its_sign():
    result = scored(
        "<p>The car went 151 km, then 3.2 h, then -47 km/h and 304 m, 1,000 m, 0.50 s.</p>"
        "<p>A drop of 5 degrees.</p><h3>Chapter 99</h3>"
    )
    assert flagged(result, "number") == ["151", "-47", "1000", "0.5", "5", "99"]
    assert result.counts["number"].n == 8


def test_numbers_are_read_from_short_blocks_too_and_not_from_maths():
    result = scored("<p>See 273.15 K.</p><p>Value <math><mn>999</mn></math> is a formula.</p>")
    assert [(v.text, v.unsupported) for v in result.verdicts if v.kind == "number"] == [
        ("273.15", False)
    ]


def test_thousands_separators_and_trailing_zeros_read_the_same():
    reference = T.Reference.from_pages(["The value is 1,250.50 metres and 3 m."])
    output = read.read_html(html("<p>It is 1250.5 metres, or 1 250.50 metres, or 3.0 m.</p>"))
    result = score.score_output(reference, output)
    # "1 250.50" with a plain space is read as 1 and 250.5; 1250.5, 3.0 are printed
    assert result.counts["number"].n == 4
    assert result.counts["number"].unsupported == 2


# ------------------------------------------------------------------ headings


def test_a_heading_not_in_the_pdf_is_flagged_and_a_wrapped_one_is_found():
    result = scored(
        "<h2>2.1 Speed and Velocity</h2><h3>Position vs. Time Graphs and the Meaning of Slope</h3>"
        "<h3>Safety</h3><h3>Summary and Key Takeaways</h3>"
    )
    assert flagged(result, "heading") == ["Safety", "Summary and Key Takeaways"]


def test_heading_with_only_punctuation_is_unchecked_not_flagged():
    result = scored("<h3>***</h3><h3>Safety</h3>")
    assert result.counts["heading"].unchecked == 1
    assert result.counts["heading"].n == 1


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


def test_tex_that_does_not_convert_is_unchecked():
    result = scored(r"<p>Value \(\undefinedcommand{3} \frac{\) here.</p>")
    assert result.counts["equation_unaligned"].unchecked == 1
    assert result.counts["equation_unaligned"].n == 0


def test_tex_equations_are_checked_like_mathml_ones():
    result = scored(r"<p>\(v = d/t\)</p><p>\[ E = m c^{2} \]</p>")
    assert [(v.kind, v.unsupported) for v in result.verdicts if v.kind.startswith("equation")] == [
        ("equation_unaligned", False),
        ("equation_unaligned", True),
    ]


# ------------------------------------------------------------------ tables


def test_table_cells_are_checked_for_numbers_and_for_wording():
    body = (
        "<table>"
        "<tr><th>Temperature (K)</th><td>273.15</td><td>373.16</td></tr>"
        "<tr><td>water freezes and water boils</td><td>average speed of 47 km/h</td>"
        "<td>an entirely invented phrase here</td></tr>"
        "<tr><td>N/A</td></tr></table>"
    )
    result = scored(body)
    cells = result.counts["table_cell"]
    assert cells.n == 7
    assert flagged(result, "table_cell") == [
        "373.16",
        "water freezes and water boils",
        "an entirely invented phrase here",
    ]
    assert [v.detail["numbers_missing"] for v in result.verdicts if v.text == "373.16"] == [
        ["373.16"]
    ]


def test_cells_of_a_figures_data_table_are_a_type_of_their_own():
    body = (
        "<figure><img src='x.png' alt='A graph.'></figure>"
        "<details><summary>Data</summary><table><tr><td>3.5</td></tr></table></details>"
    )
    result = scored(body)
    assert result.counts["description_cell"].n == 1 and result.counts["table_cell"].n == 0


# ------------------------------------------------------------------ descriptions, report


def test_descriptions_are_counted_by_source_and_left_to_the_judge():
    result = scored(
        "<figure><img src='x.png' alt='A graph of distance against time.'>"
        "<figcaption>Figure 2.1 Distance against time on a trip.</figcaption></figure>"
    )
    assert result.counts["figure_description"].n == 1
    assert result.counts["figure_description"].unsupported == 0
    assert result.descriptions_by_source == {"alt": 1}


def test_to_dict_has_counts_per_page_and_units_only_on_request():
    result = scored(FAITHFUL + "<p>An invented sentence about something else entirely.</p>")
    plain = result.to_dict()
    assert "units" not in plain
    assert plain["reference"]["pages"] == 2
    assert plain["per_page"]["sentence"] == pytest.approx(0.5)
    assert plain["counts"]["sentence"]["unsupported"] == 1
    assert json.dumps(plain)  # JSON-ready
    with_units = result.to_dict(units=True)
    assert any(u["unsupported"] and u["kind"] == "sentence" for u in with_units["units"])


def test_rule_summary_records_every_threshold():
    rules = score.rule_summary()
    assert rules["sentence_min_words"] == 7 and rules["sentence_support_min"] == 0.25
    assert rules["heading_ratio"] == 0.85 and rules["unaligned_ratio"] == 0.8
    assert rules["unaligned_min_symbols"] == 3 and rules["cell_support_min"] == 0.5


# ------------------------------------------------------------------ gold equations


def test_gold_equations_come_in_the_order_of_the_pages_asked_for(tmp_path):
    folder = tmp_path / "openstax" / "physics"
    folder.mkdir(parents=True)
    for slug, marks in (("2-2-b", ["B1", "B2"]), ("2-1-a", ["A1"])):
        lines = [
            json.dumps(
                {
                    "kind": "equation",
                    "book_slug": "physics",
                    "page_slug": slug,
                    "index_on_page": i,
                    "mathml": f"<math><mi>{mark}</mi></math>",
                }
            )
            for i, mark in enumerate(marks)
        ]
        (folder / f"{slug}.jsonl").write_text("\n".join(lines))
    both = score.gold_equation_mathml(tmp_path, ["2-2-b", "2-1-a"])
    assert [("B1" in m, "B2" in m, "A1" in m) for m in both] == [
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ]
    assert len(score.gold_equation_mathml(tmp_path)) == 3
    assert len(score.gold_equation_mathml(tmp_path, book="chemistry")) == 0
    with pytest.raises(ValueError, match="no gold equations for page"):
        score.gold_equation_mathml(tmp_path, ["9-9-nope"])


# ------------------------------------------------------------------ planted inventions

PLANTS = [
    (
        "sentence",
        "<p>Students sometimes forget that acceleration also depends on the mass involved.</p>",
    ),
    (
        "sentence",
        "<p>Hint: remember to convert every unit to meters before you start the problem.</p>",
    ),
    (
        "sentence",
        "<p>This idea was first described by Galileo in a famous series of experiments.</p>",
    ),
    ("number", "<p>See page 417 of the textbook.</p>"),
    ("number", "<p>The value 9.81 is used.</p>"),
    ("heading", "<h3>Key Takeaways for Students</h3>"),
    ("heading", "<h3>Common Misconceptions</h3>"),
]


def test_planted_inventions_are_found_and_the_faithful_text_is_left_alone():
    base = scored(FAITHFUL)
    assert sum(base.counts[k].unsupported for k in ("sentence", "number", "heading")) == 0
    for kind, planted in PLANTS:
        result = scored(FAITHFUL + planted)
        assert result.counts[kind].unsupported >= 1, planted
    everything = scored(FAITHFUL + "".join(p for _k, p in PLANTS))
    assert everything.counts["sentence"].unsupported == 3
    assert everything.counts["heading"].unsupported == 2
    assert everything.counts["number"].unsupported == 2


def test_the_text_layer_footer_and_attribution_are_supported_when_copied():
    # R includes what the print adds, so a converted copy of it is not an invention
    result = scored(
        "<p>Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 2 of 2</p>"
    )
    assert result.counts["sentence"].unsupported == 0 and result.counts["number"].unsupported == 0


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


def test_a_bracketed_number_counts_as_a_tag_too():
    result = _gold_with_tag("<mo>(</mo><mn>2.1</mn><mo>)</mo>")
    assert result.counts["equation_aligned"].tag_only == 1


def test_a_trailing_number_the_pdf_does_not_print_is_not_a_tag():
    result = _gold_with_tag("<mn>3.7</mn>")
    assert result.counts["equation_aligned"].unsupported == 1
    assert result.counts["equation_aligned"].tag_only == 0


def test_an_added_letter_next_to_a_tag_is_not_a_tag():
    result = _gold_with_tag("<mi>b</mi><mn>2.1</mn>")
    assert result.counts["equation_aligned"].unsupported == 1
    assert result.counts["equation_aligned"].tag_only == 0
    assert result.added_tag_symbols == 0
