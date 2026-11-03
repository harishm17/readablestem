# ruff: noqa: RUF001, RUF002, RUF003
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


# ------------------------------------------------------------------------ report fields
#
# Two numbers a chemistry report carries beside `exact_chem`, never in its place (the
# primary scorer, the gate and the committed reports do not move; a report with no
# chemistry gold has neither field).
#
# * `chem_right_excl_times`: the gold marks a lone multiplication sign (`×` between two
#   numbers in a sentence) as an equation of its own, and the sentence around it already
#   says "times", so finding it or not changes nothing for a listener. It is left out of
#   the count of gold and of the right ones, and reported with how many were left out.
# * `silent_harm`: an aligned equation that is wrong on `exact_chem`, was not put to the
#   reviewer (`review` is not "required"), and is spoken differently from the gold. A
#   reading that only lacks what the printed page does not show (the atomic number of a
#   prefix nuclide symbol) is not harm. Counted per 100 chemistry gold equations.

LONE_TIMES = ["×"]
_PREFIX_SUB = re.compile(r"_ (?:<g> )?[−+]?\d+(?: </g>)? \^")


def is_chemistry_id(gold_id: str) -> bool:
    """The chemistry book's equations and the authored chemistry document's (the id says it)."""
    return "chemistry" in gold_id


def is_lone_times(gold: str | ET.Element) -> bool:
    """The gold equation is a multiplication sign and nothing else."""
    return chem_of(gold) == LONE_TIMES


def without_prefix_subscripts(stream: list[str]) -> list[str]:
    """A chemistry stream with the atomic number of every prefix nuclide symbol taken out."""
    return _PREFIX_SUB.sub("^", " ".join(stream)).split()


def lacks_only_what_the_page_lacks(pred: list[str], gold: list[str]) -> bool:
    """The reading is the gold less a prefix nuclide's atomic number: the printed page does
    not show it, so the reading is not wrong about anything the page says."""
    return pred == without_prefix_subscripts(gold) != gold


def _heard(text: str) -> str:
    """Speech compared as a listener would: case, commas and full stops do not count."""
    return re.sub(r"\s+", " ", text.lower().replace(",", " ").replace(".", " ")).strip()


def spoken_alike(pred: str, gold: str) -> bool:
    """The two read the same aloud (`pipeline.speech`). A reading that cannot be spoken
    counts as different."""
    from readablestem.pipeline.speech import mathml_to_speech

    try:
        return _heard(mathml_to_speech(pred)) == _heard(mathml_to_speech(gold))
    except Exception:  # speech is a heuristic over untrusted markup: no reading is "different"
        return False


def silent_harm(pred: str | None, gold: str, review: str | None) -> bool:
    """Wrong, not asked, and it changes what a listener hears (comment above).

    `pred` None is a reading nobody kept (a failed fix): a different kind of miss, not counted.
    """
    if pred is None or review == "required":
        return False
    try:
        pred_stream = chem_of(pred)
    except MathMLError:
        return False
    gold_stream = chem_of(gold)
    if pred_stream == gold_stream or lacks_only_what_the_page_lacks(pred_stream, gold_stream):
        return False
    return not spoken_alike(pred, gold)


def _rate(n: int, d: int) -> float | None:
    return None if d == 0 else n / d


def chem_gold_ids(items: Sequence[dict]) -> set[str]:
    """Every chemistry gold equation of the document items (aligned, missed or split)."""
    ids: set[str] = set()
    for item in items:
        found = [p["gold_id"] for p in item["pairs"]]
        found += item["missed_gold_ids"] + item.get("split_gold_ids", [])
        ids.update(g for g in found if is_chemistry_id(g))
    return ids


def chem_right_excl_times(items: Sequence[dict]) -> dict:
    """Chemically right on the chemistry gold, with the lone `×` left out of both sides.

    `n` and `right` are what `rate` is over; `lone_times` is how many were left out, and
    `chem_right` beside them keeps them in, so neither number replaces the other.
    """
    chem = chem_gold_ids(items)
    lone = {g for item in items for g in item.get("chem_lone_times_gold_ids", []) if g in chem}
    right = {
        p["gold_id"]
        for item in items
        for p in item["pairs"]
        if p["gold_id"] in chem and p.get("exact_chem")
    }
    kept = chem - lone
    return {
        "n": len(kept),
        "right": len(right & kept),
        "rate": _rate(len(right & kept), len(kept)),
        "lone_times": len(lone),
        "chem_right_n": len(chem),
        "chem_right": len(right),
        "chem_right_rate": _rate(len(right), len(chem)),
    }


def silent_harm_summary(items: Sequence[dict]) -> dict:
    """How many chemistry gold equations were silently harmed, and per 100 gold. Counts only:
    a report of a held-out run must not name items (the ids are `silent_harm` on the pairs of
    the kept `items.jsonl`)."""
    chem = chem_gold_ids(items)
    harmed = sorted(
        p["gold_id"]
        for item in items
        for p in item["pairs"]
        if p["gold_id"] in chem and p.get("silent_harm")
    )
    return {
        "n_gold": len(chem),
        "n": len(harmed),
        "per_100_gold": None if not chem else 100 * len(harmed) / len(chem),
    }


def document_metrics(items: Sequence[dict]) -> dict:
    """The report's `chemistry` block; empty when the items hold no chemistry gold."""
    if not chem_gold_ids(items):
        return {}
    return {
        "chem_right_excl_times": chem_right_excl_times(items),
        "silent_harm": silent_harm_summary(items),
    }
