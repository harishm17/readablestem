"""Reviewer decisions on fixes: accept, edit or reject, applied to a `DocumentResult`.

The web route owns the transaction; this module owns the rules:

- accept: only a fix with a proposal that passed every error-level check (an abstained
  fix has nothing to accept, and an invalid proposal must be edited, which re-validates
  it; the reviewer can also keep the original by rejecting it), and never with a field
  of the form changed from the proposal (the change would be dropped silently: the
  reviewer is asked to Save edit it or undo it),
- edit: the reviewer's LaTeX / alt text / heading level (and a figure's long
  description, `pipeline.figure_details`) goes through the same cleaning
  and deterministic validators as model output (`equation_step.clean_latex`,
  `validate.clean_alt`, `pipeline.validate`) before it is stored; error-level failures
  are shown back to the reviewer, nothing is saved. LaTeX also gets the proposal's
  checks against the PDF's own text (`web.edit_check`: symbols, a vector's bold,
  signs): an edit that differs there where it changes the meaning is not used unless
  the reviewer chooses "Use my edit anyway" (`anyway`). An edit that changes nothing is
  refused too ("Nothing changed"), so it is never counted as a correction. A heading
  level the output's outline repair would change (one that skips a level after the
  heading before it) is refused, with the levels that fit; so is accepting a proposed
  level the repair would change (after a heading before it was rejected or changed),
- reject: the original stays in the document, flagged.

There is no bulk accept (removed 2026-10-09): every decision the pipeline asks for is
meaning-level ("Review tiers"), so each is opened and decided on its own; repeated
headings are already one decision (`apply_group`).

A table is edited one change at a time (`edit_table`, the "tables" section below): use
the grid read from the PDF (`Element.table`), make the first row or column a header or
not, or change one cell's text (a maths cell's LaTeX goes through an equation's edit
checks, `web.edit_check.check_cell_edit`, against the PDF's text in that cell: one that
differs where it changes the meaning is used only with "Use my edit anyway"). Each is
refused, with nothing saved, when it would leave the grid with a
structural problem it did not have (`core.types.grid_problems`).

A heading decision can change the level the output writes for later headings (the
children of a rejected heading move up together). Stored levels are not rewritten, so
restoring the heading puts them back; instead `heading_places` tells the review screen
the level each heading is written at, and `ReviewChange.moved` names the other headings
a decision moved.

It also decides the order of the review screen. A reviewer's time should go where the
risk is, so fixes are grouped by kind (equations, then figures, then tables, then
headings); within a
group the ones still waiting for a decision come first, in page and reading order, then
the ones accepted with an optional note (`Fix.review == "optional"`), then the ones
already accepted or decided (both collapsed on the page).

Decisions, not fixes, are what a reviewer makes: repeated headings that share a
`Fix.group_key` (`pipeline.heading_step`) are one decision. `apply_group` keeps them all
as headings, gives them all one level, or makes them all bold text, in one transaction.

Every review write counts itself on the job (`bump_result_version`, `jobs.result_version`),
and every decision form carries the number its page was drawn at: a form from a page that
is out of date (a tab left open while the document was changed in another one) is refused
(`is_stale`) instead of silently overwriting the newer decision.

Validators, speech and the HTML assembler are looked up on their modules at call time,
so tests (and other work packages' stubs) can be swapped in with monkeypatch.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from readablestem.core import triage
from readablestem.core.types import (
    KEPT_ORIGINAL_STATUSES,
    PENDING_STATUSES,
    Check,
    DocumentResult,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    grid_problems,
)
from readablestem.web.edit_check import MEANING_CODES
from readablestem.web.messages import explain_check

if TYPE_CHECKING:
    from readablestem.web.db import Job
    from readablestem.web.page_edits import StructureChange

logger = logging.getLogger(__name__)

ACTIONS = ("accept", "edit", "reject")
STATUS_FOR_ACTION = {"accept": Status.ACCEPTED, "edit": Status.EDITED, "reject": Status.REJECTED}
# A chart's data table read from the image is an optional extra, not in the student file
# (`figure_details.data_table_offered`): the reviewer adds it with this action, which
# changes nothing else about the figure (`add_data_table`).
ADD_DATA_TABLE = "add_data_table"
# "Describe it in words" on an item kept as an image (T3-16): the text becomes the image's
# alt text after the same checks as any alt text (`describe_in_words`).
DESCRIBE = "describe"
MAX_LATEX_CHARS = 5000
MAX_ALT_CHARS = 1000

# Review screen groups, in priority order, and the slug each has in URLs and anchors.
# A wrong table changes what a student reads as much as a wrong figure description, so
# tables come before headings.
GROUP_SLUGS = {
    Kind.EQUATION: "equations",
    Kind.FIGURE: "figures",
    Kind.TABLE: "tables",
    Kind.HEADING: "headings",
}
GROUP_KINDS = tuple(GROUP_SLUGS)
# Waiting for a decision: a proposal to check, or nothing usable (the reviewer can write
# one, or reject to confirm the original stays).
PENDING = PENDING_STATUSES
NOT_CONVERTED = frozenset({Status.FAILED, Status.ABSTAINED})


@dataclass
class FieldError:
    field_id: str  # id of the form control, for the error summary link
    message: str


class ReviewInvalid(Exception):
    """The reviewer's input was not saved. Messages are shown to that reviewer only and
    never logged; they may name what was typed (an unknown command), never other content."""

    def __init__(self, errors: list[FieldError], values: dict[str, str] | None = None) -> None:
        super().__init__("review input rejected")
        self.errors = errors
        self.values = values or {}  # the reviewer's input, re-shown in the form
        # The edit was refused only because it differs from the PDF where that changes the
        # meaning: the form offers "Use my edit anyway" (`apply_review(..., anyway=True)`).
        self.override = False


class ReviewStale(Exception):
    """The form was drawn before the job's last review write (another tab or window):
    nothing is applied (`is_stale`)."""


# --------------------------------------------------------------------------- versions

_VERSION = re.compile(r"[0-9]{1,9}")  # ASCII digits only, like _LEVEL


def bump_result_version(job: Job) -> int:
    """Count one more review write on the job (`jobs.result_version`) and return the new
    number. Called inside the write's transaction, after the job row was locked, so two
    writes never share a number; the forms of the page drawn after it carry it."""
    job.result_version = (job.result_version or 0) + 1
    return job.result_version


def is_stale(current: int | None, sent: str) -> bool:
    """A form drawn at another version than the job's now. A form without the field (a
    page opened before the guard existed, a script) is not stale: the guard is there for
    tabs left open, not as a lock."""
    sent = sent.strip()
    if not sent:
        return False
    return not _VERSION.fullmatch(sent) or int(sent) != (current or 0)


@dataclass
class ReviewChange:
    fix: Fix
    action: str
    before: dict[str, Any]
    after: dict[str, Any]
    warnings: list[Check] = field(default_factory=list)
    # Other headings the output now writes at a different level (reading order).
    moved: list[str] = field(default_factory=list)
    # What an edit changed, when "edited" alone does not say (a table: which cell, which
    # headers). Positions and fixed words only, never the document's text.
    note: str = ""
    # An edit used although it differs from the PDF where it changes the meaning.
    anyway: bool = False

    @property
    def element_id(self) -> str:
        return self.fix.element_id


@dataclass
class FixGroup:
    """One kind's fixes as the review screen shows them."""

    kind: Kind
    pending: list[Fix]  # waiting for a decision, page and reading order
    done: list[Fix]  # accepted (no note), edited or rejected, page and reading order
    optional: list[Fix] = field(default_factory=list)  # accepted with an optional note
    name: str = ""  # a slug of its own ("pages": the figures that are kept images)

    @property
    def slug(self) -> str:
        return self.name or GROUP_SLUGS[self.kind]

    @property
    def units(self) -> list[list[Fix]]:
        """The decisions waiting: one per fix, one per group of repeated headings."""
        return triage.units(self.pending)


# --------------------------------------------------------------------------- order


def needs_attention(status: Status | str) -> bool:
    return Status(status) in PENDING


def kind_for_slug(slug: str) -> Kind | None:
    return next((kind for kind, s in GROUP_SLUGS.items() if s == slug), None)


def _positions(result: DocumentResult) -> dict[str, tuple[int, int, int]]:
    """element_id -> (group rank, page, reading order): the review screen's sort key."""
    elements = {e.id: e for e in result.elements}
    far = 1 << 30  # a fix whose element is missing goes last in its group
    out = {}
    for fix in result.fixes:
        if fix.kind not in GROUP_SLUGS:
            continue
        element = elements.get(fix.element_id)
        page, order = (element.page, element.order) if element else (far, far)
        out[fix.element_id] = (GROUP_KINDS.index(fix.kind), page, order)
    return out


