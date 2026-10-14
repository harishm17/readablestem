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
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from readablestem.core.types import DocumentResult
from readablestem.pipeline import export_canvas, export_cartridge, text_pass
from readablestem.pipeline import language as languages
from readablestem.web import (
    documents,
    events,
    notation,
    office_convert,
    pending,
    retention,
    sample,
    upload_result,
)
from readablestem.web import feedback as feedback_questions
from readablestem.web import job_page as page_rules
from readablestem.web import retry as retry_rules
from readablestem.web.database import as_utc, utc_today
from readablestem.web.db import Blob, Job, JobStatus, session_scope, utcnow
from readablestem.web.deps import (
    CSRF_FIELD,
    ELEMENT_ID,
    JOB_ID,
    AppContext,
    CsrfFailed,
    CurrentUser,
    PageError,
    UserDep,
    csrf_valid,
    enforce,
    flash,
    get_ctx,
    limited,
    render,
    require_csrf,
)
from readablestem.web.intake import (  # noqa: F401  (re-exported: the caps are documented here)
    MAX_ACTIVE_JOBS_PER_USER,
    MAX_LIVE_JOBS_PER_USER,
    QuotaRefused,
    TooManyJobs,
    live_job_count,
    store_job,
)
from readablestem.web.logsafe import log_event
from readablestem.web.outcome import JobOutcome, job_outcome
from readablestem.web.pending import MAX_PENDING_PER_USER
from readablestem.web.quota import next_day_start, pages_left
from readablestem.web.security import VIEW_CSP
from readablestem.web.uploads import (
    INSPECT_TIMEOUT_S,
    MAX_FILES_PER_UPLOAD,
    UploadedFile,
    UploadRejected,
    convert_office_isolated,
    inspect_pdf_isolated,
    is_office_upload,
    max_upload_bytes,
    read_upload_form,
)
from readablestem.web.views import JobView, job_view, last_decision

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
# A refused upload costs nothing: the probe runs before the daily page count.
NOTHING_TAKEN = "Nothing was converted, and no pages were taken from your daily allowance."


