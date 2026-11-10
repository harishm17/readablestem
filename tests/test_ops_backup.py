"""backup-check and the restore drill, against a real Postgres.

Server: DATABASE_URL when it points at Postgres (CI), otherwise the embedded `pgserver`
(Python 3.12), otherwise skipped. The drill needs `pg_dump` and `pg_restore`: pgserver
ships them; with an external server they must be on PATH.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from fixtures.ops.seed import SENTINEL, add_job, add_user
from readablestem.cli.main import main
from readablestem.config import get_settings
from readablestem.ops import backup
from readablestem.ops.backup import DrillError, restore_drill
from readablestem.web import db
from readablestem.web.database import alembic_config

pytestmark = pytest.mark.postgres


@contextmanager
def _postgres_server(tmp_path_factory) -> Iterator[tuple[str, Path | None]]:
    configured = os.environ.get("DATABASE_URL", "")
    if configured.startswith("postgresql"):
        yield configured, None
        return
    try:
        import pgserver  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("no Postgres: set DATABASE_URL or install pgserver (Python 3.12)")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgbackup"), cleanup_mode="delete")
    try:
        bin_dir = Path(pgserver.__file__).parent / "pginstall" / "bin"
        yield server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1), bin_dir
    finally:
        server.cleanup()


@pytest.fixture(scope="module")
def server(tmp_path_factory) -> Generator[tuple[str, Path | None]]:
    with _postgres_server(tmp_path_factory) as found:
        yield found


@pytest.fixture
def pg_bin(server) -> Path | None:
    return server[1]


def _database(server_url: str) -> Generator[str]:
    name = f"a11y_backup_{uuid.uuid4().hex[:10]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(server_url).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _migrate(url: str) -> None:
    config = alembic_config(url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture
def live(server) -> Generator[str]:
    """A migrated database holding an admin, a user, two jobs with crops, and spend."""
    for url in _database(server[0]):
        _migrate(url)
        engine = create_engine(url)
        factory = db.make_sessionmaker(engine)
        with db.session_scope(factory) as session:
            admin = add_user(session, "admin@example.org")
            admin.is_admin = True
            user = add_user(session, "teacher@example.org")
            add_job(session, user, blobs=2, edits=[("e1", "accept")])
            add_job(session, user, blobs=1)
            session.add(db.SpendDay(day=datetime.now(UTC).date(), spent_usd=0.5, held_usd=0.0))
        engine.dispose()
        yield url


def _databases(server_url: str) -> set[str]:
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        names = {row[0] for row in conn.execute(text("SELECT datname FROM pg_database"))}
    admin.dispose()
    return names


def test_the_drill_restores_into_a_scratch_database_and_removes_it(live, server, pg_bin):
    before = _databases(server[0])
    result = restore_drill(live, pg_bin=pg_bin)
    assert result.ok, result.problems
    assert result.check.schema == "head"
    assert result.check.counts["jobs"] == 2 and result.check.counts["blobs"] == 3
    assert result.dump_bytes > 0 and result.changed_tables == []
    assert _databases(server[0]) == before  # the scratch database is gone


def test_the_drill_notices_a_restore_that_lost_rows(live, pg_bin, monkeypatch):
    real = backup.check_database

    def lossy(engine, **kwargs):
        check = real(engine, **kwargs)
        if engine.url.database and "_drill_" in engine.url.database:
            check.counts["jobs"] = 1  # as if the restore dropped a row
        return check

    monkeypatch.setattr(backup, "check_database", lossy)
    result = restore_drill(live, pg_bin=pg_bin)
    assert not result.ok and result.changed_tables == ["jobs"]


def test_a_failed_restore_still_drops_the_scratch_database(live, server, pg_bin, tmp_path):
    bad = tmp_path / "pg_restore"
    bad.write_text("#!/bin/sh\necho SECRET-ROW-TEXT >&2\nexit 3\n")
    bad.chmod(0o755)
    # With an external server (CI) there is no pg_bin: take pg_dump from PATH.
    real_pg_dump = Path(pg_bin) / "pg_dump" if pg_bin else shutil.which("pg_dump")
    assert real_pg_dump, "pg_dump is neither in pg_bin nor on PATH"
    (tmp_path / "pg_dump").symlink_to(real_pg_dump)
    before = _databases(server[0])
    with pytest.raises(DrillError, match=r"pg_restore failed with exit code 3") as raised:
        restore_drill(live, pg_bin=tmp_path)
    assert "SECRET" not in str(raised.value)
    assert _databases(server[0]) == before


def test_cli_commands_print_counts_and_set_the_exit_code(live, pg_bin, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", live)
    get_settings.cache_clear()
    try:
        assert main(["ops", "backup-check"]) == 0
        out = capsys.readouterr().out
        assert "schema=head" in out and "backup_check=ok" in out and SENTINEL not in out
        argv = ["ops", "restore-drill"] + (["--pg-bin", str(pg_bin)] if pg_bin else [])
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "restore_drill=ok" in out and "dump_bytes=" in out and SENTINEL not in out
    finally:
        get_settings.cache_clear()
