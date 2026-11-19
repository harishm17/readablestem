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
from datetime import datetime
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup

from readablestem.web import access, events, pending, poll, ranges
from readablestem.web import storage as stored_bytes
from readablestem.web.database import utc_today
from readablestem.web.dates import time_tag
from readablestem.web.db import PendingUpload, session_scope, utcnow
from readablestem.web.deps import (
    JOB_ID,
    AppContext,
    CurrentUser,
    PageError,
    UserDep,
    conversions_closed,
    enforce,
    flash,
    get_ctx,
    give_back,
    limited,
    render,
    require_csrf,
)
from readablestem.web.forms import submit_once
from readablestem.web.intake import (
    QuotaRefused,
    TooManyJobs,
    store_job,
)
from readablestem.web.logsafe import log_event
from readablestem.web.quota import next_day_start, pages_left, retry_after
from readablestem.web.uploads import (
    ExtractFailed,
    UploadRejected,
    extract_part_isolated,
    inspect_pdf_detail,
    inspect_pdf_isolated,
    outline_starts_isolated,
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
    fragment: bool = False,
) -> dict[str, Any]:
    """What the chooser page shows. `fragment`: only the preview is wanted (it is fetched
    while the person types), so the suggestion, which reads the file's outline in a child
    process, is not made."""
    s = ctx.settings
    now = utcnow()
    from readablestem.pipeline.page_type import ImagePageCount
    from readablestem.web.routes.jobs import Placed, scans_note

    with session_scope(ctx.factory) as session:
        row = owned_pending(session, upload_id, user)
        left = pages_left(
            session, user.id, quota=access.page_quota(s, user.access_tier), day=utc_today(now)
        )
        session.expunge(row)
    done = {p for p in (row.done_pages or []) if isinstance(p, int)}
    remaining = row.pages - len(done)
    chapters = [] if fragment else outline_starts_isolated(bytes(row.content))
    suggested = (
        []
        if fragment
        else ranges.suggest_parts(
            row.pages, s.max_pages_per_job, done, left=left, chapters=chapters
        )
    )
    left_out_pages = ranges.left_out(row.pages, done, suggested)
    heavy = row.size_bytes > s.max_upload_mb * 1024 * 1024
    shown_text = ranges.suggestion_text(suggested) if typed is None else typed
    rows = ranges.preview(shown_text, total=row.pages, limit=s.max_pages_per_job, taken=done)
    # The scanned and handwritten pages the upload probe counted, said as everywhere else
    # (`scans_note`). A file within the limits with most pages of that kind waits for a choice
    # about them before anything is converted (WS7.8); a larger one says it beside its parts.
    note = scans_note(s, Placed(row.filename, row.pages, scans=row.scans_json))
    counted = ImagePageCount(**row.scans_json) if row.scans_json else ImagePageCount()
    choice = bool(
        note and not done and row.pages <= s.max_pages_per_job and counted.mostly(row.pages)
    )
    context: dict[str, Any] = {
        "scans_note": note,
        "scans_choice": choice,
        "preview_rows": rows,
        "preview_summary": preview_summary(rows, left, next_day_start(now)),
        "pending": row,
        "size_text": pending.size_text(row.size_bytes),
        "examples": ranges.examples(s.max_pages_per_job, row.pages),
        "max_parts": ranges.MAX_PARTS,
        "left_out_label": ranges.label(left_out_pages) if left_out_pages else "",
        "left_out_count": len(left_out_pages),
        "limit": s.max_pages_per_job,
        "remaining": remaining,
        "done_label": ranges.label(sorted(done)) if done else "",
        "done_count": len(done),
        "pages_left": left,
        "page_quota": access.page_quota(s, user.access_tier),
        "next_reset": next_day_start(now),
        "typed_parts": shown_text,
        "suggestion_lines": len(suggested),
        "shrink": heavy if shrink is None else shrink,
        "heavy": heavy,
        "shrink_from": SHRINK_FROM_DPI,
        "shrink_to": SHRINK_TO_DPI,
        "started": started or [],
    }
    if errors:
        context |= {"errors": errors, "field_errors": {"parts": errors[0]}}
    return context


