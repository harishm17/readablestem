"""Heading fixes. Deterministic: no model call.

Turns `Kind.HEADING` elements into `Fix(kind=HEADING, heading=HeadingFix(level, text))`.
Levels come from layout clustering (`layout.heading_levels`); this step repairs the outline
so it never skips a level going down (h1 -> h3 becomes h1 -> h2) and the first heading is
h1 or h2, and marks uncertain ones NEEDS_REVIEW.

Evidence and confidence (also in docs/layout.md):

| Evidence (from layout `Element.reasons`)          | Base | Strong? |
|---------------------------------------------------|------|---------|
| larger font (>= 1.15 x body text)                 | 0.90 | yes     |
| ... plus a section number ("2.3", "Chapter 2")    | 0.95 | yes     |
| bold body-size line, or a standalone numbered line | 0.60 | no      |
| ... plus a section number                         | 0.88 | yes     |

Penalties: all caps -0.15, fewer than 4 letters -0.20, list-style number ("1.", "A)")
without a larger font -0.15, outline repaired -0.03. A fix is ACCEPTED only with strong
evidence and none of the
first three penalties, which keeps every accepted heading at >= 0.85 (the default
`Settings.auto_accept_confidence`); everything else is NEEDS_REVIEW. Check codes are
content-free categories ("heading.level_repaired", "heading.weak_evidence", ...).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from a11y_stem.core.types import Check, Element, Fix, HeadingFix, Kind, Status
from a11y_stem.pipeline.layout import (
    REASON_BOLD_BODY,
    REASON_LARGER_FONT,
    REASON_LIST_NUMBER,
    REASON_NUMBERED,
    REASON_NUMBERED_LINE,
    heading_levels,
)

ACCEPT_AT = 0.85  # matches the default Settings.auto_accept_confidence
MIN_LETTERS = 4  # fewer letters than this ("Ex.", "A") is too little to trust


def heading_fixes(elements: list[Element]) -> list[Fix]:
    """One fix per heading element, in reading order, with a repaired outline."""
    headings = sorted((e for e in elements if e.kind == Kind.HEADING), key=lambda e: e.order)
    raw = heading_levels(headings)
    levels = repair_outline([raw[h.id] for h in headings])
    return [_fix(h, raw[h.id], level) for h, level in zip(headings, levels, strict=True)]


def repair_outline(levels: Sequence[int]) -> list[int]:
    """Levels that never step down by more than one; the first heading is h1 or h2.

    Siblings stay siblings: each heading goes one level below the nearest earlier heading
    with a smaller raw level, so h1, h3, h3, h2, h3 becomes h1, h2, h2, h2, h3. A repair
    only ever raises a heading (smaller number); going back up the outline is always fine.
    """
    repaired: list[int] = []
    open_parents: list[tuple[int, int]] = []  # (raw level, repaired level)
    for raw in levels:
        while open_parents and open_parents[-1][0] >= raw:
            open_parents.pop()
        level = open_parents[-1][1] + 1 if open_parents else min(raw, 2)
        open_parents.append((raw, level))
        repaired.append(level)
    return repaired


@dataclass(frozen=True)
class _Evidence:
    larger_font: bool
    numbered: bool
    list_like: bool
    all_caps: bool
    very_short: bool

    @property
    def strong(self) -> bool:
        return self.larger_font or self.numbered

    @property
    def maybe_list_item(self) -> bool:
        """ "1. Measure the cart" at body size reads like a list item; in a large font,
        "1. Introduction" is an ordinary heading style."""
        return self.list_like and not self.larger_font

    @property
    def weak_flags(self) -> bool:
        return self.all_caps or self.very_short or self.maybe_list_item

    def confidence(self, repaired: bool) -> float:
        if self.larger_font:
            value = 0.95 if self.numbered else 0.90
        else:
            value = 0.88 if self.numbered else 0.60
        value -= 0.15 * self.all_caps + 0.20 * self.very_short + 0.15 * self.maybe_list_item
        value -= 0.03 * repaired
        return round(min(0.99, max(0.05, value)), 2)


def _evidence(element: Element, text: str) -> _Evidence:
    reasons = set(element.reasons)
    letters = [c for c in text if c.isalpha()]
    if not reasons & {REASON_LARGER_FONT, REASON_BOLD_BODY, REASON_NUMBERED_LINE}:
        reasons.add(REASON_BOLD_BODY)  # unknown origin: treat as the weakest kind
    return _Evidence(
        larger_font=REASON_LARGER_FONT in reasons,
        numbered=REASON_NUMBERED in reasons,
        list_like=REASON_LIST_NUMBER in reasons,
        all_caps=len(letters) >= MIN_LETTERS and all(c.isupper() for c in letters),
        very_short=len(letters) < MIN_LETTERS,
    )


def _check(name: str, passed: bool, code: str) -> Check:
    return Check(name=name, passed=passed, severity="warning", code=None if passed else code)


def _fix(element: Element, raw_level: int, level: int) -> Fix:
    text = " ".join((element.text or "").split())
    if not text:
        return Fix(
            element_id=element.id,
            kind=Kind.HEADING,
            status=Status.ABSTAINED,
            reason="heading.empty_text",
            checks=[Check(name="heading.text", passed=False, code="heading.empty_text")],
        )
    evidence = _evidence(element, text)
    repaired = level != raw_level
    checks = [
        _check("heading.outline", not repaired, "heading.level_repaired"),
        _check("heading.evidence", evidence.strong, "heading.weak_evidence"),
        _check("heading.all_caps", not evidence.all_caps, "heading.all_caps"),
        _check("heading.length", not evidence.very_short, "heading.very_short"),
        _check("heading.list_like", not evidence.maybe_list_item, "heading.list_like"),
    ]
    accepted = evidence.strong and not evidence.weak_flags
    return Fix(
        element_id=element.id,
        kind=Kind.HEADING,
        status=Status.ACCEPTED if accepted else Status.NEEDS_REVIEW,
        confidence=evidence.confidence(repaired),
        heading=HeadingFix(level=level, text=text),
        checks=checks,
    )
