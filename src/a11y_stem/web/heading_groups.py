"""The advice on a group of repeated headings (T1-18; BW3 of the UX review): the answer the
card suggests, the one fact that decides it, and which copies to show.

The pipeline groups headings that repeat one label in one style (`Fix.group_key`). Whether
they are headings usually shows in the outline: a box title ("Teacher Support") followed
by the same words as a bold line is one heading written twice, and a screen reader that
jumps by heading says it twice, eleven times over. So:

- a copy that sits directly under a heading with the same words: suggest bold text;
- the group's label is a run-in label of a worked example ("Solution", "Procedure"):
  suggest bold text;
- otherwise the tool's proposal stands: suggest keeping them as headings.

The sentence says which of these it is, in words about the document's structure, never a
confidence. Content: the label is the document's own text and is shown to its owner only.
Pure functions on the stored result; no model, no rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from a11y_stem.core.types import DocumentResult, Element, Fix, Kind
from a11y_stem.pipeline import assemble

Suggestion = Literal["bold", "keep"]

# Labels that start the answer or the method inside a worked example: usually bold text.
RUN_IN_LABELS = frozenset({"solution", "answer", "procedure", "discussion", "hint", "strategy"})
MAX_CROPS = 3


@dataclass(frozen=True)
class Advice:
    suggest: Suggestion
    fact: str  # the deciding fact, one sentence
    under_same: int  # copies directly under a heading with the same words
    total: int


@dataclass(frozen=True)
class Crop:
    """A copy to show: its element (the page crop is `/jobs/{id}/regions/{element_id}.png`)
    and the page it is on (1-based)."""

    element_id: str
    page: int


def _words(text: str) -> str:
    return " ".join(text.split()).casefold().rstrip(":.")


def _label(fix: Fix) -> str:
    return fix.heading.text if fix.heading is not None else ""


def _above(result: DocumentResult) -> dict[str, Element | None]:
    """For each element, the element before it in reading order (inline equations, which
    sit inside a line of text, do not count)."""
    ordered = [
        e
        for e in sorted(result.elements, key=lambda e: e.order)
        if not (e.kind is Kind.EQUATION and e.display == "inline")
    ]
    return {e.id: (ordered[i - 1] if i else None) for i, e in enumerate(ordered)}


def _under_same(result: DocumentResult, members: list[Fix]) -> int:
    """How many members sit directly under a heading, not one of the group, with the same
    words."""
    above = _above(result)
    first: dict[str, Fix] = {}
    for fix in result.fixes:
        first.setdefault(fix.element_id, fix)
    ids = {f.element_id for f in members}
    count = 0
    for fix in members:
        previous = above.get(fix.element_id)
        if previous is None or previous.kind is not Kind.HEADING or previous.id in ids:
            continue
        level, text = assemble.heading_parts(previous, first.get(previous.id))
        if level is not None and _words(text) == _words(_label(fix)):
            count += 1
    return count


def advice(result: DocumentResult, members: list[Fix]) -> Advice:
    """The suggestion and its deciding fact for the headings of one group."""
    total = len(members)
    if not total:
        return Advice("keep", "", 0, 0)
    label = _label(members[0])
    under = _under_same(result, members)
    if under and under * 2 > total:
        if under == total:
            which = "The copy sits" if total == 1 else "Each copy sits"
            fact = (
                f"{which} directly under a heading with the same words, so as headings "
                "students would hear it twice in a row."
            )
        else:
            fact = (
                f"{under} of the {total} copies sit directly under a heading with the same "
                "words, so as headings students would hear those twice in a row."
            )
        return Advice("bold", fact, under, total)
    if _words(label) in RUN_IN_LABELS:
        fact = (
            f"“{label.strip()}” is a label that starts part of a worked example. Labels "
            "like it are usually bold text, not headings."
        )
        return Advice("bold", fact, under, total)
    fact = (
        "No copy sits directly under a heading with the same words, so the tool's proposal stands."
    )
    return Advice("keep", fact, under, total)


def crops(members: list[Fix], elements: dict[str, Element]) -> list[Crop]:
    """Up to three copies to show, spread over the group: the first, one from the middle
    and the last, each with the page it is on."""
    shown = [f for f in members if f.element_id in elements]
    if not shown:
        return []
    picks = {0, len(shown) // 2, len(shown) - 1}
    return [
        Crop(shown[i].element_id, elements[shown[i].element_id].page + 1)
        for i in sorted(picks)[:MAX_CROPS]
    ]
