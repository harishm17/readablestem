"""The review record of a finished document (T3-6): a page to print and a CSV, for the
person's accessibility office. Owner only; both are GETs that change nothing about the
document (a record is made on request from what is stored, `web.record`, after the HTML file
is brought up to date if a review write left it out of date), so they do not restart the
deletion clock and are not rate limited. The words say what the record is and is not.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from a11y_stem.core.types import DocumentResult
from a11y_stem.web import documents, record
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import ReviewEdit, User, session_scope, utcnow
from a11y_stem.web.deps import CurrentUser, UserDep, get_ctx, render
from a11y_stem.web.routes.jobs import (
    content_disposition,
    current_html,
    owned_job,
    require_done,
    slug,
)

router = APIRouter()


def _record(request: Request, job_id: str, user: CurrentUser) -> tuple[record.Record, str]:
    """The job's record, and the base of its file name."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        # The automatic checks it lists are those of the file as it is now: a review write
        # only marks the file out of date, and it is made again here (`jobs.current_html`).
        # That is not a change to the document, so the deletion clock is not touched.
        job = current_html(ctx, session, job)
        result = DocumentResult.model_validate(job.result_json or {})
        edits = list(session.scalars(select(ReviewEdit).where(ReviewEdit.job_id == job.id)))
        person = session.get(User, job.user_id)
        owner = user.email
        if person is not None:
            owner = f"{person.name} ({person.email})" if person.name else person.email
        title = documents.display_title(result.title, job.filename, job.title_override)
        made = record.build(
            result=result,
            edits=edits,
            who={job.user_id: owner},
            owner=owner,
            filename=job.filename,
            title=title,
            job_ref=job.id[:6],
            converted_at=as_utc(job.finished_at),
            made_at=utcnow(),
            title_chosen=bool(job.title_override),
            contact_set=bool(job.contact_line),
        )
        return made, f"review-record-{slug(title) or 'document'}-{job.id[:6]}"


@router.get("/jobs/{job_id}/record")
def record_page(request: Request, job_id: str, user: UserDep) -> Response:
    made, _ = _record(request, job_id, user)
    context = {
        "record": made,
        "job_id": job_id,
        "statement": record.STATEMENT,
        "keep_note": record.KEEP_NOTE,
        "long_when": record.long_when,
    }
    return render(request, "record.html", context)


@router.get("/jobs/{job_id}/record.csv")
def record_csv(request: Request, job_id: str, user: UserDep) -> Response:
    made, name = _record(request, job_id, user)
    body = "\ufeff" + record.to_csv(made)  # the mark lets Excel read the letters right
    return Response(
        body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(name + ".csv")},
    )
