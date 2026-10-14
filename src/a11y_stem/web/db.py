"""Database schema (SQLAlchemy 2). Postgres in production, SQLite for local dev and tests.

Retention model: everything derived from a user's document (the upload, crops, the
result JSON, the output HTML, review edits, stage progress) hangs off `jobs` and is
deleted with it by `ops.retention`. Tables that survive a job's deletion (`spend_days`,
`quota_days`, `error_aggregates`, `events`) hold numbers and category codes only, never
document text.

User content that is not a document (a user's `courses` and their `notation_entries`)
and sign-in data (`auth_identities`) belong to the user: they stay until the user deletes
them or is erased (`ops.retention.erase_user`, which deletes every table that holds a
user id). `events` are pseudonymous usage events (a user id, a name, a code, a number,
a time), content-free by construction: no column can hold text from a document.
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
    false,
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
    # Bumped on sign-out and on removal from the allowlist: a session signed with an
    # older value (a copied cookie, another browser) no longer signs anyone in. The
    # server default keeps rows inserted by an older release valid after a rollback.
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

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
    # The course whose notation the conversion used; a deleted course leaves the job.
    # Named, so the migration can drop it again (SQLite rebuilds the table to do so).
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL", name="fk_jobs_course_id_courses"),
        index=True,
    )
    # Live progress by stage (`core.types.ProgressEvent` as JSON): content-free.
    progress_json: Mapped[dict | None] = mapped_column(JSON)
    # Bumped by every review write, so a review screen open in an older tab can tell its
    # copy is stale. The server default keeps rows inserted by an older release valid.
    result_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # How long the job is kept (`web.retention`): "day" (the default hours), "week" (7
    # days, only its owner sees it) or "dev" (30 days, the developer may look; always
    # with `opt_in_retention`). The clock restarts on activity for "day" and "week".
    retention_mode: Mapped[str] = mapped_column(String(8), default="day", server_default="day")
    # The title the instructor chose for students (`web.titles`), used instead of the one
    # read from the PDF. Document-derived, so deleted with the job.
    title_override: Mapped[str | None] = mapped_column(String(300))
    # A contact line (an email address or web address) the instructor chose for the
    # student file's footer (`web.titles.clean_contact`). Document-derived.
    contact_line: Mapped[str | None] = mapped_column(String(320))
    # The upload form's "Exam or quiz" box: the figure step describes what is shown and
    # never the result (`ConversionOptions.exam_mode`, read by `web.notation.conversion_options`).
    exam_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # One part of a larger file converted in parts (T3-3, `web.routes.parts`): the id of the
    # waiting file it was cut from (the same for every part, not a foreign key: the waiting
    # file is deleted when its last page has been sent) and the pages it holds ("31-59").
    part_group: Mapped[str | None] = mapped_column(String(32), index=True)
    part_label: Mapped[str | None] = mapped_column(String(64))
    # The sample document (T3-9, `web.sample`): a recorded conversion put in a person's list so
    # they can practise on the review screen before uploading a real file. Outside the quota,
    # the spend and the caps on documents, and outside the usage numbers.
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # What the documents list needs from `result_json`, kept beside it so the list (polled
    # every few seconds while anything converts) never loads a result's fixes: the decisions
    # still open (a heading group is one) and the title. Both are set whenever `result_json`
    # is assigned (`_keep_list_facts`); NULL on a row from before they existed, which the
    # list fills in on first sight (`web.documents`). Document-derived: deleted with the job.
    open_decisions: Mapped[int | None] = mapped_column(Integer)
    result_title: Mapped[str | None] = mapped_column(Text)
    # A review write changes `result_json` at once and the HTML file only when somebody
    # asks for it (download, view, the job page), so a run of review clicks costs one
    # render, not one each (`web.routes.jobs.current_html`). True: `html` and the checks in
    # `result_json` are from before the last review write.
    html_stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # A revised upload (T3-2): the id of the earlier version whose decisions were to be
    # carried over (`web.carry`), not a foreign key (the earlier version may be deleted
    # first), and what happened to them: element ids and counts, never text
    # (`carry.CarryReport.to_json`, with "from_name", the earlier file's name, shown to the
    # owner only). Both document-derived: deleted with the job.
    revises_job_id: Mapped[str | None] = mapped_column(String(32))
    carried_json: Mapped[dict | None] = mapped_column(JSON)
    # "Try again" (T3-1, `web.retry`): how many times the person asked for a stopped or
    # partly converted job to be converted again, and the value of `attempts` when the last
    # one began (a job interrupted `MAX_ATTEMPTS` times since then is failed, as before).
    retries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempts_at_retry: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    user: Mapped[User] = relationship(back_populates="jobs")
    blobs: Mapped[list[Blob]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    edits: Mapped[list[ReviewEdit]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


@event.listens_for(Job.result_json, "set")
def _keep_list_facts(target: Job, value: object, _old: object, _initiator: object) -> None:
    """Whenever a result is stored, store the two facts the list reads next to it."""
    from a11y_stem.web import documents

    target.open_decisions, target.result_title = documents.list_facts(value)


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


class StepRecord(Base):
    """A finished model step of a job, kept so "Try again" does not pay for it twice (T3-1,
    `web.step_cache`). `input_key` is `pipeline.run.step_key`: what the answer depends on.
    The answer is a `Fix` as JSON. Document-derived: deleted with the job."""

    __tablename__ = "step_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    element_id: Mapped[str] = mapped_column(String(64))
    input_key: Mapped[str] = mapped_column(String(64))
    fix_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("job_id", "element_id", name="uq_step_record_slot"),)


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


class PendingUpload(Base):
    """A large file kept while the person chooses which of its pages to convert (T3-3,
    `web.pending`). Document content: deleted at `retain_until`, when its last page has been
    sent, by the person, and with the person (`ops.retention`)."""

    __tablename__ = "pending_uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    pages: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    # The choices made with the upload, applied to every part: the course, exam mode, how
    # long to keep the documents.
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL", name="fk_pending_uploads_course_id_courses")
    )
    exam_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    retention_mode: Mapped[str] = mapped_column(String(8), default="day", server_default="day")
    # The 0-based pages already sent to be converted, so the rest can be chosen later.
    done_pages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ErrorAggregate(Base):
    """Daily counts by step and category. Written before deletion; never holds text."""

    __tablename__ = "error_aggregates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    step: Mapped[str] = mapped_column(String(32))  # "equation" | "figure" | "heading" | "job"
    category: Mapped[str] = mapped_column(String(64))  # e.g. "status:needs_review"
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("day", "step", "category", name="uq_aggregate"),)


class Course(Base):
    """An instructor's course: the home of its notation memory. User content."""

    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    notation: Mapped[list[CourseNotation]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )


