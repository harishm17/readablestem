"""Postgres-only behaviour: no spend/quota overshoot under concurrency, SKIP LOCKED
claiming with two workers, real column types, and the migration on Postgres.

Uses TEST_POSTGRES_URL when set (CI service container), else an embedded server from the
`pgserver` dev dependency (Python 3.12). Skips when neither is available.
"""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from fixtures.web.support import (
    FakeConverter,
    fake_client_factory,
    make_pdf,
    make_settings,
    output_with_nul,
)
from readablestem.core.budget import BudgetExceeded
from readablestem.web import db
from readablestem.web.quota import pages_used, try_consume_pages
from readablestem.web.spend import DbSpendGuard
from readablestem.web.worker import Worker

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    url = os.environ.get("TEST_POSTGRES_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver", reason="pgserver unavailable; set TEST_POSTGRES_URL")
    try:
        server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="delete")
    except Exception as exc:  # pragma: no cover - environment specific
        pytest.skip(f"embedded Postgres did not start: {type(exc).__name__}")
    uri = make_url(server.get_uri()).set(drivername="postgresql+psycopg")
    yield uri.render_as_string(hide_password=False)
    server.cleanup()


@pytest.fixture
def pg_url(server_url):
    """A fresh, empty database per test."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def factory(pg_url):
    engine = db.make_engine(pg_url)
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def add_user(factory) -> str:
    with db.session_scope(factory) as session:
        user = db.User(email=f"{uuid.uuid4().hex[:6]}@example.org", is_allowed=True)
        session.add(user)
        session.flush()
        return user.id


def queue_jobs(factory, n: int) -> list[str]:
    user_id = add_user(factory)
    ids = []
    now = db.utcnow()
    with db.session_scope(factory) as session:
        for i in range(n):
            job = db.Job(
                user_id=user_id,
                filename="f.pdf",
                page_count=1,
                retain_until=now + timedelta(hours=1),
                created_at=now + timedelta(milliseconds=i),
            )
            session.add(job)
            session.flush()
            session.add(
                db.Blob(
                    job_id=job.id, kind="upload", media_type="application/pdf", content=make_pdf(1)
                )
            )
            ids.append(job.id)
    return ids


def run_threads(n: int, target) -> None:
    barrier = threading.Barrier(n)

    def wrapped() -> None:
        barrier.wait()
        target()

    threads = [threading.Thread(target=wrapped) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_spend_guard_does_not_overshoot_with_20_threads(factory):
    guard = DbSpendGuard(factory, 1.0)
    granted = []
    lock = threading.Lock()

    def reserve_many() -> None:
        for _ in range(10):
            with contextlib.suppress(BudgetExceeded):
                reservation = guard.reserve(0.01)
                with lock:
                    granted.append(reservation)

    run_threads(20, reserve_many)  # 200 attempts against room for 100
    assert len(granted) == 100
    snapshot = guard.snapshot()
    assert snapshot.spent_usd + snapshot.held_usd == pytest.approx(1.0)

    # Settling races too: every hold is released and the actual costs add up exactly.
    chunks = iter([granted[i::20] for i in range(20)])

    def settle_chunk() -> None:
        with lock:
            chunk = next(chunks)
        for reservation in chunk:
            guard.settle(reservation, 0.005)

    run_threads(20, settle_chunk)
    snapshot = guard.snapshot()
    assert snapshot.held_usd == pytest.approx(0.0, abs=1e-9)
    assert snapshot.spent_usd == pytest.approx(0.5)


def test_quota_does_not_overshoot_with_20_threads(factory):
    user_id = add_user(factory)
    day = db.utcnow().date()
    ok = []
    lock = threading.Lock()

    def consume() -> None:
        with db.session_scope(factory) as session:
            if try_consume_pages(session, user_id, 3, quota=30, day=day):
                with lock:
                    ok.append(1)

    run_threads(20, consume)
    assert len(ok) == 10
    with db.session_scope(factory) as session:
        assert pages_used(session, user_id, day) == 30


def make_worker(factory, tmp_path) -> Worker:
    settings = make_settings(tmp_path, database_url="postgresql+psycopg://unused")
    return Worker(settings, factory, converter=FakeConverter(), client_factory=fake_client_factory)


def test_two_workers_claim_every_job_exactly_once(factory, tmp_path):
    ids = queue_jobs(factory, 30)
    workers = [make_worker(factory, tmp_path), make_worker(factory, tmp_path)]
    claimed: list[str] = []
    lock = threading.Lock()
    turn = iter(workers)

    def drain() -> None:
        with lock:
            worker = next(turn)
        while (claim := worker.claim()) is not None:
            with lock:
                claimed.append(claim.job_id)

    run_threads(2, drain)
    assert sorted(claimed) == sorted(ids)


def test_job_cap_on_the_job_row_does_not_overshoot_with_20_threads(factory, tmp_path):
    """Every reservation adds its hold to the job's row in one conditional UPDATE, so 20
    step threads (or a dead attempt plus a retry) cannot pass the job's cap together."""
    [job_id] = queue_jobs(factory, 1)
    worker = make_worker(factory, tmp_path)
    worker.guard = DbSpendGuard(factory, 100.0)
    claim = worker.claim()
    assert claim is not None
    attempt = worker._start_attempt(claim, 0.0, 0, 1)
    max_usd, _ = worker_caps(worker)
    granted = []
    lock = threading.Lock()

    def reserve_many() -> None:
        for _ in range(10):
            with contextlib.suppress(BudgetExceeded):
                reservation = attempt.budget.reserve(0.01)
                with lock:
                    granted.append(reservation)

    run_threads(20, reserve_many)
    assert len(granted) == round(max_usd / 0.01)
    with db.session_scope(factory) as session:
        job = session.get(db.Job, job_id)
        assert job is not None
        assert job.cost_usd == pytest.approx(max_usd)
        assert job.model_calls == len(granted)


