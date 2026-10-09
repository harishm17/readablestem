"""Content-free usage events: the beta's few numbers (`events` table).

By the go/no-go the beta has to say, for each instructor, whether they uploaded,
reviewed, downloaded and meant to post the file (FEATURE-REVIEW "Beta 6"). The app
records that on the server, with no cookies and no script in the browser:

| Event | When | `code` | `value` |
|---|---|---|---|
| `signed_in` | a sign-in finishes | | |
| `uploaded` | an upload is queued | `fp:` and the file's fingerprint | pages |
| `reuploaded` | ... a file this person uploaded before | | hours since then |
| `finished` | the worker finishes a job | `done`, `failed:timeout`, ... | seconds since upload |
| `review_opened` | the review screen or list opens | `split` or `list` | |
| `decision_made` | a review decision is saved | `single`, `group` or `bulk` | fixes changed |
| `review_completed` | no decision is left open (once) | | seconds since finished |
| `html_viewed` | the HTML opens in the browser | | |
| `downloaded` | the HTML is downloaded | `open` (decisions open) or `done` | decisions open |
| `will_post` | "Will you post this for students?" | `yes`, `no` or `not_sure` | |
| `minutes_saved` | "About how many minutes did this save?" | | minutes |

`review_opened` and `html_viewed` are written once per visit (`SAME_VISIT`): the review
screen is shown again after every decision, and a reload is not a new look.

Rule 1 holds by construction: a row is a user id, an event name, a job id, a category code
and a number (`tests/test_web_content_schema.py` fixes the columns), never a file name, a
title or document text, and `make` refuses anything else. The fingerprint is an HMAC of
the file's SHA-256, keyed with a key derived from SESSION_SECRET and salted with the
user's id, cut to 96 bits: it can tell "the same file as this person's earlier upload"
and nothing more. Neither the file nor its plain hash can be got back from it (a plain
hash would let anyone with a copy of a public PDF check who had uploaded it), and the
same file uploaded by two people gives two unrelated fingerprints. Rotating
SESSION_SECRET makes the next upload of a file look new. "Delete now" clears the
fingerprint of that upload (`forget_upload`): the `uploaded` row stays for the counts,
but nothing derived from the deleted file does.

Events are pseudonymous (the random user id), deleted with the user
(`ops.retention.erase_user`) and after `EVENT_RETENTION_DAYS` (`ops.retention`). With
EVENTS_ENABLED=false nothing is written, and the feedback questions are not asked.

Recording never gets in the way of what the person was doing: an event that cannot be
written is logged as `event_failed` (its name and the error type, nothing else) and
dropped. The events of a review decision go in the decision's own transaction
(`add_decision`), so they commit or roll back with it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import Event, Job, JobStatus, session_scope, utcnow
from a11y_stem.web.logsafe import log_exception

logger = logging.getLogger(__name__)

SIGNED_IN = "signed_in"
UPLOADED = "uploaded"
REUPLOADED = "reuploaded"
FINISHED = "finished"
REVIEW_OPENED = "review_opened"
DECISION_MADE = "decision_made"
REVIEW_COMPLETED = "review_completed"
HTML_VIEWED = "html_viewed"
DOWNLOADED = "downloaded"
WILL_POST = "will_post"
MINUTES_SAVED = "minutes_saved"
NAMES = (
    SIGNED_IN,
    UPLOADED,
    REUPLOADED,
    FINISHED,
    REVIEW_OPENED,
    DECISION_MADE,
    REVIEW_COMPLETED,
    HTML_VIEWED,
    DOWNLOADED,
    WILL_POST,
    MINUTES_SAVED,
)

# Kept this long, then deleted by the hourly purge (docs/operations.md, "Usage events"):
# a whole academic year and the first weeks of the next, so a course taught again can be
# compared with its first run, and the four-week return rate measured all year.
EVENT_RETENTION_DAYS = 400
# A reload, or coming back to the review screen after each decision, is not a new visit.
SAME_VISIT = timedelta(minutes=30)

_CODE = re.compile(r"[a-z0-9_.:-]{1,64}")
_JOB_ID = re.compile(r"[0-9a-f]{32}")
_FINGERPRINT_INFO = b"a11y-stem upload fingerprint v1"
_DEV_KEY = b"a11y-stem development fingerprint key"


def make(
    user_id: str,
    name: str,
    *,
    job_ref: str | None = None,
    code: str | None = None,
    value: float | None = None,
    at: datetime | None = None,
) -> Event:
    """An event row, checked to be content-free. A ValueError is a bug in the caller."""
    if name not in NAMES:
        raise ValueError(f"unknown event {name!r}")
    if job_ref is not None and not _JOB_ID.fullmatch(job_ref):
        raise ValueError("job_ref must be a job id")
    if code is not None and not _CODE.fullmatch(code):
        raise ValueError("an event code is a short lower-case category")
    if value is not None and not math.isfinite(value):
        raise ValueError("an event value is a finite number")
    return Event(
        user_id=user_id,
        name=name,
        job_ref=job_ref,
        code=code,
        value=None if value is None else float(value),
        created_at=at or utcnow(),
    )


def record(
    factory: sessionmaker[Session],
    settings: Settings,
    user_id: str,
    name: str,
    *,
    job_ref: str | None = None,
    code: str | None = None,
    value: float | None = None,
    once_per_visit: bool = False,
) -> None:
    """Write one event in its own transaction, after the action it records.

    Never raises for a database problem (`event_failed` is logged instead). With
    `once_per_visit`, nothing is written when the same event for the same job was
    written in the last `SAME_VISIT`.
    """
    if not settings.events_enabled:
        return
    row = make(user_id, name, job_ref=job_ref, code=code, value=value)
    try:
        with session_scope(factory) as session:
            if once_per_visit and _since(session, user_id, name, job_ref, row.created_at):
                return
            session.add(row)
    except Exception as exc:
        log_exception(logger, "event_failed", exc, kind=name)


def _since(session: Session, user_id: str, name: str, job_ref: str | None, now: datetime) -> bool:
    """True when this person had this event (for this job) within `SAME_VISIT`."""
    query = select(Event.id).where(
        Event.user_id == user_id, Event.name == name, Event.created_at >= now - SAME_VISIT
    )
    query = query.where(Event.job_ref.is_(None) if job_ref is None else Event.job_ref == job_ref)
    return session.scalar(query.limit(1)) is not None


def _exists(session: Session, user_id: str, name: str, job_ref: str) -> bool:
    query = select(Event.id).where(
        Event.user_id == user_id, Event.name == name, Event.job_ref == job_ref
    )
    return session.scalar(query.limit(1)) is not None


# --------------------------------------------------------------------------- uploads


def fingerprint(settings: Settings, user_id: str, data: bytes) -> str:
    """`fp:` and 24 hex digits that match only this person's uploads of these bytes
    (module docstring). Never the file's plain hash."""
    secret = settings.session_secret.get_secret_value() if settings.session_secret else ""
    key = hmac.new(secret.encode() or _DEV_KEY, _FINGERPRINT_INFO, hashlib.sha256).digest()
    digest = hashlib.sha256(data).hexdigest()
    mark = hmac.new(key, f"{user_id}:{digest}".encode(), hashlib.sha256).hexdigest()
    return "fp:" + mark[:24]