def upload_error_message(
    code: str,
    ctx: AppContext,
    filename: str = "",
    pages: int | None = None,
    left: int | None = None,
    *,
    office: bool = False,
) -> str:
    """What went wrong with an upload, naming the file (and its pages) when known. The
    file name is shown to its owner only, never logged. `left`: pages left today, for
    "page_quota". `office`: the file was a Word or PowerPoint file, made into a PDF first
    (T3-30), so the advice about pages is about the document and not about a PDF."""
    s = ctx.settings
    name = filename or "This file"
    limit = s.source_page_limit
    too_many = f"{name} has {pages} pages" if pages else f"{name} has more than {limit} pages"
    if code == "page_quota":
        return _quota_message(name, pages, left, s.per_user_daily_page_quota)
    accepted = office_convert.input_kinds(s)  # Word and PowerPoint files taken (T3-30)
    word = bool(accepted)
    kinds, endings = office_convert.accepted_phrases(accepted)
    return {
        "missing_file": f"Choose {kinds} to upload." if word else "Choose a PDF file to upload.",
        "bad_form": "The upload did not arrive complete. Please try again.",
        "empty": f"{name} is empty. Choose a file that has content."
        if word
        else f"{name} is empty. Choose a PDF that has content.",
        "not_pdf": f"{name} is not {kinds}. Choose {endings}."
        if word
        else f"{name} is not a PDF. Choose a PDF file.",
        "too_large": f"{name} is larger than {s.upload_limit_mb} MB. Split it into smaller "
        "files and upload each one."
        if word
        else f"{name} is larger than {s.upload_limit_mb} MB. Split it into smaller "
        "PDFs and upload each one.",
        "too_many_files": f"You chose more than {MAX_FILES_PER_UPLOAD} files. Upload "
        f"{MAX_FILES_PER_UPLOAD} or fewer at a time.",
        "revise_one_file": "A revised version replaces one document: choose one file. To "
        "upload several documents, use “Convert a PDF” on My documents.",
        "revision_too_large": f"{name} has more pages or is larger than one conversion takes, "
        "so it cannot be a revised version. Upload it as a new document and choose which "
        "pages to convert.",
        "too_many_pending": f"You already have {MAX_PENDING_PER_USER} large files waiting for "
        f"you to choose their pages. Choose or discard one, then upload {name} again. Nothing "
        "was uploaded.",
        "office_unavailable": f"{name} is a Word or PowerPoint file, and this server cannot "
        "read those yet. In Word or PowerPoint, save a copy as a PDF (File, Save As, PDF; "
        f"keep the option for accessibility tags ticked) and upload that. {NOTHING_TAKEN}",
        "office_unsupported": f"{name} is not {kinds}. Choose {endings}.",
        "office_old_or_locked": f"{name} is in an older Word or PowerPoint format (.doc or "
        ".ppt), or it is protected with a password. Save a copy as .docx or .pptx "
        "without a password, or save it as a PDF, and upload that.",
        "office_unsafe": f"{name} has something this tool will not open: macros, content "
        "linked from another file or web address, or far more data inside it than a "
        "document needs. Save a copy without them (put pictures in the file instead of "
        "linking them), or save it as a PDF, and upload that.",
        "office_failed": f"{name} could not be turned into a PDF. It may be damaged or too "
        "complicated. Open it in Word or PowerPoint, save it as a PDF, and upload that.",
        "office_busy": "Another Word or PowerPoint file is being converted at the moment. "
        f"Wait a minute, then upload {name} again. Nothing was uploaded.",
        "office_budget": f"{name} was not converted: Word and PowerPoint files take a while, "
        "and this upload had used up its time. Upload it again on its own. Nothing was "
        "uploaded.",
        "encrypted": f"{name} is password-protected. Save a copy without a password (for "
        "example with Print to PDF) and upload that.",
        "unreadable": f"{name} could not be opened. It may be damaged. {FRESH_COPY}",
        "damaged": f"{name} looks damaged or incomplete: it may not have finished "
        f"downloading. {FRESH_COPY}",
        "no_pages": f"{name} has no pages. Check that you chose the right file.",
        "too_many_pages": f"{too_many} once it is made into a PDF (the count can differ from "
        f"Word's or PowerPoint's); the limit is {limit} per upload. Split it into parts of "
        f"up to {limit} pages and upload each part."
        if office
        else f"{too_many}; the limit is {limit} per upload. Split it into parts "
        f"of up to {limit} pages (in Acrobat: Organize Pages, then Split) and upload each "
        "part.",
        "page_too_large": f"{name} has a page larger than 1.7 metres on a side, which is too "
        "large to convert. Course documents are usually Letter or A4 size.",
        # Every page handwritten or scanned (pipeline.page_type.refusal_code). Never "run
        # OCR first": that makes exactly the files refused here. One sentence for what
        # happened (the error summary's link), then why and what to do (the field's
        # message, `upload_error_summary`).
        "scanned": f"{name} is scanned, so this tool cannot convert it. It has no text "
        f"this tool can use. {NOTHING_TAKEN} If you have the original file (LaTeX, Word or "
        "PowerPoint), export a PDF from it and upload that instead.",
        "scanned_ocr": f"{name} is scanned, so this tool cannot convert it. Its text was "
        "guessed from the images by a text recognition (OCR) program, which is often wrong "
        f"in maths, so it is not used; running OCR again will not help. {NOTHING_TAKEN} If "
        "you have the original file (LaTeX, Word or PowerPoint), export a PDF from it and "
        "upload that instead.",
        "handwritten": f"{name} is handwritten, so this tool cannot convert it. Its hidden "
        "text, guessed from the handwriting by a tablet or an OCR program, is often wrong in "
        f"maths, so it is not used. {NOTHING_TAKEN} If you have a typed version (LaTeX, Word "
        "or PowerPoint), export a PDF from it and upload that instead.",
        "handwritten_no_text": f"{name} is handwritten, so this tool cannot convert it. "
        f"It has no text this tool can use. {NOTHING_TAKEN} If you have a typed version "
        "(LaTeX, Word or PowerPoint), export a PDF from it and upload that instead.",
        "no_content": f"Every page of {filename or 'this file'} is blank. Check that you "
        "chose the right file.",
        "unknown_revision": "The document you wanted to replace with a new version was not "
        f"found. It may have been deleted. Upload {name} as a new document, or choose "
        "another document to revise.",
        "too_many_active": f"You already have {MAX_ACTIVE_JOBS_PER_USER} documents waiting or "
        f"still being converted. Wait for one to finish, then upload {name} again. Nothing was "
        "uploaded.",
        "too_many_stored": f"You are keeping {MAX_LIVE_JOBS_PER_USER} documents, the most "
        f"allowed. Delete one you no longer need, then upload {name} again. Nothing was "
        "uploaded.",
    }.get(code, f"{name} cannot be converted.")


def upload_error_summary(message: str) -> str:
    """The error summary's link for an upload error: its first sentence, when the message
    has more (the whole message is shown under the file field)."""
    first, dot, rest = message.partition(". ")
    return first + "." if dot and rest else message


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
    if job.status != JobStatus.DONE or (job.html is None and not job.html_stale):
        raise PageError(
            409,
            "Not ready yet",
            "This document has not finished converting, so there is nothing to show yet.",
            context={"job_id": job.id},
        )


