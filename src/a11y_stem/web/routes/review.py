"""Review decisions (the POST route both review views share) and the list view of every
item ("All items as a list", `/jobs/{id}/review/list`). The main review screen, one
decision at a time beside the page it is on, is `routes/split.py`.

Every decision form carries a hidden `view` field: "list" from the list view, "split"
from the review screen (the default). After a decision the reviewer goes back to the view
the form was on: the list's next card (`#item-<id>`), or the review screen's next open
decision (`?item=<key>`). A refused edit re-shows the same view with the error.

The list view:

Layout (see `web.review` for the rules), in three sections:

1. "Needs your decision (N)": equations, then figures, then headings, each listing the
   fixes still waiting (page and reading order). Repeated headings that share a
   `Fix.group_key` are one card with one decision for all of them ("Apply to all 11",
   "Make all 11 bold text", or one level for all); their own cards are folded inside it.
   N counts decisions, so such a group counts once. Groups offer bulk accept for
   proposals that a single "Accept" could take anyway.
2. "Optional checks (M)": fixes accepted automatically with a note (a cosmetic style
   difference, a heading with weak evidence, a long alt text), folded away in a
   <details>, each note in plain English. Nothing there blocks "done"; each can still be
   edited or rejected. A note of the second automated check (a possible mismatch between
   the alt text and the image) is listed there too, by policy, but the page says it is
   worth a look instead of calling it small.
3. "Accepted or decided": everything else, folded away.

A decision is one transaction: lock the job row, apply the decision to the stored result
(`web.review`), store a `ReviewEdit` per changed fix, re-render the output HTML once with
the pipeline's assembler, save. If any step fails nothing changes. Without JavaScript
each button is a normal form POST that redirects to the next decision still waiting
(`#item-<id>`, or `#group-card-<key>` for a heading group), where a notice links back to
the one just changed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from a11y_stem.core import triage
from a11y_stem.core.types import Check, DocumentResult, Element, Fix, Kind, Status
from a11y_stem.pipeline.layout import REASON_LARGER_FONT
from a11y_stem.pipeline.validate import ALT_WARN_CHARS
from a11y_stem.web.db import Blob, ReviewEdit, session_scope
from a11y_stem.web.deps import (
    ELEMENT_ID,
    CurrentUser,
    PageError,
    UserDep,
    flash,
    get_ctx,
    pop_flash,
    render,
    require_csrf,
)
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.mathml import try_sanitize_mathml
from a11y_stem.web.messages import (
    NOTE_LABEL,
    Explained,
    confidence_label,
    explain_check,
    explain_reason,
    severity_label,
)
from a11y_stem.web.review import (
    ACCEPT_ALL_KINDS,
    BULK_DEFAULT_THRESHOLD,
    BULK_THRESHOLDS,
    NOT_CONVERTED,
    STATUS_FOR_ACTION,
    BulkRequest,
    FieldError,
    FixGroup,
    GroupOutcome,
    HeadingPlace,
    ReviewChange,
    ReviewInvalid,
    apply_bulk_accept,
    apply_group,
    apply_review,
    at_or_above,
    bulk_eligible,
    can_accept,
    group_anchor,
    group_fixes,
    heading_level_written,
    heading_moved,
    heading_places,
    is_group_key,
    item_numbers,
    kind_for_slug,
    needs_attention,
    next_to_review,
    parse_bulk,
    rerender_html,
    review_anchor,
)
from a11y_stem.web.routes.jobs import NOT_FOUND, owned_job, require_done
from a11y_stem.web.views import (
    KIND_LABELS,
    JobView,
    decisions_label,
    fix_status_label,
    job_view,
)

logger = logging.getLogger(__name__)
router = APIRouter()

DONE_MESSAGES = {
    "accept": "Accepted.",
    "edit": "Your edit passed the required checks and was saved.",
    "reject": "Rejected. The original from the PDF is kept in the document.",
}
# A flash whose target starts with this belongs to a group (a bulk action), not to one fix.
GROUP_FLASH = "group:"
# ... and this one to a heading group's card (a group decision).
HEADING_GROUP_FLASH = "heading-group:"
NOUNS = {
    Kind.EQUATION: ("equation", "equations"),
    Kind.FIGURE: ("figure", "figures"),
    Kind.HEADING: ("heading", "headings"),
}


def counted(n: int, kind: Kind) -> str:
    """'1 figure', '6 equations'."""
    one, many = NOUNS[kind]
    return f"{n} {one if n == 1 else many}"


@dataclass
class CheckView:
    text: str  # sentence for the reviewer (web.messages)
    code: str  # "name: code", shown small for debugging
    severity: str  # "Must be fixed", "Worth checking", or "Note" on an optional check


@dataclass
class Notice:
    message: str
    label: str | None = None  # the fix it is about, e.g. "Equation 3 on page 2"
    href: str | None = None  # link to that fix


@dataclass
class ReviewItem:
    element_id: str
    kind: str
    label: str  # "Equation 3": number within its kind, in reading order
    page: int  # 1-based; 0 when the element is missing
    status: str
    status_label: str
    pending: bool
    confidence_label: str
    checks: list[CheckView]
    reason: Explained | None
    crop_url: str | None
    mathml: Markup | None
    speech: str | None
    alt: str | None
    decorative: bool
    caption: str | None
    heading_level: int | None
    heading_text: str | None
    # Headings: levels 1 to this one keep their place in the outline (web.review).
    heading_max_level: int | None
    # Headings the outline repair moves: the level the output writes instead, and
    # whether it is written as a heading now (else it would be, if accepted again).
    heading_shown: int | None
    heading_in_document: bool
    can_accept: bool
    values: dict[str, str]
    optional: bool = False  # accepted automatically with optional notes (`checks`)
    second_check: bool = False  # ... one of them the second automated check's
    errors: dict[str, str] = field(default_factory=dict)  # field prefix -> message
    notices: list[Notice] = field(default_factory=list)  # changes elsewhere, shown above
    changed: list[str] = field(default_factory=list)  # changes to this fix, shown inside
    # The form comes back with the reviewer's own change in a field (a refused Accept or
    # edit): "Save edit" is then the main button.
    edited: bool = False
    # Figures with long descriptions on: the proposal's long description, and its chart
    # data table as HTML (`table_html`, captioned like the student file's) with the id
    # prefix its caption id starts with. None when the figure has none.
    long_description: str | None = None
    data_table: Markup | None = None
    data_table_id: str | None = None

    @property
    def open(self) -> bool:
        """A folded fix is unfolded when the reviewer just changed it or it has an error
        to correct."""
        return bool(self.changed or self.errors)

    @property
    def alt_chars(self) -> int:
        return len(self.values.get("alt", ""))

    @property
    def notes(self) -> str:
        """The optional notes as one line of plain English (for a folded summary)."""
        return " ".join(c.text for c in self.checks)


@dataclass
class HeadingGroupView:
    """One decision for repeated headings with the same label and style."""

    key: str
    anchor: str
    text: str  # the label, as the first heading has it
    level: int | None  # the level proposed for them (the same for all: heading_step)
    level_max: int  # levels 1 to this keep their place for the first one waiting
    style: str  # how they look in the PDF, in words
    size: int  # how many headings the pipeline found
    members: list[ReviewItem]  # the ones still waiting, reading order
    pages: list[int]
    keep: int  # how many "Apply to all" would keep at their level
    values: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    notices: list[str] = field(default_factory=list)
    members_open: bool = False  # one of them was just decided on its own

    @property
    def waiting(self) -> int:
        return len(self.members)

    @property
    def open(self) -> bool:
        """The folded cards of its headings are unfolded when the reviewer is working
        through them one by one, or one has an error to correct (its error summary link
        must reach the field)."""
        return self.members_open or any(m.errors for m in self.members)

    @property
    def pages_text(self) -> str:
        pages = sorted(set(self.pages))
        if len(pages) == 1:
            return f"page {pages[0]}"
        return "pages " + ", ".join(str(p) for p in pages[:-1]) + f" and {pages[-1]}"


@dataclass
class UnitView:
    """One decision in the "Needs your decision" list: a fix, or a heading group."""

    item: ReviewItem | None = None
    group: HeadingGroupView | None = None


@dataclass
class BulkOption:
    percent: int
    count: int
    selected: bool


@dataclass
class GroupView:
    slug: str
    title: str  # "Equations"
    one: str  # "equation"
    many: str  # "equations"
    units: list[UnitView]  # decisions waiting
    pending: list[ReviewItem]  # every fix waiting, also those inside a heading group
    optional: list[ReviewItem]
    done: list[ReviewItem]
    needs_review: int
    not_converted: int
    accept_all: int  # proposals "Accept all" would take; 0 when it is not offered
    moved: int  # headings left out of bulk accept: the output writes another level
    bulk_options: list[BulkOption]
    bulk_eligible: int  # proposals any bulk accept could take
    bulk_warned: int  # ... of which have a failed warning-level check
    notices: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.pending) + len(self.optional) + len(self.done)


# --------------------------------------------------------------------------- view model


def _item(
    fix: Fix,
    element: Element | None,
    number: int,
    job_id: str,
    crop_ids: set[str],
    place: HeadingPlace | None = None,
) -> ReviewItem:
    place = place if fix.kind is Kind.HEADING else None
    moved = place is not None and place.moved
    optional = fix.optional_note
    values: dict[str, str] = {}
    if fix.equation is not None:
        values["latex"] = fix.equation.latex
    if fix.figure is not None:
        values["alt"] = fix.figure.alt
        values["decorative"] = "on" if fix.figure.decorative else ""
        if fix.figure.long_description is not None:
            values["long_description"] = fix.figure.long_description
    if fix.heading is not None:
        # The select offers the level the document shows, not one it would move.
        suggested = place.suggested if place is not None else None
        values["level"] = str(suggested or fix.heading.level)
    return ReviewItem(
        element_id=fix.element_id,
        kind=fix.kind.value,
        label=f"{KIND_LABELS.get(fix.kind, fix.kind.value.title())} {number}",
        page=(element.page + 1) if element else 0,
        status=fix.status.value,
        status_label=fix_status_label(fix),
        pending=needs_attention(fix.status),
        confidence_label=confidence_label(fix.confidence),
        checks=[_check_view(c, note=optional) for c in fix.failed_checks],
        reason=explain_reason(fix.reason) if fix.reason else None,
        crop_url=f"/jobs/{job_id}/crops/{fix.element_id}.png"
        if fix.element_id in crop_ids
        else None,
        mathml=try_sanitize_mathml(fix.equation.mathml) if fix.equation else None,
        speech=fix.equation.speech if fix.equation else None,
        alt=fix.figure.alt if fix.figure else None,
        decorative=bool(fix.figure and fix.figure.decorative),
        caption=element.caption if element and fix.kind is Kind.FIGURE else None,
        heading_level=fix.heading.level if fix.heading else None,
        heading_text=fix.heading.text if fix.heading else (element.text if element else None),
        heading_max_level=place.highest if place is not None else None,
        heading_shown=place.written if place is not None and moved else None,
        heading_in_document=place is not None and place.in_document,
        # Accepting a level the output moves is refused (web.review), so not offered.
        can_accept=can_accept(fix) and fix.status is not Status.ACCEPTED and not moved,
        values=values,
        optional=optional,
        second_check=triage.second_check_note(fix),
        **_figure_details(fix, element),
    )


def _figure_details(fix: Fix, element: Element | None) -> dict[str, Any]:
    """A figure's long description and data table for its card (figures work)."""
    from a11y_stem.pipeline import assemble_tables, figure_details, table_html

    figure = fix.figure
    if figure is None or figure.decorative:
        return {}
    shown: dict[str, Any] = {"long_description": figure.long_description}
    if figure.data_table is not None:
        table_id = "review-" + assemble_tables.id_prefix(fix.element_id) + "-data"
        caption = element.caption if element is not None else None
        grid = figure.data_table.model_copy(
            update={"caption": figure_details.table_caption(figure, caption)}
        )
        # Escaped and rebuilt by the serialiser (cell text escaped, MathML allowlisted).
        shown["data_table"] = Markup(table_html.render_table(grid, id_prefix=table_id))
        shown["data_table_id"] = table_id
    return shown


