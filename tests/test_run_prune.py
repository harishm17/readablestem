"""Kept runs are private, can be pruned (but not what a committed report names), and the
held-out and sealed ones are recognised."""

from __future__ import annotations

import datetime as dt

import pytest

from readablestem.cli.main import main
from readablestem.eval import run_record
from test_eval_cli import (  # noqa: F401  (fixtures)
    fake_inputs,
    gold,
    offline_settings,
    pipeline_args,
)


def _run(root, name, stamp):
    when = dt.datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=dt.UTC)
    return run_record.start(root, name, repo=root, now=when)


def test_run_folders_are_private_and_so_are_their_files(tmp_path):
    rec = run_record.start(tmp_path, "gold", repo=tmp_path)
    assert (tmp_path / "runs").stat().st_mode & 0o777 == 0o700
    assert rec.directory.stat().st_mode & 0o777 == 0o700
    for name in ("answers.jsonl", "items.jsonl", "report.json", "run.json"):
        assert (rec.directory / name).stat().st_mode & 0o777 == 0o600, name


def test_prune_deletes_old_runs_but_keeps_new_ones_and_those_a_report_names(tmp_path):
    old = _run(tmp_path, "gold", "20260101")
    named = _run(tmp_path, "gold", "20260102")
    new = _run(tmp_path, "gold", "20261101")
    (tmp_path / "runs" / "notes").mkdir()
    now = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)
    keep = {named.relative}
    deleted, kept = run_record.prune(
        tmp_path, "gold", older_than_days=30, keep=keep, now=now, dry_run=True
    )
    assert deleted == [old.directory] and kept == [named.directory]
    assert old.directory.exists()  # a dry run deletes nothing
    run_record.prune(tmp_path, "gold", older_than_days=30, keep=keep, now=now)
    assert not old.directory.exists()
    assert named.directory.exists() and new.directory.exists()
    assert (tmp_path / "runs" / "notes").exists()  # not a timestamp: never touched


def test_eval_prune_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    old = _run(run_record.set_root("dev-v2"), "dev-v2", "20250101")
    assert main(["eval", "prune", "--older-than", "30", "--dry-run"]) == 0
    assert old.directory.exists() and "would delete 1 run" in capsys.readouterr().out
    assert main(["eval", "prune", "--older-than", "30", "--set", "dev-v2"]) == 0
    assert not old.directory.exists()


@pytest.mark.parametrize(
    "set_name,sealed",
    [("heldout", True), ("chem-heldout", True), ("dev-v2-check", True), ("dev-v2", False)],
)
def test_which_runs_are_off_limits(tmp_path, set_name, sealed):
    rec = _run(tmp_path / "r", set_name, "20260101")
    assert bool(run_record.sealed_reason(rec.directory)) is sealed
    assert bool(run_record.sealed_reason(rec.items)) is sealed
