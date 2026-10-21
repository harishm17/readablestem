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


def test_skip_locked_lets_a_second_worker_pass_a_locked_job(factory, tmp_path):
    first, second = queue_jobs(factory, 2)
    worker = make_worker(factory, tmp_path)
    holder = factory()
    try:
        # Another worker is in the middle of claiming the oldest job (row locked).
        locked = holder.scalar(
            select(db.Job.id).where(db.Job.id == first).with_for_update(skip_locked=True)
        )
        assert locked == first
        claim = worker.claim()  # must not block on, or take, the locked row
        assert claim is not None and claim.job_id == second
    finally:
        holder.rollback()
        holder.close()
    claim = worker.claim()
    assert claim is not None and claim.job_id == first


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


def test_jobs_of_removed_users_are_skipped_by_the_skip_locked_claim(factory, tmp_path):
    first, second = queue_jobs(factory, 2)
    with db.session_scope(factory) as session:
        job = session.get(db.Job, first)
        assert job is not None
        other = db.User(email="removed@example.org", is_allowed=False)
        session.add(other)
        session.flush()
        job.user_id = other.id
    claim = make_worker(factory, tmp_path).claim()
    assert claim is not None and claim.job_id == second


def test_queue_position_follows_the_claim_order_on_postgres(factory, tmp_path):
    """views.queue_ahead compares (attempts, created_at, id) as a row value, the order
    Worker.claim takes jobs in; checked here against the real claims."""
    from readablestem.web.views import queue_ahead

    retried, fresh, last = queue_jobs(factory, 3)
    with db.session_scope(factory) as session:
        job = session.get(db.Job, retried)
        assert job is not None
        job.attempts = 1  # requeued after a lost lease: goes behind fresh jobs
    with db.session_scope(factory) as session:
        ahead = {j: queue_ahead(session, session.get(db.Job, j)) for j in (retried, fresh, last)}  # type: ignore[arg-type]
    assert ahead == {fresh: 0, last: 1, retried: 2}
    worker = make_worker(factory, tmp_path)
    claimed = [worker.claim() for _ in range(3)]
    assert [c.job_id for c in claimed if c] == [fresh, last, retried]


def test_worker_stores_results_with_postgres_types(factory, tmp_path):
    [job_id] = queue_jobs(factory, 1)
    worker = make_worker(factory, tmp_path)
    assert worker.run_once() == job_id
    with db.session_scope(factory) as session:
        job = session.get(db.Job, job_id)
        assert job is not None and job.status == "done"
        assert job.result_json is not None and job.result_json["fixes"][0]["element_id"] == "p1-e0"
        assert job.lease_until is None and job.finished_at is not None
        crops = session.scalars(select(db.Blob).where(db.Blob.kind == "crop")).all()
        assert {c.element_id for c in crops} == {"p1-e3", "p1-e4", "p1-e5"}
        session.delete(job)
    with db.session_scope(factory) as session:
        assert session.scalar(select(db.Blob).where(db.Blob.job_id == job_id)) is None


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