def forget_upload(session: Session, job_id: str) -> None:
    """Clear the file fingerprint of a job's `uploaded` event, in the caller's
    transaction, when its owner deletes it ("Delete now": everything made from the file
    goes). The row stays for the counts; that upload no longer counts as "the same file
    again" for a later one."""
    for event in session.scalars(
        select(Event).where(Event.job_ref == job_id, Event.name == UPLOADED)
    ):
        event.code = None


def record_upload(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    user_id: str,
    job_id: str,
    data: bytes,
    pages: int,
) -> None:
    """`uploaded` (value: pages), and `reuploaded` when this person uploaded the same
    file before (value: hours since their latest upload of it)."""
    if not settings.events_enabled:
        return
    code = fingerprint(settings, user_id, data)
    now = utcnow()
    try:
        with session_scope(factory) as session:
            earlier = as_utc(
                session.scalar(
                    select(func.max(Event.created_at)).where(
                        Event.user_id == user_id, Event.name == UPLOADED, Event.code == code
                    )
                )
            )
            session.add(make(user_id, UPLOADED, job_ref=job_id, code=code, value=pages, at=now))
            if earlier is not None:
                hours = round((now - earlier).total_seconds() / 3600, 2)
                session.add(make(user_id, REUPLOADED, job_ref=job_id, value=hours, at=now))
    except Exception as exc:
        log_exception(logger, "event_failed", exc, kind=UPLOADED)