def _check_view(check: Check, *, note: bool = False) -> CheckView:
    explained = explain_check(check)
    return CheckView(explained.text, explained.code, NOTE_LABEL if note else severity_label(check))


def _style_words(element: Element | None) -> str:
    """How a group's headings look in the PDF, without any of their text."""
    if element is None:
        return "with the same style"
    if REASON_LARGER_FONT in element.reasons:
        return "in a larger font"
    if element.bold:
        return "as a bold line in the body text size"
    return "as a line on its own"


def _heading_group(
    result: DocumentResult,
    unit: list[Fix],
    items: dict[str, ReviewItem],
    elements: dict[str, Element],
    places: dict[str, HeadingPlace],
) -> HeadingGroupView:
    first = unit[0]
    key = first.group_key or ""
    level = first.heading.level if first.heading is not None else None
    place = places.get(first.element_id)
    keep = sum(
        1
        for f in unit
        if can_accept(f)
        and f.heading is not None
        and heading_level_written(result, f.element_id, f.heading.level) == f.heading.level
    )
    members = [items[f.element_id] for f in unit]
    return HeadingGroupView(
        key=key,
        anchor=group_anchor(key),
        text=(first.heading.text if first.heading is not None else "") or "(no text)",
        level=level,
        level_max=place.highest if place is not None else 6,
        style=_style_words(elements.get(first.element_id)),
        size=first.group_size or len(unit),
        members=members,
        pages=[m.page for m in members if m.page],
        keep=keep,
        values={"level": str(level or 1)},
    )


