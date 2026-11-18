"""Per-user daily page quota (abuse limit), counted at upload time.

One `quota_days` row per (user, UTC day). Pages are added with a single conditional
UPDATE (`... WHERE pages + n <= quota`), so two uploads racing each other cannot both
squeeze under the limit: the database decides, not a read-then-write in Python.

Pages are not refunded when a job fails or is deleted: the quota limits what a user can
submit, and refunds would let a failing upload be retried without bound. The one exception
is a document canceled before a worker began it, on the day it was counted (`give_back_pages`);
converting it again counts its pages again (`routes.jobs.retry_job`).

The same row counts the model reads started from the review screen on a person's documents
(`try_consume_reread`: a region marked as an equation, a table cell read as maths, "Read
again" on an equation; an open-tier account has an allowance, an invited one only the count),
and one `signup_days` row per UTC day counts the accounts open
sign-up made (`try_take_signup`). All three are one conditional UPDATE, so the database
decides who is over the limit. A page and a sign-up are not given back: what is counted is
what was asked for. A read that comes back with nothing is given back
(`give_back_reread`). Which limit a person has depends on their tier (`web.access`).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from markupsafe import Markup
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from readablestem.web.database import insert_ignore, rowcount
from readablestem.web.db import QuotaDay, SignupDay
from readablestem.web.logsafe import log_event

logger = logging.getLogger(__name__)


# The rule, in one sentence for every page that shows an allowance. The clause "A document
# you cancel before it starts does" joins it when cancelling gives pages back (WS11.3a): a
# sentence that says more than the code does would be untrue.
PAGES_RULE = "Pages count when you upload. A failed or deleted document does not give them back."


def allowance_phrase(
    used: int, total: int | None, unit: str, resets_at: str | Markup | None = None
) -> str:
    """One allowance in one order, used then total then left: "0 of 150 pages used today,
    150 left. Resets at 4:00 PM." `unit` is the singular ("page", "model read"); `resets_at`
    is the time already written for the page (the `utc` filter's `<time>` or plain text).
    With no total: "12 pages used today, no limit set." Never "left of total"."""
    plural = unit if total == 1 else f"{unit}s"
    if total is None:
        text = f"{used} {unit if used == 1 else unit + 's'} used today, no limit set."
    else:
        text = f"{used} of {total} {plural} used today, {max(0, total - used)} left."
    if resets_at:
        return Markup("{} Resets {}.").format(text, resets_at)
    return text


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


def give_back_pages(session: Session, user_id: str, pages: int, *, day: date) -> bool:
    """Take `pages` off the user's count for `day`: the document was canceled before any worker
    began it (`routes.jobs.cancel_job`). The one case where pages come back; never below zero,
    so a day that was already reset gives nothing back."""
    stmt = (
        update(QuotaDay)
        .where(QuotaDay.user_id == user_id, QuotaDay.day == day, QuotaDay.pages >= pages)
        .values(pages=QuotaDay.pages - pages)
        .execution_options(synchronize_session=False)
    )
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


@dataclass(frozen=True)
class Wait:
    """A refusal's "come back later" in both its forms, made from one number so the header
    and the sentence cannot disagree: `header` is the `Retry-After` value (whole seconds),
    `words` completes "Try again ...": "at midnight UTC" for the daily reset, else "in about
    5 minutes"."""

    seconds: int
    daily: bool = False

    @property
    def header(self) -> str:
        return str(self.seconds)

    @property
    def words(self) -> str:
        if self.daily:
            return "at midnight UTC"
        if self.seconds < 60:
            return f"in {self.seconds} second{'' if self.seconds == 1 else 's'}"
        minutes = round(self.seconds / 60)
        if minutes < 60:
            about = "" if self.seconds % 60 == 0 else "about "
            return f"in {about}{'a minute' if minutes == 1 else f'{minutes} minutes'}"
        hours = round(self.seconds / 3600)
        about = "" if self.seconds % 3600 == 0 else "about "
        return f"in {about}{'an hour' if hours == 1 else f'{hours} hours'}"


def retry_after(seconds: float | None = None, *, now: datetime | None = None) -> Wait:
    """The wait a refusal states and sends as `Retry-After`. With `seconds` (what a rate
    limiter or a fixed pause knows): that many, at least one. With none: until the daily
    allowance and the day's spend cap start again (`next_day_start`), the time the page
    quota really resets at. Every refusal that tells a person to come back makes it here."""
    if seconds is not None:
        return Wait(max(1, math.ceil(seconds)))
    moment = now or datetime.now(UTC)
    return Wait(max(1, math.ceil((next_day_start(moment) - moment).total_seconds())), daily=True)


def try_consume_reread(session: Session, user_id: str, *, allowance: int | None, day: date) -> bool:
    """Count one model read from review against the user's allowance for `day`. False
    (nothing changed) when the allowance is used up; with no allowance (an invited account)
    the read is counted and never refused. Same pattern as `try_consume_pages`, on the
    same row."""
    insert_ignore(session, QuotaDay, {"user_id": user_id, "day": day, "pages": 0, "rereads": 0})
    within = [] if allowance is None else [QuotaDay.rereads + 1 <= allowance]
    stmt = (
        update(QuotaDay)
        .where(QuotaDay.user_id == user_id, QuotaDay.day == day, *within)
        .values(rereads=QuotaDay.rereads + 1)
        .execution_options(synchronize_session=False)
    )
    return rowcount(session.execute(stmt)) == 1


def give_back_reread(session: Session, user_id: str, *, day: date) -> None:
    """Take one model read off the user's count for `day`: the read came back with nothing, so
    it did not use the allowance. Never goes below zero."""
    session.execute(
        update(QuotaDay)
        .where(QuotaDay.user_id == user_id, QuotaDay.day == day, QuotaDay.rereads >= 1)
        .values(rereads=QuotaDay.rereads - 1)
        .execution_options(synchronize_session=False)
    )


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
    if rowcount(session.execute(stmt)) == 1:
        return True
    _warn_signups_full_once(session, day)
    return False


def _warn_signups_full_once(session: Session, day: date) -> None:
    """The first refusal of the day logs `signup_cap_reached` (a WARNING with a category code,
    nothing about the person); later ones do not. The flag is one conditional UPDATE, so two
    processes refusing at once log once between them. It lives in the caller's transaction,
    which commits on a refusal (the sign-in returns normally)."""
    won = session.execute(
        update(SignupDay)
        .where(SignupDay.day == day, SignupDay.cap_warned.is_(False))
        .values(cap_warned=True)
        .execution_options(synchronize_session=False)
    )
    if rowcount(won) == 1:
        log_event(logger, "signup_cap_reached", logging.WARNING, code="daily_cap")


def signups_today(session: Session, day: date) -> int:
    return int(session.scalar(select(SignupDay.accounts).where(SignupDay.day == day)) or 0)
