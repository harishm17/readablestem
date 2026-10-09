"""Reviewer decisions on fixes: accept, edit or reject, applied to a `DocumentResult`.

The web route owns the transaction; this module owns the rules:

- accept: only a fix with a proposal that passed every error-level check (an abstained
  fix has nothing to accept, and an invalid proposal must be edited, which re-validates
  it; the reviewer can also keep the original by rejecting it), and never with a field
  of the form changed from the proposal (the change would be dropped silently: the
  reviewer is asked to Save edit it or undo it),
- edit: the reviewer's LaTeX / alt text / heading level goes through the same cleaning
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
- reject: the original stays in the document, flagged,
- bulk accept: many fixes of one kind at once, but only the ones a single "Accept" could
  take (NEEDS_REVIEW, a proposal, no failed error-level check, and for a heading a
  level the output keeps). FAILED and ABSTAINED fixes have nothing safe to accept and
  are never included.

A heading decision can change the level the output writes for later headings (the
children of a rejected heading move up together). Stored levels are not rewritten, so
restoring the heading puts them back; instead `heading_places` tells the review screen
the level each heading is written at, and `ReviewChange.moved` names the other headings
a decision moved.

It also decides the order of the review screen. A reviewer's time should go where the
risk is, so fixes are grouped by kind (equations, then figures, then headings); within a
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
from typing import TYPE_CHECKING, Any

from a11y_stem.core import triage
from a11y_stem.core.types import (
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
)
from a11y_stem.web.edit_check import MEANING_CODES
from a11y_stem.web.messages import confidence_percent, explain_check

if TYPE_CHECKING:
    from a11y_stem.web.db import Job

logger = logging.getLogger(__name__)

ACTIONS = ("accept", "edit", "reject")
STATUS_FOR_ACTION = {"accept": Status.ACCEPTED, "edit": Status.EDITED, "reject": Status.REJECTED}
MAX_LATEX_CHARS = 5000
MAX_ALT_CHARS = 1000

# Review screen groups, in priority order, and the slug each has in URLs and anchors.
GROUP_SLUGS = {Kind.EQUATION: "equations", Kind.FIGURE: "figures", Kind.HEADING: "headings"}
GROUP_KINDS = tuple(GROUP_SLUGS)
# Waiting for a decision: a proposal to check, or nothing usable (the reviewer can write
# one, or reject to confirm the original stays).
PENDING = PENDING_STATUSES
NOT_CONVERTED = frozenset({Status.FAILED, Status.ABSTAINED})

# Bulk accept. "Accept all" is only offered for headings: a wrong heading level is cheap
# to spot and fix later, a wrong equation or alt text is not. The confidence threshold
# is offered for every group, from a fixed list so the page can show how many each
# choice would accept (no JavaScript).
BULK_ACCEPT_ALL = "accept_all"
BULK_ACCEPT_CONFIDENT = "accept_confident"
BULK_ACTIONS = (BULK_ACCEPT_ALL, BULK_ACCEPT_CONFIDENT)
ACCEPT_ALL_KINDS = frozenset({Kind.HEADING})
BULK_THRESHOLDS = (90, 80, 70, 60, 50)  # percent
BULK_DEFAULT_THRESHOLD = 80


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
    # An edit used although it differs from the PDF where it changes the meaning.
    anyway: bool = False


@dataclass
class FixGroup:
    """One kind's fixes as the review screen shows them."""

    kind: Kind
    pending: list[Fix]  # waiting for a decision, page and reading order
    done: list[Fix]  # accepted (no note), edited or rejected, page and reading order
    optional: list[Fix] = field(default_factory=list)  # accepted with an optional note

    @property
    def slug(self) -> str:
        return GROUP_SLUGS[self.kind]

    @property
    def units(self) -> list[list[Fix]]:
        """The decisions waiting: one per fix, one per group of repeated headings."""
        return triage.units(self.pending)


@dataclass(frozen=True)
class BulkRequest:
    kind: Kind
    min_percent: int | None  # None: every eligible proposal ("accept all", headings only)


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
            (f.element_id for f in result.fixes if f.kind is kind), key=position.__getitem__
        )
        numbers.update({element_id: i for i, element_id in enumerate(ordered, start=1)})
    return numbers


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
    return any(p is not None for p in (fix.equation, fix.figure, fix.heading))


