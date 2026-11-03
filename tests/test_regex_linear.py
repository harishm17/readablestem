"""Patterns that read text a person or a model wrote stay linear on adversarial input (CodeQL
py/polynomial-redos): a long run that never completes a match must not take quadratic time."""

from __future__ import annotations

import time

import pytest

from readablestem.pipeline import chem, flag_policy, validate

N = 50_000


def _fast(fn) -> None:
    start = time.perf_counter()
    fn()
    assert time.perf_counter() - start < 0.5


@pytest.mark.parametrize(
    "text",
    ["a-" * N, "<a" * N, "x" * N],
)
def test_alt_text_checks_are_linear(text: str) -> None:
    _fast(lambda: validate._FILE_EXTENSION.search(text))
    _fast(lambda: validate._URL_OR_MARKUP.search(text))


def test_ce_bracket_strip_is_linear() -> None:
    start = time.perf_counter()
    with pytest.raises(chem.CeError):
        chem.ce_to_latex("[" * N + "$")
    assert time.perf_counter() - start < 0.5


def test_flag_policy_tags_are_linear() -> None:
    _fast(lambda: flag_policy._TAGS.sub("", "<" * N))


@pytest.mark.parametrize(
    ("alt", "filename", "markup"),
    [
        ("diagram.png", True, False),
        ("my-scan_2.JPEG shows the cell", True, False),
        ("A cell, see <b>nucleus</b>", False, True),
        ("a < b and c > d", False, False),
        ("see https://example.org", False, True),
        ("A plot of x against time", False, False),
    ],
)
def test_alt_text_checks_keep_their_answers(alt: str, filename: bool, markup: bool) -> None:
    assert bool(validate._FILE_EXTENSION.search(alt)) is filename
    assert bool(validate._URL_OR_MARKUP.search(alt)) is markup


def test_ce_arrow_label_with_maths_still_reads() -> None:
    assert chem.ce_to_latex(r"N2 <=>[$\Delta$] NO")
