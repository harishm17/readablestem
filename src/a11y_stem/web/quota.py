"""Per-user daily page quota (abuse limit), counted at upload time.

One `quota_days` row per (user, UTC day). Pages are added with a single conditional
UPDATE (`... WHERE pages + n <= quota`), so two uploads racing each other cannot both
squeeze under the limit: the database decides, not a read-then-write in Python.

Pages are not refunded when a job fails or is deleted: the quota limits what a user can
submit, and refunds would let a failing upload be retried without bound.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from a11y_stem.web.database import insert_ignore, rowcount
from a11y_stem.web.db import QuotaDay


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