def current_html(ctx: AppContext, session: Session, job: Job) -> Job:
    """`job` with its HTML file up to date.

    A review write changes the stored result and only marks the file out of date
    (`Job.html_stale`): a run of clicks costs one render, made here when somebody asks for
    the file or for the checks of it (download, view, Canvas, the job page). The row is
    locked for the rewrite, so a review write that comes meanwhile waits, then marks the
    file out of date again. The student note's date is the day of the last decision, not
    of the render."""
    if job.status != JobStatus.DONE or not job.html_stale:
        return job
    from readablestem.web.review import rerender_job_html

    session.refresh(job, with_for_update=True)
    if job.html_stale:
        result = DocumentResult.model_validate(job.result_json or {})
        with notation.in_force(ctx.factory, ctx.settings, job.id):
            job.html = rerender_job_html(
                job,
                result,
                _crops(session, job.id),
                checked_on=last_decision(session, job),
                math_alttext=ctx.settings.mathml_alttext,
            )
        job.result_json = result.model_dump(mode="json", exclude={"html"})
        job.html_stale = False
    return job


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
        # The recorded sample document the person already has, if any (T3-9).
        sample_job = sample.live_sample(session, user.id, now)
        # Large files waiting for their pages to be chosen (T3-3).
        waiting = [
            {
                "id": row.id,
                "filename": row.filename,
                "pages": row.pages,
                "size_mb": f"{row.size_bytes / (1024 * 1024):.1f}",
                "retain_until": as_utc(row.retain_until),
            }
            for row in pending.live(session, user.id, now)
        ]
    kinds = office_convert.input_kinds(ctx.settings)
    phrases = office_convert.accepted_phrases(kinds)
    return {
        "documents": listing,
        "pages_left": left,
        "page_quota": ctx.settings.per_user_daily_page_quota,
        "spend_closed": spend_closed,
        # Daily limits and today's budget start again then (shown in local time by app.js).
        "next_reset": next_day_start(now),
        "active": "app",
        "courses": courses,
        "pending": waiting,
        "sample_available": sample.available(),
        "sample_job": sample_job,
        "own_documents": any(not row.is_sample for row in listing.rows),
        "max_files": MAX_FILES_PER_UPLOAD,
        "upload_limit_mb": ctx.settings.upload_limit_mb,
        "source_pages": ctx.settings.source_page_limit,
        "ready_documents": sum(
            1 for row in listing.rows if row.status == JobStatus.DONE and not row.open_decisions
        ),
        # Word (.docx) and PowerPoint (.pptx) files are taken (T3-30): the kinds installed.
        "office_kinds": kinds,
        "office_what": phrases[0],
        "office_choose": phrases[1],
    }


@router.get("/app")
def app_home(request: Request, user: UserDep, revises: str = "") -> Response:
    ctx = get_ctx(request)
    context = app_context(ctx, user)
    if revises:
        context |= revision_context(ctx, user, revises)
    return render(request, "app.html", context)


def revision_context(ctx: AppContext, user: CurrentUser, job_id: str) -> dict[str, Any]:
    """The upload form for a revised version of one of the person's finished documents
    (T3-2): its title, and its course and exam choice as the form's starting values.
    Nothing when the document is not theirs, is gone or is not finished (a plain upload
    form, as without the link)."""
    if not JOB_ID.match(job_id):
        return {}
    with session_scope(ctx.factory) as session:
        job = session.get(Job, job_id)
        if (
            job is None
            or job.user_id != user.id
            or expired(job)
            or job.status != JobStatus.DONE
            or job.is_sample
        ):
            return {}
        title = documents.display_title(
            (job.result_json or {}).get("title"), job.filename, job.title_override
        )
        return {
            "revises": {"id": job.id, "title": title, "filename": job.filename},
            "chosen_course": job.course_id or "",
            "exam_default": bool(job.exam_mode),
        }