def _group(
    result: DocumentResult,
    group: FixGroup,
    items: dict[str, ReviewItem],
    elements: dict[str, Element],
    places: dict[str, HeadingPlace],
) -> GroupView:
    one, many = NOUNS[group.kind]
    eligible = [f for f in group.pending if bulk_eligible(f, places)]
    options = [
        BulkOption(t, sum(at_or_above(f, t) for f in eligible), t == BULK_DEFAULT_THRESHOLD)
        for t in BULK_THRESHOLDS
    ]
    units = [
        UnitView(group=_heading_group(result, unit, items, elements, places))
        if unit[0].group_key and unit[0].kind is Kind.HEADING
        else UnitView(item=items[unit[0].element_id])
        for unit in group.units
    ]
    return GroupView(
        slug=group.slug,
        title=many.capitalize(),
        one=one,
        many=many,
        units=units,
        pending=[items[f.element_id] for f in group.pending],
        optional=[items[f.element_id] for f in group.optional],
        done=[items[f.element_id] for f in group.done],
        needs_review=sum(f.status is Status.NEEDS_REVIEW for f in group.pending),
        not_converted=sum(f.status in NOT_CONVERTED for f in group.pending),
        accept_all=len(eligible) if group.kind in ACCEPT_ALL_KINDS else 0,
        moved=sum(
            f.status is Status.NEEDS_REVIEW and can_accept(f) and heading_moved(f, places)
            for f in group.pending
        ),
        bulk_options=options,
        bulk_eligible=len(eligible),
        bulk_warned=sum(bool(f.failed_checks) for f in eligible),
    )


