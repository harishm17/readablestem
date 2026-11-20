"""Notices that follow from the fixes, and one list of notices for the job page.

A notice (`core.types.Notice`) is something the job page lists beyond the review
decisions: "Not handled in this file" (review "required" or "none") and "Worth a look"
(review "optional"). It never adds a decision: when one is needed, the decision is the
fix with the same `element_id`, and only fixes count.

Some notices are facts about a fix, so they are derived here from the fixes rather than
written by the steps (the figure and table work only set their fix fields):

- `table_not_rebuilt` (required): a table shown as an image of the original, because
  the step did not rebuild it, a spend cap stopped it, or the reviewer kept the image;
- `scanned_page`, `handwritten_page`, `unread_page`, `page_read_failed` (required): the
  figure that stands for a page kept as its image (scanned, handwritten, painted with
  nothing read from it, or one that layout raised on), and `handwriting_on_page`
  (required): the image of handwriting on a typed page.
  Their decision is the figure's fix, one per reason for the whole document
  (`pipeline.page_type`, `run.PAGE_GROUP_PREFIX`);
- `table_header_missing` (optional): a table that goes on from the page before and has no
  header row, which could not be taken from the part before (`assemble_tables.carry_headers`),
  and `table_header_carried` (optional): one that is written with the header row of the part
  before, which the review card of the part does not show (the grid stored for it has none);
- `colour_only` (optional): a figure with `FigureFix.colour_only`;
- `long_description_missing` (optional): a chart or diagram without a long
  description, when long descriptions are on.

and two follow from layout's reasons on the text: `option_labels` and `rtl_text`
(required), a paragraph or list item that holds several answer options, or right-to-left
words in the order the PDF gives them. Each has a decision, the paragraph or list item's own
fix (`text_decisions`, reason `text.option_labels` or `text.rtl_order`), and its notice is
derived from that fix while the decision waits (`from_fixes`); a stored result from before
the decision existed has only the element's reason, and `from_reasons` still lists it, with
no decision behind it. And one follows from the text itself
(`from_runs`): `struck_text` (optional), a paragraph,
list item or heading with words the page strikes through, kept and marked as deleted
(`Element.runs`). A reviewer's text edit that changes the struck words takes the mark off,
and the notice goes with it.

Other notices (the text pass's links, colour and language codes, layout facts) come
with the result and are merged in unchanged. `current(result)` derives the fix-based
ones again from the fixes as they are now, so the job page stays true after review
decisions (a table rebuilt by hand is no longer "not rebuilt").

Content-free: codes, pages, element ids and counts only (rule 1).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from readablestem.core.page_checks import NOT_HANDLED_CODES, TABLE, Section, make
from readablestem.core.types import (
    KEPT_ORIGINAL_STATUSES,
    DocumentResult,
    Element,
    Fix,
    Kind,
    Notice,
    Status,
)

TABLE_NOT_REBUILT = "table_not_rebuilt"
SCANNED_PAGE = "scanned_page"
HANDWRITTEN_PAGE = "handwritten_page"
UNREAD_PAGE = "unread_page"
PAGE_READ_FAILED = "page_read_failed"
HANDWRITING_ON_PAGE = "handwriting_on_page"
COLOUR_ONLY = "colour_only"
LONG_DESCRIPTION_MISSING = "long_description_missing"
STRUCK_TEXT = "struck_text"
TABLE_HEADER_MISSING = "table_header_missing"
TABLE_HEADER_CARRIED = "table_header_carried"
# Text decisions layout raises on a paragraph or list item (`Element.reasons`), WS2.4a and
# SW54a: options run together in one block, and right-to-left words whose order the PDF's
# text layer does not give reliably. Both are "required": not handled in this file.
OPTION_LABELS = "option_labels"
RTL_TEXT = "rtl_text"
TEXT_CODES = (OPTION_LABELS, RTL_TEXT)
# The reason of the decision each of those raises (`Fix.reason`), in the order one is chosen
# when a block has both: words in the wrong order change what is read more than run-together
# options do. The decision is the fix of the element itself, the shape a scanned page's
# paragraph has, so the review card, the counts and carry-over need no second one.
RTL_ORDER_REASON = "text.rtl_order"
OPTION_LABELS_REASON = "text.option_labels"
TEXT_REASONS = {RTL_TEXT: RTL_ORDER_REASON, OPTION_LABELS: OPTION_LABELS_REASON}
TEXT_NOTICE_OF = {reason: code for code, reason in TEXT_REASONS.items()}
TEXT_DECISION_KINDS = (Kind.PARAGRAPH, Kind.LIST_ITEM)
# The reasons of figures kept as images before any model work; each is its own notice
# code (`pipeline.page_type`).
KEPT_IMAGE_CODES = frozenset(
    {SCANNED_PAGE, HANDWRITTEN_PAGE, UNREAD_PAGE, PAGE_READ_FAILED, HANDWRITING_ON_PAGE}
)
# Codes this module derives from the fixes; any stored notice with one of them is
# replaced by `current`.
DERIVED_CODES = frozenset(
    {
        TABLE_NOT_REBUILT,
        COLOUR_ONLY,
        LONG_DESCRIPTION_MISSING,
        STRUCK_TEXT,
        TABLE_HEADER_MISSING,
        TABLE_HEADER_CARRIED,
        *TEXT_CODES,
    }
) | (KEPT_IMAGE_CODES)
# Figures that need more than short alt text (W3C "complex images").
# Where a notice is listed on the job page: `core.page_checks.Section`, from the table.
# Notices that record something found and converted, with no decision and nothing to check
# (a footnote kept where it falls, a block of program code kept as code) are counted under
# "Handled in this file", never listed as left out (F-043): `HANDLED_CODES`.
# The language is a check on the document (SW20a): the owner confirms or changes it before the
# file is "Ready to post" (`web.states`), whatever `review` an older stored notice has:
# `LANGUAGE_CODES`, the table's rows that are always listed as not handled.
LANGUAGE_CODES = NOT_HANDLED_CODES

# A PDF repaired when it was opened (`source_facts.repair_notices`). `file_cut_off`: its last
# pages are empty. It is required until the owner continues with the pages before them
# (`continue_cut_off`), which sets its `review` to "none" and nothing else: the notice stays,
# because the pages are still not in the file, and the summary says so from it
# (`checked.cut_off_of`). `file_repaired`: damaged and repaired, content on every page.
CUT_OFF = "file_cut_off"
REPAIRED = "file_repaired"


def cut_off_pages(notices: Iterable[Notice], *, open_only: bool = False) -> list[int]:
    """The 0-based empty last pages of a PDF that looks cut off; with `open_only`, only while
    the owner has not answered."""
    return [
        page
        for n in notices
        if n.code == CUT_OFF and n.page is not None and (n.review == "required" or not open_only)
        for page in range(n.page, n.page + n.count)
    ]


def continue_cut_off(result: DocumentResult) -> DocumentResult:
    """The result with the owner's answer "continue with the pages before the empty ones":
    the notice no longer holds the document back. It is kept, so every surface still says the
    pages are not in the file."""
    notices = [
        n.model_copy(update={"review": "none"}) if n.code == CUT_OFF else n for n in result.notices
    ]
    return result.model_copy(update={"notices": notices})


def section(notice: Notice) -> Section:
    """ "Handled in this file" for what was found and converted (`HANDLED_CODES`), "Not
    handled in this file" for the other required and none, "Worth a look" for optional."""
    row = TABLE.get(notice.code)
    if row is not None and row.section is not None:
        return row.section
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
    from readablestem.pipeline import assemble_tables  # (it imports the layout, which imports this)

    elements = sorted(elements, key=lambda e: e.order)
    fixes = list(fixes)
    page_of = {e.id: e.page for e in elements}
    found: list[Notice] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, fix: Fix) -> None:
        if (code, fix.element_id) in seen:
            return
        seen.add((code, fix.element_id))
        found.append(make(code, page=page_of.get(fix.element_id), element_id=fix.element_id))

    for fix in fixes:
        if _not_rebuilt(fix):
            add(TABLE_NOT_REBUILT, fix)
        if fix.kind is Kind.FIGURE and fix.reason in KEPT_IMAGE_CODES:
            add(fix.reason, fix)
        if fix.figure is not None and fix.figure.colour_only:
            add(COLOUR_ONLY, fix)
        if long_descriptions and _missing_long_description(fix):
            add(LONG_DESCRIPTION_MISSING, fix)
        if fix.pending and fix.kind in TEXT_DECISION_KINDS and fix.reason in TEXT_NOTICE_OF:
            add(TEXT_NOTICE_OF[fix.reason], fix)
    first: dict[str, Fix] = {}
    for fix in fixes:
        first.setdefault(fix.element_id, fix)
    carried, refused = assemble_tables.carry_headers(elements, first)
    for part in refused:
        found.append(make(TABLE_HEADER_MISSING, page=part.page, element_id=part.id))
    for part in elements:
        if part.id in carried:
            found.append(make(TABLE_HEADER_CARRIED, page=part.page, element_id=part.id))
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
    derived = [
        *from_fixes(result.elements, result.fixes, long_descriptions=True),
        *from_runs(result.elements),
        *from_reasons(result.elements, result.fixes),
    ]
    kept = [n for n in result.notices if n.code not in DERIVED_CODES]
    return ordered(merge(derived, kept), result.elements)


