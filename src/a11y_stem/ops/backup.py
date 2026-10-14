"""Is a database copy good enough to run on? (`ops backup-check`, `ops restore-drill`)

A backup nobody has restored is a hope. Two checks, both content-free (table names and
row counts only, never a row):

- `check_database(engine)` reads a database, normally a restored copy, and says whether
  the app could start on it: the schema is at this release's migration head, every table
  is there, an admin can sign in, and how old the newest data is. Run it against the
  instance a backup was restored into (docs/operations.md, "Backups and restore").
- `restore_drill(url)` proves the dump-and-restore path on any Postgres: `pg_dump` the
  database, `pg_restore` the dump into a scratch database on the same server, run
  `check_database` on the scratch copy, compare row counts with the source, drop the
  scratch database and the dump file. The dump holds documents, so it is written to a
  private temporary directory and always removed.

The password is handed to the Postgres tools in the environment, never on a command line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import URL, Engine, make_url

from a11y_stem.web import db
from a11y_stem.web.database import current_revisions, migration_heads


class DrillError(RuntimeError):
    """The drill could not run (no Postgres tools, not a Postgres URL, no permission)."""


@dataclass
class DatabaseCheck:
    schema: str  # "head", "ahead", "behind" or "none"
    counts: dict[str, int] = field(default_factory=dict)
    missing_tables: list[str] = field(default_factory=list)
    admins: int = 0
    allowed_users: int = 0
    newest_job_age_h: float | None = None
    newest_spend_day: str | None = None

    @property
    def problems(self) -> list[str]:
        out = []
        if self.schema in {"none", "behind"}:
            out.append(f"schema is {self.schema}: run `a11y-stem db upgrade`")
        if self.missing_tables:
            out.append("missing tables: " + ", ".join(self.missing_tables))
        elif self.admins == 0:
            out.append("no admin user: nobody could sign in to manage the beta")
        return out

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> list[str]:
        age = "none" if self.newest_job_age_h is None else f"{self.newest_job_age_h:.1f}"
        out = [
            f"schema={self.schema}",
            f"tables={len(self.counts)} missing_tables={len(self.missing_tables)}",
            f"allowed_users={self.allowed_users} admins={self.admins}",
            f"newest_job_age_h={age} newest_spend_day={self.newest_spend_day or 'none'}",
        ]
        out.append(" ".join(f"{name}={n}" for name, n in sorted(self.counts.items())))
        return out


def check_database(engine: Engine, *, now: datetime | None = None) -> DatabaseCheck:
    """Read-only. Counts rows per table; nothing from any row is returned."""
    now = now or datetime.now(UTC)
    found = set(inspect(engine).get_table_names())
    expected = set(db.Base.metadata.tables)
    check = DatabaseCheck(schema=_schema_state(engine, found))
    check.missing_tables = sorted(expected - found)
    with engine.connect() as conn:
        for name in sorted(expected & found):
            table = db.Base.metadata.tables[name]
            check.counts[name] = int(conn.scalar(select(func.count()).select_from(table)) or 0)
        if "users" in found:
            users = db.User.__table__
            check.allowed_users = int(
                conn.scalar(select(func.count()).select_from(users).where(users.c.is_allowed)) or 0
            )
            check.admins = int(
                conn.scalar(
                    select(func.count())
                    .select_from(users)
                    .where(users.c.is_allowed, users.c.is_admin)
                )
                or 0
            )
        if "jobs" in found:
            newest = conn.scalar(select(func.max(db.Job.__table__.c.created_at)))
            if newest is not None:
                newest = newest.replace(tzinfo=UTC) if newest.tzinfo is None else newest
                check.newest_job_age_h = (now - newest).total_seconds() / 3600
        if "spend_days" in found:
            day = conn.scalar(select(func.max(db.SpendDay.__table__.c.day)))
            check.newest_spend_day = None if day is None else str(day)
    return check


def _schema_state(engine: Engine, tables: set[str]) -> str:
    if "alembic_version" not in tables:
        return "none"
    current = current_revisions(engine)
    heads = migration_heads()
    if not current:
        return "none"
    if heads is None or current == heads:
        return "head"
    from a11y_stem.web.database import known_revisions

    return "ahead" if current - (known_revisions() or set()) else "behind"


# --------------------------------------------------------------------------- the drill


@dataclass
class DrillResult:
    check: DatabaseCheck
    source_counts: dict[str, tuple[int, int]]  # table -> (before the dump, after the restore)
    dump_bytes: int
    seconds: float
    changed_tables: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        out = list(self.check.problems)
        if self.changed_tables:
            out.append(
                "row counts differ from the source in: "
                + ", ".join(self.changed_tables)
                + " (is the source busy? run the drill on a quiet copy)"
            )
        return out

    @property
    def ok(self) -> bool:
        return not self.problems


def find_pg_tool(name: str, pg_bin: Path | None = None) -> str:
    """Path of `pg_dump` or `pg_restore`: in `pg_bin` if given, else on PATH."""
    if pg_bin is not None:
        candidate = Path(pg_bin) / name
        if candidate.is_file():
            return str(candidate)
        raise DrillError(f"{name} not found in {pg_bin}")
    found = shutil.which(name)
    if found is None:
        raise DrillError(
            f"{name} not found on PATH: install the PostgreSQL client tools "
            "(for example `apt install postgresql-client`) or pass --pg-bin DIR"
        )
    return found


def restore_drill(database_url: str, *, pg_bin: Path | None = None) -> DrillResult:
    """Dump `database_url`, restore into a scratch database on the same server, check it.

    The scratch database is dropped and the dump deleted whatever happens. Needs the
    CREATE DATABASE privilege on the server (the `postgres` user; on Cloud SQL the
    default `postgres` user, not the app's `a11y` user).
    """
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise DrillError("the drill needs a Postgres DATABASE_URL")
    pg_dump = find_pg_tool("pg_dump", pg_bin)
    pg_restore = find_pg_tool("pg_restore", pg_bin)
    scratch = f"{url.database}_drill_{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    source = create_engine(url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    scratch_url = url.set(database=scratch)
    created = False
    try:
        before = check_database(source).counts
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch}"'))
        created = True
        with tempfile.TemporaryDirectory(prefix="a11y-drill-") as tmp:
            os.chmod(tmp, 0o700)
            dump = Path(tmp) / "drill.dump"
            dump_flags = ["--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)]
            _run([pg_dump, *dump_flags, *_connection_flags(url)], url)
            dump_bytes = dump.stat().st_size
            restore_flags = ["--exit-on-error", "--no-owner", "--no-privileges"]
            restore_flags += ["--dbname", scratch]
            _run(
                [pg_restore, *restore_flags, *_connection_flags(url, with_dbname=False), str(dump)],
                url,
            )
        restored = create_engine(scratch_url)
        try:
            check = check_database(restored)
        finally:
            restored.dispose()
        after = check_database(source).counts
        counts = {
            name: (before.get(name, 0), after.get(name, 0)) for name in set(before) | set(after)
        }
        changed = sorted(
            name
            for name, (low, high) in counts.items()
            if not min(low, high) <= check.counts.get(name, -1) <= max(low, high)
        )
        return DrillResult(
            check=check,
            source_counts=counts,
            dump_bytes=dump_bytes,
            seconds=time.monotonic() - started,
            changed_tables=changed,
        )
    finally:
        if created:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        admin.dispose()
        source.dispose()


def _connection_flags(url: URL, *, with_dbname: bool = True) -> list[str]:
    """Host, port, user and database as flags. A `?host=/cloudsql/...` query (Cloud SQL's
    unix socket) is the host. The password is not here: see `_run`."""
    host = url.query.get("host") or url.host
    flags = []
    if host:
        flags += ["--host", str(host)]
    if url.port:
        flags += ["--port", str(url.port)]
    if url.username:
        flags += ["--username", url.username]
    if with_dbname and url.database:
        flags += ["--dbname", url.database]
    return flags


def _run(command: list[str], url: URL) -> None:
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    env.pop("PGDATABASE", None)  # the flags name the database
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Postgres tools print table names and sometimes row text in errors: report only
        # the tool and the exit code.
        raise DrillError(f"{Path(command[0]).name} failed with exit code {result.returncode}")