def _load(
    request: Request, job_id: str, user: CurrentUser
) -> tuple[JobView, DocumentResult, set[str]]:
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        view = job_view(session, job, with_summary=True)
        result = DocumentResult.model_validate(job.result_json or {})
        crop_ids = set(
            session.scalars(
                select(Blob.element_id).where(Blob.job_id == job.id, Blob.kind == "crop")
            ).all()
        )
    return view, result, {c for c in crop_ids if c}


def _place_flashes(
    flashes: list[dict[str, Any]],
    result: DocumentResult,
    items: dict[str, ReviewItem],
    groups: dict[str, GroupView],
    cards: dict[str, HeadingGroupView],
) -> list[Notice]:
    """Put each one-time notice where the reviewer lands: a single decision's notice at
    the next decision still waiting (where the redirect points), linked back to the fix
    that changed; a bulk action's notice in its group, a heading group's on its card (or
    at the top when the whole group is decided). Every notice is also returned for the
    progress summary at the top, which says what just happened and what is left."""
    top: list[Notice] = []
    for entry in flashes:
        message = str(entry.get("message", ""))
        target = entry.get("element_id")
        target = target if isinstance(target, str) else ""
        if target.startswith(GROUP_FLASH):
            group = groups.get(target.removeprefix(GROUP_FLASH))
            if group is not None:
                group.notices.append(message)
            top.append(Notice(message))
            continue
        if target.startswith(HEADING_GROUP_FLASH):
            card = cards.get(target.removeprefix(HEADING_GROUP_FLASH))
            if card is not None:
                card.notices.append(message)
            top.append(Notice(message))
            continue
        changed = items.get(target)
        if changed is None:
            top.append(Notice(message))
            continue
        changed.changed.append(message)
        where = f" on page {changed.page}" if changed.page else ""  # 0: element missing
        notice = Notice(message, changed.label + where, f"#item-{target}")
        landing = next_to_review(result, changed.element_id)
        landing_fix = result.fix_for(landing) if landing else None
        if landing_fix is not None and landing_fix.group_key in cards:
            card = cards[landing_fix.group_key]
            card.notices.append(f"{notice.label}: {message}" if notice.label else message)
            changed_fix = result.fix_for(target)
            if changed_fix is not None and changed_fix.group_key == card.key:
                card.members_open = True  # deciding them one by one: keep them in view
        elif landing in items:
            items[landing].notices.append(notice)
        top.append(notice)
    return top


