"""The comparator's size behaviour, and its agreement with the registered scorer scripts.

The script `rw_notation_neutral.py` is hashed by registrations and stays as it is; the library
repeats its folding and the as-written speech step of `rw_spoken.py`, and these tests fail if
the two ever disagree.
"""

from __future__ import annotations

import importlib.util
import random
import re
import time
from pathlib import Path

import pytest

from readablestem.pipeline import math_canonical as mc

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "latex",
    [
        "a " * 20000,
        r"\alpha " * 10000,
        "x^" * 20000,
        "^{" * 5000 + "a" + "}" * 5000,
        "{x}+" * 10000,
        "x^{a}_{b}" * 5000,
        r"\text{ab}" * 10000,
    ],
    ids=["spaces", "macro-spaces", "carets", "nested", "braces", "scripts", "text"],
)
def test_canonical_latex_is_linear(latex: str) -> None:
    start = time.perf_counter()
    mc.canonical_latex(latex)
    assert time.perf_counter() - start < 1.0


def _slow_squeeze(s: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(s):
        if not ch.isspace():
            out.append(ch)
            continue
        before = "".join(out)
        rest = s[i + 1 :].lstrip()
        if (
            re.search(r"\\[A-Za-z]+$", before)
            and rest[:1].isascii()
            and rest[:1].isalpha()
            and not before.endswith(" ")
        ):
            out.append(" ")
    return "".join(out)


def test_squeeze_matches_the_simple_rule() -> None:
    rng = random.Random(0)
    pieces = ["\\", "a", "b", " ", "  ", "1", "{", "^", "\\alpha"]
    for _ in range(500):
        s = "".join(rng.choice(pieces) for _ in range(12))
        assert mc._squeeze(s) == _slow_squeeze(s), s


CORPUS = [
    r"\mathrm{in}+\operatorname*{lim}+{\rm d}x",
    r"\mathrm{a{b}}",
    r"\text{x}\mathbf{y}\,",
    r"{\rm  kg}\cdot\textnormal{m}",
    r"\mbox{if } x",
    r"\frac{a}{b}",
    "<x",
]


def test_fold_agrees_with_the_registered_scorer() -> None:
    neutral = _script("rw_notation_neutral")
    for latex in CORPUS:
        assert mc.fold_upright_words(latex) == neutral.fold(latex)
        folded = neutral.fold(latex)
        assert mc.latex_to_mathml(folded) == neutral._converted(folded)


def test_speech_agrees_with_the_spoken_scorer() -> None:
    spoken = _script("rw_spoken")
    for latex in [*CORPUS, r"\frac{"]:
        try:
            got = mc.speech_of(mc.latex_to_mathml(latex))
        except Exception:
            got = None
        assert got == spoken._pred_speech(latex), latex
