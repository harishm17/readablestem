"""Large files: choose which pages to convert, and convert them in parts (T3-3).

A PDF over the page or size limit is kept as a `PendingUpload` (`web.pending`) instead of
being turned away. This page shows it, suggests parts that each fit, and starts the ones the
person chooses: each part is cut out of the file in a child process (`web.pdf_probe
extract`, which can also shrink heavy pictures), checked like any upload, and queued as a
job of its own. Everything an upload is held to still holds:

- the part is probed (`uploads.inspect_pdf_isolated`) and must fit the size limit;
- the pages of each part are counted against the daily quota, in the same transaction as its
  job (`intake.store_job`); a part that does not fit today is not started, and the file
  stays so the rest can be chosen tomorrow;
- the caps on documents waiting or kept (`intake`) and the rate limits apply, and the whole
  request counts as one upload in progress (`UploadGate`), so a person cannot have several
  files being cut up at once;
- the owner check is the same as for a job: someone else's file is a 404.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response

from a11y_stem.web import events, pending, ranges
from a11y_stem.web.database import utc_today
from a11y_stem.web.db import PendingUpload, session_scope, utcnow
from a11y_stem.web.deps import (
    JOB_ID,
    AppContext,
    CurrentUser,
    PageError,
    UserDep,
    enforce,
    flash,
    get_ctx,
    limited,
    render,
    require_csrf,
)
from a11y_stem.web.intake import (
    MAX_ACTIVE_JOBS_PER_USER,
    QuotaRefused,
    TooManyJobs,
    store_job,
)
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.quota import next_day_start, pages_left
from a11y_stem.web.uploads import (
    ExtractFailed,
    UploadRejected,
    extract_part_isolated,
    inspect_pdf_isolated,
    safe_filename,
)

logger = logging.getLogger(__name__)
router = APIRouter()

NOT_FOUND = (
    "Page not found",
    "There is no file at this address. It may have been deleted, or the link may be incomplete.",
)
# Pictures sharper than this many dots per inch are saved at the second number when the
# person ticks "Shrink large pictures first" (web.pdf_probe.SHRINK_*).
SHRINK_FROM_DPI = 200
SHRINK_TO_DPI = 150


def owned_pending(
    session, upload_id: str, user: CurrentUser, *, lock: bool = False
) -> PendingUpload:
    """The person's waiting file, or a 404 page (also for someone else's, and for one past
    its deletion time, so the retention promise does not wait for the purge)."""
    if not JOB_ID.match(upload_id):
        raise PageError(404, *NOT_FOUND)
    row = session.get(PendingUpload, upload_id, with_for_update=lock or None)
    if row is None or row.user_id != user.id or pending.is_expired(row):
        raise PageError(404, *NOT_FOUND)
    return row


def part_filename(name: str, label: str) -> str:
    """ "chapter2 (pages 31-59).pdf" for a part of chapter2.pdf."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not ext or len(ext) > 10:
        stem, ext = name, "pdf"
    if ext.lower() in {"docx", "pptx"}:  # a part of a Word file is a PDF
        ext = "pdf"
    tag = f" (pages {label})"
    room = max(20, 200 - len(tag) - len(ext) - 1)
    return safe_filename(f"{stem[:room]}{tag}.{ext}")


# --------------------------------------------------------------------------- the page