def test_the_content_migration_round_trips_on_postgres(pg_url):
    """Down to the revision before it (tables and job columns gone, jobs kept), up again
    (schema matches the models), and an older release's job insert still works."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    from readablestem.web.database import alembic_config
    from test_web_migrations import (
        BEFORE_CONTENT,
        CONTENT_TABLES,
        INSERT_JOB,
        INSERT_USER,
        NEW_JOB_COLUMNS,
    )

    config = alembic_config(pg_url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    command.downgrade(config, BEFORE_CONTENT)
    engine = create_engine(pg_url)
    try:
        assert not set(inspect(engine).get_table_names()) & CONTENT_TABLES
        assert not {c["name"] for c in inspect(engine).get_columns("jobs")} & NEW_JOB_COLUMNS
        with engine.begin() as conn:
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
            found = conn.execute(text("SELECT id, result_version FROM jobs ORDER BY id")).all()
        assert [tuple(row) for row in found] == [("j1", 0), ("j2", 0)]
    finally:
        engine.dispose()


def test_the_list_facts_migration_round_trips_on_postgres(pg_url):
    """Down to the exam revision (the columns gone, jobs kept), up again (schema matches
    the models), and an older release's job insert still gets a valid row."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    from readablestem.web.database import alembic_config
    from test_web_migrations import EXAM_REVISION, FACTS_COLUMNS, INSERT_JOB, INSERT_USER

    config = alembic_config(pg_url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    command.downgrade(config, EXAM_REVISION)
    engine = create_engine(pg_url)
    try:
        assert not {c["name"] for c in inspect(engine).get_columns("jobs")} & FACTS_COLUMNS
        with engine.begin() as conn:
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
            found = conn.execute(
                text("SELECT id, open_decisions, result_title FROM jobs ORDER BY id")
            ).all()
        assert [tuple(row) for row in found] == [("j1", None, None), ("j2", None, None)]
    finally:
        engine.dispose()


def test_the_stale_html_migration_round_trips_on_postgres(pg_url):
    """Down one revision (the flag gone; a file marked stale emptied so the older release
    does not serve it), up again (a job without a file is marked to be made again)."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    from readablestem.web.database import alembic_config
    from test_web_migrations import (
        FACTS_REVISION,
        INSERT_DONE_JOB,
        INSERT_JOB,
        INSERT_USER,
    )

    config = alembic_config(pg_url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    engine = create_engine(pg_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_USER))
            conn.execute(text(INSERT_DONE_JOB), {"id": "j1", "html": "<p>a</p>", "result": "{}"})
            conn.execute(text(INSERT_DONE_JOB), {"id": "j2", "html": "<p>b</p>", "result": "{}"})
            conn.execute(text("UPDATE jobs SET html_stale = true WHERE id = 'j2'"))
    finally:
        engine.dispose()
    command.downgrade(config, FACTS_REVISION)
    engine = create_engine(pg_url)
    try:
        assert "html_stale" not in {c["name"] for c in inspect(engine).get_columns("jobs")}
        with engine.connect() as conn:
            found = conn.execute(text("SELECT id, html FROM jobs ORDER BY id")).all()
        assert [tuple(row) for row in found] == [("j1", "<p>a</p>"), ("j2", None)]
        with engine.begin() as conn:
            conn.execute(text(INSERT_JOB), {"id": "j3"})  # an older release's insert
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(pg_url)
    try:
        with engine.begin() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
            found = conn.execute(text("SELECT id, html_stale FROM jobs ORDER BY id")).all()
        assert [tuple(row) for row in found] == [("j1", False), ("j2", True), ("j3", False)]
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
    """The review routes lock the job row (`owned_job(..., lock=True)`), then check the
    form's version (`web.review.is_stale`) and count the write (`bump_result_version`) in
    one transaction: of 20 tabs made from version 0 and writing at once, exactly one write
    is applied."""
    from readablestem.web.deps import CurrentUser
    from readablestem.web.review import ReviewStale, bump_result_version, is_stale
    from readablestem.web.routes.jobs import owned_job

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
                job = owned_job(session, job_id, user, lock=True)
                if is_stale(job.result_version, "0"):
                    raise ReviewStale()
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


def test_the_step_cache_round_trips_on_postgres(factory):
    """T3-1: a finished step is kept as JSON (NUL characters dropped, which Postgres
    refuses), replaced for the same element, given back under its key only, and removed
    with the job."""
    from readablestem.core.types import Element, EquationFix, Fix, Kind, Status
    from readablestem.ops.retention import delete_jobs
    from readablestem.web.step_cache import DbStepCache

    [job_id] = queue_jobs(factory, 1)
    element = Element(id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=1)
    fix = Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        equation=EquationFix(latex="x\x00y", mathml="<math/>", speech="x"),
    )
    DbStepCache(factory, job_id).store(element, "k" * 64, fix)
    DbStepCache(factory, job_id).store(element, "j" * 64, fix)  # replaces the first
    again = DbStepCache(factory, job_id)
    assert again.lookup(element, "k" * 64) is None
    kept = again.lookup(element, "j" * 64)
    assert kept is not None and kept.equation is not None and kept.equation.latex == "xy"
    with db.session_scope(factory) as session:
        assert session.scalar(select(func.count()).select_from(db.StepRecord)) == 1
        delete_jobs(session, [job_id])
        assert session.scalar(select(func.count()).select_from(db.StepRecord)) == 0


# --------------------------------------------------------------------------- open sign-up


def test_the_open_pool_and_the_day_are_never_passed_with_20_threads(factory):
    """Open and invited holds racing: at most the pool for the open ones, at most the day for
    everyone, one conditional UPDATE each, and settling leaves the columns exact."""
    from readablestem.web.spend import PoolExhausted

    guard = DbSpendGuard(factory, 1.0, open_cap_usd=0.4)
    granted: dict[str, list] = {"open": [], "invited": []}
    lock = threading.Lock()

    def reserve_many(kind: str) -> None:
        for _ in range(10):
            try:
                reservation = guard.reserve(0.01, pool="open" if kind == "open" else None)
            except PoolExhausted:
                continue
            except BudgetExceeded:
                continue
            with lock:
                granted[kind].append(reservation)

    kinds = ["open"] * 12 + ["invited"] * 8
    barrier = threading.Barrier(len(kinds))

    def wrapped(kind: str) -> None:
        barrier.wait()
        reserve_many(kind)

    threads = [threading.Thread(target=wrapped, args=(k,)) for k in kinds]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    opened, invited = len(granted["open"]), len(granted["invited"])
    assert opened <= 40 and opened + invited <= 100
    assert opened == 40 or opened + invited == 100  # a limit is what stopped them
    snapshot = guard.snapshot()
    assert snapshot.open_held_usd == pytest.approx(0.01 * opened)
    assert snapshot.held_usd == pytest.approx(0.01 * (opened + invited))
    for reservation in granted["open"] + granted["invited"]:
        guard.settle(reservation, 0.005)
    snapshot = guard.snapshot()
    assert snapshot.held_usd == pytest.approx(0.0, abs=1e-9)
    assert snapshot.open_held_usd == pytest.approx(0.0, abs=1e-9)
    assert snapshot.open_spent_usd == pytest.approx(0.005 * opened)
    assert snapshot.spent_usd == pytest.approx(0.005 * (opened + invited))


def test_the_first_open_holds_of_a_day_racing_for_its_row_are_all_granted(factory):
    guard = DbSpendGuard(factory, 100.0, open_cap_usd=100.0)
    granted = []
    lock = threading.Lock()

    def reserve() -> None:
        reservation = guard.reserve(0.01, pool="open")
        with lock:
            granted.append(reservation)

    run_threads(20, reserve)
    assert len(granted) == 20
    assert guard.snapshot().open_held_usd == pytest.approx(0.2)


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


def test_the_open_signup_migration_round_trips_on_postgres(pg_url):
    """Existing accounts become `invited`, the numbers of the day and the quota are kept,
    down drops the columns and the table, up again matches the models, and an older
    release's inserts still work."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    from readablestem.web.database import alembic_config

    config = alembic_config(pg_url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "d3f8a1c5b920")
    engine = create_engine(pg_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, is_allowed, is_admin, created_at) VALUES "
                    "('u1', 'a@example.org', true, false, '2026-10-08 00:00:00'), "
                    "('u2', 'gone@example.org', false, false, '2026-10-08 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO spend_days (day, spent_usd, held_usd) "
                    "VALUES ('2026-10-20', 1.5, 0.25)"
                )
            )
            conn.execute(
                text("INSERT INTO quota_days (user_id, day, pages) VALUES ('u1', '2026-10-20', 7)")
            )
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(pg_url)
    try:
        with engine.begin() as conn:
            tiers = conn.execute(text("SELECT access_tier FROM users")).scalars().all()
            assert tiers == ["invited", "invited"]
            spend = conn.execute(
                text("SELECT spent_usd, held_usd, open_spent_usd, open_held_usd FROM spend_days")
            ).one()
            assert tuple(spend) == (1.5, 0.25, 0.0, 0.0)
            assert tuple(conn.execute(text("SELECT pages, rereads FROM quota_days")).one()) == (
                7,
                0,
            )
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
            # An older release inserts without the new columns.
            conn.execute(
                text(
                    "INSERT INTO users (id, email, is_allowed, is_admin, created_at) "
                    "VALUES ('u3', 'old@example.org', true, false, '2026-10-21 00:00:00')"
                )
            )
            assert (
                conn.execute(text("SELECT access_tier FROM users WHERE id = 'u3'")).scalar_one()
                == "invited"
            )
    finally:
        engine.dispose()
    command.downgrade(config, "d3f8a1c5b920")
    engine = create_engine(pg_url)
    try:
        inspector = inspect(engine)
        assert "access_tier" not in {c["name"] for c in inspector.get_columns("users")}
        assert "signup_days" not in inspector.get_table_names()
        assert "rereads" not in {c["name"] for c in inspector.get_columns("quota_days")}
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(pg_url)
    try:
        with engine.begin() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
    finally:
        engine.dispose()
