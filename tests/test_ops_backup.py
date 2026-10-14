"""backup-check and the restore drill, against a real Postgres.

Server: DATABASE_URL when it points at Postgres (CI), otherwise the embedded `pgserver`
(Python 3.12), otherwise skipped. The drill needs `pg_dump` and `pg_restore`: pgserver
ships them; with an external server they must be on PATH.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from a11y_stem.cli.main import main
from a11y_stem.config import get_settings
from a11y_stem.ops import backup
from a11y_stem.ops.backup import DrillError, check_database, restore_drill
from a11y_stem.web import db
from a11y_stem.web.database import alembic_config
from fixtures.ops.seed import SENTINEL, add_job, add_user

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


def test_check_database_describes_a_good_copy_by_counts_only(live):
    engine = create_engine(live)
    check = check_database(engine)
    engine.dispose()
    assert check.ok and check.schema == "head" and check.problems == []
    assert check.admins == 1 and check.allowed_users == 2
    assert check.counts["jobs"] == 2 and check.counts["blobs"] == 3
    assert check.counts["review_edits"] == 1
    assert check.newest_job_age_h is not None and check.newest_spend_day is not None
    assert SENTINEL not in " ".join(check.lines())


def test_check_database_names_each_way_a_copy_cannot_run(server):
    for url in _database(server[0]):
        engine = create_engine(url)
        empty = check_database(engine)
        assert empty.schema == "none" and not empty.ok
        assert "users" in empty.missing_tables
        _migrate(url)
        no_admin = check_database(engine)
        assert no_admin.schema == "head" and not no_admin.ok
        assert any("no admin" in p for p in no_admin.problems)
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = '000000000000'"))
        ahead = check_database(engine)
        assert ahead.schema == "ahead"
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE courses CASCADE"))
        assert "courses" in check_database(engine).missing_tables
        engine.dispose()


def test_the_drill_restores_into_a_scratch_database_and_removes_it(live, server, pg_bin):
    before = _databases(server[0])
    result = restore_drill(live, pg_bin=pg_bin)
    assert result.ok, result.problems
    assert result.check.schema == "head"
    assert result.check.counts["jobs"] == 2 and result.check.counts["blobs"] == 3
    assert result.dump_bytes > 0 and result.changed_tables == []
    assert _databases(server[0]) == before  # the scratch database is gone


def test_the_drill_fails_loudly_on_a_database_that_is_not_ready(server, pg_bin):
    for url in _database(server[0]):
        _migrate(url)  # migrated, but no users: nobody could sign in
        result = restore_drill(url, pg_bin=pg_bin)
        assert not result.ok and any("no admin" in p for p in result.problems)


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


def test_the_drill_errors_clearly_without_tools_or_postgres(live, tmp_path, monkeypatch):
    with pytest.raises(DrillError, match="needs a Postgres"):
        restore_drill("sqlite:///x.db")
    with pytest.raises(DrillError, match="pg_dump not found"):
        restore_drill(live, pg_bin=tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(DrillError, match="install the PostgreSQL client tools"):
        restore_drill(live)


def test_a_failed_restore_still_drops_the_scratch_database(live, server, pg_bin, tmp_path):
    bad = tmp_path / "pg_restore"
    bad.write_text("#!/bin/sh\necho SECRET-ROW-TEXT >&2\nexit 3\n")
    bad.chmod(0o755)
    (tmp_path / "pg_dump").symlink_to(Path(pg_bin or "") / "pg_dump")
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


def test_cli_backup_check_exits_1_for_an_unmigrated_database(server, monkeypatch, capsys):
    for url in _database(server[0]):
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            assert main(["ops", "backup-check"]) == 1
            out = capsys.readouterr().out
            assert "schema=none" in out and "backup_check=failed" in out
        finally:
            get_settings.cache_clear()


def test_a_check_with_old_data_reports_its_age(live):
    engine = create_engine(live)
    later = datetime.now(UTC) + timedelta(hours=30)
    check = check_database(engine, now=later)
    engine.dispose()
    assert check.newest_job_age_h is not None and check.newest_job_age_h > 29