KEPT_IMAGES_SLUG = "pages"


def kept_images_apart(groups: list[FixGroup]) -> list[FixGroup]:
    """The figure group split in two for the list view: the figures, then the pages and
    handwriting kept as images (`is_kept_image`), which are not figures in words."""
    out: list[FixGroup] = []
    for group in groups:
        if group.kind is not Kind.FIGURE:
            out.append(group)
            continue
        for name, kept in (("", False), (KEPT_IMAGES_SLUG, True)):
            part = FixGroup(
                group.kind,
                pending=[f for f in group.pending if is_kept_image(f) is kept],
                done=[f for f in group.done if is_kept_image(f) is kept],
                optional=[f for f in group.optional if is_kept_image(f) is kept],
                name=name,
            )
            if part.pending or part.done or part.optional:
                out.append(part)
    return out


def group_fixes(result: DocumentResult) -> list[FixGroup]:
    """Non-empty groups in priority order (equations, figures, headings). Paragraph and
    list fixes, if a future step makes any, are not reviewable here."""
    position = _positions(result)
    groups = []
    for kind in GROUP_KINDS:
        fixes = sorted(
            (f for f in result.fixes if f.kind is kind), key=lambda f: position[f.element_id]
        )
        if fixes:
            pending = [f for f in fixes if f.status in PENDING]
            optional = [f for f in fixes if f.status not in PENDING and f.optional_note]
            done = [f for f in fixes if f.status not in PENDING and not f.optional_note]
            groups.append(FixGroup(kind, pending, done, optional))
    return groups


def item_numbers(result: DocumentResult) -> dict[str, int]:
    """element_id -> 1-based number within its kind, in reading order. Stable while the
    reviewer works (unlike a position in the review order, which changes after every
    decision), so "Equation 3" means the same equation in every message."""
    position = _positions(result)
    numbers: dict[str, int] = {}
    for kind in GROUP_KINDS:
        ordered = sorted(
            (f.element_id for f in result.fixes if f.kind is kind and not is_kept_image(f)),
            key=position.__getitem__,
        )
        numbers.update({element_id: i for i, element_id in enumerate(ordered, start=1)})
    # A page kept as an image is named by its page (`item_labels`), not as a figure.
    numbers.update({f.element_id: 0 for f in result.fixes if is_kept_image(f)})
    return numbers


# "Table 4" (or "Table 2.1") at the start of a table's caption, as printed.
_PRINTED_TABLE_NUMBER = re.compile(r"\s*table\s+(\d+(?:[.\-]\d+)*[a-z]?)\b", re.IGNORECASE)


def item_labels(result: DocumentResult) -> dict[str, str]:
    """element_id -> the item's name in messages ("Equation 3", "Table 4"). Numbers count
    within a kind (`item_numbers`), except that tables carry the number their captions
    print when every table has a "Table N" caption and no two share one: a table the
    detector missed would otherwise shift every number, and the card titled "Table 3"
    would show the caption "Table 4"."""
    from readablestem.web.views import KIND_LABELS

    numbers = item_numbers(result)
    labels: dict[str, str] = _kept_image_labels(result)
    for fix in result.fixes:
        if fix.element_id in numbers and fix.element_id not in labels:
            kind = KIND_LABELS.get(fix.kind, fix.kind.value.title())
            labels[fix.element_id] = f"{kind} {numbers[fix.element_id]}"
    captions = {e.id: e.caption for e in result.elements if e.kind is Kind.TABLE}
    printed: dict[str, str] = {}
    for fix in result.fixes:
        if fix.kind is not Kind.TABLE or fix.element_id in printed:
            continue
        found = _PRINTED_TABLE_NUMBER.match(captions.get(fix.element_id) or "")
        if found is None:
            return labels  # one without: ordinals for all, so no two names clash
        printed[fix.element_id] = found.group(1)
    if len(set(printed.values())) == len(printed):
        labels.update({eid: f"Table {number}" for eid, number in printed.items()})
    return labels


def _kept_image_labels(result: DocumentResult) -> dict[str, str]:
    """ "Page 3" for a page kept as an image; "Handwriting on page 3" for handwriting on a
    typed page ("Handwriting 2 on page 3" when there is more than one there)."""
    page_of = {e.id: e.page + 1 for e in result.elements}
    kept = [f for f in result.fixes if is_kept_image(f) and f.element_id in page_of]
    labels: dict[str, str] = {}
    regions: dict[int, list[str]] = {}
    for fix in kept:
        page = page_of[fix.element_id]
        if fix.reason in KEPT_PAGE_REASONS:
            labels[fix.element_id] = f"Page {page}"
        else:
            regions.setdefault(page, []).append(fix.element_id)
    position = _positions(result)
    for page, ids in regions.items():
        ids.sort(key=position.__getitem__)
        for n, element_id in enumerate(ids, start=1):
            which = f" {n}" if len(ids) > 1 else ""
            labels[element_id] = f"Handwriting{which} on page {page}"
    return labels


def next_to_review(result: DocumentResult, element_id: str) -> str | None:
    """The first decision still waiting after `element_id` in review order, wrapping
    round to the first one; None when nothing is left. A group of headings is one
    decision, named by its first member still waiting (`review_anchor` finds its card).

    After one heading of a group was decided on its own, the rest of its group is still
    the decision at hand, so that comes first."""
    position = _positions(result)
    fix = result.fix_for(element_id)
    own_group = fix.group_key if fix is not None else None
    if own_group:
        rest = [
            f.element_id for f in pending_members(result, own_group) if f.element_id != element_id
        ]
        if rest:
            return rest[0]
    heads = [
        unit[0].element_id
        for g in group_fixes(result)
        for unit in g.units
        if unit[0].element_id != element_id
    ]
    here = position.get(element_id)
    later = [e for e in heads if here is not None and position[e] > here]
    remaining = later or heads
    return remaining[0] if remaining else None


def review_anchor(result: DocumentResult, element_id: str) -> str:
    """The id of the card that shows this fix on the review screen while it waits: its
    own, or its heading group's."""
    fix = result.fix_for(element_id)
    if fix is not None and fix.group_key and fix.pending:
        return group_anchor(fix.group_key)
    return f"item-{element_id}"


def group_anchor(group_key: str) -> str:
    return f"group-card-{group_key}"


# --------------------------------------------------------------------------- one fix


def has_proposal(fix: Fix) -> bool:
    return any(p is not None for p in (fix.equation, fix.figure, fix.heading, fix.table))


def can_accept(fix: Fix) -> bool:
    """Only a proposal that passed every error-level check may be accepted as it is;
    anything else goes through "Save edit", which validates it again."""
    return has_proposal(fix) and not _blocking(fix.checks)


SNAPSHOT_FIELDS = frozenset(
    {"status", "equation", "figure", "heading", "table", "checks", "reviewed"}
)
# Fields added to snapshots later: a snapshot without one is still restorable, and puts
# back that field's default (`described` was not there before T3-16: None).
LATER_SNAPSHOT_FIELDS = frozenset({"described"})
# Beside the fields, a ReviewEdit's `after` holds the review version its write made (a
# number), so the edits of one write can be found again (`undo_last`), and `True` under
# ANYWAY_KEY for an edit used although it differs from the PDF (`apply_review`).
WRITE_KEY = "write"
ANYWAY_KEY = "anyway"
# Also `True` in `after`, for the record (T3-6): a decision made as one of many clean tables
# accepted in one action (T3-11), and a table cell the model read at the person's request.
BULK_KEY = "bulk"
MODEL_READ_KEY = "model_read"
# A `before` that holds a structural snapshot instead of a fix's (`web.page_edits.capture`).
STRUCTURE_KEY = "structure"


def snapshot(fix: Fix) -> dict[str, Any]:
    """What a ReviewEdit stores as before/after (deleted with the job): everything a
    decision can change, so `undo_last` can put it back. Snapshots stored before the
    checks and `reviewed` were in them cannot be undone."""
    return fix.model_dump(mode="json", include=set(SNAPSHOT_FIELDS | LATER_SNAPSHOT_FIELDS))


def field_id(prefix: str, element_id: str) -> str:
    return f"{prefix}-{element_id}"


