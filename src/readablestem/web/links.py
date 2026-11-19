"""Class links: one link an owner makes that lets whoever holds it read a document's published
student version (WS27.2, behind `FEATURE_CLASS_LINK`, off).

This is the only way in the product to read a document without a sign-in, so what it gives is
small and fixed:

- It gives one thing: the published copy of the student version, at `/s/{token}`
  (`routes.class_link`). It is not a role. It never passes through `job_access`, so it never
  reaches the job page, the review, the record, the settings, a download, a crop or any
  `/jobs/...` address, whoever holds it and whether or not they are signed in.
- Only the owner makes, pauses, resumes or removes a link or updates the published copy
  (`routes.sharing`, which asks `job_access` for the owner right). A person the document is
  shared with, at any rank, cannot.
- The token is made and kept as a sign-in link's is (`web.email_login`): 32 random bytes, and
  only their SHA-256 is stored (`job_links.token_hash`), so a copy of the database opens
  nothing. The owner is shown it once, in the address, when the link is made; the page after
  that shows that a link exists and what it has done, never the address. An owner who lost it
  makes a new link, which removes the old one.
- The page serves a copy of the student HTML made when the owner published it (a `Blob` of the
  job, kind `published`), never the working output, so a class never sees a draft or a
  half-made edit. A link is made, and the copy updated, only when the document is ready
  (`output.export_state`).
- No person is recorded: the page sets no cookie, so the only state is `open_count` and
  `first_opened_at` on the link.

How long it lasts. `closes_at` is required and at most `MAX_DAYS` after the link is made;
`opens_at` is optional. `is_live` is the one answer to "is this link open" for the route, the
share page and retention. While a document has a link that is kept (not removed, not yet
closed; a paused or not-yet-open link still keeps it, or pausing would end the document) its
`retain_until` is the latest `closes_at` plus the window the document goes back to
(`retention.after_link`: `day` 24 hours, `week` 7 days, `dev` its 30 days from upload or a day,
whichever ends later), and there is no new retention mode. So the document runs out on its own
clock after the link closes and nothing has to release it: it never reads as expired while the
link works or in the window after it. Removing the link ends the keeping at once: the document
goes back to its retention with the clock starting then (`reconcile`,
`retention.clock_after_link`). A link's row is deleted with its document and `ROW_KEEP` after it
closes or is removed, so the owner can still read its count. The published copy goes when no
link is kept any more (at once on removal, by the hourly purge after a closing: `purge_copies`;
`find` and `is_live` refuse it from the closing on), or with the document.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from readablestem.config import Settings
from readablestem.web import output, retention
from readablestem.web.database import as_utc
from readablestem.web.dates import format_day
from readablestem.web.db import Blob, Job, JobLink, utcnow
from readablestem.web.email_login import hash_token

CLASS = "class"  # `JobLink.kind`; the only one used
PUBLISHED = "published"  # `Blob.kind` of the published copy; `element_id` is its version id
MAX_DAYS = 180
ROW_KEEP = timedelta(days=30)
MAX_LABEL = 80
PUBLISHED_MEDIA_TYPE = "text/html; charset=utf-8"


class LinkError(ValueError):
    """The owner's request cannot be done; `field` names the form field the message is for."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


# --------------------------------------------------------------------------- is it open


def _ends(link: JobLink) -> datetime:
    """`closes_at` as an aware UTC time (SQLite returns naive ones; the column is never null)."""
    return link.closes_at.replace(tzinfo=UTC) if link.closes_at.tzinfo is None else link.closes_at


def is_live(link: JobLink, now: datetime | None = None, *, serving: bool = True) -> bool:
    """Is this link open? The one answer, for the route (`serving`), the share page and
    retention. Open means not removed, not paused, opened (`opens_at` empty or past) and not
    closed (`closes_at` in the future). With `serving=False` it answers "does this link keep
    its document": pausing and an `opens_at` still ahead do not end that, removing and closing
    do."""
    moment = as_utc(now) or utcnow()
    if link.revoked_at is not None or _ends(link) <= moment:
        return False
    if not serving:
        return True
    opens = as_utc(link.opens_at)
    return link.paused_at is None and (opens is None or opens <= moment)


def links_of(session: Session, job_id: str) -> list[JobLink]:
    """The document's links, newest first."""
    return list(
        session.scalars(
            select(JobLink)
            .where(JobLink.job_id == job_id)
            .order_by(JobLink.created_at.desc(), JobLink.id)
        )
    )


def current_link(session: Session, job_id: str) -> JobLink | None:
    """The link the share page shows: the newest one that has not been removed (open, paused
    or closed within `ROW_KEEP`, so its count can still be read), else None."""
    return next((row for row in links_of(session, job_id) if row.revoked_at is None), None)


