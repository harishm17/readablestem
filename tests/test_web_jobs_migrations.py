"""Migrations of the jobs work (T3-1, T3-2, T3-3, T3-4, T3-9): each follows the one before
it, keeps existing jobs, lets an older release insert rows without the new columns (a
traffic rollback), downgrades and upgrades again, and matches web/db.py.

(The Postgres variant is in test_web_postgres.py.)
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from a11y_stem.web import db
from a11y_stem.web.database import alembic_config
from test_web_migrations import INSERT_JOB, INSERT_USER, migrate, upgrade

STALE = "c4a7e9b05d18"
REVISED = "e2b6d1f84a30"
REVISED_COLUMNS = {"revises_job_id", "carried_json"}
RETRY = "f7a3c9d20b15"
RETRY_COLUMNS = {"retries", "attempts_at_retry"}
PARTS = "a8c4e0b61d97"
PARTS_COLUMNS = {"part_group", "part_label"}
SAMPLE = "b1d5f7a29c46"
SAMPLE_COLUMNS = {"is_sample"}

# (revision, the one before it, columns it adds to jobs, tables it adds)
CHAIN = [
    (REVISED, STALE, REVISED_COLUMNS, set()),
    (RETRY, REVISED, RETRY_COLUMNS, {"step_records"}),
    (PARTS, RETRY, PARTS_COLUMNS, {"pending_uploads"}),
    (SAMPLE, PARTS, SAMPLE_COLUMNS, set()),
]


def columns(engine, table: str = "jobs") -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_the_migrations_follow_each_other_in_one_line():
    script = ScriptDirectory.from_config(alembic_config())
    for revision, before, _, _ in CHAIN:
        found = script.get_revision(revision)
        assert found is not None and found.down_revision == before


@pytest.mark.parametrize(("revision", "before", "added", "tables"), CHAIN)
def test_each_migration_keeps_jobs_and_old_inserts_still_work(
    tmp_path, revision, before, added, tables
):
    url = f"sqlite:///{tmp_path}/{revision}.db"
    migrate(url, before)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_USER))
            conn.execute(text(INSERT_JOB), {"id": "j1"})
    finally:
        engine.dispose()
    migrate(url, revision)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_JOB), {"id": "j2"})  # a release from before the columns
            found = conn.execute(text("SELECT id FROM jobs ORDER BY id")).all()
        assert [row[0] for row in found] == ["j1", "j2"]
        assert columns(engine) >= added
        assert tables <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


@pytest.mark.parametrize(("revision", "before", "added", "tables"), CHAIN)
def test_each_migration_downgrades_and_upgrades_again(tmp_path, revision, before, added, tables):
    url = f"sqlite:///{tmp_path}/down-{revision}.db"
    upgrade(url)
    migrate(url, before, down=True)
    engine = create_engine(url)
    try:
        assert not columns(engine) & added
        assert not tables & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    upgrade(url)
    engine = create_engine(url)
    try:
        assert columns(engine) >= added
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
    finally:
        engine.dispose()


def test_downgrading_the_sample_migration_deletes_sample_documents_and_keeps_the_rest(tmp_path):
    """An older release would count a sample document as the person's own file (their cap,
    the beta's numbers): the downgrade deletes it with what hangs off it."""
    url = f"sqlite:///{tmp_path}/sample-down.db"
    upgrade(url)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_USER))
            conn.execute(text(INSERT_JOB), {"id": "real"})
            conn.execute(text(INSERT_JOB), {"id": "sample"})
            conn.execute(text("UPDATE jobs SET is_sample = 1 WHERE id = 'sample'"))
            for job in ("real", "sample"):
                conn.execute(
                    text(
                        "INSERT INTO blobs "
                        "(id, job_id, kind, media_type, content, created_at) "
                        "VALUES (:id, :job, 'upload', 'application/pdf', x'25', "
                        "'2026-10-09 00:00:00')"
                    ),
                    {"id": f"b-{job}", "job": job},
                )
                conn.execute(
                    text(
                        "INSERT INTO review_edits "
                        "(id, job_id, user_id, element_id, action, created_at) "
                        "VALUES (:id, :job, 'u1', 'e1', 'accept', '2026-10-09 00:00:00')"
                    ),
                    {"id": f"r-{job}", "job": job},
                )
    finally:
        engine.dispose()
    migrate(url, PARTS, down=True)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert [r[0] for r in conn.execute(text("SELECT id FROM jobs"))] == ["real"]
            assert [r[0] for r in conn.execute(text("SELECT job_id FROM blobs"))] == ["real"]
            assert [r[0] for r in conn.execute(text("SELECT job_id FROM review_edits"))] == ["real"]
        assert "is_sample" not in columns(engine)
    finally:
        engine.dispose()