def describe_failed(checks: list[Check]) -> str:
    """The reviewer-facing sentences for failed checks, with their codes in brackets."""
    explained = [explain_check(c) for c in checks]
    sentences = " ".join(e.text for e in explained)
    return f"{sentences} ({'; '.join(e.code for e in explained)})"


def apply_review(
    result: DocumentResult,
    element_id: str,
    action: str,
    form: Mapping[str, str],
    *,
    anyway: bool = False,
) -> ReviewChange:
    """Apply one decision to `result` in place. Raises LookupError or ReviewInvalid.

    `anyway`: the reviewer chose "Use my edit anyway" for LaTeX that differs from the
    PDF's text where it changes the meaning (`web.edit_check`); the failed checks stay
    on the fix, and `ReviewChange.anyway` says so."""
    fix = result.fix_for(element_id)
    if fix is None:
        raise LookupError(element_id)
    if action == DESCRIBE:
        return describe_in_words(result, fix, form)
    if is_kept_image(fix) and action != "reject":
        # A page (or handwriting) kept as an image has one decision, keep it: alt text or
        # "decorative" would take its "Not converted" mark, or the page, from students.
        raise ReviewInvalid([FieldError(field_id("actions", element_id), KEPT_IMAGE_ONLY)])
    if action == ADD_DATA_TABLE:
        return add_data_table(result, fix)
    if action not in ACTIONS:
        raise ReviewInvalid([FieldError(field_id("actions", element_id), "Choose an action.")])
    element = next((e for e in result.elements if e.id == element_id), None)
    before = snapshot(fix)
    warnings: list[Check] = []
    note = ""
    if action == "accept" and not has_proposal(fix):
        message = "There is no proposal to accept. Edit it, or reject it to keep the original."
        if fix.kind is Kind.TABLE:
            message = NO_TABLE_TO_ACCEPT
        raise ReviewInvalid([FieldError(field_id("actions", element_id), message)])
    if action == "accept" and not can_accept(fix):
        message = "This proposal failed a required check, so it cannot be accepted as it is. "
        message += "Edit it, or reject it to keep the original."
        raise ReviewInvalid([FieldError(field_id("actions", element_id), message)])
    if action == "accept" and fix.heading is not None:
        _refuse_moved_level(result, element_id, fix.heading.level, then="then Save edit")
    if action == "accept":
        _refuse_unsaved_change(fix, form)
    # Only a heading decision can move other headings: the outline before it, to compare.
    places = heading_places(result) if fix.kind is Kind.HEADING else {}
    overridden = False
    status = STATUS_FOR_ACTION[action]
    if action == "edit" and fix.kind is Kind.TABLE:
        warnings, note = edit_table(fix, element, form, anyway=anyway)
        overridden = anyway and any(c.code in MEANING_CODES for c in warnings)
        if table_still_open(warnings):
            # A table is changed one cell or setting at a time: while a required check
            # still fails (a header problem, a cell unlike the PDF, flattened maths), the
            # change is saved and the decision stays open, so one edit cannot settle a
            # table that is still wrong. Accept (or Keep as image) decides it.
            status = Status.NEEDS_REVIEW
    elif action == "edit":
        warnings = _edit(result, fix, element, form, anyway=anyway)
        overridden = anyway and any(c.code in MEANING_CODES for c in warnings)
    fix.status = status
    if status in {Status.ACCEPTED, Status.EDITED}:
        fix.described = None  # converted now: a description of the image is not used
    # A reviewer's Accept is not the pipeline's automatic one; an open table is undecided.
    fix.reviewed = status not in PENDING
    _recount(result)
    after = snapshot(fix)
    if overridden:
        after[ANYWAY_KEY] = True  # the ReviewEdit records the explicit choice
    return ReviewChange(
        fix=fix,
        action=action,
        before=before,
        after=after,
        warnings=warnings,
        moved=_moved_by(places, heading_places(result), element_id) if places else [],
        note=note,
        anyway=overridden,
    )


UNSAVED = "Choose Save edit to use your change, or {undo} to accept the proposal."


def _same_text(typed: str, stored: str) -> bool:
    """A textarea sends its line breaks as CRLF, and may gain blanks at either end."""
    return typed.replace("\r\n", "\n").strip() == stored.replace("\r\n", "\n").strip()


def _refuse_unsaved_change(fix: Fix, form: Mapping[str, str]) -> None:
    """Raise ReviewInvalid when Accept comes with a field changed from the proposal.

    Accept and Save edit share one form, so the fields always come with Accept; Accept
    keeps the proposal and never reads them. A reviewer who typed a correction and then
    pressed Accept out of habit would lose it without a word (and with it the fix the
    screen asked for), so that is refused with the change kept in the field. Only fields
    the form sent are compared: a POST without them (the list view's tests, scripts)
    accepts as before. Raises nothing for an unchanged form."""
    eid = fix.element_id
    if fix.equation is not None and "latex" in form:
        typed = form["latex"]
        if not _same_text(typed, fix.equation.latex):
            message = "You changed the LaTeX. " + UNSAVED.format(undo="undo it")
            raise ReviewInvalid([FieldError(field_id("latex", eid), message)], {"latex": typed})
    if fix.figure is not None and "alt" in form:
        from readablestem.pipeline import validate

        alt = validate.clean_alt(form["alt"])
        decorative = form.get("decorative") in {"on", "true", "1"}
        values = {"alt": alt, "decorative": "on" if decorative else "", **_details_values(form)}
        if decorative != fix.figure.decorative:
            message = "You changed “Decorative”. " + UNSAVED.format(undo="change it back")
            raise ReviewInvalid([FieldError(field_id("decorative", eid), message)], values)
        if not decorative and alt != validate.clean_alt(fix.figure.alt):
            message = "You changed the alt text. " + UNSAVED.format(undo="undo it")
            raise ReviewInvalid([FieldError(field_id("alt", eid), message)], values)
        _refuse_unsaved_details(fix, form, values)
    if fix.heading is not None and "level" in form and form["level"] != str(fix.heading.level):
        message = "You changed the level. " + UNSAVED.format(undo="set it back")
        raise ReviewInvalid([FieldError(field_id("level", eid), message)], {"level": form["level"]})


def _moved_by(
    before: Mapping[str, HeadingPlace], after: Mapping[str, HeadingPlace], element_id: str
) -> list[str]:
    """Headings other than `element_id` written as headings before and after a decision,
    at a different level."""
    return [
        eid
        for eid, place in after.items()
        if eid != element_id
        and eid in before
        and before[eid].in_document
        and place.in_document
        and before[eid].written != place.written
    ]


def _recount(result: DocumentResult) -> None:
    result.stats.by_status = dict(Counter(f.status.value for f in result.fixes))


def _edit(
    result: DocumentResult,
    fix: Fix,
    element: Element | None,
    form: Mapping[str, str],
    *,
    anyway: bool = False,
) -> list[Check]:
    if fix.kind is Kind.EQUATION:
        return _edit_equation(fix, element, form, anyway=anyway)
    if fix.kind is Kind.FIGURE:
        return _edit_figure(fix, element, form)
    if fix.kind is Kind.HEADING:
        return _edit_heading(result, fix, element, form)
    raise ReviewInvalid([FieldError(field_id("actions", fix.element_id), "This cannot be edited.")])


def _blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.passed and c.severity == "error"]


def plain_failed(checks: list[Check]) -> str:
    """The reviewer-facing sentences for failed checks, without codes (those stay in the
    "Details" of the item, for bug reports)."""
    return " ".join(explain_check(c).text for c in checks)


def _refuse_no_op(fix: Fix, fid: str, what: str, values: dict[str, str]) -> None:
    """Saving a field as it was is not a correction: say so instead of recording one (it
    would also count as an edit in ops). A rejected fix is the exception: saving its
    proposal unchanged puts it back in the document."""
    if fix.status is Status.REJECTED:
        return
    if fix.pending and can_accept(fix):
        message = f"Nothing changed. Choose Accept to keep it as it is, or change the {what} first."
    else:
        message = f"Nothing changed: this is the {what} it already has."
    raise ReviewInvalid([FieldError(fid, message)], values)


