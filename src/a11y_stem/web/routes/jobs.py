"""Upload, job status, results (download, sandboxed view, crops) and deletion.

Every /jobs/* route loads the job through `owned_job`, which answers 404 for a job that
does not exist *or* belongs to someone else, so job ids cannot be probed.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from a11y_stem.web.database import utc_today
from a11y_stem.web.db import Blob, Job, JobStatus, session_scope, utcnow
from a11y_stem.web.deps import (
    CSRF_FIELD,
    ELEMENT_ID,
    JOB_ID,
    AppContext,
    CsrfFailed,
    CurrentUser,
    PageError,
    UserDep,
    csrf_valid,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.quota import pages_left, try_consume_pages
from a11y_stem.web.security import VIEW_CSP
from a11y_stem.web.uploads import (
    UploadRejected,
    inspect_pdf_isolated,
    max_upload_bytes,
    read_upload_form,
)
from a11y_stem.web.views import JobView, job_view

logger = logging.getLogger(__name__)
router = APIRouter()

NOT_FOUND = ("Page not found", "There is nothing at this address, or it is not yours to see.")
# Opt-in reload interval for the job page without JavaScript: long enough to hear the
# status before the page starts over.
AUTO_RELOAD_S = 15


def upload_error_message(code: str, ctx: AppContext) -> str:
    s = ctx.settings
    return {
        "missing_file": "Choose a PDF file to upload.",
        "bad_form": "The upload did not arrive complete. Please try again.",
        "empty": "The file is empty. Choose a PDF that has content.",
        "not_pdf": "This file is not a PDF. Choose a PDF file.",
        "too_large": f"The file is larger than {s.max_upload_mb} MB. Split it into smaller "
        "PDFs and upload each one.",
        "encrypted": "This PDF is password-protected. Save a copy without a password (for "
        "example with Print to PDF) and upload that.",
        "unreadable": "This PDF could not be opened. It may be damaged; try saving it again "
        "from the program that made it.",
        "no_pages": "This PDF has no pages.",
        "too_many_pages": f"This PDF has more than {s.max_pages_per_job} pages. Split it into "
        f"parts of up to {s.max_pages_per_job} pages.",
    }.get(code, "This file cannot be converted.")


def owned_job(session: Session, job_id: str, user: CurrentUser, *, lock: bool = False) -> Job:
    if not JOB_ID.match(job_id):
        raise PageError(404, *NOT_FOUND)
    job = session.get(Job, job_id, with_for_update=lock or None)
    if job is None or job.user_id != user.id:
        raise PageError(404, *NOT_FOUND)
    return job


def require_done(job: Job) -> None:
    if job.status != JobStatus.DONE or job.html is None:
        raise PageError(
            409,
            "Not ready yet",
            "This document has not finished converting, so there is nothing to show yet.",
            context={"job_id": job.id},
        )


# --------------------------------------------------------------------------- /app


def app_context(ctx: AppContext, user: CurrentUser) -> dict[str, Any]:
    with session_scope(ctx.factory) as session:
        jobs = session.scalars(
            select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())
        ).all()
        views = [job_view(session, job) for job in jobs]
        left = pages_left(
            session,
            user.id,
            quota=ctx.settings.per_user_daily_page_quota,
            day=utc_today(),
        )
    return {
        "jobs": views,
        "pages_left": left,
        "spend_closed": ctx.guard.cap_reached(ctx.min_headroom_usd),
        "active": "app",
    }


@router.get("/app")
def app_home(request: Request, user: UserDep) -> Response:
    return render(request, "app.html", app_context(get_ctx(request), user))


def _upload_error_page(request: Request, user: CurrentUser, code: str, status: int) -> Response:
    ctx = get_ctx(request)
    log_event(logger, "upload_rejected", code=code)
    errors = [{"field_id": "pdf", "message": upload_error_message(code, ctx)}]
    context = app_context(ctx, user) | {"errors": errors, "field_errors": {"pdf": errors[0]}}
    return render(request, "app.html", context, status_code=status)


@router.post("/jobs")
async def create_job(request: Request, user: UserDep) -> Response:
    ctx = get_ctx(request)
    if await run_in_threadpool(ctx.guard.cap_reached, ctx.min_headroom_usd):
        log_event(logger, "upload_rejected", code="spend_cap")
        raise PageError(
            503,
            "Conversions are paused for today",
            "Today's budget for conversions has been used up. New uploads open again "
            "after midnight UTC. Nothing was uploaded.",
            headers={"Retry-After": "3600"},
        )
    try:
        form = await read_upload_form(
            request, max_bytes=max_upload_bytes(ctx.settings.max_upload_mb)
        )
    except UploadRejected as exc:
        return await run_in_threadpool(_upload_error_page, request, user, exc.code, exc.status_code)
    if not csrf_valid(request, form.fields.get(CSRF_FIELD)):
        raise CsrfFailed()
    if form.data is None:
        return await run_in_threadpool(_upload_error_page, request, user, "missing_file", 400)
    try:
        pages = await run_in_threadpool(
            lambda: inspect_pdf_isolated(form.data or b"", max_pages=ctx.settings.max_pages_per_job)
        )
    except UploadRejected as exc:
        return await run_in_threadpool(_upload_error_page, request, user, exc.code, exc.status_code)
    keep = form.fields.get("keep") == "on"
    job_id = await run_in_threadpool(
        _store_upload, ctx, user, form.filename, form.data, pages, keep
    )
    log_event(logger, "job_created", job_id=job_id, pages=pages, opt_in=keep)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


def _store_upload(
    ctx: AppContext, user: CurrentUser, filename: str, data: bytes, pages: int, keep: bool
) -> str:
    """Count the pages against today's quota and queue the job, in one transaction."""
    s = ctx.settings
    now = utcnow()
    keep_for = (
        timedelta(days=s.opt_in_retention_days) if keep else timedelta(hours=s.retention_hours)
    )
    with session_scope(ctx.factory) as session:
        if not try_consume_pages(
            session, user.id, pages, quota=s.per_user_daily_page_quota, day=utc_today(now)
        ):
            left = pages_left(
                session, user.id, quota=s.per_user_daily_page_quota, day=utc_today(now)
            )
            log_event(logger, "upload_rejected", code="page_quota")
            raise PageError(
                429,
                "Daily page limit reached",
                f"You can convert up to {s.per_user_daily_page_quota} pages a day. This PDF has "
                f"{pages} pages and you have {left} left today. The limit resets at midnight "
                "UTC. Nothing was uploaded.",
                headers={"Retry-After": "3600"},
            )
        job = Job(
            user_id=user.id,
            status=JobStatus.QUEUED,
            filename=filename,
            page_count=pages,
            opt_in_retention=keep,
            retain_until=now + keep_for,
            created_at=now,
        )
        session.add(job)
        session.flush()
        session.add(Blob(job_id=job.id, kind="upload", media_type="application/pdf", content=data))
        return job.id