def preview_summary(rows: list[ranges.PreviewRow], left: int | None, resets_at: datetime) -> Markup:
    """The preview in whole sentences, from `ranges.schedule`: which parts start now and which
    can start after the day's pages reset ("Part 1 (pages 1 to 80) starts now. Parts 2 and 3
    can start after Nov 19, 2026, 00:00 UTC, when your pages reset."). The parts that wait are
    not started by the page: `_start_parts` stops at the first part today's pages cannot start
    and the file stays, so the person sends them again after the reset."""
    if not rows:
        return Markup("No parts yet.")
    plan = ranges.schedule(rows, left)
    if plan.bad:
        return Markup("Fix {} before converting.").format(
            "the part marked below" if plan.bad == 1 else f"the {plan.bad} parts marked below"
        )
    sentences = []
    if plan.now:
        pages = sum(r.count for r in plan.now)
        if len(plan.now) == 1:
            row = plan.now[0]
            what = f"Part {row.number} ({'page' if row.count == 1 else 'pages'} "
            sentences.append(f"{what}{ranges.spoken(row.label)}) starts now.")
        elif not plan.later:
            sentences.append(f"All {len(plan.now)} parts ({pages} pages) start now.")
        else:
            sentences.append(f"{_numbers(plan.now)} ({pages} pages) start now.")
    elif plan.later:
        sentences.append("No part starts today.")
    text = Markup(" ".join(sentences))
    if plan.later:
        text = Markup("{} {} {} after {}, when your pages reset.").format(
            text, _numbers(plan.later), "can start", time_tag(resets_at)
        )
    return Markup(text.strip())


def _numbers(rows: tuple[ranges.PreviewRow, ...]) -> str:
    """ "Part 3", "Parts 2 and 3", "Parts 2 to 5": the parts a sentence is about."""
    first, last = rows[0].number, rows[-1].number
    if first == last:
        return f"Part {first}"
    return f"Parts {first} and {last}" if len(rows) == 2 else f"Parts {first} to {last}"


@router.get("/uploads/{upload_id}")
def choose(request: Request, upload_id: str, user: UserDep) -> Response:
    return render(request, "upload_parts.html", chooser_context(get_ctx(request), user, upload_id))


@router.get("/uploads/{upload_id}/preview")
def preview(request: Request, upload_id: str, user: UserDep, parts: str = "") -> Response:
    """The parts as typed, with their page counts: what the chooser's preview swaps in while
    the person types (htmx). Only page numbers are sent; nothing is stored or logged."""
    context = chooser_context(get_ctx(request), user, upload_id, typed=parts[:2000], fragment=True)
    if redirect := poll.fragment_or_redirect(
        request, f"/uploads/{upload_id}"
    ):  # after the owner check
        return redirect
    return render(request, "_parts_preview.html", context | {"oob": True})


# --------------------------------------------------------------------------- converting parts


@dataclass
class Outcome:
    """What `_start_parts` did."""

    started: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    status: int = 200
    retry_after: str = ""


