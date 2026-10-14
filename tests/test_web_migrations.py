"""Alembic migrations match web/db.py; production refuses an unmigrated database.

(The Postgres variant of the schema check is in test_web_postgres.py.)
"""

from __future__ import annotations

import logging
from itertools import pairwise

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from a11y_stem.config import ConfigError
from a11y_stem.web import database, db
from a11y_stem.web.database import (
    alembic_config,
    alembic_ini_path,
    migration_heads,
    prepare_database,
    require_migrated,
)
from fixtures.web.support import make_settings


def upgrade(url: str) -> None:
    config = alembic_config(url)
    config.attributes["configure_logger"] = False  # keep pytest's logging setup intact
    command.upgrade(config, "head")


def test_alembic_ini_is_found_and_there_is_one_head():
    assert alembic_ini_path() is not None
    heads = migration_heads()
    assert heads is not None and len(heads) == 1
    script = ScriptDirectory.from_config(alembic_config())
    # One straight line from the head back to the initial schema: no branches to merge.
    revisions = list(script.walk_revisions())
    assert {revisions[0].revision} == set(heads) and revisions[-1].down_revision is None
    for newer, older in pairwise(revisions):
        assert newer.down_revision == older.revision


def test_an_older_release_can_still_add_users_after_the_session_version_migration(tmp_path):
    """Traffic rollback (docs/deploy.md): the old code inserts users without
    session_version; the server default must fill it in."""
    url = f"sqlite:///{tmp_path}/rollback.db"
    upgrade(url)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, is_allowed, is_admin, created_at) "
                    "VALUES ('u1', 'a@example.org', 1, 0, '2026-10-08 00:00:00')"
                )
            )
            version = conn.execute(text("SELECT session_version FROM users")).scalar_one()
        assert version == 0
    finally:
        engine.dispose()


def test_initial_migration_matches_the_models_on_sqlite(tmp_path):
    url = f"sqlite:///{tmp_path}/migrated.db"
    upgrade(url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
        assert set(inspect(engine).get_table_names()) == set(db.Base.metadata.tables) | {
            "alembic_version"
        }
    finally:
        engine.dispose()


def test_downgrade_removes_everything(tmp_path):
    url = f"sqlite:///{tmp_path}/down.db"
    upgrade(url)
    config = alembic_config(url)
    config.attributes["configure_logger"] = False
    command.downgrade(config, "base")
    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()


def test_production_refuses_a_database_without_migrations(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/empty.db")
    settings = make_settings(tmp_path, env="prod")
    with pytest.raises(ConfigError, match="a11y-stem db upgrade"):
        prepare_database(engine, settings)
    engine.dispose()


def test_production_accepts_a_migrated_database(tmp_path):
    url = f"sqlite:///{tmp_path}/ready.db"
    upgrade(url)
    engine = db.make_engine(url)
    require_migrated(engine)
    engine.dispose()


def test_dev_and_test_create_tables_directly(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/dev.db")
    prepare_database(engine, make_settings(tmp_path, env="dev"))
    assert "jobs" in inspect(engine).get_table_names()
    engine.dispose()


def set_revision(engine, revision: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revision})


def test_a_database_migrated_by_a_newer_release_still_starts(tmp_path, caplog):
    """Rolling back to an older revision (or that revision restarting between a ship's
    migrate and service steps) used to fail start-up with exit 3 once any newer release
    had migrated: the image did not know the database's revision."""
    url = f"sqlite:///{tmp_path}/ahead.db"
    upgrade(url)
    engine = db.make_engine(url)
    set_revision(engine, "9a9a9a9a9a9a")  # a revision this image has never heard of
    caplog.set_level(logging.WARNING)
    require_migrated(engine)
    assert "schema_ahead" in caplog.messages
    engine.dispose()


def test_a_database_behind_this_release_is_refused(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/behind.db"
    upgrade(url)
    engine = db.make_engine(url)
    [current] = migration_heads() or {""}
    monkeypatch.setattr(database, "migration_heads", lambda: {"f00dfeedbeef"})
    monkeypatch.setattr(database, "known_revisions", lambda: {current, "f00dfeedbeef"})
    with pytest.raises(ConfigError, match="behind"):
        require_migrated(engine)
    engine.dispose()


# --------------------------------------------------------------------------- content features

CONTENT_REVISION = "fa3662c01396"
BEFORE_CONTENT = "eb74844c4a9d"
CONTENT_TABLES = {"courses", "notation_entries", "events", "auth_identities"}
NEW_JOB_COLUMNS = {"course_id", "progress_json", "result_version"}
INSERT_JOB = (
    "INSERT INTO jobs (id, user_id, status, filename, page_count, pages_done, "
    "opt_in_retention, retain_until, created_at, attempts, cost_usd, model_calls, "
    "aggregated) VALUES (:id, 'u1', 'done', 'f.pdf', 1, 1, false, '2026-10-10 00:00:00', "
    "'2026-10-09 00:00:00', 1, 0.0, 0, false)"
)
INSERT_USER = (
    "INSERT INTO users (id, email, is_allowed, is_admin, created_at) "
    "VALUES ('u1', 'a@example.org', true, false, '2026-10-08 00:00:00')"
)


def migrate(url: str, revision: str, *, down: bool = False) -> None:
    config = alembic_config(url)
    config.attributes["configure_logger"] = False
    (command.downgrade if down else command.upgrade)(config, revision)


def test_the_content_migration_follows_the_session_version_one():
    script = ScriptDirectory.from_config(alembic_config())
    revision = script.get_revision(CONTENT_REVISION)
    assert revision is not None and revision.down_revision == BEFORE_CONTENT


def test_the_content_migration_downgrades_and_upgrades_again(tmp_path):
    url = f"sqlite:///{tmp_path}/content.db"
    upgrade(url)
    migrate(url, BEFORE_CONTENT, down=True)  # through the later revisions, too
    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
        assert not names & CONTENT_TABLES and "jobs" in names
        assert not {c["name"] for c in inspect(engine).get_columns("jobs")} & NEW_JOB_COLUMNS
        referred = [fk["referred_table"] for fk in inspect(engine).get_foreign_keys("jobs")]
        assert referred == ["users"]
    finally:
        engine.dispose()
    upgrade(url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
    finally:
        engine.dispose()


def test_jobs_survive_the_content_migration_and_old_inserts_still_work(tmp_path):
    """Upgrading keeps existing jobs (result_version 0, no course); after it, an older
    release inserting a job without the new columns (a traffic rollback) still gets a
    valid row."""
    url = f"sqlite:///{tmp_path}/rollback-content.db"
    migrate(url, BEFORE_CONTENT)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_USER))
            conn.execute(text(INSERT_JOB), {"id": "j1"})
    finally:
        engine.dispose()
    upgrade(url)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_JOB), {"id": "j2"})
            found = conn.execute(
                text("SELECT id, result_version, course_id, progress_json FROM jobs ORDER BY id")
            ).all()
        assert [tuple(row) for row in found] == [("j1", 0, None, None), ("j2", 0, None, None)]
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- job page choices

