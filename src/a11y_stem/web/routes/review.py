"""Review screen: every fix (needs-review first) with accept / save edit / reject.

A decision is one transaction: lock the job row, apply the decision to the stored result
(`web.review`), store a `ReviewEdit`, re-render the output HTML with the pipeline's
assembler, save. If any step fails nothing changes. Without JavaScript each button is a
normal form POST that redirects back to the same fix (`#fix-<id>`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup
from sqlalchemy import select

from a11y_stem.core.types import Check, DocumentResult, Fix, Kind, Status
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
from a11y_stem.web.review import (
    STATUS_FOR_ACTION,
    FieldError,
    ReviewInvalid,
    apply_review,
    can_accept,
    needs_attention,
    rerender_html,
    review_order,
)
from a11y_stem.web.routes.jobs import NOT_FOUND, owned_job, require_done
from a11y_stem.web.views import FIX_STATUS_LABELS, KIND_LABELS, JobView, job_view

logger = logging.getLogger(__name__)
router = APIRouter()

DONE_MESSAGES = {
    "accept": "Accepted.",
    "edit": "Your edit passed the checks and was saved.",
    "reject": "Rejected. The original from the PDF is kept in the document.",
}


@dataclass
class ReviewItem:
    number: int
    element_id: str
    kind: str
    kind_label: str
    page: int  # 1-based
    status: str
    status_label: str
    confidence_label: str
    failed_checks: list[Check]
    reason: str | None
    crop_url: str | None
    mathml: Markup | None
    speech: str | None
    alt: str | None
    decorative: bool
    heading_level: int | None
    heading_text: str | None
    can_accept: bool
    values: dict[str, str]
    errors: dict[str, str] = field(default_factory=dict)  # field prefix -> message
    notices: list[str] = field(default_factory=list)


def _item(
    number: int, fix: Fix, result: DocumentResult, job_id: str, crop_ids: set[str]
) -> ReviewItem:
    element = next((e for e in result.elements if e.id == fix.element_id), None)
    values: dict[str, str] = {}
    if fix.equation is not None:
        values["latex"] = fix.equation.latex
    if fix.figure is not None:
        values["alt"] = fix.figure.alt
        values["decorative"] = "on" if fix.figure.decorative else ""
    if fix.heading is not None:
        values["level"] = str(fix.heading.level)
    confidence = "Not given" if fix.confidence is None else f"{round(fix.confidence * 100)}%"
    return ReviewItem(
        number=number,
        element_id=fix.element_id,
        kind=fix.kind.value,
        kind_label=KIND_LABELS.get(fix.kind, fix.kind.value.title()),
        page=(element.page + 1) if element else 0,
        status=fix.status.value,
        status_label=FIX_STATUS_LABELS.get(fix.status, fix.status.value),
        confidence_label=confidence,
        failed_checks=fix.failed_checks,
        reason=fix.reason,
        crop_url=f"/jobs/{job_id}/crops/{fix.element_id}.png"
        if fix.element_id in crop_ids
        else None,
        mathml=try_sanitize_mathml(fix.equation.mathml) if fix.equation else None,
        speech=fix.equation.speech if fix.equation else None,
        alt=fix.figure.alt if fix.figure else None,
        decorative=bool(fix.figure and fix.figure.decorative),
        heading_level=fix.heading.level if fix.heading else None,
        heading_text=fix.heading.text if fix.heading else (element.text if element else None),
        can_accept=can_accept(fix) and fix.status is not Status.ACCEPTED,
        values=values,
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


def _page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    *,
    invalid: tuple[str, ReviewInvalid] | None = None,
    status_code: int = 200,
) -> Response:
    view, result, crop_ids = _load(request, job_id, user)
    items = [
        _item(i, fix, result, view.id, crop_ids)
        for i, fix in enumerate(review_order(result), start=1)
        if fix.kind is not Kind.PARAGRAPH
    ]
    flashes = pop_flash(request)
    progress: list[str] = []
    for item in items:
        item.notices = [f["message"] for f in flashes if f.get("element_id") == item.element_id]
        progress += [f"{item.kind_label} on page {item.page}: {n}" for n in item.notices]
    general = [f for f in flashes if not f.get("element_id")]
    errors: list[FieldError] = []
    if invalid is not None:
        element_id, exc = invalid
        errors = exc.errors
        for item in items:
            if item.element_id == element_id:
                item.values.update(exc.values)
                for error in exc.errors:
                    item.errors[error.field_id.split("-", 1)[0]] = error.message
    context: dict[str, Any] = {
        "job": view,
        "items": items,
        "errors": [{"field_id": e.field_id, "message": e.message} for e in errors],
        "flash": general,
        "progress": progress,
        "remaining": sum(1 for item in items if needs_attention(item.status)),
        "active": "app",
    }
    return render(request, "review.html", context, status_code=status_code)


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
) -> Response:
    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    form = {"latex": latex, "alt": alt, "decorative": decorative, "level": level}
    try:
        with session_scope(get_ctx(request).factory) as session:
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            result = DocumentResult.model_validate(job.result_json or {})
            change = apply_review(result, element_id, action, form)
            crops = {
                b.element_id: b.content
                for b in session.scalars(
                    select(Blob).where(Blob.job_id == job.id, Blob.kind == "crop")
                )
                if b.element_id
            }
            job.html = rerender_html(result, crops)
            job.result_json = result.model_dump(mode="json", exclude={"html"})
            session.add(
                ReviewEdit(
                    job_id=job.id,
                    user_id=user.id,
                    element_id=element_id,
                    action=action,
                    before=change.before,
                    after=change.after,
                )
            )
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
        message += " Some checks gave warnings: " + ", ".join(
            c.code or c.name for c in change.warnings
        )
    flash(request, message, element_id=element_id)
    # A fix that needed attention has now moved down the list, so go back to the progress
    # note above the list, where the next fix to review now sits. Otherwise stay on it.
    anchor = "review-progress" if needs_attention(change.before["status"]) else f"fix-{element_id}"
    return RedirectResponse(f"/jobs/{job_id}/review#{anchor}", status_code=303)