def can_accept(fix: Fix) -> bool:
    """Only a proposal that passed every error-level check may be accepted as it is;
    anything else goes through "Save edit", which validates it again."""
    return has_proposal(fix) and not _blocking(fix.checks)


SNAPSHOT_FIELDS = frozenset(
    {"status", "equation", "figure", "heading", "table", "checks", "reviewed"}
)
# Beside the fields, a ReviewEdit's `after` holds the review version its write made (a
# number), so the edits of one write can be found again (`undo_last`), and `True` under
# ANYWAY_KEY for an edit used although it differs from the PDF (`apply_review`).
WRITE_KEY = "write"
ANYWAY_KEY = "anyway"


def snapshot(fix: Fix) -> dict[str, Any]:
    """What a ReviewEdit stores as before/after (deleted with the job): everything a
    decision can change, so `undo_last` can put it back. Snapshots stored before the
    checks and `reviewed` were in them cannot be undone."""
    return fix.model_dump(mode="json", include=set(SNAPSHOT_FIELDS))


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
    if action not in ACTIONS:
        raise ReviewInvalid([FieldError(field_id("actions", element_id), "Choose an action.")])
    element = next((e for e in result.elements if e.id == element_id), None)
    before = snapshot(fix)
    warnings: list[Check] = []
    if action == "accept" and not has_proposal(fix):
        message = "There is no proposal to accept. Edit it, or reject it to keep the original."
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
    if action == "edit":
        warnings = _edit(result, fix, element, form, anyway=anyway)
        overridden = anyway and any(c.code in MEANING_CODES for c in warnings)
    fix.status = STATUS_FOR_ACTION[action]
    fix.reviewed = True  # a reviewer's Accept is not the pipeline's automatic one
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
        from a11y_stem.pipeline import validate

        alt = validate.clean_alt(form["alt"])
        decorative = form.get("decorative") in {"on", "true", "1"}
        values = {"alt": alt, "decorative": "on" if decorative else ""}
        if decorative != fix.figure.decorative:
            message = "You changed “Decorative”. " + UNSAVED.format(undo="change it back")
            raise ReviewInvalid([FieldError(field_id("decorative", eid), message)], values)
        if not decorative and alt != validate.clean_alt(fix.figure.alt):
            message = "You changed the alt text. " + UNSAVED.format(undo="undo it")
            raise ReviewInvalid([FieldError(field_id("alt", eid), message)], values)
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
    from a11y_stem.web import edit_check

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
    from a11y_stem.pipeline import validate

    fid = field_id("alt", fix.element_id)
    # What is stored is exactly what check_alt judges: invisible format characters (bidi
    # controls, zero-width spaces) removed and whitespace collapsed, as for model output.
    alt = validate.clean_alt(form.get("alt", ""))
    decorative = form.get("decorative") in {"on", "true", "1"}
    values = {"alt": alt, "decorative": "on" if decorative else ""}
    if (
        fix.figure is not None
        and decorative == fix.figure.decorative
        and (decorative or alt == validate.clean_alt(fix.figure.alt))
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
    fix.figure = FigureFix(figure_type=figure_type, alt=alt, decorative=decorative)
    fix.checks = checks
    return [c for c in checks if not c.passed]


_LEVEL = re.compile(r"[1-6]")  # ASCII only: "²".isdigit() is True, int("²") fails


def heading_level_written(result: DocumentResult, element_id: str, level: int) -> int:
    """The level the output would show for this heading if it were given `level`.

    The assembler repairs the outline (`assemble.Outline`): a heading more than one
    level below the heading still open before it is moved up. Accepting such a level
    would store one level and show another, so review refuses it.
    """
    from a11y_stem.pipeline import assemble

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
    from a11y_stem.pipeline import assemble

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
      at another level is left waiting, as bulk accept does);
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


# --------------------------------------------------------------------------- bulk


def bulk_eligible(fix: Fix, places: Mapping[str, HeadingPlace]) -> bool:
    """What a bulk accept may take: exactly what a single "Accept" could (for a heading,
    only a level the output keeps; `places` from `heading_places`), and only while it is
    still waiting for review (a reviewer's own decision is never overwritten)."""
    return fix.status is Status.NEEDS_REVIEW and can_accept(fix) and not heading_moved(fix, places)


def at_or_above(fix: Fix, min_percent: int) -> bool:
    return fix.confidence is not None and confidence_percent(fix.confidence) >= min_percent