def _edit_equation(
    fix: Fix, element: Element | None, form: Mapping[str, str], *, anyway: bool = False
) -> list[Check]:
    from readablestem.web import edit_check

    fid = field_id("latex", fix.element_id)
    typed = form.get("latex", "").strip()
    values = {"latex": typed}  # re-shown as typed when it is refused
    if len(typed) > MAX_LATEX_CHARS:
        raise ReviewInvalid(
            [FieldError(fid, f"Keep the LaTeX under {MAX_LATEX_CHARS} characters.")], values
        )
    # The cleaning model output gets ("$v = at$" loses its dollars, a bare "%" is escaped
    # instead of silently dropping the rest of the line), then the proposal's checks.
    checked = edit_check.check_equation_edit(element, fix, typed)
    if checked.empty:
        raise ReviewInvalid([FieldError(fid, "Enter the LaTeX for this equation.")], values)
    if checked.unchanged:
        _refuse_no_op(fix, fid, "LaTeX", values)
    if checked.mathml is None:
        raise ReviewInvalid([FieldError(fid, edit_check.blocking_message(checked))], values)
    if checked.meaning and not anyway:
        refused = ReviewInvalid([FieldError(fid, edit_check.meaning_message(checked))], values)
        refused.override = True
        raise refused
    fix.equation = EquationFix(
        latex=checked.latex, mathml=checked.mathml, speech=checked.speech, display=checked.display
    )
    fix.checks = checked.checks
    return [c for c in fix.checks if not c.passed]


def _edit_figure(fix: Fix, element: Element | None, form: Mapping[str, str]) -> list[Check]:
    from readablestem.pipeline import validate

    fid = field_id("alt", fix.element_id)
    # What is stored is exactly what check_alt judges: invisible format characters (bidi
    # controls, zero-width spaces) removed and whitespace collapsed, as for model output.
    alt = validate.clean_alt(form.get("alt", ""))
    decorative = form.get("decorative") in {"on", "true", "1"}
    values = {"alt": alt, "decorative": "on" if decorative else "", **_details_values(form)}
    if (
        fix.figure is not None
        and decorative == fix.figure.decorative
        and (decorative or alt == validate.clean_alt(fix.figure.alt))
        and not _details_changed(fix, form)
    ):
        _refuse_no_op(fix, fid, "alt text", values)
    if decorative:
        alt = ""
    elif not alt:
        raise ReviewInvalid(
            [FieldError(fid, "Enter alt text, or mark the figure as decorative.")], values
        )
    if len(alt) > MAX_ALT_CHARS:
        raise ReviewInvalid(
            [FieldError(fid, f"Keep the alt text under {MAX_ALT_CHARS} characters.")], values
        )
    checks = list(
        validate.check_alt(alt, decorative=decorative, caption=element.caption if element else None)
    )
    if failed := _blocking(checks):
        raise ReviewInvalid(
            [FieldError(fid, "Your alt text was not saved. " + plain_failed(failed))], values
        )
    figure_type = fix.figure.figure_type if fix.figure else "other"
    details, detail_checks = _edited_details(fix, form, values, decorative=decorative)
    fix.figure = FigureFix(figure_type=figure_type, alt=alt, decorative=decorative, **details)
    fix.checks = checks + detail_checks
    return [c for c in checks if not c.passed]


# --------------------------------------------------------------------------- figure details

NO_TABLE_TO_ADD = (
    "There is no data table to add: it is already in the document, or the figure has none."
)


def add_data_table(result: DocumentResult, fix: Fix) -> ReviewChange:
    """ "Add the data table": the chart's data table read from the image goes into the
    student file (`FigureFix.data_table_added`). Nothing else about the figure changes;
    an automatic accept with this note becomes the reviewer's own (the note was acted
    on), and a figure still waiting for a decision keeps waiting. Undo puts it back.
    Refused for a figure kept as the original image: restore it first."""
    from readablestem.pipeline import figure_details

    figure = fix.figure
    fid = field_id("datatable", fix.element_id)
    if not figure_details.data_table_offered(figure) or figure is None:
        raise ReviewInvalid([FieldError(fid, NO_TABLE_TO_ADD)])
    if fix.status is Status.REJECTED:
        message = "The original image is kept for this figure. Accept or edit it first."
        raise ReviewInvalid([FieldError(fid, message)])
    before = snapshot(fix)
    fix.figure = figure.model_copy(update={"data_table_added": True})
    if fix.status is Status.ACCEPTED:
        fix.reviewed = True
    _recount(result)
    return ReviewChange(fix=fix, action=ADD_DATA_TABLE, before=before, after=snapshot(fix))


# --------------------------------------------------------------------------- describe

DESCRIBE_FIRST = (
    "Keep it as an image first: a description in words is for an item shown as an image."
)
DESCRIBE_EMPTY = "Write a description of it in words, or leave it as it is."


def can_describe(fix: Fix) -> bool:
    """An item shown as an image that a description in words can stand for (T3-16): an
    equation or a table whose original is kept (the reviewer's choice, or nothing usable
    was made), or a page or handwriting kept as an image. A figure has alt text of its
    own."""
    if fix.kind in {Kind.EQUATION, Kind.TABLE}:
        return fix.status in KEPT_ORIGINAL_STATUSES
    return is_kept_image(fix)


def describe_in_words(result: DocumentResult, fix: Fix, form: Mapping[str, str]) -> ReviewChange:
    """ "Describe it in words": the text becomes the alt text of the image the item is kept
    as. It is cleaned and checked exactly like a figure's alt text (invisible format
    characters out, whitespace collapsed, `validate.check_alt`: length, no "image of", no
    file name, no address or markup, not a placeholder), and written escaped like every
    alt text. Describing an item that waits for a decision keeps it as an image (it is
    the decision); an empty box takes a description away again. Raises ReviewInvalid."""
    from readablestem.pipeline import validate

    fid = field_id("description", fix.element_id)
    text = validate.clean_alt(form.get("description", ""))
    values = {"description": text}
    if not can_describe(fix):
        raise ReviewInvalid([FieldError(fid, DESCRIBE_FIRST)], values)
    current = fix.described or ""
    if text == current:
        message = DESCRIBE_EMPTY if not text else "Nothing changed: this is the description it has."
        raise ReviewInvalid([FieldError(fid, message)], values)
    if len(text) > MAX_ALT_CHARS:
        message = f"Keep the description under {MAX_ALT_CHARS} characters."
        raise ReviewInvalid([FieldError(fid, message)], values)
    warnings: list[Check] = []
    if text:
        checks = validate.check_alt(text, decorative=False)
        if failed := _blocking(checks):
            message = "Your description was not saved. " + plain_failed(failed)
            raise ReviewInvalid([FieldError(fid, message)], values)
        warnings = [c for c in checks if not c.passed]
    before = snapshot(fix)
    fix.described = text or None
    if text and fix.status is not Status.REJECTED:
        fix.status = Status.REJECTED  # described = kept as an image, by the reviewer
        fix.reviewed = True
    _recount(result)
    return ReviewChange(
        fix=fix, action=DESCRIBE, before=before, after=snapshot(fix), warnings=warnings
    )


# Long descriptions and chart data tables (FEATURE_LONG_DESCRIPTIONS, pipeline.figure_details).
# The split view's figure card sends `long_description` when the figure has one, and the
# `remove_data_table` checkbox when it has a table; a form without them (the list view)
# keeps both as they are, so an alt-text edit there never loses them.
_CHECKED = frozenset({"on", "true", "1"})
LONG_NOT_SAVED = (
    "The longer description was not saved: use plain sentences, at least 40 characters "
    "and at most 12 lines, with no web address or markup."
)


def _details_values(form: Mapping[str, str]) -> dict[str, str]:
    """The reviewer's long description and table choice, re-shown when a form is refused."""
    values: dict[str, str] = {}
    if "long_description" in form:
        values["long_description"] = form["long_description"].replace("\r\n", "\n")
    if form.get("remove_data_table") in _CHECKED:
        values["remove_data_table"] = "on"
    return values


def _details_changed(fix: Fix, form: Mapping[str, str]) -> bool:
    """The form changes the long description or removes the data table, so saving it is
    a change even when the alt text stays as it was (`_refuse_no_op`)."""
    from readablestem.pipeline import figure_details

    figure = fix.figure
    if figure is None:
        return False
    if "long_description" in form:
        typed = figure_details.clean_long_description(form["long_description"])
        if typed != (figure.long_description or ""):
            return True
    return form.get("remove_data_table") in _CHECKED and figure_details.data_table_shown(figure)


