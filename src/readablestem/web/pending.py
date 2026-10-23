"""Large files waiting for the instructor to choose which pages to convert (T3-3).

A PDF over the page or size limit (30 pages, 20 MB) is not turned away. It is kept for a
while as a `PendingUpload`, and the instructor chooses which pages to convert, in parts that
each fit (`web.routes.parts`). Nothing is converted, counted or sent anywhere until they do:
no pages are taken from the daily count when the file is put aside.

What a pending upload is, and is not:

- It holds the uploaded bytes (up to `MAX_LARGE_UPLOAD_MB`, 30 MB), so it is deleted like every
  other copy of a document: `retain_until` is `RETENTION_HOURS` after it was made, the
  hourly purge deletes it (`ops.retention.purge_pending`), "Discard" deletes it at once, and
  erasing the person deletes it.
- It is not a job. It is never queued, never charged to the daily budget, and does not count
  toward the documents a person may keep (`intake.MAX_LIVE_JOBS_PER_USER`); the cap on
  waiting large files is its own (`MAX_PENDING_PER_USER`, because each can be 30 MB).
- `done_pages` records which pages were already sent to be converted, so choosing the rest
  later (tomorrow, when the daily pages start again) cannot convert a page twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from readablestem.config import Settings
from readablestem.web.database import as_utc
from readablestem.web.db import PendingUpload, utcnow

# A large file is up to 30 MB, so only a few wait at once per person.
MAX_PENDING_PER_USER = 2


class TooManyPending(Exception):
    """The person already has `MAX_PENDING_PER_USER` large files waiting."""


def retain_until(settings: Settings, now: datetime | None = None) -> datetime:
    return (now or utcnow()) + timedelta(hours=settings.retention_hours)


def live(session: Session, user_id: str, now: datetime | None = None) -> list[PendingUpload]:
    """The person's large files still waiting (past their deletion time they count as
    deleted before the purge removes them), newest first."""
    moment = now or utcnow()
    rows = session.scalars(
        select(PendingUpload)
        .where(PendingUpload.user_id == user_id, PendingUpload.retain_until > moment)
        .order_by(PendingUpload.created_at.desc(), PendingUpload.id)
    )
    return list(rows)


def create(
    session: Session,
    settings: Settings,
    *,
    user_id: str,
    filename: str,
    data: bytes,
    pages: int,
    mode: str,
    course_id: str | None,
    exam: bool,
    image_pages: bool = False,
    now: datetime | None = None,
) -> PendingUpload:
    """Keep a large file for the person to choose its pages. Raises `TooManyPending`."""
    moment = now or utcnow()
    waiting = session.scalar(
        select(func.count())
        .select_from(PendingUpload)
        .where(PendingUpload.user_id == user_id, PendingUpload.retain_until > moment)
    )
    if int(waiting or 0) >= MAX_PENDING_PER_USER:
        raise TooManyPending
    row = PendingUpload(
        user_id=user_id,
        filename=filename,
        pages=pages,
        size_bytes=len(data),
        content=data,
        course_id=course_id,
        exam_mode=exam,
        image_pages=image_pages,
        retention_mode=mode,
        done_pages=[],
        created_at=moment,
        retain_until=retain_until(settings, moment),
    )
    session.add(row)
    session.flush()
    return row


def is_expired(row: PendingUpload, now: datetime | None = None) -> bool:
    until = as_utc(row.retain_until)
    return until is not None and until <= (now or utcnow())
