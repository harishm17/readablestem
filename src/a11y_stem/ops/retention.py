"""Retention: delete jobs past `retain_until` with everything derived from the document
(upload, crops, result, HTML, review edits).

Every deletion of a job goes through `delete_jobs`: the hourly purge here, and the web
app's "delete now" (POST /jobs/{job_id}/delete must call it instead of
`session.delete(job)`). Before deleting, it counts what has not been counted yet, in the
same transaction: a finished job that was never aggregated (a user can review, download
and delete within the hour, before the cron runs) and the job's review actions.

Deletion is mandatory: it runs even if counting fails (privacy beats stats). Counting runs
in savepoints; a failure is logged and skipped, and finished jobs deleted without their
counts are reported in `PurgeStats.unaggregated_deleted` so the loss is visible.

Children are deleted explicitly before their job, so the purge does not depend on the
database enforcing `ON DELETE CASCADE` (SQLite only does with `PRAGMA foreign_keys=ON`);
the cascade stays as a second guarantee.

Review actions are counted at deletion because a reviewer can act any time until then, so
each action is counted exactly once.

Erasing a person (a data-deletion request, `a11y-stem users delete`) goes through
`erase_user`: their jobs through `delete_jobs` (so counts are kept), then every row of
every table that holds their user id, then the user. Only content-free aggregates and
daily spend remain. A new table with a user id must be added there.

Usage events (`web.events`) outlive the jobs they mention, by design, but not for ever:
the same purge deletes every event older than `EVENT_RETENTION_DAYS` (400 days).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.observability.logging import get_logger
from a11y_stem.ops.aggregate import (
    FINISHED,
    Key,
    aggregate_finished,
    aggregate_jobs,
    code,
    upsert_counts,
    utc_day,
)
from a11y_stem.web.db import (
    AuthIdentity,
    Blob,
    Course,
    CourseNotation,
    Event,
    Job,
    LinkedEmail,
    LoginLink,
    PendingUpload,
    QuotaDay,
    ReviewEdit,
    StepRecord,
    UploadResult,
    User,
    session_scope,
)
from a11y_stem.web.events import EVENT_RETENTION_DAYS

BATCH_SIZE = 200


@dataclass
class PurgeStats:
    jobs_deleted: int
    blobs_deleted: int
    unaggregated_deleted: int  # finished jobs deleted without their counts
    events_deleted: int = 0  # usage events past EVENT_RETENTION_DAYS
    pending_deleted: int = 0  # large files waiting for their pages to be chosen (T3-3)
    results_deleted: int = 0  # answers to uploads of several files, past their hour (T3-4)


def purge_expired(
    factory: sessionmaker[Session], now: datetime | None = None, *, source: str = "cron"
) -> PurgeStats:
    """Delete every job whose `retain_until` is before `now` (default: current UTC time).

    Batches of `BATCH_SIZE` jobs, one transaction each. On Postgres the job rows are
    locked with SKIP LOCKED, so a job the worker is updating at that instant is left for
    the next run instead of blocking it. `source` (a code: "cron", "worker", "cli") is
    logged with `purge_done`, so the hourly scheduler run and the worker's backstop can
    be told apart.
    """
    now = _as_utc(now or datetime.now(UTC))
    stats = PurgeStats(jobs_deleted=0, blobs_deleted=0, unaggregated_deleted=0)
    while True:
        with session_scope(factory) as session:
            batch = _purge_batch(session, now)
        stats.jobs_deleted += batch.jobs_deleted
        stats.blobs_deleted += batch.blobs_deleted
        stats.unaggregated_deleted += batch.unaggregated_deleted
        if batch.jobs_deleted < BATCH_SIZE:
            break
    with session_scope(factory) as session:
        stats.events_deleted = purge_old_events(session, now)
        stats.pending_deleted = purge_pending(session, now)
        stats.results_deleted = purge_upload_results(session, now)
    get_logger(__name__).info(
        "purge_done",
        source=source,
        jobs_deleted=stats.jobs_deleted,
        blobs_deleted=stats.blobs_deleted,
        unaggregated_deleted=stats.unaggregated_deleted,
        events_deleted=stats.events_deleted,
        pending_deleted=stats.pending_deleted,
        results_deleted=stats.results_deleted,
    )
    return stats


def purge_pending(session: Session, now: datetime) -> int:
    """Delete the large files kept for choosing their pages that are past their deletion
    time (`web.pending`), in the caller's transaction. Returns how many."""
    return _deleted(session.execute(delete(PendingUpload).where(PendingUpload.retain_until < now)))


