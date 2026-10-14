"""Admin page: allowlist, today's spend against the cap, queue depth, aggregate counts,
usage counts.

Shows no document content: no filenames, no results, only emails of people on the
allowlist (which admins manage) and numbers. Usage is shown per pseudonymous person (the
start of their random id), never next to an email.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

from a11y_stem.web import events, linked_emails
from a11y_stem.web.auth import normalise_email, set_allowed
from a11y_stem.web.database import as_utc, utc_today
from a11y_stem.web.db import ErrorAggregate, Job, JobStatus, User, session_scope
from a11y_stem.web.deps import (
    AdminDep,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from a11y_stem.web.logsafe import log_event

logger = logging.getLogger(__name__)
router = APIRouter()

AGGREGATE_DAYS = 14
AGGREGATE_ROWS = 200
USAGE_DAYS = 28  # four weeks: the return-visit measure of the beta


def admin_context(request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    since = utc_today() - timedelta(days=AGGREGATE_DAYS)
    with session_scope(ctx.factory) as session:
        users = [
            {
                "email": u.email,
                "is_admin": u.is_admin,
                "last_login_at": as_utc(u.last_login_at),
                "linked": linked_emails.linked_to(session, u.id),
            }
            for u in session.scalars(
                select(User).where(User.is_allowed.is_(True)).order_by(User.email)
            )
        ]
        rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
        counts = {str(status): int(count) for status, count in rows}
        aggregates = session.execute(
            select(
                ErrorAggregate.day,
                ErrorAggregate.step,
                ErrorAggregate.category,
                ErrorAggregate.count,
            )
            .where(ErrorAggregate.day >= since)
            .order_by(ErrorAggregate.day.desc(), ErrorAggregate.step, ErrorAggregate.category)
            .limit(AGGREGATE_ROWS)
        ).all()
        # Usage events (web/events.py): counts per day and per pseudonymous person.
        usage = events.usage(session, days=USAGE_DAYS)
    return {
        "usage": usage,
        "usage_columns": {"day": events.DAY_COLUMNS, "person": events.PERSON_COLUMNS},
        "events_on": ctx.settings.events_enabled,
        "event_days": events.EVENT_RETENTION_DAYS,
        "users": users,
        "spend": ctx.guard.snapshot(),
        "queue": {status.value: counts.get(status.value, 0) for status in JobStatus},
        "aggregates": aggregates,
        "aggregate_days": AGGREGATE_DAYS,
        "active": "admin",
        "errors": [],
        "email_error": "",
        "email_value": "",
        "link_error": "",
        "link_owner_value": "",
        "link_other_value": "",
    }


@router.get("/admin")
def admin_page(request: Request, user: AdminDep) -> Response:
    return render(request, "admin.html", admin_context(request))


@router.post("/admin/allowlist", dependencies=[Depends(require_csrf)])
def allow(
    request: Request,
    user: AdminDep,
    email: Annotated[str, Form()] = "",
    admin: Annotated[str, Form()] = "",
) -> Response:
    normalised = normalise_email(email)
    if normalised is None:
        message = "Enter an email address, like name@example.org."
        context = admin_context(request) | {
            "errors": [{"field_id": "allow-email", "message": message}],
            "email_error": message,
            "email_value": email,
        }
        return render(request, "admin.html", context, status_code=422)
    try:
        with session_scope(get_ctx(request).factory) as session:
            # Unticked keeps the person's current role: re-adding an admin must not demote them.
            set_allowed(session, normalised, allowed=True, admin=True if admin == "on" else None)
    except linked_emails.LinkRefused as refused:
        context = admin_context(request) | {
            "errors": [{"field_id": "allow-email", "message": refused.message}],
            "email_error": refused.message,
            "email_value": email,
        }
        return render(request, "admin.html", context, status_code=422)
    log_event(logger, "allowlist_add", by=user.id, admin=admin == "on")
    flash(request, "The allowlist was updated.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/allowlist/remove", dependencies=[Depends(require_csrf)])
def remove(request: Request, user: AdminDep, email: Annotated[str, Form()] = "") -> Response:
    normalised = normalise_email(email)
    if normalised == user.email:
        flash(request, "You cannot remove yourself. Ask another admin.")
        return RedirectResponse("/admin", status_code=303)
    if normalised is not None:
        with session_scope(get_ctx(request).factory) as session:
            set_allowed(session, normalised, allowed=False)
        log_event(logger, "allowlist_remove", by=user.id)
    flash(request, "The allowlist was updated.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/linked-emails", dependencies=[Depends(require_csrf)])
def link_email(
    request: Request,
    user: AdminDep,
    email: Annotated[str, Form()] = "",
    other: Annotated[str, Form()] = "",
) -> Response:
    """Let an allowed instructor also sign in with another Google address (T3-10)."""
    owner, linked = normalise_email(email), normalise_email(other)
    message = ""
    if owner is None or linked is None:
        message = "Enter both email addresses, like name@example.org."
    else:
        try:
            with session_scope(get_ctx(request).factory) as session:
                linked_emails.link(session, owner, linked)
        except linked_emails.LinkRefused as refused:
            message = refused.message
    if message:
        context = admin_context(request) | {
            "errors": [{"field_id": "link-other", "message": message}],
            "link_error": message,
            "link_owner_value": email,
            "link_other_value": other,
        }
        return render(request, "admin.html", context, status_code=422)
    log_event(logger, "linked_email_add", by=user.id)
    flash(request, "The other Google account was linked.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/linked-emails/remove", dependencies=[Depends(require_csrf)])
def unlink_email(request: Request, user: AdminDep, other: Annotated[str, Form()] = "") -> Response:
    linked = normalise_email(other)
    if linked is not None:
        with session_scope(get_ctx(request).factory) as session:
            linked_emails.unlink(session, linked)
        log_event(logger, "linked_email_remove", by=user.id)
    flash(request, "The other Google account was removed. That instructor was signed out.")
    return RedirectResponse("/admin", status_code=303)