# --------------------------------------------------------------------------- jobs


def record_finished(factory: sessionmaker[Session], settings: Settings, job_id: str) -> None:
    """`finished` for a job the worker has just finished or failed: its outcome as a code
    ("done", "done:scanned_pages", "failed:timeout") and the seconds since upload (what
    the instructor waited, queue included)."""
    if not settings.events_enabled:
        return
    try:
        with session_scope(factory) as session:
            job = session.execute(
                select(
                    Job.user_id, Job.status, Job.error_code, Job.created_at, Job.finished_at
                ).where(Job.id == job_id)
            ).one_or_none()
            if job is None or job.status not in {JobStatus.DONE, JobStatus.FAILED}:
                return
            status = str(job.status)
            code = f"{status}:{job.error_code}" if job.error_code else status
            started, finished = as_utc(job.created_at), as_utc(job.finished_at)
            seconds = (
                round((finished - started).total_seconds(), 1) if started and finished else None
            )
            session.add(
                make(
                    job.user_id,
                    FINISHED,
                    job_ref=job_id,
                    code=code if _CODE.fullmatch(code) else status,
                    value=seconds,
                )
            )
    except Exception as exc:
        log_exception(logger, "event_failed", exc, kind=FINISHED)


def record_review_opened(
    factory: sessionmaker[Session], settings: Settings, user_id: str, job_id: str, view: str
) -> None:
    """`review_opened` (`view`: "split" or "list"), once per visit: the review screen is
    shown again after every decision, which is not a new visit."""
    record(
        factory, settings, user_id, REVIEW_OPENED, job_ref=job_id, code=view, once_per_visit=True
    )


def add_decision(
    session: Session,
    settings: Settings,
    *,
    user_id: str,
    job: Job,
    kind: str,
    changed: int,
    open_after: int,
) -> None:
    """In the decision's transaction: `decision_made` for a review write that changed
    something (`kind`: "single", "group" or "bulk"; value: fixes changed), and
    `review_completed` the first time a write leaves no decision open (value: seconds
    since the job finished)."""
    if not settings.events_enabled or changed <= 0:
        return
    now = utcnow()
    session.add(make(user_id, DECISION_MADE, job_ref=job.id, code=kind, value=changed, at=now))
    if open_after == 0 and not _exists(session, user_id, REVIEW_COMPLETED, job.id):
        finished = as_utc(job.finished_at)
        seconds = round((now - finished).total_seconds(), 1) if finished else None
        session.add(make(user_id, REVIEW_COMPLETED, job_ref=job.id, value=seconds, at=now))


def record_download(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    user_id: str,
    job_id: str,
    open_decisions: int,
) -> None:
    """`downloaded`, saying whether decisions were still open (the measure "downloads
    made while decisions are open", target under 1 in 10)."""
    record(
        factory,
        settings,
        user_id,
        DOWNLOADED,
        job_ref=job_id,
        code="open" if open_decisions else "done",
        value=open_decisions,
    )


def downloaded(session: Session, user_id: str, job_id: str) -> bool:
    return _exists(session, user_id, DOWNLOADED, job_id)


def answered(session: Session, user_id: str, job_id: str) -> bool:
    """This person already answered the questions about this job."""
    query = select(Event.id).where(
        Event.user_id == user_id,
        Event.name.in_([WILL_POST, MINUTES_SAVED]),
        Event.job_ref == job_id,
    )
    return session.scalar(query.limit(1)) is not None


