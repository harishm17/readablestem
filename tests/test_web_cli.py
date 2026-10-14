"""`readablestem serve`, `users allow|deny|list` and `db upgrade` (offline, SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, select

from fixtures.web.support import make_settings
from readablestem.cli.main import build_parser, main
from readablestem.web import db


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = make_settings(tmp_path, env="dev")
    monkeypatch.setattr("readablestem.config.get_settings", lambda: s)
    return s


def allowed(settings) -> dict[str, bool]:
    engine = db.make_engine(settings.database_url)
    try:
        with db.session_scope(db.make_sessionmaker(engine)) as session:
            return {
                u.email: u.is_admin
                for u in session.scalars(select(db.User).where(db.User.is_allowed.is_(True)))
            }
    finally:
        engine.dispose()


def test_help_lists_the_web_commands(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for command in ("serve", "users", "db"):
        assert command in out


def test_serve_runs_uvicorn_with_the_app_factory(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    assert main(["serve", "--host", "0.0.0.0", "--port", "9000", "--reload"]) == 0
    assert seen["app"] == "readablestem.web.app:create_app" and seen["factory"] is True
    assert (seen["host"], seen["port"], seen["reload"]) == ("0.0.0.0", 9000, True)


def test_users_allow_list_and_deny(settings, capsys):
    assert main(["users", "allow", "Prof@Example.org"]) == 0
    assert main(["users", "allow", "boss@example.org", "--admin"]) == 0
    assert allowed(settings) == {"prof@example.org": False, "boss@example.org": True}
    assert main(["users", "list"]) == 0
    out = capsys.readouterr().out
    assert "prof@example.org" in out and "admin" in out and "never" in out

    assert main(["users", "deny", "prof@example.org"]) == 0
    assert allowed(settings) == {"boss@example.org": True}
    assert main(["users", "deny", "nobody@example.org"]) == 2
    assert "not a known user" in capsys.readouterr().err


def test_users_delete_erases_the_account_and_every_document(settings, capsys):
    """The privacy page offers deletion on request; this is how it is done."""
    from datetime import timedelta

    from readablestem.web.db import ErrorAggregate, Job, User

    assert main(["users", "allow", "prof@example.org"]) == 0
    engine = db.make_engine(settings.database_url)
    factory = db.make_sessionmaker(engine)
    with db.session_scope(factory) as session:
        user = session.scalar(select(User).where(User.email == "prof@example.org"))
        assert user is not None
        now = db.utcnow()
        job = Job(
            user_id=user.id,
            filename="notes.pdf",
            status="failed",
            error_code="timeout",
            retain_until=now + timedelta(hours=24),
            finished_at=now,
        )
        session.add(job)
        session.flush()
        session.add(
            db.Blob(job_id=job.id, kind="upload", media_type="application/pdf", content=b"%PDF-")
        )
    assert main(["users", "delete", "prof@example.org"]) == 2  # needs --yes
    assert "--yes" in capsys.readouterr().err
    assert main(["users", "delete", "prof@example.org", "--yes"]) == 0
    assert "jobs_deleted=1 blobs_deleted=1" in capsys.readouterr().out
    with db.session_scope(factory) as session:
        assert session.scalar(select(User)) is None
        assert session.scalar(select(Job)) is None
        assert session.scalar(select(db.Blob)) is None
        # The content-free counts survive.
        assert session.scalar(select(ErrorAggregate).where(ErrorAggregate.step == "job"))
    engine.dispose()
    assert main(["users", "delete", "prof@example.org", "--yes"]) == 2


def test_users_allow_rejects_a_malformed_email(settings, capsys):
    assert main(["users", "allow", "not-an-email"]) == 2
    assert "not an email address" in capsys.readouterr().err


def test_users_commands_refuse_an_unmigrated_production_database(tmp_path, monkeypatch, capsys):
    prod = make_settings(tmp_path, env="prod")
    monkeypatch.setattr("readablestem.config.get_settings", lambda: prod)
    assert main(["users", "list"]) == 2
    assert "readablestem db upgrade" in capsys.readouterr().err


def test_db_upgrade_applies_the_migrations(tmp_path, monkeypatch, capsys):
    s = make_settings(tmp_path, env="prod")
    monkeypatch.setattr("readablestem.config.get_settings", lambda: s)
    from readablestem.web import database

    original = database.alembic_config

    def quiet_config(url=None):
        config = original(url)
        config.attributes["configure_logger"] = False
        return config

    monkeypatch.setattr(database, "alembic_config", quiet_config)
    assert main(["db", "upgrade"]) == 0
    assert "latest migration" in capsys.readouterr().out
    engine = create_engine(s.database_url)
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert main(["users", "list"]) == 0  # production check now passes
