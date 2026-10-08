"""Daily error aggregation. Runs before retention deletes documents.

Counts, per UTC day of job completion: fixes by kind and status, failed check codes, fix
reasons, model call outcomes, fallback and escalation counts, and job error codes. Writes
`ErrorAggregate` rows (upsert) and marks each job `aggregated=True`. Category codes only;
never document text.

Rows are (day, step, category, count). `step` is the fix kind ("equation", "figure",
"heading") or "job". Categories, with the step shown in front for reading:

    equation  status:needs_review                       fixes by status
    equation  check:latex.allowed_commands:unknown_command   failed checks (name:code)
    equation  reason:low_confidence                     Fix.reason
    equation  call:gpt-6-luna:outcome:timeout           one per model call attempt
    equation  call:fallback                             calls made as a fallback
    equation  call:escalated                            fixes re-asked on another model
    job       status:done | status:failed               jobs
    job       error:encrypted                           Job.error_code
    job       pages                                     sum of pages (not a count of jobs)
    job       aggregate:invalid_result                  result_json that did not parse
    equation  aggregate:checks_edited                   checks lost to an earlier edit

Fixes are counted as the pipeline left them, even when a reviewer acted before the cron
ran. A review rewrites the stored result in place (the status becomes accepted, edited or
rejected; an edit replaces the checks), so each fix's status is taken from the `before`
snapshot of its earliest `ReviewEdit`. An edit's replaced checks cannot be recovered;
that fix's checks are counted as "aggregate:checks_edited" instead of the reviewer's.

Review actions ("review:edit") are counted by `ops.retention` when the job is deleted,
because reviewers can act until then.

Every code part is checked against `CODE_PATTERN`; anything else (a sentence, LaTeX, an
email) is recorded as "other", so a bug upstream cannot write text into this table.
Idempotent: the counts and `aggregated=True` are committed in one transaction, and on
Postgres the job rows are locked with SKIP LOCKED so two cron runs cannot count a job twice.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.core.types import DocumentResult, Fix
from a11y_stem.observability.logging import get_logger
from a11y_stem.web.db import ErrorAggregate, Job, JobStatus, ReviewEdit, session_scope

CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,47}$")
OTHER = "other"
FINISHED = (JobStatus.DONE.value, JobStatus.FAILED.value)
BATCH_SIZE = 100
_STEP_MAX = 32
_CATEGORY_MAX = 64

Key = tuple[str, str]  # (step, category)


def code(value: object) -> str:
    """A category code safe to store, or "other". Lower-cases; never passes text through."""
    if not isinstance(value, str):
        return OTHER
    candidate = value.strip().lower()
    return candidate if CODE_PATTERN.match(candidate) else OTHER


def utc_day(when: datetime | None, fallback: datetime) -> date:
    """UTC calendar day. SQLite returns naive datetimes; they are stored as UTC."""
    moment = when or fallback
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def _key(step: str, *parts: str) -> Key:
    return step[:_STEP_MAX], ":".join(parts)[:_CATEGORY_MAX]


@dataclass
class Reviewed:
    """What reviewers changed in one job's stored result before it was aggregated."""

    status: dict[str, str] = field(default_factory=dict)  # element_id -> pipeline status
    edited: set[str] = field(default_factory=set)  # element_ids whose checks were replaced


def fix_counts(fix: Fix, reviewed: Reviewed | None = None) -> Counter[Key]:
    """Categories for one fix (see the module docstring for the list), as the pipeline
    produced it: `reviewed` undoes what a reviewer changed in the stored result."""
    step = code(fix.kind.value)
    counts: Counter[Key] = Counter()
    status = fix.status.value
    if reviewed is not None:
        status = reviewed.status.get(fix.element_id, status)
    counts[_key(step, "status", code(status))] += 1
    if reviewed is not None and fix.element_id in reviewed.edited:
        counts[_key(step, "aggregate", "checks_edited")] += 1
    else:
        for check in fix.failed_checks:
            counts[_key(step, "check", code(check.name), code(check.code or "failed"))] += 1
    if fix.reason:
        counts[_key(step, "reason", code(fix.reason))] += 1
    for call in fix.calls:
        counts[_key(step, "call", code(call.model), "outcome", code(call.outcome))] += 1
        if call.is_fallback:
            counts[_key(step, "call", "fallback")] += 1
    if _escalated(fix):
        counts[_key(step, "call", "escalated")] += 1
    return counts


def _escalated(fix: Fix) -> bool:
    """A non-fallback call to a different model than the first one: the router escalated
    (e.g. a low-confidence gpt-6-luna answer re-asked on gpt-6.1-sol)."""
    if not fix.calls:
        return False
    first = fix.calls[0].model
    return any(c.model != first and not c.is_fallback for c in fix.calls[1:])


def job_counts(
    status: str,
    error_code: str | None,
    page_count: int,
    result_json: object,
    reviewed: Reviewed | None = None,
) -> Counter[Key]:
    """Categories for one finished job, from its row and its stored result."""
    counts: Counter[Key] = Counter()
    counts[_key("job", "status", code(status))] += 1
    if error_code:
        counts[_key("job", "error", code(error_code))] += 1
    if page_count > 0:
        counts[_key("job", "pages")] += page_count
    if result_json is None:
        return counts
    try:
        result = DocumentResult.model_validate(result_json)
    except ValidationError:
        # The error text would quote the stored document; count the category only.
        counts[_key("job", "aggregate", "invalid_result")] += 1
        return counts
    for fix in result.fixes:
        counts.update(fix_counts(fix, reviewed))
    return counts


