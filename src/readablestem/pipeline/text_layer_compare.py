# ruff: noqa: RUF001
# (the dotless i and j, and the increment sign TeX uses for Delta, are what is mapped here)
"""Two cross-checks of a transcription against the PDF's text layer that the symbol bag
cannot make: numbers as numbers, and capital against small letters.

`equation_step.text_layer_symbols_differ` compares letters, digits and Greek letters as a
case-folded multiset. That cannot see 36 written as 63, 9.81 as 98.1, 10^23 as 10^32 (the
same digits), or A for a, W/g for w/g, S for s (the same letters, case-folded). Both
slipped through in the review of the held-out run (A11, A12), and the same two checks were
added to the sister checker `stemcheck` after the same review of its own.

Both run only when the bags already agree, so they never repeat a symbol difference.

- `compare_numbers`: each number of the PDF text must be made of the MathML's numbers,
  each used once, joined in any order. A number is a run of digits with its decimal
  points inside it (`NUMBER`). The PDF's
  text glues a script to its base ("10^{23}" prints "1023", "v_0^2" prints "v20") and
  separates digits only by a space (a thin space in "1 000", or the numerator and the
  denominator of a flattened fraction), so a run of numbers split by spaces alone is one
  number there. So "v20" is v_0^2 (the numbers 0 and 2, in either order) and "1023" is
  10^23, but 36 against 63, 8.5 against 85 and 10^23 against 10^32 are differences. The
  search is bounded (`NUMBER_SEARCH_BUDGET`); past the bound the answer is "cannot tell",
  never a difference.
- `compare_case`: letters counted as (letter, is capital) on both sides; a letter whose
  capital and small counts differ is a difference. Small-caps fonts, which print small
  letters for capitals, are not recognised: the text layer has the capital letter, so
  nothing is flagged for them.

Pure functions of text and MathML; no model, no network. Ported from stemcheck
(checks/maths.py, `compare_numbers`, `compare_case`) and adapted to this module's
`_symbol_bag` conventions.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import defusedxml.ElementTree as DET

# A number: a run of digits with its decimal points inside it. A point in front of the
# digits (".5") is not part of the number: in a text layer it is more often a stray full
# stop or operator dot that a fraction's flattened pieces put next to a digit (".42" in
# "1 -13 .42 + 4": 9 of the 10 false flags on 1,748 gold equations printed by Chromium),
# and the symbol check already sees a "0" added to or dropped from ".5".
NUMBER = r"\d+(?:\.\d+)*"
_NUMBER = re.compile(NUMBER)
_PDF_NUMBER = re.compile(NUMBER + r"(?:\s+\d+(?:\.\d+)*)*")
# Steps of the search that makes each PDF number from the MathML's numbers. A real pair
# takes tens of steps; past the budget the pair is "cannot tell", never a difference.
NUMBER_SEARCH_BUDGET = 20_000
_NUMBER_TOKENS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
# Typesetting artefacts that are not letters: dotless i and j under an accent, TeX's
# capital Delta (the Adobe Glyph List maps "Delta" to U+2206 INCREMENT).
_DOTLESS = str.maketrans({"ı": "i", "ȷ": "j", "∆": "Δ"})


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


# ------------------------------------------------------------------------ numbers


@dataclass(frozen=True)
class NumberDiff:
    """The numbers one side cannot make of the other's. Both empty: the numbers agree."""

    pdf: tuple[str, ...]  # PDF numbers the MathML's numbers cannot make
    mathml: tuple[str, ...]  # MathML numbers left over

    @property
    def differ(self) -> bool:
        return bool(self.pdf or self.mathml)


def pdf_numbers(text: str) -> tuple[str, ...]:
    """The PDF text's numbers in reading order (runs split by spaces alone are joined)."""
    folded = unicodedata.normalize("NFKC", text)
    return tuple("".join(m.group().split()) for m in _PDF_NUMBER.finditer(folded))


def mathml_numbers(mathml: str) -> tuple[str, ...]:
    """The numbers of the MathML's tokens in document order (base before script). A
    decimal point written as a token of its own between two numbers joins them
    (`<mn>3</mn><mo>.</mo><mn>0</mn>` is 3.0)."""
    root = DET.fromstring(mathml, forbid_dtd=True)
    leaves = [
        unicodedata.normalize("NFKC", elem.text or "").strip()
        for elem in root.iter()
        if _local(elem.tag) in _NUMBER_TOKENS
    ]
    leaves = [leaf for leaf in leaves if leaf]
    out: list[str] = []
    k = 0
    while k < len(leaves):
        leaf = leaves[k]
        point = leaf == "." and 0 < k < len(leaves) - 1
        if point and out and _NUMBER.fullmatch(leaves[k - 1]) and _NUMBER.fullmatch(leaves[k + 1]):
            out[-1] = f"{out[-1]}.{leaves[k + 1]}"
            k += 2
            continue
        out.extend(_NUMBER.findall(leaf))
        k += 1
    return tuple(out)


