"""A hyphen at the end of a line: a broken word loses it, a compound keeps it.

"veloc-" / "ity" is one word; "well-" / "known" is a compound that would read "wellknown"
without its hyphen. `pipeline/hyphens.py` tells them apart with a list of English words
(`pipeline/data/words-en.txt.gz`, SCOWL; source and licence in `pipeline/data/README.md`).
"""

from __future__ import annotations

import gzip
import hashlib
from importlib import resources

import pymupdf
import pytest

from readablestem.pipeline import hyphens
from readablestem.pipeline.layout import _line_break, analyze

WORDS_SHA256 = "1e2e548ab2be7d641a24470ec121034f6d9b03f36df8a0b66fbe2ecaddbfbb8e"


def test_the_word_list_is_the_documented_file():
    data = resources.files("readablestem.pipeline").joinpath("data/words-en.txt.gz").read_bytes()
    assert hashlib.sha256(data).hexdigest() == WORDS_SHA256
    words = gzip.decompress(data).decode("utf-8").split("\n")
    assert words[-1] == "" and len(words) - 1 == 63_849  # the README says so
    assert all(w.isascii() and w.islower() and w.isalpha() and len(w) >= 2 for w in words[:-1])
    assert words[:-1] == sorted(words[:-1])


@pytest.mark.parametrize(
    ("before", "after", "joined"),
    [
        ("a constant veloc-", "ity until", "a constant velocity until"),  # a broken word
        ("the semi-", "conductor", "the semiconductor"),
        ("a non-", "linear term", "a nonlinear term"),
        ("the photo-", "electric effect", "the photoelectric effect"),
        ("eigen-", "vector of", "eigenvector of"),  # technical: neither piece is a word
    ],
)
def test_a_hyphen_inside_a_word_goes(before, after, joined):
    text, separator = _line_break(before, after)
    assert text + separator + after == joined


@pytest.mark.parametrize(
    ("before", "after", "joined"),
    [
        ("the well-", "known result", "the well-known result"),
        ("a cross-", "section of", "a cross-section of"),
        ("the self-", "consistent field", "the self-consistent field"),
        ("a mass-energy-", "balance", "a mass-energy-balance"),  # the piece next to the break
        ("an x-", "ray beam", "an x-ray beam"),  # a single letter before the hyphen
        ("the T-", "shirt", "the T-shirt"),
        ("the Newton-", "Raphson method", "the Newton-Raphson method"),  # a capital: as before
        # A known limit: both pieces are words and the list lacks the joined technical word.
        ("an anti-", "derivative", "an anti-derivative"),
    ],
)
def test_a_hyphen_in_a_compound_stays_without_a_space(before, after, joined):
    text, separator = _line_break(before, after)
    assert text + separator + after == joined


def test_a_soft_hyphen_always_goes_and_other_line_ends_are_joined_with_a_space():
    assert _line_break("a soft­", "hyphen") == ("a soft", "")
    assert _line_break("ends here", "and goes on") == ("ends here", " ")
    assert _line_break("a range 5-", "7 m") == ("a range 5-", " ")  # no letter before the hyphen
    assert _line_break("a dash \u2013", "like this") == ("a dash \u2013", " ")


def test_a_missing_word_list_joins_as_it_always_did(monkeypatch):
    monkeypatch.setattr(hyphens, "words", lambda: frozenset())
    assert _line_break("the well-", "known result") == ("the well", "")
    assert _line_break("an x-", "ray") == ("an x-", "")  # a single letter needs no list


def test_the_rule_in_a_whole_page():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    lines = [
        "The cart keeps a constant veloc-",
        "ity, which is a well-",
        "known result for a cross-",
        "section of the ramp in an x-",
        "ray image of the semi-",
        "conductor wafer.",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + 14 * i), line, fontsize=11)
    (paragraph,) = analyze(doc.tobytes()).elements
    assert paragraph.text == (
        "The cart keeps a constant velocity, which is a well-known result for a "
        "cross-section of the ramp in an x-ray image of the semiconductor wafer."
    )
