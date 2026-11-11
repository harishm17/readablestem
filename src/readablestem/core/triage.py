"""Review load: which fixes need a person's decision, and how many decisions that makes.

The pipeline gives every fix a review tier (`Fix.review`, "meaning-level only" policy of
2026-10-08): `required` items block "done" and are counted; `optional` items are
accepted automatically and listed as notes; `none` items need nothing. Repeated headings
with weak evidence share a `group_key` and make one decision together, so counting is in
*decisions* (units), not fixes.

Everything here is content-free: the reason codes are check codes, `Fix.reason` codes or
the fixed words below, so the web app, the ops aggregates and the eval can all use them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from readablestem.core.types import DocumentResult, Fix, Kind

# Reason for a required fix with no failed check and no Fix.reason: the model's own
# confidence stayed below the auto-accept bar (after escalation, for equations).
BELOW_ACCEPT = "below_accept"
# Reason for a member of a group of repeated headings (`Fix.group_key`).
REPEATED_LABEL = "heading.repeated_label"
# Code of the second automated check's note on a figure (`figure_step.second_check`, ADR
# 0005). Optional by policy in this version, but unlike the other notes it says the alt
# text may not match the image, so the review screen never calls it something small.
# The figure step writes it as a literal, so the message-table test can find the code;
# tests/test_web_second_check_review.py checks the two agree.
SECOND_CHECK_MISMATCH = "second_check_mismatch"


def units(fixes: Iterable[Fix]) -> list[list[Fix]]:
    """Fixes as decisions: the members of one heading group together (in the order they
    come), every other fix on its own. Units keep the order of their first member."""
    out: list[list[Fix]] = []
    groups: dict[str, list[Fix]] = {}
    for fix in fixes:
        if fix.group_key:
            if fix.group_key not in groups:
                groups[fix.group_key] = []
                out.append(groups[fix.group_key])
            groups[fix.group_key].append(fix)
        else:
            out.append([fix])
    return out


def required_decisions(fixes: Iterable[Fix]) -> int:
    """Decisions the pipeline asked for (its tiers, whatever a reviewer did since)."""
    return len(units(f for f in fixes if f.review == "required"))


@dataclass(frozen=True)
class Decision:
    """One decision the pipeline asked for: where it is, what it is about and why."""

    page: int | None  # 0-based, the page of its first member; None when no element holds it
    kind: str
    reasons: tuple[str, ...]  # `tier_reasons` of its members, sorted, no repeats
    members: int = 1  # the fixes decided together (more than 1: a heading group)


def decisions(result: DocumentResult) -> list[Decision]:
    """The decisions `required_decisions` counts for a document, one row each, in order."""
    page_of = {element.id: element.page for element in result.elements}
    return [
        Decision(
            page=page_of.get(unit[0].element_id),
            kind=unit[0].kind.value,
            reasons=tuple(sorted({code for fix in unit for code in tier_reasons(fix)})),
            members=len(unit),
        )
        for unit in units(fix for fix in result.fixes if fix.review == "required")
    ]


def open_decisions(fixes: Iterable[Fix]) -> int:
    """Decisions still waiting now: a group counts while any member is waiting."""
    return len(units(f for f in fixes if f.pending))


def second_check_note(fix: Fix) -> bool:
    """An optional note nobody has acted on yet that comes from the second automated
    check: a possible mismatch between the alt text and the image."""
    return fix.optional_note and any(c.code == SECOND_CHECK_MISMATCH for c in fix.failed_checks)


def tier_reasons(fix: Fix) -> list[str]:
    """Why a fix has its tier, as content-free codes (sorted, no repeats).

    Required: its `Fix.reason` (failed, abstained), the group marker for a repeated
    heading, its failed checks, or `below_accept` when only the confidence held it back.
    Optional: its failed checks (the notes). None: nothing. Only a heading group is a
    repeated label: the pages kept as images with one reason ("pg-" groups) say so by
    their own reason.
    """
    if fix.review == "none":
        return []
    codes = {c.code or c.name for c in fix.checks if not c.passed}
    if fix.review == "required":
        if fix.reason:
            codes.add(fix.reason)
        if fix.group_key and fix.kind is Kind.HEADING:
            codes.add(REPEATED_LABEL)
        if not codes:
            codes.add(BELOW_ACCEPT)
    return sorted(codes)


@dataclass
class ReviewLoad:
    """Counts for one document (or many): what the pipeline asked a person to look at."""

    required_decisions: int = 0  # units: a heading group counts once
    required_fixes: int = 0
    optional: int = 0
    none: int = 0
    # Per kind (a fix's `Kind`: "equation", "figure", "heading", "table", and "paragraph"
    # for a page read from its picture) and per reason code.
    required_by_kind: Counter[str] = field(default_factory=Counter)  # decisions
    optional_by_kind: Counter[str] = field(default_factory=Counter)
    required_by_reason: Counter[str] = field(default_factory=Counter)  # fixes
    optional_by_reason: Counter[str] = field(default_factory=Counter)

    def add(self, other: ReviewLoad) -> None:
        self.required_decisions += other.required_decisions
        self.required_fixes += other.required_fixes
        self.optional += other.optional
        self.none += other.none
        self.required_by_kind.update(other.required_by_kind)
        self.optional_by_kind.update(other.optional_by_kind)
        self.required_by_reason.update(other.required_by_reason)
        self.optional_by_reason.update(other.optional_by_reason)

    def as_dict(self) -> dict:
        return {
            "required_decisions": self.required_decisions,
            "required_fixes": self.required_fixes,
            "optional": self.optional,
            "none": self.none,
            "required_by_kind": dict(sorted(self.required_by_kind.items())),
            "optional_by_kind": dict(sorted(self.optional_by_kind.items())),
            "required_by_reason": dict(sorted(self.required_by_reason.items())),
            "optional_by_reason": dict(sorted(self.optional_by_reason.items())),
        }


def review_load(fixes: Sequence[Fix]) -> ReviewLoad:
    """The pipeline's tiers for these fixes, whatever their kind: its `required_decisions`
    is `required_decisions(fixes)`, the number the review screen counts."""
    load = ReviewLoad()
    for unit in units(f for f in fixes if f.review == "required"):
        load.required_decisions += 1
        load.required_by_kind[unit[0].kind.value] += 1
    for fix in fixes:
        if fix.review == "required":
            load.required_fixes += 1
            load.required_by_reason.update(tier_reasons(fix))
        elif fix.review == "optional":
            load.optional += 1
            load.optional_by_kind[fix.kind.value] += 1
            load.optional_by_reason.update(tier_reasons(fix))
        else:
            load.none += 1
    return load
