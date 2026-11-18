"""Migrations of the jobs work (T3-1, T3-2, T3-3, T3-4, T3-9, PF5) and of the request tokens
(SW43): each follows the one before it, keeps existing jobs, lets an older release insert
rows without the new columns (a traffic rollback), downgrades and upgrades again, and
matches web/db.py.

(The Postgres variant is in test_web_postgres.py.)
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from readablestem.web import db
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
HTML_VERSION = "7b1d3e9a4c28"
HTML_VERSION_COLUMNS = {"html_version"}
OUTCOMES = "c2f4a8d61e57"
REQUEST_TOKENS = "8b3d5f0e2a71"  # SW43: one row per submitted form
STEP_FACTS = "8b1d5f3a9e04"
STATE_FACTS = "e4b8c2d6a193"
OUTPUT_VERSION = "a9c3e5b7d1f2"  # WS20.1: a count and a time of the changes to an output

# (revision, the one before it, columns it adds to jobs, tables it adds)
CHAIN = [
    (REVISED, STALE, REVISED_COLUMNS, set()),
    (RETRY, REVISED, RETRY_COLUMNS, {"step_records"}),
    (PARTS, RETRY, PARTS_COLUMNS, {"pending_uploads"}),
    (SAMPLE, PARTS, SAMPLE_COLUMNS, set()),
    (HTML_VERSION, OUTCOMES, HTML_VERSION_COLUMNS, set()),
    (REQUEST_TOKENS, HTML_VERSION, set(), {"request_tokens"}),
    (STEP_FACTS, REQUEST_TOKENS, set(), {"job_calls", "job_stages", "job_not_finished"}),
    (OUTPUT_VERSION, STATE_FACTS, {"output_version", "output_changed_at"}, set()),
]


def columns(engine, table: str = "jobs") -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


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


def test_jobs_from_before_the_output_version_get_version_1_and_the_time_of_their_last_change(
    tmp_path,
):
    """WS20.1: the time is the last decision, else the finish, else the upload."""
    url = f"sqlite:///{tmp_path}/output-version.db"
    migrate(url, STATE_FACTS)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_USER))
            for job in ("decided", "finished", "uploaded"):
                conn.execute(text(INSERT_JOB), {"id": job})
            conn.execute(
                text("UPDATE jobs SET finished_at = '2026-10-09 01:00:00' WHERE id <> 'uploaded'")
            )
            conn.execute(
                text(
                    "INSERT INTO review_edits "
                    "(id, job_id, user_id, element_id, action, created_at) "
                    "VALUES ('r1', 'decided', 'u1', 'e1', 'accept', '2026-10-09 05:00:00'), "
                    "('r2', 'decided', 'u1', 'e2', 'accept', '2026-10-09 03:00:00')"
                )
            )
    finally:
        engine.dispose()
    migrate(url, OUTPUT_VERSION)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, output_version, output_changed_at FROM jobs ORDER BY id")
            ).all()
        found = {row[0]: (row[1], str(row[2])[:19]) for row in rows}
        assert found == {
            "decided": (1, "2026-10-09 05:00:00"),
            "finished": (1, "2026-10-09 01:00:00"),
            "uploaded": (1, "2026-10-09 00:00:00"),
        }
    finally:
        engine.dispose()
