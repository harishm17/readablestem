"""The order the worker takes queued jobs in: round-robin between people.

Without this, one person who queues ten documents is served all ten before the person who
queued one a minute later (oldest first). The order is:

1. fewest attempts (a job requeued after a lost lease, or a PDF that keeps killing the
   process, still goes behind everyone's fresh work),
2. the person served least recently: the owner whose latest job start is oldest (someone
   never served goes first),
3. oldest, then id, which is the old order, so one person alone is served exactly as before.

`Worker.claim` takes the first job in this order (`claim_order_by`, in SQL). The job page's
"documents ahead of yours" must say the same thing, so `queue_order` replays the claims
in Python: after each simulated claim the owner counts as just served, so the next job is
another person's. `tests/test_web_fair_queue.py` checks that the two agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, aliased

from readablestem.web.database import as_utc
from readablestem.web.db import Job


@dataclass(frozen=True)
class Queued:
    """One queued job as the order sees it (ids and counts only)."""

    id: str
    user_id: str
    attempts: int
    created_at: datetime
    page_count: int = 0


def last_started(session: Session, user_ids: Iterable[str]) -> dict[str, datetime]:
    """When each of these people's most recent job started (people never served are
    left out)."""
    ids = sorted(set(user_ids))
    if not ids:
        return {}
    rows = session.execute(
        select(Job.user_id, func.max(Job.started_at))
        .where(Job.user_id.in_(ids), Job.started_at.is_not(None))
        .group_by(Job.user_id)
    ).all()
    out: dict[str, datetime] = {}
    for user_id, started in rows:
        when = as_utc(started)
        if when is not None:
            out[user_id] = when
    return out


def claim_order_by(job: Any = Job) -> list[ColumnElement[Any]]:
    """ORDER BY terms for `Worker.claim` over `job` (the `Job` entity of the query)."""
    other = aliased(Job)
    served = (
        select(func.max(other.started_at))
        .where(other.user_id == job.user_id, other.started_at.is_not(None))
        .correlate(job)
        .scalar_subquery()
    )
    return [job.attempts, served.asc().nulls_first(), job.created_at, job.id]


def queue_order(queued: Sequence[Queued], served: dict[str, datetime]) -> list[Queued]:
    """The queued jobs in the order successive claims take them.

    `served` is `last_started` for their owners. Each simulated claim marks its owner as
    served after everyone else, which is the round-robin."""
    pending = list(queued)
    replayed: dict[str, int] = {}  # owner -> turn of their simulated claim

    def key(item: Queued) -> tuple[int, int, float, int, datetime, str]:
        if item.user_id in replayed:  # served in this replay: after every real timestamp
            tier, when, turn = 2, 0.0, replayed[item.user_id]
        elif item.user_id in served:
            tier, when, turn = 1, served[item.user_id].timestamp(), 0
        else:  # never served
            tier, when, turn = 0, 0.0, 0
        return (item.attempts, tier, when, turn, item.created_at, item.id)

    ordered: list[Queued] = []
    while pending:
        best = min(pending, key=key)
        pending.remove(best)
        ordered.append(best)
        replayed[best.user_id] = len(ordered)
    return ordered
