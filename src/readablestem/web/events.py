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
| `decision_made` | a review decision or edit is saved | `single`, `group`, `structure` | fixes |
| `decision_outcome` | ... one row per kind of thing it did (columns below) | | signed count |
| `review_completed` | no decision is left open (once) | | seconds since finished |
| `html_viewed` | the HTML opens in the browser | | |
| `downloaded` | the HTML is downloaded | `open` (decisions open) or `done` | decisions open |
| `download_format` | each download, beside `downloaded` | `html`, `canvas`, `docx`, ... | |
| `will_post` | "Will you post this for students?" | `yes`, `no` or `not_sure` | |
| `minutes_saved` | "About how many minutes did this save?" | | minutes |

`decision_outcome` (decisions A15 and A16.17 of the accuracy round) says what a review write
did, so production can count false flags, corrections nobody was asked for and decisions per
page, per release. It has no `code`; it fills eight short columns instead, each checked
against a closed list by `make`: `kind` (`OUTCOME_KINDS`), `reason` (the flag's reason code,
or "none" for something nobody was asked about: `reason_codes`), `outcome` (`OUTCOMES`),
`origin` (`ORIGINS`: read from the source file or from a page), `source` (`SOURCES`: what put
it in front of the reviewer), `mode` (one thing or several), `write_mode` (`single`, `group`
or `structure`, as `decision_made`'s code) and `release` (the job output's
`pipeline_version`, `RELEASE`). `value` is the signed count: an Undo writes the rows of the
write it reverses with the count negated, so the sums over a job are the decisions that
stand. `web.outcomes` works out the rows from a write; `add_outcomes` is the one writer;
`outcome_cells` is the one reader, with the small-count rules applied.

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
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from readablestem.config import Settings
from readablestem.web.database import as_utc
from readablestem.web.db import Event, Job, JobStatus, User, session_scope, utcnow
from readablestem.web.logsafe import log_exception

logger = logging.getLogger(__name__)

SIGNED_IN = "signed_in"
UPLOADED = "uploaded"
REUPLOADED = "reuploaded"
FINISHED = "finished"
REVIEW_OPENED = "review_opened"
DECISION_MADE = "decision_made"
DECISION_OUTCOME = "decision_outcome"
REVIEW_COMPLETED = "review_completed"
HTML_VIEWED = "html_viewed"
DOWNLOADED = "downloaded"
DOWNLOAD_FORMAT = "download_format"
# The file types a download can be, as the `download_format` event's code: the review record
# says which of them were downloaded (`web.record`).
FORMATS = ("html", "canvas", "cartridge", "docx", "epub")
WILL_POST = "will_post"
MINUTES_SAVED = "minutes_saved"
NAMES = (
    SIGNED_IN,
    UPLOADED,
    REUPLOADED,
    FINISHED,
    REVIEW_OPENED,
    DECISION_MADE,
    DECISION_OUTCOME,
    REVIEW_COMPLETED,
    HTML_VIEWED,
    DOWNLOADED,
    DOWNLOAD_FORMAT,
    WILL_POST,
    MINUTES_SAVED,
)

