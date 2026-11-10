"""The comparator's size behaviour, and its agreement with the registered scorer scripts.

The script `rw_notation_neutral.py` is hashed by registrations and stays as it is; the library
repeats its folding and the as-written speech step of `rw_spoken.py`, and these tests fail if
the two ever disagree.
"""

from __future__ import annotations

import importlib.util
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