def _apply_invalid(
    invalid: tuple[str, ReviewInvalid],
    items: dict[str, ReviewItem],
    groups: dict[str, GroupView],
    cards: dict[str, HeadingGroupView],
) -> None:
    target, exc = invalid
    if target in groups:
        groups[target].error = " ".join(e.message for e in exc.errors)
        return
    if target in cards:
        cards[target].error = " ".join(e.message for e in exc.errors)
        cards[target].values.update(exc.values)
        return
    item = items.get(target)
    if item is not None:
        item.values.update(exc.values)
        for error in exc.errors:
            item.errors[error.field_id.split("-", 1)[0]] = error.message


def _page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    *,
    invalid: tuple[str, ReviewInvalid] | None = None,
    status_code: int = 200,
) -> Response:
    view, result, crop_ids = _load(request, job_id, user)
    elements = {e.id: e for e in result.elements}
    numbers = item_numbers(result)
    fix_groups = group_fixes(result)
    places = heading_places(result)
    items = {
        f.element_id: _item(
            f,
            elements.get(f.element_id),
            numbers[f.element_id],
            view.id,
            crop_ids,
            places.get(f.element_id),
        )
        for g in fix_groups
        for f in (*g.pending, *g.optional, *g.done)
    }
    groups = {g.slug: _group(result, g, items, elements, places) for g in fix_groups}
    cards = {
        unit.group.key: unit.group
        for g in groups.values()
        for unit in g.units
        if unit.group is not None
    }
    top = _place_flashes(pop_flash(request), result, items, groups, cards)
    errors: list[FieldError] = []
    if invalid is not None:
        errors = invalid[1].errors
        _apply_invalid(invalid, items, groups, cards)
    optional = [item for g in groups.values() for item in g.optional]
    done = [item for g in groups.values() for item in g.done]
    context: dict[str, Any] = {
        "job": view,
        "groups": list(groups.values()),
        "deciding": [g for g in groups.values() if g.units or g.error or g.notices],
        "optional": optional,
        "optional_open": any(item.open for item in optional),
        "second_checks": sum(item.second_check for item in optional),
        "done": done,
        "done_open": any(item.open for item in done),
        "errors": [{"field_id": e.field_id, "message": e.message} for e in errors],
        "flash": [],  # placed by _place_flashes instead of at the top of <main>
        "notices": top,
        "remaining": sum(len(g.units) for g in fix_groups),
        "remaining_by_kind": [decisions_label(g.units, g.kind) for g in fix_groups if g.pending],
        "alt_guideline": ALT_WARN_CHARS,
        # No `active` nav item: this page is not "My documents" itself, so the link
        # must not be announced as the current page (aria-current="page").
    }
    return render(request, "review.html", context, status_code=status_code)