# The closed lists of a `decision_outcome` row's columns (module docstring). "page" is a
# whole page (a page left out); the other kinds are `core.types.Kind`.
OUTCOME_KINDS = ("heading", "paragraph", "equation", "figure", "list_item", "table", "page")
# kept: the proposal stood as the tool had it (a flag that was false). edited, replaced (the
# other reading was taken), rejected (the original stays), removed: a flag that was true, or,
# with the reason "none", a correction nobody was asked for. added: content put in by hand.
OUTCOMES = ("kept", "edited", "replaced", "rejected", "removed", "added")
ORIGINS = ("page", "source")  # read from a page (a PDF), or from a LaTeX source
# What put the thing in front of the reviewer: a flag the pipeline raised, or nothing (the
# reviewer found it). The sources of WS8.22 add their names here when they arrive.
SOURCES = ("pipeline", "browsing")
MODES = ("single", "group")  # the write changed one thing, or several
WRITE_MODES = ("single", "group", "structure")
NO_REASON = "none"  # nobody was asked about it
OTHER_REASON = "other"  # a flag whose code is in no message table (never expected)
# `pipeline_version` of the job's output; P9's WS0.9 adds "+" and 12 hex digits of the
# production profile's hash when it lands. "unknown" for a version that does not fit.
RELEASE = re.compile(r"[a-z0-9][a-z0-9.]{0,23}(\+[0-9a-f]{12})?")
UNKNOWN_RELEASE = "unknown"
# Element kind -> lane, read by `outcome_cells` and the release report: one table, so the two
# cannot disagree. Lanes with no kind yet (drawn structures, scans) come with their items.
LANES = {
    "equation": "equations",
    "heading": "text",
    "paragraph": "text",
    "list_item": "text",
    "table": "tables",
    "figure": "figures",
    "page": "pages",
}
_OUTCOME_COLUMNS = (
    "kind",
    "reason",
    "outcome",
    "origin",
    "source",
    "mode",
    "write_mode",
    "release",
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


@cache
def reason_codes() -> frozenset[str]:
    """Every reason a flag can carry: the codes and check names of the review screen's
    message tables (which `tests/test_web_review_messages.py` keeps equal to what the
    pipeline emits), the triage codes, "none" and "other"."""
    from readablestem.core import triage
    from readablestem.web.messages import CHECK_MESSAGES, REASON_MESSAGES, TABLE_CHECK_PARTS

    codes = {c for pair in CHECK_MESSAGES for c in pair} | set(REASON_MESSAGES)
    codes |= set(TABLE_CHECK_PARTS)
    codes |= {triage.BELOW_ACCEPT, triage.REPEATED_LABEL, triage.SECOND_CHECK_MISMATCH}
    return frozenset(c for c in codes if _CODE.fullmatch(c)) | {NO_REASON, OTHER_REASON}


def _closed(column: str) -> frozenset[str] | None:
    lists = {
        "kind": OUTCOME_KINDS,
        "outcome": OUTCOMES,
        "origin": ORIGINS,
        "source": SOURCES,
        "mode": MODES,
        "write_mode": WRITE_MODES,
    }
    if column == "reason":
        return reason_codes()
    return frozenset(lists[column]) if column in lists else None


def make(
    user_id: str,
    name: str,
    *,
    job_ref: str | None = None,
    code: str | None = None,
    value: float | None = None,
    at: datetime | None = None,
    **outcome: str,
) -> Event:
    """An event row, checked to be content-free. A ValueError is a bug in the caller.

    A `decision_outcome` row takes all eight outcome columns as keywords (each from its
    closed list; `release` must fit `RELEASE`) and a non-zero count; no other event takes
    any of them."""
    if name not in NAMES:
        raise ValueError(f"unknown event {name!r}")
    if job_ref is not None and not _JOB_ID.fullmatch(job_ref):
        raise ValueError("job_ref must be a job id")
    if code is not None and not _CODE.fullmatch(code):
        raise ValueError("an event code is a short lower-case category")
    if value is not None and not math.isfinite(value):
        raise ValueError("an event value is a finite number")
    if set(outcome) - set(_OUTCOME_COLUMNS):
        raise ValueError("unknown event column")
    if name == DECISION_OUTCOME:
        if set(outcome) != set(_OUTCOME_COLUMNS) or code is not None or not value:
            raise ValueError("an outcome has every outcome column, no code and a count")
        for column, given in outcome.items():
            allowed = _closed(column)
            fits = isinstance(given, str) and len(given) <= 64
            if fits and allowed is None:
                fits = RELEASE.fullmatch(given) is not None or given == UNKNOWN_RELEASE
            elif fits and allowed is not None:
                fits = given in allowed
            if not fits:
                raise ValueError(f"an outcome's {column} is a code from its closed list")
    elif outcome:
        raise ValueError("only a decision outcome has outcome columns")
    return Event(
        user_id=user_id,
        name=name,
        job_ref=job_ref,
        code=code,
        value=None if value is None else float(value),
        created_at=at or utcnow(),
        **outcome,
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
            if job_ref is not None and is_sample(session, job_ref):
                return  # the sample document is not part of the beta's numbers (web.sample)
            if once_per_visit and _since(session, user_id, name, job_ref, row.created_at):
                return
            session.add(row)
    except Exception as exc:
        log_exception(logger, "event_failed", exc, kind=name)


def is_sample(session: Session, job_id: str) -> bool:
    """The job is the sample document (T3-9): no usage event is written for it."""
    return bool(session.scalar(select(Job.is_sample).where(Job.id == job_id)))


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
    something (`kind`: "single" or "group"; value: fixes changed), and
    `review_completed` the first time a write leaves no decision open (value: seconds
    since the job finished)."""
    if not settings.events_enabled or changed <= 0 or job.is_sample:
        return
    now = utcnow()
    session.add(make(user_id, DECISION_MADE, job_ref=job.id, code=kind, value=changed, at=now))
    if open_after == 0 and not _exists(session, user_id, REVIEW_COMPLETED, job.id):
        finished = as_utc(job.finished_at)
        seconds = round((now - finished).total_seconds(), 1) if finished else None
        session.add(make(user_id, REVIEW_COMPLETED, job_ref=job.id, value=seconds, at=now))


@dataclass(frozen=True)
class Outcome:
    """What one review write did to one thing (`web.outcomes` works it out): codes only.
    `count` is 1, or -1 for a write that takes one back (a page put back)."""

    kind: str
    reason: str
    outcome: str
    origin: str
    source: str
    count: int = 1


def release_of(pipeline_version: str | None) -> str:
    """The release a job's output was made by, as an outcome row carries it."""
    version = (pipeline_version or "").strip().lower()
    return version if RELEASE.fullmatch(version) else UNKNOWN_RELEASE


def add_outcomes(
    session: Session,
    settings: Settings,
    *,
    user_id: str,
    job: Job,
    release: str,
    write_mode: str,
    outcomes: Sequence[Outcome],
    sign: int = 1,
) -> None:
    """The one writer of `decision_outcome`, in the review write's own transaction: one row
    per distinct (kind, reason, outcome, origin, source), its count summed (times `sign`:
    -1 for an Undo of the write that recorded `outcomes`). Nothing for a sample document or
    with EVENTS_ENABLED=false, as `add_decision`."""
    if not settings.events_enabled or job.is_sample or not outcomes:
        return
    sums: Counter[tuple[str, str, str, str, str]] = Counter()
    for o in outcomes:
        sums[(o.kind, o.reason, o.outcome, o.origin, o.source)] += o.count
    mode = "single" if sum(abs(o.count) for o in outcomes) == 1 else "group"
    now = utcnow()
    for (kind, reason, outcome, origin, source), count in sums.items():
        if count:
            session.add(
                make(
                    user_id,
                    DECISION_OUTCOME,
                    job_ref=job.id,
                    value=sign * count,
                    at=now,
                    kind=kind,
                    reason=reason,
                    outcome=outcome,
                    origin=origin,
                    source=source,
                    mode=mode,
                    write_mode=write_mode,
                    release=release,
                )
            )


def record_download(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    user_id: str,
    job_id: str,
    open_decisions: int,
    file_type: str = "html",
) -> None:
    """`downloaded`, saying whether decisions were still open (the measure "downloads
    made while decisions are open", target under 1 in 10), and `download_format`, saying
    which file type it was (`FORMATS`; the review record lists them)."""
    if file_type not in FORMATS:
        raise ValueError(f"unknown file type {file_type!r}")
    record(
        factory,
        settings,
        user_id,
        DOWNLOADED,
        job_ref=job_id,
        code="open" if open_decisions else "done",
        value=open_decisions,
    )
    record(factory, settings, user_id, DOWNLOAD_FORMAT, job_ref=job_id, code=file_type)


def downloaded_formats(session: Session, job_id: str) -> list[str]:
    """The file types of this job that were downloaded, in the order of `FORMATS`."""
    codes = set(
        session.scalars(
            select(Event.code).where(Event.name == DOWNLOAD_FORMAT, Event.job_ref == job_id)
        )
    )
    return [f for f in FORMATS if f in codes]


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
    deleted document can tell its owner what happened (`routes.jobs.job_access`)."""
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


# --------------------------------------------------------------------------- outcomes in use

# Small counts (P6b's WS11.18, rule 4): a cell is shown only when it draws on this many
# accounts and documents, and no one account gives more than half of its decisions or of its
# pages. Design values, not measured ones.
MIN_ACCOUNTS = 5
MIN_DOCUMENTS = 10
MAX_SHARE = 0.5
ALL_LANES = "all"
TRUE_OUTCOMES = frozenset({"edited", "replaced", "rejected", "removed", "added"})


@dataclass
class OutcomeCell:
    """One lane (or "all") of one release: counts and the three measures of decision A15.
    No user id and no job id. When `shown` is False every number is withheld (0 or None)
    and `needs_accounts` says how many more accounts it would take."""

    lane: str
    shown: bool = False
    needs_accounts: int = 0
    accounts: int = 0
    documents: int = 0
    pages: int = 0
    decisions: int = 0
    false_flags: int = 0  # flagged, and kept as the tool had it
    true_flags: int = 0  # flagged, and changed (edited, replaced, rejected, removed, added)
    kept_in_bulk: int = 0  # of the false flags, kept by a group or bulk write
    unflagged: int = 0  # anything changed or added that carried no flag: silent misses found
    added: int = 0  # of those, content put in by hand
    false_per_true: float | None = None
    unflagged_per_100_pages: float | None = None
    decisions_per_page_median: float | None = None


@dataclass
class _Tally:
    accounts: Counter[str] = field(default_factory=Counter)  # decisions per account
    account_pages: Counter[str] = field(default_factory=Counter)
    documents: dict[str, str] = field(default_factory=dict)  # job -> account
    per_document: Counter[str] = field(default_factory=Counter)
    counts: Counter[str] = field(default_factory=Counter)


def outcome_cells(session: Session, release: str) -> list[OutcomeCell]:
    """What reviewers did with one release's output, per lane and for all lanes ("all",
    last), from the `decision_outcome` rows net of undo, with the small-count rules applied:
    the one way production numbers are read (the admin block and the release report).

    Admin accounts are left out of every count and threshold (sample documents never write
    a row). A document's pages are those its `uploaded` event recorded. A withheld cell
    keeps no number; when exactly one lane is withheld, the next smallest is withheld with
    it, so the hidden one cannot be found from "all"."""
    rows = session.execute(
        select(
            Event.user_id,
            Event.job_ref,
            Event.kind,
            Event.reason,
            Event.outcome,
            Event.write_mode,
            func.sum(Event.value),
        )
        .join(User, User.id == Event.user_id)
        .where(
            Event.name == DECISION_OUTCOME,
            Event.release == release,
            User.is_admin.is_not(True),
        )
        .group_by(
            Event.user_id,
            Event.job_ref,
            Event.kind,
            Event.reason,
            Event.outcome,
            Event.write_mode,
        )
    ).all()
    jobs = {job for _, job, *_ in rows if job}
    pages: dict[str, int] = {}
    if jobs:
        uploads = session.execute(
            select(Event.job_ref, func.max(Event.value))
            .where(Event.name == UPLOADED, Event.job_ref.in_(jobs))
            .group_by(Event.job_ref)
        )
        pages = {job: int(n or 0) for job, n in uploads if job}
    tallies: dict[str, _Tally] = defaultdict(_Tally)
    for user, job, kind, reason, outcome, write_mode, total in rows:
        count = int(total or 0)
        if not count or not job:
            continue
        for lane in (LANES.get(kind or "", "other"), ALL_LANES):
            tally = tallies[lane]
            tally.accounts[user] += count
            tally.documents[job] = user
            tally.per_document[job] += count
            tally.counts["decisions"] += count
            if reason == NO_REASON:
                tally.counts["unflagged"] += count
                if outcome == "added":
                    tally.counts["added"] += count
            elif outcome == "kept":
                tally.counts["false_flags"] += count
                if write_mode == "group":
                    tally.counts["kept_in_bulk"] += count
            elif outcome in TRUE_OUTCOMES:
                tally.counts["true_flags"] += count
    cells = {lane: _cell(lane, tally, pages) for lane, tally in tallies.items()}
    lanes = sorted((c for c in cells.values() if c.lane != ALL_LANES), key=lambda c: c.lane)
    withheld = [c for c in lanes if not c.shown]
    if len(withheld) == 1:
        others = [c for c in lanes if c.shown]
        if others:
            _withhold(min(others, key=lambda c: (c.decisions, c.lane)))
    return lanes + ([cells[ALL_LANES]] if ALL_LANES in cells else [])


def _cell(lane: str, tally: _Tally, pages: dict[str, int]) -> OutcomeCell:
    for job, user in tally.documents.items():
        tally.account_pages[user] += pages.get(job, 0)
    total_pages = sum(pages.get(job, 0) for job in tally.documents)
    counts = tally.counts
    cell = OutcomeCell(
        lane=lane,
        accounts=len(tally.accounts),
        documents=len(tally.documents),
        pages=total_pages,
        decisions=counts["decisions"],
        false_flags=counts["false_flags"],
        true_flags=counts["true_flags"],
        kept_in_bulk=counts["kept_in_bulk"],
        unflagged=counts["unflagged"],
        added=counts["added"],
    )
    if cell.true_flags:
        cell.false_per_true = round(cell.false_flags / cell.true_flags, 2)
    if total_pages:
        cell.unflagged_per_100_pages = round(100 * cell.unflagged / total_pages, 2)
        per_page = [
            tally.per_document[job] / pages[job] for job in tally.documents if pages.get(job)
        ]
        cell.decisions_per_page_median = round(statistics.median(per_page), 2)
    dominant = max(tally.accounts.values(), default=0) > MAX_SHARE * max(cell.decisions, 0) or (
        max(tally.account_pages.values(), default=0) > MAX_SHARE * total_pages
    )
    cell.shown = cell.accounts >= MIN_ACCOUNTS and cell.documents >= MIN_DOCUMENTS and not dominant
    if not cell.shown:
        _withhold(cell)
    return cell


def _withhold(cell: OutcomeCell) -> None:
    """Keep the lane's name and how many more accounts it needs; drop every number."""
    needs = max(0, MIN_ACCOUNTS - cell.accounts)
    blank = OutcomeCell(lane=cell.lane, shown=False, needs_accounts=needs)
    for name in OutcomeCell.__dataclass_fields__:
        setattr(cell, name, getattr(blank, name))
