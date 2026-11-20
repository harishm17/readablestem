"""Download all: one zip of the documents that are ready to share (T3-4, `web.bundle`).

`GET /app/download` is every ready document of the person, `GET /courses/{id}/download` the
ones converted for one course. Both are plain GETs for the owner, like a single download: no
script is needed, nothing is changed except that downloading restarts the deletion clock of
the documents it took (`retention.restart_clock`, as a single download does), and each
document counts as downloaded for the usage events. A document with decisions still open is
a draft and is left out, with the count in the zip's `contents.txt` and on the page that
offered the link.

Owner only, in every format (G1): the zip is built from the person's own rows
(`documents._live(user.id, ...)`), never from a document shared with them, so there is no
`job_access` call to make.

`?format=docx` or `?format=epub` makes the zip of Word or EPUB files instead of HTML, with the
same rules and limits plus the Word export's (`web.bundle`, `routes.exports.make_file`); each
document counts as downloaded in that file type.
"""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import load_only

from readablestem.pipeline import export_docx, export_epub
from readablestem.web import bundle, documents, events, notation, output, retention
from readablestem.web.dates import machine_date
from readablestem.web.db import Job, JobStatus, User, session_scope, utcnow
from readablestem.web.deps import JOB_ID, PageError, UserDep, enforce, get_ctx
from readablestem.web.logsafe import log_event
from readablestem.web.routes import exports
from readablestem.web.routes.jobs import (
    NOT_FOUND,
    content_disposition,
    current_html,
    download_filename,
    head_reply,
    slug,
)
from readablestem.web.routes.record import record_csv_file
from readablestem.web.security import VIEW_CSP
from readablestem.web.seo import is_head

logger = logging.getLogger(__name__)
router = APIRouter()


EVERYTHING_NAME = "readablestem-everything.zip"
_FOLDERS = {"html": "html", "docx": "word", "epub": "epub"}
_ENDING = {"html": ".html", "docx": ".docx", "epub": ".epub"}
_ZIP_WORD = {"html": "documents", "docx": "word-documents", "epub": "epub-documents"}


def zip_name(label: str, file_type: str = "html") -> str:
    return f"{slug(label) or 'documents'}-{_ZIP_WORD[file_type]}.zip"


@dataclass
class _Ready:
    """The ready documents of a person in one file type, made once for a zip."""

    items: list[tuple[str, str, str | bytes]] = field(default_factory=list)  # title, name, file
    taken: list[str] = field(default_factory=list)  # job ids, in the order of `items`
    held_back: int = 0  # a decision still open
    failed: int = 0  # could not be made as this file type
    over_cap: int = 0  # ready, but the zip is full


def _ready_files(
    request: Request,
    user,
    course_id: str | None,
    file_type: str,
    job_ids: list[str] | None = None,
) -> _Ready:
    """Every ready document of `user` (of a course, when given) as `file_type`, up to the
    zip's cap. The deletion clock of each document taken restarts, as a single download's
    does. "Download all" and "Download everything" (`/account/export`) both read this."""
    ctx = get_ctx(request)
    now = utcnow()
    cap = bundle.limit(file_type)
    ready = _Ready()
    with session_scope(ctx.factory) as session:
        # One query for the summary columns `output.export_state` reads (the result and the
        # page are deferred): which are ready, held back or over the cap is known before any
        # document is loaded. Only a document taken is read further, one at a time.
        rows = session.scalars(
            select(Job)
            .options(
                load_only(
                    Job.id,
                    Job.status,
                    Job.html_stale,
                    Job.html_version,
                    Job.open_decisions,
                    # What a document taken reads next: its title, name and deletion clock.
                    Job.filename,
                    Job.title_override,
                    Job.result_title,
                    Job.retention_mode,
                    Job.retain_until,
                )
            )
            .where(*documents._live(user.id, now, course_id, job_ids), Job.status == JobStatus.DONE)
            .order_by(Job.created_at.desc(), Job.id)
        ).all()
        for job in rows:
            if not (job.html_stale or job.has_html):
                continue
            if output.export_state(job).key != output.READY:  # a draft stays out (SW21)
                ready.held_back += 1
                continue
            if len(ready.items) >= cap:  # the zip is full: do not make (or restart) the rest
                ready.over_cap += 1
                continue
            job = current_html(ctx, session, job)
            title = documents.document_title(job).text
            name = download_filename(title, job.id).removesuffix(".html") + _ENDING[file_type]
            if file_type == "html":
                content: str | bytes = job.html or ""
            else:
                try:
                    content = exports.stored_file(
                        lambda: nullcontext(session),
                        job.id,
                        job.result_version or 0,
                        file_type,
                        job.html or "",
                        title,
                    )
                except exports.Busy:
                    raise exports.busy_page() from None
                except (export_docx.DocxExportError, export_epub.CanvasExportError) as exc:
                    log_event(
                        logger,
                        "bundle_export_failed",
                        file_type=file_type,
                        code=getattr(exc, "code", "image_type"),
                    )
                    ready.failed += 1
                    continue
            ready.items.append((title, name, content))
            ready.taken.append(job.id)
            retention.restart_clock(job, ctx.settings)
    return ready


