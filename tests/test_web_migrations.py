"""Alembic migrations match web/db.py; production refuses an unmigrated database.

(The Postgres variant of the schema check is in test_web_postgres.py.)
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from a11y_stem.config import ConfigError
from a11y_stem.web import db
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
    assert [rev.revision for rev in script.walk_revisions()] == list(heads)  # one so far


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