def _refuse_unsaved_details(fix: Fix, form: Mapping[str, str], values: dict[str, str]) -> None:
    """Accept with the long description changed, or "Remove the data table" ticked, would
    drop the change silently: refused like a changed alt text."""
    from readablestem.pipeline import figure_details

    figure, eid = fix.figure, fix.element_id
    if figure is None:
        return
    if "long_description" in form:
        typed = figure_details.clean_long_description(form["long_description"])
        if typed != (figure.long_description or ""):
            message = "You changed the longer description. " + UNSAVED.format(undo="undo it")
            raise ReviewInvalid([FieldError(field_id("longdesc", eid), message)], values)
    if form.get("remove_data_table") in _CHECKED and figure_details.data_table_shown(figure):
        message = "You chose to remove the data table. " + UNSAVED.format(undo="untick it")
        raise ReviewInvalid([FieldError(field_id("datatable", eid), message)], values)


def _edited_details(
    fix: Fix, form: Mapping[str, str], values: dict[str, str], *, decorative: bool
) -> tuple[dict[str, Any], list[Check]]:
    """The long description, data table and colour flag an edited figure keeps, and the
    checks that stay with them. A decorative figure keeps none of them (it is hidden
    from screen readers). The long description is cleaned and checked like the model's
    (`figure_details`); an empty one removes it. The data table cannot be edited, only
    removed; when it stays, so does its `figure.data_values` check (where its values
    came from is still true)."""
    from readablestem.pipeline import figure_details

    old = fix.figure
    if old is None or decorative:
        return {}, []
    long_description = old.long_description
    if "long_description" in form:
        fid = field_id("longdesc", fix.element_id)
        text = figure_details.clean_long_description(form["long_description"])
        if len(text) > figure_details.LONG_MAX_CHARS:
            limit = figure_details.LONG_MAX_CHARS
            message = f"Keep the longer description under {limit:,} characters."
            raise ReviewInvalid([FieldError(fid, message)], values)
        if text and not figure_details.long_description_ok(text):
            raise ReviewInvalid([FieldError(fid, LONG_NOT_SAVED)], values)
        long_description = text or None
    table, from_text, added = old.data_table, old.data_values_from_text, old.data_table_added
    if form.get("remove_data_table") in _CHECKED:
        table, from_text, added = None, None, False
    kept = table is not None
    checks = [c for c in fix.checks if c.name == "figure.data_values"] if kept else []
    details = {
        "long_description": long_description,
        "data_table": table,
        "data_values_from_text": from_text,
        "data_table_added": added,
        "colour_only": old.colour_only,
    }
    return details, checks


_LEVEL = re.compile(r"[1-6]")  # ASCII only: "²".isdigit() is True, int("²") fails


def heading_level_written(result: DocumentResult, element_id: str, level: int) -> int:
    """The level the output would show for this heading if it were given `level`.

    The assembler repairs the outline (`assemble.Outline`): a heading more than one
    level below the heading still open before it is moved up. Accepting such a level
    would store one level and show another, so review refuses it.
    """
    from readablestem.pipeline import assemble

    outline = assemble.outline_before(result.elements, result.fixes, element_id)
    return outline.would_write(level)


@dataclass(frozen=True)
class HeadingPlace:
    """One heading's place in the output's outline (`assemble.Outline`)."""

    highest: int  # levels 1 to this are written as they are after the heading before it
    level: int | None  # the fix's level; None without a proposal
    written: int | None  # what the output makes of `level` here (or would, if accepted)
    in_document: bool  # written as a heading now, not as a paragraph (rejected, failed)

    @property
    def moved(self) -> bool:
        """The output writes (or would write) another level than the fix's."""
        return self.level is not None and self.written != self.level

    @property
    def suggested(self) -> int | None:
        """The level to offer for this heading: the fix's, or when the output moves it,
        the level it is written at if that level keeps its place (else the deepest that
        does)."""
        if self.level is None or self.written is None:
            return self.level
        return min(self.written, self.highest) if self.moved else self.level


def heading_places(result: DocumentResult) -> dict[str, HeadingPlace]:
    """element_id -> its place, for every heading element. One pass in reading order,
    the way `assemble.render_html` writes the headings (the first fix for an id wins)."""
    from readablestem.pipeline import assemble

    first: dict[str, Fix] = {}
    for fix in result.fixes:
        first.setdefault(fix.element_id, fix)
    outline = assemble.Outline()
    places: dict[str, HeadingPlace] = {}
    for element in sorted(result.elements, key=lambda e: e.order):
        if element.kind is not Kind.HEADING:
            continue
        fix = first.get(element.id)
        level, _ = assemble.heading_parts(element, fix)
        stored = fix.heading.level if fix is not None and fix.heading is not None else None
        if element.id not in places:  # a repeated id (a layout bug): the first one
            places[element.id] = HeadingPlace(
                highest=max(n for n in range(1, 7) if outline.would_write(n) == n),
                level=stored,
                written=None if stored is None else outline.would_write(stored),
                in_document=level is not None,
            )
        if level is not None:
            outline.write(level)
    return places


def heading_moved(fix: Fix, places: Mapping[str, HeadingPlace]) -> bool:
    """The output writes this heading fix at another level than the one stored."""
    place = places.get(fix.element_id)
    return fix.kind is Kind.HEADING and place is not None and place.moved


def heading_levels_that_fit(result: DocumentResult, element_id: str) -> list[int]:
    """Levels the output keeps as they are for this heading: always 1 to n (n is at
    least 1; the first heading may take any level)."""
    return [
        level for level in range(1, 7) if heading_level_written(result, element_id, level) == level
    ]


def _refuse_moved_level(
    result: DocumentResult,
    element_id: str,
    level: int,
    *,
    then: str = "",
    values: dict[str, str] | None = None,
) -> None:
    """Raise ReviewInvalid, on the level select, when the output would write `level` as
    another level for this heading; `then` is what to do after choosing one that fits."""
    written = heading_level_written(result, element_id, level)
    if written == level:
        return
    highest = max(heading_levels_that_fit(result, element_id))
    message = (
        f"Level {level} would skip a level after the heading before it, so the "
        f"document would show it as level {written}. Choose a level from 1 to "
        f"{highest}{', ' + then if then else ''}, or change the heading before it first."
    )
    raise ReviewInvalid([FieldError(field_id("level", element_id), message)], values)


def _edit_heading(
    result: DocumentResult, fix: Fix, element: Element | None, form: Mapping[str, str]
) -> list[Check]:
    fid = field_id("level", fix.element_id)
    raw = form.get("level", "")
    if not _LEVEL.fullmatch(raw):
        raise ReviewInvalid(
            [FieldError(fid, "Choose a heading level from 1 to 6.")], {"level": raw}
        )
    level = int(raw)
    _refuse_moved_level(result, fix.element_id, level, values={"level": raw})
    if fix.heading is not None and fix.heading.level == level:
        _refuse_no_op(fix, fid, "level", {"level": raw})
    if fix.heading is not None:
        text = fix.heading.text
    else:
        text = (element.text if element else None) or ""
    fix.heading = HeadingFix(level=level, text=text)
    return []


# --------------------------------------------------------------------------- heading groups

GROUP_PREFIX = "hg-"  # pipeline.heading_step.GROUP_PREFIX (element ids start with "p")


def is_group_key(target: str) -> bool:
    return target.startswith(GROUP_PREFIX)


def group_members(result: DocumentResult, group_key: str) -> list[Fix]:
    """Every heading of one group, in reading order."""
    position = _positions(result)
    members = [f for f in result.fixes if f.group_key == group_key and f.kind is Kind.HEADING]
    return sorted(members, key=lambda f: position[f.element_id])


def pending_members(result: DocumentResult, group_key: str) -> list[Fix]:
    return [f for f in group_members(result, group_key) if f.pending]


@dataclass
class GroupOutcome:
    changes: list[ReviewChange]
    left: list[Fix]  # members still waiting (their level would move: decide them one by one)


