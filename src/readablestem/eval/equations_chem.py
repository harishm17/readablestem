# ruff: noqa: RUF001
# (look-alike bars, arrows and deltas are what this module unifies)
r"""Scorer "chem": is it the same chemistry, however the markup writes it? (secondary)

The primary scorer (`eval.equations`, ADR 0001, `exact_lenient`) compares the shape of the
MathML. For chemistry the shape is mostly how a formula was typed, not what it says. The
chemistry gold comes from two places and each spells a formula its own way:

* OpenStax (people): an `mtext` per run of element symbols, scripts on the group, upright;
* the authored document (our converter): one `<mi mathvariant="normal">` per letter, the
  arrow as an `<mi>`.

A transcription that writes `\ce{H2O}`, `\text{H}_2\text{O}`, `\mathrm{H_2O}` or `H_2O` is
the same water, and on the primary scorer it matches only the spelling of whichever gold it
happens to share. The development run of 2026-10-21 (docs/chemistry.md, "Scorer finding")
has 104 of the authored document's 108 equations right on this scorer and 4 on the primary.

This is a labelled secondary measure, reported beside the primary one and never in its place:
the primary numbers, the gate and the committed reports do not change, and it is computed for
chemistry gold only (the caller decides which gold is chemistry; nothing here is applied to
algebra or physics). It starts from the symbols scorer (`eval.equations_symbols`: the same
lenient normalisation, tables flattened, a script a marker in reading order, a script on a
bracketed group the same as one on its closing bracket) and forgives, in addition:

1. **Style.** Upright, italic and bold letters are one letter: an element symbol is upright
   whether the markup says `mathvariant="normal"`, `mtext`, or nothing (`H` in `<mi>` is
   italic by default), and the key terms of OpenStax set whole nuclide symbols in bold.
2. **Arrows.** A long and a short forward arrow (`→ ⟶ ➔`), and the equilibrium arrows
   (`⇌ ⇋ ⇄`), are one thing each. A forward arrow is not an equilibrium arrow.
3. **Look-alike signs.** The box-drawing bars OpenStax uses in cell notation (`│ ║`) are the
   vertical bar and the double bar; the increment sign `∆` is Greek capital delta; the
   dots a hydrate is joined with are already one dot (stream v2).
4. **Numbers cut in pieces.** OpenStax sets a long number as two `mn` (`0.2` `57` for 0.257,
   `2.2` `0` for 2.20): digits that follow a number with nothing between are joined. LaTeX's
   `96{,}485` is three atoms and the answer key's `96,485` one: a comma and exactly three
   digits after a number are joined.
5. **A bracket the span does not own.** A closing bracket at the end, or an opening bracket
   at the start, that nothing in the equation matches (the answer key took the sentence's
   parenthesis with the formula, `[Al(H2O)6]^{3+})`) is dropped on both sides, as the primary
   scorer already drops a final full stop.

What it does not forgive: a different count, charge, element, state symbol, coefficient or
arrow kind; a script that moved between sub and super; a missing or an extra part. `H2O` and
`H2O2` differ, `Fe^{2+}` and `Fe^{3+}` differ, `(aq)` and `(s)` differ, `->` and `<=>` differ.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from readablestem.eval import equations_symbols as sym
from readablestem.eval import equations_v2 as v2
from readablestem.eval.equations import MathMLError, latex_to_mathml

LABEL = "chem (secondary; chemistry gold only; the primary scorer is exact_lenient)"
SCORER = "chem"

_VARIANT = re.compile(r"^(?:normal|italic|bold|bold-italic|sans-serif|monospace):")

_FORWARD = "→"  # `⟶` is already mapped to this by stream v2
_EQUILIBRIUM = "⇌"
_MAP: dict[str, str] = {
    **dict.fromkeys("➔➜➝⟼", _FORWARD),
    **dict.fromkeys("⇋⇄⇆", _EQUILIBRIUM),
    "│": "|",  # box drawings light vertical (OpenStax cell notation)
    "║": "‖",  # box drawings double vertical
    "∆": "Δ",  # increment sign -> Greek capital delta
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_DIGITS = re.compile(r"\d+")
_OPEN = {"(": ")", "[": "]"}
_CLOSE = {")": "(", "]": "["}


def _simple(atom: str) -> str:
    return _MAP.get(_VARIANT.sub("", atom), _VARIANT.sub("", atom))


def _join_split_numbers(stream: list[str]) -> list[str]:
    """`0.2`, `57` -> `0.257`: digits that follow a number directly (module docstring, 4)."""
    out: list[str] = []
    for atom in stream:
        if out and _NUMBER.fullmatch(out[-1]) and _DIGITS.fullmatch(atom):
            out[-1] += atom
        else:
            out.append(atom)
    return out


def _join_thousands(stream: list[str]) -> list[str]:
    """`96`, `,`, `485` -> `96,485`: LaTeX `96{,}485` is three atoms, OpenStax's `<mn>` one."""
    out: list[str] = []
    i = 0
    while i < len(stream):
        if (
            stream[i] == ","
            and out
            and i + 1 < len(stream)
            and re.fullmatch(r"\d{1,3}(?:,\d{3})*", out[-1])
            and re.fullmatch(r"\d{3}", stream[i + 1])
        ):
            out[-1] += "," + stream[i + 1]
            i += 2
            continue
        out.append(stream[i])
        i += 1
    return out


