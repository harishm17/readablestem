"""Review screen: fixes grouped by kind, what needs a decision first, one-click decisions.

Layout (see `web.review` for the rules): equations, then figures, then headings, each
group listing the fixes still waiting for a decision (page and reading order) and, folded
away in a <details>, the ones already decided. Groups offer bulk accept for proposals
that a single "Accept" could take anyway.

A decision is one transaction: lock the job row, apply the decision to the stored result
(`web.review`), store a `ReviewEdit` per changed fix, re-render the output HTML once with
the pipeline's assembler, save. If any step fails nothing changes. Without JavaScript
each button is a normal form POST that redirects to the next fix still waiting
(`#item-<id>`), where a notice links back to the one just changed.
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

from a11y_stem.core.types import Check, DocumentResult, Element, Fix, Kind, Status
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
    ReviewChange,
    ReviewInvalid,
    apply_bulk_accept,
    apply_review,
    at_or_above,
    bulk_eligible,
    can_accept,
    group_fixes,
    highest_heading_levels,
    item_numbers,
    kind_for_slug,
    needs_attention,
    next_to_review,
    parse_bulk,
    rerender_html,
)
from a11y_stem.web.routes.jobs import NOT_FOUND, owned_job, require_done
from a11y_stem.web.views import KIND_LABELS, JobView, fix_status_label, job_view

logger = logging.getLogger(__name__)
router = APIRouter()

DONE_MESSAGES = {
    "accept": "Accepted.",
    "edit": "Your edit passed the required checks and was saved.",
    "reject": "Rejected. The original from the PDF is kept in the document.",
}
# A flash whose target starts with this belongs to a group (a bulk action), not to one fix.
GROUP_FLASH = "group:"
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
    severity: str  # "Must be fixed" or "Worth checking"


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
    can_accept: bool
    values: dict[str, str]
    errors: dict[str, str] = field(default_factory=dict)  # field prefix -> message
    notices: list[Notice] = field(default_factory=list)  # changes elsewhere, shown above
    changed: list[str] = field(default_factory=list)  # changes to this fix, shown inside

    @property
    def open(self) -> bool:
        """A decided fix is folded away unless the reviewer just changed it or it has
        an error to correct."""
        return bool(self.changed or self.errors)

    @property
    def alt_chars(self) -> int:
        return len(self.values.get("alt", ""))


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
    pending: list[ReviewItem]
    done: list[ReviewItem]
    needs_review: int
    not_converted: int
    accept_all: int  # proposals "Accept all" would take; 0 when it is not offered
    bulk_options: list[BulkOption]
    bulk_eligible: int  # proposals any bulk accept could take
    bulk_warned: int  # ... of which have a failed warning-level check
    notices: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.pending) + len(self.done)

    @property
    def done_open(self) -> bool:
        return any(item.open for item in self.done)


# --------------------------------------------------------------------------- view model


def _item(
    fix: Fix,
    element: Element | None,
    number: int,
    job_id: str,
    crop_ids: set[str],
    max_level: int | None = None,
) -> ReviewItem:
    values: dict[str, str] = {}
    if fix.equation is not None:
        values["latex"] = fix.equation.latex
    if fix.figure is not None:
        values["alt"] = fix.figure.alt
        values["decorative"] = "on" if fix.figure.decorative else ""
    if fix.heading is not None:
        values["level"] = str(fix.heading.level)
    return ReviewItem(
        element_id=fix.element_id,
        kind=fix.kind.value,
        label=f"{KIND_LABELS.get(fix.kind, fix.kind.value.title())} {number}",
        page=(element.page + 1) if element else 0,
        status=fix.status.value,
        status_label=fix_status_label(fix),
        pending=needs_attention(fix.status),
        confidence_label=confidence_label(fix.confidence),
        checks=[_check_view(c) for c in fix.failed_checks],
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
        heading_max_level=max_level if fix.kind is Kind.HEADING else None,
        can_accept=can_accept(fix) and fix.status is not Status.ACCEPTED,
        values=values,
    )


def _check_view(check: Check) -> CheckView:
    explained = explain_check(check)
    return CheckView(explained.text, explained.code, severity_label(check))


def _group(group: FixGroup, items: dict[str, ReviewItem]) -> GroupView:
    one, many = NOUNS[group.kind]
    eligible = [f for f in group.pending if bulk_eligible(f)]
    options = [
        BulkOption(t, sum(at_or_above(f, t) for f in eligible), t == BULK_DEFAULT_THRESHOLD)
        for t in BULK_THRESHOLDS
    ]
    return GroupView(
        slug=group.slug,
        title=many.capitalize(),
        one=one,
        many=many,
        pending=[items[f.element_id] for f in group.pending],
        done=[items[f.element_id] for f in group.done],
        needs_review=sum(f.status is Status.NEEDS_REVIEW for f in group.pending),
        not_converted=sum(f.status in NOT_CONVERTED for f in group.pending),
        accept_all=len(eligible) if group.kind in ACCEPT_ALL_KINDS else 0,
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
) -> list[Notice]:
    """Put each one-time notice where the reviewer lands: a single decision's notice at
    the next fix still waiting (where the redirect points), linked back to the fix that
    changed; a bulk action's notice in its group. Every notice is also returned for the
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
        changed = items.get(target)
        if changed is None:
            top.append(Notice(message))
            continue
        changed.changed.append(message)
        where = f" on page {changed.page}" if changed.page else ""  # 0: element missing
        notice = Notice(message, changed.label + where, f"#item-{target}")
        landing = next_to_review(result, changed.element_id)
        if landing in items:
            items[landing].notices.append(notice)
        top.append(notice)
    return top


