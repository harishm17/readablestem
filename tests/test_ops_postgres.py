"""ops on Postgres: ON CONFLICT upserts, SKIP LOCKED under concurrent cron runs, cascade.

Server: DATABASE_URL when it points at Postgres (CI's service container), otherwise the
embedded `pgserver` dev dependency (Python 3.12 only), otherwise skipped. Each run uses
its own throwaway database, so other packages' Postgres tests on the same server are
unaffected.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from a11y_stem.ops import aggregate, retention
from a11y_stem.ops.aggregate import aggregate_finished
from a11y_stem.ops.retention import PurgeStats, purge_expired
from a11y_stem.web import db
from fixtures.ops.seed import (
    EXPECTED_DONE_COUNTS,
    NOW,
    add_job,
    add_user,
    aggregate_rows,
    sentinel_result,
)

pytestmark = pytest.mark.postgres


@contextmanager
def _postgres_server(tmp_path_factory) -> Iterator[str]:
    configured = os.environ.get("DATABASE_URL", "")
    if configured.startswith("postgresql"):
        yield configured
        return
    try:
        import pgserver  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("no Postgres: set DATABASE_URL or install pgserver (Python 3.12)")
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="delete")
    try:
        yield server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    finally:
        server.cleanup()


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory) -> Generator[str]:
    """URL of a fresh database created for this module and dropped afterwards."""
    with _postgres_server(tmp_path_factory) as server_url:
        name = f"a11y_ops_{uuid.uuid4().hex[:12]}"
        admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            yield make_url(server_url).set(database=name).render_as_string(hide_password=False)
        finally:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            admin.dispose()


@pytest.fixture
def factory(pg_url):
    engine = db.make_engine(pg_url)
    db.Base.metadata.drop_all(engine)
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def test_upsert_accumulates_on_postgres(factory):
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, result=sentinel_result())
    assert aggregate_finished(factory, NOW) == 1
    with db.session_scope(factory) as s:
        add_job(s, s.query(db.User).one(), result=sentinel_result())
    assert aggregate_finished(factory, NOW) == 1
    assert aggregate_finished(factory, NOW) == 0
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == {k: 2 * v for k, v in EXPECTED_DONE_COUNTS.items()}


def test_concurrent_cron_runs_count_each_job_once(factory, monkeypatch):
    monkeypatch.setattr(aggregate, "BATCH_SIZE", 3)
    with db.session_scope(factory) as s:
        user = add_user(s)
        for _ in range(40):
            add_job(s, user, page_count=1)

    totals: list[int] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            totals.append(aggregate_finished(factory, NOW))
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert sum(totals) == 40
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == {("job", "status:done"): 40, ("job", "pages"): 40}


def test_purge_cascades_and_counts_reviews_on_postgres(factory, monkeypatch):
    monkeypatch.setattr(retention, "BATCH_SIZE", 2)
    expired = NOW - timedelta(minutes=1)
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(
            s,
            user,
            retain_until=expired,
            result=sentinel_result(),
            blobs=2,
            edits=[("p1-e1", "edit")],
        )
        for _ in range(3):
            add_job(s, user, retain_until=expired, aggregated=True, blobs=1)
        add_job(s, user, retain_until=NOW + timedelta(hours=1), blobs=1)

    # The never-aggregated job is counted in the delete's transaction (its edit snapshot
    # has no status, so only its checks are replaced by "aggregate:checks_edited").
    assert purge_expired(factory, NOW) == PurgeStats(
        jobs_deleted=4, blobs_deleted=5, unaggregated_deleted=0
    )
    expected = dict(EXPECTED_DONE_COUNTS)
    del expected[("equation", "check:latex.allowed_commands:unknown_command")]  # p1-e1's
    expected[("equation", "aggregate:checks_edited")] = 1
    del expected[("equation", "tier:required:unknown_command")]  # the reason was that check
    expected[("equation", "tier:required:checks_edited")] = 1
    expected[("equation", "review:edit")] = 1
    with db.session_scope(factory) as s:
        assert s.query(db.Job).count() == 1
        assert s.query(db.Blob).count() == 1
        assert s.query(db.ReviewEdit).count() == 0
        assert aggregate_rows(s) == expected


def test_delete_now_waits_for_a_cron_run_and_never_counts_twice(factory):
    """A user's "delete now" locks the job row (no SKIP LOCKED): while a cron run holds
    it, the delete waits, then sees `aggregated=True` and counts nothing again."""
    with db.session_scope(factory) as s:
        job_id = add_job(s, add_user(s), result=sentinel_result())

    cron = factory()
    try:
        assert aggregate._aggregate_batch(cron, NOW) == 1  # holds the row lock, uncommitted
        result: list[PurgeStats] = []

        def delete_now() -> None:
            with db.session_scope(factory) as s:
                result.append(retention.delete_jobs(s, [job_id], NOW))

        worker = threading.Thread(target=delete_now)
        worker.start()
        worker.join(timeout=1)
        assert worker.is_alive()  # blocked on the cron's lock
        cron.commit()
        worker.join(timeout=10)
    finally:
        cron.close()
    assert result == [PurgeStats(jobs_deleted=1, blobs_deleted=0, unaggregated_deleted=0)]
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == EXPECTED_DONE_COUNTS