@router.get("/app/documents")
def app_documents(request: Request, user: UserDep, v: str = "", course: str = "") -> Response:
    """The documents list alone, polled by htmx while a document is waiting or
    converting. 204 (no swap) while nothing changed, so an open tab costs one small
    query every few seconds; when something did, the whole list, with a `<title>` that
    htmx moves into the tab title ("My documents (4 of 6 finished)")."""
    ctx = get_ctx(request)
    spend_closed = ctx.guard.cap_reached(ctx.min_headroom_usd)
    now = utcnow()
    # The list of one course's documents (the course page, T3-4) polls with its own id.
    course_id = course if JOB_ID.match(course) else None
    with session_scope(ctx.factory) as session:
        if v and v == documents.list_version(
            session, user.id, now=now, budget_closed=spend_closed, course_id=course_id
        ):
            return Response(status_code=204)
        listing = documents.documents(
            session, user.id, now=now, budget_closed=spend_closed, course_id=course_id
        )
    context: dict[str, Any] = {
        "documents": listing,
        "list_url": f"/app/documents?course={course_id}" if course_id else "/app/documents",
    }
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
    revises: str = "",
    office: bool = False,
) -> Response:
    ctx = get_ctx(request)
    log_event(logger, "upload_rejected", code=code, office=office)
    message = upload_error_message(code, ctx, filename, pages, left, office=office)
    context = app_context(ctx, user)
    # The course chosen stays chosen (the form marks it only among the user's courses),
    # so the file chosen again is read with the course's notation.
    context["chosen_course"] = course
    if revises:  # the form stays the revised version's
        context |= revision_context(ctx, user, revises) | {"chosen_course": course}
    # With no pages left the form is replaced by a notice (app.html): the summary's link
    # goes there instead of to a file field that is not on the page.
    field_id = "upload-closed" if context["pages_left"] == 0 else "pdf"
    # The summary links to the field with a short line; the field says all of it.
    summary = message if field_id == "upload-closed" else upload_error_summary(message)
    errors = [{"field_id": field_id, "message": summary}]
    context |= {
        "errors": errors,
        "field_errors": {"pdf": {"field_id": field_id, "message": message}},
    }
    return render(request, "app.html", context, status_code=status, headers=headers)


@router.post("/jobs", dependencies=[limited("upload")])
async def create_job(request: Request, user: UserDep) -> Response:
    ctx = get_ctx(request)
    enforce(request, "upload_user", user.id)  # before anything is read or any process starts
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


@dataclass
class Placed:
    """What became of one file of an upload: queued as a job (`job_id`), kept for the
    person to choose its pages (`pending_id`), or refused (`code`, with the `status` and
    the page counts its message needs)."""

    filename: str
    pages: int = 0
    job_id: str = ""
    pending_id: str = ""
    code: str = ""
    status: int = 0
    left: int | None = None
    retry_after: str = ""
    office: bool = False  # the file was Word or PowerPoint, made into a PDF first (T3-30)


# How long the probes of one upload may take in all: each file has its own 30 s, but ten
# files that hang must not hold the person's upload slot for five minutes.
INSPECT_BUDGET_S = 120.0
# Word and PowerPoint files of one upload are made into PDFs one after another, each in the
# time `OFFICE_TIMEOUT_S` allows; together they get this long, so ten slow files cannot hold
# the request for fifteen minutes. A file the time ran out for is refused, not queued.
OFFICE_BUDGET_S = 180.0


async def _take_upload(request: Request, ctx: AppContext, user: CurrentUser) -> Response:
    """Read, check and store an upload of one or several files (inside the upload gate)."""
    s = ctx.settings
    try:
        form = await read_upload_form(
            request,
            max_bytes=s.upload_limit_bytes,
            max_total_bytes=s.upload_limit_bytes,
            max_files=MAX_FILES_PER_UPLOAD,
        )
    except UploadRejected as exc:
        return await run_in_threadpool(_upload_error_page, request, user, exc.code, exc.status_code)
    if not csrf_valid(request, form.fields.get(CSRF_FIELD)):
        raise CsrfFailed()
    # Course choice (notation memory): one of the user's own courses, or none.
    course_id = (form.fields.get("course") or None) if notation.enabled(ctx.settings) else None
    chosen = course_id or ""
    # A revised version of one of the person's documents (T3-2): its decisions are carried
    # over when this one has been converted. Checked now, before the file is opened.
    revises = form.fields.get("revises") or None

    def refuse(code: str, status: int = 400, filename: str = "") -> Any:
        return run_in_threadpool(
            partial(
                _upload_error_page,
                request,
                user,
                code,
                status,
                filename,
                course=chosen,
                revises=revises or "",
            )
        )

    if revises is not None and not await run_in_threadpool(_users_revision, ctx, user, revises):
        return await refuse("unknown_revision", 400, form.filename)
    if revises is not None and len(form.files) > 1:
        return await refuse("revise_one_file")
    if not form.files:
        return await refuse("missing_file")
    if course_id is not None and not await run_in_threadpool(_users_course, ctx, user, course_id):
        return await run_in_threadpool(_course_error_page, request, user)
    for _ in form.files[1:]:  # every file after the first is an upload for the rate limits
        enforce(request, "upload_user", user.id)
    mode = retention.mode_for_upload(
        keep=form.fields.get("keep") == "on", share=form.fields.get("share") == "on"
    )
    # "Exam or quiz": figures are described without giving answers (T1-14).
    exam = form.fields.get("exam") == "on"
    placed = await run_in_threadpool(
        _place_files, ctx, user, form.files, mode, course_id, exam, revises
    )
    if len(placed) == 1:
        return await _answer_one(request, user, placed[0], chosen, revises or "")
    return await run_in_threadpool(_results_redirect, ctx, user, placed)