def _ungroup_single_atoms(stream: list[str]) -> list[str]:
    """`<g> x </g>` -> `x`: a group of one atom is the atom (a number joined from pieces)."""
    out: list[str] = []
    for atom in stream:
        out.append(atom)
        if atom == sym.GROUP_CLOSE and len(out) >= 3 and out[-3] == sym.GROUP_OPEN:
            out[-3:] = [out[-2]]
    return out


def _drop_unowned_brackets(stream: list[str]) -> list[str]:
    """A final `)`/`]` or a first `(`/`[` that nothing in the stream matches (docstring, 5)."""
    out = list(stream)
    for end in (-1, 0):
        while out:
            atom = out[end]
            if atom in _CLOSE and end == -1:
                partner = _CLOSE[atom]
            elif atom in _OPEN and end == 0:
                partner = _OPEN[atom]
            else:
                break
            if out.count(atom) > out.count(partner):
                del out[end]
            else:
                break
    return out


def chem_of(source: str | ET.Element) -> list[str]:
    """The comparable chemistry stream of a MathML tree (module docstring)."""
    stream = [_simple(a) for a in sym.symbols_of(source)]
    joined = _join_thousands(_join_split_numbers(stream))
    return _drop_unowned_brackets(_ungroup_single_atoms(joined))


def chem_of_latex(latex: str) -> list[str]:
    return chem_of(latex_to_mathml(latex))


@dataclass
class ChemScore:
    exact: bool
    similarity: float
    gold_atoms: int
    pred_atoms: int
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def score_chem(pred: str | ET.Element, gold: str | ET.Element) -> ChemScore:
    """Score predicted MathML against gold MathML as chemistry streams. An unparseable
    prediction scores zero with the error, like the primary scorer."""
    gold_stream = chem_of(gold)
    try:
        pred_stream = chem_of(pred)
    except MathMLError as exc:
        return ChemScore(False, 0.0, len(gold_stream), 0, str(exc))
    largest = max(len(pred_stream), len(gold_stream))
    distance = v2.edit_distance(pred_stream, gold_stream)
    return ChemScore(
        exact=pred_stream == gold_stream,
        similarity=1.0 if largest == 0 else max(0.0, 1.0 - distance / largest),
        gold_atoms=len(gold_stream),
        pred_atoms=len(pred_stream),
    )


def score_chem_latex(pred_latex: str, gold: str | ET.Element) -> ChemScore:
    try:
        pred = latex_to_mathml(pred_latex)
    except MathMLError as exc:
        return ChemScore(False, 0.0, len(chem_of(gold)), 0, str(exc))
    return score_chem(pred, gold)


def missing_chem(gold: str | ET.Element) -> ChemScore:
    """Score for "no proposal": wrong, like a missing prediction in the primary scorer."""
    return ChemScore(False, 0.0, len(chem_of(gold)), 0, "no proposal")


def summarize(scores: Sequence[ChemScore]) -> dict:
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
