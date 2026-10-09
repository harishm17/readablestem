"""Upload, job status, results (download, sandboxed view, crops) and deletion.

Every /jobs/* route loads the job through `owned_job`, which answers 404 for a job that
does not exist *or* belongs to someone else, so job ids cannot be probed.

The job page (`job_page`, docs/web.md "The job page") shows an honest headline, live
progress by step, the before/after card, what the file does not handle, the finish line
and the document language (`web.job_page`). Its routes: the status fragment and the list
of steps, each polled by htmx on its own; a page that asks before a draft is downloaded
while decisions are open; and the language control's POST.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta
from functools import partial
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from a11y_stem.core.types import DocumentResult
from a11y_stem.pipeline import language as languages
from a11y_stem.pipeline import text_pass
from a11y_stem.web import documents, events, notation
from a11y_stem.web import feedback as feedback_questions
from a11y_stem.web import job_page as page_rules
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
from a11y_stem.web.quota import next_day_start, pages_left, try_consume_pages
from a11y_stem.web.security import VIEW_CSP
from a11y_stem.web.uploads import (
    UploadRejected,
    inspect_pdf_isolated,
    max_upload_bytes,
    read_upload_form,
)
from a11y_stem.web.views import JobView, job_view, last_decision

logger = logging.getLogger(__name__)
router = APIRouter()

NOT_FOUND = (
    "Page not found",
    "There is no document at this address. It may have been deleted, or the link may be "
    "incomplete.",
)
# What the owner of a deleted document sees at its old address (UX review MF10).
DELETED = ("This document has been deleted", "This document has been deleted.")
# Opt-in reload interval for the job page without JavaScript: long enough to hear the
# status before the page starts over.
AUTO_RELOAD_S = 15
# How to save a fresh copy of a PDF: the advice for files that are damaged or cut short.
FRESH_COPY = (
    "If it opens on your computer, save a fresh copy (File > Save As, or Print to PDF) "
    "and upload that."
)


def upload_error_message(
    code: str,
    ctx: AppContext,
    filename: str = "",
    pages: int | None = None,
    left: int | None = None,
) -> str:
    """What went wrong with an upload, naming the file (and its pages) when known. The
    file name is shown to its owner only, never logged. `left`: pages left today, for
    "page_quota"."""
    s = ctx.settings
    name = filename or "This file"
    limit = s.max_pages_per_job
    too_many = f"{name} has {pages} pages" if pages else f"{name} has more than {limit} pages"
    if code == "page_quota":
        return _quota_message(name, pages, left, s.per_user_daily_page_quota)
    return {
        "missing_file": "Choose a PDF file to upload.",
        "bad_form": "The upload did not arrive complete. Please try again.",
        "empty": f"{name} is empty. Choose a PDF that has content.",
        "not_pdf": f"{name} is not a PDF. Choose a PDF file.",
        "too_large": f"{name} is larger than {s.max_upload_mb} MB. Split it into smaller "
        "PDFs and upload each one.",
        "encrypted": f"{name} is password-protected. Save a copy without a password (for "
        "example with Print to PDF) and upload that.",
        "unreadable": f"{name} could not be opened. It may be damaged. {FRESH_COPY}",
        "damaged": f"{name} looks damaged or incomplete: it may not have finished "
        f"downloading. {FRESH_COPY}",
        "no_pages": f"{name} has no pages. Check that you chose the right file.",
        "too_many_pages": f"{too_many}; the limit is {limit} per upload. Split it into parts "
        f"of up to {limit} pages (in Acrobat: Organize Pages, then Split) and upload each "
        "part.",
        "page_too_large": f"{name} has a page larger than 1.7 metres on a side, which is too "
        "large to convert. Course documents are usually Letter or A4 size.",
        "scanned": f"{name} is made of scanned page images with no text in it, so there is "
        "nothing this tool can convert yet. Run text recognition (OCR) on it first, for "
        "example with your scanner's software or Adobe Acrobat, then upload the result.",
        "no_content": f"Every page of {filename or 'this file'} is blank. Check that you "
        "chose the right file.",
    }.get(code, f"{name} cannot be converted.")


def _quota_message(name: str, pages: int | None, left: int | None, quota: int | None) -> str:
    """The daily page limit refused an upload (UX review MF20, MF21): the file, its
    pages, what is left, and when the count starts again (the page under it says when
    in the reader's own time)."""
    has = f"{name} has {pages} {'page' if pages == 1 else 'pages'}"
    if not left:
        return (
            f"{has}, and you have used all {quota} pages you can convert today. Upload it "
            "again after the count starts again at midnight UTC. Nothing was uploaded."
        )
    return (
        f"{has}, but you have {left} of today's {quota} pages left. Upload a part of up "
        f"to {left} {'page' if left == 1 else 'pages'} now, or the whole file after the "
        "count starts again at midnight UTC. Nothing was uploaded."
    )


class QuotaRefused(Exception):
    """The upload has more pages than are left today (`_store_upload`)."""

    def __init__(self, pages: int, left: int) -> None:
        super().__init__("page_quota")
        self.pages = pages
        self.left = left


def owned_job(session: Session, job_id: str, user: CurrentUser, *, lock: bool = False) -> Job:
    """The user's job, or a 404 page. A job past its `retain_until` counts as deleted even
    before the hourly purge removes it, so the retention promise does not depend on the
    scheduler running on time."""
    if not JOB_ID.match(job_id):
        raise PageError(404, *NOT_FOUND)
    job = session.get(Job, job_id, with_for_update=lock or None)
    if job is None or job.user_id != user.id or expired(job):
        raise _missing(session, job_id, user, job)
    return job


def _missing(session: Session, job_id: str, user: CurrentUser, job: Job | None) -> PageError:
    """The 404 for a job that cannot be shown. Its owner learns that it was deleted and
    why (an expired row still waiting for the purge, or the upload in their own usage
    events); anyone else learns nothing, not even that the id exists."""
    if job is not None and job.user_id == user.id:
        kept = (as_utc(job.retain_until) or utcnow()) - (as_utc(job.created_at) or utcnow())
        return PageError(404, *DELETED, template="job_deleted.html", context={"kept": _kept(kept)})
    if job is None and events.uploaded_at(session, user.id, job_id) is not None:
        return PageError(404, *DELETED, template="job_deleted.html", context={"kept": ""})
    return PageError(404, *NOT_FOUND)


def _kept(delta: timedelta) -> str:
    """'24 hours', '30 days': how long a job was kept."""
    hours = round(delta.total_seconds() / 3600)
    if hours >= 48 and hours % 24 == 0:
        return f"{hours // 24} days"
    return f"{hours} {'hour' if hours == 1 else 'hours'}"


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
    now = utcnow()
    with session_scope(ctx.factory) as session:
        # My documents as a to-do list (web/documents.py); expired jobs count as deleted.
        listing = documents.documents(session, user.id, now=now, budget_closed=spend_closed)
        left = pages_left(
            session,
            user.id,
            quota=ctx.settings.per_user_daily_page_quota,
            day=utc_today(),
        )
        # The upload form's course choice (notation memory); None hides it.
        courses = (
            notation.list_courses(session, user.id) if notation.enabled(ctx.settings) else None
        )
    return {
        "documents": listing,
        "pages_left": left,
        "page_quota": ctx.settings.per_user_daily_page_quota,
        "spend_closed": spend_closed,
        # Daily limits and today's budget start again then (shown in local time by app.js).
        "next_reset": next_day_start(now),
        "active": "app",
        "courses": courses,
    }


@router.get("/app")
def app_home(request: Request, user: UserDep) -> Response:
    return render(request, "app.html", app_context(get_ctx(request), user))


@router.get("/app/documents")
def app_documents(request: Request, user: UserDep, v: str = "") -> Response:
    """The documents list alone, polled by htmx while a document is waiting or
    converting. 204 (no swap) while nothing changed, so an open tab costs one small
    query every few seconds; when something did, the whole list, with a `<title>` that
    htmx moves into the tab title ("My documents (4 of 6 finished)")."""
    ctx = get_ctx(request)
    spend_closed = ctx.guard.cap_reached(ctx.min_headroom_usd)
    now = utcnow()
    with session_scope(ctx.factory) as session:
        if v and v == documents.list_version(session, user.id, now=now, budget_closed=spend_closed):
            return Response(status_code=204)
        listing = documents.documents(session, user.id, now=now, budget_closed=spend_closed)
    context: dict[str, Any] = {"documents": listing}
    if request.headers.get("HX-Request") == "true":
        context["page_title"] = listing.page_title
    return render(request, "_documents.html", context)


def _upload_error_page(
    request: Request,
    user: CurrentUser,
    code: str,
    status: int,
    filename: str = "",
    pages: int | None = None,
    left: int | None = None,
    headers: dict[str, str] | None = None,
    course: str = "",
) -> Response:
    ctx = get_ctx(request)
    log_event(logger, "upload_rejected", code=code)
    message = upload_error_message(code, ctx, filename, pages, left)
    context = app_context(ctx, user)
    # The course chosen stays chosen (the form marks it only among the user's courses),
    # so the file chosen again is read with the course's notation.
    context["chosen_course"] = course
    # With no pages left the form is replaced by a notice (app.html): the summary's link
    # goes there instead of to a file field that is not on the page.
    field_id = "upload-closed" if context["pages_left"] == 0 else "pdf"
    errors = [{"field_id": field_id, "message": message}]
    context |= {"errors": errors, "field_errors": {"pdf": errors[0]}}
    return render(request, "app.html", context, status_code=status, headers=headers)


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
        "Another upload of yours is still being checked",
        "A document you uploaded a moment ago, perhaps in another tab, is still being "
        "checked. Wait a few seconds, then go back and upload this one again. Nothing was "
        "uploaded this time.",
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
    # Course choice (notation memory): one of the user's own courses, or none.
    course_id = (form.fields.get("course") or None) if notation.enabled(ctx.settings) else None
    chosen = course_id or ""
    if form.data is None:
        return await run_in_threadpool(
            partial(_upload_error_page, request, user, "missing_file", 400, course=chosen)
        )
    if course_id is not None and not await run_in_threadpool(_users_course, ctx, user, course_id):
        return await run_in_threadpool(_course_error_page, request, user)
    try:
        pages = await run_in_threadpool(
            lambda: inspect_pdf_isolated(form.data or b"", max_pages=ctx.settings.max_pages_per_job)
        )
    except UploadRejected as exc:
        return await run_in_threadpool(
            partial(
                _upload_error_page,
                request,
                user,
                exc.code,
                exc.status_code,
                form.filename,
                exc.pages,
                course=chosen,
            )
        )
    keep = form.fields.get("keep") == "on"
    try:
        job_id = await run_in_threadpool(
            _store_upload, ctx, user, form.filename, form.data, pages, keep, course_id
        )
    except QuotaRefused as exc:
        return await run_in_threadpool(
            partial(
                _upload_error_page,
                request,
                user,
                "page_quota",
                429,
                form.filename,
                exc.pages,
                left=exc.left,
                headers={"Retry-After": "3600"},
                course=chosen,
            )
        )
    log_event(logger, "job_created", job_id=job_id, pages=pages, opt_in=keep)
    # Usage events (web/events.py): `uploaded`, and `reuploaded` for the same file again.
    data = form.data or b""
    await run_in_threadpool(
        lambda: events.record_upload(
            ctx.factory, ctx.settings, user_id=user.id, job_id=job_id, data=data, pages=pages
        )
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


def _job_course(request: Request, job_id: str, user: CurrentUser) -> dict[str, str] | None:
    """The course the job is read for (id and name), for the job page; None without
    one or with the feature off."""
    ctx = get_ctx(request)
    if not notation.enabled(ctx.settings):
        return None
    with session_scope(ctx.factory) as session:
        course = notation.job_course(session, owned_job(session, job_id, user))
        return {"id": course.id, "name": course.name} if course is not None else None


def _users_course(ctx: AppContext, user: CurrentUser, course_id: str) -> bool:
    if not JOB_ID.match(course_id):
        return False
    with session_scope(ctx.factory) as session:
        return notation.owned_course(session, course_id, user.id) is not None


def _course_error_page(request: Request, user: CurrentUser) -> Response:
    """The upload form again when its course is not one of the user's (deleted meanwhile
    in another tab, or a changed form). Nothing was uploaded."""
    ctx = get_ctx(request)
    log_event(logger, "upload_rejected", code="unknown_course")
    message = "That course was not found. Choose one of your courses, or \u201cNo course\u201d."
    errors = [{"field_id": "course", "message": message}]
    context = app_context(ctx, user) | {"errors": errors, "field_errors": {"course": errors[0]}}
    return render(request, "app.html", context, status_code=400)


def _store_upload(
    ctx: AppContext,
    user: CurrentUser,
    filename: str,
    data: bytes,
    pages: int,
    keep: bool,
    course_id: str | None = None,
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
            # The upload page again, with the file named (`_take_upload`).
            raise QuotaRefused(pages, left or 0)
        job = Job(
            user_id=user.id,
            status=JobStatus.QUEUED,
            filename=filename,
            page_count=pages,
            opt_in_retention=keep,
            retain_until=now + keep_for,
            created_at=now,
            course_id=course_id,
        )
        session.add(job)
        session.flush()
        session.add(Blob(job_id=job.id, kind="upload", media_type="application/pdf", content=data))
        return job.id


# --------------------------------------------------------------------------- status


def load_job_status(
    request: Request, job_id: str, user: CurrentUser
) -> tuple[JobView, JobOutcome | None]:
    """The job's view model (with the job page's data) plus its outcome (`web.outcome`),
    from one read."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        view = job_view(session, job, with_page=True)
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
    counts = job.summary.counts if job.summary is not None else None
    headline = page_rules.headline(job.status, job.status_detail, outcome, counts)
    return {"job": job, "outcome": outcome, "status_version": version, "headline": headline}


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str, user: UserDep, auto: str = "") -> Response:
    """Status page. Without JavaScript it reloads itself only if the user opted in
    (`?auto=1`, offered in a <noscript> link); htmx polls otherwise.

    `outcome` (label, message, `partial`) is in the context for the status box, which
    shows it instead of the plain status when the job is incomplete or waiting.
    """
    job, outcome = load_job_status(request, job_id, user)
    context = status_context(job, outcome) | {"auto_reload": auto == "1"}
    context["course"] = _job_course(request, job_id, user)  # notation memory
    context["after_download"] = after_download_state(request, job_id, user)  # ux-a
    # No `active` nav item: this page is not "My documents" itself (aria-current="page"
    # would announce it as such).
    return render(request, "job.html", context | {"auto_reload_s": AUTO_RELOAD_S})


@router.get("/jobs/{job_id}/status")
def job_status(request: Request, job_id: str, user: UserDep, v: str = "") -> Response:
    """Status fragment polled by htmx. 204 (no swap) while nothing changed.

    Once the job finishes, the fragment says so and links to the results instead of
    reloading the page (HX-Refresh did that before): a reload nobody asked for sends a
    screen reader back to the top of the page and loses the reader's place, while the
    status region already announces the change and the link is one step away.
    """
    job, outcome = load_job_status(request, job_id, user)
    context = status_context(job, outcome)
    if v and v == context["status_version"]:
        return Response(status_code=204)
    context["results_link"] = job.finished
    if request.headers.get("HX-Request") == "true":
        # htmx sets document.title from a top-level <title> in the fragment.
        headline: page_rules.Headline = context["headline"]
        context["page_title"] = f"{headline.label}: {job.filename}"
    return render(request, "_job_status.html", context)


@router.get("/jobs/{job_id}/progress")
def job_progress(request: Request, job_id: str, user: UserDep, v: str = "") -> Response:
    """The list of steps with their counts, polled by htmx outside the status box's live
    region (so a screen reader is not told every element). 204 while unchanged; once the
    job has finished, an empty fragment that stops polling."""
    job, _ = load_job_status(request, job_id, user)
    if v and v == job.progress_version:
        return Response(status_code=204)
    return render(request, "_job_progress.html", {"job": job})


# --------------------------------------------------------------------------- draft


@router.get("/jobs/{job_id}/draft")
def draft(request: Request, job_id: str, user: UserDep) -> Response:
    """Asked before a download while decisions are open: what students would get, with
    the review as the main way on (MF9). With none open, the job page's finish line."""
    job, _ = load_job_status(request, job_id, user)
    if job.status != JobStatus.DONE or job.page is None:
        raise PageError(409, "Not ready yet", "This document has not finished converting.",
                        context={"job_id": job_id})  # fmt: skip
    decisions = job.page.counts.decisions
    if not decisions:
        return RedirectResponse(f"/jobs/{job_id}#ready", status_code=303)
    item = f"?item={job.page.first_open}" if job.page.first_open else ""
    context = {
        "job": job,
        "decisions": decisions,
        "draft": job.page.draft,
        "review_url": f"/jobs/{job_id}/review{item}",
    }
    return render(request, "job_draft.html", context)


# --------------------------------------------------------------------------- language


@router.post("/jobs/{job_id}/language", dependencies=[Depends(require_csrf)])
def set_language(
    request: Request, job_id: str, user: UserDep, lang: Annotated[str, Form()] = ""
) -> Response:
    """The instructor confirms or changes the document language (lang_source "user"):
    the stored result changes, the notices that asked for it are answered, and the HTML
    file is made again with the new language. Only a language the control offers, or the
    one the document already has, is taken."""
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user, lock=True)
        require_done(job)
        result = DocumentResult.model_validate(job.result_json or {})
        refused = PageError(
            400,
            "Language not changed",
            "Choose the language from the list on the document's page.",
            context={"job_id": job_id},
        )
        if lang not in languages.LANGUAGE_NAMES and lang != result.lang:
            raise refused
        try:
            # The text work's rule: notices answered, passages worked out again.
            result = text_pass.set_document_language(result, lang)
        except ValueError:
            raise refused from None
        crops = _crops(session, job.id)
        from a11y_stem.web.review import bump_result_version, rerender_html

        job.html = rerender_html(result, crops, checked_on=last_decision(session, job))
        job.result_json = result.model_dump(mode="json", exclude={"html"})
        bump_result_version(job)  # review forms open in other tabs are now stale
    log_event(logger, "language_set", job_id=job_id)
    flash(
        request,
        f"The language is now {page_rules.language_label(result.lang)}. "
        "The HTML file was made again with it.",
    )
    # No fragment: the confirmation (the flash at the top) takes focus on load, as after
    # any other change; a fragment would scroll past it and keep focus on the page.
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


def _crops(session: Session, job_id: str) -> dict[str, bytes]:
    blobs = session.scalars(select(Blob).where(Blob.job_id == job_id, Blob.kind == "crop"))
    return {b.element_id: b.content for b in blobs if b.element_id}


# --------------------------------------------------------------------------- results


# Longest title part of a download's file name: long enough to recognise, short enough
# for a course's file list.
MAX_SLUG = 60


def slug(text: str) -> str:
    """'Problem Set 2: Kinematics' -> 'problem-set-2-kinematics'. Letters and digits of
    any script are kept (the download header carries UTF-8); everything else is a hyphen."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    words = re.sub(r"[\W_]+", "-", folded).strip("-")
    if len(words) > MAX_SLUG:
        words = words[:MAX_SLUG].rsplit("-", 1)[0] if "-" in words[:MAX_SLUG] else words[:MAX_SLUG]
    return words


def download_filename(title: str, job_id: str) -> str:
    """The document's title as a slug plus the start of its id, so two versions of a
    handout (or two uploads called "source.pdf") never overwrite each other in a
    Downloads folder or a course's files (UX review MF24)."""
    return f"{slug(title) or 'document'}-{job_id[:6]}.html"


def content_disposition(filename: str) -> str:
    """attachment with an ASCII fallback plus the RFC 5987 UTF-8 name."""
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else "_" for ch in filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/jobs/{job_id}/download")
def download(request: Request, job_id: str, user: UserDep) -> Response:
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        result = job.result_json or {}
        title = documents.display_title(result.get("title"), job.filename)
        html, name = job.html or "", download_filename(title, job.id)
        still_open = documents.open_decisions(result.get("fixes"))
    events.record_download(
        ctx.factory, ctx.settings, user_id=user.id, job_id=job_id, open_decisions=still_open
    )
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
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        html = job.html or ""
    events.record(
        ctx.factory, ctx.settings, user.id, events.HTML_VIEWED, job_ref=job_id, once_per_visit=True
    )
    return Response(
        html, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": VIEW_CSP}
    )


def after_download_state(
    request: Request, job_id: str, user: CurrentUser
) -> feedback_questions.AfterDownload:
    """What `_after_download.html` shows under the download link (web/feedback.py)."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        return feedback_questions.after_download(session, ctx.settings, user.id, job_id)


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


def _confirm_delete_page(
    request: Request, job_id: str, user: CurrentUser, came_from: str = ""
) -> Response:
    """'Delete <title>?' with what will go: the file, when it was uploaded and how many
    decisions the person made on it. "No, keep it" goes back where they came from (the
    list's Delete links say `from=app`)."""
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        result = job.result_json or {}
        title = documents.display_title(result.get("title"), job.filename)
        decided = documents.decided_by_you(result.get("fixes"))
        filename, uploaded = job.filename, as_utc(job.created_at)
    context = {
        "job_id": job_id,
        "title": title,
        "filename": filename,
        "uploaded": uploaded,
        "decided": decided,
        "back": "/app" if came_from == "app" else f"/jobs/{job_id}",
    }
    return render(request, "delete_confirm.html", context)


@router.get("/jobs/{job_id}/delete")
def confirm_delete(
    request: Request,
    job_id: str,
    user: UserDep,
    came_from: Annotated[str, Query(alias="from")] = "",
) -> Response:
    """The confirmation step on its own: what the Delete links of My documents open (a
    link, so going Back afterwards never offers to send a form again)."""
    return _confirm_delete_page(request, job_id, user, came_from)


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
        events.forget_upload(session, job_id)  # the file's fingerprint goes with it
    log_event(logger, "job_deleted", job_id=job_id, reason="user")
    flash(request, "Your document and everything made from it have been deleted.")
    return RedirectResponse("/app", status_code=303)
