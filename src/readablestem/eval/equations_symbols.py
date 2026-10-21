# ruff: noqa: RUF001
# (look-alike bars and brackets are what this module unifies)
r"""Scorer "symbols": are the right symbols there, in the right reading order? (secondary)

Two scorers already exist. The primary one (`eval.equations`, ADR 0001, `exact_lenient`)
compares normalised MathML trees. Stream v2 (`eval.equations_v2`) compares a stream of
atoms but keeps the tree's structure as open and close markers. The held-out analysis
(2026-10-20) found that the structure markers are where the remaining scorer artefacts sit
once the symbols are right:

* tables: the answer key writes a matrix, a determinant or an aligned derivation as an
  `mtable` with one cell per row or with empty spacer cells (`<mtd/>`) between the columns;
  a transcription writes the same rows with other cell boundaries;
* brackets: OpenStax writes `[H3O+]_0` as a subscript on the whole bracketed group, a
  transcription as a subscript on the closing bracket;
* grouping: `mrow`s that wrap one slot's content differently.

This module is a second secondary measure, reported beside the primary one and stream v2,
and labelled as such (`LABEL`). It never replaces either; the primary numbers, the gate and
the committed reports do not change. It answers a narrower question than "is the markup the
same": it says whether a reader of the two outputs would see the same symbols in the same
order, with the same fractions, roots, subscripts and superscripts. What it forgives, on top
of everything stream v2 forgives (the same lenient normalisation; `mi`/`mn`/`mtext`/`mo` as
one token type; every token split into atoms, so a signed number `-5` is two atoms whether
the answer key packed it into one `<mn>` or not; look-alike dashes, mu, primes, degrees;
a decimal point as its own token), and in addition to the bar, bracket and angle variants
unified here:

1. **Tables are flattened.** Rows and cells disappear: the stream of every non-empty cell, row
   by row, left to right. Empty cells (spacer columns) add nothing. A matrix and the same
   numbers written as aligned lines give the same stream; so do two matrices that differ
   only in where the cells are cut. The price: a 2x3 matrix and a 3x2 matrix with the same
   entries in row-major order are equal here. That is why this is a secondary measure.
2. **A script is a marker, not a container.** `base _ sub ^ sup` is written in the stream in
   reading order whether the script hangs on a single symbol or on a bracketed group that
   ends in that symbol. A slot holding more than one atom is still a group (`<g>...</g>`),
   so `x^{ab}` and `x^a b` differ, but the `mrow`s that make the group do not matter.
3. **Under and over are scripts.** `\lim_{x \to 0}` (limits) and `\lim_{x \to 0}` written as
   a subscript are the same symbols.

On the development sets (docs/eval-symbols note) every pair stream v2 calls exact is exact
here too; the tests check the cases that matter. The score is `exact` (equal
streams) and a similarity (1 - edit distance / longer stream), and `credit_split` and
`credit_merged` of `equations_v2` apply to these streams unchanged.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from readablestem.eval import equations_v2 as v2
from readablestem.eval.equations import MathMLError, latex_to_mathml, normalize

LABEL = "symbols (secondary; tables flattened; the primary scorer is exact_lenient)"
SCORER = "symbols"

GROUP_OPEN = "<g>"
GROUP_CLOSE = "</g>"
_MARKERS = {GROUP_OPEN, GROUP_CLOSE}

# Bars, brackets and angle brackets that are one symbol written with several code points.
_EXTRA_MAP: dict[str, str] = {
    **dict.fromkeys("∣❘｜", "|"),
    **dict.fromkeys("∥‖", "‖"),
    **dict.fromkeys("〈〈", "⟨"),
    **dict.fromkeys("〉〉", "⟩"),
}

# Elements that put a symbol above, below or beside a base.
_SUB = {"msub", "munder"}
_SUP = {"msup", "mover"}
_SUBSUP = {"msubsup", "munderover"}
_SCRIPT_TAGS = _SUB | _SUP | _SUBSUP


def _atoms(elem: ET.Element) -> list[str]:
    return [_EXTRA_MAP.get(a, a) for a in v2._leaf(elem)]


def _plain(stream: list[str]) -> list[str]:
    """The atoms of a stream, without group markers."""
    return [a for a in stream if a not in _MARKERS]


def _slot(elem: ET.Element) -> list[str]:
    """The stream of an element that fills a slot: a group when it holds several atoms."""
    stream = _walk(elem)
    return [GROUP_OPEN, *stream, GROUP_CLOSE] if len(_plain(stream)) > 1 else stream


def _walk(elem: ET.Element) -> list[str]:
    tag = elem.tag
    if tag in {"tok", "mo"}:
        return _atoms(elem)
    children = list(elem)
    if tag == "mtable":
        out: list[str] = []
        for row in children:
            cells = list(row) if row.tag == "mtr" else [row]
            for cell in cells:
                out += _walk(cell)
        return out
    if tag in _SCRIPT_TAGS and len(children) in (2, 3):
        base = _walk(children[0])
        if tag in _SUP and len(children) == 2:
            bare = _plain(_walk(children[1]))
            if bare and all(a == v2._DEGREE or a in v2._PRIMES for a in bare):
                return [*base, *bare]  # 90^\circ is 90°, f^\prime is f′
        out = list(base)
        if tag in _SUB:
            out += ["_", *_slot(children[1])]
        elif tag in _SUP:
            out += ["^", *_slot(children[1])]
        else:
            out += ["_", *_slot(children[1]), "^", *_slot(children[2])]
        return out
    if tag == "mfrac" and len(children) == 2:
        return ["<frac>", *_slot(children[0]), "|", *_slot(children[1]), "</frac>"]
    if tag == "msqrt":
        return ["<sqrt>", *[a for c in children for a in _walk(c)], "</sqrt>"]
    if tag == "mroot" and len(children) == 2:
        return ["<root>", *_slot(children[0]), "|", *_slot(children[1]), "</root>"]
    out = []
    for child in children:
        out += _walk(child)
    return out


def symbols_of(source: str | ET.Element) -> list[str]:
    """The comparable symbol stream of a MathML tree (module docstring)."""
    root = normalize(source, lenient=True)
    return v2._join_decimals(_walk(root))


def symbols_of_latex(latex: str) -> list[str]:
    return symbols_of(latex_to_mathml(latex))


@dataclass
class SymbolScore:
    exact: bool
    similarity: float
    gold_atoms: int
    pred_atoms: int
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def score_symbols(pred: str | ET.Element, gold: str | ET.Element) -> SymbolScore:
    """Score predicted MathML against gold MathML as symbol streams. An unparseable
    prediction scores zero with the error, like the primary scorer."""
    gold_stream = symbols_of(gold)
    try:
        pred_stream = symbols_of(pred)
    except MathMLError as exc:
        return SymbolScore(False, 0.0, len(gold_stream), 0, str(exc))
    largest = max(len(pred_stream), len(gold_stream))
    distance = v2.edit_distance(pred_stream, gold_stream)
    return SymbolScore(
        exact=pred_stream == gold_stream,
        similarity=1.0 if largest == 0 else max(0.0, 1.0 - distance / largest),
        gold_atoms=len(gold_stream),
        pred_atoms=len(pred_stream),
    )


def score_symbols_latex(pred_latex: str, gold: str | ET.Element) -> SymbolScore:
    try:
        pred = latex_to_mathml(pred_latex)
    except MathMLError as exc:
        return SymbolScore(False, 0.0, len(symbols_of(gold)), 0, str(exc))
    return score_symbols(pred, gold)


def missing_symbols(gold: str | ET.Element) -> SymbolScore:
    """Score for "no proposal": wrong, like a missing prediction in the primary scorer."""
    return SymbolScore(False, 0.0, len(symbols_of(gold)), 0, "no proposal")


def summarize(scores: Sequence[SymbolScore]) -> dict:
    """Aggregate secondary scores; rates are over all items, errors included."""
    n = len(scores)
    if n == 0:
        return {"scorer": SCORER, "label": LABEL, "n": 0}
    return {
        "scorer": SCORER,
        "label": LABEL,
        "n": n,
        "exact": sum(s.exact for s in scores) / n,
        "mean_similarity": sum(s.similarity for s in scores) / n,
        "errors": sum(s.error is not None for s in scores),
    }
