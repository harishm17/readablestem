"""What the instructor sets on the job page beyond the review: the title students hear (T1-7),
a contact line for the student file's footer (T1-17), and keeping the document longer (T1-8).

Each is a POST that is CSRF-checked and owner-only (`owned_job` answers 404 for someone
else's job), and works without JavaScript: it answers with a redirect to the job page and a
confirmation, or, when the typed text cannot be used, the job page again with the error
under the field and what was typed still in it.

Title and contact are stored on the job (`Job.title_override`, `Job.contact_line`) and the
HTML file is made again with them. They are document-derived text: shown to the owner,
never logged (only that the title or the contact line was set).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from a11y_stem.core.types import DocumentResult
from a11y_stem.web import job_page as page_rules
from a11y_stem.web import retention, titles
from a11y_stem.web.db import Job, session_scope, utcnow
from a11y_stem.web.deps import (
    CurrentUser,
    PageError,
    UserDep,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.review import rerender_job_html
from a11y_stem.web.routes.jobs import (
    AUTO_RELOAD_S,
    MAX_LIVE_JOBS_PER_USER,
    _crops,
    _job_course,
    after_download_state,
    live_job_count,
    load_job_status,
    owned_job,
    require_done,
    status_context,
)
from a11y_stem.web.views import last_decision

logger = logging.getLogger(__name__)
router = APIRouter()


def _job_page_with_errors(
    request: Request, job_id: str, user: CurrentUser, field: str, message: str, typed: str
) -> Response:
    """The job page again, status 400, with `message` under `field` and `typed` kept."""
    job, outcome = load_job_status(request, job_id, user)
    context: dict[str, Any] = status_context(job, outcome) | {"auto_reload": False}
    context["course"] = _job_course(request, job_id, user)
    context["after_download"] = after_download_state(request, job_id, user)
    context["auto_reload_s"] = AUTO_RELOAD_S
    context["errors"] = [{"field_id": field, "message": message}]
    context["field_errors"] = {field: {"field_id": field, "message": message}}
    context["typed"] = {field: typed}
    return render(request, "job.html", context, status_code=400)


def _remake_html(session: Session, job: Job) -> DocumentResult:
    """Make the HTML file again with the job's own choices (the caller holds the job)."""
    result = DocumentResult.model_validate(job.result_json or {})
    job.html = rerender_job_html(
        job, result, _crops(session, job.id), checked_on=last_decision(session, job)
    )
    # The static checks of the new file replace the stored ones (they ran in the render).
    job.result_json = result.model_dump(mode="json", exclude={"html"})
    return result


# --------------------------------------------------------------------------- title


@router.post("/jobs/{job_id}/title", dependencies=[Depends(require_csrf)])
def set_title(
    request: Request, job_id: str, user: UserDep, title: Annotated[str, Form()] = ""
) -> Response:
    """The title students hear. Empty, or the title the file would have anyway, means "the
    one read from the PDF" (no choice stored)."""
    ctx = get_ctx(request)
    try:
        typed = titles.clean_title(title)
    except titles.Invalid as exc:
        return _job_page_with_errors(request, job_id, user, "title", str(exc), title)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user, lock=True)
        require_done(job)
        result = DocumentResult.model_validate(job.result_json or {})
        chosen = typed if typed and typed != page_rules.suggested_title(result) else None
        if chosen == job.title_override:
            flash(request, "The title is already set to that.", undo=False)
            return RedirectResponse(f"/jobs/{job_id}", status_code=303)
        job.title_override = chosen
        _remake_html(session, job)
        retention.restart_clock(job, ctx.settings)
        now_title = chosen or page_rules.suggested_title(result)
    log_event(logger, "title_set", job_id=job_id, own=chosen is not None)
    flash(
        request,
        f"The title students hear is now “{now_title}”. The HTML file was made again with it.",
        undo=False,
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# --------------------------------------------------------------------------- contact


@router.post("/jobs/{job_id}/contact", dependencies=[Depends(require_csrf)])
def set_contact(
    request: Request, job_id: str, user: UserDep, contact: Annotated[str, Form()] = ""
) -> Response:
    """The contact line in the student file's footer: an email or web address, or none."""
    ctx = get_ctx(request)
    try:
        line = titles.clean_contact(contact)
    except titles.Invalid as exc:
        return _job_page_with_errors(request, job_id, user, "contact", str(exc), contact)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user, lock=True)
        require_done(job)
        if (line or None) == job.contact_line:
            flash(request, "The contact line is already set to that.", undo=False)
            return RedirectResponse(f"/jobs/{job_id}", status_code=303)
        job.contact_line = line or None
        _remake_html(session, job)
        retention.restart_clock(job, ctx.settings)
    log_event(logger, "contact_set", job_id=job_id, has_contact=bool(line))
    message = (
        "The contact line was added to the student file. The HTML file was made again."
        if line
        else "The contact line was removed from the student file."
    )
    flash(request, message, undo=False)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# --------------------------------------------------------------------------- keep


@router.post("/jobs/{job_id}/keep", dependencies=[Depends(require_csrf)])
def keep_longer(request: Request, job_id: str, user: UserDep) -> Response:
    """ "Keep 7 more days": seven days from now, only the owner sees it. Refused (429, nothing
    changed) when keeping this document would put the person over the number of documents
    they may keep (`MAX_LIVE_JOBS_PER_USER`, the cap the upload also enforces)."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user, lock=True)
        if retention.can_keep_longer(job):
            others = live_job_count(session, user.id, utcnow(), excluding=job.id)
            if others + 1 > MAX_LIVE_JOBS_PER_USER:
                log_event(logger, "keep_refused", logging.WARNING, job_id=job_id)
                raise PageError(
                    429,
                    "This document was not kept longer",
                    f"You are keeping {others + 1} documents, and the most allowed is "
                    f"{MAX_LIVE_JOBS_PER_USER}. Delete one you no longer need, then choose "
                    "Keep 7 more days again. Nothing was changed.",
                    context={"job_id": job_id},
                )
        changed = retention.keep_seven_more_days(job)
    if changed:
        log_event(logger, "kept_longer", job_id=job_id)
        flash(
            request, "This document will be kept for 7 more days. Only you can see it.", undo=False
        )
    else:
        flash(request, "This document is already kept for longer than 7 days.", undo=False)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
