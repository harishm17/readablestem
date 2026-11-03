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

A typed equation whose page witness read it differently (`EquationFix.other_latex`,
`pipeline.witness_reconcile`) gets the same machinery: `other_reading_for` shows the other
reading (rendered, and how it is said) with one button for the whole of it, and the symbols in
which it differs (`reading_alternatives.from_reading` on the proposal and `other_latex`) as the
same chips. Typed and handwritten cards therefore behave alike: nothing chosen for the person,
each choice an ordinary edit, shown only while the decision is open and for the proposal the
other reading was made against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from markupsafe import Markup

from readablestem.core.types import Element, Fix, ReadingAlternatives, SymbolAlternative
from readablestem.pipeline import reading_alternatives
from readablestem.pipeline.validate import has_errors
from readablestem.web.mathml import try_sanitize_mathml

# The check that says the witness also read the formula's start and end differently.
WITNESS_EXTENT_CODE = "witness_extent_differs"


@dataclass(frozen=True)
class Chip:
    phrase: str  # what the choice means: "capital X"
    latex: str  # the whole equation with this choice in place (posted as an edit)
    speech: str  # that equation read aloud, roughly ("" when it could not be made)


@dataclass(frozen=True)
class ChipGroup:
    symbol: str  # the proposal's LaTeX for this symbol
    which: str  # "the 2nd " when the symbol stands more than once, else ""
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


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _which(reading: ReadingAlternatives, symbol: SymbolAlternative) -> str:
    """ "the 2nd " when the symbol stands more than once in the reading (`x + x`), found from
    where this one starts, so the person knows which of them is meant."""
    seen, found = 0, 0
    while span := reading_alternatives.find_symbol(reading.latex, symbol.symbol, seen + 1):
        seen += 1
        if span[0] == symbol.start:
            found = seen
    return f"the {_ordinal(found)} " if seen > 1 and found else ""


def _group(reading: ReadingAlternatives, index: int) -> ChipGroup:
    symbol = reading.symbols[index]
    chips = []
    for number, option in enumerate(symbol.options):
        whole = reading.apply(index, number)
        chips.append(Chip(phrase(symbol, option), whole, _say(whole, "block")))
    return ChipGroup(
        symbol.symbol, _which(reading, symbol), _say(symbol.symbol) or symbol.symbol, chips
    )


def _chips(reading: ReadingAlternatives) -> Chips:
    return Chips([_group(reading, i) for i in range(len(reading.symbols))])


def chips_for(element: Element | None, fix: Fix) -> Chips | None:
    """The choices to show for this decision, or None (no alternatives, nothing waiting, or
    the reading they were made for is no longer the fix's)."""
    reading = element.alternatives if element is not None else None
    if reading is None or not fix.pending or fix.equation is None:
        return None
    if fix.equation.latex != reading.latex:
        return None
    return _chips(reading)


@dataclass(frozen=True)
class OtherReading:
    """The reading the proposal was not chosen over, for the card: its LaTeX (posted as an
    edit by "Use the other reading"), the rendered formula, how it is said, where it came from
    ("page_witness": the reading of the whole page; "crop": the reading of the picture of the
    formula), why the item is asked, and the symbols in which it differs as chips (None when
    it differs in too much for single symbols, or in one symbol only: then the button for the
    whole reading is the same choice)."""

    latex: str
    mathml: Markup
    speech: str
    source: Literal["crop", "page_witness"]
    why: str
    chips: Chips | None


def _why(fix: Fix, source: str) -> str:
    if source == "crop":
        lead = (
            "A second reading, of the whole page, read this formula differently, and it is the "
            "proposal above. This is the reading from the picture of the formula."
        )
    else:
        lead = (
            "A second reading, of the whole page, read this formula differently. The proposal "
            "above is the reading closer to the characters in the PDF."
        )
    if any(c.code == WITNESS_EXTENT_CODE and not c.passed for c in fix.checks):
        lead += (
            " It also marks where the formula starts and ends differently, so it may cover "
            "more or less than this item."
        )
    return lead + " Look at the picture, then choose."


def other_reading_for(fix: Fix, *, enabled: bool = True) -> OtherReading | None:
    """The other reading of a typed equation to show on its card, or None: the page witness is
    off (`enabled`), nothing is waiting, there is no other reading, or it is the proposal's own
    LaTeX or does not convert (a button that would be refused is no choice). The other reading
    is stored with the proposal it was made against, so after an edit (which replaces the
    equation) or an Undo to another reading it is not shown."""
    equation = fix.equation
    if not enabled or not fix.pending or equation is None or not equation.other_latex:
        return None
    other = equation.other_latex
    if other.strip() == equation.latex.strip():
        return None
    from readablestem.pipeline import equation_step

    display = "block" if equation.display == "block" else "inline"
    mathml, checks = equation_step._convert(other, display)
    safe = try_sanitize_mathml(mathml) if mathml is not None and not has_errors(checks) else None
    if mathml is None or safe is None:
        return None
    source = equation.other_source or "page_witness"
    chips = None
    found = reading_alternatives.from_reading(equation.latex, other, display)
    if found is not None:
        grouped = _chips(found)
        if not (grouped.count == 1 and grouped.groups[0].chips[0].latex.strip() == other.strip()):
            chips = grouped
    return OtherReading(
        latex=other,
        mathml=safe,
        speech=equation_step._speech(mathml)[0] or "",
        source=source,
        why=_why(fix, source),
        chips=chips,
    )


def offered_witness_latex(fix: Fix, *, enabled: bool = True) -> set[str]:
    """The whole LaTeX each choice of the other reading would give: the reading itself and
    each chip. What `web.review` checks before it records an edit as such a choice."""
    other = other_reading_for(fix, enabled=enabled)
    if other is None:
        return set()
    return {
        other.latex,
        *(c.latex for g in (other.chips.groups if other.chips else []) for c in g.chips),
    }