def held_until(session: Session, job_id: str, now: datetime | None = None) -> datetime | None:
    """When the document's links stop working: the latest `closes_at` of a link that keeps
    it, or None when none does."""
    moment = as_utc(now) or utcnow()
    ends = [_ends(row) for row in links_of(session, job_id) if is_live(row, moment, serving=False)]
    return max(ends) if ends else None


def retention_for(
    session: Session, job: Job, settings: Settings, now: datetime | None = None
) -> datetime | None:
    """The `retain_until` the document's links give it: their last day plus the window it goes
    back to (`retention.after_link`), or None when no link keeps it."""
    closing = held_until(session, job.id, now)
    return None if closing is None else retention.after_link(job, settings, closing)


def keeping(
    session: Session, job: Job, settings: Settings, now: datetime | None = None
) -> datetime | None:
    """The last day of the link that decides when the document is deleted, for the job page's
    line: None when no link keeps it, when it is kept longer for another reason (7 days, or
    30 for `dev`), or when the link adds nothing."""
    closing = held_until(session, job.id, now)
    if closing is None or as_utc(job.retain_until) != retention.after_link(job, settings, closing):
        return None
    if job.retention_mode == "dev" and (
        closing + retention.window(settings, "day")
        <= (as_utc(job.created_at) or closing) + retention.window(settings, "dev")
    ):
        return None
    return closing


def extend(session: Session, job: Job, settings: Settings, now: datetime | None = None) -> None:
    """After the document's mode changed under a link ("Keep for 7 days"): the link's window
    is now the new mode's. Never shortens."""
    target = retention_for(session, job, settings, now)
    current = as_utc(job.retain_until)
    if target is not None and (current is None or current < target):
        job.retain_until = target


# --------------------------------------------------------------------------- making


def close_of(day: date) -> datetime:
    """The end of the last day a link works: 23:59:59 UTC of the date the owner chose (the
    page says UTC, as every date in the product does)."""
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


def last_day(now: datetime) -> date:
    """The last day a link made at `now` may work: the end of it is no more than `MAX_DAYS`
    after `now`."""
    limit = now + timedelta(days=MAX_DAYS)
    day = limit.date()
    return day if close_of(day) <= limit else day - timedelta(days=1)


def check_dates(
    opens: date | None, closes: date | None, now: datetime
) -> tuple[datetime | None, datetime]:
    """The window the owner typed, as moments, or `LinkError` with the words for the field."""
    if closes is None:
        raise LinkError("link-closes", "Choose the last day the link works.")
    if closes < now.date():
        raise LinkError("link-closes", "Choose a day that has not passed.")
    latest = last_day(now)
    if closes > latest:
        raise LinkError(
            "link-closes",
            f"A link can work for at most {MAX_DAYS} days. Choose a day on or before "
            f"{format_day(latest)}.",
        )
    if opens is not None and opens > closes:
        raise LinkError("link-opens", "The first day cannot be after the last day.")
    return (datetime.combine(opens, time(0, 0), tzinfo=UTC) if opens else None), close_of(closes)


def make(
    session: Session,
    job: Job,
    settings: Settings,
    *,
    label: str,
    opens: date | None,
    closes: date | None,
    now: datetime | None = None,
) -> str:
    """A new class link for `job`: its token, which is not stored and is shown to the owner
    once. The link the document had is removed (one link at a time: "Make a new
    link" is how a lost address is replaced). The caller has checked that the document is its
    owner's and ready, and publishes the copy (`store_copy`) in the same transaction."""
    moment = as_utc(now) or utcnow()
    opens_at, closes_at = check_dates(opens, closes, moment)
    before = retention_for(session, job, settings, moment)
    for old in links_of(session, job.id):
        if old.revoked_at is None:
            old.revoked_at = moment
    token = secrets.token_urlsafe(32)
    row = JobLink(
        job_id=job.id,
        token_hash=hash_token(token),
        kind=CLASS,
        label=" ".join(label.split())[:MAX_LABEL],
        opens_at=opens_at,
        closes_at=closes_at,
        created_at=moment,
    )
    session.add(row)
    session.flush()
    reconcile(session, job, settings, before, moment)
    # Written now: the caller publishes the copy next, and `current_html` refreshes the job row,
    # which would drop an unflushed `retain_until` and leave the link keeping nothing.
    session.flush()
    return token


def pause(session: Session, job: Job, *, paused: bool, now: datetime | None = None) -> bool:
    """Pause the document's link, or resume it. False when there is nothing to change."""
    moment = as_utc(now) or utcnow()
    row = current_link(session, job.id)
    if (
        row is None
        or (row.paused_at is not None) == paused
        or not is_live(row, moment, serving=False)
    ):
        return False
    row.paused_at = moment if paused else None
    return True