def apply_group(
    result: DocumentResult,
    group_key: str,
    action: str,
    form: Mapping[str, str],
    *,
    every: bool = False,
) -> GroupOutcome:
    """One decision for every heading of a group still waiting, in reading order:

    - accept: keep each as a heading at its proposed level (one the output would write
      at another level is left waiting);
    - edit: give each the level in `form["level"]`; refused as a whole (nothing saved) if
      the output would move that level for any of them;
    - reject: make each plain bold text (not a heading).

    With `every`, the decision is made again for every heading of the group, decided or
    not (the review screen's "Change all 11" on a decided group): a heading already in
    that state is left as it is, and one the output would move is left as it was
    (`left`).

    Raises LookupError for an unknown group and ReviewInvalid for a bad level.
    """
    members = group_members(result, group_key) if every else pending_members(result, group_key)
    if not group_members(result, group_key):
        raise LookupError(group_key)
    fid = field_id("group-level", group_key)
    if action not in ACTIONS:
        raise ReviewInvalid([FieldError(field_id("group-actions", group_key), "Choose an action.")])
    level = None
    if action == "edit":
        raw = form.get("level", "")
        if not _LEVEL.fullmatch(raw):
            raise ReviewInvalid([FieldError(fid, "Choose a heading level from 1 to 6.")])
        level = int(raw)
    changes: list[ReviewChange] = []
    left: list[Fix] = []
    for fix in members:
        before = snapshot(fix)
        if every and _already(fix, action, level):
            continue
        if action == "accept":
            if not can_accept(fix) or fix.heading is None:
                left.append(fix)
                continue
            if heading_level_written(result, fix.element_id, fix.heading.level) != (
                fix.heading.level
            ):
                left.append(fix)
                continue
        elif action == "edit" and level is not None:
            written = heading_level_written(result, fix.element_id, level)
            if written != level:
                highest = max(heading_levels_that_fit(result, fix.element_id))
                message = (
                    f"Level {level} would skip a level after the heading before one of "
                    f"these, so the document would show it as level {written}. Choose a "
                    f"level from 1 to {highest} for all of them, or decide them one by one."
                )
                raise ReviewInvalid([FieldError(fid, message)], {"level": raw})
            text = fix.heading.text if fix.heading is not None else ""
            fix.heading = HeadingFix(level=level, text=text)
        fix.status = STATUS_FOR_ACTION[action]
        fix.reviewed = True
        changes.append(ReviewChange(fix=fix, action=action, before=before, after=snapshot(fix)))
    if changes:
        _recount(result)
    return GroupOutcome(changes, left)


# --------------------------------------------------------------------------- kept images

# Pages and handwriting the pipeline kept as images before any model work
# (pipeline.page_type; there is no handwriting or scan conversion). The fixes with one
# reason share a group key ("pg-" + the first one's element id, pipeline.run) and are one
# decision: keep them as images, marked "Not converted". A kept image has no proposal,
# so the decision is the same as "Keep as image" (reject) on each of them.
PAGE_GROUP_PREFIX = "pg-"  # pipeline.run.PAGE_GROUP_PREFIX
KEPT_IMAGE_REASONS = frozenset(
    {"handwritten_page", "scanned_page", "unread_page", "handwriting_on_page"}
)
KEPT_PAGE_REASONS = frozenset({"handwritten_page", "scanned_page", "unread_page"})
KEPT_IMAGE_ONLY = (
    "This stays an image of the original, marked “Not converted”: there is nothing else "
    "to choose for it."
)


def is_kept_image(fix: Fix) -> bool:
    """A page (or a region of handwriting) kept as an image before any model work."""
    return fix.kind is Kind.FIGURE and fix.reason in KEPT_IMAGE_REASONS


def is_page_group_key(target: str) -> bool:
    return target.startswith(PAGE_GROUP_PREFIX)


def page_group_members(result: DocumentResult, group_key: str) -> list[Fix]:
    """Every kept image of one group, in reading order."""
    position = _positions(result)
    members = [f for f in result.fixes if f.group_key == group_key and is_kept_image(f)]
    return sorted(members, key=lambda f: position[f.element_id])


def apply_page_group(result: DocumentResult, group_key: str) -> GroupOutcome:
    """Keep every image of the group still waiting as it is, marked "Not converted" (each
    one's "Keep as image"). Raises LookupError for an unknown group."""
    members = page_group_members(result, group_key)
    if not members:
        raise LookupError(group_key)
    changes = [apply_review(result, f.element_id, "reject", {}) for f in members if f.pending]
    return GroupOutcome(changes, [])


def _already(fix: Fix, action: str, level: int | None) -> bool:
    """The heading is already where this group decision would put it."""
    if action == "reject":
        return fix.status is Status.REJECTED
    if action == "accept":
        return fix.status in {Status.ACCEPTED, Status.EDITED}  # a heading, at a level
    return (
        fix.status in {Status.ACCEPTED, Status.EDITED}
        and fix.heading is not None
        and fix.heading.level == level
    )


# --------------------------------------------------------------------------- tables

# The changes a table edit can make (the form's `change` field).
TABLE_CHANGES = ("cell", "headers", "use_local", "not_a_table")
NOT_A_TABLE_NOTE = "It is now written as text, one cell after another, not as a table."
NO_TEXT_GRID = (
    "There is no text to write for this grid (it has none, or holds maths the PDF gives flat). "
    "Keep the image instead."
)
USE_LOCAL_NOTE = "The table now uses the grid read from the PDF."
MAX_CELL_CHARS = 1000
NO_TABLE_TO_ACCEPT = (
    "There is no rebuilt table to accept. Use the grid read from the PDF, if there is one, "
    "or keep the image."
)

# What each `core.types.grid_problems` code means, for the table card and for an edit
# that is refused. Positions and fixed words only.
GRID_PROBLEM_SENTENCES = {
    "no_cells": "The table has no cells.",
    "cell_out_of_bounds": "A cell reaches past the edge of the table.",
    "cells_overlap": "Two cells cover the same place in the table.",
    "grid_hole": "There is a gap in the table where no cell is.",
    "headers_cover_table": "The headers would cover the whole table, leaving no row or "
    "column for data.",
    "header_span_into_body": "A cell in the header row reaches down into the rows below it.",
    "header_below_body": "A column header sits below the header rows.",
    "data_in_header_rows": "A cell in the header row is not marked as a header.",
    "data_in_header_cols": "A cell in the header column is not marked as a header.",
}


def grid_problem_sentence(code: str) -> str:
    return GRID_PROBLEM_SENTENCES.get(code, "The table's grid has a problem.")


def usable_local_grid(element: Element | None) -> TableGrid | None:
    """The grid layout read from the PDF's own words and lines (`Element.table`), when it
    could be used as it is: a data table with cells and no structural problem."""
    grid = element.table if element is not None else None
    if grid is None or not grid.is_data or not grid.cells or grid_problems(grid):
        return None
    return grid


def first_row_is_header(grid: TableGrid) -> bool:
    return grid.header_rows > 0


def first_col_is_header(grid: TableGrid) -> bool:
    """The output makes the first column's body cells row headers: `header_cols` says so,
    or every non-empty one of them already is one (a model may mark the cells and not
    the count)."""
    if grid.header_cols > 0:
        return True
    firsts = [c for c in _first_col_body(grid) if not c.is_empty]
    return bool(firsts) and all(c.header == "row" for c in firsts)


def _first_col_body(grid: TableGrid) -> list[TableCell]:
    """Cells anchored in the first column below the header rows, other than section
    headings (a row header across the whole width)."""
    return [
        c
        for c in grid.cells
        if c.col == 0 and c.row >= grid.header_rows and not (c.header and c.col_span >= grid.cols)
    ]


def with_header_row(grid: TableGrid, on: bool) -> TableGrid:
    """The grid with its first row as the column headers, or with no header rows (every
    column header becomes a data cell)."""
    if on == first_row_is_header(grid):
        return grid
    cells = [c.model_copy() for c in grid.cells]
    for cell in cells:
        if on and cell.row == 0 and cell.header is None:
            cell.header = "col"
        elif not on and cell.header == "col":
            cell.header = None
    return grid.model_copy(update={"cells": cells, "header_rows": 1 if on else 0})


def with_header_col(grid: TableGrid, on: bool) -> TableGrid:
    """The grid with its first column as row headers (below the header rows), or with
    none (those cells become data cells; section headings stay)."""
    if on == first_col_is_header(grid):
        return grid
    first = {id(c) for c in _first_col_body(grid)}
    cells = []
    for cell in grid.cells:
        copy = cell.model_copy()
        if id(cell) in first:
            if on and copy.header is None:
                copy.header = "row"
            elif not on and copy.header == "row":
                copy.header = None
        cells.append(copy)
    return grid.model_copy(update={"cells": cells, "header_cols": 1 if on else 0})


def cell_label(cell: TableCell) -> str:
    """Where a cell is, 1-based ("Row 2, column 3"), with the rows or columns it spans."""
    rows = f"row {cell.row + 1}"
    if cell.row_span > 1:
        rows = f"rows {cell.row + 1} to {cell.row + cell.row_span}"
    cols = f"column {cell.col + 1}"
    if cell.col_span > 1:
        cols = f"columns {cell.col + 1} to {cell.col + cell.col_span}"
    return f"{rows}, {cols}".capitalize()