def purge_upload_results(session: Session, now: datetime) -> int:
    """Delete the stored answers to uploads of several files (`web.upload_result`) that are
    past their hour, in the caller's transaction. Returns how many."""
    return _deleted(session.execute(delete(UploadResult).where(UploadResult.retain_until < now)))


def purge_old_events(session: Session, now: datetime) -> int:
    """Delete usage events older than `EVENT_RETENTION_DAYS`, in the caller's transaction.
    Returns how many. One DELETE on the indexed `created_at`."""
    cutoff = _as_utc(now) - timedelta(days=EVENT_RETENTION_DAYS)
    return _deleted(session.execute(delete(Event).where(Event.created_at < cutoff)))


def aggregate_and_purge(
    factory: sessionmaker[Session], now: datetime | None = None
) -> tuple[int | None, PurgeStats]:
    """The cron task: aggregate, then purge even if aggregation raised.

    Returns (jobs aggregated, or None when aggregation failed; purge stats). Purge errors
    propagate: the caller must report a failed run.
    """
    now = _as_utc(now or datetime.now(UTC))
    aggregated: int | None
    try:
        aggregated = aggregate_finished(factory, now)
    except Exception as exc:
        get_logger(__name__).error("aggregate_failed", error_type=type(exc).__name__)
        aggregated = None
    return aggregated, purge_expired(factory, now)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _purge_batch(session: Session, now: datetime) -> PurgeStats:
    ids = session.scalars(
        select(Job.id)
        .where(Job.retain_until < now)
        .order_by(Job.retain_until, Job.id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    ).all()
    return delete_jobs(session, ids, now)


def delete_jobs(
    session: Session, job_ids: Iterable[str], now: datetime | None = None
) -> PurgeStats:
    """Delete these jobs with everything derived from them, in the caller's transaction,
    after counting what was not counted yet (see the module docstring).

    The one supported way to delete a job; the web app's "delete now" should call
    `delete_jobs(session, [job.id])` inside its `session_scope`. Ids that do not exist are
    ignored. Never raises because counting failed.
    """
    now = _as_utc(now or datetime.now(UTC))
    rows = session.execute(
        select(Job.id, Job.status, Job.aggregated, Job.finished_at, Job.created_at, Job.is_sample)
        .where(Job.id.in_(sorted(set(job_ids))))
        .order_by(Job.id)
        .with_for_update()
    ).all()
    if not rows:
        return PurgeStats(jobs_deleted=0, blobs_deleted=0, unaggregated_deleted=0)
    ids = [row.id for row in rows]
    pending = [row.id for row in rows if row.status in FINISHED and not row.aggregated]
    unaggregated = 0 if _aggregate_before_delete(session, pending, now) else len(pending)

    # The sample document (web.sample) is not part of the counts.
    day_of = {
        row.id: utc_day(row.finished_at or row.created_at, now) for row in rows if not row.is_sample
    }
    _count_review_actions(session, day_of)
    blobs = _deleted(session.execute(delete(Blob).where(Blob.job_id.in_(ids))))
    session.execute(delete(ReviewEdit).where(ReviewEdit.job_id.in_(ids)))
    session.execute(delete(StepRecord).where(StepRecord.job_id.in_(ids)))
    jobs = _deleted(session.execute(delete(Job).where(Job.id.in_(ids))))
    # The student-file images made from the figures are kept in memory for a while
    # (`pipeline.figure_images`); a deleted document's go with it.
    from a11y_stem.pipeline import figure_images

    figure_images.clear_cache()
    return PurgeStats(jobs_deleted=jobs, blobs_deleted=blobs, unaggregated_deleted=unaggregated)


def erase_user(session: Session, user_id: str, now: datetime | None = None) -> PurgeStats:
    """Delete a user and everything of theirs, in the caller's transaction.

    Their jobs go through `delete_jobs` (counted first, like any deletion); then, children
    before parents and without relying on ON DELETE CASCADE (see the module docstring):
    review edits they made on any job, their courses' notation entries, their courses,
    their usage events, sign-in identities, linked Google addresses, email sign-in links and
    daily page counts, and the user row.
    Returns the job deletion's stats.
    """
    job_ids = list(session.scalars(select(Job.id).where(Job.user_id == user_id)))
    stats = delete_jobs(session, job_ids, now)
    courses = select(Course.id).where(Course.user_id == user_id).scalar_subquery()
    session.execute(delete(ReviewEdit).where(ReviewEdit.user_id == user_id))
    session.execute(delete(PendingUpload).where(PendingUpload.user_id == user_id))
    session.execute(delete(UploadResult).where(UploadResult.user_id == user_id))
    session.execute(delete(CourseNotation).where(CourseNotation.course_id.in_(courses)))
    session.execute(delete(Course).where(Course.user_id == user_id))
    session.execute(delete(Event).where(Event.user_id == user_id))
    session.execute(delete(AuthIdentity).where(AuthIdentity.user_id == user_id))
    session.execute(delete(LinkedEmail).where(LinkedEmail.user_id == user_id))
    session.execute(delete(LoginLink).where(LoginLink.user_id == user_id))
    session.execute(delete(QuotaDay).where(QuotaDay.user_id == user_id))
    session.execute(delete(User).where(User.id == user_id))
    return stats


def _aggregate_before_delete(session: Session, job_ids: list[str], now: datetime) -> bool:
    """Count finished jobs that were never aggregated. False if that failed."""
    if not job_ids:
        return True
    try:
        with session.begin_nested():
            aggregate_jobs(session, job_ids, now)
    except Exception as exc:  # stats are best effort; the delete must still happen
        get_logger(__name__).warning("aggregate_on_delete_failed", error_type=type(exc).__name__)
        return False
    return True


def _deleted(result: object) -> int:
    """Rows a DELETE removed (a DML statement always returns a CursorResult)."""
    return cast(CursorResult[Any], result).rowcount


def _count_review_actions(session: Session, day_of: dict[str, date]) -> None:
    """Add "review:<action>" counts (step = the reviewed fix's kind) for these jobs.

    `day_of` maps each job id to its completion day, the day its other counts went to.
    """
    try:
        with session.begin_nested():
            for day, counts in _review_counts(session, day_of).items():
                upsert_counts(session, day, counts)
    except Exception as exc:  # stats are best effort; the delete must still happen
        get_logger(__name__).warning("review_count_failed", error_type=type(exc).__name__)


def _review_counts(session: Session, day_of: dict[str, date]) -> dict[date, Counter[Key]]:
    edits = session.execute(
        select(ReviewEdit.job_id, ReviewEdit.element_id, ReviewEdit.action).where(
            ReviewEdit.job_id.in_(list(day_of))
        )
    ).all()
    if not edits:
        return {}
    reviewed = sorted({edit.job_id for edit in edits})
    kinds = {
        job_id: _fix_kinds(result_json)
        for job_id, result_json in session.execute(
            select(Job.id, Job.result_json).where(Job.id.in_(reviewed))
        ).all()
    }
    by_day: dict[date, Counter[Key]] = {}
    for edit in edits:
        kind = kinds.get(edit.job_id, {}).get(edit.element_id, "unknown")
        key: Key = (code(kind), f"review:{code(edit.action)}")
        by_day.setdefault(day_of[edit.job_id], Counter())[key] += 1
    return by_day


def _fix_kinds(result_json: object) -> dict[str, str]:
    """element_id -> fix kind, read straight from the stored JSON (no full validation)."""
    if not isinstance(result_json, dict):
        return {}
    kinds: dict[str, str] = {}
    for fix in result_json.get("fixes") or []:
        if isinstance(fix, dict) and isinstance(fix.get("element_id"), str):
            kinds[fix["element_id"]] = str(fix.get("kind", "unknown"))
    return kinds
