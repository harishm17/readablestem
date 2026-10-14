"""Notation found in a converted document, offered to the instructor as course entries
(T3-12), so nobody has to type `\\mathbf{d}` to teach the course its symbols.

A suggestion is a notation key (`core.notation.canonical_symbol`: "bold d", "double-struck
R", "v with an arrow") the document actually uses, how many equations use it, where it
first appears and a guess at what it means. Sources, all of them already in the stored
result:

- the LaTeX of every equation that is used in the file (not a rejected or failed one): the
  style commands that mark a symbol (`\\mathbf{d}`, `\\boldsymbol{\\omega}`, `\\mathbb{R}`,
  `\\mathcal{L}`...), the accents (`\\vec{v}`, `\\hat{x}`) and the operator symbols `\\nabla`
  and `\\hbar`;
- styled letters in the PDF's own text (a bold d that the text layer gives as U+1D41D).

A guess is only a guess: bold is a vector, double-struck is a set (`E` and `P` an operator),
an arrow or a hat a vector, the nabla an operator, the h-bar a constant, anything else
"something else". The instructor ticks what to keep and can change what each means; nothing
is added to a course here. Pure functions over a `DocumentResult`: no model, no database,
no document text in the output beyond the symbols themselves.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from readablestem.core.mathchars import STYLE_GROUPS, base_char, char_variant
from readablestem.core.notation import canonical_from_parts, canonical_symbol
from readablestem.core.types import KEPT_ORIGINAL_STATUSES, DocumentResult, Element, Fix, Kind
from readablestem.pipeline import notation_memory

MAX_SUGGESTIONS = 40  # a page of choices, not a wall of them

# `\mathbf{d}`, `\boldsymbol{\omega}`, `\mathbb{R}`: a style command around one letter.
_STYLED = re.compile(
    r"\\(?:mathbf|boldsymbol|bm|pmb|mathbb|mathcal|mathscr|mathfrak|mathsf|mathtt)"
    r"\s*\{\s*(?:\\[A-Za-z]+|[A-Za-z0-9])\s*\}"
)
# `\vec{v}`, `\hat{x}`, `\overrightarrow{F}` and the braceless `\vec v`.
_ACCENTED = re.compile(
    r"\\(?:vec|hat|overrightarrow)(?:\s*\{\s*(?:\\[A-Za-z]+|[A-Za-z])\s*\}|\s+[A-Za-z]\b)"
)
# Operator symbols that a course may mean something particular by.
_OPERATORS = re.compile(r"\\(?:nabla|hbar)(?![A-Za-z])")
_PLAIN_VARIANTS = frozenset({"italic", "sans-serif-italic"})  # a maths letter's default
_OPERATOR_LETTERS = frozenset({"E", "P"})  # double-struck E, P: expectation, probability


@dataclass(frozen=True)
class Suggestion:
    canonical: str  # the notation key
    meaning: str  # the guess, a `NotationMeaning`
    count: int  # equations (or passages of text) that use it
    page: int  # 1-based page of the first one

    @property
    def words(self) -> str:
        """ "bold d"."""
        return notation_memory.describe(self.canonical)

    @property
    def latex(self) -> str | None:
        return notation_memory.latex_of(self.canonical)


def guess_meaning(canonical: str) -> str:
    """What a key most likely means, for the form's starting choice."""
    base, variant, accent = notation_memory._parts(canonical)
    if accent is not None:
        return "vector"
    if canonical == "\u2207":  # nabla
        return "operator"
    if canonical == "\u210f":  # h-bar
        return "constant"
    group = STYLE_GROUPS.get(variant or "")
    if group == "bold":
        return "vector"
    if group == "double-struck":
        return "operator" if base in _OPERATOR_LETTERS else "set"
    return "other"


def _latex_keys(latex: str) -> set[str]:
    found = set()
    for pattern in (_STYLED, _ACCENTED, _OPERATORS):
        for match in pattern.finditer(latex):
            key = canonical_symbol(match.group(0))
            if key is not None:
                found.add(key)
    return found


def _text_keys(text: str) -> set[str]:
    found = set()
    for ch in text:
        variant = char_variant(ch)
        if variant is None or variant in _PLAIN_VARIANTS:
            continue
        key = canonical_from_parts(base_char(ch), variant)
        if key is not None:
            found.add(key)
    return found


def _used(fix: Fix | None) -> bool:
    return fix is not None and fix.equation is not None and fix.status not in KEPT_ORIGINAL_STATUSES


def keys_by_element(result: DocumentResult) -> dict[str, set[str]]:
    """element id -> the notation keys it uses, for the elements that use any."""
    first: dict[str, Fix] = {}
    for fix in result.fixes:
        first.setdefault(fix.element_id, fix)
    found: dict[str, set[str]] = {}
    for element in result.elements:
        keys: set[str] = set()
        fix = first.get(element.id)
        if element.kind is Kind.EQUATION and _used(fix) and fix is not None:
            assert fix.equation is not None
            keys |= _latex_keys(fix.equation.latex)
        if element.text and element.kind in (Kind.EQUATION, Kind.PARAGRAPH, Kind.LIST_ITEM):
            keys |= _text_keys(element.text)
        if keys:
            found[element.id] = keys
    return found


def suggest(
    results: Iterable[DocumentResult],
    *,
    known: Collection[str] = (),
    limit: int = MAX_SUGGESTIONS,
) -> list[Suggestion]:
    """The notation the documents use that is not in `known` (keys the course has),
    most used first, then by page, at most `limit`."""
    counts: Counter[str] = Counter()
    first_page: dict[str, int] = {}
    for result in results:
        by_id: dict[str, Element] = {e.id: e for e in result.elements}
        for element_id, keys in keys_by_element(result).items():
            page = by_id[element_id].page + 1
            for key in keys:
                counts[key] += 1
                first_page[key] = min(first_page.get(key, page), page)
    skip = set(known)
    ranked = sorted(
        (key for key in counts if key not in skip),
        key=lambda key: (-counts[key], first_page[key], notation_memory.describe(key)),
    )
    return [
        Suggestion(key, guess_meaning(key), counts[key], first_page[key]) for key in ranked[:limit]
    ]
