"""The content-feature tables (courses, notation, events, sign-in identities, job progress)
and what deletion does to them (SQLite; the Postgres variants are in
test_web_postgres.py).

- `events` is content-free by construction: its columns are fixed here, so a column that
  could hold document text cannot be added without changing this test.
- `ops.retention.erase_user` deletes every row of every table that holds the user's id
  (checked against the schema itself, so a new table cannot be forgotten), and nothing of
  anyone else's.
- Deleting a job leaves its usage events; deleting a course leaves its jobs.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import DateTime, Float, Integer, String, func, select

from readablestem.core.types import ProgressEvent
from readablestem.ops.retention import erase_user
from readablestem.web import db

GOOGLE_ISSUER = "https://accounts.google.com"
EVENT_COLUMNS = {
    "id": Integer,
    "user_id": String,
    "name": String,
    "job_ref": String,
    "code": String,
    "value": Float,
    "created_at": DateTime,
}


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/content.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def seed_user(factory, email: str) -> dict[str, str]:
    """A user with a finished job (upload, review edit), a course with notation, events,
    a sign-in identity, a day's page count and a large file waiting for its pages to be
    chosen. Returns the ids."""
    now = db.utcnow()
    with db.session_scope(factory) as session:
        user = db.User(email=email, is_allowed=True)
        session.add(user)
        session.flush()
        course = db.Course(user_id=user.id, name="Physics 101")
        session.add(course)
        session.flush()
        session.add(
            db.CourseNotation(
                course_id=course.id,
                symbol="bold d",
                canonical="\U0001d41d",
                meaning="vector",
                source="user",
            )
        )
        job = db.Job(
            user_id=user.id,
            filename="notes.pdf",
            status="done",
            retain_until=now + timedelta(hours=24),
            finished_at=now,
            course_id=course.id,
            progress_json=ProgressEvent(stage="checks", done=1, total=1).model_dump(),
        )
        session.add(job)
        session.flush()
        session.add_all(
            [
                db.Blob(job_id=job.id, kind="upload", media_type="application/pdf", content=b"%"),
                db.ReviewEdit(job_id=job.id, user_id=user.id, element_id="e1", action="accept"),
                db.Event(user_id=user.id, name="download", job_ref=job.id, value=1.0),
                db.Event(user_id=user.id, name="feedback", code="worked", value=1.0),
                db.AuthIdentity(
                    user_id=user.id, provider="google", issuer=GOOGLE_ISSUER, subject=email
                ),
                db.LinkedEmail(user_id=user.id, email=f"other-{email}"),
                db.LoginLink(
                    user_id=user.id,
                    token_hash=f"{email:0<64}"[:64],
                    expires_at=now + timedelta(minutes=15),
                ),
                db.QuotaDay(user_id=user.id, day=date(2026, 10, 9), pages=3),
                db.UploadResult(
                    user_id=user.id,
                    items=[{"filename": "secret.pdf", "code": "not_pdf"}],
                    retain_until=now + timedelta(hours=1),
                ),
                db.PendingUpload(
                    user_id=user.id,
                    filename="chapter.pdf",
                    pages=40,
                    size_bytes=1,
                    content=b"%",
                    done_pages=[],
                    retain_until=now + timedelta(hours=24),
                ),
            ]
        )
        return {"user": user.id, "course": course.id, "job": job.id}


def rows(factory, model, **where) -> int:
    with db.session_scope(factory) as session:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return session.scalar(query) or 0


# --------------------------------------------------------------------------- the tables


def test_events_hold_no_text_by_construction():
    table = db.Base.metadata.tables["events"]
    assert {c.name: type(c.type) for c in table.columns} == EVENT_COLUMNS
    for column in table.columns:
        if isinstance(column.type, String):
            assert column.type.length is not None and column.type.length <= 64, column.name
    assert not table.c.job_ref.foreign_keys  # a job's events outlive the job
    assert table.c.created_at.index


# --------------------------------------------------------------------------- erasure


def user_tables() -> list[tuple[str, str]]:
    """(table, column) for every column that points at users.id."""
    return [
        (table.name, column.name)
        for table in db.Base.metadata.sorted_tables
        for column in table.columns
        if any(fk.column.table.name == "users" for fk in column.foreign_keys)
    ]


def test_every_table_that_holds_a_user_id_is_known():
    """A new table with a user id must be added to `erase_user` (and to this list)."""
    assert sorted(user_tables()) == [
        ("auth_identities", "user_id"),
        ("courses", "user_id"),
        ("events", "user_id"),
        ("jobs", "user_id"),
        ("linked_emails", "user_id"),
        ("login_links", "user_id"),
        ("pending_uploads", "user_id"),
        ("quota_days", "user_id"),
        ("review_edits", "user_id"),
        ("upload_results", "user_id"),
    ]


def test_erasing_a_user_deletes_everything_of_theirs_and_nothing_else(factory):
    gone = seed_user(factory, "gone@example.org")
    kept = seed_user(factory, "kept@example.org")
    with db.session_scope(factory) as session:
        stats = erase_user(session, gone["user"])
    assert (stats.jobs_deleted, stats.blobs_deleted) == (1, 1)
    engine = factory.kw["bind"]
    with engine.connect() as conn:
        for table, column in user_tables():
            t = db.Base.metadata.tables[table]
            count = select(func.count()).select_from(t)
            assert conn.execute(count.where(t.c[column] == gone["user"])).scalar() == 0, table
            assert conn.execute(count.where(t.c[column] == kept["user"])).scalar() >= 1, table
    assert rows(factory, db.User, id=gone["user"]) == 0
    assert rows(factory, db.CourseNotation, course_id=gone["course"]) == 0
    assert rows(factory, db.CourseNotation, course_id=kept["course"]) == 1
    assert rows(factory, db.Blob) == 1 and rows(factory, db.ReviewEdit) == 1
    # Content-free counts written before the deletion survive it.
    assert rows(factory, db.ErrorAggregate) > 0
