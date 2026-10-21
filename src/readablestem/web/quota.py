"""Per-user daily page quota (abuse limit), counted at upload time.

One `quota_days` row per (user, UTC day). Pages are added with a single conditional
UPDATE (`... WHERE pages + n <= quota`), so two uploads racing each other cannot both
squeeze under the limit: the database decides, not a read-then-write in Python.

Pages are not refunded when a job fails or is deleted: the quota limits what a user can
submit, and refunds would let a failing upload be retried without bound.

The same row counts the model reads an open-tier account starts from the review screen
(`try_consume_reread`: a region marked as an equation, a table cell read as maths), and one
`signup_days` row per UTC day counts the accounts open sign-up made (`try_take_signup`). All
three are one conditional UPDATE, so the database decides who is over the limit, and none is
given back: what is counted is what was asked for, not what came of it. Which limit a person
has depends on their tier (`web.access`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from readablestem.web.database import insert_ignore, rowcount
from readablestem.web.db import QuotaDay, SignupDay


def try_consume_pages(
    session: Session, user_id: str, pages: int, *, quota: int | None, day: date
) -> bool:
    """Add `pages` to the user's count for `day`. False (nothing changed) if over quota.

    `quota=None` means unlimited (local development); pages are still counted so the
    numbers look the same as in production. Runs inside the caller's transaction, so the
    quota is only spent if the job row is committed too.
    """
    if pages < 0:
        raise ValueError("pages must be >= 0")
    insert_ignore(session, QuotaDay, {"user_id": user_id, "day": day, "pages": 0})
    stmt = (
        update(QuotaDay)
        .where(QuotaDay.user_id == user_id, QuotaDay.day == day)
        .values(pages=QuotaDay.pages + pages)
        .execution_options(synchronize_session=False)
    )
    if quota is not None:
        stmt = stmt.where(QuotaDay.pages + pages <= quota)
    return rowcount(session.execute(stmt)) == 1


def pages_used(session: Session, user_id: str, day: date) -> int:
    used = session.scalar(
        select(QuotaDay.pages).where(QuotaDay.user_id == user_id, QuotaDay.day == day)
    )
    return int(used or 0)


def pages_left(session: Session, user_id: str, *, quota: int | None, day: date) -> int | None:
    """Pages the user may still submit today; None when there is no quota."""
    if quota is None:
        return None
    return max(0, quota - pages_used(session, user_id, day))


def next_day_start(now: datetime) -> datetime:
    """The next midnight UTC: when the page quota and the daily spend cap start again
    (pages show it in the reader's own time: the `utc` template filter and app.js)."""
    tomorrow = now.astimezone(UTC).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(), tzinfo=UTC)


def try_consume_reread(session: Session, user_id: str, *, allowance: int, day: date) -> bool:
    """Count one model read from review against the user's allowance for `day`. False
    (nothing changed) when the allowance is used up. Same pattern as `try_consume_pages`,
    on the same row."""
    insert_ignore(session, QuotaDay, {"user_id": user_id, "day": day, "pages": 0, "rereads": 0})
    stmt = (
        update(QuotaDay)
        .where(
            QuotaDay.user_id == user_id,
            QuotaDay.day == day,
            QuotaDay.rereads + 1 <= allowance,
        )
        .values(rereads=QuotaDay.rereads + 1)
        .execution_options(synchronize_session=False)
    )
    return rowcount(session.execute(stmt)) == 1


def rereads_used(session: Session, user_id: str, day: date) -> int:
    used = session.scalar(
        select(QuotaDay.rereads).where(QuotaDay.user_id == user_id, QuotaDay.day == day)
    )
    return int(used or 0)


def try_take_signup(session: Session, *, limit: int, day: date) -> bool:
    """Count one new account against today's `limit`. False (nothing changed) when sign-ups
    are full for the day. Runs inside the caller's transaction: if the account is not
    committed, the count is not either."""
    insert_ignore(session, SignupDay, {"day": day, "accounts": 0})
    stmt = (
        update(SignupDay)
        .where(SignupDay.day == day, SignupDay.accounts + 1 <= limit)
        .values(accounts=SignupDay.accounts + 1)
        .execution_options(synchronize_session=False)
    )
    return rowcount(session.execute(stmt)) == 1


def signups_today(session: Session, day: date) -> int:
    return int(session.scalar(select(SignupDay.accounts).where(SignupDay.day == day)) or 0)