def _start_parts(
    ctx: AppContext,
    user: CurrentUser,
    upload_id: str,
    text: str,
    shrink: bool,
    enforce_each,
    *,
    whole: bool = False,
    images: bool | None = None,
) -> Outcome:
    """Cut out, check and queue the parts typed in `text`, in order, until one cannot be.

    `whole`: the choice about scanned and handwritten pages (WS7.8): the file as it is, one
    document with its own name, and `images` says whether those pages are read."""
    s = ctx.settings
    out = Outcome()
    with session_scope(ctx.factory) as session:
        row = owned_pending(session, upload_id, user)
        data, name, total = bytes(row.content), row.filename, row.pages
        done = {p for p in (row.done_pages or []) if isinstance(p, int)}
        course, exam, mode = row.course_id, bool(row.exam_mode), row.retention_mode
        chosen_language = row.language
        asked_images = bool(row.image_pages) and s.feature_image_pages
        office = dict(row.office_json) if row.office_json else None
    if whole:
        text, shrink = f"1-{total}", False
        asked_images = bool(images) and s.feature_image_pages
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
            cut = data if whole else extract_part_isolated(data, list(part.pages), shrink=shrink)
            if len(cut) > limit_bytes:
                out.error = (
                    f"{named} is {pending.size_text(len(cut))}, over the {s.max_upload_mb} MB "
                    "limit for one document. Check “Shrink large pictures first”, or split it into "
                    "smaller parts."
                )
                out.status = 413
                break
            if asked_images:  # the choice made with the upload, for the parts that have such pages
                pages, found = inspect_pdf_detail(
                    cut, max_pages=s.max_pages_per_job, allow_image_pages=True
                )
                may_have = found.possible
            else:
                pages, may_have = inspect_pdf_isolated(cut, max_pages=s.max_pages_per_job), False
            filename = name if whole else part_filename(name, part.label)
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
                language=chosen_language,
                image_pages=asked_images and may_have,
                part_group=None if whole else upload_id,
                part_label=None if whole else part.label,
                office=office,
            )
        except ExtractFailed:
            out.error = (
                f"{named} could not be cut out of the file. It may be damaged. Nothing was "
                "converted for it."
            )
            out.status = 422
            break
        except UploadRejected as exc:
            from readablestem.web.routes.jobs import upload_error_message

            out.error = upload_error_message(exc.code, ctx, named, exc.pages, tier=user.access_tier)
            out.status = exc.status_code
            break
        except TooManyJobs as exc:
            out.error = (
                f"You already have {access.files_per_upload(user.access_tier)} documents waiting "
                "or being converted."
                if exc.code == "too_many_active"
                else "You are keeping the most documents allowed."
            ) + (
                f" {named} was not started. Wait for one to finish or delete one you no longer "
                "need, then choose it again here: this file is kept."
            )
            out.status, out.retry_after = 429, "60"
            break
        except stored_bytes.StorageRefused as exc:
            out.error = (
                f"You are storing {exc.used_mb} MB of the {exc.limit_mb} MB you can keep, and "
                f"{named} would go over it. It was not started. Delete documents you no longer "
                "need (My documents, then Delete), then choose it again: this file is kept."
            )
            out.status, out.retry_after = 413, ""
            break
        except stored_bytes.StorageFull:
            wait = retry_after(stored_bytes.FULL_WAIT_S)
            out.error = (
                "The service holds as much uploaded material as it can for now, so "
                f"{named} was not started. Room frees up through the day: choose it again "
                f"{wait.words}. This file is kept."
            )
            out.status, out.retry_after = 503, wait.header
            break
        except QuotaRefused as exc:
            left = exc.left
            wait = retry_after()
            limit = access.page_quota(s, user.access_tier)
            of_today = (
                f"the {limit} pages a new account can convert today"
                if access.is_open(user.access_tier)
                else f"today's {limit} pages"
            )
            out.error = (
                f"{named} has {exc.pages} {'page' if exc.pages == 1 else 'pages'}, but you have "
                f"{left} of {of_today} left. It was not started. "
                f"The count starts again {wait.words}; this file is kept so you can choose it "
                "then."
            )
            out.status, out.retry_after = 429, wait.header
            break
        ctx.wake_worker()
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
@submit_once("upload.parts")
async def convert_parts(request: Request, upload_id: str, user: UserDep) -> Response:
    ctx = get_ctx(request)
    counted, outcome = [0], Outcome()
    form = await request.form()
    text = str(form.get("parts") or "")
    shrink = form.get("shrink") == "on"
    whole = form.get("whole") == "1"  # the choice about scanned pages: the file as it is
    images = form.get("images") == "read"
    if (closed := await run_in_threadpool(conversions_closed, ctx, user)) is not None:
        from readablestem.web.routes.jobs import conversions_paused

        log_event(logger, "upload_rejected", code="spend_cap" if closed == "daily" else "open_pool")
        raise conversions_paused(closed, kept=True)
    if (refused := ctx.uploads.enter(user.id)) is not None:
        from readablestem.web.routes.jobs import upload_busy

        log_event(logger, "upload_rejected", code=f"upload_{refused}")
        raise upload_busy(refused)
    try:
        enforce(request, "upload_user", user.id)
        counted[0] = 1

        def enforce_each(count: int) -> None:
            for _ in range(max(0, count - 1)):
                enforce(request, "upload_user", user.id)
                counted[0] += 1

        outcome = await run_in_threadpool(
            partial(
                _start_parts, ctx, user, upload_id, text, shrink, enforce_each,
                whole=whole, images=images,
            )
        )  # fmt: skip
    finally:
        ctx.uploads.leave(user.id)
        # The per-person rule counts accepted parts only: a part not started gives its count back.
        give_back(request, "upload_user", user.id, counted[0] - len(outcome.started))
    if not outcome.error and outcome.started:
        if len(outcome.started) == 1:
            return RedirectResponse(f"/jobs/{outcome.started[0]['id']}", status_code=303)
        flash(
            request,
            f"{len(outcome.started)} parts are converting. Each part is its own document.",
            undo=False,
            kind="success",
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
@submit_once("upload.discard")
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
    flash(request, "The file was discarded.", undo=False, kind="success")
    return RedirectResponse("/app", status_code=303)


def _confirm_page(request: Request, upload_id: str, user: CurrentUser) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        row = owned_pending(session, upload_id, user)
        name = row.filename
    return render(request, "upload_delete_confirm.html", {"upload_id": upload_id, "filename": name})
