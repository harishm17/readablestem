"""A hyphen or dot beside the formula in the padded crop is read as part of it ("(k-1)-regular").

The reading `(k-1)\\text{-}` has one more dash than the run's text layer `(k -1)`; the neighbour's
hyphen goes. A sign at the start of a reading stays: it may be the formula's own."""

from __future__ import annotations

import pytest

from readablestem.pipeline.equation_step import drop_neighbour_marks


@pytest.mark.parametrize(
    ("latex", "text", "expected"),
    [
        (r"(k-1)\text{-}", "(k −1)", r"(k-1)"),
        (r"(k-1)-", "(k −1)", r"(k-1)"),
        (r"(k-1)\,\text{-}", "(k −1)", r"(k-1)"),
        (r"\text{-}n\text{-}", "n", r"n"),
        (r"O(m\ell)\cdot", "O(m\u2113)", r"O(m\ell)"),
        (r"(k-1)", "(k −1)", r"(k-1)"),
        (r"x-", "x−", r"x-"),  # the text layer has the dash: it is the run's
        (r"-x", "x", r"-x"),  # a leading sign stays
        (r"a \cdot b \cdot", "a ⋅ b ⋅", r"a \cdot b \cdot"),
    ],
)
def test_a_mark_the_text_layer_lacks_is_dropped(latex, text, expected):
    assert drop_neighbour_marks(latex, text) == expected


def test_without_a_text_layer_nothing_is_dropped():
    assert drop_neighbour_marks(r"(k-1)\text{-}", None) == r"(k-1)\text{-}"
