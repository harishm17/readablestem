"""Upload, job status, results (download, sandboxed view, crops) and deletion.

Every /jobs/* route loads the job through `owned_job`, which answers 404 for a job that
does not exist *or* belongs to someone else, so job ids cannot be probed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from a11y_stem.web.database import as_utc, utc_today
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
from a11y_stem.web.outcome import JobOutcome, job_outcome
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
        "page_too_large": "This PDF has a page larger than 1.7 metres on a side, which is too "
        "large to convert. Course documents are usually Letter or A4 size.",
        "scanned": "This PDF is made of scanned page images with no text in it, so there is "
        "nothing this tool can convert yet. Run text recognition (OCR) on it first, for "
        "example with your scanner's software or Adobe Acrobat, then upload the result.",
        "no_content": "Every page of this PDF is blank. Check that you chose the right file.",
    }.get(code, "This file cannot be converted.")


def owned_job(session: Session, job_id: str, user: CurrentUser, *, lock: bool = False) -> Job:
    """The user's job, or a 404 page. A job past its `retain_until` counts as deleted even
    before the hourly purge removes it, so the retention promise does not depend on the
    scheduler running on time."""
    if not JOB_ID.match(job_id):
        raise PageError(404, *NOT_FOUND)
    job = session.get(Job, job_id, with_for_update=lock or None)
    if job is None or job.user_id != user.id or expired(job):
        raise PageError(404, *NOT_FOUND)
    return job


def expired(job: Job, now: datetime | None = None) -> bool:
    retain_until = as_utc(job.retain_until)
    return retain_until is not None and retain_until <= (now or utcnow())


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
    spend_closed = ctx.guard.cap_reached(ctx.min_headroom_usd)
    with session_scope(ctx.factory) as session:
        jobs = session.scalars(
            select(Job)
            .where(Job.user_id == user.id, Job.retain_until > utcnow())  # see `owned_job`
            .order_by(Job.created_at.desc())
        ).all()
        views = [job_view(session, job) for job in jobs]
        # Labels only (no counts), so the stored results are not loaded for the list.
        outcomes = {
            job.id: outcome
            for job in jobs
            if (
                outcome := job_outcome(
                    status=str(job.status), error_code=job.error_code, budget_closed=spend_closed
                )
            )
            is not None
        }
        left = pages_left(
            session,
            user.id,
            quota=ctx.settings.per_user_daily_page_quota,
            day=utc_today(),
        )
    return {
        "jobs": views,
        "outcomes": outcomes,
        "pages_left": left,
        "spend_closed": spend_closed,
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
    if (refused := ctx.uploads.enter(user.id)) is not None:
        log_event(logger, "upload_rejected", code=f"upload_{refused}")
        raise PageError(*UPLOAD_BUSY[refused], headers={"Retry-After": "10"})
    try:
        return await _take_upload(request, ctx, user)
    finally:
        ctx.uploads.leave(user.id)


# Status, title and message when the upload gate (web/uploads.py) turns someone away.
UPLOAD_BUSY = {
    "busy": (
        503,
        "Too many uploads right now",
        "Several documents are being uploaded at this moment. Wait a few seconds, then "
        "go back and upload again. Nothing was uploaded.",
    ),
    "own_upload": (
        429,
        "Your other upload is still in progress",
        "You are already uploading a document. Wait until it has finished, then upload "
        "the next one. Nothing was uploaded.",
    ),
}


async def _take_upload(request: Request, ctx: AppContext, user: CurrentUser) -> Response:
    """Read, check and store one upload (inside the upload gate)."""
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


def load_job_status(
    request: Request, job_id: str, user: CurrentUser
) -> tuple[JobView, JobOutcome | None]:
    """The job's view model plus its outcome (`web.outcome`), from one read."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        view = job_view(session, job, with_summary=True)
        status, error_code, result_json = str(job.status), job.error_code, job.result_json
    budget_closed = status == JobStatus.QUEUED and ctx.guard.cap_reached(ctx.min_headroom_usd)
    outcome = job_outcome(
        status=status,
        error_code=error_code,
        result_json=result_json,
        budget_closed=budget_closed,
    )
    return view, outcome


def status_context(job: JobView, outcome: JobOutcome | None) -> dict[str, Any]:
    """What `_job_status.html` needs. `status_version` changes whenever the fragment
    would look different, outcome included (htmx polling answers 204 until then)."""
    version = f"{job.version}-{outcome.code}" if outcome is not None else job.version
    return {"job": job, "outcome": outcome, "status_version": version}


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str, user: UserDep, auto: str = "") -> Response:
    """Status page. Without JavaScript it reloads itself only if the user opted in
    (`?auto=1`, offered in a <noscript> link); htmx polls otherwise.

    `outcome` (label, message, `partial`) is in the context for the status box, which
    shows it instead of the plain status when the job is incomplete or waiting.
    """
    job, outcome = load_job_status(request, job_id, user)
    context = status_context(job, outcome) | {"auto_reload": auto == "1"}
    # No `active` nav item: this page is not "My documents" itself (aria-current="page"
    # would announce it as such).
    return render(request, "job.html", context | {"auto_reload_s": AUTO_RELOAD_S})


@router.get("/jobs/{job_id}/status")
def job_status(request: Request, job_id: str, user: UserDep, v: str = "") -> Response:
    """Status fragment polled by htmx. 204 (no swap) while nothing changed; once the job
    finishes, HX-Refresh reloads the page so the results appear."""
    job, outcome = load_job_status(request, job_id, user)
    context = status_context(job, outcome)
    if v and v == context["status_version"]:
        return Response(status_code=204)
    htmx = request.headers.get("HX-Request") == "true"
    if htmx:  # htmx sets document.title from a top-level <title> in the fragment
        label = outcome.label if outcome is not None else job.status_label
        context["page_title"] = f"{label}: {job.filename}"
    headers = {"HX-Refresh": "true"} if job.finished and htmx else None
    return render(request, "_job_status.html", context, headers=headers)


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


def _confirm_delete_page(request: Request, job_id: str, user: CurrentUser) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        filename = owned_job(session, job_id, user).filename
    context = {"job_id": job_id, "filename": filename}
    return render(request, "delete_confirm.html", context)


@router.get("/jobs/{job_id}/delete")
def confirm_delete(request: Request, job_id: str, user: UserDep) -> Response:
    """The confirmation step on its own (for a link)."""
    return _confirm_delete_page(request, job_id, user)


@router.post("/jobs/{job_id}/delete", dependencies=[Depends(require_csrf)])
async def delete_job(request: Request, job_id: str, user: UserDep) -> Response:
    """Delete only with `confirm=yes`; any other POST (the "Delete" buttons) gets the
    confirmation page first. Deleting user data cannot be undone, so it is confirmed
    (WCAG 3.3.4), and it works without JavaScript."""
    form = await request.form()
    if form.get("confirm") != "yes":
        return await run_in_threadpool(_confirm_delete_page, request, job_id, user)
    return await run_in_threadpool(_delete_now, request, job_id, user)


def _delete_now(request: Request, job_id: str, user: CurrentUser) -> Response:
    from a11y_stem.ops.retention import delete_jobs

    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        # The one supported deletion path: counts the job's categories before deleting.
        delete_jobs(session, [job.id])
    log_event(logger, "job_deleted", job_id=job_id, reason="user")
    flash(request, "Your document and everything made from it have been deleted.")
    return RedirectResponse("/app", status_code=303)
