"""Heartbeats of the scheduled tasks: the proof that a daily backup and the hourly cron ran.

Each task writes one row of `ops_heartbeat` when it finishes well: the cron service after a
successful `ops cron` (here), the backup service after its dump has been restored and checked
(a `psql` upsert at the end of its start command in `.railway/railway.ts`). `GET /healthz/ops`
(`web.health.ops_freshness`) reads them, and the outside check (`.github/workflows/uptime.yml`)
turns a stale one into an email. A row holds a name, a time and a short note of counts, never
document content.

A task that has never run has no row and counts as stale, so the check stays unhealthy until
both have run once after the first deploy that has this table.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from readablestem.web.database import as_utc, insert_ignore
from readablestem.web.db import OpsHeartbeat, utcnow

BACKUP = "backup"
CRON = "cron"

# The backup runs daily (08:17 UTC): 26 hours is one day and two of slack. The cron runs
# hourly (minute 7): two hours is one missed run.
MAX_AGE: dict[str, timedelta] = {BACKUP: timedelta(hours=26), CRON: timedelta(hours=2)}


def beat(
    session: Session, name: str, detail: str | None = None, now: datetime | None = None
) -> None:
    """Record that `name` finished well, in the caller's transaction."""
    moment = now or utcnow()
    insert_ignore(session, OpsHeartbeat, {"name": name, "last_ok_at": moment, "detail": detail})
    session.execute(
        update(OpsHeartbeat)
        .where(OpsHeartbeat.name == name)
        .values(last_ok_at=moment, detail=detail)
        .execution_options(synchronize_session=False)
    )


def stale_names(session: Session, now: datetime | None = None) -> list[str]:
    """The tasks whose last good run is older than `MAX_AGE` or that never ran, sorted."""
    moment = now or utcnow()
    seen = {
        row.name: as_utc(row.last_ok_at)
        for row in session.execute(select(OpsHeartbeat.name, OpsHeartbeat.last_ok_at))
    }
    stale = []
    for name, limit in MAX_AGE.items():
        last = seen.get(name)
        if last is None or moment - last > limit:
            stale.append(name)
    return sorted(stale)
