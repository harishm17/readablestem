"""How long a job is kept, and what restarts the clock (T1-8 of the improvements list).

Three choices on the upload form, stored as `Job.retention_mode`:

- `day`: nothing ticked. Deleted `RETENTION_HOURS` (24) after the last activity.
- `week`: "Keep for 7 days". Deleted 7 days after the last activity;
  nobody else looks at it.
- `dev`: "Also let us look at the results (30 days)". Kept 30 days from upload, and the
  developer may look at the results to find and fix mistakes in the tool
  (`Job.opt_in_retention`, which `ops.export` reads, is true exactly for this mode).

Activity is a review decision, a language, title or contact change, and a download: it moves
`retain_until` to a full window from now for `day` and `week`, never earlier than it was.
`dev` keeps its 30 days from upload, because the developer's permission was given for that
long. "Keep for 7 days" on the job page makes a `day` job a `week` job and starts its
seven days now; for a `dev` job it changes nothing.

The purge (`ops.retention.purge_expired`) deletes by `retain_until` and knows nothing of
modes, so this module is the whole of the behaviour. Pure functions on the job row; the
caller commits.

This module is also where every sentence about retention is written: the job page's
kept-until line, the notices that name a new time, the expired page, the list's last-hours
mark. The numbers in them come from `Settings` and `WEEK_DAYS`, never from a template.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from readablestem.config import Settings
from readablestem.web.database import as_utc
from readablestem.web.dates import format_when
from readablestem.web.db import Job, utcnow

Mode = Literal["day", "week", "dev"]
WEEK_DAYS = 7
# The documents list marks a document as ending soon when this little of its time is left
# (a quarter of the shortest window, `retention_hours` by default).
LAST_HOURS = timedelta(hours=6)
# An action says the new deletion time only when it moved the time by at least this much: a
# run of decisions moves it by seconds each, and a notice under every one would be noise.
WORTH_SAYING = timedelta(hours=1)


def mode_for_upload(*, keep: bool, share: bool) -> Mode:
    """The upload form's two boxes. Letting us look at the results is the longer choice and
    includes keeping the work, whether or not the first box is ticked too."""
    if share:
        return "dev"
    return "week" if keep else "day"


def window(settings: Settings, mode: str) -> timedelta:
    """How long a job of this mode is kept from the last activity (from upload for `dev`)."""
    if mode == "dev":
        return timedelta(days=settings.opt_in_retention_days)
    if mode == "week":
        return timedelta(days=WEEK_DAYS)
    return timedelta(hours=settings.retention_hours)


def retain_until_at_upload(settings: Settings, mode: str, now: datetime) -> datetime:
    return now + window(settings, mode)


def restart_clock(job: Job, settings: Settings, now: datetime | None = None) -> bool:
    """Activity on the job: a full window from now for `day` and `week`. True when
    `retain_until` moved. Never shortens, and never touches a `dev` job."""
    if job.retention_mode == "dev":
        return False
    moment = now or utcnow()
    target = moment + window(settings, job.retention_mode)
    current = as_utc(job.retain_until)
    if current is not None and current >= target:
        return False
    job.retain_until = target
    return True


def can_keep_longer(job: Job) -> bool:
    """The job page offers "Keep for 7 days" unless the developer's 30 days apply."""
    return job.retention_mode != "dev"


def keep_offer(
    mode: str, retain_until: datetime | None, now: datetime | None = None
) -> datetime | None:
    """When the document would be deleted after "Keep for 7 days" (seven days from now), or
    None when the button would change nothing: the developer's 30 days apply, or the
    document is already kept at least that long (an hour of margin, so a 7-day document
    worked on a moment ago does not offer it again)."""
    if mode == "dev":
        return None
    target = (now or utcnow()) + timedelta(days=WEEK_DAYS)
    current = as_utc(retain_until)
    if current is not None and current >= target - timedelta(hours=1):
        return None
    return target


