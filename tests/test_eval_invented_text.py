"""The source's side of the invented-content rules: normalisation, numbers, symbols, R."""

from __future__ import annotations

import time
from collections import Counter
from decimal import Decimal

import pymupdf
import pytest

from readablestem.eval.invented import text as T
from readablestem.pipeline import equation_step

MINUS = "−"


def pdf_bytes(*pages: str) -> bytes:
    document = pymupdf.open()
    for body in pages:
        page = document.new_page()
        y = 72
        for line in body.split("\n"):
            page.insert_text((72, y), line, fontsize=11)
            y += 16
    data = document.tobytes()
    document.close()
    return data


# ------------------------------------------------------------------ words


def test_norm_words_folds_ligatures_quotes_case_and_drops_punctuation():
    assert T.norm_words("The \N{LATIN SMALL LIGATURE FI}rst one") == ["the", "first", "one"]
    # the quote is folded to an apostrophe, which then splits the run
    assert T.norm_words("it\N{RIGHT SINGLE QUOTATION MARK}s") == ["it", "s"]
    assert T.norm_words("F = m\N{MIDDLE DOT}a (2.5 m/s)") == ["f", "m", "a", "2", "5", "m", "s"]
    # NFKC: a circled one is 1, a subscript two is 2, a math italic letter is its letter
    assert T.norm_words("\N{CIRCLED DIGIT ONE} v\N{SUBSCRIPT TWO}") == ["1", "v2"]
    assert T.norm_words("\N{MATHEMATICAL ITALIC SMALL V}") == ["v"]


def test_ngrams_and_sentences():
    assert T.ngrams(["a", "b", "c", "d", "e"], 4) == {("a", "b", "c", "d"), ("b", "c", "d", "e")}
    assert T.ngrams(["a", "b"], 4) == set()
    text = "First one. Second one! Is it third? Fig. 2.5 shows 1.5 m."
    assert T.split_sentences(text) == [
        "First one.",
        "Second one!",
        "Is it third?",
        "Fig.",
        "2.5 shows 1.5 m.",
    ]


def test_segments_split_at_maths_so_no_gram_crosses_a_gap():
    sentence = f"the line starts at {T.MATH}, as well as here"
    assert T.segments(sentence) == [["the", "line", "starts", "at"], ["as", "well", "as", "here"]]


# ------------------------------------------------------------------ numbers


def test_numbers_are_read_the_way_figure_details_reads_them():
    read = T.numbers_of
    assert read("a drop of -5 or −5 degrees") == [Decimal(-5), Decimal(-5)]
    assert read("1,000 and 0.50 and .5 and 2.50") == [
        Decimal(1000),
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("2.5"),
    ]
    assert set(read("0.50 and 0.5")) == {Decimal("0.5")}
    assert read("Figure 2.10") == [Decimal("2.1")]
    # a dash between numbers is not a sign; letters glued to a digit are not a number
    assert read("pages 1-3 of x2") == [Decimal(1), Decimal(3)]


def test_a_sign_is_kept_so_a_flipped_sign_is_a_different_number():
    assert Decimal(-40) in T.numbers_of(f"{MINUS}40 m/s")
    assert Decimal(-40) not in T.numbers_of("40 m/s")
    # printed with a space after the hyphen, the sign is not attached: the print says 40
    assert T.numbers_of("b. - 40 m/s") == [Decimal(40)]


def test_math_digits_and_the_maths_marker_are_handled():
    assert T.numbers_of("\U0001d7d1\U0001d7ce") == [Decimal(30)]  # bold 3 and 0
    assert T.numbers_of(f"2{T.MATH}3") == [Decimal(2), Decimal(3)]


def test_a_superscript_run_is_read_apart_from_its_base():
    assert T.numbers_of("10²⁴") == [Decimal(10), Decimal(24)]


# ------------------------------------------------------------------ symbols