def _place_files(
    ctx: AppContext,
    user: CurrentUser,
    files: list[UploadedFile],
    mode: str,
    course_id: str | None,
    exam: bool,
    revises: str | None,
) -> list[Placed]:
    """Check each file and queue it, or keep it for choosing its pages, or say why not.
    A refusal of one file does not stop the others: each is its own document."""
    s = ctx.settings
    deadline = time.monotonic() + INSPECT_BUDGET_S
    office_left = OFFICE_BUDGET_S
    placed: list[Placed] = []
    for file in files:
        # A Word or PowerPoint file is made into a tagged PDF first (T3-30); from here on the
        # PDF is what is probed, stored, split into parts and converted, as for any upload.
        data, office = file.data, False
        try:
            if is_office_upload(file.data) and office_convert.input_kinds(s):
                office = True
                if office_left < 5.0:
                    raise UploadRejected("office_budget")
                started = time.monotonic()
                try:
                    data = _office_to_pdf(
                        ctx, file.data, timeout_s=min(s.office_timeout_s, office_left)
                    )
                finally:
                    spent = time.monotonic() - started
                    office_left -= spent
                    deadline += spent  # the probes keep their own time
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                raise UploadRejected("unreadable")
            pages = inspect_pdf_isolated(
                data,
                max_pages=s.source_page_limit,
                timeout_s=min(INSPECT_TIMEOUT_S, remaining),
            )
            if len(data) > s.upload_limit_bytes:  # the PDF made from a Word file can be big
                raise UploadRejected("too_large")
        except UploadRejected as exc:
            placed.append(
                Placed(
                    file.filename,
                    exc.pages or 0,
                    code=exc.code,
                    status=exc.status_code,
                    retry_after="10" if exc.code == "office_busy" else "",
                    office=office,
                )
            )
            continue
        too_big = len(data) > max_upload_bytes(s.max_upload_mb)
        if pages > s.max_pages_per_job or too_big:
            placed.append(
                _keep_for_parts(
                    ctx,
                    user,
                    UploadedFile(file.filename, data),
                    pages,
                    mode,
                    course_id,
                    exam,
                    revises,
                )
            )
            placed[-1].office = office
            continue
        try:
            job_id = store_job(
                ctx.factory,
                s,
                user.id,
                file.filename,
                data,
                pages,
                mode,
                course_id=course_id,
                exam=exam,
                revises=revises,
                now=utcnow(),
            )
        except TooManyJobs as exc:
            placed.append(
                Placed(file.filename, code=exc.code, status=429, retry_after="60", office=office)
            )
            continue
        except QuotaRefused as exc:
            placed.append(
                Placed(
                    file.filename,
                    exc.pages,
                    code="page_quota",
                    status=429,
                    left=exc.left,
                    retry_after="3600",
                    office=office,
                )
            )
            continue
        log_event(
            logger,
            "job_created",
            job_id=job_id,
            pages=pages,
            opt_in=mode == "dev",
            retention=mode,
            files=len(files),
        )
        events.record_upload(
            ctx.factory, s, user_id=user.id, job_id=job_id, data=file.data, pages=pages
        )
        placed.append(Placed(file.filename, pages, job_id=job_id, office=office))
    return placed


def _keep_for_parts(
    ctx: AppContext,
    user: CurrentUser,
    file: UploadedFile,
    pages: int,
    mode: str,
    course_id: str | None,
    exam: bool,
    revises: str | None,
) -> Placed:
    """A file over the page or size limit is kept for the person to choose its pages
    (T3-3, `web.pending`); nothing is converted or counted until they do."""
    if revises is not None:
        return Placed(file.filename, pages, code="revision_too_large", status=422)
    try:
        with session_scope(ctx.factory) as session:
            row = pending.create(
                session,
                ctx.settings,
                user_id=user.id,
                filename=file.filename,
                data=file.data,
                pages=pages,
                mode=mode,
                course_id=course_id,
                exam=exam,
            )
            pending_id = row.id
    except pending.TooManyPending:
        return Placed(file.filename, pages, code="too_many_pending", status=429, retry_after="60")
    log_event(logger, "upload_kept_for_parts", pending_id=pending_id, pages=pages)
    return Placed(file.filename, pages, pending_id=pending_id)