def keep_seven_more_days(job: Job, now: datetime | None = None) -> bool:
    """ "Keep for 7 days": from now, only the owner sees it. A `day` job becomes a `week`
    job. False (nothing changed) for a `dev` job."""
    if job.retention_mode == "dev":
        return False
    moment = now or utcnow()
    job.retention_mode = "week"
    target = moment + timedelta(days=WEEK_DAYS)
    current = as_utc(job.retain_until)
    if current is None or current < target:
        job.retain_until = target
    return True


# --------------------------------------------------------------------------- the words


def window_words(settings: Settings, mode: str) -> str:
    """How long a document of this mode is kept: "24 hours", "7 days", "30 days"."""
    return span_words(window(settings, mode))


def kept_line(settings: Settings, mode: str, retain_until: datetime | None) -> str:
    """The job page's one line: the day and time the document is kept until, and why."""
    when = format_when(retain_until)
    if not when:
        return ""
    if mode == "dev":
        return (
            f"Kept until {when}, {window_words(settings, mode)} after upload. "
            "Working on it does not change that."
        )
    return (
        f"Kept until {when}, {window_words(settings, mode)} after you last work on it. "
        "Your decisions, edits and downloads restart that time; sharing does not."
    )


def keep_hint(until: datetime) -> str:
    """Beside "Keep for 7 days": the time it would be kept until."""
    return f"Then it is kept until {format_when(until)}."


def keep_done(until: datetime | None) -> str:
    """The notice after "Keep for 7 days" worked."""
    return (
        f"This document will be kept for {WEEK_DAYS} days from now"
        f"{', until ' + format_when(until) if until else ''}. "
        "Only you and the people you share it with can see it."
    )


def keep_already() -> str:
    """The notice after "Keep for 7 days" when nothing changed."""
    return f"This document is already kept for longer than {WEEK_DAYS} days."


def keep_label() -> str:
    return f"Keep for {WEEK_DAYS} days"


def restart_clock_and_say(job: Job, settings: Settings, now: datetime | None = None) -> str:
    """`restart_clock`, and the sentence that says the new time ("" when nothing moved, or
    it moved by less than `WORTH_SAYING`). The caller puts it in the notice that follows."""
    before = as_utc(job.retain_until)
    if not restart_clock(job, settings, now):
        return ""
    after = as_utc(job.retain_until)
    if before is not None and after is not None and after - before < WORTH_SAYING:
        return ""
    return f"It is now kept until {format_when(after)}."


def last_hours_note(retain_until: datetime | None, now: datetime | None = None) -> str:
    """The list's mark on a document in its last hours ("" for any other)."""
    left = _left(retain_until, now)
    if left is None or left > LAST_HOURS:
        return ""
    hours = max(1, -(-int(left.total_seconds()) // 3600))
    return f"Deleted in under {hours} {'hour' if hours == 1 else 'hours'}"


def _left(retain_until: datetime | None, now: datetime | None) -> timedelta | None:
    end = as_utc(retain_until)
    return None if end is None else max(end - (now or utcnow()), timedelta(0))


def span_words(delta: timedelta) -> str:
    """ "24 hours", "30 days": how long a document was kept. "" for no time at all, so a date
    that puts the end before the start (or equal to it) never prints a negative span."""
    hours = round(delta.total_seconds() / 3600)
    if hours < 1:
        return ""
    if hours >= 48 and hours % 24 == 0:
        return f"{hours // 24} days"
    return f"{hours} {'hour' if hours == 1 else 'hours'}"


def deleted_line(deleted_on: datetime | None, uploaded_on: datetime | None, kept: str) -> str:
    """The first sentence of the expired page: what is known of when it went. After the
    purge the row is gone, and only the upload date may be known."""
    if deleted_on is not None:
        after = f", after {kept} in all" if kept else ""
        return f"Deleted automatically on {format_when(deleted_on)}{after}."
    if uploaded_on is not None:
        return (
            f"You uploaded it on {format_when(uploaded_on)}. When it was deleted is not recorded."
        )
    return ""


def rules_line(settings: Settings) -> str:
    """The rules, as the expired page and the limits say them."""
    return (
        f"Documents go {window_words(settings, 'day')} after you last work on them "
        f"({WEEK_DAYS} days if you keep them, {window_words(settings, 'dev')} from upload if "
        "you let us look at the results). Files you downloaded are not affected."
    )