def is_maths_cell(grid: TableGrid, cell: TableCell) -> bool:
    """Edited as LaTeX: a maths cell, or a cell of the grid read from the PDF that holds
    flattened maths (`assemble_tables.flattened_cells`)."""
    from readablestem.pipeline.assemble_tables import flattened_cells

    return cell.latex is not None or any(c is cell for c in flattened_cells(grid))


def find_cell(grid: TableGrid, row: int, col: int) -> TableCell | None:
    """The cell anchored at (row, col), 0-based; the first one when a broken grid has two."""
    return next((c for c in grid.cells if c.row == row and c.col == col), None)


_INDEX = re.compile(r"[0-9]{1,4}")  # ASCII digits only, as for heading levels


def edit_table(
    fix: Fix, element: Element | None, form: Mapping[str, str], *, anyway: bool = False
) -> tuple[list[Check], str]:
    """Apply one table edit (`form["change"]`, one of TABLE_CHANGES) to `fix.table`.
    Returns (failed checks to warn about, what changed in words). Raises ReviewInvalid,
    with nothing changed, for an edit the page never offers or one that fails a check.
    `anyway`: a maths cell's LaTeX that differs from the PDF where it changes the
    meaning is used ("Use my edit anyway"), as for an equation."""
    eid = fix.element_id
    change = form.get("change", "")
    if change == "use_local":
        grid = usable_local_grid(element)
        if grid is None:
            message = "There is no grid read from the PDF that could be used for this table."
            raise ReviewInvalid([FieldError(field_id("actions", eid), message)])
        fix.table = TableFix(grid=grid.model_copy(deep=True))
        fix.checks = table_checks(fix.table.grid, element)
        failed = [c for c in fix.checks if not c.passed]
        return failed, USE_LOCAL_NOTE
    if change == "not_a_table":
        grid = grid_as_text(fix, element)
        if grid is None:
            raise ReviewInvalid([FieldError(field_id("actions", eid), NO_TEXT_GRID)])
        fix.table = TableFix(grid=grid)
        fix.checks = []  # the reviewer's own answer: nothing left to check against
        return [], NOT_A_TABLE_NOTE
    if fix.table is None:
        message = (
            "There is no rebuilt table to change. Use the grid read from the PDF, if there "
            "is one, or keep the image."
        )
        raise ReviewInvalid([FieldError(field_id("actions", eid), message)])
    if not fix.table.grid.is_data and change in {"headers", "cell"}:
        message = (
            "This table is written as text, not as a table. To change it, use the grid read "
            "from the PDF first."
        )
        raise ReviewInvalid([FieldError(field_id("actions", eid), message)])
    if change == "headers":
        return _edit_headers(fix, fix.table.grid, element, form)
    if change == "cell":
        return _edit_cell(fix, fix.table.grid, element, form, anyway=anyway)
    raise ReviewInvalid([FieldError(field_id("actions", eid), "Choose what to change.")])


def grid_as_text(fix: Fix, element: Element | None) -> TableGrid | None:
    """The grid to write as text when the reviewer says "this grid is not a table": what the
    table step writes for a grid it judged not to be data (`table_step.text_grid`: the grid
    read from the PDF's own words, `is_data` False), else the proposal's grid written the
    same way. None when it has no text, holds maths the PDF gives flat (written out, that
    would read as something else), or is already written as text."""
    from readablestem.pipeline import table_step

    if (
        fix.table is not None
        and not fix.table.grid.is_data
        and fix.status not in KEPT_ORIGINAL_STATUSES
    ):
        return None
    if element is not None:
        local = table_step.text_grid(element)
        if local is not None:
            return local
    if fix.table is None:
        return None
    grid = fix.table.grid.model_copy(update={"is_data": False})
    from readablestem.pipeline.assemble_tables import flattened_cells

    if flattened_cells(grid) or not any(c.text.strip() for c in grid.cells):
        return None
    return grid


def cell_region(
    fix: Fix, element: Element, row: str, col: str
) -> tuple[TableGrid, TableCell, tuple[float, float, float, float]] | str:
    """The (grid, cell, box on the page) to read, or a sentence saying why not. The cell
    must be a maths cell of the proposal with a place on the page: its own box, else the
    box of the cell of the grid read from the PDF that stands in its place."""
    if fix.table is None or not fix.table.grid.is_data:
        return "This table has no cells to read."
    if not (row.isascii() and row.isdigit() and col.isascii() and col.isdigit()):
        return "That cell is not in the table any more. Choose it again from the list."
    grid = fix.table.grid
    cell = find_cell(grid, int(row), int(col))
    if cell is None:
        return "That cell is not in the table any more. Choose it again from the list."
    if not is_maths_cell(grid, cell):
        return "Only a cell that holds maths is read as an equation."
    box = cell.bbox
    if box is None and element.table is not None:
        counterpart = find_cell(element.table, cell.row, cell.col)
        box = counterpart.bbox if counterpart is not None else None
    if box is None:
        return "This cell has no place on the page to read from. Type the LaTeX yourself."
    return grid, cell, box


def table_still_open(failed: list[Check]) -> bool:
    """After a table edit: does a required-tier check still fail (anything but the table
    step's notes)? Then the decision stays open (`apply_review`)."""
    from readablestem.pipeline import table_step

    return any(c.code not in table_step.NOTE_CODES for c in failed)


def table_checks(grid: TableGrid, element: Element | None) -> list[Check]:
    """The table step's own checks of a grid against the PDF, run again after a change, so
    the card shows what is still wrong with the table as it is now (a cell put right
    clears its finding; one still wrong keeps it).

    They are `pipeline.table_step.table_checks(grid, element)` (the grid's structure and
    headers, agreement with the grid read from the PDF, the cells' words, the maths
    cells), pure functions of the stored grid and element, looked up at call time like
    the other validators. A table whose element is gone has none; structural problems
    are refused by the edit itself either way (`_refuse_new_problems`)."""
    from readablestem.pipeline import table_step

    if element is None:
        return []
    return list(table_step.table_checks(grid, element))


def _refuse_new_problems(
    before: TableGrid, after: TableGrid, fid: str, what: str, values: dict[str, str]
) -> None:
    """Raise ReviewInvalid when `after` has a structural problem `before` did not have
    (one the proposal already had is left for the reviewer to judge)."""
    new = [p for p in grid_problems(after) if p not in grid_problems(before)]
    if new:
        sentences = " ".join(grid_problem_sentence(p) for p in new)
        message = f"{what} not saved. {sentences} ({'; '.join(new)})"
        raise ReviewInvalid([FieldError(fid, message)], values)


def _edit_headers(
    fix: Fix, grid: TableGrid, element: Element | None, form: Mapping[str, str]
) -> tuple[list[Check], str]:
    fid = field_id("header-row", fix.element_id)
    row = form.get("header_row") in {"on", "true", "1"}
    col = form.get("header_col") in {"on", "true", "1"}
    values = {"header_row": "on" if row else "", "header_col": "on" if col else ""}
    if row == first_row_is_header(grid) and col == first_col_is_header(grid):
        message = "The headers are already like that. Tick or untick a box, then Save headers."
        raise ReviewInvalid([FieldError(fid, message)], values)
    edited = with_header_col(with_header_row(grid, row), col)
    _refuse_new_problems(grid, edited, fid, "The headers were", values)
    fix.table = TableFix(grid=edited)
    fix.checks = table_checks(edited, element)
    first_row = "The first row is a header" if row else "The first row is not a header"
    first_col = "the first column is a header" if col else "the first column is not a header"
    return [c for c in fix.checks if not c.passed], f"{first_row}, and {first_col}."