async def _answer_one(
    request: Request, user: CurrentUser, item: Placed, chosen: str, revises: str
) -> Response:
    """The answer to an upload of one file: its job page, the page for choosing its pages,
    or the upload form again with the reason."""
    if item.job_id:
        return RedirectResponse(f"/jobs/{item.job_id}", status_code=303)
    if item.pending_id:
        return RedirectResponse(f"/uploads/{item.pending_id}", status_code=303)
    headers = {"Retry-After": item.retry_after} if item.retry_after else None
    return await run_in_threadpool(
        partial(
            _upload_error_page,
            request,
            user,
            item.code,
            item.status,
            item.filename,
            item.pages or None,
            left=item.left,
            headers=headers,
            course=chosen,
            revises=revises,
            office=item.office,
        )
    )


def _results_redirect(ctx: AppContext, user: CurrentUser, placed: list[Placed]) -> Response:
    """The answer to an upload of several files: the outcome of each is stored for a short
    time (`web.upload_result`) and the person is sent to the page that shows it. A redirect
    and not the page itself, so refreshing the page cannot upload the files again."""
    for item in placed:
        if item.code:
            log_event(logger, "upload_rejected", code=item.code)
    with session_scope(ctx.factory) as session:
        result_id = upload_result.store(session, user.id, [asdict(item) for item in placed])
    return RedirectResponse(f"/app/upload/{result_id}", status_code=303)


@router.get("/app/upload/{result_id}")
def upload_results_page(request: Request, result_id: str, user: UserDep) -> Response:
    """What became of the files of an upload of several: what was queued, what waits for the
    person to choose its pages, and what was refused and why. Owner only, for an hour."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        items = upload_result.load(session, user.id, result_id)
    if items is None:
        raise PageError(404, *UPLOAD_RESULT_GONE)
    placed = [Placed(**item) for item in items]
    queued = [i for i in placed if i.job_id]
    choose = [i for i in placed if i.pending_id]
    refused = [
        {
            "message": upload_error_message(
                i.code, ctx, i.filename, i.pages or None, i.left, office=i.office
            )
        }
        for i in placed
        if i.code
    ]
    context = {
        "queued": [{"id": i.job_id, "filename": i.filename, "pages": i.pages} for i in queued],
        "choose": [{"id": i.pending_id, "filename": i.filename, "pages": i.pages} for i in choose],
        "refused": refused,
        "received": len(queued) + len(choose),  # a large file kept for its pages is uploaded
        "total": len(placed),
        "active": "app",
    }
    return render(request, "upload_results.html", context)


UPLOAD_RESULT_GONE = (
    "This answer is no longer kept",
    "The answer to an upload is kept for an hour. Your documents are on My documents.",
)


def _office_to_pdf(ctx: AppContext, data: bytes, *, timeout_s: float | None = None) -> bytes:
    """A Word or PowerPoint upload as PDF bytes (`web.office_convert`); `UploadRejected`
    when this server cannot take it."""
    s = ctx.settings
    soffice = office_convert.soffice_command(s.soffice_path)
    if not office_convert.input_kinds(s):
        raise UploadRejected("office_unavailable")
    return convert_office_isolated(
        data, soffice=soffice, timeout_s=s.office_timeout_s if timeout_s is None else timeout_s
    )


def _job_course(request: Request, job_id: str, user: CurrentUser) -> dict[str, str] | None:
    """The course the job is read for (id and name), for the job page; None without
    one or with the feature off."""
    ctx = get_ctx(request)
    if not notation.enabled(ctx.settings):
        return None
    with session_scope(ctx.factory) as session:
        course = notation.job_course(session, owned_job(session, job_id, user))
        return {"id": course.id, "name": course.name} if course is not None else None


def _job_suggestions(request: Request, job_id: str, user: CurrentUser) -> dict[str, Any]:
    """The notation this finished document uses that its course does not have (T3-12),
    for the job page's suggestion form; empty without a course, with the feature off or
    when there is nothing to add."""
    ctx = get_ctx(request)
    if not notation.enabled(ctx.settings):
        return {}
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        course = notation.job_course(session, job)
        if course is None:
            return {}
        rows = notation.course_suggestions(session, ctx.settings, course, job)
        if not rows:
            return {}
        return {
            "suggestions": rows,
            "suggest_job": job.id,
            "meanings": notation.MEANINGS,
        }


def _users_course(ctx: AppContext, user: CurrentUser, course_id: str) -> bool:
    if not JOB_ID.match(course_id):
        return False
    with session_scope(ctx.factory) as session:
        return notation.owned_course(session, course_id, user.id) is not None


def _users_revision(ctx: AppContext, user: CurrentUser, job_id: str) -> bool:
    """The id is one of the person's finished documents that is still kept."""
    if not JOB_ID.match(job_id):
        return False
    with session_scope(ctx.factory) as session:
        job = session.get(Job, job_id)
        return (
            job is not None
            and job.user_id == user.id
            and not expired(job)
            and job.status == JobStatus.DONE
            and not job.is_sample
        )