def chooser_context(
    ctx: AppContext,
    user: CurrentUser,
    upload_id: str,
    *,
    typed: str | None = None,
    shrink: bool | None = None,
    errors: list[dict[str, str]] | None = None,
    started: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    s = ctx.settings
    now = utcnow()
    with session_scope(ctx.factory) as session:
        row = owned_pending(session, upload_id, user)
        left = pages_left(session, user.id, quota=s.per_user_daily_page_quota, day=utc_today(now))
        session.expunge(row)
    done = {p for p in (row.done_pages or []) if isinstance(p, int)}
    remaining = row.pages - len(done)
    suggested = ranges.suggest_parts(row.pages, s.max_pages_per_job, done)
    heavy = row.size_bytes > s.max_upload_mb * 1024 * 1024
    context: dict[str, Any] = {
        "pending": row,
        "size_mb": f"{row.size_bytes / (1024 * 1024):.1f}",
        "limit": s.max_pages_per_job,
        "remaining": remaining,
        "done_label": ranges.label(sorted(done)) if done else "",
        "done_count": len(done),
        "pages_left": left,
        "next_reset": next_day_start(now),
        "typed_parts": ranges.suggestion_text(suggested) if typed is None else typed,
        "suggestion_lines": len(suggested),
        "shrink": heavy if shrink is None else shrink,
        "heavy": heavy,
        "shrink_from": SHRINK_FROM_DPI,
        "shrink_to": SHRINK_TO_DPI,
        "started": started or [],
        "active": "app",
    }
    if errors:
        context |= {"errors": errors, "field_errors": {"parts": errors[0]}}
    return context


@router.get("/uploads/{upload_id}")
def choose(request: Request, upload_id: str, user: UserDep) -> Response:
    return render(request, "upload_parts.html", chooser_context(get_ctx(request), user, upload_id))


# --------------------------------------------------------------------------- converting parts


@dataclass
class Outcome:
    """What `_start_parts` did."""

    started: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    status: int = 200
    retry_after: str = ""


def _start_parts(
    ctx: AppContext, user: CurrentUser, upload_id: str, text: str, shrink: bool, enforce_each
) -> Outcome:
    """Cut out, check and queue the parts typed in `text`, in order, until one cannot be."""
    s = ctx.settings
    out = Outcome()
    with session_scope(ctx.factory) as session:
        row = owned_pending(session, upload_id, user)
        data, name, total = bytes(row.content), row.filename, row.pages
        done = {p for p in (row.done_pages or []) if isinstance(p, int)}
        course, exam, mode = row.course_id, bool(row.exam_mode), row.retention_mode
    try:
        parts = ranges.parse_parts(text, total=total, limit=s.max_pages_per_job, taken=done)
    except ranges.PartsError as exc:
        out.error, out.status = exc.message, 400
        return out
    enforce_each(len(parts))  # each part is an upload for the rate limits
    limit_bytes = s.max_upload_mb * 1024 * 1024
    sent: set[int] = set()
    for number, part in enumerate(parts, start=1):
        named = f"Part {number} (pages {part.label})" if len(parts) > 1 else f"Pages {part.label}"
        try:
            cut = extract_part_isolated(data, list(part.pages), shrink=shrink)
            if len(cut) > limit_bytes:
                out.error = (
                    f"{named} is {len(cut) / (1024 * 1024):.1f} MB, over the {s.max_upload_mb} MB "
                    "limit for one document. Tick “Shrink large pictures first”, or split it into "
                    "smaller parts."
                )
                out.status = 413
                break
            pages = inspect_pdf_isolated(cut, max_pages=s.max_pages_per_job)
            filename = part_filename(name, part.label)
            job_id = store_job(
                ctx.factory,
                s,
                user.id,
                filename,
                cut,
                pages,
                mode,
                course_id=course,
                exam=exam,
                part_group=upload_id,
                part_label=part.label,
            )
        except ExtractFailed:
            out.error = (
                f"{named} could not be cut out of the file. It may be damaged. Nothing was "
                "converted for it."
            )
            out.status = 422
            break
        except UploadRejected as exc:
            from a11y_stem.web.routes.jobs import upload_error_message

            out.error = upload_error_message(exc.code, ctx, named, exc.pages)
            out.status = exc.status_code
            break
        except TooManyJobs as exc:
            out.error = (
                f"You already have {MAX_ACTIVE_JOBS_PER_USER} documents waiting or being converted."
                if exc.code == "too_many_active"
                else "You are keeping the most documents allowed."
            ) + (
                f" {named} was not started. Wait for one to finish or delete one you no longer "
                "need, then choose it again here: this file is kept."
            )
            out.status, out.retry_after = 429, "60"
            break
        except QuotaRefused as exc:
            left = exc.left
            out.error = (
                f"{named} has {exc.pages} {'page' if exc.pages == 1 else 'pages'}, but you have "
                f"{left} of today's {s.per_user_daily_page_quota} pages left. It was not started. "
                "The count starts again at midnight UTC; this file is kept so you can choose it "
                "then."
            )
            out.status, out.retry_after = 429, "3600"
            break
        out.started.append({"id": job_id, "label": part.label})
        sent.update(part.pages)
        events.record_upload(ctx.factory, s, user_id=user.id, job_id=job_id, data=cut, pages=pages)
        log_event(logger, "part_started", job_id=job_id, pages=pages, shrink=shrink)
    if sent:
        _remember(ctx, upload_id, sent)
    return out


def _remember(ctx: AppContext, upload_id: str, sent: set[int]) -> None:
    """Record the pages sent, and delete the file once every page has been."""
    with session_scope(ctx.factory) as session:
        row = session.get(PendingUpload, upload_id, with_for_update=True)
        if row is None:  # discarded or purged meanwhile: the jobs stand on their own
            return
        done = sorted({*(row.done_pages or []), *sent})
        row.done_pages = done
        if len(done) >= row.pages:
            session.delete(row)


@router.post("/uploads/{upload_id}", dependencies=[limited("upload"), Depends(require_csrf)])
async def convert_parts(request: Request, upload_id: str, user: UserDep) -> Response:
    ctx = get_ctx(request)
    enforce(request, "upload_user", user.id)
    form = await request.form()
    text = str(form.get("parts") or "")
    shrink = form.get("shrink") == "on"
    if await run_in_threadpool(ctx.guard.cap_reached, ctx.min_headroom_usd):
        log_event(logger, "upload_rejected", code="spend_cap")
        raise PageError(
            503,
            "Conversions are paused for today",
            "Today's budget for conversions has been used up. New uploads open again "
            "after midnight UTC. Nothing was started; this file is kept.",
            headers={"Retry-After": "3600"},
        )
    if (refused := ctx.uploads.enter(user.id)) is not None:
        from a11y_stem.web.routes.jobs import UPLOAD_BUSY

        log_event(logger, "upload_rejected", code=f"upload_{refused}")
        raise PageError(*UPLOAD_BUSY[refused], headers={"Retry-After": "10"})
    try:

        def enforce_each(count: int) -> None:
            for _ in range(max(0, count - 1)):
                enforce(request, "upload_user", user.id)

        outcome = await run_in_threadpool(
            _start_parts, ctx, user, upload_id, text, shrink, enforce_each
        )
    finally:
        ctx.uploads.leave(user.id)
    if not outcome.error and outcome.started:
        if len(outcome.started) == 1:
            return RedirectResponse(f"/jobs/{outcome.started[0]['id']}", status_code=303)
        flash(
            request,
            f"{len(outcome.started)} parts are converting. Each part is its own document.",
            undo=False,
        )
        return RedirectResponse("/app", status_code=303)
    errors = [{"field_id": "parts", "message": outcome.error}]
    context = await run_in_threadpool(
        lambda: chooser_context(
            ctx, user, upload_id, typed=text, shrink=shrink, errors=errors, started=outcome.started
        )
    )
    headers = {"Retry-After": outcome.retry_after} if outcome.retry_after else None
    return render(
        request, "upload_parts.html", context, status_code=outcome.status, headers=headers
    )


# --------------------------------------------------------------------------- discarding


@router.post("/uploads/{upload_id}/delete", dependencies=[Depends(require_csrf)])
async def discard(request: Request, upload_id: str, user: UserDep) -> Response:
    """Discard a waiting file. Only a POST with `confirm=yes` deletes it; any other POST
    (the "Discard" button) gets the confirmation page first (WCAG 3.3.4)."""
    form = await request.form()
    ctx = get_ctx(request)
    if form.get("confirm") != "yes":
        return await run_in_threadpool(_confirm_page, request, upload_id, user)

    def delete() -> None:
        with session_scope(ctx.factory) as session:
            row = owned_pending(session, upload_id, user)
            session.delete(row)

    await run_in_threadpool(delete)
    log_event(logger, "pending_discarded", reason="user")
    flash(request, "The file was discarded.", undo=False)
    return RedirectResponse("/app", status_code=303)


def _confirm_page(request: Request, upload_id: str, user: CurrentUser) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        row = owned_pending(session, upload_id, user)
        name = row.filename
    return render(request, "upload_delete_confirm.html", {"upload_id": upload_id, "filename": name})
