"""Daily error aggregation (WP6 implements). Runs before retention deletes documents.

Counts, per UTC day of job completion: fixes by kind and status, failed check codes, fix
reasons, model call outcomes, fallback and escalation counts, job error codes and review
actions. Writes `ErrorAggregate` rows (upsert) and marks each job `aggregated=True`.
Category codes only; never document text.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker


def aggregate_finished(factory: sessionmaker[Session], now: datetime | None = None) -> int:
    """Aggregate every finished, not-yet-aggregated job. Returns the number of jobs."""
    raise NotImplementedError("WP6")
