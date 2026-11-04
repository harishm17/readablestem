"""The spoken-equal scorer: LaTeX that prints alike is spoken alike, and nothing that prints
differently becomes equal.

`scripts/rw_spoken.py` scores the primary of fresh5; `math_canonical.speakable_latex` writes each
print one way before the speech rules read it. The pairs are modelled on the classes found in the
recorded dev, fresh2-4 and held-out scores (synthetic, not copied from any paper).
"""

from __future__ import annotations

import importlib.util
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


spoken = _script("rw_spoken")


def right(pred: str, gold: str) -> bool:
    return spoken.spoken_exact(pred, gold, False)


# (gold, prediction): the same print, spelled two ways.
SAME_PRINT = [
    # upright words: spelled out against read as a word
    (r"\Gamma_\mathrm{in}", r"\Gamma_\text{in}"),
    (r"\rho_{\rm out} < \rho_{\rm in}", r"\rho_{\text{out}} < \rho_{\text{in}}"),
    (r"{\rm tr}_E \rho", r"\operatorname{tr}_E\rho"),
    (r"S_{\mathrm{gauge\;theory}}[A]", r"S_\text{gauge theory}[A]"),
    # operator names
    (r"\theta_{\min} \to 0", r"\theta_{\text{min}} \rightarrow 0"),
    (r"\theta_{\text{max}}", r"\theta_{\max}"),
    (r"{\rm exp}(x^\beta)", r"\exp(x^{\beta})"),
    (r"N_0 = \text{max}[0, z]", r"N_0 = \max[0, z]"),
    (r"\sigma = 0.1~\text{min}(d)", r"\sigma = 0.1\ \min(d)"),
    # gold \textbf in maths keeps its bold
    (r"F_\alpha(\textbf{R})", r"F_\alpha(\mathbf{R})"),
    (r"\eta_\textbf{r}", r"\eta_{\mathbf{r}}"),
    (r"{\bf R}_k", r"\mathbf{R}_k"),
    # sized delimiters
    (r"\bigl(\sqrt{a} - \sqrt{b}\bigr)^2 > 2", r"\left(\sqrt{a} - \sqrt{b}\right)^2 > 2"),
    (r"\biggl \langle x \biggr \rangle", r"\left\langle x \right\rangle"),
    (r"\Big\{ x \Big\}", r"\left\{ x \right\}"),
    # ellipses
    (r"i=1,...,S", r"i = 1, \ldots, S"),
    # the rest of the recorded classes
    (r"N_{j \backslash 0}", r"N_{j\setminus 0}"),
    (r"\epsilon^{^{(i)}}(k)", r"\epsilon^{(i)}(k)"),
    (r"a^{^{\rm (i)}}", r"a^{(\mathrm{i})}"),
    (r"\text{B}19", r"\text{B19}"),
    (r"\lambda_\text{1rsb}^{\text{$1$r}}", r"\lambda_\text{1rsb}^{\text{1r}}"),
    (
        r"p = \begin{cases} a, & x \\[6pt] b, & y \end{cases}",
        r"p = \begin{cases} a, & x \\ b, & y \end{cases}",
    ),
    (r"\Vert \widehat{\underline{w}}\Vert", r"\|\underline{\widehat{w}}\|"),
    (r"l^\prime + l^{\prime \prime}", r"l' + l''"),
    (r"u_\theta '", r"u'_\theta"),
]


@pytest.mark.parametrize(("gold", "pred"), SAME_PRINT)
def test_same_print_is_right(gold: str, pred: str) -> None:
    assert right(pred, gold)
    assert right(gold, pred)