def remove(session: Session, job: Job, settings: Settings, now: datetime | None = None) -> bool:
    """Remove the document's link: it stops working at once and the document goes back to its
    earlier retention. False when there is none."""
    moment = as_utc(now) or utcnow()
    before = retention_for(session, job, settings, moment)
    row = current_link(session, job.id)
    if row is None:
        return False
    row.revoked_at = moment
    reconcile(session, job, settings, before, moment)
    return True


def reconcile(
    session: Session, job: Job, settings: Settings, before: datetime | None, now: datetime
) -> None:
    """After a link was made or removed: `retain_until` is what the links that keep the document
    give it (`retention_for`), and when none is left it goes back to its retention with the
    clock starting now (`retention.clock_after_link`). `before` is `retention_for` as it was
    before the change; a document is kept by its link only when its `retain_until` was that
    (a document kept longer for another reason, 7 days or 30, is not shortened by a link). The
    published copy is deleted when no link keeps the document."""
    after = retention_for(session, job, settings, now)
    current = as_utc(job.retain_until)
    by_link = before is not None and current is not None and current <= before
    if after is not None:
        job.retain_until = after if by_link or current is None else max(current, after)
        return
    if by_link:
        retention.clock_after_link(job, settings, now)
    session.execute(delete(Blob).where(Blob.job_id == job.id, Blob.kind == PUBLISHED))


# --------------------------------------------------------------------------- the published copy


@dataclass(frozen=True)
class Published:
    version: str  # the version id the file says (`output.version_id`)
    html: str


def published(session: Session, job_id: str) -> Published | None:
    row = session.execute(
        select(Blob.element_id, Blob.content).where(Blob.job_id == job_id, Blob.kind == PUBLISHED)
    ).first()
    if row is None:
        return None
    return Published(row[0] or "", bytes(row[1]).decode("utf-8"))


def published_version(session: Session, job_id: str) -> str | None:
    return session.scalar(
        select(Blob.element_id).where(Blob.job_id == job_id, Blob.kind == PUBLISHED)
    )


def store_copy(session: Session, job: Job, html: str) -> str:
    """Keep `html` (the ready student file, up to date: `routes.jobs.current_html`) as the
    published copy, replacing the earlier one, with its version id. Returns the version id.
    Only a ready document is published: the caller checks `output.export_state`."""
    version = output.version_id(job)
    session.execute(delete(Blob).where(Blob.job_id == job.id, Blob.kind == PUBLISHED))
    session.add(
        Blob(
            job_id=job.id,
            kind=PUBLISHED,
            element_id=version,
            media_type=PUBLISHED_MEDIA_TYPE,
            content=html.encode("utf-8"),
        )
    )
    return version


# --------------------------------------------------------------------------- opening


def find(session: Session, token_hash: str, now: datetime) -> tuple[JobLink, Job] | None:
    """The link and document a token opens now, or None for every other case (no such token,
    removed, paused, not yet open, closed, document gone or past its time): the caller cannot
    tell them apart, and neither can a person guessing."""
    row = session.scalar(select(JobLink).where(JobLink.token_hash == token_hash))
    if row is None or not is_live(row, now):
        return None
    job = session.get(Job, row.job_id)
    if job is None or (as_utc(job.retain_until) or now) <= now:
        return None
    return row, job


def record_open(session: Session, link: JobLink, now: datetime) -> None:
    """Count one open, and the first. One UPDATE, so two opens at once are both counted."""
    session.execute(
        update(JobLink)
        .where(JobLink.id == link.id)
        .values(
            open_count=JobLink.open_count + 1,
            first_opened_at=func.coalesce(JobLink.first_opened_at, now),
        )
        .execution_options(synchronize_session=False)
    )


# --------------------------------------------------------------------------- lifecycle (purge)


def purge_copies(session: Session, now: datetime) -> int:
    """For the hourly purge: delete the published copy of every document that no link keeps
    (every link removed or closed). A closed link's copy was never served after its closing
    (`find`); this frees it. The document itself expires on its own clock, so nothing here
    touches `retain_until`. Returns how many copies went."""
    kept = (
        select(JobLink.id)
        .where(
            JobLink.job_id == Blob.job_id,
            JobLink.revoked_at.is_(None),
            JobLink.closes_at > now,
        )
        .exists()
    )
    result = session.execute(delete(Blob).where(Blob.kind == PUBLISHED, ~kept))
    return int(getattr(result, "rowcount", 0) or 0)


def purge_rows(session: Session, now: datetime) -> int:
    """Delete the rows of links closed or removed more than `ROW_KEEP` ago."""
    cutoff = (as_utc(now) or utcnow()) - ROW_KEEP
    result = session.execute(
        delete(JobLink).where(or_(JobLink.closes_at < cutoff, JobLink.revoked_at < cutoff))
    )
    return int(getattr(result, "rowcount", 0) or 0)