# --------------------------------------------------------------------------- routes

LIST_VIEW = "list"


def list_url(job_id: str, anchor: str) -> str:
    return f"/jobs/{job_id}/review/list#{anchor}"


@router.get("/jobs/{job_id}/review/list")
def review_list(request: Request, job_id: str, user: UserDep) -> Response:
    return _page(request, job_id, user)


def _split_redirect(job_id: str, result: DocumentResult, after: str | None) -> Response:
    """To the review screen's next decision still open after `after` (an element id or a
    group key; None: the first one still open), or to its done state."""
    from a11y_stem.web import queue
    from a11y_stem.web.routes.split import review_url

    decisions = queue.decisions(result)
    current = queue.find(decisions, after) if after else None
    landing = queue.next_open(decisions, current)
    item = landing.key if landing is not None else None
    return RedirectResponse(review_url(job_id, item=item), status_code=303)


def _invalid_page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    view: str,
    target: str,
    exc: ReviewInvalid,
) -> Response:
    """The view the form was on again, with the error and the reviewer's input (422)."""
    if view == LIST_VIEW:
        return _page(request, job_id, user, invalid=(target, exc), status_code=422)
    from a11y_stem.web.routes.split import split_page

    return split_page(request, job_id, user, item=target, invalid=(target, exc), status_code=422)


@router.post("/jobs/{job_id}/review/{element_id}", dependencies=[Depends(require_csrf)])
def review_action(
    request: Request,
    job_id: str,
    element_id: str,
    user: UserDep,
    action: Annotated[str, Form()] = "",
    latex: Annotated[str | None, Form()] = None,
    alt: Annotated[str | None, Form()] = None,
    decorative: Annotated[str | None, Form()] = None,
    level: Annotated[str | None, Form()] = None,
    min_confidence: Annotated[str, Form()] = "",
    view: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "",
    # Figures with long descriptions on (web.review `_edited_details`).
    long_description: Annotated[str | None, Form()] = None,
    remove_data_table: Annotated[str | None, Form()] = None,
) -> Response:
    """One decision on one fix; when `element_id` names a kind ("equations", "figures",
    "headings"), a bulk accept in that group; when it is a heading group's key
    ("hg-p1-e7"), one decision for every heading of that group still waiting (with
    `scope=all`, for every heading of the group: a decided group decided again).

    Element ids look like "p3-e12", so neither a kind nor a group key can be one.
    Sharing the route keeps a single CSRF-checked, ownership-checked way to change a
    review. `view` says which view the form was on (module docstring). The fields are
    passed on only when the form sent them: Accept refuses a changed one
    (`web.review.apply_review`), and a field that was not sent was not changed.
    """
    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    view = LIST_VIEW if view == LIST_VIEW else "split"
    if kind_for_slug(element_id) is not None:
        return _bulk_action(request, job_id, element_id, user, action, min_confidence, view)
    if is_group_key(element_id):
        every = scope == "all"
        return _group_action(request, job_id, element_id, user, action, level or "", view, every)
    sent = {
        "latex": latex,
        "alt": alt,
        "decorative": decorative,
        "level": level,
        "long_description": long_description,
        "remove_data_table": remove_data_table,
    }
    form = {name: value for name, value in sent.items() if value is not None}
    try:
        with session_scope(get_ctx(request).factory) as session:
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            result = DocumentResult.model_validate(job.result_json or {})
            change = apply_review(result, element_id, action, form)
            job.html = rerender_html(result, _crops(session, job.id))
            job.result_json = result.model_dump(mode="json", exclude={"html"})
            session.add(_review_edit(job.id, user, change))
    except LookupError:
        raise PageError(404, *NOT_FOUND) from None
    except ReviewInvalid as exc:
        # `action` is free text from the form: only known values reach the log.
        logged_action = action if action in STATUS_FOR_ACTION else "unknown"
        log_event(logger, "review_invalid", job_id=job_id, action=logged_action)
        return _invalid_page(request, job_id, user, view, element_id, exc)
    log_event(
        logger,
        "review",
        job_id=job_id,
        element_id=element_id,
        action=action,
        status=STATUS_FOR_ACTION[action].value,
    )
    message = DONE_MESSAGES[action]
    if change.warnings:
        n = len(change.warnings)
        message += f" {n} {'check gives a warning' if n == 1 else 'checks give warnings'}: "
        message += "see the notes on it."
    message += _moved_message(change.moved, item_numbers(result))
    flash(request, message, element_id=element_id)
    if view != LIST_VIEW:
        return _split_redirect(job_id, result, element_id)
    return RedirectResponse(_landing(job_id, result, element_id), status_code=303)


