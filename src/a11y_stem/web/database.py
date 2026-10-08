"""Database helpers shared by the web app, the worker and the CLI.

Two jobs:

- `prepare_database`: dev/test create tables directly; production refuses to start unless
  Alembic migrations are at head (`a11y-stem db upgrade`), so a deploy cannot silently
  run new code against an old schema.
- Small portable SQL helpers. The spend guard, the page quota and the job queue all rely
  on *conditional* UPDATEs whose rowcount says whether the update won, and on an
  insert-if-missing that works on both SQLite (dev, tests) and Postgres (production).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine, Result
from sqlalchemy.orm import Session

from a11y_stem.config import ConfigError, Settings
from a11y_stem.web import db

_REPO_ROOT = Path(__file__).resolve().parents[3]


def utc_today(now: datetime | None = None) -> date:
    return (now or db.utcnow()).astimezone(UTC).date()


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone-aware columns; we only store UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def rowcount(result: Result[Any]) -> int:
    """Rows matched by an UPDATE/DELETE (`Result` hides the attribute from type checkers)."""
    return int(getattr(result, "rowcount", 0) or 0)


def insert_ignore(session: Session, model: type[db.Base], values: dict[str, Any]) -> None:
    """INSERT the row unless its primary key already exists (SQLite and Postgres)."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        session.execute(pg_insert(model).values(**values).on_conflict_do_nothing())
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        session.execute(sqlite_insert(model).values(**values).on_conflict_do_nothing())
    else:  # pragma: no cover - only the two supported databases
        raise NotImplementedError(f"insert_ignore does not support {dialect}")


def ping(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


# --------------------------------------------------------------------------- migrations


def alembic_ini_path() -> Path | None:
    """`alembic.ini` at the repository root (checkout or container image), if present."""
    from a11y_stem.cli.main import project_root

    for root in (project_root(), _REPO_ROOT):
        candidate = root / "alembic.ini"
        if candidate.is_file():
            return candidate
    return None


def alembic_config(database_url: str | None = None):
    """Alembic `Config` for this repository, optionally pointed at `database_url`."""
    from alembic.config import Config

    ini = alembic_ini_path()
    if ini is None:
        raise ConfigError("alembic.ini not found; run from the a11y-stem checkout")
    config = Config(str(ini))
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def migration_heads() -> set[str] | None:
    """Head revision(s) of the migration scripts, or None if the scripts are not shipped."""
    from alembic.script import ScriptDirectory

    if alembic_ini_path() is None:
        return None
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def current_revisions(engine: Engine) -> set[str]:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return set(MigrationContext.configure(conn).get_current_heads())


def require_migrated(engine: Engine) -> None:
    """Refuse to run against a schema that is missing or behind the migration scripts."""
    current = current_revisions(engine)
    heads = migration_heads()
    if not current or (heads is not None and current != heads):
        raise ConfigError(
            "database schema is not at the latest migration; run `a11y-stem db upgrade`"
        )


def prepare_database(engine: Engine, settings: Settings) -> None:
    """Dev/test: create tables. Production: require Alembic migrations at head."""
    if settings.env in {"dev", "test"}:
        db.create_all(engine)
    else:
        require_migrated(engine)
