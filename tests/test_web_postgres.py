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

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from a11y_stem.core.budget import BudgetExceeded
from a11y_stem.web import db
from a11y_stem.web.quota import pages_used, try_consume_pages
from a11y_stem.web.spend import DbSpendGuard
from a11y_stem.web.worker import Worker
from fixtures.web.support import (
    FakeConverter,
    fake_client_factory,
    make_pdf,
    make_settings,
    output_with_nul,
)

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
    from a11y_stem.web.worker import job_caps

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

    from a11y_stem.web.database import alembic_config, require_migrated

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
