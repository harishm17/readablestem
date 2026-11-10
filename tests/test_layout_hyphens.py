"""A hyphen at the end of a line: a broken word loses it, a compound keeps it.

"veloc-" / "ity" is one word; "well-" / "known" is a compound that would read "wellknown"
without its hyphen. `pipeline/hyphens.py` tells them apart with a list of English words
(`pipeline/data/words-en.txt.gz`, SCOWL; source and licence in `pipeline/data/README.md`).
"""

from __future__ import annotations

from readablestem.pipeline import hyphens
from readablestem.pipeline.layout import _line_break


def test_a_soft_hyphen_always_goes_and_other_line_ends_are_joined_with_a_space():
    assert _line_break("a soft­", "hyphen") == ("a soft", "")
    assert _line_break("ends here", "and goes on") == ("ends here", " ")
    assert _line_break("a range 5-", "7 m") == ("a range 5-", " ")  # no letter before the hyphen
    assert _line_break("a dash \u2013", "like this") == ("a dash \u2013", " ")


def test_a_missing_word_list_joins_as_it_always_did(monkeypatch):
    monkeypatch.setattr(hyphens, "words", lambda: frozenset())
    assert _line_break("the well-", "known result") == ("the well", "")
    assert _line_break("an x-", "ray") == ("an x-", "")  # a single letter needs no list
