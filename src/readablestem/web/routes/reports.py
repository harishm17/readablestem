"""/jobs/{id}/report: the problem-report form (`web.reports`).

GET shows one short form with the ids the report will carry; POST stores the row and goes
back to where the person was (the item on the review screen, or the document's page) with a
confirmation and a reference. It works without script, and a failed send shows the error
summary with focus on it, like every signed-in form. The owner and a person the document was
shared with for review may report (the same right as the review screen's "Report this item");
anyone else gets the page they would get from any other `/jobs/*` route.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from readablestem.web import documents, reports
from readablestem.web.db import session_scope
from readablestem.web.deps import (
    CurrentUser,
    UserDep,
    enforce,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from readablestem.web.forms import submit_once
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import job_access
from readablestem.web.shares import REVIEW

logger = logging.getLogger(__name__)
router = APIRouter()

TEXT_FIELD = "report-text"


def _back(where: reports.Where) -> str:
    """Where the person was, built from the stored job's and the stored element's ids
    (`reports.where_of`), not from what the form sent."""
    if where.card:
        return f"/jobs/{where.job_id}/review?item={where.card}"
    return f"/jobs/{where.job_id}"


def _page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    item: str | None,
    *,
    text: str = "",
    error: str | None = None,
) -> Response:
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, REVIEW)
        title = documents.document_title(job).text
        where = reports.where_of(job, item)
    context: dict[str, Any] = {
        "job_id": job_id,
        "doc_title": title,
        "where": where,
        "back": _back(where),
        "text": text,
        "max_text": reports.MAX_TEXT,
        "closed_days": reports.CLOSED_DAYS,
        "max_days": reports.MAX_DAYS,
        "errors": [{"field_id": TEXT_FIELD, "message": error}] if error else [],
        "text_error": error or "",
    }
    return render(request, "report.html", context, status_code=422 if error else 200)


@router.get("/jobs/{job_id}/report")
def report_page(request: Request, job_id: str, user: UserDep, item: str = "") -> Response:
    return _page(request, job_id, user, item)


@router.post("/jobs/{job_id}/report", dependencies=[Depends(require_csrf)])
@submit_once("report.send")
def send_report(
    request: Request,
    job_id: str,
    user: UserDep,
    item: Annotated[str, Form()] = "",
    text: Annotated[str, Form()] = "",
) -> Response:
    clean, error = reports.clean_text(text)
    if error:
        return _page(request, job_id, user, item, text=text[: reports.MAX_TEXT * 2], error=error)
    with session_scope(get_ctx(request).factory) as session:
        job = job_access(session, job_id, user, REVIEW)
        where = reports.where_of(job, item)
    enforce(request, "report", user.id)  # counted when it is about to be written
    with session_scope(get_ctx(request).factory) as session:
        number = reports.add(session, user.id, where, clean).id
    log_event(
        logger,
        "report_sent",
        job_id=job_id,
        item_kind=where.item_kind or "none",
        has_text=bool(clean),
    )
    flash(
        request,
        f"Thank you. Your report went to the people who run ReadableSTEM. "
        f"Its reference is {number}.",
        undo=False,
        kind="success",
    )
    return RedirectResponse(_back(where), status_code=303)