def _apply_invalid(
    invalid: tuple[str, ReviewInvalid],
    items: dict[str, ReviewItem],
    groups: dict[str, GroupView],
) -> None:
    target, exc = invalid
    if target in groups:
        groups[target].error = " ".join(e.message for e in exc.errors)
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
    max_levels = highest_heading_levels(result)
    items = {
        f.element_id: _item(
            f,
            elements.get(f.element_id),
            numbers[f.element_id],
            view.id,
            crop_ids,
            max_levels.get(f.element_id),
        )
        for g in fix_groups
        for f in (*g.pending, *g.done)
    }
    groups = {g.slug: _group(g, items) for g in fix_groups}
    top = _place_flashes(pop_flash(request), result, items, groups)
    errors: list[FieldError] = []
    if invalid is not None:
        errors = invalid[1].errors
        _apply_invalid(invalid, items, groups)
    context: dict[str, Any] = {
        "job": view,
        "groups": list(groups.values()),
        "errors": [{"field_id": e.field_id, "message": e.message} for e in errors],
        "flash": [],  # placed by _place_flashes instead of at the top of <main>
        "notices": top,
        "remaining": sum(len(g.pending) for g in fix_groups),
        "remaining_by_kind": [counted(len(g.pending), g.kind) for g in fix_groups if g.pending],
        "alt_guideline": ALT_WARN_CHARS,
        # No `active` nav item: this page is not "My documents" itself, so the link
        # must not be announced as the current page (aria-current="page").
    }
    return render(request, "review.html", context, status_code=status_code)


# --------------------------------------------------------------------------- routes


@router.get("/jobs/{job_id}/review")
def review_page(request: Request, job_id: str, user: UserDep) -> Response:
    return _page(request, job_id, user)


@router.post("/jobs/{job_id}/review/{element_id}", dependencies=[Depends(require_csrf)])
def review_action(
    request: Request,
    job_id: str,
    element_id: str,
    user: UserDep,
    action: Annotated[str, Form()] = "",
    latex: Annotated[str, Form()] = "",
    alt: Annotated[str, Form()] = "",
    decorative: Annotated[str, Form()] = "",
    level: Annotated[str, Form()] = "",
    min_confidence: Annotated[str, Form()] = "",
) -> Response:
    """One decision on one fix or, when `element_id` names a group ("equations",
    "figures", "headings"), a bulk accept in that group.

    Element ids look like "p3-e12", so a group name can never be one. Sharing the route
    keeps a single CSRF-checked, ownership-checked way to change a review.
    """
    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    if kind_for_slug(element_id) is not None:
        return _bulk_action(request, job_id, element_id, user, action, min_confidence)
    form = {"latex": latex, "alt": alt, "decorative": decorative, "level": level}
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
        return _page(request, job_id, user, invalid=(element_id, exc), status_code=422)
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
    flash(request, message, element_id=element_id)
    landing = next_to_review(result, element_id)
    anchor = f"item-{landing}" if landing else "review-progress"
    return RedirectResponse(f"/jobs/{job_id}/review#{anchor}", status_code=303)


def _bulk_action(
    request: Request, job_id: str, slug: str, user: CurrentUser, action: str, min_confidence: str
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
    return RedirectResponse(f"/jobs/{job_id}/review#group-{slug}", status_code=303)


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
