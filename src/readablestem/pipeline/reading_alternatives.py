r"""Other readings of a formula, as choices on its symbols (`core.types.ReadingAlternatives`).

A reading of handwriting can be doubtful in one symbol: `x` or `X`, `p` or `\rho`, `f(y)` or
`f^{-1}(y)`. A review can settle that with one click if it knows where the symbol is and what
else it may be. This module makes that data from any source of "what else":

- `from_reading` takes another reading of the same expression (a second reader's) and turns
  where the two differ into spans of the stored reading, with the other reading's LaTeX as the
  option;
- `resolve_doubts` takes named doubts (a symbol, which time it stands, other LaTeX for it), as
  a reader that lists its own doubts would give them.

Both find their spans in the LaTeX as stored, count LaTeX tokens (so `x` is never found inside
`\max`, and `f_i` is the three tokens `f`, `_`, `i`), and keep an option only when putting it in
place leaves LaTeX that converts: a choice that breaks the equation is no choice. At most
`MAX_DOUBTS_PER_READING` doubts of `MAX_OPTIONS_PER_DOUBT` options stay. Pure string work and
the LaTeX converter; no model call. Self-reported doubts were measured on handwriting
(docs/eval-handwriting.md, "Doubtful symbols: tried, not shipped") and were not selective enough
to ship, which is why the source of the options is left to the caller.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from readablestem.core.types import (
    MAX_DOUBTS_PER_READING,
    MAX_OPTIONS_PER_DOUBT,
    DoubtKind,
    ReadingAlternatives,
    SymbolAlternative,
)
from readablestem.pipeline import equation_step
from readablestem.pipeline.validate import has_errors

Display = Literal["block", "inline"]

# A LaTeX token: a command (`\rho`), an escaped character (`\%`), or one other character.
_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\S", re.DOTALL)
# Tokens that change the look of a formula and not what it says: not a difference between readings.
_SPACING = frozenset({"\\left", "\\right", "\\,", "\\;", "\\!", "\\ ", "\\displaystyle"})
MAX_DOUBTS_READ = 4 * MAX_DOUBTS_PER_READING  # doubts looked at, resolved or not
MAX_REGION_TOKENS = 6  # a longer stretch that differs is another expression, not a symbol


@dataclass(frozen=True)
class Doubt:
    """A symbol of a reading and the LaTeX it may be instead: `symbol` as it stands in the
    reading, `occurrence` 1 for the first time it stands, 2 for the second."""

    symbol: str
    occurrence: int
    options: Sequence[str]
    kind: DoubtKind = "other"


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def find_symbol(latex: str, symbol: str, occurrence: int) -> tuple[int, int] | None:
    r"""The span `(start, end)` of the `occurrence`-th time `symbol` stands in `latex`, counted in
    LaTeX tokens, or None. Matches do not overlap."""
    want = [token for token, _, _ in _tokens(symbol)]
    if not want or occurrence < 1:
        return None
    have = _tokens(latex)
    seen = 0
    at = 0
    while at + len(want) <= len(have):
        if [token for token, _, _ in have[at : at + len(want)]] == want:
            seen += 1
            if seen == occurrence:
                return have[at][1], have[at + len(want) - 1][2]
            at += len(want)
        else:
            at += 1
    return None


def _convertible(latex: str, display: Display) -> bool:
    mathml, checks = equation_step._convert(latex, display)
    return mathml is not None and not has_errors(checks)


def _finish(
    latex: str, found: list[tuple[int, int, DoubtKind, Sequence[str]]], display: Display
) -> ReadingAlternatives | None:
    """Spans with candidate options to the stored value: options that are empty, the symbol
    itself, a repeat or not convertible out, spans that overlap an earlier one out, the counts
    bounded."""
    kept: list[SymbolAlternative] = []
    for start, end, kind, candidates in sorted(found, key=lambda f: f[0]):
        if kept and start < kept[-1].end:
            continue
        current = latex[start:end]
        options: list[str] = []
        for option in candidates[: 2 * MAX_OPTIONS_PER_DOUBT]:
            option = option.strip()
            if not option or option == current or option in options:
                continue
            if not _convertible(latex[:start] + option + latex[end:], display):
                continue
            options.append(option)
            if len(options) == MAX_OPTIONS_PER_DOUBT:
                break
        if options:
            kept.append(
                SymbolAlternative(start=start, end=end, symbol=current, kind=kind, options=options)
            )
    if not kept:
        return None
    return ReadingAlternatives(latex=latex, symbols=kept[:MAX_DOUBTS_PER_READING])


def resolve_doubts(
    latex: str, doubts: Sequence[Doubt], display: Display = "inline"
) -> ReadingAlternatives | None:
    """Named doubts about `latex` (the formula as stored) as spans of it, or None when none
    survives. A doubt whose symbol is not found is dropped."""
    found: list[tuple[int, int, DoubtKind, Sequence[str]]] = []
    for doubt in doubts[:MAX_DOUBTS_READ]:
        span = find_symbol(latex, doubt.symbol, doubt.occurrence)
        if span is not None:
            found.append((span[0], span[1], doubt.kind, doubt.options))
    return _finish(latex, found, display)


def _kind(current: str, other: str) -> DoubtKind:
    """Why two stretches differ, for the wording of a choice."""
    a, b = current.strip(), other.strip()
    if a.lower() == b.lower():
        return "case"
    scripts = ("^", "_")
    if any(s in a for s in scripts) != any(s in b for s in scripts):
        return "script"
    greek = [x.startswith("\\") and x[1:].isalpha() for x in (a, b)]
    if greek[0] != greek[1] and len(_tokens(a)) == len(_tokens(b)) == 1:
        return "greek_latin"
    if len(a) == len(b) == 1 and a.isalnum() and b.isalnum() and a.isdigit() != b.isdigit():
        return "digit_letter"
    return "other"


def from_reading(latex: str, other: str, display: Display = "inline") -> ReadingAlternatives | None:
    """Where `other`, another reading of the same expression, differs from `latex` (the stored
    reading), as choices: each differing stretch of `latex` with the other reading's LaTeX for it
    as the option. None when they say the same, when more than `MAX_DOUBTS_PER_READING` stretches
    differ or one is longer than `MAX_REGION_TOKENS` tokens (then the two are not one reading
    with a doubtful symbol), or when no option survives the convertibility check. Spacing
    commands and nothing else are ignored in the comparison."""
    ours = [t for t in _tokens(latex) if t[0] not in _SPACING]
    theirs = [t for t in _tokens(other) if t[0] not in _SPACING]
    if not ours or not theirs:
        return None
    matcher = difflib.SequenceMatcher(
        None, [t[0] for t in ours], [t[0] for t in theirs], autojunk=False
    )
    regions = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if not regions or len(regions) > MAX_DOUBTS_PER_READING:
        return None
    found: list[tuple[int, int, DoubtKind, Sequence[str]]] = []
    for _, i1, i2, j1, j2 in regions:
        if i1 == i2 or j1 == j2:  # an insertion or a deletion: take the symbol beside it too
            if i1 > 0 and j1 > 0:
                i1, j1 = i1 - 1, j1 - 1
            elif i2 < len(ours) and j2 < len(theirs):
                i2, j2 = i2 + 1, j2 + 1
            else:
                return None
        if i2 - i1 > MAX_REGION_TOKENS or j2 - j1 > MAX_REGION_TOKENS:
            return None
        start, end = ours[i1][1], ours[i2 - 1][2]
        option = other[theirs[j1][1] : theirs[j2 - 1][2]]
        found.append((start, end, _kind(latex[start:end], option), [option]))
    return _finish(latex, found, display)