# (gold, prediction): prints differently, so a reading like this is wrong.
DIFFERENT_PRINT = [
    (r"\theta_{\min}", r"\theta_{\max}"),
    (r"\Gamma_\mathrm{in}", r"\Gamma_\mathrm{out}"),
    (r"\sin x", r"\cos x"),
    (r"\log n", r"\ln n"),
    # a limit sits under the word in display style: not the same as a subscript text
    (r"\min_x f(x)", r"\text{min}_x f(x)"),
    (r"\lim_{x \to 0} f", r"\text{lim}_{x \to 0} f"),
    # bold against plain; bold letter against another
    (r"F_\alpha(\textbf{R})", r"F_\alpha(R)"),
    (r"\textbf{R}", r"\mathbf{S}"),
    # sizes and delimiters: another bracket is another formula
    (r"\bigl(a\bigr)", r"[a]"),
    (r"\bigl(a\bigr)", r"\bigl\{a\bigr\}"),
    # ellipsis kinds and counts
    (r"a_1, ..., a_n", r"a_1, a_n"),
    (r"i = 1, \ldots, S", r"i = 1, \ldots, M"),
    # backslash is not a slash
    (r"A \backslash B", r"A / B"),
    # scripts: where a script sits, which script, how many primes
    (r"x^{*}", r"x*"),
    (r"x'", r"x''"),
    (r"u_\theta'", r"u_{\theta'}"),
    (r"x^{(i)}", r"x_{(i)}"),
    (r"x^{^{(i)}}", r"x_{(i)}"),
    (r"\widehat{\underline{w}}", r"\underline{w}"),
    (r"\widehat{\underline{w}}", r"\overline{\widehat{w}}"),
    # word and digits
    (r"\text{B}19", r"\text{B}20"),
    (r"\text{$1$r}", r"\text{2r}"),
    # a line gap is not a row
    (r"\begin{cases} a \\[6pt] b \end{cases}", r"\begin{cases} a \end{cases}"),
    # recorded wrong readings: a lookalike glyph, a relation, an extra unit
    (r"\upsilon(x)", r"v(x)"),
    (r"n_k \asymp n_l", r"n_k \succsim n_l"),
    (r"\Delta t = 1", r"\Delta t = 1\ \text{h}"),
    (r"-2/3", r"-\frac{2}{3}"),
]


@pytest.mark.parametrize(("gold", "pred"), DIFFERENT_PRINT)
def test_different_print_is_not_right(gold: str, pred: str) -> None:
    assert not right(pred, gold)
    assert not right(gold, pred)


def test_a_pair_right_as_written_stays_right() -> None:
    # spoken alike without any folding (letters read one by one on both sides)
    assert right(r"\mathrm{Diam}(K)", r"Diam(K)")
    assert right(r"\boldsymbol{x}", r"\mathbf{x}")


def test_the_scorer_never_turns_a_right_pair_wrong() -> None:
    assert spoken.spoken_exact(r"\text{nonsense}", r"x", True) is True
    assert spoken.spoken_exact(None, r"x", False) is False
    assert spoken.spoken_exact("", r"x", False) is False


def test_unreadable_never_matches() -> None:
    assert not right(r"\frac{", r"\frac{")


@pytest.mark.parametrize(
    "latex",
    [
        r"\text{if } x > 0",
        r"\mathrm{x_1} + y",
        r"\mathrm{\alpha}",
        r"\operatorname*{lim}_{n} a_n",
        r"\min_x f",
        r"a_1, \ldots, a_n",
        r"\left(x\right)",
    ],
)
def test_fold_leaves_what_it_has_no_rule_for(latex: str) -> None:
    assert mc.speakable_latex(latex) == latex


def test_fold_is_idempotent() -> None:
    for gold, pred in [*SAME_PRINT, *DIFFERENT_PRINT]:
        for latex in (gold, pred):
            once = mc.speakable_latex(latex)
            assert mc.speakable_latex(once) == once, latex


def test_repair_changes_only_the_converter_gaps() -> None:
    assert mc.repair_converter_gaps(r"\bigl(\textbf{R}\bigr)") == r"(\mathbf{R})"
    assert mc.repair_converter_gaps(r"\mathrm{in} \min ...") == r"\mathrm{in} \min ..."


@pytest.mark.parametrize(
    "latex",
    [
        r"\mathrm{ab}" * 5000,
        r"\text{$a$b}" * 5000,
        r"x_{a} '" * 5000,
        r"\bigl(" * 5000,
        "..." * 5000,
        r"\min " * 5000,
        r"^{^{" * 3000,
        r"\text{(" * 3000,
        r"\\[" * 3000,
    ],
    ids=["mathrm", "dollar", "prime", "sizes", "dots", "ops", "scripts", "parens", "rows"],
)
def test_speakable_latex_is_linear(latex: str) -> None:
    import time

    start = time.perf_counter()
    mc.speakable_latex(latex)
    assert time.perf_counter() - start < 1.0