TIER1_REVISION = "7c1d9a4e2b60"
TIER1_JOB_COLUMNS = {"retention_mode", "title_override", "contact_line"}


def test_the_job_page_migration_follows_the_content_one_and_is_the_head():
    script = ScriptDirectory.from_config(alembic_config())
    revision = script.get_revision(TIER1_REVISION)
    assert revision is not None and revision.down_revision == CONTENT_REVISION
    assert migration_heads() == {TIER1_REVISION}


def test_the_job_page_migration_keeps_jobs_and_maps_the_old_opt_in(tmp_path):
    """Jobs made before it: the 30-day opt-in becomes mode "dev", the rest "day"; an older
    release inserting a job without the new columns still gets a valid row."""
    url = f"sqlite:///{tmp_path}/tier1.db"
    migrate(url, CONTENT_REVISION)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_USER))
            conn.execute(text(INSERT_JOB), {"id": "j1"})
            conn.execute(text(INSERT_JOB.replace("1, 1, false", "1, 1, true")), {"id": "j2"})
    finally:
        engine.dispose()
    upgrade(url)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_JOB), {"id": "j3"})
            found = conn.execute(
                text(
                    "SELECT id, retention_mode, title_override, contact_line FROM jobs "
                    "ORDER BY id"
                )
            ).all()
        assert [tuple(row) for row in found] == [
            ("j1", "day", None, None),
            ("j2", "dev", None, None),
            ("j3", "day", None, None),
        ]
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
    finally:
        engine.dispose()


def test_the_job_page_migration_downgrades_and_upgrades_again(tmp_path):
    url = f"sqlite:///{tmp_path}/tier1-down.db"
    upgrade(url)
    migrate(url, CONTENT_REVISION, down=True)
    engine = create_engine(url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("jobs")}
        assert not columns & TIER1_JOB_COLUMNS and "result_version" in columns
    finally:
        engine.dispose()
    upgrade(url)
    engine = create_engine(url)
    try:
        assert TIER1_JOB_COLUMNS <= {c["name"] for c in inspect(engine).get_columns("jobs")}
    finally:
        engine.dispose()
