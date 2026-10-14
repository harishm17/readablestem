"""/jobs/{id}/feedback: the two optional questions after a download (`web.feedback`).

GET shows them on a page of their own (where errors are shown, and a link to them works
without JavaScript); POST stores the answers as usage events and goes back to the
document. Owner only, like every /jobs/* route, and a 404 when the questions are off.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from readablestem.web import documents, events, feedback
from readablestem.web.db import session_scope
from readablestem.web.deps import (
    CurrentUser,
    PageError,
    UserDep,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import NOT_FOUND, owned_job

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_on(request: Request) -> None:
    if not feedback.enabled(get_ctx(request).settings):
        raise PageError(404, *NOT_FOUND)


def _page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    *,
    values: dict[str, str] | None = None,
    errors: list[feedback.FieldError] | None = None,
    status_code: int = 200,
) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        title = documents.display_title((job.result_json or {}).get("title"), job.filename)
        already = events.answered(session, user.id, job.id)
    errors = errors or []
    context: dict[str, Any] = {
        "job_id": job_id,
        "doc_title": title,
        "answered": already,
        "choices": feedback.WILL_POST_CHOICES,
        "values": values or {},
        "errors": [{"field_id": e.field_id, "message": e.message} for e in errors],
        "field_errors": {e.field_id: e.message for e in errors},
    }
    return render(request, "feedback.html", context, status_code=status_code)


@router.get("/jobs/{job_id}/feedback", dependencies=[Depends(_require_on)])
def feedback_page(request: Request, job_id: str, user: UserDep) -> Response:
    return _page(request, job_id, user)


@router.post("/jobs/{job_id}/feedback", dependencies=[Depends(require_csrf), Depends(_require_on)])
def save_feedback(
    request: Request,
    job_id: str,
    user: UserDep,
    will_post: Annotated[str, Form()] = "",
    minutes: Annotated[str, Form()] = "",
) -> Response:
    """Save the answers once per document; a second answer keeps the first (so a
    reload or a second tab cannot count twice)."""
    answers, errors = feedback.parse(will_post, minutes)
    if errors:
        values = {"will_post": will_post, "minutes": minutes[:20]}
        return _page(request, job_id, user, values=values, errors=errors, status_code=422)
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        if events.answered(session, user.id, job.id):
            message = "You had already answered for this document, so your first answers are kept."
        elif answers.empty:
            message = "Nothing was sent: both questions were left empty."
        else:
            feedback.record(session, user.id, job.id, answers)
            message = "Thank you. Your answers were saved."
    log_event(
        logger,
        "feedback",
        job_id=job_id,
        will_post=answers.will_post,
        minutes=answers.minutes is not None,
    )
    flash(request, message)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
