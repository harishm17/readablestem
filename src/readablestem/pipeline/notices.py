"""Notices that follow from the fixes, and one list of notices for the job page.

A notice (`core.types.Notice`) is something the job page lists beyond the review
decisions: "Not handled in this file" (review "required" or "none") and "Worth checking"
(review "optional"). It never adds a decision: when one is needed, the decision is the
fix with the same `element_id`, and only fixes count (docs/design/content-features.md,
"Notices").

Some notices are facts about a fix, so they are derived here from the fixes rather than
written by the steps (the figure and table work only set their fix fields):

- `table_not_rebuilt` (required): a table shown as an image of the original, because
  the step did not rebuild it, a spend cap stopped it, or the reviewer kept the image;
- `scanned_page`, `handwritten_page`, `unread_page` (required): the figure that stands
  for a page kept as its image (scanned, handwritten, or painted with nothing read from
  it), and `handwriting_on_page` (required): the image of handwriting on a typed page.
  Their decision is the figure's fix, one per reason for the whole document
  (`pipeline.page_type`, `run.PAGE_GROUP_PREFIX`);
- `colour_only` (optional): a figure with `FigureFix.colour_only`;
- `long_description_missing` (optional): a chart or diagram without a long
  description, when long descriptions are on.

Other notices (the text pass's links, colour and language codes, layout facts) come
with the result and are merged in unchanged. `current(result)` derives the fix-based
ones again from the fixes as they are now, so the job page stays true after review
decisions (a table rebuilt by hand is no longer "not rebuilt").

Content-free: codes, pages, element ids and counts only (rule 1).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from readablestem.core.types import (
    KEPT_ORIGINAL_STATUSES,
    DocumentResult,
    Element,
    Fix,
    Kind,
    Notice,
)

TABLE_NOT_REBUILT = "table_not_rebuilt"
SCANNED_PAGE = "scanned_page"
HANDWRITTEN_PAGE = "handwritten_page"
UNREAD_PAGE = "unread_page"
HANDWRITING_ON_PAGE = "handwriting_on_page"
COLOUR_ONLY = "colour_only"
LONG_DESCRIPTION_MISSING = "long_description_missing"
# The reasons of figures kept as images before any model work; each is its own notice
# code (`pipeline.page_type`).
KEPT_IMAGE_CODES = frozenset({SCANNED_PAGE, HANDWRITTEN_PAGE, UNREAD_PAGE, HANDWRITING_ON_PAGE})
# Codes this module derives from the fixes; any stored notice with one of them is
# replaced by `current`.
DERIVED_CODES = frozenset({TABLE_NOT_REBUILT, COLOUR_ONLY, LONG_DESCRIPTION_MISSING}) | (
    KEPT_IMAGE_CODES
)
# Figures that need more than short alt text (W3C "complex images").
# Where a notice is listed on the job page.
Section = Literal["not_handled", "worth_checking"]


def section(notice: Notice) -> Section:
    """ "Not handled in this file" for required and none, "Worth checking" for optional."""
    return "worth_checking" if notice.review == "optional" else "not_handled"


def _not_rebuilt(fix: Fix) -> bool:
    """A table shown as an image of the original: the step did not rebuild it ("table
    not rebuilt", or a spend cap stopped it), or the reviewer kept the image. A table
    whose rebuilt grid is used (accepted, waiting, or rebuilt by the reviewer) is not."""
    return fix.kind is Kind.TABLE and fix.status in KEPT_ORIGINAL_STATUSES


def _missing_long_description(fix: Fix) -> bool:
    """A chart or diagram described with the long-description prompt but left without
    one: the figures work's own rule (`figure_step.long_description_missing`)."""
    from readablestem.pipeline import figure_step

    return fix.kind is Kind.FIGURE and figure_step.long_description_missing(fix)


def from_fixes(
    elements: Iterable[Element], fixes: Iterable[Fix], *, long_descriptions: bool
) -> list[Notice]:
    """The notices that follow from these fixes, one per (code, element), in reading
    order. `long_descriptions`: whether the run wrote long descriptions (the flag);
    without it, a chart without one is the expected output, not something to check."""
    page_of = {e.id: e.page for e in elements}
    found: list[Notice] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, fix: Fix, review: Literal["required", "optional", "none"]) -> None:
        if (code, fix.element_id) in seen:
            return
        seen.add((code, fix.element_id))
        found.append(
            Notice(code=code, page=page_of.get(fix.element_id), element_id=fix.element_id,
                   review=review)
        )  # fmt: skip

    for fix in fixes:
        if _not_rebuilt(fix):
            add(TABLE_NOT_REBUILT, fix, "required")
        if fix.kind is Kind.FIGURE and fix.reason in KEPT_IMAGE_CODES:
            add(fix.reason, fix, "required")
        if fix.figure is not None and fix.figure.colour_only:
            add(COLOUR_ONLY, fix, "optional")
        if long_descriptions and _missing_long_description(fix):
            add(LONG_DESCRIPTION_MISSING, fix, "optional")
    return found


def _key(notice: Notice) -> tuple[str, str | int | None]:
    """One notice per (code, element), or per (code, page) for those without one."""
    if notice.element_id is not None:
        return notice.code, notice.element_id
    return notice.code, notice.page


def merge(*groups: Iterable[Notice]) -> list[Notice]:
    """Every notice once: the first one for a (code, element); page-level notices for
    the same (code, page) add up their counts. Order: first seen."""
    merged: dict[tuple[str, str | int | None], Notice] = {}
    for group in groups:
        for notice in group:
            key = _key(notice)
            known = merged.get(key)
            if known is None:
                merged[key] = notice.model_copy()
            elif notice.element_id is None:
                known.count += notice.count
    return list(merged.values())


def ordered(notices: Iterable[Notice], elements: Iterable[Element] = ()) -> list[Notice]:
    """Whole-document notices first, then by page and reading order (stable)."""
    order = {e.id: e.order for e in elements}
    far = 1 << 30
    return sorted(
        notices,
        key=lambda n: (
            -1 if n.page is None else n.page,
            order.get(n.element_id or "", far if n.element_id else -1),
        ),
    )


def current(result: DocumentResult) -> list[Notice]:
    """The result's notices as the job page lists them now: the fix-based ones derived
    again from the fixes as they are (after review decisions), merged with the stored
    notices of every other code, in page and reading order.

    Whether a figure was described with long descriptions on is in its own model calls
    (the long prompt's version), so the notice follows the fix as it is now.
    """
    derived = from_fixes(result.elements, result.fixes, long_descriptions=True)
    kept = [n for n in result.notices if n.code not in DERIVED_CODES]
    return ordered(merge(derived, kept), result.elements)