@pytest.mark.parametrize(
    "text",
    [
        "v = d / t",
        "Δx = x₂ − x₁",
        "\U0001d41f = m\U0001d41a",  # bold f = m bold a
        "ω = 2πf ± 0.5",
        "ıȷ Δ µ",
        "\N{COMBINING CIRCUMFLEX ACCENT}v and \N{MODIFIER LETTER CIRCUMFLEX ACCENT}a",  # accents
    ],
)
def test_symbol_stream_has_the_symbols_equation_step_counts(text):
    assert Counter(T.symbol_stream(text)) == equation_step._symbol_bag(text)
    assert T.symbol_bag(text) == equation_step._symbol_bag(text)


def test_symbol_stream_keeps_reading_order():
    assert T.symbol_stream("x = 2y + 3") == "x2y3"
    assert T.symbol_stream("v / t = x") == "vtx"


def test_symbol_index_finds_a_sequence_up_to_a_few_wrong_symbols():
    index = T.SymbolIndex("abcdefghij" * 20 + "x2y3z4w5" + "klmnop" * 10)
    assert index.supports("x2y3z4w5")  # exact
    assert index.supports("x2y3z4w6")  # one wrong symbol in 8: ratio 0.875
    assert not index.supports("x2q9r8w5")  # three wrong in 8: 0.625
    assert not index.supports("qqqqqqqq")
    assert index.best_ratio("a" * 400) == 0.0  # longer than the stream
    assert index.best_ratio("") == 0.0


def test_symbol_index_uses_the_whole_window_not_a_bag():
    index = T.SymbolIndex("12345678" * 3)
    assert index.supports("23456781")  # a window of the stream
    assert not index.supports("87654321")  # same bag, reversed: not a window


def test_symbol_index_is_fast_on_a_long_stream():
    stream = ("the quick brown fox jumps over the lazy dog 0123456789 " * 4000).replace(" ", "")
    index = T.SymbolIndex(stream)
    started = time.perf_counter()
    for k in range(60):
        index.best_ratio(f"vavg{k}t2x")
    assert time.perf_counter() - started < 5


# ------------------------------------------------------------------ the reference


def test_reference_from_pdf_reads_every_page_and_keeps_signs_footers_and_lines():
    data = pdf_bytes(
        "2.1 Speed and Velocity\nThe car went 5.2 m in 1.8 s.\nAccess for free at openstax.org",
        "A drop of -5 degrees.\nPage 2 of 2",
    )
    reference = T.Reference.from_pdf(data)
    assert reference.pages == 2
    assert reference.numbers >= {Decimal("5.2"), Decimal("1.8"), Decimal(2), Decimal(-5)}
    assert ("the", "car", "went", "5") in reference.grams4
    assert "2 1 speed and velocity" in reference.line_keys
    assert "access for free at openstax org" in reference.line_keys
    assert len(reference.sha256) == 64
    assert reference.sha256 != T.Reference.from_pdf(pdf_bytes("other")).sha256


def test_reference_heading_ratio_matches_a_heading_printed_over_two_lines():
    reference = T.Reference.from_pages(
        ["Position vs. Time Graphs and the\nMeaning of Slope\nBody text goes here."]
    )
    assert reference.heading_ratio("Position vs. Time Graphs and the Meaning of Slope") >= 0.99
    assert reference.heading_ratio("Position") < 0.85  # a prefix is not a match
    assert reference.heading_ratio("Something else entirely") < 0.5
    assert reference.heading_ratio("") == 0.0
    assert reference.heading_ratio("!!!") == 0.0


def test_reference_from_pages_has_the_forms_the_rules_read():
    reference = T.Reference.from_pages(["one two three four five", "six seven"])
    assert reference.words == ("one", "two", "three", "four", "five", "six", "seven")
    assert ("one", "two", "three", "four") in reference.grams4
    assert ("five", "six") in reference.grams2
    assert reference.symbols == "onetwothreefourfivesixseven"