def from_runs(elements: Iterable[Element]) -> list[Notice]:
    """`struck_text` for each element with struck runs, counting them ("Worth a look",
    never a decision: the words are kept and marked, and whether students need them at all
    is the instructor's call)."""
    return [
        make(STRUCK_TEXT, page=e.page, element_id=e.id, count=n)
        for e in elements
        if (n := sum(1 for r in e.runs if r.kind == "deleted"))
    ]


def from_reasons(elements: Iterable[Element], fixes: Iterable[Fix] = ()) -> list[Notice]:
    """`option_labels` and `rtl_text` (required) for each element layout flagged with that
    reason and that has no decision of its own: a result stored before the decision existed.
    Nothing stands behind that notice, so the job page lists it under "Not handled in this
    file" and holds the job at "Ready" (`web.states`). An element with the decision gets its
    notice from the fix (`from_fixes`), and loses it when the decision is made."""
    decided = {
        (f.element_id, TEXT_NOTICE_OF[f.reason]) for f in fixes if f.reason in TEXT_NOTICE_OF
    }
    return [
        make(code, page=e.page, element_id=e.id)
        for e in elements
        for code in TEXT_CODES
        if code in e.reasons and (e.id, code) not in decided
    ]


def text_decisions(elements: Iterable[Element], fixes: Mapping[str, Fix]) -> list[Fix]:
    """The decision on each paragraph or list item layout flagged (`option_labels`,
    `rtl_text`) that has no fix yet: `Fix(status=NEEDS_REVIEW, review="required")` with the
    reason `text.rtl_order` or `text.option_labels`. Its proposal is the element's own text
    (it has no field of its own), so the person says the words are right, or changes them
    with the page tools. One decision a block: when it has both flags the first of
    `TEXT_REASONS` is its reason, and the other still has its notice."""
    out: list[Fix] = []
    for element in elements:
        if element.kind not in TEXT_DECISION_KINDS or element.id in fixes:
            continue
        code = next((c for c in TEXT_REASONS if c in element.reasons), None)
        if code is not None:
            out.append(
                Fix(
                    element_id=element.id,
                    kind=element.kind,
                    status=Status.NEEDS_REVIEW,
                    review="required",
                    sources=["text"],
                    reason=TEXT_REASONS[code],
                )
            )
    return out
