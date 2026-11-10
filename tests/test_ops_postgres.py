"""ops on Postgres: ON CONFLICT upserts, SKIP LOCKED under concurrent cron runs, cascade.

Server: DATABASE_URL when it points at Postgres (CI's service container), otherwise the
embedded `pgserver` dev dependency (Python 3.12 only), otherwise skipped. Each run uses
its own throwaway database, so other packages' Postgres tests on the same server are
unaffected.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from fixtures.ops.seed import (
    EXPECTED_DONE_COUNTS,
    NOW,
    add_job,
    add_user,
    aggregate_rows,
    sentinel_result,
)
from readablestem.ops import retention
from readablestem.ops.retention import PurgeStats, purge_expired
from readablestem.web import db

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