class CourseNotation(Base):
    """One notation entry of a course (`core.types.NotationEntry`). User content.

    `canonical` is `core.notation.canonical_symbol` of `symbol`: one entry per key.
    """

    __tablename__ = "notation_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(64))
    canonical: Mapped[str] = mapped_column(String(64))
    meaning: Mapped[str] = mapped_column(String(16))  # NotationEntry.meaning
    note: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16))  # "user" | "review"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course] = relationship(back_populates="notation")

    __table_args__ = (
        UniqueConstraint("course_id", "canonical", name="uq_notation_course_canonical"),
    )


class Event(Base):
    """A content-free usage event: who (pseudonymous user id), what (`name`, a code),
    a category `code` and a number `value`, when. `job_ref` is a job id but not a
    foreign key, so a job's events outlive the job (they say nothing about its content).
    Deleted with the user."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))  # e.g. "download", "feedback"
    job_ref: Mapped[str | None] = mapped_column(String(32))
    code: Mapped[str | None] = mapped_column(String(64))  # category, never text
    value: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class AuthIdentity(Base):
    """A sign-in identity linked to a user: one per (provider, issuer, subject).

    `provider` is "google" or "microsoft"; `issuer` and `subject` are the ID token's
    `iss` and `sub`; `tenant` the Microsoft directory id (`tid`). The allowlist is still
    `users.is_allowed`, by email.
    """

    __tablename__ = "auth_identities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    issuer: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    tenant: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("provider", "issuer", "subject", name="uq_auth_identity"),)


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
