"""Your account: what it is, the limits that apply to it, and deleting it (open sign-up).

`GET /account` shows the address and name we hold, the person's access tier and today's
limits. Deleting the account is a confirmation page (`GET /account/delete`, a link, so Back
never offers to send a form again) and a POST with `confirm=yes` that erases everything of
theirs through `ops.retention.erase_user`, the same path as `readablestem users delete`:
every document with everything derived from it (counted first, so the content-free totals
stay right), review decisions, courses and notation, usage events, sign-in identities,
linked addresses, email sign-in links, daily counts, waiting files and the account row. The
person is signed out and the session cleared. What remains holds no content and no link to
them: daily spend and error counts, and the number of accounts made today
(`signup_days`), which is not given back, so making and deleting accounts cannot make room
for more of them.

Signing in again afterwards makes a new, empty account in open mode (and counts as a new
account), and is refused in invitation mode, where deleting removes the person from the
list.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

from readablestem.ops.retention import erase_user
from readablestem.web import access, tombstones
from readablestem.web.database import as_utc, utc_today
from readablestem.web.db import Job, User, session_scope, utcnow
from readablestem.web.deps import CurrentUser, UserDep, flash, get_ctx, render, require_csrf
from readablestem.web.logsafe import log_event
from readablestem.web.quota import next_day_start, pages_left, rereads_used

logger = logging.getLogger(__name__)
router = APIRouter()

DELETED = "Your account and everything in it have been deleted."


def _context(request: Request, user: CurrentUser) -> dict[str, Any]:
    ctx = get_ctx(request)
    s = ctx.settings
    now = utcnow()
    quota = access.page_quota(s, user.access_tier)
    allowance = access.reread_allowance(s, user.access_tier)
    with session_scope(ctx.factory) as session:
        left = pages_left(session, user.id, quota=quota, day=utc_today(now))
        rereads_left = (
            None
            if allowance is None
            else max(0, allowance - rereads_used(session, user.id, utc_today(now)))
        )
        documents = int(
            session.scalar(select(func.count()).select_from(Job).where(Job.user_id == user.id)) or 0
        )
        row = session.get(User, user.id)
        joined = as_utc(row.created_at) if row is not None else None
    return {
        "active": "account",
        "joined": joined,
        "tier_open": access.is_open(user.access_tier),
        "page_quota": quota,
        "pages_left": left,
        "reread_allowance": allowance,
        "rereads_left": rereads_left,
        "documents": documents,
        "is_admin": user.is_admin,
        "provider": s.model_provider,
        "signup_open": s.signup_open,
        "next_reset": next_day_start(now),
    }


@router.get("/account")
def account_page(request: Request, user: UserDep) -> Response:
    return render(request, "account.html", _context(request, user))


def _confirm_page(request: Request, user: CurrentUser) -> Response:
    return render(
        request,
        "account_delete_confirm.html",
        _context(request, user) | {"invitation_only": not get_ctx(request).settings.signup_open},
    )


@router.get("/account/delete")
def confirm_delete(request: Request, user: UserDep) -> Response:
    """The confirmation step on its own: what the "Delete my account" link opens."""
    return _confirm_page(request, user)


@router.post("/account/delete", dependencies=[Depends(require_csrf)])
async def delete_account(
    request: Request, user: UserDep, confirm: Annotated[str, Form()] = ""
) -> Response:
    """Delete only with `confirm=yes`; any other POST gets the confirmation page first.
    Erasing an account cannot be undone, so it is confirmed (WCAG 3.3.4), and it works
    without JavaScript."""
    if confirm != "yes":
        return await run_in_threadpool(_confirm_page, request, user)
    return await run_in_threadpool(_delete_now, request, user)


def _delete_now(request: Request, user: CurrentUser) -> Response:
    settings = get_ctx(request).settings
    with session_scope(get_ctx(request).factory) as session:
        # What the account used today is kept, under a hash of its address, for the rest of
        # the day (`web.tombstones`): deleting it does not reset the day's limits.
        stats = erase_user(session, user.id, tombstone_key=tombstones.tombstone_key(settings))
    log_event(logger, "account_deleted", jobs=stats.jobs_deleted, tier=user.access_tier)
    request.session.clear()  # signed out; the cookie is signed, so nothing else to end
    request.state.user = None
    flash(request, DELETED)
    return RedirectResponse("/", status_code=303)
