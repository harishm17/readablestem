"""One-click choices for a doubtful symbol on the review card (`Element.alternatives`).

When a second reader read a handwritten equation differently in a few symbols
(`pipeline.image_page_reconcile`), the card offers each other reading as a button: "Use capital
X". Each button posts an ordinary edit with the whole LaTeX that choice gives, so it goes
through the same checks, audit record and undo as typed LaTeX. Nothing is chosen for the
person, and the buttons appear only while the decision is open and only for the reading they
were made for (`ReadingAlternatives.latex` is the fix's current LaTeX: after an edit, or an
undo to another reading, they would point at the wrong characters and are not shown).

The words of a button say what the choice means, from its kind and the way the symbol is
spoken ("capital X", "Greek rho", "the digit 1"), because a screen reader user chooses by what
is heard. Below the button, how the whole equation would then be read aloud.
"""

from __future__ import annotations

from dataclasses import dataclass

from readablestem.core.types import Element, Fix, ReadingAlternatives, SymbolAlternative


@dataclass(frozen=True)
class Chip:
    phrase: str  # what the choice means: "capital X"
    latex: str  # the whole equation with this choice in place (posted as an edit)
    speech: str  # that equation read aloud, roughly ("" when it could not be made)


@dataclass(frozen=True)
class ChipGroup:
    symbol: str  # the page reading's LaTeX for this symbol
    said: str  # ... and how it is spoken
    chips: list[Chip]


@dataclass(frozen=True)
class Chips:
    groups: list[ChipGroup]

    @property
    def count(self) -> int:
        return sum(len(g.chips) for g in self.groups)


def _say(latex: str, display: str = "inline") -> str:
    """How `latex` is read aloud (the product's own speech), or "" when it does not convert."""
    from readablestem.pipeline import equation_step

    mathml, _ = equation_step._convert(latex, "block" if display == "block" else "inline")
    if mathml is None:
        return ""
    return equation_step._speech(mathml)[0] or ""


def phrase(symbol: SymbolAlternative, option: str) -> str:
    """What choosing `option` for `symbol` means, in words."""
    said = _say(option) or option
    greek = option.startswith("\\") and option[1:].isalpha()
    if symbol.kind == "case":
        return said if said.lower().startswith("capital") else f"small {said}"
    if symbol.kind == "greek_latin":
        return f"Greek {said}" if greek else f"the letter {said}"
    if symbol.kind == "digit_letter":
        return f"the digit {said}" if option.strip().isdigit() else f"the letter {said}"
    if symbol.kind == "script":
        raised = any(mark in option for mark in "^_")
        return f"with a raised or lowered part: {said}" if raised else f"without it: {said}"
    return said


def _group(reading: ReadingAlternatives, index: int) -> ChipGroup:
    symbol = reading.symbols[index]
    chips = []
    for number, option in enumerate(symbol.options):
        whole = reading.apply(index, number)
        chips.append(Chip(phrase(symbol, option), whole, _say(whole, "block")))
    return ChipGroup(symbol.symbol, _say(symbol.symbol) or symbol.symbol, chips)


def chips_for(element: Element | None, fix: Fix) -> Chips | None:
    """The choices to show for this decision, or None (no alternatives, nothing waiting, or
    the reading they were made for is no longer the fix's)."""
    reading = element.alternatives if element is not None else None
    if reading is None or not fix.pending or fix.equation is None:
        return None
    if fix.equation.latex != reading.latex:
        return None
    return Chips([_group(reading, i) for i in range(len(reading.symbols))])
