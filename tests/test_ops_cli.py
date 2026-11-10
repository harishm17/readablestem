"""readablestem ops aggregate|purge|cron against a SQLite file named by DATABASE_URL."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
import structlog

from fixtures.ops.seed import SENTINEL, add_job, add_user, sentinel_result
from readablestem.cli.main import main
from readablestem.config import get_settings
from readablestem.web import db

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


def seed(factory) -> None:
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, result=sentinel_result(), retain_until=PAST, blobs=2)
        add_job(s, user, retain_until=datetime.now(UTC) + timedelta(hours=1), blobs=1)


def test_cron_prints_counts_only(database, capsys):
    seed(database)
    assert main(["ops", "cron"]) == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == (
        "aggregated_jobs=2 jobs_deleted=1 blobs_deleted=2 unaggregated_deleted=0"
    )
    assert SENTINEL not in out


def test_export_writes_opted_in_jobs_only_and_prune_honours_their_deadline(
    database, capsys, tmp_path
):
    from readablestem.ops.export import EXPIRES

    soon = datetime.now(UTC) + timedelta(days=3)
    with db.session_scope(database) as s:
        user = add_user(s)
        kept = add_job(s, user, result=sentinel_result(), retain_until=soon, blobs=2)
        add_job(s, user, result=sentinel_result(), retain_until=soon)  # not opted in
        job = s.get(db.Job, kept)
        assert job is not None
        job.opt_in_retention = True
    out_dir = tmp_path / "export"
    assert main(["ops", "export-opted-in", "--out", str(out_dir)]) == 0
    assert "jobs=1 crops=2 labels=5" in capsys.readouterr().out
    assert [p.name for p in out_dir.iterdir()] == [kept]
    files = {p.name for p in (out_dir / kept).iterdir()}
    assert files == {"result.json", "labels.jsonl", "crops", EXPIRES}
    exported = " ".join(p.read_text() for p in (out_dir / kept).glob("*.json*"))
    assert "lecture notes.pdf" not in exported and "@example.org" not in exported

    assert main(["ops", "export-opted-in", "--prune", str(out_dir)]) == 0
    assert "pruned_jobs=0" in capsys.readouterr().out
    (out_dir / kept / EXPIRES).write_text((datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    assert main(["ops", "export-opted-in", "--prune", str(out_dir)]) == 0
    assert "pruned_jobs=1" in capsys.readouterr().out and not list(out_dir.iterdir())


def test_database_errors_are_reported_by_type(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/empty.db")  # no tables
    assert main(["ops", "purge"]) == 1
    err = capsys.readouterr().err
    assert "error: database error (OperationalError)" in err
    assert "SELECT" not in err  # the statement (and its parameters) stay out
