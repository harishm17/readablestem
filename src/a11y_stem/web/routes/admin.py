"""Admin page: allowlist, today's spend against the cap, queue depth, aggregate counts.

Shows no document content: no filenames, no results, only emails of people on the
allowlist (which admins manage) and numbers.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

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


def admin_context(request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    since = utc_today() - timedelta(days=AGGREGATE_DAYS)
    with session_scope(ctx.factory) as session:
        users = [
            {
                "email": u.email,
                "is_admin": u.is_admin,
                "last_login_at": as_utc(u.last_login_at),
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
    return {
        "users": users,
        "spend": ctx.guard.snapshot(),
        "queue": {status.value: counts.get(status.value, 0) for status in JobStatus},
        "aggregates": aggregates,
        "aggregate_days": AGGREGATE_DAYS,
        "active": "admin",
        "errors": [],
        "email_error": "",
        "email_value": "",
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
    with session_scope(get_ctx(request).factory) as session:
        # Unticked keeps the person's current role: re-adding an admin must not demote them.
        set_allowed(session, normalised, allowed=True, admin=True if admin == "on" else None)
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