def _count_downloads(request: Request, user, job_ids: list[str], file_type: str) -> None:
    ctx = get_ctx(request)
    for job_id in job_ids:
        events.record_download(
            ctx.factory,
            ctx.settings,
            user_id=user.id,
            job_id=job_id,
            open_decisions=0,
            file_type=file_type,
        )


def _zip_response(
    request: Request,
    user,
    course_id: str | None,
    label: str,
    file_type: str = "html",
    job_ids: list[str] | None = None,
) -> Response:
    if is_head(request):
        # The zip's name and type; whether anything is ready is known only by going through
        # the documents, which a HEAD does not do (nothing is built, restarted or recorded).
        return head_reply("application/zip", zip_name(label, file_type))
    enforce(request, "zip_user", user.id)  # building a zip is the cost; a HEAD builds none
    ready = _ready_files(request, user, course_id, file_type, job_ids)
    made = bundle.build(
        ready.items,
        held_back=ready.held_back,
        failed=ready.failed,
        left_out=ready.over_cap,
        file_type=file_type,
    )
    # Only the documents that are in the zip count as downloaded.
    in_zip = {name for _, name in made.included}
    downloaded = [
        j for j, (_, name, _) in zip(ready.taken, ready.items, strict=True) if name in in_zip
    ]
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
            "None of these documents is ready to post. A document is ready when it has "
            "finished converting and no decision is left to make. Nothing was downloaded.",
        )
    _count_downloads(request, user, downloaded, file_type)
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


def _courses_json(request: Request, user) -> bytes | None:
    """The person's courses with their notation, or None when courses are switched off."""
    ctx = get_ctx(request)
    if not notation.enabled(ctx.settings):
        return None
    courses = []
    with session_scope(ctx.factory) as session:
        for view in notation.list_courses(session, user.id):
            course = notation.owned_course(session, view.id, user.id)
            if course is None:
                continue
            courses.append(
                {
                    "name": view.name,
                    "notation": [
                        {
                            "symbol": e.symbol,
                            "meaning": e.meaning_label,
                            "note": e.note,
                            "source": e.source,
                        }
                        for e in notation.entries(course)
                    ],
                }
            )
    return json.dumps({"courses": courses}, indent=2, ensure_ascii=False).encode("utf-8")


def _account_json(request: Request, user) -> bytes:
    with session_scope(get_ctx(request).factory) as session:
        row = session.get(User, user.id)
        settings = {
            "email": user.email,
            "name": user.name,
            "account_made": machine_date(row.created_at) or None if row is not None else None,
            "keep_new_documents_for_7_days": bool(row is not None and row.keep_week_default),
        }
    return json.dumps(settings, indent=2, ensure_ascii=False).encode("utf-8")


@router.get("/account/export")
def download_everything(request: Request, user: UserDep) -> Response:
    """Everything a person can use again, in one zip: every ready document in each file type,
    the review record of each, their courses with notation and their account settings. Built
    from the same rows and by the same code as "Download all" (`_ready_files`); owner only,
    like it. A document with a decision still open is left out and counted in `contents.txt`."""
    if is_head(request):
        return head_reply("application/zip", EVERYTHING_NAME)
    enforce(request, "zip_user", user.id)
    files: list[tuple[str, bytes]] = []
    notes: list[str] = []
    record_ids: list[str] = []
    for file_type in bundle.FORMATS:
        ready = _ready_files(request, user, None, file_type)
        folder = _FOLDERS[file_type]
        for _, name, content in ready.items:
            raw = content.encode("utf-8") if isinstance(content, str) else content
            files.append((f"documents/{folder}/{name}", raw))
        _count_downloads(request, user, ready.taken, file_type)
        if file_type == "html":
            record_ids = ready.taken
        if ready.held_back and file_type == "html":
            notes.append(
                f"Not included: {ready.held_back} document(s) with decisions still to make."
            )
        if ready.failed:
            notes.append(
                f"Not included as {folder}: {ready.failed} document(s) it cannot be made as."
            )
        if ready.over_cap:
            notes.append(
                f"Not included as {folder}: {ready.over_cap} more document(s), because one zip "
                f"holds at most {bundle.limit(file_type)} of them. Download them one by one."
            )
    for job_id in record_ids:
        raw, name = record_csv_file(request, job_id, user)
        files.append((f"records/{name}", raw))
    courses = _courses_json(request, user)
    if courses is not None:
        files.append(("courses.json", courses))
    files.append(("account.json", _account_json(request, user)))
    log_event(logger, "everything_zipped", documents=len(record_ids))
    return Response(
        bundle.build_everything(files, notes),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(EVERYTHING_NAME),
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
