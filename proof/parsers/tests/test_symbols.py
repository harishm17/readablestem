from __future__ import annotations

from parsers_bench.symbols import SymbolStream, symbol_bag, symbol_sequence


def test_symbols_are_letters_digits_and_greek_in_order() -> None:
    assert symbol_sequence("v = d / t (m/s)") == "vdtms"
    assert symbol_sequence("Δt = 25") == "δt25"


def test_math_italic_letters_become_ascii_and_case_is_folded() -> None:
    assert symbol_sequence("\U0001d463\U0001d44e\U0001d463\U0001d454") == "vavg"
    assert symbol_sequence("ABc") == "abc"


def test_the_increment_sign_is_read_as_capital_delta() -> None:
    assert symbol_sequence("∆t") == symbol_sequence("Δt")


def test_bag_counts_repeats() -> None:
    assert symbol_bag("aab")["a"] == 2


def test_window_search_finds_an_exact_and_a_near_match() -> None:
    stream = SymbolStream(symbol_sequence("some words then v = d / t and more words"))
    assert stream.contains(symbol_sequence("v = d / t"))
    assert stream.contains(symbol_sequence("q = z / w")) is False


def test_one_wrong_symbol_in_ten_still_matches_but_two_wrong_in_five_do_not() -> None:
    stream = SymbolStream("zzzabcdefghixzzz")
    assert stream.contains("abcdefghij")  # ratio 0.9
    assert stream.contains("abxyz") is False  # ratio 0.6


def test_a_short_needle_is_not_checked() -> None:
    assert SymbolStream("abcdef").contains("ab") is False


def test_an_empty_stream_finds_nothing() -> None:
    assert SymbolStream("").contains("abc") is False
