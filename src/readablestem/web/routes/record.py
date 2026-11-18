"""The review record of a finished document (T3-6): a page to print and a CSV, for the
person's accessibility office. For anyone who may open the document (`shares.VIEW`). Both are
GETs that change nothing about the document (a record is made on request from what is stored,
`web.record`, after the HTML file is brought up to date if a review write left it out of
date), so they do not restart the deletion clock and are not rate limited. The words say what
the record is and is not.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from readablestem.core.types import DocumentResult
from readablestem.pipeline import checked
from readablestem.web import documents, events, record, shares, states
from readablestem.web.database import as_utc
from readablestem.web.db import ReviewEdit, User, session_scope, utcnow
from readablestem.web.deps import CurrentUser, UserDep, get_ctx, render
from readablestem.web.routes.jobs import (
    content_disposition,
    current_html,
    job_access,
    require_done,
    slug,
)
from readablestem.web.shares import VIEW
from readablestem.web.views import audience_of

router = APIRouter()


def _record(request: Request, job_id: str, user: CurrentUser) -> tuple[record.Record, str]:
    """The job's record, and the base of its file name."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, VIEW)
        require_done(job)
        # The automatic checks it lists are those of the file as it is now: a review write
        # only marks the file out of date, and it is made again here (`jobs.current_html`).
        # That is not a change to the document, so the deletion clock is not touched.
        job = current_html(ctx, session, job)
        result = DocumentResult.model_validate(job.result_json or {})
        edits = list(session.scalars(select(ReviewEdit).where(ReviewEdit.job_id == job.id)))
        is_owner = job.user_id == user.id
        owner = user.email if is_owner else "the owner"
        # Who made each edit: the owner, and anyone the document was shared with for review.
        # A person whose account is gone has no edits left (they go with it), so every
        # edit's author is found.
        who: dict[str, str] = {}
        for person in session.scalars(
            select(User).where(User.id.in_({job.user_id, *(e.user_id for e in edits)}))
        ):
            if is_owner:  # only the owner sees addresses
                who[person.id] = f"{person.name} ({person.email})" if person.name else person.email
            else:  # everyone else sees names, and "the owner" or "a reviewer" for no name
                fallback = "the owner" if person.id == job.user_id else "a reviewer"
                who[person.id] = person.name or fallback
        owner = who.get(job.user_id, owner)
        title = documents.document_title(job).text
        role = shares.role_of(session, job, user) or VIEW
        viewer = audience_of(role)
        state = states.state_of(
            states.Facts.of(job),
            states.counts_of(checked.of_result(result)),
            viewer=viewer,
        )
        made = record.build(
            result=result,
            edits=edits,
            who=who,
            owner=owner,
            filename=job.filename,
            title=title,
            job_ref=job.id[:6],
            converted_at=as_utc(job.finished_at),
            made_at=utcnow(),
            title_chosen=bool(job.title_override),
            contact_set=bool(job.contact_line),
            exam_mode=bool(job.exam_mode),
            downloaded_formats=(
                events.downloaded_formats(session, job.id) if ctx.settings.events_enabled else None
            ),
            state=state,
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


def record_csv_file(request: Request, job_id: str, user: CurrentUser) -> tuple[bytes, str]:
    """The record as the bytes of its CSV file and the file's name."""
    made, name = _record(request, job_id, user)
    # The mark lets Excel read the letters right.
    return ("\ufeff" + record.to_csv(made)).encode("utf-8"), name + ".csv"


@router.get("/jobs/{job_id}/record.csv")
def record_csv(request: Request, job_id: str, user: UserDep) -> Response:
    body, name = record_csv_file(request, job_id, user)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(name)},
    )
