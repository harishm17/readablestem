"""The review screen's queue: the decisions the pipeline asked for, numbered, in order.

A reviewer makes decisions, not fixes (docs/design/beta-architecture.md, "Review tiers"):
a fix that needs a decision is one, and a group of repeated headings that share a
`Fix.group_key` is one for all of its headings, and so are the pages (or the pieces of
handwriting) kept as images for one reason ("pg-" keys, `web.review.is_kept_image`). The
queue holds every decision the
pipeline asked for (`Fix.review == "required"`, plus anything still waiting, for results
stored before tiers existed), in the review order of `web.review` (equations, then
figures, then tables, then headings, each in page and reading order), numbered from 1. A
table kept as an image is a decision like any other (keep the image, or use the grid
read from the PDF). Numbers do not
change while the reviewer works, so "Decision 2 of 3" names the same thing before and
after decision 1 is made; a decision is *open* while any of its fixes is still waiting.

Counts are in decisions everywhere: the sentence at the top, the progress, the page list
(an open decision counts on the page of its first fix still waiting, so the pages add up
to the total) and the job page.
"""

from __future__ import annotations

from dataclasses import dataclass

from a11y_stem.core import triage
from a11y_stem.core.types import DocumentResult, Element, Fix, Kind
from a11y_stem.web.review import GROUP_KINDS, PAGE_GROUP_PREFIX, group_fixes, is_kept_image


@dataclass(frozen=True)
class Decision:
    """One decision: a single fix, every heading of a group, or every page kept as an
    image for one reason (reading order)."""

    key: str  # the element id, or the group key: "hg-p1-e7" (headings), "pg-p2-e0" (pages)
    number: int  # 1-based position in the queue
    kind: Kind
    fixes: tuple[Fix, ...]

    @property
    def is_group(self) -> bool:
        """A group of repeated headings."""
        return self.key.startswith("hg-")

    @property
    def is_page_group(self) -> bool:
        """Pages (or handwriting) kept as images for one reason."""
        return self.key.startswith(PAGE_GROUP_PREFIX)

    @property
    def open(self) -> bool:
        return any(f.pending for f in self.fixes)

    @property
    def waiting(self) -> list[Fix]:
        return [f for f in self.fixes if f.pending]

    @property
    def lead(self) -> Fix:
        """The fix the decision is shown with: the first one still waiting, else the
        first one."""
        return next(iter(self.waiting), self.fixes[0])


def _asked(fix: Fix) -> bool:
    return fix.kind in GROUP_KINDS and (fix.review == "required" or fix.pending)


def decisions(result: DocumentResult) -> list[Decision]:
    """Every decision the pipeline asked for, in review order, numbered from 1."""
    far = (1 << 30, 1 << 30)  # a fix whose element is missing goes last in its kind
    rank = {e.id: (e.page, e.order) for e in result.elements}
    ordered = [
        fix
        for group in group_fixes(result)
        for fix in sorted(
            (*group.pending, *group.optional, *group.done),
            key=lambda f: rank.get(f.element_id, far),
        )
        if _asked(fix)
    ]
    out: list[Decision] = []
    for number, unit in enumerate(triage.units(ordered), start=1):
        first = unit[0]
        grouped = first.kind is Kind.HEADING or is_kept_image(first)
        key = first.group_key if first.group_key and grouped else None
        out.append(Decision(key or first.element_id, number, first.kind, tuple(unit)))
    return out


def find(queue: list[Decision], key: str) -> Decision | None:
    """The decision named by `key`: its own key, or the id of any of its fixes."""
    for decision in queue:
        if decision.key == key or any(f.element_id == key for f in decision.fixes):
            return decision
    return None


def first_open(queue: list[Decision]) -> Decision | None:
    return next((d for d in queue if d.open), None)


def next_open(queue: list[Decision], after: Decision | None) -> Decision | None:
    """The next open decision after `after` in queue order, wrapping round; None when
    every decision is made."""
    if after is None:
        return first_open(queue)
    later = [d for d in queue if d.number > after.number and d.open]
    earlier = [d for d in queue if d.number < after.number and d.open]
    return next(iter(later or earlier), None)


def previous(queue: list[Decision], current: Decision | None) -> Decision | None:
    if current is None or current.number <= 1:
        return None
    return queue[current.number - 2]


def following(queue: list[Decision], current: Decision | None) -> Decision | None:
    if current is None or current.number >= len(queue):
        return None
    return queue[current.number]


def page_of(fix: Fix, elements: dict[str, Element]) -> int | None:
    """0-based page of a fix's element, None when the element is missing."""
    element = elements.get(fix.element_id)
    return element.page if element is not None else None


def open_by_page(queue: list[Decision], elements: dict[str, Element]) -> dict[int, int]:
    """0-based page -> open decisions on it, each counted once, on the page of its first
    fix still waiting."""
    counts: dict[int, int] = {}
    for decision in queue:
        if not decision.open:
            continue
        page = page_of(decision.lead, elements)
        if page is not None:
            counts[page] = counts.get(page, 0) + 1
    return counts
