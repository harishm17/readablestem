"""Download all: one zip of the documents that are ready to share (T3-4, `web.bundle`).

`GET /app/download` is every ready document of the person, `GET /courses/{id}/download` the
ones converted for one course. Both are plain GETs for the owner, like a single download: no
script is needed, nothing is changed except that downloading restarts the deletion clock of
the documents it took (`retention.restart_clock`, as a single download does), and each
document counts as downloaded for the usage events. A document with decisions still open is
a draft and is left out, with the count in the zip's `contents.txt` and on the page that
offered the link.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from readablestem.web import bundle, documents, events, notation, retention
from readablestem.web.db import Job, JobStatus, session_scope, utcnow
from readablestem.web.deps import JOB_ID, PageError, UserDep, get_ctx
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import (
    NOT_FOUND,
    content_disposition,
    current_html,
    download_filename,
    slug,
)
from readablestem.web.security import VIEW_CSP

logger = logging.getLogger(__name__)
router = APIRouter()


def zip_name(label: str) -> str:
    return f"{slug(label) or 'documents'}-documents.zip"


def _zip_response(request: Request, user, course_id: str | None, label: str) -> Response:
    ctx = get_ctx(request)
    now = utcnow()
    with session_scope(ctx.factory) as session:
        ids = session.scalars(
            select(Job.id)
            .where(*documents._live(user.id, now, course_id), Job.status == JobStatus.DONE)
            .order_by(Job.created_at.desc(), Job.id)
        ).all()
        items: list[tuple[str, str, str]] = []
        taken: list[str] = []
        held_back = 0
        for job_id in ids:
            job = session.get(Job, job_id)
            if job is None or (job.html is None and not job.html_stale):
                continue
            result = job.result_json or {}
            if documents.open_decisions(result.get("fixes")):
                held_back += 1
                continue
            job = current_html(ctx, session, job)
            title = documents.display_title(
                (job.result_json or {}).get("title"), job.filename, job.title_override
            )
            items.append((title, download_filename(title, job.id), job.html or ""))
            taken.append(job.id)
            retention.restart_clock(job, ctx.settings)
        made = bundle.build(items, held_back=held_back)
        # Only the documents that are in the zip count as downloaded.
        in_zip = {name for _, name in made.included}
        downloaded = [j for j, (_, name, _) in zip(taken, items, strict=True) if name in in_zip]
    if not made.included:
        raise PageError(
            409,
            "Nothing to download yet",
            "None of these documents is ready to share. A document is ready when it has "
            "finished converting and no decision is left to make. Nothing was downloaded.",
        )
    for job_id in downloaded:
        events.record_download(
            ctx.factory, ctx.settings, user_id=user.id, job_id=job_id, open_decisions=0
        )
    log_event(
        logger,
        "documents_zipped",
        documents=len(made.included),
        held_back=made.held_back,
        left_out=made.left_out_size,
        course=bool(course_id),
    )
    return Response(
        made.data,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(zip_name(label)),
            "Content-Security-Policy": VIEW_CSP,
        },
    )


@router.get("/app/download")
def download_all(request: Request, user: UserDep) -> Response:
    """Every ready document of the person, as one zip."""
    return _zip_response(request, user, None, "my")


@router.get("/courses/{course_id}/download")
def download_course(request: Request, course_id: str, user: UserDep) -> Response:
    """The ready documents converted for one course, as one zip (a 404 for someone else's
    course, or with courses switched off)."""
    ctx = get_ctx(request)
    if not notation.enabled(ctx.settings) or not JOB_ID.match(course_id):
        raise PageError(404, *NOT_FOUND)
    with session_scope(ctx.factory) as session:
        course = notation.owned_course(session, course_id, user.id)
        if course is None:
            raise PageError(404, *NOT_FOUND)
        name = course.name
    return _zip_response(request, user, course_id, name)
