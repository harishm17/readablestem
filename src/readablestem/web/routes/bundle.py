"""Download all: one zip of the documents that are ready to share (T3-4, `web.bundle`).

`GET /app/download` is every ready document of the person, `GET /courses/{id}/download` the
ones converted for one course. Both are plain GETs for the owner, like a single download: no
script is needed, nothing is changed except that downloading restarts the deletion clock of
the documents it took (`retention.restart_clock`, as a single download does), and each
document counts as downloaded for the usage events. A document with decisions still open is
a draft and is left out, with the count in the zip's `contents.txt` and on the page that
offered the link.

Owner only, in every format (G1): the zip is built from the person's own rows (`documents._live(user.id, ...)`),
never from a document shared with them, so there is no `job_access` call to make.

`?format=docx` or `?format=epub` makes the zip of Word or EPUB files instead of HTML, with the
same rules and limits plus the Word export's (`web.bundle`, `routes.exports.make_file`); each
document counts as downloaded in that file type.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from readablestem.pipeline import export_docx, export_epub
from readablestem.web import bundle, documents, events, notation, retention
from readablestem.web.db import Job, JobStatus, session_scope, utcnow
from readablestem.web.deps import JOB_ID, PageError, UserDep, get_ctx
from readablestem.web.logsafe import log_event
from readablestem.web.routes import exports
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


_ENDING = {"html": ".html", "docx": ".docx", "epub": ".epub"}
_ZIP_WORD = {"html": "documents", "docx": "word-documents", "epub": "epub-documents"}


def zip_name(label: str, file_type: str = "html") -> str:
    return f"{slug(label) or 'documents'}-{_ZIP_WORD[file_type]}.zip"


def _zip_response(
    request: Request, user, course_id: str | None, label: str, file_type: str = "html"
) -> Response:
    ctx = get_ctx(request)
    now = utcnow()
    cap = bundle.limit(file_type)
    failed = 0
    over_cap = 0
    with session_scope(ctx.factory) as session:
        ids = session.scalars(
            select(Job.id)
            .where(*documents._live(user.id, now, course_id), Job.status == JobStatus.DONE)
            .order_by(Job.created_at.desc(), Job.id)
        ).all()
        items: list[tuple[str, str, str | bytes]] = []
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
            if len(items) >= cap:  # the zip is full: do not make (or restart) the rest
                over_cap += 1
                continue
            job = current_html(ctx, session, job)
            title = documents.display_title(
                (job.result_json or {}).get("title"), job.filename, job.title_override
            )
            name = download_filename(title, job.id).removesuffix(".html") + _ENDING[file_type]
            if file_type == "html":
                content: str | bytes = job.html or ""
            else:
                try:
                    content = exports.make_file(file_type, job.html or "", title)
                except exports.Busy:
                    raise exports.busy_page() from None
                except (export_docx.DocxExportError, export_epub.CanvasExportError) as exc:
                    log_event(
                        logger,
                        "bundle_export_failed",
                        file_type=file_type,
                        code=getattr(exc, "code", "image_type"),
                    )
                    failed += 1
                    continue
            items.append((title, name, content))
            taken.append(job.id)
            retention.restart_clock(job, ctx.settings)
        made = bundle.build(
            items, held_back=held_back, failed=failed, left_out=over_cap, file_type=file_type
        )
        # Only the documents that are in the zip count as downloaded.
        in_zip = {name for _, name in made.included}
        downloaded = [j for j, (_, name, _) in zip(taken, items, strict=True) if name in in_zip]
    if not made.included and made.failed:
        raise PageError(
            409,
            "These documents cannot be made in that file type",
            "None of the ready documents could be made as Word or EPUB files. The HTML files "
            "are still available. Nothing was downloaded.",
        )
    if not made.included:
        raise PageError(
            409,
            "Nothing to download yet",
            "None of these documents is ready to share. A document is ready when it has "
            "finished converting and no decision is left to make. Nothing was downloaded.",
        )
    for job_id in downloaded:
        events.record_download(
            ctx.factory,
            ctx.settings,
            user_id=user.id,
            job_id=job_id,
            open_decisions=0,
            file_type=file_type,
        )
    log_event(
        logger,
        "documents_zipped",
        documents=len(made.included),
        held_back=made.held_back,
        left_out=made.left_out_size,
        failed=made.failed,
        file_type=file_type,
        course=bool(course_id),
    )
    return Response(
        made.data,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(zip_name(label, file_type)),
            "Content-Security-Policy": VIEW_CSP,
        },
    )


@router.get("/app/download")
def download_all(request: Request, user: UserDep, format: str = "html") -> Response:
    """Every ready document of the person, as one zip of HTML files (or, with `?format=docx`
    or `?format=epub`, of Word or EPUB files)."""
    return _zip_response(request, user, None, "my", exports.checked_format(format))


@router.get("/courses/{course_id}/download")
def download_course(
    request: Request, course_id: str, user: UserDep, format: str = "html"
) -> Response:
    """The ready documents converted for one course, as one zip (a 404 for someone else's
    course, or with courses switched off). `format` is as for `/app/download`."""
    ctx = get_ctx(request)
    if not notation.enabled(ctx.settings) or not JOB_ID.match(course_id):
        raise PageError(404, *NOT_FOUND)
    with session_scope(ctx.factory) as session:
        course = notation.owned_course(session, course_id, user.id)
        if course is None:
            raise PageError(404, *NOT_FOUND)
        name = course.name
    return _zip_response(request, user, course_id, name, exports.checked_format(format))