def _edit_cell(
    fix: Fix,
    grid: TableGrid,
    element: Element | None,
    form: Mapping[str, str],
    *,
    anyway: bool = False,
) -> tuple[list[Check], str]:
    from readablestem.pipeline import table_step, validate
    from readablestem.web import edit_check

    eid = fix.element_id
    fid = field_id("cell", eid)
    raw_row, raw_col = form.get("row", ""), form.get("col", "")
    typed = form.get("cell", "")
    values = {"row": raw_row, "col": raw_col, "cell": typed}
    cell = None
    if _INDEX.fullmatch(raw_row) and _INDEX.fullmatch(raw_col):
        cell = find_cell(grid, int(raw_row), int(raw_col))
    if cell is None:
        message = "That cell is not in the table any more. Choose it again from the list."
        raise ReviewInvalid([FieldError(field_id("cell-pick", eid), message)])
    edited = cell.model_copy()
    checks: list[Check] = []
    if is_maths_cell(grid, cell):
        # A maths cell: its LaTeX goes through an equation's edit checks, against the
        # PDF's text in that cell (web.edit_check): one that differs where it changes
        # the meaning is used only with "Use my edit anyway". A cell of the grid read
        # from the PDF whose maths its text gives flat is one too: it is written as
        # LaTeX (the PDF's text stays, for the checks).
        if len(typed) > MAX_LATEX_CHARS:
            message = f"Keep the LaTeX under {MAX_LATEX_CHARS} characters."
            raise ReviewInvalid([FieldError(fid, message)], values)
        layer = table_step.cell_text_layer(grid, cell, element)
        checked = edit_check.check_cell_edit(layer, typed, current=cell.latex)
        if checked.empty:
            raise ReviewInvalid([FieldError(fid, "Enter the LaTeX for this cell.")], values)
        if checked.mathml is None:
            message = edit_check.blocking_message(checked)
            raise ReviewInvalid([FieldError(fid, message)], values)
        if checked.unchanged:
            raise ReviewInvalid(
                [FieldError(fid, "This is the LaTeX the cell has already.")], values
            )
        if checked.meaning and not anyway:
            refused = ReviewInvalid([FieldError(fid, edit_check.meaning_message(checked))], values)
            refused.override = True
            raise refused
        edited.latex, edited.mathml = checked.latex, checked.mathml
        checks = checked.checks
    else:
        # What is stored is what is shown: invisible format characters (bidi controls,
        # zero-width spaces) removed and whitespace collapsed, as for alt text.
        text = validate.clean_alt(typed)
        if len(text) > MAX_CELL_CHARS:
            message = f"Keep the text of one cell under {MAX_CELL_CHARS} characters."
            raise ReviewInvalid([FieldError(fid, message)], values)
        if text == cell.text:
            raise ReviewInvalid([FieldError(fid, "This is the text the cell has already.")], values)
        edited.text = text
    # Text typed into an empty cell of the header rows or column is a header too.
    if edited.header is None and not edited.is_empty:
        if edited.row < grid.header_rows:
            edited.header = "col"
        elif edited.col < grid.header_cols:
            edited.header = "row"
    cells = [edited if c is cell else c for c in grid.cells]
    after = grid.model_copy(update={"cells": cells})
    _refuse_new_problems(grid, after, fid, "The cell was", values)
    fix.table = TableFix(grid=after)
    fix.checks = checks + table_checks(after, element)
    return [c for c in fix.checks if not c.passed], f"{cell_label(cell)} changed."


# --------------------------------------------------------------------------- clean tables

NO_CLEAN_TABLES = "No table is waiting with nothing wrong found in it."


def is_clean_table(fix: Fix, element: Element | None = None) -> bool:
    """A table that waits for a decision although nothing was found wrong with it (the model
    was not sure enough of it to accept it): a data table with a proposal, no failed check
    of any kind, no structural problem and no maths the PDF gives flat. Accepting it is the
    answer. A table with a finding, a table kept as an image and a table written as text
    are each their own decision."""
    from readablestem.pipeline.assemble_tables import flattened_cells

    if fix.kind is not Kind.TABLE or fix.status is not Status.NEEDS_REVIEW:
        return False
    table = fix.table
    return (
        table is not None
        and table.grid.is_data
        and can_accept(fix)
        and not fix.failed_checks
        and not grid_problems(table.grid)
        and not flattened_cells(table.grid)
    )


def clean_tables(result: DocumentResult) -> list[Fix]:
    """The clean tables still waiting, in page and reading order."""
    position = _positions(result)
    elements = {e.id: e for e in result.elements}
    found = [f for f in result.fixes if is_clean_table(f, elements.get(f.element_id))]
    return sorted(found, key=lambda f: position.get(f.element_id, (1 << 30, 1 << 30, 1 << 30)))


def accept_clean_tables(result: DocumentResult) -> list[ReviewChange]:
    """Accept every clean table at once (T3-11): one ordinary "Accept" per table, so each is
    recorded and can be undone together. Raises ReviewInvalid when there is none."""
    waiting = clean_tables(result)
    if not waiting:
        raise ReviewInvalid([FieldError(field_id("tables", "accept"), NO_CLEAN_TABLES)])
    return [apply_review(result, fix.element_id, "accept", {}) for fix in waiting]


# --------------------------------------------------------------------------- undo

UNDO_ACTION = "undo"  # ReviewEdit.action of an undo (a write of its own)


def restorable(before: object) -> bool:
    """A `before` snapshot `undo_last` can put back: it has every field a decision can
    change (snapshots stored before the checks were in them do not), or it is a structural
    snapshot (`web.page_edits`)."""
    return isinstance(before, Mapping) and (
        before.keys() >= SNAPSHOT_FIELDS or isinstance(before.get(STRUCTURE_KEY), Mapping)
    )


def undo_last(
    result: DocumentResult, edits: list[tuple[str, Mapping[str, Any]]]
) -> list[ReviewChange | StructureChange]:
    """Put back, in place, what one write changed: `edits` are its ReviewEdits as
    (element id, before snapshot). One ReviewChange per fix (action "undo"). Raises
    LookupError when a fix is gone or a snapshot cannot be put back; nothing is changed
    then (the route's transaction is rolled back)."""
    changes: list[ReviewChange | StructureChange] = []
    for element_id, before in edits:
        if not restorable(before):
            raise LookupError(element_id)
        if isinstance(before.get(STRUCTURE_KEY), Mapping):
            from readablestem.web import page_edits

            try:
                changes.append(page_edits.undo(result, element_id, dict(before[STRUCTURE_KEY])))
            except (ValueError, KeyError, TypeError):
                raise LookupError(element_id) from None
            continue
        fix = result.fix_for(element_id)
        if fix is None:
            raise LookupError(element_id)
        current = snapshot(fix)
        merged = (
            fix.model_dump(mode="json")
            | {name: before[name] for name in SNAPSHOT_FIELDS}
            | {name: before.get(name) for name in LATER_SNAPSHOT_FIELDS}
        )
        restored = Fix.model_validate(merged)
        for name in SNAPSHOT_FIELDS | LATER_SNAPSHOT_FIELDS:
            setattr(fix, name, getattr(restored, name))
        changes.append(
            ReviewChange(fix=fix, action=UNDO_ACTION, before=current, after=snapshot(fix))
        )
    if changes:
        _recount(result)
    return changes


# --------------------------------------------------------------------------- output


def rerender_html(
    result: DocumentResult,
    crops: dict[str, bytes],
    *,
    checked_on: date | None = None,
    title_override: str | None = None,
    contact: str | None = None,
    math_alttext: bool | None = None,
) -> str:
    """The output HTML after a review decision, from the same assembler the pipeline uses.

    The static checks of the new HTML replace `result.html_checks`, so the job page lists
    the checks of the file the user downloads, not of the one before the review.
    `checked_on` is the day of the last decision for the student note ("checked by your
    instructor on ..." once no decision is open); by default today (UTC), since a
    re-render follows a decision. The assembler ignores it while a decision is open.
    `math_alttext` is the app's `Settings.mathml_alttext`, the setting the first render used
    (T2-24): the file made again must read like the first one (None: the process settings).
    """
    from readablestem.pipeline import assemble, html_checks

    # The instructor's choices are passed only when there are any, so a file made without
    # them is made exactly as before.
    chosen: dict[str, Any] = {}
    if title_override:
        chosen |= {"title_set": True}
    if contact:
        chosen |= {"contact": contact}
    html = assemble.render_html(
        title=title_override or result.title,
        **chosen,
        lang=result.lang,
        elements=result.elements,
        fixes=result.fixes,
        crops=crops,
        source_note=None,
        checked_on=checked_on or datetime.now(UTC).date(),
        math_alttext=math_alttext,
        **({"omitted_pages": result.omitted_pages} if result.omitted else {}),
    )
    result.html_checks = html_checks.static_checks(html)
    return html


def rerender_job_html(
    job: Job,
    result: DocumentResult,
    crops: dict[str, bytes],
    *,
    checked_on: date | None = None,
    math_alttext: bool | None = None,
) -> str:
    """`rerender_html` with the job's own choices for the student file: the title the
    instructor set and the contact line (T1-7, T1-17), and the app's MathML setting."""
    return rerender_html(
        result,
        crops,
        checked_on=checked_on,
        title_override=job.title_override,
        contact=job.contact_line,
        math_alttext=math_alttext,
    )
