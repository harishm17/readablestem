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

# (revision, the one before it, columns it adds to jobs, tables it adds)
CHAIN = [
    (REVISED, STALE, REVISED_COLUMNS, set()),
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