def uploaded_at(session: Session, user_id: str, job_id: str) -> datetime | None:
    """When this person uploaded this job, if their events still say so: how a link to a
    deleted document can tell its owner what happened (`routes.jobs.owned_job`)."""
    return as_utc(
        session.scalar(
            select(func.min(Event.created_at)).where(
                Event.user_id == user_id, Event.name == UPLOADED, Event.job_ref == job_id
            )
        )
    )


# --------------------------------------------------------------------------- admin

# Columns of the admin page's tables: (counter key, heading). A key is an event name, or
# "name:code" for one of its codes.
DAY_COLUMNS = (
    (SIGNED_IN, "Sign-ins"),
    (UPLOADED, "Uploads"),
    (REUPLOADED, "Same file again"),
    (FINISHED, "Finished"),
    (REVIEW_OPENED, "Reviews opened"),
    (DECISION_MADE, "Decisions"),
    (REVIEW_COMPLETED, "Reviews completed"),
    (HTML_VIEWED, "Viewed"),
    (DOWNLOADED, "Downloads"),
    (f"{DOWNLOADED}:open", "Downloads with decisions open"),
    (WILL_POST, "Feedback answers"),
)
PERSON_COLUMNS = (
    ("days", "Days active"),
    (UPLOADED, "Uploads"),
    (FINISHED, "Finished"),
    (DECISION_MADE, "Decisions"),
    (REVIEW_COMPLETED, "Reviews completed"),
    (DOWNLOADED, "Downloads"),
    (f"{WILL_POST}:yes", "Will post: yes"),
    (f"{WILL_POST}:no", "Will post: no"),
    (f"{WILL_POST}:not_sure", "Will post: not sure"),
    ("minutes", "Minutes saved (sum)"),
)


@dataclass
class UsageRow:
    """One row of the admin tables: a UTC day ("2026-10-09") or a pseudonymous person
    ("u-1a2b3c4d", the start of their random id; never an email)."""

    label: str
    counts: Counter[str] = field(default_factory=Counter)

    def cell(self, key: str) -> str:
        value = self.counts.get(key, 0)
        return f"{value:g}"


@dataclass
class Usage:
    days: int
    by_day: list[UsageRow]
    by_person: list[UsageRow]


def pseudonym(user_id: str) -> str:
    return "u-" + user_id[:8]


def usage(session: Session, *, days: int = 28, now: datetime | None = None) -> Usage:
    """Counts per UTC day (the last `days` days, newest first; days with events only)
    and per person (every event kept), for the admin page. Numbers only. A beta has a
    few people and a few thousand events, so they are counted here, not in SQL."""
    now = as_utc(now) or utcnow()
    first_day = (now - timedelta(days=days - 1)).date()
    by_day: dict[str, UsageRow] = {}
    by_person: dict[str, UsageRow] = {}
    active: dict[str, set[str]] = {}
    rows = session.execute(
        select(Event.user_id, Event.name, Event.code, Event.value, Event.created_at)
    ).all()
    for user_id, name, code, value, created in rows:
        at = as_utc(created)
        if at is None:
            continue
        day = at.date().isoformat()
        keys = [name] + ([f"{name}:{code}"] if code and name in {DOWNLOADED, WILL_POST} else [])
        person = by_person.setdefault(user_id, UsageRow(pseudonym(user_id)))
        person.counts.update(keys)
        if name == MINUTES_SAVED and value is not None:
            person.counts["minutes"] += round(value)
        active.setdefault(user_id, set()).add(day)
        if at.date() >= first_day:
            by_day.setdefault(day, UsageRow(day)).counts.update(keys)
    for user_id, person in by_person.items():
        person.counts["days"] = len(active[user_id])
    return Usage(
        days=days,
        by_day=[by_day[d] for d in sorted(by_day, reverse=True)],
        by_person=sorted(by_person.values(), key=lambda r: r.label),
    )
