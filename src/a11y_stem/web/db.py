"""Database schema (SQLAlchemy 2). Postgres in production, SQLite for local dev and tests.

Retention model: everything derived from a user's document (the upload, crops, the
result JSON, the output HTML, review edits) hangs off `jobs` and is deleted with it by
`ops.retention`. Tables that survive deletion (`spend_days`, `quota_days`,
`error_aggregates`) hold numbers and category codes only, never document text.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    jobs: Mapped[list[Job]] = relationship(back_populates="user")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED, index=True)
    # Original filename is document-derived, so it is deleted with the job.
    filename: Mapped[str] = mapped_column(String(255))
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    opt_in_retention: Mapped[bool] = mapped_column(Boolean, default=False)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Worker lease: a RUNNING job whose lease expired is requeued (crash recovery).
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))  # category, never text
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict | None] = mapped_column(JSON)  # DocumentResult without html
    html: Mapped[str | None] = mapped_column(Text)
    aggregated: Mapped[bool] = mapped_column(Boolean, default=False)  # counted in aggregates

    user: Mapped[User] = relationship(back_populates="jobs")
    blobs: Mapped[list[Blob]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    edits: Mapped[list[ReviewEdit]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


class Blob(Base):
    """Binary content tied to a job: the uploaded PDF and per-element crops (PNG)."""

    __tablename__ = "blobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "upload" | "crop"
    element_id: Mapped[str | None] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="blobs")

    __table_args__ = (UniqueConstraint("job_id", "kind", "element_id", name="uq_blob_slot"),)


class ReviewEdit(Base):
    """A reviewer's decision on one fix. Kept only as long as its job."""

    __tablename__ = "review_edits"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    element_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))  # "accept" | "edit" | "reject"
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="edits")


class SpendDay(Base):
    """Global model spend per UTC day. Updated with conditional UPDATEs (see web.spend)."""

    __tablename__ = "spend_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    held_usd: Mapped[float] = mapped_column(Float, default=0.0)


class QuotaDay(Base):
    """Pages a user has submitted per UTC day."""

    __tablename__ = "quota_days"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    pages: Mapped[int] = mapped_column(Integer, default=0)


class ErrorAggregate(Base):
    """Daily counts by step and category. Written before deletion; never holds text."""

    __tablename__ = "error_aggregates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    step: Mapped[str] = mapped_column(String(32))  # "equation" | "figure" | "heading" | "job"
    category: Mapped[str] = mapped_column(String(64))  # e.g. "status:needs_review"
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("day", "step", "category", name="uq_aggregate"),)


# --------------------------------------------------------------------------- engine


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
            from pathlib import Path

            Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            database_url, connect_args={"check_same_thread": False}, pool_pre_ping=True
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return engine
    # 2 instances x (3 + 2) = 10 connections, well under db-f1-micro's 25.
    return create_engine(database_url, pool_pre_ping=True, pool_size=3, max_overflow=2)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Create tables (dev/tests). Production uses Alembic migrations (migrations/)."""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Generator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
