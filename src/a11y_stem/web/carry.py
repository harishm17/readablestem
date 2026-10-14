"""Carry decisions over from an earlier version of a document onto a revised one (T3-2).

An instructor who fixes a typo and uploads the PDF again should not have to decide the same
equations and figures twice. The earlier version's decisions (the ones a person made on the
review screen: accepted, edited, kept as the original) are carried onto the new conversion
when, and only when, the item is the same item:

- **Same item** means the same kind and the same content fingerprint. For an equation, a
  figure or a table that is the SHA-256 of its crop (the picture cut from the page), plus
  the display mode of an equation and the caption of a figure, which the figure's alt text
  is written for. For a heading it is its words, size and weight. A crop that differs by a
  single pixel is a different fingerprint: a changed item never inherits a decision.
- **Position** chooses between items that share a fingerprint (the same small figure on
  twenty pages): the nearest one on the page and in the reading order is matched first, one
  earlier item to one new item. It also finds the items that *changed*: an earlier decided
  item with no match, and a new item waiting for a decision in the same place, is shown to
  the instructor as "changed, decide again".
- A decision to *accept* is carried only if the tool proposes the same thing now. The tool
  can propose another reading of the same picture; an accept of the old proposal says
  nothing about the new one. An edit (the instructor's own text) or a rejection (keep the
  original) belongs to the picture and is carried either way.
- A heading level is carried only if the output would still write that level there.

Nothing here reads a model, and the report holds element ids and counts, never text. The
changes are returned as `web.review.ReviewChange`-like records so the caller can store a
`ReviewEdit` for each and write the HTML again.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from a11y_stem.core import triage
from a11y_stem.core.types import (
    PENDING_STATUSES,
    DocumentResult,
    Element,
    Fix,
    Kind,
    Status,
)

# A decision is carried only onto the kinds a person decides on the review screen.
CARRIED_KINDS = (Kind.EQUATION, Kind.FIGURE, Kind.TABLE, Kind.HEADING)
# Pages count for more than the place on a page when two items are compared.
PAGE_WEIGHT = 2.0
# How much of the smaller box an earlier item and a new one must share to be "the same
# place" when their content differs.
SAME_PLACE = 0.5
# `ReviewEdit.action` of a carried decision. Kept apart from "accept", "edit" and "reject"
# (usage counts show "review:carried") and without a write number, so Undo never takes
# back a whole carry at once.
CARRIED_ACTION = "carried"

_SPACES = re.compile(r"\s+")


@dataclass
class CarryReport:
    """What happened to the earlier decisions, as element ids of the *new* document."""

    carried: list[str] = field(default_factory=list)
    # The same picture, but the tool now proposes something else: decide again.
    suggestion_changed: list[str] = field(default_factory=list)
    # An item in the place of an earlier decided item, with other content: decide again.
    changed: list[str] = field(default_factory=list)
    # Waiting for a decision, with nothing like it in the earlier version.
    new: list[str] = field(default_factory=list)
    # Earlier decisions whose item is no longer in the document.
    removed: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "carried": self.carried,
            "suggestion_changed": self.suggestion_changed,
            "changed": self.changed,
            "new": self.new,
            "removed": self.removed,
        }

    @classmethod
    def from_json(cls, data: object) -> CarryReport:
        if not isinstance(data, dict):
            return cls()

        def ids(name: str) -> list[str]:
            value = data.get(name)
            return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

        removed = data.get("removed")
        return cls(
            carried=ids("carried"),
            suggestion_changed=ids("suggestion_changed"),
            changed=ids("changed"),
            new=ids("new"),
            removed=removed if isinstance(removed, int) and removed > 0 else 0,
        )


@dataclass(frozen=True)
class CarriedChange:
    """One decision put onto the new document: the fix before and after, as the snapshots
    a `ReviewEdit` keeps (`web.review.snapshot`)."""

    element_id: str
    before: dict[str, Any]
    after: dict[str, Any]


# --------------------------------------------------------------------------- fingerprints


def _crop_hash(png: bytes) -> str:
    return hashlib.sha256(png).hexdigest()


def _words(text: str | None) -> str:
    return _SPACES.sub(" ", (text or "").strip()).casefold()


def fingerprint(element: Element, crops: dict[str, bytes]) -> str | None:
    """The content of one item, or None when it has nothing to compare (no crop, no
    words). Two items with the same fingerprint are the same item."""
    if element.kind is Kind.HEADING:
        words = _words(element.text)
        if not words:
            return None
        parts = [words, f"{element.font_size or 0:.1f}", "b" if element.bold else "r"]
    elif element.kind in (Kind.EQUATION, Kind.FIGURE, Kind.TABLE):
        png = crops.get(element.id)
        if not png:
            return None
        parts = [_crop_hash(png)]
        if element.kind is Kind.EQUATION:
            parts.append(element.display or "")
        elif element.kind is Kind.FIGURE:
            parts.append(_words(element.caption))
    else:
        return None
    return hashlib.sha256("\x1f".join([element.kind.value, *parts]).encode()).hexdigest()


# --------------------------------------------------------------------------- position


def _frame(result: DocumentResult, page: int) -> tuple[float, float]:
    width, height = result.page_sizes.get(page, (612.0, 792.0))
    return (width or 612.0, height or 792.0)


def _centre(result: DocumentResult, element: Element) -> tuple[float, float]:
    width, height = _frame(result, element.page)
    x0, y0, x1, y1 = element.bbox
    return ((x0 + x1) / 2 / width, (y0 + y1) / 2 / height)


def _distance(old: DocumentResult, a: Element, new: DocumentResult, b: Element) -> float:
    """How far apart two items sit: pages first, then the place on the page."""
    ax, ay = _centre(old, a)
    bx, by = _centre(new, b)
    return abs(a.page - b.page) * PAGE_WEIGHT + abs(ax - bx) + abs(ay - by)


def _share(a: Element, b: Element) -> float:
    """The part of the smaller box that the two boxes share (0 on different pages)."""
    if a.page != b.page:
        return 0.0
    ax0, ay0, ax1, ay1 = a.bbox
    bx0, by0, bx1, by1 = b.bbox
    width = min(ax1, bx1) - max(ax0, bx0)
    height = min(ay1, by1) - max(ay0, by0)
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return (width * height) / smaller if smaller > 0 else 0.0


# --------------------------------------------------------------------------- the decision


def _decided(fix: Fix) -> bool:
    """A person's decision that can be carried: accepted, edited or rejected on the review
    screen (the tool's own automatic acceptance is not one)."""
    return fix.decided_by_reviewer and fix.status not in PENDING_STATUSES


def _proposal(fix: Fix) -> dict[str, Any]:
    return fix.model_dump(
        mode="json", include={"equation", "figure", "heading", "table"}, exclude_none=True
    )


def _same_proposal(old: Fix, new: Fix) -> bool:
    return _proposal(old) == _proposal(new) and bool(_proposal(new))


def _wants_decision(fix: Fix) -> bool:
    return fix.status in PENDING_STATUSES


def _carry_one(old: Fix, new: Fix) -> str | None:
    """None when `old`'s decision can be put on `new`, else why not ("suggestion_changed")."""
    if old.status is Status.ACCEPTED:
        if not _wants_decision(new):
            return "same"  # already accepted by the tool: nothing to carry, nothing lost
        if not _same_proposal(old, new):
            return "suggestion_changed"
    return None


def carry_decisions(
    old: DocumentResult,
    old_crops: dict[str, bytes],
    new: DocumentResult,
    new_crops: dict[str, bytes],
) -> tuple[CarryReport, list[CarriedChange]]:
    """Put the decisions made on `old` onto `new` (in place) where the item is the same.

    Returns the report and the changes made. The caller stores a `ReviewEdit` per change,
    writes the HTML again from `new` and keeps the report.
    """
    from a11y_stem.web import review

    report = CarryReport()
    changes: list[CarriedChange] = []
    old_elements = {e.id: e for e in old.elements}
    new_elements = {e.id: e for e in new.elements}

    old_fixes: dict[str, Fix] = {}
    for fix in old.fixes:
        if fix.kind in CARRIED_KINDS and fix.element_id in old_elements:
            old_fixes.setdefault(fix.element_id, fix)
    new_fixes: dict[str, Fix] = {}
    for fix in new.fixes:
        if fix.kind in CARRIED_KINDS and fix.element_id in new_elements:
            new_fixes.setdefault(fix.element_id, fix)

    # Fingerprints of both sides, by kind.
    old_by_print: defaultdict[str, list[str]] = defaultdict(list)
    for element_id in old_fixes:
        if (print_ := fingerprint(old_elements[element_id], old_crops)) is not None:
            old_by_print[print_].append(element_id)
    new_print: dict[str, str] = {}
    new_by_print: defaultdict[str, list[str]] = defaultdict(list)
    for element_id in new_fixes:
        if (print_ := fingerprint(new_elements[element_id], new_crops)) is not None:
            new_print[element_id] = print_
            new_by_print[print_].append(element_id)

    # One earlier item to one new item with the same fingerprint, the nearest pairs first.
    matched: dict[str, str] = {}  # new id -> old id
    for print_, new_ids in new_by_print.items():
        old_ids = old_by_print.get(print_, [])
        pairs = sorted(
            (_distance(old, old_elements[o], new, new_elements[n]), n, o)
            for n in new_ids
            for o in old_ids
        )
        used_old: set[str] = set()
        for _, n, o in pairs:
            if n in matched or o in used_old:
                continue
            matched[n] = o
            used_old.add(o)

    # Reading order, so a heading's level is checked against the headings before it.
    order = {e.id: e.order for e in new.elements}
    for new_id in sorted(matched, key=lambda i: order.get(i, 0)):
        old_fix, fix = old_fixes[matched[new_id]], new_fixes[new_id]
        if not _decided(old_fix):
            continue
        verdict = _carry_one(old_fix, fix)
        if verdict == "same":
            continue
        if verdict is not None:
            report.suggestion_changed.append(new_id)
            continue
        before = review.snapshot(fix)
        _put_decision(fix, old_fix)
        if (
            fix.kind is Kind.HEADING
            and fix.heading is not None
            and fix.status is not Status.REJECTED
        ):
            written = review.heading_level_written(new, new_id, fix.heading.level)
            if written != fix.heading.level:  # the outline here is not the one it was decided in
                _put_back(fix, before)
                report.suggestion_changed.append(new_id)
                continue
        changes.append(CarriedChange(new_id, before, review.snapshot(fix)))
        report.carried.append(new_id)
    if changes:
        review._recount(new)

    # Earlier decisions with no counterpart, and where a changed item sits.
    paired_old = set(matched.values())
    unmatched_old = [o for o, fix in old_fixes.items() if _decided(fix) and o not in paired_old]
    waiting_new = [
        n
        for n, fix in new_fixes.items()
        if _wants_decision(fix) and n not in matched and n not in report.carried
    ]
    taken: set[str] = set()
    for old_id in unmatched_old:
        element = old_elements[old_id]
        place = max(
            (
                (_share(element, new_elements[n]), n)
                for n in waiting_new
                if n not in taken and new_elements[n].kind is element.kind
            ),
            default=(0.0, ""),
        )
        if place[0] >= SAME_PLACE:
            taken.add(place[1])
            report.changed.append(place[1])
        else:
            report.removed += 1
    # Waiting items with nothing like them before.
    already = {*report.changed, *report.suggestion_changed}
    report.new = [n for n in waiting_new if n not in already]
    return report, changes


def _put_decision(target: Fix, source: Fix) -> None:
    """Copy what a decision can change from `source` onto `target` (`web.review.snapshot`'s
    fields), so the new fix reads exactly as the earlier one did after the decision."""
    for name in ("equation", "figure", "heading", "table"):
        value = getattr(source, name)
        setattr(target, name, value.model_copy(deep=True) if value is not None else None)
    target.checks = [c.model_copy(deep=True) for c in source.checks]
    target.status = source.status
    target.reviewed = True


def _put_back(fix: Fix, before: dict[str, Any]) -> None:
    restored = Fix.model_validate(fix.model_dump(mode="json") | before)
    for name in ("equation", "figure", "heading", "table", "checks", "status", "reviewed"):
        setattr(fix, name, getattr(restored, name))


# --------------------------------------------------------------------------- the page


@dataclass(frozen=True)
class CarrySummary:
    """The carry report in decisions (a group of repeated headings is one), for the job
    page. Counted from the fixes as they are now: a carried decision the person has since
    changed is still "carried over" (it was), but a decision that is still waiting counts
    where it stands."""

    carried: int
    changed: int
    new: int
    removed: int
    changed_ids: list[str]
    new_ids: list[str]
    waiting: int  # of the changed and new ones, how many are still waiting now

    @property
    def any(self) -> bool:
        return bool(self.carried or self.changed or self.new or self.removed)


def summary(report: CarryReport, result: DocumentResult) -> CarrySummary:
    fixes = {f.element_id: f for f in result.fixes}

    def count(ids: list[str]) -> int:
        return len(triage.units(fixes[i] for i in ids if i in fixes))

    changed_ids = [*report.changed, *report.suggestion_changed]
    waiting = triage.units(
        fixes[i] for i in [*changed_ids, *report.new] if i in fixes and fixes[i].pending
    )
    return CarrySummary(
        carried=count(report.carried),
        changed=count(changed_ids),
        new=count(report.new),
        removed=report.removed,
        changed_ids=changed_ids,
        new_ids=list(report.new),
        waiting=len(waiting),
    )
