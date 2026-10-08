"""a11y-stem ops aggregate|purge|cron against a SQLite file named by DATABASE_URL."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
import structlog

from a11y_stem.cli.main import build_parser, main
from a11y_stem.config import get_settings
from a11y_stem.ops import retention
from a11y_stem.web import db
from fixtures.ops.seed import SENTINEL, add_job, add_user, sentinel_result

PAST = datetime.now(UTC) - timedelta(days=2)


@pytest.fixture(autouse=True)
def isolated_process_state():
    """The CLI configures logging and caches settings; undo both after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A migrated SQLite database, selected through the environment like in production."""
    url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = db.make_engine(url)
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def seed(factory) -> None:
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, result=sentinel_result(), retain_until=PAST, blobs=2)
        add_job(s, user, retain_until=datetime.now(UTC) + timedelta(hours=1), blobs=1)


def test_ops_commands_are_registered(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ops", "--help"])
    out = capsys.readouterr().out
    for command in ("aggregate", "purge", "cron"):
        assert command in out
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ops"])  # a subcommand is required


def test_aggregate_then_purge(database, capsys):
    seed(database)
    assert main(["ops", "aggregate"]) == 0
    assert last_line(capsys) == "aggregated_jobs=2"
    assert main(["ops", "purge"]) == 0
    assert last_line(capsys) == "jobs_deleted=1 blobs_deleted=2 unaggregated_deleted=0"


def test_cron_prints_counts_only(database, capsys):
    seed(database)
    assert main(["ops", "cron"]) == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == (
        "aggregated_jobs=2 jobs_deleted=1 blobs_deleted=2 unaggregated_deleted=0"
    )
    assert SENTINEL not in out


def test_cron_purges_and_fails_when_aggregation_fails(database, capsys, monkeypatch):
    seed(database)

    def broken(*_args, **_kwargs):
        raise RuntimeError("aggregation bug")

    monkeypatch.setattr(retention, "aggregate_finished", broken)
    assert main(["ops", "cron"]) == 1
    assert last_line(capsys) == (
        "aggregated_jobs=failed jobs_deleted=1 blobs_deleted=2 unaggregated_deleted=1"
    )


def test_database_errors_are_reported_by_type(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/empty.db")  # no tables
    assert main(["ops", "purge"]) == 1
    err = capsys.readouterr().err
    assert "error: database error (OperationalError)" in err
    assert "SELECT" not in err  # the statement (and its parameters) stay out
