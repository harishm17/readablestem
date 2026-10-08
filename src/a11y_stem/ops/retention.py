"""Retention (WP6 implements): delete jobs past `retain_until` with everything derived
from the document (upload, crops, result, HTML, review edits) via the cascade.

Deletion is mandatory: it runs even if aggregation failed for a job (privacy beats stats).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker


@dataclass
class PurgeStats:
    jobs_deleted: int
    blobs_deleted: int
    unaggregated_deleted: int  # finished jobs deleted before they were aggregated


def purge_expired(factory: sessionmaker[Session], now: datetime | None = None) -> PurgeStats:
    raise NotImplementedError("WP6")
