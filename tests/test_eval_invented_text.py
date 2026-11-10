"""The source's side of the invented-content rules: normalisation, numbers, symbols, R."""

from __future__ import annotations

import pymupdf

from readablestem.eval.invented import text as T

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


# ------------------------------------------------------------------ numbers


# ------------------------------------------------------------------ symbols


# ------------------------------------------------------------------ the reference