def _tilings(number: str, pool: Counter[str], budget: list[int]) -> Iterator[Counter[str]]:
    """Each multiset of numbers from `pool` whose concatenation, in some order, is
    `number` (the PDF glues a script to its base, in either order: "v20" is v_0^2)."""
    if not number:
        yield Counter()
        return
    for part in sorted(pool):
        if pool[part] <= 0 or not number.startswith(part):
            continue
        budget[0] -= 1
        if budget[0] < 0:
            return
        pool[part] -= 1
        for rest in _tilings(number[len(part) :], pool, budget):
            rest[part] += 1
            yield rest
        pool[part] += 1


def compare_numbers(pdf: Sequence[str], mathml: Sequence[str]) -> NumberDiff | None:
    """Do the PDF's numbers and the MathML's numbers agree? None: the search ran out of
    its budget (cannot tell, so no difference is reported)."""
    p, h = Counter(pdf), Counter(mathml)
    same = p & h
    p, h = p - same, h - same
    if not p and not h:
        return NumberDiff((), ())
    order = sorted(p.elements(), key=lambda s: (-len(s), s))
    budget = [NUMBER_SEARCH_BUDGET]

    def solve(i: int, pool: Counter[str]) -> bool:
        if i == len(order):
            return not +pool
        before = Counter(pool)
        for used in _tilings(order[i], pool, budget):
            if solve(i + 1, before - used):
                return True
            if budget[0] < 0:
                return False
        return False

    if solve(0, Counter(h)):
        return NumberDiff((), ())
    if budget[0] < 0:
        return None
    # Which ones to name: drop each PDF number some of the MathML's numbers still make.
    left_pdf: list[str] = []
    pool = Counter(h)
    for number in order:
        made = next(_tilings(number, Counter(pool), [NUMBER_SEARCH_BUDGET]), None)
        if made is None:
            left_pdf.append(number)
        else:
            pool -= made
    return NumberDiff(tuple(left_pdf), tuple(sorted(pool.elements())))


def numbers_differ(text_layer: str | None, mathml: str) -> NumberDiff | None:
    """The number comparison of a text layer and a transcription, for a pair whose letter
    and digit bags already agree (the caller checks). None: no text layer, or no number
    in it, or the search could not tell."""
    if not text_layer:
        return None
    pdf = pdf_numbers(text_layer)
    if not pdf:
        return None
    return compare_numbers(pdf, mathml_numbers(mathml))


# --------------------------------------------------------------------------- case


@dataclass(frozen=True)
class CaseRow:
    letter: str  # case-folded
    pdf_capital: int
    pdf_small: int
    mathml_capital: int
    mathml_small: int


def case_bag(text: str) -> Counter[tuple[str, bool]]:
    """Letters as (case-folded letter, is capital), counted. Modifier letters (accents a
    LaTeX hat leaves in a text layer) are not letters; digits have no case."""
    out: Counter[tuple[str, bool]] = Counter()
    for ch in unicodedata.normalize("NFKC", text).translate(_DOTLESS):
        if ch.isalpha() and unicodedata.category(ch) != "Lm":
            capital = ch.isupper()
            for folded in ch.casefold():
                out[(folded, capital)] += 1
    return out


def compare_case(text_layer: str | None, mathml: str) -> tuple[CaseRow, ...]:
    """Letters whose capital and small counts differ between the text layer and the
    MathML (empty: the case agrees, or there is no text layer). Meant for a pair whose
    case-folded bags already agree (the caller checks)."""
    if not text_layer:
        return ()
    root = DET.fromstring(mathml, forbid_dtd=True)
    pdf = case_bag(text_layer)
    got = case_bag(" ".join(root.itertext()))
    rows = []
    for letter in sorted({f for f, _ in pdf} | {f for f, _ in got}):
        counts = (pdf[(letter, True)], pdf[(letter, False)])
        found = (got[(letter, True)], got[(letter, False)])
        if counts != found:
            rows.append(CaseRow(letter, counts[0], counts[1], found[0], found[1]))
    return tuple(rows)
