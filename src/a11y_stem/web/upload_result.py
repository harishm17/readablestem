"""The answer to an upload of several files, kept for a short time (T3-4).

The upload is a POST. If its answer were the page of what was queued and what was refused,
a refresh would send every file again. So the outcome of each file is stored here, under
the person's id, and the POST answers with a redirect to `GET /app/upload/{id}`; opening
that page again changes nothing.

What is stored is what the page shows: for each file its display name, page count, the job
or large-file id it became, or the refusal category with the numbers its message needs.
The file names are document-derived text, so a row is deleted `KEPT_FOR` after it was made
(`ops.retention.purge_upload_results`) and with the person; it is never in the session
cookie, which carries no file names. Only the person who uploaded can open it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from a11y_stem.web.database import as_utc
from a11y_stem.web.db import UploadResult, utcnow

KEPT_FOR = timedelta(hours=1)

FIELDS = (
    "filename",
    "pages",
    "job_id",
    "pending_id",
    "code",
    "status",
    "left",
    "retry_after",
    "office",
)


def store(
    session: Session, user_id: str, items: list[dict[str, Any]], now: datetime | None = None
) -> str:
    """Keep the outcomes of one upload for its owner; returns the id for the redirect."""
    moment = now or utcnow()
    row = UploadResult(
        user_id=user_id,
        items=[{key: item.get(key) for key in FIELDS} for item in items],
        created_at=moment,
        retain_until=moment + KEPT_FOR,
    )
    session.add(row)
    session.flush()
    return row.id


def load(
    session: Session, user_id: str, result_id: str, now: datetime | None = None
) -> list[dict[str, Any]] | None:
    """The outcomes stored under `result_id` when they are this person's and not past their
    time; else None (the same answer for someone else's, a missing and an old one)."""
    row = session.get(UploadResult, result_id)
    if row is None or row.user_id != user_id or as_utc(row.retain_until) <= (now or utcnow()):
        return None
    return [dict(item) for item in row.items]