def _course_error_page(request: Request, user: CurrentUser) -> Response:
    """The upload form again when its course is not one of the user's (deleted meanwhile
    in another tab, or a changed form). Nothing was uploaded."""
    ctx = get_ctx(request)
    log_event(logger, "upload_rejected", code="unknown_course")
    message = "That course was not found. Choose one of your courses, or \u201cNo course\u201d."
    errors = [{"field_id": "course", "message": message}]
    context = app_context(ctx, user) | {"errors": errors, "field_errors": {"course": errors[0]}}
    return render(request, "app.html", context, status_code=400)


# --------------------------------------------------------------------------- status


def load_job_status(
    request: Request, job_id: str, user: CurrentUser
) -> tuple[JobView, JobOutcome | None]:
    """The job's view model (with the job page's data) plus its outcome (`web.outcome`),
    from one read."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = current_html(ctx, session, owned_job(session, job_id, user))
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
    # The AI service is not answering right now (T3-1, `web.provider_health`): said on a
    # document that is waiting or converting, and beside "Try again". The status version
    # changes with it, so a page left open hears when it starts and stops.
    from readablestem.web.provider_health import HEALTH

    down = HEALTH.is_down()
    if down:
        version += "-down"
    return {
        "job": job,
        "outcome": outcome,
        "status_version": version,
        "headline": headline,
        "provider_down": down,
    }


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
    context |= _job_suggestions(request, job_id, user)  # notation found in the document
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
        context["page_title"] = f"{headline.label}: {job.title}"
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
        from readablestem.web.review import bump_result_version, rerender_job_html

        job.html = rerender_job_html(
            job,
            result,
            crops,
            checked_on=last_decision(session, job),
            math_alttext=get_ctx(request).settings.mathml_alttext,
        )
        job.result_json = result.model_dump(mode="json", exclude={"html"})
        job.html_stale = False  # made from the stored result, review writes included
        bump_result_version(job)  # review forms open in other tabs are now stale
        retention.restart_clock(job, get_ctx(request).settings)
    log_event(logger, "language_set", job_id=job_id)
    flash(
        request,
        f"The language is now {page_rules.language_label(result.lang)}. "
        "The HTML file was made again with it.",
    )
    # No fragment: the confirmation (the flash at the top) takes focus on load, as after
    # any other change; a fragment would scroll past it and keep focus on the page.
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# --------------------------------------------------------------------------- try again

RETRY_REFUSED = {
    "not_stopped": (
        409,
        "Nothing to try again",
        "This document is not stopped: it is still waiting or being converted, or it has "
        "finished completely. Nothing was changed.",
    ),
    "too_many_active": (
        429,
        "Too many documents in progress",
        f"You already have {MAX_ACTIVE_JOBS_PER_USER} documents waiting or being converted. "
        "Wait for one to finish, then try again. Nothing was changed.",
    ),
}


@router.post("/jobs/{job_id}/retry", dependencies=[limited("retry"), Depends(require_csrf)])
def retry_job(request: Request, job_id: str, user: UserDep) -> Response:
    """ "Try again" on a stopped or partly converted document (T3-1, `web.retry`): the same
    job goes back in the queue. Nothing is counted against the daily pages (this is not an
    upload), the finished steps and the decisions made are kept, and every limit holds: the
    daily budget (a queued job waits for it), the job's own spend caps, the documents
    waiting or converting at once, the tries per job and the rate limit."""
    ctx = get_ctx(request)
    enforce(request, "retry_user", user.id)
    refused = ""
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user, lock=True)
        others = int(
            session.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.user_id == user.id,
                    Job.id != job.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
            or 0
        )
        try:
            retry_rules.start(job, active_others=others, max_active=MAX_ACTIVE_JOBS_PER_USER)
        except retry_rules.RetryRefused as exc:
            refused, tries = exc.code, job.retries or 0
        else:
            retention.restart_clock(job, ctx.settings)
            tries = job.retries or 0
    if refused == "no_tries_left":
        raise PageError(
            409,
            "No more tries",
            f"You have already tried this document {tries} times. Upload the file again as a "
            "new document. Nothing was changed.",
            context={"job_id": job_id},
        )
    if refused:
        status, title, message = RETRY_REFUSED[refused]
        raise PageError(status, title, message, context={"job_id": job_id})
    log_event(logger, "job_retried", job_id=job_id, tries=tries)
    flash(
        request,
        "Your document is queued again. What was already converted is kept, and nothing "
        "was counted against your daily pages.",
        undo=False,
    )
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
        job = current_html(ctx, session, job)
        result = job.result_json or {}
        title = documents.display_title(result.get("title"), job.filename, job.title_override)
        html, name = job.html or "", download_filename(title, job.id)
        still_open = documents.open_decisions(result.get("fixes"))
        retention.restart_clock(job, ctx.settings)  # a download is activity (T1-8)
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


def canvas_filename(title: str, job_id: str) -> str:
    """The Canvas zip's name: the HTML file's name, with a zip's ending."""
    return "canvas-" + download_filename(title, job_id).removesuffix(".html") + ".zip"


