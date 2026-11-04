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
from readablestem.web import bundle, documents, events, retention
from readablestem.web.db import session_scope
from readablestem.web.deps import PageError, UserDep, get_ctx
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import (
    content_disposition,
    current_html,
    download_filename,
    job_access,
    require_done,
)
from readablestem.web.security import VIEW_CSP
from readablestem.web.shares import VIEW

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
        job = job_access(session, job_id, user, VIEW)
        require_done(job)
        job = current_html(ctx, session, job)
        result = job.result_json or {}
        title = documents.display_title(result.get("title"), job.filename, job.title_override)
        still_open = documents.open_decisions(result.get("fixes"))
        if job.user_id == user.id:  # only the owner's use keeps a document longer (G1)
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


class Busy(Exception):
    """Every Word turn is taken and none came free within `WAIT_S`."""


def make_file(file_type: str, html: str, title: str) -> bytes:
    """The Word (`docx`) or EPUB (`epub`) file of finished HTML, in memory. Raises `Busy`,
    `export_docx.DocxExportError` or `export_epub.CanvasExportError` (a picture the EPUB
    cannot hold); the callers say it in words. Shared by the single downloads, "Download all"
    and "Combine parts" (`routes.bundle`, `routes.jobs.combine_parts`), so they cannot drift
    apart."""
    if file_type == "docx":
        if not _turns.acquire(timeout=WAIT_S):
            raise Busy
        try:
            return export_docx.export_docx(html, title=title).data
        finally:
            _turns.release()
    return export_epub.build_epub(export_epub.export_epub(html, title=title))


def media_type(file_type: str) -> str:
    return export_docx.DOCX_MEDIA_TYPE if file_type == "docx" else EPUB_MEDIA_TYPE


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
    try:
        data = make_file("docx", html, title)
    except Busy:
        raise PageError(
            503,
            "Busy right now",
            "Other Word files are being made. Wait a minute and try again.",
            context={"job_id": job_id},
            headers={"Retry-After": "60"},
        ) from None
    except export_docx.DocxExportError as exc:
        log_event(logger, "docx_export_failed", code=exc.code)
        raise PageError(
            409,
            "This document cannot be made as a Word file",
            "The Word version could not be made from this document. The HTML file is still "
            "available from the job page.",
            context={"job_id": job_id},
        ) from None
    _recorded(request, user, job_id, still_open, "docx")
    return Response(
        data,
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
        data = make_file("epub", html, title)
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


def checked_format(value: str) -> str:
    """The file type of a "Download all" or "Combine parts" request: `html` (the default),
    `docx` or `epub`. Anything else is a 400 page; Word is a 503 page where pandoc is not
    installed, as on the single download."""
    if value not in bundle.FORMATS:
        raise PageError(
            400,
            "Unknown file type",
            "Choose the HTML file, Word (.docx) or EPUB from the page that offered the link.",
        )
    if value == "docx" and not export_docx.available():
        raise PageError(
            503,
            "Word files are not available here",
            "This server cannot make a Word file. The HTML file is still available from the "
            "job page.",
        )
    return value


def busy_page() -> PageError:
    return PageError(
        503,
        "Busy right now",
        "Other Word files are being made. Wait a minute and try again.",
        headers={"Retry-After": "60"},
    )
