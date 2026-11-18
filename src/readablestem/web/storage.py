"""How much each person, and the whole service, has stored: the caps on it.

Uploads and what is made from them live in the database (`blobs`, `pending_uploads`, the
job's HTML and result), and the database is on a volume of fixed size. With the upload limits
at 50 and 100 MB one person could fill it, so two limits apply, both counted from what is
stored now (documents past their deletion time do not count; the hourly purge catches up
later):

- per person (`STORAGE_PER_USER_MB` invited, `STORAGE_OPEN_TIER_MB` for accounts made by open
  sign-up): everything of theirs, the uploads kept for choosing pages, the source PDFs, the
  crops and the results. A new upload that would take them over is refused, in the same
  transaction as the job (`intake.store_job`, `pending.create`), with the person's row locked
  on Postgres so two uploads of one person cannot both pass;
- global (`STORAGE_GLOBAL_MAX_MB`): stored upload bytes (source PDFs, crops and kept large
  files) of everybody. Over it new uploads get a plain 503 until the purge has caught up;
  `/healthz/ops` lists "storage" among the stale names while it is over.

Counts are bytes of content only; nothing here logs a name or a text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from readablestem.config import Settings
from readablestem.web import access
from readablestem.web.db import Blob, Job, JobStatus, PendingUpload, StepRecord, User, utcnow

MB = 1024 * 1024

# What a stored document is counted as, by every figure here (`user_bytes`, `total_bytes`
# and `finished_job_bytes`). Text lengths are characters, near enough to bytes for a cap in
# megabytes.
_BLOB_BYTES = func.length(Blob.content)
_HTML_BYTES = func.coalesce(func.length(Job.html), 0)
_RESULT_BYTES = func.coalesce(func.length(cast(Job.result_json, Text)), 0)


class StorageRefused(Exception):
    """The upload would take the person over their stored-bytes cap. `used_mb` (rounded up)
    and `limit_mb` are whole megabytes."""

    def __init__(self, used_mb: int, limit_mb: int) -> None:
        super().__init__("storage_user")
        self.used_mb = used_mb
        self.limit_mb = limit_mb


class StorageFull(Exception):
    """The service as a whole holds as much as it will until the purge frees space."""

    code = "storage_full"


def _mb_up(n: int) -> int:
    return -(-n // MB)


def user_bytes(session: Session, user_id: str, now: datetime | None = None) -> int:
    """Bytes the person stores: blobs and the HTML and result of their documents not yet past
    deletion, and their kept large files. The sample document is ours and not counted."""
    moment = now or utcnow()
    live = (Job.user_id == user_id, Job.retain_until > moment, Job.is_sample.is_(False))
    blobs = session.scalar(
        select(func.coalesce(func.sum(_BLOB_BYTES), 0))
        .select_from(Blob)
        .join(Job, Job.id == Blob.job_id)
        .where(*live)
    )
    texts = session.scalar(
        select(func.coalesce(func.sum(_HTML_BYTES + _RESULT_BYTES), 0)).where(*live)
    )
    pending = session.scalar(
        select(func.coalesce(func.sum(PendingUpload.size_bytes), 0)).where(
            PendingUpload.user_id == user_id, PendingUpload.retain_until > moment
        )
    )
    return int(blobs or 0) + int(texts or 0) + int(pending or 0)


def total_bytes(session: Session, now: datetime | None = None) -> int:
    """Stored upload bytes of everybody: blobs of documents not yet past deletion (the sample
    too: it is stored) and kept large files. `length` of a bytea is read from the row's
    header on Postgres, not by reading the value."""
    moment = now or utcnow()
    blobs = session.scalar(
        select(func.coalesce(func.sum(_BLOB_BYTES), 0))
        .select_from(Blob)
        .join(Job, Job.id == Blob.job_id)
        .where(Job.retain_until > moment)
    )
    pending = session.scalar(
        select(func.coalesce(func.sum(PendingUpload.size_bytes), 0)).where(
            PendingUpload.retain_until > moment
        )
    )
    return int(blobs or 0) + int(pending or 0)


def global_full(session: Session, settings: Settings, now: datetime | None = None) -> bool:
    return total_bytes(session, now) >= settings.storage_global_max_mb * MB


def check_global(session: Session, settings: Settings, now: datetime | None = None) -> None:
    """Raise `StorageFull` when the service holds its limit."""
    if global_full(session, settings, now):
        raise StorageFull


def check_room(
    session: Session, settings: Settings, user_id: str, incoming: int, now: datetime | None = None
) -> None:
    """Raise `StorageFull` or `StorageRefused` when `incoming` bytes should not be stored for
    the person. Locks the person's row first (a no-op on SQLite), so two uploads of one
    person are counted one after the other."""
    session.execute(select(User.id).where(User.id == user_id).with_for_update()).first()
    check_global(session, settings, now)
    cap = access.storage_limit_mb(settings, access.tier_of(session, user_id))
    used = user_bytes(session, user_id, now)
    if used + incoming > cap * MB:
        raise StorageRefused(_mb_up(used), cap)


def report(session: Session, settings: Settings) -> dict[str, int | bool]:
    """For `/healthz/ops`: stored and limit in MB, and whether new uploads are refused."""
    total = total_bytes(session)
    return {
        "stored_mb": total // MB,
        "limit_mb": settings.storage_global_max_mb,
        "full": total >= settings.storage_global_max_mb * MB,
    }


# --------------------------------------------------------------------------- measuring


@dataclass(frozen=True)
class JobBytes:
    """What one finished document stores: its upload (the source PDF), its crops, the HTML
    file, the result and the step records "Try again" reuses. Bytes of content, no name."""

    job_id: str
    upload: int
    crops: int
    html: int
    result: int
    steps: int

    @property
    def total(self) -> int:
        return self.upload + self.crops + self.html + self.result + self.steps


def finished_job_bytes(session: Session) -> list[JobBytes]:
    """The stored bytes of every finished document (`ops storage-report`), counted as
    `user_bytes` counts them, with the step records."""
    jobs = session.execute(
        select(Job.id, _HTML_BYTES, _RESULT_BYTES).where(Job.status == JobStatus.DONE)
    ).all()
    blobs: dict[tuple[str, str], int] = {
        (job_id, kind): int(n or 0)
        for job_id, kind, n in session.execute(
            select(Blob.job_id, Blob.kind, func.sum(_BLOB_BYTES)).group_by(Blob.job_id, Blob.kind)
        )
    }
    steps: dict[str, int] = {
        job_id: int(n or 0)
        for job_id, n in session.execute(
            select(
                StepRecord.job_id, func.sum(func.length(cast(StepRecord.fix_json, Text)))
            ).group_by(StepRecord.job_id)
        )
    }
    return [
        JobBytes(
            job_id,
            upload=blobs.get((job_id, "upload"), 0),
            crops=blobs.get((job_id, "crop"), 0),
            html=int(html),
            result=int(result),
            steps=steps.get(job_id, 0),
        )
        for job_id, html, result in jobs
    ]


def table_bytes(session: Session) -> tuple[dict[str, int], int | None]:
    """Bytes on disk of each table with its indexes and out-of-line values, and the database's
    own size figure (Postgres `pg_database_size`; for SQLite the file's pages), both None or
    empty when the database cannot say. What is in the second and not the first is the
    database's own catalogs and free pages."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        tables = {
            name: int(size)
            for name, size in session.execute(
                text(
                    "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind = 'r' AND n.nspname = current_schema()"
                )
            )
        }
        return tables, int(session.scalar(text("SELECT pg_database_size(current_database())")) or 0)
    if dialect == "sqlite":
        pages = session.scalar(text("PRAGMA page_count")) or 0
        total = int(pages) * int(session.scalar(text("PRAGMA page_size")) or 0)
        try:
            # dbstat lists every page of every table and index (a build of SQLite without it
            # still gives the file's size above).
            owner = dict(session.execute(text("SELECT name, tbl_name FROM sqlite_master")).all())
            tables: dict[str, int] = {}
            for name, size in session.execute(
                text("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name")
            ):
                table = owner.get(name, name)
                tables[table] = tables.get(table, 0) + int(size)
            return tables, total
        except OperationalError:  # no dbstat in this SQLite build
            return {}, total
    return {}, None