def _landing(job_id: str, result: DocumentResult, element_id: str) -> str:
    landing = next_to_review(result, element_id)
    anchor = review_anchor(result, landing) if landing else "review-progress"
    return list_url(job_id, anchor)


GROUP_DONE = {
    "accept": "Kept {n} as they are.",
    "edit": "Set {n} to level {level}.",
    "reject": "Made {n} bold text instead of headings.",
}


def _group_action(
    request: Request,
    job_id: str,
    group_key: str,
    user: CurrentUser,
    action: str,
    level: str,
    view: str,
    every: bool = False,
) -> Response:
    try:
        with session_scope(get_ctx(request).factory) as session:
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            result = DocumentResult.model_validate(job.result_json or {})
            outcome = apply_group(result, group_key, action, {"level": level}, every=every)
            if outcome.changes:  # nothing left to decide: no write, no re-render
                job.html = rerender_html(result, _crops(session, job.id))
                job.result_json = result.model_dump(mode="json", exclude={"html"})
                session.add_all(_review_edit(job.id, user, c) for c in outcome.changes)
    except LookupError:
        raise PageError(404, *NOT_FOUND) from None
    except ReviewInvalid as exc:
        logged_action = action if action in STATUS_FOR_ACTION else "unknown"
        log_event(logger, "review_invalid", job_id=job_id, action=f"group_{logged_action}")
        return _invalid_page(request, job_id, user, view, group_key, exc)
    log_event(
        logger,
        "review_group",
        job_id=job_id,
        group=group_key,
        action=action,
        changed=len(outcome.changes),
        left=len(outcome.left),
    )
    message = _group_message(action, level, outcome, every=every)
    flash(request, message, element_id=HEADING_GROUP_FLASH + group_key)
    if view != LIST_VIEW:
        if outcome.left:
            # Headings left waiting (the output would move their level) keep the group
            # open: the reviewer stays on it to choose a level for them.
            from a11y_stem.web.routes.split import review_url

            return RedirectResponse(review_url(job_id, item=group_key), status_code=303)
        return _split_redirect(job_id, result, group_key)
    landing = None
    if not outcome.left and outcome.changes:
        landing = next_to_review(result, outcome.changes[-1].fix.element_id)
    if outcome.left or not outcome.changes:
        anchor = group_anchor(group_key) if outcome.left else "review-progress"
    else:
        anchor = review_anchor(result, landing) if landing else "review-progress"
    return RedirectResponse(list_url(job_id, anchor), status_code=303)


# A decided group decided again ("Change all 11" on the review screen).
GROUP_REDONE = {
    "accept": "Changed {n} back to headings.",
    "edit": "Set {n} to level {level}.",
    "reject": "Made {n} bold text instead of headings.",
}


