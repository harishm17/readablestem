"""Accept many decisions at once, for the two kinds where a person need not read each one.

A document with 300 pages asks for about 540 single decisions (`docs/web.md`, "Review at
scale"). Two groups of them are waiting only because the model was not sure enough to
accept them itself, while every automatic check passed:

- *equations* whose proposal failed no check, had no reason to be set aside and was not
  flagged by the second automated check ("Accept all remaining equations whose checks
  passed");
- *figures* that have generated alt text (not marked decorative, not empty) and failed no
  check ("Accept all figures that have generated alt text").

Only an undecided fix (`needs_review`) in exactly those states is touched. Each one goes
through the ordinary Accept (`web.review.apply_review`), so each is recorded as a
`ReviewEdit` marked `bulk` and the whole action is one write that one Undo takes back.
Nothing is decided for a fix with a failed check, a flagged one, a heading group, or a
page kept as an image: those stay for a person.
"""

from __future__ import annotations

from dataclasses import dataclass

from readablestem.core.types import DocumentResult, Fix, Kind, Status
from readablestem.web.review import (
    BULK_KEY,
    FieldError,
    ReviewChange,
    ReviewInvalid,
    apply_review,
    can_accept,
    is_kept_image,
    reading_order,
)


@dataclass(frozen=True)
class BulkKind:
    slug: str  # in the URL: /jobs/{id}/accept/{slug}
    kind: Kind
    one: str
    many: str
    title: str  # the action in words, without a count
    nothing: str
    tail: str  # after "Accepted 3 equations": what they have in common


BULK_KINDS = {
    "equations": BulkKind(
        "equations",
        Kind.EQUATION,
        "equation",
        "equations",
        "Accept all remaining equations whose checks passed",
        "No equation is waiting with every check passed.",
        "whose checks passed",
    ),
    "figures": BulkKind(
        "figures",
        Kind.FIGURE,
        "figure",
        "figures",
        "Accept all figures that have generated alt text",
        "No figure is waiting with generated alt text and every check passed.",
        "with generated alt text",
    ),
}


def _flagged(fix: Fix) -> bool:
    """The second automated check found a possible mismatch (kept as a note or a flag)."""
    return any(v.flagged for v in fix.verifications)


def is_bulk_candidate(fix: Fix, slug: str) -> bool:
    """Whether the bulk action for `slug` accepts this fix: undecided, a usable proposal,
    nothing failed, nothing flagged, and on its own (not in a group)."""
    spec = BULK_KINDS[slug]
    if fix.kind is not spec.kind or fix.status is not Status.NEEDS_REVIEW:
        return False
    if fix.group_key or fix.reason or is_kept_image(fix):
        return False
    if fix.failed_checks or _flagged(fix) or not can_accept(fix):
        return False
    if fix.kind is Kind.EQUATION:
        return fix.equation is not None and bool(fix.equation.latex.strip())
    figure = fix.figure
    return figure is not None and not figure.decorative and bool(figure.alt.strip())


def candidates(result: DocumentResult, slug: str) -> list[Fix]:
    """The fixes the action for `slug` would accept now, in page and reading order."""
    found = [f for f in result.fixes if is_bulk_candidate(f, slug)]
    return reading_order(result, found)


@dataclass(frozen=True)
class Offer:
    slug: str
    count: int
    title: str
    one: str
    many: str

    @property
    def counted(self) -> str:
        return f"{self.count} {self.one if self.count == 1 else self.many}"


def offers(result: DocumentResult) -> list[Offer]:
    """The bulk actions worth showing: those with something to accept."""
    out = []
    for slug, spec in BULK_KINDS.items():
        n = len(candidates(result, slug))
        if n:
            out.append(Offer(slug, n, spec.title, spec.one, spec.many))
    return out


def accept_all(result: DocumentResult, slug: str) -> list[ReviewChange]:
    """Accept every candidate, in place: one ordinary Accept per fix, each marked as one of
    a bulk action in its record (`BULK_KEY` holds the kind's slug). Raises ReviewInvalid when
    there is none."""
    spec = BULK_KINDS[slug]
    waiting = candidates(result, slug)
    if not waiting:
        raise ReviewInvalid([FieldError(f"bulk-{slug}", spec.nothing)])
    changes = [apply_review(result, fix.element_id, "accept", {}) for fix in waiting]
    for change in changes:
        change.after[BULK_KEY] = slug
    return changes