# --------------------------------------------------------------------------- status


def load_job_view(request: Request, job_id: str, user: CurrentUser) -> JobView:
    with session_scope(get_ctx(request).factory) as session:
        return job_view(session, owned_job(session, job_id, user), with_summary=True)


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str, user: UserDep, auto: str = "") -> Response:
    """Status page. Without JavaScript it reloads itself only if the user opted in
    (`?auto=1`, offered in a <noscript> link); htmx polls otherwise."""
    job = load_job_view(request, job_id, user)
    context = {"job": job, "active": "app", "auto_reload": auto == "1"}
    return render(request, "job.html", context | {"auto_reload_s": AUTO_RELOAD_S})


@router.get("/jobs/{job_id}/status")
def job_status(request: Request, job_id: str, user: UserDep, v: str = "") -> Response:
    """Status fragment polled by htmx. 204 (no swap) while nothing changed; once the job
    finishes, HX-Refresh reloads the page so the results appear."""
    job = load_job_view(request, job_id, user)
    if v and v == job.version:
        return Response(status_code=204)
    headers = {"HX-Refresh": "true"} if job.finished and request.headers.get("HX-Request") else None
    return render(request, "_job_status.html", {"job": job}, headers=headers)


# --------------------------------------------------------------------------- results


def download_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem or 'document'}-accessible.html"


def content_disposition(filename: str) -> str:
    """attachment with an ASCII fallback plus the RFC 5987 UTF-8 name."""
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else "_" for ch in filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/jobs/{job_id}/download")
def download(request: Request, job_id: str, user: UserDep) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        html, name = job.html or "", download_filename(job.filename)
    return Response(
        html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(name),
            "Content-Security-Policy": VIEW_CSP,
        },
    )


@router.get("/jobs/{job_id}/view")
def view(request: Request, job_id: str, user: UserDep) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        html = job.html or ""
    return Response(
        html, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": VIEW_CSP}
    )


@router.get("/jobs/{job_id}/crops/{element_id}.png")
def crop(request: Request, job_id: str, element_id: str, user: UserDep) -> Response:
    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        png = session.scalar(
            select(Blob.content).where(
                Blob.job_id == job.id, Blob.kind == "crop", Blob.element_id == element_id
            )
        )
    if png is None:
        raise PageError(404, *NOT_FOUND)
    return Response(png, media_type="image/png")


@router.post("/jobs/{job_id}/delete", dependencies=[Depends(require_csrf)])
def delete_job(request: Request, job_id: str, user: UserDep) -> Response:
    from a11y_stem.ops.retention import delete_jobs

    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        # The one supported deletion path: counts the job's categories before deleting.
        delete_jobs(session, [job.id])
    log_event(logger, "job_deleted", job_id=job_id, reason="user")
    flash(request, "Your document and everything made from it have been deleted.")
    return RedirectResponse("/app", status_code=303)
