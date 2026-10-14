"""Putting an accepted upload in the queue: the per-person caps, the daily page count and the
job row, in one transaction (shared by the upload route and the page chooser for large files).

The caps are the launch audit's (docs/security.md), and they live here so no route that
makes jobs can skip them:

- `MAX_ACTIVE_JOBS_PER_USER`: documents waiting or being converted. A person with a full
  queue would hold everyone else back.
- `MAX_LIVE_JOBS_PER_USER`: documents kept (finished or not) until their deletion time. A
  person with hundreds of kept files would fill the database. The sample document
  (`Job.is_sample`) is not counted: it is ours, it costs nothing, and it must not take a
  place from the person's own files.
- the daily page quota (`web.quota.try_consume_pages`): the pages of this document, added by
  one conditional UPDATE in the same transaction as the job, so a refusal costs nothing and
  two uploads racing cannot both squeeze under the limit.

Nothing here reads the file: the bytes were checked before they got here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.web import retention
from a11y_stem.web.database import utc_today
from a11y_stem.web.db import Blob, Job, JobStatus, session_scope, utcnow
from a11y_stem.web.quota import pages_left, try_consume_pages

# Per person, on top of the daily page count: documents waiting or being converted, and
# documents kept (finished or not, until their deletion time).
MAX_ACTIVE_JOBS_PER_USER = 10
MAX_LIVE_JOBS_PER_USER = 40


def live_job_count(session: Session, user_id: str, now: datetime, *, excluding: str = "") -> int:
    """The documents the person is keeping now: finished or not, until their deletion time
    (what `MAX_LIVE_JOBS_PER_USER` caps), without the job `excluding` and without a
    sample."""
    query = (
        select(func.count())
        .select_from(Job)
        .where(Job.user_id == user_id, Job.retain_until > now, Job.is_sample.is_(False))
    )
    if excluding:
        query = query.where(Job.id != excluding)
    return int(session.scalar(query) or 0)


def active_job_count(session: Session, user_id: str, *, excluding: str = "") -> int:
    """The documents waiting or being converted (what `MAX_ACTIVE_JOBS_PER_USER` caps)."""
    query = (
        select(func.count())
        .select_from(Job)
        .where(Job.user_id == user_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
    )
    if excluding:
        query = query.where(Job.id != excluding)
    return int(session.scalar(query) or 0)


class TooManyJobs(Exception):
    """The person already has as many documents as they may (`store_job`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class QuotaRefused(Exception):
    """The upload has more pages than are left today (`store_job`)."""

    def __init__(self, pages: int, left: int) -> None:
        super().__init__("page_quota")
        self.pages = pages
        self.left = left


def store_job(
    factory: sessionmaker[Session],
    settings: Settings,
    user_id: str,
    filename: str,
    data: bytes,
    pages: int,
    mode: str,
    *,
    course_id: str | None = None,
    exam: bool = False,
    revises: str | None = None,
    part_group: str | None = None,
    part_label: str | None = None,
    now: datetime | None = None,
) -> str:
    """Count the pages against today's quota and queue the job, in one transaction.

    Raises `TooManyJobs` ("too_many_active", "too_many_stored") or `QuotaRefused`; nothing
    is changed then. `part_group` and `part_label` say that the file is one part of a larger
    one converted in parts (`web.routes.parts`)."""
    now = now or utcnow()  # the caller's clock (the upload route's; tests move it)
    with session_scope(factory) as session:
        # Checked before the pages are counted: a refusal costs nothing.
        if active_job_count(session, user_id) >= MAX_ACTIVE_JOBS_PER_USER:
            raise TooManyJobs("too_many_active")
        if live_job_count(session, user_id, now) >= MAX_LIVE_JOBS_PER_USER:
            raise TooManyJobs("too_many_stored")
        quota = settings.per_user_daily_page_quota
        if not try_consume_pages(session, user_id, pages, quota=quota, day=utc_today(now)):
            left = pages_left(session, user_id, quota=quota, day=utc_today(now))
            raise QuotaRefused(pages, left or 0)
        job = Job(
            user_id=user_id,
            status=JobStatus.QUEUED,
            filename=filename,
            page_count=pages,
            opt_in_retention=mode == "dev",
            retention_mode=mode,
            retain_until=retention.retain_until_at_upload(settings, mode, now),
            created_at=now,
            course_id=course_id,
            exam_mode=exam,
            revises_job_id=revises,
            part_group=part_group,
            part_label=part_label,
        )
        session.add(job)
        session.flush()
        session.add(Blob(job_id=job.id, kind="upload", media_type="application/pdf", content=data))
        return job.id
