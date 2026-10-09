"""Courses and their notation: /courses, a course's page, and "remember for this course".

Only with `FEATURE_NOTATION_MEMORY` on; otherwise every route here is a 404, like any
address that does not exist. Each course belongs to one user: `_course` answers 404 for
a course that does not exist *or* is someone else's, so ids cannot be probed. Every
change is a normal form POST with the CSRF token, answered with a redirect (or the form
again with its errors), so it all works without JavaScript. Deleting a course asks
first (WCAG 3.3.4); removing one symbol does not (it is typed back in a moment).

The data rules live in `web.notation`. Course names, symbols and notes are user
content: only ids and counts are logged.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from a11y_stem.core.notation import canonical_symbol
from a11y_stem.core.types import DocumentResult
from a11y_stem.pipeline.notation_memory import describe, phrase
from a11y_stem.web import notation
from a11y_stem.web.db import Course, CourseNotation, session_scope
from a11y_stem.web.deps import (
    ELEMENT_ID,
    JOB_ID,
    CurrentUser,
    PageError,
    UserDep,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.routes.jobs import NOT_FOUND, owned_job

logger = logging.getLogger(__name__)
router = APIRouter()

Field = Annotated[str, Form()]


def _require_feature(request: Request) -> None:
    if not notation.enabled(get_ctx(request).settings):
        raise PageError(404, *NOT_FOUND)


def _course(session: Session, course_id: str, user: CurrentUser) -> Course:
    if not JOB_ID.match(course_id):
        raise PageError(404, *NOT_FOUND)
    course = notation.owned_course(session, course_id, user.id)
    if course is None:
        raise PageError(404, *NOT_FOUND)
    return course


def _entry(course: Course, entry_id: str) -> CourseNotation:
    row = next((r for r in course.notation if r.id == entry_id), None)
    if row is None or not JOB_ID.match(entry_id):
        raise PageError(404, *NOT_FOUND)
    return row


def _errors(exc: notation.NotationInvalid) -> dict[str, Any]:
    """The error summary and per-field messages for a template."""
    errors = [{"field_id": e.field_id, "message": e.message} for e in exc.errors]
    return {"errors": errors, "field_errors": {e["field_id"]: e for e in errors}}


# --------------------------------------------------------------------------- /courses


def _courses_page(
    request: Request, user: CurrentUser, extra: dict[str, Any] | None = None, status: int = 200
) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        courses = notation.list_courses(session, user.id)
    context = {"courses": courses, "values": {}, "max_courses": notation.MAX_COURSES}
    return render(request, "courses.html", context | (extra or {}), status_code=status)


@router.get("/courses")
def courses_page(request: Request, user: UserDep) -> Response:
    _require_feature(request)
    return _courses_page(request, user)


@router.post("/courses", dependencies=[Depends(require_csrf)])
def create_course(request: Request, user: UserDep, name: Field = "") -> Response:
    _require_feature(request)
    try:
        with session_scope(get_ctx(request).factory) as session:
            course = notation.create_course(session, user.id, name)
            course_id = course.id
    except notation.NotationInvalid as exc:
        extra = _errors(exc) | {"values": exc.values or {"name": name}}
        return _courses_page(request, user, extra, status=422)
    log_event(logger, "course_created", course_id=course_id)
    flash(request, "Course added. Add the symbols your course uses below.")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


# --------------------------------------------------------------------------- one course


def _course_page(
    request: Request,
    course_id: str,
    user: CurrentUser,
    extra: dict[str, Any] | None = None,
    status: int = 200,
) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        course = _course(session, course_id, user)
        context = {
            "course": notation.CourseView(course.id, course.name, len(course.notation)),
            "entries": notation.entries(course),
            "meanings": notation.MEANINGS,
            "examples": notation.SYMBOL_EXAMPLES,
            "values": {"meaning": "vector"},
            "rename_value": course.name,
        }
    return render(request, "course.html", context | (extra or {}), status_code=status)


@router.get("/courses/{course_id}")
def course_page(request: Request, course_id: str, user: UserDep) -> Response:
    _require_feature(request)
    return _course_page(request, course_id, user)


@router.post("/courses/{course_id}/rename", dependencies=[Depends(require_csrf)])
def rename_course(request: Request, course_id: str, user: UserDep, name: Field = "") -> Response:
    _require_feature(request)
    try:
        with session_scope(get_ctx(request).factory) as session:
            notation.rename_course(session, _course(session, course_id, user), name)
    except notation.NotationInvalid as exc:
        extra = _errors(exc) | {"rename_value": exc.values.get("rename", name)}
        return _course_page(request, course_id, user, extra, status=422)
    log_event(logger, "course_renamed", course_id=course_id)
    flash(request, "Course renamed.")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


def _confirm_delete_page(request: Request, course_id: str, user: CurrentUser) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        course = _course(session, course_id, user)
        context = {"course_id": course.id, "name": course.name, "entries": len(course.notation)}
    return render(request, "course_delete_confirm.html", context)


@router.get("/courses/{course_id}/delete")
def confirm_delete(request: Request, course_id: str, user: UserDep) -> Response:
    _require_feature(request)
    return _confirm_delete_page(request, course_id, user)


@router.post("/courses/{course_id}/delete", dependencies=[Depends(require_csrf)])
def delete_course(request: Request, course_id: str, user: UserDep, confirm: Field = "") -> Response:
    """Delete only with `confirm=yes`; the "Delete course" button gets the question
    first. The documents converted for the course keep their results."""
    _require_feature(request)
    if confirm != "yes":
        return _confirm_delete_page(request, course_id, user)
    with session_scope(get_ctx(request).factory) as session:
        notation.delete_course(session, _course(session, course_id, user))
    log_event(logger, "course_deleted", course_id=course_id)
    flash(request, "The course and its notation have been deleted.")
    return RedirectResponse("/courses", status_code=303)


# --------------------------------------------------------------------------- entries


@router.post("/courses/{course_id}/entries", dependencies=[Depends(require_csrf)])
def add_entry(
    request: Request,
    course_id: str,
    user: UserDep,
    symbol: Field = "",
    meaning: Field = "",
    note: Field = "",
) -> Response:
    _require_feature(request)
    try:
        entry = notation.parse_entry(symbol, meaning, note)
        with session_scope(get_ctx(request).factory) as session:
            notation.add_entry(session, _course(session, course_id, user), entry)
    except notation.NotationInvalid as exc:
        extra = _errors(exc) | {"values": exc.values}
        return _course_page(request, course_id, user, extra, status=422)
    log_event(logger, "notation_added", course_id=course_id, source="user")
    flash(request, f"Added: {phrase(entry.canonical, entry.meaning)}.")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


def _entry_page(
    request: Request,
    course_id: str,
    entry_id: str,
    user: CurrentUser,
    extra: dict[str, Any] | None = None,
    status: int = 200,
) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        course = _course(session, course_id, user)
        row = _entry(course, entry_id)
        view = next(v for v in notation.entries(course) if v.id == row.id)
        context = {
            "course": notation.CourseView(course.id, course.name, len(course.notation)),
            "entry": view,
            "meanings": notation.MEANINGS,
            "examples": notation.SYMBOL_EXAMPLES,
            "values": {"symbol": row.symbol, "meaning": row.meaning, "note": row.note or ""},
        }
    return render(request, "course_entry.html", context | (extra or {}), status_code=status)


@router.get("/courses/{course_id}/entries/{entry_id}")
def entry_page(request: Request, course_id: str, entry_id: str, user: UserDep) -> Response:
    _require_feature(request)
    return _entry_page(request, course_id, entry_id, user)


@router.post("/courses/{course_id}/entries/{entry_id}", dependencies=[Depends(require_csrf)])
def save_entry(
    request: Request,
    course_id: str,
    entry_id: str,
    user: UserDep,
    symbol: Field = "",
    meaning: Field = "",
    note: Field = "",
) -> Response:
    _require_feature(request)
    try:
        entry = notation.parse_entry(symbol, meaning, note)
        with session_scope(get_ctx(request).factory) as session:
            course = _course(session, course_id, user)
            notation.update_entry(session, course, _entry(course, entry_id), entry)
    except notation.NotationInvalid as exc:
        extra = _errors(exc) | {"values": exc.values}
        return _entry_page(request, course_id, entry_id, user, extra, status=422)
    log_event(logger, "notation_changed", course_id=course_id)
    flash(request, f"Saved: {phrase(entry.canonical, entry.meaning)}.")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


@router.post("/courses/{course_id}/entries/{entry_id}/delete", dependencies=[Depends(require_csrf)])
def remove_entry(request: Request, course_id: str, entry_id: str, user: UserDep) -> Response:
    _require_feature(request)
    with session_scope(get_ctx(request).factory) as session:
        course = _course(session, course_id, user)
        row = _entry(course, entry_id)
        words = describe(row.canonical)
        session.delete(row)
    log_event(logger, "notation_removed", course_id=course_id)
    flash(request, f"Removed {words}. To keep it after all, add it again below.")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


# --------------------------------------------------------------------------- remember


@router.post("/courses/{course_id}/remember", dependencies=[Depends(require_csrf)])
def remember(
    request: Request,
    course_id: str,
    user: UserDep,
    job: Field = "",
    item: Field = "",
    symbol: Field = "",
    meaning: Field = "",
) -> Response:
    """Add a letter a review decision settled to the job's course (source "review"),
    then go back to the review screen where the reviewer was."""
    _require_feature(request)
    from a11y_stem.web.routes.split import review_url

    if not JOB_ID.match(job):
        raise PageError(404, *NOT_FOUND)
    item_key = item if ELEMENT_ID.match(item) else None
    back = review_url(job, item=item_key, anchor="decision-title")
    canonical = canonical_symbol(symbol)
    if canonical is None or canonical != symbol or meaning not in notation.MEANINGS:
        raise PageError(400, "That could not be saved", "Go back and try again.")
    with session_scope(get_ctx(request).factory) as session:
        owned = owned_job(session, job, user)
        course = _course(session, course_id, user)
        if owned.course_id != course.id:
            raise PageError(404, *NOT_FOUND)
        entry = notation.EntryInput(
            symbol=describe(canonical),
            canonical=canonical,
            meaning=meaning,
            note=None,
        )
        try:
            notation.add_entry(session, course, entry, source="review")
        except notation.NotationInvalid as exc:
            message = exc.errors[0].message  # already there, or the course is full
        else:
            message = f"Remembered for {course.name}: {phrase(canonical, meaning)}."
            log_event(logger, "notation_added", course_id=course_id, source="review")
    # Remembering is no review write: the Undo shown under a decision's notice would
    # take back the decision before it, so none is offered here.
    flash(request, message, undo=False)
    return RedirectResponse(back, status_code=303)


# --------------------------------------------------------------------------- review screen


def review_context(
    request: Request,
    job_id: str,
    user: CurrentUser,
    result: DocumentResult,
    shown: str | None,
    flashes: list[dict[str, Any]],
) -> dict[str, Any]:
    """For the review screen (`routes/split.py`): `course_notation`, what the job's
    course says about the equation shown (`shown`, an element id), and `remember`, the
    offer to keep a styled letter the last decision settled (the last one-time notice
    names its element). Empty with the feature off."""
    ctx = get_ctx(request)
    if not notation.enabled(ctx.settings):
        return {}
    last = flashes[-1].get("element_id") if flashes else None
    decided = last if isinstance(last, str) and ELEMENT_ID.match(last) else None
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        if job.course_id is None:
            return {}
        fix = result.fix_for(shown) if shown else None
        element = next((e for e in result.elements if e.id == shown), None) if shown else None
        card = notation.card_notation(session, ctx.settings, job, element, fix)
        offer = (
            notation.remember_offer(session, ctx.settings, job, result, decided)
            if decided
            else None
        )
    return {"course_notation": card, "remember": offer}