def _group_message(action: str, level: str, outcome: GroupOutcome, *, every: bool = False) -> str:
    n = len(outcome.changes)
    if not n:
        if every:
            return "Nothing changed: these headings were already like that."
        return "Nothing changed: none of these headings were waiting for a decision."
    if every:  # after "Repeated heading “Teacher Support”: " (the notice's label)
        message = GROUP_REDONE.get(action, "Done.").format(n=n, level=level)
    else:
        message = GROUP_DONE.get(action, "Done.").format(n=counted(n, Kind.HEADING), level=level)
    if outcome.left and every:
        left = counted(len(outcome.left), Kind.HEADING)
        message += (
            f" {left} {'was' if len(outcome.left) == 1 else 'were'} left as before: the "
            "document would show them at another level."
        )
    elif outcome.left:
        left = counted(len(outcome.left), Kind.HEADING)
        message += (
            f" {left} {'was' if len(outcome.left) == 1 else 'were'} left: the document "
            "would show them at another level, so choose a level for them."
        )
    return message


def _bulk_action(
    request: Request,
    job_id: str,
    slug: str,
    user: CurrentUser,
    action: str,
    min_confidence: str,
    view: str,
) -> Response:
    try:
        with session_scope(get_ctx(request).factory) as session:
            # Ownership first: someone else's job is a 404 whatever the form says.
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            bulk = parse_bulk(slug, action, min_confidence)
            result = DocumentResult.model_validate(job.result_json or {})
            changes = apply_bulk_accept(result, bulk)
            if changes:  # nothing left to accept: no write, no re-render (idempotent)
                job.html = rerender_html(result, _crops(session, job.id))
                job.result_json = result.model_dump(mode="json", exclude={"html"})
                session.add_all(_review_edit(job.id, user, c) for c in changes)
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action="bulk")
        return _page(request, job_id, user, invalid=(slug, exc), status_code=422)
    log_event(
        logger,
        "review_bulk",
        job_id=job_id,
        kind=bulk.kind.value,
        min_percent=bulk.min_percent,
        accepted=len(changes),
    )
    flash(request, _bulk_message(bulk, len(changes)), element_id=GROUP_FLASH + slug)
    if view != LIST_VIEW:  # bulk accept is only offered on the list view
        return _split_redirect(job_id, result, None)
    return RedirectResponse(list_url(job_id, f"group-{slug}"), status_code=303)


MOVED_NAMED = 5  # name up to this many moved headings; count any more


def _moved_message(moved: list[str], numbers: dict[str, int]) -> str:
    """' Heading 3 is now shown at a different level.': the other headings a heading
    decision moved in the output, so the change to them is not silent."""
    if not moved:
        return ""
    if len(moved) > MOVED_NAMED:
        return f" {len(moved)} later headings are now shown at a different level."
    names = [str(numbers[eid]) for eid in moved]
    if len(names) == 1:
        return f" Heading {names[0]} is now shown at a different level."
    listed = ", ".join(names[:-1]) + " and " + names[-1]
    return f" Headings {listed} are now shown at a different level."


def _bulk_message(bulk: BulkRequest, n: int) -> str:
    where = "" if bulk.min_percent is None else f" at {bulk.min_percent}% confidence or higher"
    if n:
        return f"Accepted {counted(n, bulk.kind)}{where}."
    many = NOUNS[bulk.kind][1]
    return f"Nothing changed: no {many}{where} were waiting that could be accepted."


def _crops(session: Session, job_id: str) -> dict[str, bytes]:
    blobs = session.scalars(select(Blob).where(Blob.job_id == job_id, Blob.kind == "crop"))
    return {b.element_id: b.content for b in blobs if b.element_id}


def _review_edit(job_id: str, user: CurrentUser, change: ReviewChange) -> ReviewEdit:
    return ReviewEdit(
        job_id=job_id,
        user_id=user.id,
        element_id=change.fix.element_id,
        action=change.action,
        before=change.before,
        after=change.after,
    )
