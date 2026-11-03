"""Word and EPUB downloads of a finished document: `/jobs/{id}/download.docx` and
`/jobs/{id}/download.epub`.

Both are made from the finished HTML on request (`pipeline/export_docx.py`,
`pipeline/export_epub.py`), in memory, and are not stored. They follow the HTML download
(`routes.jobs.download`) in every rule: a plain GET for the owner (another person's or an
expired document is a 404, a document that has not finished is a 409), the file is brought
up to date first (`jobs.current_html`), the deletion clock restarts, the download is
recorded for the usage events (with the decisions that were open, and its file type, which the
review record lists), and the response carries the view CSP. The job page offers them only when
no decision is open, as it offers the HTML file; a direct request for a draft works as the HTML
download of a draft does.

The Word export runs pandoc as a child process (`export_docx`). At most `MAX_RUNNING` run at
once; a request that cannot get a turn within `WAIT_S` seconds is told to try again. If pandoc
is not installed the route is a 503 and the job page does not show the button.
"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Request
from fastapi.responses import Response

from readablestem.pipeline import export_docx, export_epub
from readablestem.web import documents, events, retention
from readablestem.web.db import session_scope
from readablestem.web.deps import PageError, UserDep, get_ctx
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import (
    content_disposition,
    current_html,
    download_filename,
    owned_job,
    require_done,
)
from readablestem.web.security import VIEW_CSP

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_RUNNING = 2
WAIT_S = 20.0
_turns = threading.BoundedSemaphore(MAX_RUNNING)

EPUB_MEDIA_TYPE = "application/epub+zip"


def _filename(title: str, job_id: str, ending: str) -> str:
    """The HTML download's file name, with this file type's ending."""
    return download_filename(title, job_id).removesuffix(".html") + ending


def _source(request: Request, job_id: str, user) -> tuple[str, str, int]:
    """(html, title, decisions still open) of the owner's finished document, up to date; the
    deletion clock restarts, as for the HTML download."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        job = current_html(ctx, session, job)
        result = job.result_json or {}
        title = documents.display_title(result.get("title"), job.filename, job.title_override)
        still_open = documents.open_decisions(result.get("fixes"))
        retention.restart_clock(job, ctx.settings)  # a download is activity (T1-8)
        return job.html or "", title, still_open


def _recorded(request: Request, user, job_id: str, still_open: int, file_type: str) -> None:
    ctx = get_ctx(request)
    events.record_download(
        ctx.factory,
        ctx.settings,
        user_id=user.id,
        job_id=job_id,
        open_decisions=still_open,
        file_type=file_type,
    )


@router.get("/jobs/{job_id}/download.docx")
def download_docx(request: Request, job_id: str, user: UserDep) -> Response:
    """The document as a Word file with native equations (pipeline/export_docx.py)."""
    if not export_docx.available():
        raise PageError(
            503,
            "Word files are not available here",
            "This server cannot make a Word file. The HTML file is still available from the "
            "job page.",
            context={"job_id": job_id},
        )
    html, title, still_open = _source(request, job_id, user)
    if not _turns.acquire(timeout=WAIT_S):
        raise PageError(
            503,
            "Busy right now",
            "Other Word files are being made. Wait a minute and try again.",
            context={"job_id": job_id},
            headers={"Retry-After": "60"},
        )
    try:
        export = export_docx.export_docx(html, title=title)
    except export_docx.DocxExportError as exc:
        log_event(logger, "docx_export_failed", code=exc.code)
        raise PageError(
            409,
            "This document cannot be made as a Word file",
            "The Word version could not be made from this document. The HTML file is still "
            "available from the job page.",
            context={"job_id": job_id},
        ) from None
    finally:
        _turns.release()
    _recorded(request, user, job_id, still_open, "docx")
    return Response(
        export.data,
        media_type=export_docx.DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(_filename(title, job_id, ".docx")),
            "Content-Security-Policy": VIEW_CSP,
        },
    )


@router.get("/jobs/{job_id}/download.epub")
def download_epub(request: Request, job_id: str, user: UserDep) -> Response:
    """The document as an EPUB 3 publication with MathML (pipeline/export_epub.py)."""
    html, title, still_open = _source(request, job_id, user)
    try:
        data = export_epub.build_epub(export_epub.export_epub(html, title=title))
    except export_epub.CanvasExportError:  # EpubExportError is one: a picture it cannot hold
        log_event(logger, "epub_export_failed", code="image_type")
        raise PageError(
            409,
            "This document cannot be made as an EPUB",
            "It has a picture in a form the EPUB cannot hold. The HTML file is still available "
            "from the job page.",
            context={"job_id": job_id},
        ) from None
    _recorded(request, user, job_id, still_open, "epub")
    return Response(
        data,
        media_type=EPUB_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(_filename(title, job_id, ".epub")),
            "Content-Security-Policy": VIEW_CSP,
        },
    )