def worker_caps(worker: Worker) -> tuple[float, int]:
    from readablestem.web.worker import job_caps

    return job_caps(worker.settings, 1)


def test_worker_stores_a_result_whose_text_contains_nul(factory, tmp_path):
    """Postgres refuses U+0000 in text; the worker strips it instead of failing the store
    (which used to leave the job running and convert it again on every lease expiry)."""
    [job_id] = queue_jobs(factory, 1)
    worker = make_worker(factory, tmp_path)
    converter = FakeConverter(output_with_nul())
    worker.converter = converter
    assert worker.run_once() == job_id
    with db.session_scope(factory) as session:
        job = session.get(db.Job, job_id)
        assert job is not None and job.status == "done", job.error_code if job else None
        assert job.html is not None and "SECRET-DOC-TEXTtail" in job.html
        assert job.result_json is not None
        assert job.result_json["elements"][1]["text"] == "paragraph"
    assert len(converter.calls) == 1


def test_initial_migration_matches_the_models_on_postgres(pg_url):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from readablestem.web.database import alembic_config, require_migrated

    config = alembic_config(pg_url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
        require_migrated(engine)  # production start check passes
    finally:
        engine.dispose()


def test_erasure_and_course_deletion_on_postgres(factory):
    """Real ON DELETE behaviour: a deleted course leaves its job (course_id NULL) and
    takes its notation; erasing a user leaves nothing of theirs and keeps the other
    user's rows."""
    from readablestem.ops.retention import erase_user

    def seed(email: str) -> tuple[str, str, str]:
        with db.session_scope(factory) as session:
            user = db.User(email=email, is_allowed=True)
            session.add(user)
            session.flush()
            course = db.Course(user_id=user.id, name="Physics")
            session.add(course)
            session.flush()
            job = db.Job(
                user_id=user.id,
                filename="f.pdf",
                course_id=course.id,
                retain_until=db.utcnow() + timedelta(hours=1),
            )
            session.add_all(
                [
                    job,
                    db.CourseNotation(
                        course_id=course.id,
                        symbol="bold d",
                        canonical="d",
                        meaning="vector",
                        source="user",
                    ),
                    db.Event(user_id=user.id, name="download", value=1.0),
                    db.AuthIdentity(user_id=user.id, provider="google", issuer="i", subject=email),
                ]
            )
            session.flush()
            return user.id, course.id, job.id

    gone_user, gone_course, _ = seed("gone@example.org")
    kept_user, kept_course, kept_job = seed("kept@example.org")
    with db.session_scope(factory) as session:
        session.execute(text("DELETE FROM courses WHERE id = :id"), {"id": kept_course})
    notation = select(db.CourseNotation.id).where(
        db.CourseNotation.course_id.in_([gone_course, kept_course])
    )
    with db.session_scope(factory) as session:
        kept = session.get(db.Job, kept_job)
        assert kept is not None and kept.course_id is None
        assert len(session.scalars(notation).all()) == 1  # only the other course's entry
        erase_user(session, gone_user)
    with db.session_scope(factory) as session:
        for model in (db.Job, db.Event, db.AuthIdentity):
            owners = set(session.scalars(select(model.user_id)))
            assert gone_user not in owners and kept_user in owners, model.__tablename__
        assert session.scalar(select(db.Course.id)) is None
        assert session.scalars(notation).all() == []
        assert session.get(db.User, gone_user) is None and session.get(db.User, kept_user)


def test_only_one_of_20_tabs_writing_from_the_same_version_gets_through(factory):
    """The review routes lock the job row (`job_access(..., lock=True)`), then check the
    form's version (`web.review.is_stale`) and count the write (`bump_result_version`) in
    one transaction: of 20 tabs made from version 0 and writing at once, exactly one write
    is applied."""
    from readablestem.web.deps import CurrentUser
    from readablestem.web.review import ReviewStale, bump_result_version, is_stale
    from readablestem.web.routes.jobs import job_access

    [job_id] = queue_jobs(factory, 1)
    with db.session_scope(factory) as session:
        job = session.get(db.Job, job_id)
        assert job is not None and job.result_version == 0
        user = CurrentUser(id=job.user_id, email="x@example.org", name=None, is_admin=False)
    outcomes: list[str] = []
    lock = threading.Lock()

    def write() -> None:
        try:
            with db.session_scope(factory) as session:
                job = job_access(session, job_id, user, "review", lock=True)
                if is_stale(job.result_version, "0"):
                    raise ReviewStale(job.id)
                bump_result_version(job)
            outcome = "saved"
        except ReviewStale:
            outcome = "stale"
        with lock:
            outcomes.append(outcome)

    run_threads(20, write)
    assert sorted(outcomes) == ["saved"] + ["stale"] * 19
    with db.session_scope(factory) as session:
        job = session.get(db.Job, job_id)
        assert job is not None and job.result_version == 1


def test_the_jobs_migrations_round_trip_on_postgres(pg_url):
    """Each migration of the jobs work (T3-1 ...): down one step (its columns and tables
    gone, a job kept), up again (the schema matches the models), and an older release's job
    insert still gets a valid row."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    from readablestem.web.database import alembic_config
    from test_web_jobs_migrations import CHAIN
    from test_web_migrations import INSERT_JOB, INSERT_USER

    config = alembic_config(pg_url)
    config.attributes["configure_logger"] = False
    for revision, before, added, tables in reversed(CHAIN):
        command.upgrade(config, "head")
        command.downgrade(config, before)
        engine = create_engine(pg_url)
        try:
            names = set(inspect(engine).get_table_names())
            assert not tables & names, revision
            assert not {c["name"] for c in inspect(engine).get_columns("jobs")} & added, revision
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM jobs"))
                conn.execute(text("DELETE FROM users"))
                conn.execute(text(INSERT_USER))
                conn.execute(text(INSERT_JOB), {"id": "j1"})
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(pg_url)
        try:
            with engine.begin() as conn:
                assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
                conn.execute(text(INSERT_JOB), {"id": "j2"})
                found = conn.execute(text("SELECT id FROM jobs ORDER BY id")).all()
            assert [row[0] for row in found] == ["j1", "j2"]
            assert tables <= set(inspect(engine).get_table_names())
        finally:
            engine.dispose()


# --------------------------------------------------------------------------- open sign-up


def test_new_accounts_never_pass_the_daily_cap_with_20_threads(factory):
    from readablestem.web.auth import sign_in_by_email

    settings = make_settings(Path("."), signup_mode="open", max_new_accounts_per_day=5)
    results = []
    lock = threading.Lock()
    counter = iter(range(1000))

    def sign_up() -> None:
        with lock:
            n = next(counter)
        outcome = sign_in_by_email(factory, settings, f"user{n}@example.org", None)
        with lock:
            results.append(outcome)

    run_threads(20, sign_up)
    assert sum(1 for r in results if r.created) == 5
    assert sum(1 for r in results if r.refused == "signups_full") == 15
    with db.session_scope(factory) as session:
        assert session.scalar(select(func.count()).select_from(db.User)) == 5
        assert session.scalar(select(db.SignupDay.accounts)) == 5


def test_one_address_signing_up_20_times_at_once_makes_one_account(factory):
    from readablestem.web.auth import sign_in_by_email

    settings = make_settings(Path("."), signup_mode="open")
    results = []
    lock = threading.Lock()

    def sign_up() -> None:
        outcome = sign_in_by_email(factory, settings, "same@example.org", None)
        with lock:
            results.append(outcome)

    run_threads(20, sign_up)
    assert len(results) == 20 and len({r.user_id for r in results}) == 1
    assert sum(1 for r in results if r.created) == 1
    with db.session_scope(factory) as session:
        assert session.scalar(select(func.count()).select_from(db.User)) == 1
        assert session.scalar(select(db.SignupDay.accounts)) == 1


def test_the_reread_allowance_does_not_overshoot_with_20_threads(factory):
    from readablestem.web.quota import rereads_used, try_consume_reread

    user_id = add_user(factory)
    day = db.utcnow().date()
    ok = []
    lock = threading.Lock()

    def consume() -> None:
        with db.session_scope(factory) as session:
            if try_consume_reread(session, user_id, allowance=7, day=day):
                with lock:
                    ok.append(1)

    run_threads(20, consume)
    assert len(ok) == 7
    with db.session_scope(factory) as session:
        assert rereads_used(session, user_id, day) == 7


def test_settlements_and_releases_racing_for_the_same_holds_release_each_once(factory):
    guard = DbSpendGuard(factory, 100.0, open_cap_usd=100.0)
    reservations = [guard.scoped("open", "a" * 32).reserve(0.1) for _ in range(40)]
    turn = iter(range(8))
    lock = threading.Lock()

    def settle_or_release() -> None:
        with lock:
            me = next(turn)
        if me == 0:  # one thread settles all forty ...
            for reservation in reservations:
                guard.settle(reservation, 0.05)
        for _ in range(20):  # ... while every thread keeps releasing the job's holds
            guard.release_holds(job_ids=["a" * 32])

    run_threads(8, settle_or_release)
    snapshot = guard.snapshot()
    assert snapshot.held_usd == pytest.approx(0.0, abs=1e-9)
    assert snapshot.open_held_usd == pytest.approx(0.0, abs=1e-9)
    assert snapshot.spent_usd == pytest.approx(0.05 * 40)
    with db.session_scope(factory) as session:
        assert session.scalar(select(func.count()).select_from(db.SpendHold)) == 0
