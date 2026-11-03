"""Patterns that read text a person or a model wrote stay linear on adversarial input (CodeQL
py/polynomial-redos): a long run that never completes a match must not take quadratic time."""

from __future__ import annotations

import random
import re
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
    _fast(lambda: validate._has_file_name(text))
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
    assert validate._has_file_name(alt) is filename
    assert bool(validate._URL_OR_MARKUP.search(alt)) is markup


def test_ce_arrow_label_with_maths_still_reads() -> None:
    assert chem.ce_to_latex(r"N2 <=>[$\Delta$] NO")


# The pattern `_has_file_name` replaced; it must give the same answer on any text.
_OLD_FILE_EXTENSION = re.compile(
    r"\b[\w\-]+\.(png|jpe?g|gif|svg|bmp|tiff?|webp|heic|pdf|eps|emf|wmf|ai|psd)\b", re.I
)


def test_file_name_scan_agrees_with_the_old_pattern() -> None:
    rng = random.Random(110)
    alphabet = ["a", "Z", "7", "_", "-", ".", " ", ",", "é", "png", "JPEG", "jpe", "tif", "ai", "x"]
    for _ in range(200_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        assert validate._has_file_name(text) is bool(_OLD_FILE_EXTENSION.search(text)), text


# The pattern `_is_file_stem` replaced.
_OLD_FILE_STEM = re.compile(
    r"^(img|dsc|dscn|dcim|pxl|image|fig|figure|scan|screenshot|pic|photo)[\W_]*\d[\w\-]*$"
    r"|^\w+_\w+$",
    re.I,
)


def test_file_stem_check_agrees_with_the_old_pattern() -> None:
    rng = random.Random(1102)
    alphabet = ["IMG", "dsc", "n", "Figure", "scan", "pic", "_", "-", ".", "\n", "7", "a", "é", "!"]
    for _ in range(200_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))
        assert validate._is_file_stem(text) is bool(_OLD_FILE_STEM.match(text)), repr(text)


@pytest.mark.parametrize("text", ["a_" * N + "!", "dsc0" + "0" * N + "!", "IMG" + "_" * N])
def test_file_stem_check_is_linear(text: str) -> None:
    _fast(lambda: validate._is_file_stem(text))