def bulk_candidates(result: DocumentResult, request: BulkRequest) -> list[Fix]:
    # Accepting a fix waiting for review changes no level and keeps it in the document,
    # so the outline is the same after each one: one pass is enough.
    places = heading_places(result) if request.kind is Kind.HEADING else {}
    return [
        f
        for f in result.fixes
        if f.kind is request.kind
        and bulk_eligible(f, places)
        and (request.min_percent is None or at_or_above(f, request.min_percent))
    ]


def bulk_field_id(slug: str) -> str:
    """The control a bulk error points at: the threshold select of that group."""
    return f"min-confidence-{slug}"


def parse_bulk(slug: str, action: str, min_confidence: str) -> BulkRequest:
    """The bulk form's fields as a request. Raises LookupError for an unknown group and
    ReviewInvalid for anything the page never offers."""
    kind = kind_for_slug(slug)
    if kind is None:
        raise LookupError(slug)
    fid = bulk_field_id(slug)
    if action == BULK_ACCEPT_ALL:
        if kind not in ACCEPT_ALL_KINDS:
            message = "Accepting every proposal at once is only offered for headings."
            raise ReviewInvalid([FieldError(fid, message)])
        return BulkRequest(kind, None)
    if action != BULK_ACCEPT_CONFIDENT:
        raise ReviewInvalid([FieldError(fid, "Choose an action.")])
    raw = min_confidence.strip().removesuffix("%")
    if not raw.isdigit() or int(raw) not in BULK_THRESHOLDS:
        choices = ", ".join(f"{t}%" for t in BULK_THRESHOLDS)
        raise ReviewInvalid([FieldError(fid, f"Choose a confidence from the list ({choices}).")])
    return BulkRequest(kind, int(raw))


def apply_bulk_accept(result: DocumentResult, request: BulkRequest) -> list[ReviewChange]:
    """Accept every candidate in place; one ReviewChange each (empty if none were left,
    so repeating the same request changes nothing)."""
    changes = []
    for fix in bulk_candidates(result, request):
        before = snapshot(fix)
        fix.status = Status.ACCEPTED
        fix.reviewed = True
        changes.append(ReviewChange(fix=fix, action="accept", before=before, after=snapshot(fix)))
    if changes:
        _recount(result)
    return changes


# --------------------------------------------------------------------------- undo

UNDO_ACTION = "undo"  # ReviewEdit.action of an undo (a write of its own)


def restorable(before: object) -> bool:
    """A `before` snapshot `undo_last` can put back: it has every field a decision can
    change (snapshots stored before the checks were in them do not)."""
    return isinstance(before, Mapping) and before.keys() >= SNAPSHOT_FIELDS


def undo_last(
    result: DocumentResult, edits: list[tuple[str, Mapping[str, Any]]]
) -> list[ReviewChange]:
    """Put back, in place, what one write changed: `edits` are its ReviewEdits as
    (element id, before snapshot). One ReviewChange per fix (action "undo"). Raises
    LookupError when a fix is gone or a snapshot cannot be put back; nothing is changed
    then (the route's transaction is rolled back)."""
    changes: list[ReviewChange] = []
    for element_id, before in edits:
        fix = result.fix_for(element_id)
        if fix is None or not restorable(before):
            raise LookupError(element_id)
        current = snapshot(fix)
        merged = fix.model_dump(mode="json") | {name: before[name] for name in SNAPSHOT_FIELDS}
        restored = Fix.model_validate(merged)
        for name in SNAPSHOT_FIELDS:
            setattr(fix, name, getattr(restored, name))
        changes.append(
            ReviewChange(fix=fix, action=UNDO_ACTION, before=current, after=snapshot(fix))
        )
    if changes:
        _recount(result)
    return changes


# --------------------------------------------------------------------------- output


def rerender_html(result: DocumentResult, crops: dict[str, bytes]) -> str:
    """The output HTML after a review decision, from the same assembler the pipeline uses.

    The static checks of the new HTML replace `result.html_checks`, so the job page lists
    the checks of the file the user downloads, not of the one before the review.
    """
    from a11y_stem.pipeline import assemble, html_checks

    html = assemble.render_html(
        title=result.title,
        lang=result.lang,
        elements=result.elements,
        fixes=result.fixes,
        crops=crops,
        source_note=None,
    )
    result.html_checks = html_checks.static_checks(html)
    return html