def reviewed_before_aggregation(session: Session, job_ids: Sequence[str]) -> dict[str, Reviewed]:
    """job_id -> what its reviews changed, from the `ReviewEdit` rows (oldest first).

    Only the `status` of each element's earliest `before` snapshot is read; the rest of
    the snapshot (the proposal itself) is ignored.
    """
    if not job_ids:
        return {}
    edits = session.execute(
        select(ReviewEdit.job_id, ReviewEdit.element_id, ReviewEdit.action, ReviewEdit.before)
        .where(ReviewEdit.job_id.in_(list(job_ids)))
        .order_by(ReviewEdit.created_at, ReviewEdit.id)
    ).all()
    reviewed: dict[str, Reviewed] = {}
    for edit in edits:
        job = reviewed.setdefault(edit.job_id, Reviewed())
        if edit.element_id not in job.status:
            before = _status_of(edit.before)
            if before is not None:
                job.status[edit.element_id] = before
        if edit.action == "edit":
            job.edited.add(edit.element_id)
    return reviewed


def _status_of(snapshot: object) -> str | None:
    if isinstance(snapshot, Mapping):
        status = snapshot.get("status")
        if isinstance(status, str):
            return status
    return None


def upsert_counts(session: Session, day: date, counts: Counter[Key]) -> None:
    """Add `counts` to the day's rows: INSERT ... ON CONFLICT DO UPDATE (SQLite, Postgres)."""
    rows = [
        {"day": day, "step": step, "category": category, "count": n}
        for (step, category), n in sorted(counts.items())
        if n
    ]
    if not rows:
        return
    dialect = session.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        _upsert_portable(session, rows)
        return
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(ErrorAggregate).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["day", "step", "category"],
        set_={"count": ErrorAggregate.count + stmt.excluded.count},
    )
    session.execute(stmt)


def _upsert_portable(session: Session, rows: Iterable[dict]) -> None:
    """Fallback for other databases (not used in production; not safe under concurrency)."""
    for row in rows:
        existing = session.scalar(
            select(ErrorAggregate).where(
                ErrorAggregate.day == row["day"],
                ErrorAggregate.step == row["step"],
                ErrorAggregate.category == row["category"],
            )
        )
        if existing is None:
            session.add(ErrorAggregate(**row))
        else:
            existing.count += row["count"]
    session.flush()


# Only the columns counting needs (never the stored HTML).
_COLUMNS = (
    Job.id,
    Job.status,
    Job.error_code,
    Job.page_count,
    Job.result_json,
    Job.finished_at,
    Job.created_at,
)


def _count_and_mark(session: Session, jobs: Sequence[Any], now: datetime) -> int:
    """Upsert the counts for these job rows and mark them aggregated (caller's transaction).

    Upserts run in sorted order so concurrent runs take row locks in the same order and
    cannot deadlock.
    """
    if not jobs:
        return 0
    reviewed = reviewed_before_aggregation(session, [job.id for job in jobs])
    by_day: dict[date, Counter[Key]] = {}
    for job in jobs:
        day = utc_day(job.finished_at or job.created_at, now)
        counts = job_counts(
            job.status, job.error_code, job.page_count, job.result_json, reviewed.get(job.id)
        )
        by_day.setdefault(day, Counter()).update(counts)
    for day, counts in sorted(by_day.items()):
        upsert_counts(session, day, counts)
    session.execute(update(Job).where(Job.id.in_([job.id for job in jobs])).values(aggregated=True))
    return len(jobs)


def _aggregate_batch(session: Session, now: datetime) -> int:
    """Count one batch and mark it aggregated, in the caller's transaction."""
    jobs = session.execute(
        select(*_COLUMNS)
        .where(Job.status.in_(FINISHED), Job.aggregated.is_(False))
        .order_by(Job.finished_at, Job.id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    ).all()
    return _count_and_mark(session, jobs, now)


def aggregate_jobs(session: Session, job_ids: Iterable[str], now: datetime | None = None) -> int:
    """Aggregate these jobs, if finished and not yet aggregated, in the caller's
    transaction. Returns how many were counted.

    For code that deletes jobs (`ops.retention.delete_jobs`) or finishes them: on
    Postgres the rows are locked (waiting for a cron run that holds them), so a job is
    never counted twice.
    """
    ids = sorted(set(job_ids))
    if not ids:
        return 0
    jobs = session.execute(
        select(*_COLUMNS)
        .where(Job.id.in_(ids), Job.status.in_(FINISHED), Job.aggregated.is_(False))
        .order_by(Job.id)
        .with_for_update()
    ).all()
    return _count_and_mark(session, jobs, now or datetime.now(UTC))


def aggregate_finished(factory: sessionmaker[Session], now: datetime | None = None) -> int:
    """Aggregate every finished, not-yet-aggregated job. Returns the number of jobs.

    Works in batches of `BATCH_SIZE`, one transaction each, so a large backlog never holds
    locks for long and a failure keeps the batches already committed.
    """
    now = now or datetime.now(UTC)
    total = 0
    while True:
        with session_scope(factory) as session:
            done = _aggregate_batch(session, now)
        total += done
        if done < BATCH_SIZE:
            break
    get_logger(__name__).info("aggregate_done", jobs=total)
    return total
