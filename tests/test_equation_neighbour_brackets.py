"""A padded inline crop can show the parenthesis next to the formula, and the model writes it.

"(x = 0.9)" in a sentence: the run is `x = 0.9`, the crop's 2 pt of padding take in half of each
parenthesis, and the reading is `(x = 0.9)` or `x = 0.9\\ (`. On the development set 32 of the
span failures were a pair of parentheses the text layer of the run does not have. The text layer
says what the run holds; a bracket the reading has and the text layer lacks is the neighbour's."""

from __future__ import annotations

import pytest

from readablestem.pipeline.equation_step import drop_neighbour_brackets


@pytest.mark.parametrize(
    ("latex", "text", "expected"),
    [
        (r"(\beta_r)", "βr", r"\beta_r"),
        (r"\left(\gamma = -0.9\right)", "\u03b3 = \u22120.9", r"\gamma = -0.9"),
        (r"(cm/s)", "cm/s", r"cm/s"),
        (r"K\ (", "K", r"K"),
        (r"q = 0\ (", "q = 0", r"q = 0"),
        (r"x_1)", "x1", r"x_1"),
        (r"p = 60)", "p = 60", r"p = 60"),
        (r"(x, y)", "x, y", r"(x, y)"),  # a tuple keeps its pair
        (r"(a+b)^2", "a+b2", r"(a+b)^2"),  # the pair does not enclose the run
        (r"(x = 1)", "(x = 1)", r"(x = 1)"),  # the text layer has them: they are the run's
        (r"f(x)", "f(x)", r"f(x)"),
        (r"f(x)", "fx", r"f(x)"),  # balanced inside: not an edge
        (r"(x", "(x", r"(x"),
    ],
)
def test_a_bracket_the_text_layer_lacks_is_dropped(latex, text, expected):
    assert drop_neighbour_brackets(latex, text) == expected


def test_without_a_text_layer_nothing_is_dropped():
    assert drop_neighbour_brackets(r"(x = 1)", None) == r"(x = 1)"