@router.get("/jobs/{job_id}/download/canvas")
def download_canvas(request: Request, job_id: str, user: UserDep, course: str = "") -> Response:
    """The same document for Canvas (pipeline/export_canvas.py): a zip with index.html, its
    pictures as files, and a README; with `course` (the Canvas course number) also a page to
    paste. A GET like the HTML download, for the owner only; no script is needed."""
    number = course.strip() or None
    if number is not None and not export_canvas.valid_course_number(number):
        raise PageError(
            400,
            "Check the course number",
            "A Canvas course number is digits only: it is the number after /courses/ in the "
            "address of your course. Go back and try again, or leave it empty.",
            context={"job_id": job_id},
        )
    ctx = get_ctx(request)
    export, title, job_ref, still_open = _canvas_export(ctx, user, job_id, number)
    events.record_download(
        ctx.factory, ctx.settings, user_id=user.id, job_id=job_id, open_decisions=still_open
    )
    return Response(
        export_canvas.build_zip(export),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(canvas_filename(title, job_ref)),
            "Content-Security-Policy": VIEW_CSP,
        },
    )


def cartridge_filename(title: str, job_id: str) -> str:
    """The Common Cartridge's name: the HTML file's name, with the cartridge ending."""
    return download_filename(title, job_id).removesuffix(".html") + ".imscc"


@router.get("/jobs/{job_id}/download/cartridge")
def download_cartridge(request: Request, job_id: str, user: UserDep) -> Response:
    """The same document as an IMS Common Cartridge 1.3 package (pipeline/export_cartridge.py),
    which Canvas, Blackboard, Moodle and D2L import. A GET like the other downloads, for the
    owner only."""
    ctx = get_ctx(request)
    export, title, job_ref, still_open = _canvas_export(ctx, user, job_id, None)
    events.record_download(
        ctx.factory, ctx.settings, user_id=user.id, job_id=job_id, open_decisions=still_open
    )
    return Response(
        export_cartridge.build_cartridge(export),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(cartridge_filename(title, job_ref)),
            "Content-Security-Policy": VIEW_CSP,
        },
    )


def _canvas_export(
    ctx: AppContext, user: CurrentUser, job_id: str, number: str | None
) -> tuple[export_canvas.CanvasExport, str, str, int]:
    """The finished document, exported for Canvas (the owner's, done, up to date). A
    picture the export cannot hold is a 409 page, and the HTML file stays available."""
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        job = current_html(ctx, session, job)
        result = job.result_json or {}
        title = documents.display_title(result.get("title"), job.filename)
        html, job_ref = job.html or "", job.id
        still_open = documents.open_decisions(result.get("fixes"))
    try:
        export = export_canvas.export_canvas(
            html,
            title=title,
            folder=export_canvas.folder_name(title, job_ref),
            course_number=number,
        )
    except export_canvas.CanvasExportError:
        log_event(logger, "canvas_export_failed", code="image_type")
        raise PageError(
            409,
            "This document cannot be made for Canvas",
            "It has a picture in a form the Canvas version cannot hold. The HTML file is "
            "still available from the job page.",
            context={"job_id": job_ref},
        ) from None
    return export, title, job_ref, still_open


@router.get("/jobs/{job_id}/view")
def view(request: Request, job_id: str, user: UserDep) -> Response:
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        html = current_html(ctx, session, job).html or ""
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
        title = documents.display_title(result.get("title"), job.filename, job.title_override)
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
    from readablestem.ops.retention import delete_jobs

    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        # The one supported deletion path: counts the job's categories before deleting.
        delete_jobs(session, [job.id])
        events.forget_upload(session, job_id)  # the file's fingerprint goes with it
    log_event(logger, "job_deleted", job_id=job_id, reason="user")
    flash(request, "Your document and everything made from it have been deleted.")
    return RedirectResponse("/app", status_code=303)
