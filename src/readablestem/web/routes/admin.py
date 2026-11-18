"""Admin page: sign-up and the allowlist, today's spend against the cap (and the open-tier
pool), queue depth, aggregate counts, usage counts.

Shows no document content: no filenames, no results, only emails of the people who have an
account (which admins manage) and numbers. Each person's access tier is shown ("Invited" or
"Open", `web.access`); an admin can block and unblock anyone (`auth.set_blocked`), and in
either sign-up mode a blocked person's sign-in is refused. Usage is shown per pseudonymous
person (the start of their random id), never next to an email.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from readablestem.ops import profile, step_report
from readablestem.web import access, events, linked_emails, reports, shares, states, step_facts
from readablestem.web.auth import normalise_email, set_allowed, set_blocked
from readablestem.web.database import as_utc, utc_today
from readablestem.web.db import ErrorAggregate, Job, QuotaDay, User, session_scope
from readablestem.web.deps import (
    AdminDep,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from readablestem.web.forms import submit_once
from readablestem.web.health import ops_report
from readablestem.web.logsafe import log_event
from readablestem.web.quota import signups_today

logger = logging.getLogger(__name__)
router = APIRouter()

AGGREGATE_DAYS = 14
AGGREGATE_ROWS = 200
USAGE_DAYS = 28  # four weeks: the return-visit measure of the beta
# Rows of each people table, newest accounts first. With open sign-up there can be thousands
# of accounts: the page lists the newest and says so, and the "Block by email" form reaches
# any account, listed or not.
PEOPLE_ROWS = 300
# A second Link for a link already made (SW43).
LINK_EXISTS = "That address was already linked to this instructor. Nothing was changed."
# What the admin is told, by what happened (the shape of `routes/sharing.SHARE_OUTCOME`): no
# address is in a notice, and nothing reports success when no row changed. A change names
# what changed, in `{what}`.
ALLOW_OUTCOME = {
    shares.ADDED: "Added to the allowlist. Nothing was sent: tell them to sign in.",
    shares.CHANGED: "Changed: {what}.",
    shares.UNCHANGED: "Nothing changed: that address was already allowed.",
}
NOTHING = "Nothing changed: "
NO_ADDRESS = NOTHING + "enter an email address, like name@example.org."
NO_ACCOUNT = NOTHING + "nobody has an account under that address."
SPEND_PEOPLE_ROWS = 20  # the biggest spenders shown in "Model spend by person"
# "Model work of recent documents": the newest documents finished in this many days.
STEP_DAYS = 7
STEP_JOBS = 50


def admin_context(request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    since = utc_today() - timedelta(days=AGGREGATE_DAYS)
    with session_scope(ctx.factory) as session:
        today = utc_today()
        listed = list(
            session.scalars(
                select(User)
                .where(User.is_allowed.is_(True))
                .order_by(User.created_at.desc(), User.email)
                .limit(PEOPLE_ROWS)
            )
        )
        # What each person's jobs cost (`quota_days.spent_usd`): the biggest spenders today and
        # over every day kept, by pseudonym like the usage tables, never next to an email.
        on_today = func.sum(case((QuotaDay.day == today, QuotaDay.spent_usd), else_=0.0))
        total = func.sum(QuotaDay.spent_usd)
        spend_people = [
            {
                "person": events.pseudonym(str(user_id)),
                "today": float(today_usd or 0.0),
                "total": float(total_usd or 0.0),
            }
            for user_id, today_usd, total_usd in session.execute(
                select(QuotaDay.user_id, on_today, total)
                .group_by(QuotaDay.user_id)
                .having(total > 0)
                .order_by(on_today.desc(), total.desc(), QuotaDay.user_id)
                .limit(SPEND_PEOPLE_ROWS)
            )
        ]
        users = [
            {
                "email": u.email,
                "is_admin": u.is_admin,
                "tier": u.access_tier,
                "last_login_at": as_utc(u.last_login_at),
                "linked": linked_emails.linked_to(session, u.id),
            }
            for u in listed
        ]
        blocked = [
            {
                "email": u.email,
                "tier": u.access_tier,
                "last_login_at": as_utc(u.last_login_at),
            }
            for u in session.scalars(
                select(User)
                .where(User.is_allowed.is_(False))
                .order_by(User.created_at.desc(), User.email)
                .limit(PEOPLE_ROWS)
            )
        ]
        by_group = {
            (bool(allowed), str(tier)): int(n)
            for allowed, tier, n in session.execute(
                select(User.is_allowed, User.access_tier, func.count()).group_by(
                    User.is_allowed, User.access_tier
                )
            )
        }
        signup = {
            "open": ctx.settings.signup_open,
            "today": (accounts_today := signups_today(session, today)),
            "limit": ctx.settings.max_new_accounts_per_day,
            "full": accounts_today >= ctx.settings.max_new_accounts_per_day,
            "invited": by_group.get((True, access.INVITED), 0),
            "open_accounts": by_group.get((True, access.OPEN), 0),
            "blocked": sum(n for (allowed, _), n in by_group.items() if not allowed),
            "page_quota": ctx.settings.open_tier_daily_page_quota,
            "rereads": ctx.settings.open_tier_daily_rereads,
        }
        # The queue in the words of every other page (F-213): how many jobs are in each
        # state of the table (`web.states`), in the table's order.
        snapshot = ctx.guard.snapshot()
        closed = (
            snapshot.remaining_usd is not None and snapshot.remaining_usd <= ctx.min_headroom_usd
        )
        found = states.load(
            session, session.scalars(select(Job.id)), context=states.Context(budget_closed=closed)
        )
        order = list(states.TABLE)
        queue: dict[str, int] = {}
        for state in sorted(found.values(), key=lambda s: order.index(s.key)):
            queue[state.label] = queue.get(state.label, 0) + 1
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
        # What recent documents took of the models and the clock (`ops.step_report`).
        steps = step_report.report(
            step_facts.measured(
                session, since=datetime.now(UTC) - timedelta(days=STEP_DAYS), last=STEP_JOBS
            )
        )
    shipped = profile.load()
    return {
        # The production profile (ops/profile.py): names only, never a value.
        "profile": {
            "name": shipped.name,
            "hash": shipped.short_hash,
            "confirmed": shipped.confirmed,
            "differs": shipped.differences(ctx.settings),
        },
        "usage": usage,
        "usage_columns": {"day": events.DAY_COLUMNS, "person": events.PERSON_COLUMNS},
        "events_on": ctx.settings.events_enabled,
        "event_days": events.EVENT_RETENTION_DAYS,
        "users": users,
        "blocked": blocked,
        "signup": signup,
        "people_rows": PEOPLE_ROWS,
        "spend": ctx.guard.snapshot(),
        "spend_people": spend_people,
        "spend_people_rows": SPEND_PEOPLE_ROWS,
        "steps": steps,
        "step_days": STEP_DAYS,
        "step_jobs": STEP_JOBS,
        "queue": queue,
        "aggregates": aggregates,
        "aggregate_days": AGGREGATE_DAYS,
        "ops": ops_report(ctx)[1],
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


@router.get("/admin/reports")
def admin_reports(request: Request, user: AdminDep) -> Response:
    """Problem reports from "Report this item" and the stopped-document link (`web.reports`),
    open ones first, with the sender's account. Ids, codes and what the sender typed (shown
    escaped, never as a link); nothing from a document."""
    with session_scope(get_ctx(request).factory) as session:
        rows = [
            {
                "id": report.id,
                "open": report.status == reports.OPEN,
                "kind": report.kind,
                "sender": email,
                "job_ref": report.job_ref,
                "item_ref": report.item_ref,
                "item_kind": report.item_kind,
                "page": report.page,
                "code": report.code,
                "version": report.pipeline_version,
                "text": report.text,
                "created_at": as_utc(report.created_at),
                "closed_at": as_utc(report.closed_at),
            }
            for report, email in reports.listed(session)
        ]
    context = {
        "rows": rows,
        "open_count": sum(1 for row in rows if row["open"]),
        "closed_days": reports.CLOSED_DAYS,
        "max_days": reports.MAX_DAYS,
        "active": "admin",
    }
    return render(request, "admin_reports.html", context)


@router.post("/admin/reports/{report_id}/close", dependencies=[Depends(require_csrf)])
@submit_once("admin.report_close")
def close_report(request: Request, user: AdminDep, report_id: int) -> Response:
    with session_scope(get_ctx(request).factory) as session:
        closed = reports.close(session, report_id)
    if closed:
        log_event(logger, "report_closed", by=user.id)
    flash(
        request,
        "Closed." if closed else NOTHING + "that report is already closed or does not exist.",
        undo=False,
    )
    return RedirectResponse("/admin/reports", status_code=303)


@router.get("/admin/health")
def admin_health(request: Request, user: AdminDep) -> Response:
    """What `/healthz/ops` used to show everyone: which scheduled tasks are late, the stored
    megabytes against the limit and the sandbox probe. Admin only; 503 while a task is late,
    like the public route."""
    ok, details = ops_report(get_ctx(request))
    return JSONResponse(
        details, status_code=200 if ok else 503, headers={"Cache-Control": "no-store"}
    )


@router.post("/admin/allowlist", dependencies=[Depends(require_csrf)])
@submit_once("admin.allow")
def allow(
    request: Request,
    user: AdminDep,
    email: Annotated[str, Form()] = "",
    admin: Annotated[str, Form()] = "",
) -> Response:
    normalised = normalise_email(email)
    if normalised is None:
        message = NO_ADDRESS
        context = admin_context(request) | {
            "errors": [{"field_id": "allow-email", "message": message}],
            "email_error": message,
            "email_value": email,
        }
        return render(request, "admin.html", context, status_code=422)
    try:
        with session_scope(get_ctx(request).factory) as session:
            outcome, what = _allow(session, normalised, admin=True if admin == "on" else None)
    except linked_emails.LinkRefused as refused:
        message = NOTHING + refused.message[:1].lower() + refused.message[1:]
        context = admin_context(request) | {
            "errors": [{"field_id": "allow-email", "message": message}],
            "email_error": message,
            "email_value": email,
        }
        return render(request, "admin.html", context, status_code=422)
    if outcome != shares.UNCHANGED:
        log_event(logger, "allowlist_add", by=user.id, admin=admin == "on")
    flash(request, ALLOW_OUTCOME[outcome].format(what=what), undo=False)
    return RedirectResponse("/admin", status_code=303)


def _allow(session: Session, email: str, *, admin: bool | None) -> tuple[str, str]:
    """Allow `email` (unticked `admin` keeps the person's current role: re-adding an admin
    must not demote them) and say what happened: `ADDED` (a new account), `CHANGED` (and what
    changed) or `UNCHANGED`."""
    before = session.scalar(select(User).where(User.email == email))
    was = None if before is None else (before.is_allowed, before.access_tier, before.is_admin)
    after = set_allowed(session, email, allowed=True, admin=admin)
    if was is None or after is None:
        return shares.ADDED, ""
    changes = []
    if not was[0]:
        changes.append("they can sign in again")
    if was[1] != after.access_tier:
        changes.append("they are now invited, with the instructors' limits")
    if was[2] != after.is_admin:
        changes.append("they are now an admin" if after.is_admin else "they are no longer an admin")
    return (shares.CHANGED, "; ".join(changes)) if changes else (shares.UNCHANGED, "")


@router.post("/admin/allowlist/remove", dependencies=[Depends(require_csrf)])
@submit_once("admin.remove")
def remove(request: Request, user: AdminDep, email: Annotated[str, Form()] = "") -> Response:
    normalised = normalise_email(email)
    if normalised == user.email:
        flash(request, "You cannot remove yourself. Ask another admin.", undo=False)
        return RedirectResponse("/admin", status_code=303)
    if normalised is None:
        message = NO_ADDRESS
    else:
        with session_scope(get_ctx(request).factory) as session:
            was = _was_allowed(session, normalised)
            if was:
                set_allowed(session, normalised, allowed=False)
        if was:
            log_event(logger, "allowlist_remove", by=user.id)
            message = "Removed from the allowlist. They were signed out and their work stopped."
        else:
            message = (
                NO_ACCOUNT
                if was is None
                else NOTHING + "that address was already off the allowlist."
            )
    flash(request, message, undo=False)
    return RedirectResponse("/admin", status_code=303)


def _was_allowed(session: Session, email: str) -> bool | None:
    """Whether the account may sign in now; None when nobody has that address."""
    return session.scalar(select(User.is_allowed).where(User.email == email))


def _block(request: Request, user: AdminDep, email: str, *, blocked: bool) -> Response:
    normalised = normalise_email(email)
    if blocked and normalised == user.email:
        flash(request, "You cannot block yourself. Ask another admin.", undo=False)
        return RedirectResponse("/admin", status_code=303)
    if normalised is None:
        message = NO_ADDRESS
    else:
        with session_scope(get_ctx(request).factory) as session:
            was = _was_allowed(session, normalised)
            if was is not None and was == blocked:  # allowed to be blocked, blocked to be let in
                set_blocked(session, normalised, blocked=blocked)
        if was is None:
            message = NO_ACCOUNT
        elif was != blocked:
            message = NOTHING + (
                "that account was already blocked." if blocked else "that account was not blocked."
            )
        else:
            log_event(logger, "user_blocked" if blocked else "user_unblocked", by=user.id)
            message = "The account was blocked." if blocked else "The account was unblocked."
    flash(request, message, undo=False)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/block", dependencies=[Depends(require_csrf)])
@submit_once("admin.block")
def block(request: Request, user: AdminDep, email: Annotated[str, Form()] = "") -> Response:
    """Stop an account: signed out everywhere, its queued and running jobs stopped, and its
    sign-ins refused (in either sign-up mode) until it is unblocked."""
    return _block(request, user, email, blocked=True)


@router.post("/admin/users/unblock", dependencies=[Depends(require_csrf)])
@submit_once("admin.unblock")
def unblock(request: Request, user: AdminDep, email: Annotated[str, Form()] = "") -> Response:
    """Let a blocked account sign in again, with the tier it had."""
    return _block(request, user, email, blocked=False)


@router.post("/admin/linked-emails", dependencies=[Depends(require_csrf)])
@submit_once("admin.link")
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
        message = NOTHING + "enter both email addresses, like name@example.org."
    else:
        try:
            with session_scope(get_ctx(request).factory) as session:
                linked_emails.link(session, owner, linked)
        except linked_emails.LinkRefused as refused:
            message = NOTHING + refused.message[:1].lower() + refused.message[1:]
            if refused.code == "already_linked" and _linked_to(request, linked, owner):
                # This very link is there already (often a second click): nothing to fix
                # (SW43, "identical repeat").
                flash(request, LINK_EXISTS, undo=False)
                return RedirectResponse("/admin", status_code=303)
    if message:
        context = admin_context(request) | {
            "errors": [{"field_id": "link-other", "message": message}],
            "link_error": message,
            "link_owner_value": email,
            "link_other_value": other,
        }
        return render(request, "admin.html", context, status_code=422)
    log_event(logger, "linked_email_add", by=user.id)
    flash(request, "The other Google account was linked.", undo=False)
    return RedirectResponse("/admin", status_code=303)


def _linked_to(request: Request, linked: str, owner: str) -> bool:
    """`linked` is linked to the instructor `owner` already."""
    with session_scope(get_ctx(request).factory) as session:
        user = session.scalar(select(User).where(User.email == owner))
        return user is not None and linked in linked_emails.linked_to(session, user.id)


@router.post("/admin/linked-emails/remove", dependencies=[Depends(require_csrf)])
@submit_once("admin.unlink")
def unlink_email(request: Request, user: AdminDep, other: Annotated[str, Form()] = "") -> Response:
    linked = normalise_email(other)
    if linked is None:
        message = NO_ADDRESS
    else:
        with session_scope(get_ctx(request).factory) as session:
            removed = linked_emails.unlink(session, linked)
        if removed:
            log_event(logger, "linked_email_remove", by=user.id)
            message = "The other Google account was removed. That instructor was signed out."
        else:
            message = NOTHING + "that address was not linked to anyone."
    flash(request, message, undo=False)
    return RedirectResponse("/admin", status_code=303)
