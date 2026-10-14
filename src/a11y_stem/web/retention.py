"""How long a job is kept, and what restarts the clock (T1-8 of the improvements list).

Three choices on the upload form, stored as `Job.retention_mode`:

- `day`: nothing ticked. Deleted `RETENTION_HOURS` (24) after the last activity.
- `week`: "Keep for 7 days, only I can see it". Deleted 7 days after the last activity;
  nobody else looks at it.
- `dev`: "Also let the developer look (30 days)". Kept 30 days from upload, and the
  developer may look at the results to find and fix mistakes in the tool
  (`Job.opt_in_retention`, which `ops.export` reads, is true exactly for this mode).

Activity is a review decision, a language, title or contact change, and a download: it moves
`retain_until` to a full window from now for `day` and `week`, never earlier than it was.
`dev` keeps its 30 days from upload, because the developer's permission was given for that
long. "Keep 7 more days" on the job page makes a `day` job a `week` job and starts its
seven days now; for a `dev` job it changes nothing.

The purge (`ops.retention.purge_expired`) deletes by `retain_until` and knows nothing of
modes, so this module is the whole of the behaviour. Pure functions on the job row; the
caller commits.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from a11y_stem.config import Settings
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import Job, utcnow

Mode = Literal["day", "week", "dev"]
WEEK_DAYS = 7


def mode_for_upload(*, keep: bool, share: bool) -> Mode:
    """The upload form's two boxes. Letting the developer look is the longer choice and
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
    """The job page offers "Keep 7 more days" unless the developer's 30 days apply."""
    return job.retention_mode != "dev"


def keep_seven_more_days(job: Job, now: datetime | None = None) -> bool:
    """ "Keep 7 more days": from now, only the owner sees it. A `day` job becomes a `week`
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
