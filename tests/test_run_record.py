"""A real eval run keeps its answers and rows; a fake run, a replay and the sealed part do not."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import pytest

from readablestem.cli import eval_cli
from readablestem.cli.main import main
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.eval import run_record
from readablestem.models.fake import FakeVisionClient
from test_eval_cli import (  # noqa: F401  (fixtures)
    fake_inputs,
    gold,
    offline_settings,
    pipeline_args,
)


def test_start_makes_a_new_folder_per_run_and_writes_the_commit(tmp_path):
    now = dt.datetime(2026, 11, 3, 12, 0, 0, tzinfo=dt.UTC)
    first = run_record.start(tmp_path, "chem-heldout", repo=tmp_path, now=now, task="document")
    second = run_record.start(tmp_path, "chem-heldout", repo=tmp_path, now=now)
    assert first.directory == tmp_path / "runs" / "20261103T120000Z"
    assert second.directory.name == "20261103T120000Z-2"
    assert first.relative == "chem-heldout/runs/20261103T120000Z"
    info = json.loads((first.directory / "run.json").read_text())
    assert info["set"] == "chem-heldout" and info["task"] == "document"
    assert info["sha"] == "unknown"  # tmp_path is no git checkout


def test_source_state_reads_the_commit_of_a_checkout():
    sha, dirty = run_record.source_state(Path(__file__).resolve().parent)
    assert len(sha) == 40 and isinstance(dirty, bool)


def test_describe_never_holds_a_home_path(tmp_path):
    rec = run_record.start(tmp_path, "dev-v2", repo=tmp_path)
    said = rec.describe(answers=True, items=False)
    assert said["files"] == ["answers.jsonl", "report.json", "run.json"]
    assert str(tmp_path) not in json.dumps(said)


def namespace(**fields):
    base = {"fake": False, "answers": None, "answers_offline": False, "items": None, "task": "x"}
    return argparse.Namespace(**{**base, **fields})


def test_a_live_run_points_answers_and_items_into_its_folder(tmp_path):
    args = namespace()
    with eval_cli._recording(args, (tmp_path, "gold")) as rec:
        assert args.answers == rec.answers and args.items == rec.items
        (rec.directory / "items.jsonl").write_text("{}\n")
    assert rec.directory.is_dir()


def test_files_given_by_hand_are_kept_as_given(tmp_path):
    mine, rows = tmp_path / "a.jsonl", tmp_path / "i.jsonl"
    args = namespace(answers=mine, items=rows)
    with eval_cli._recording(args, (tmp_path, "gold")) as rec:
        assert (args.answers, args.items) == (mine, rows)
        report: dict = {}
        eval_cli._finish_recording(rec, args, report)
    assert report["recording"]["files"] == ["report.json", "run.json"]


@pytest.mark.parametrize(
    "fields,keep_set",
    [({"fake": True}, True), ({"answers_offline": True}, True), ({}, False)],
)
def test_fake_runs_replays_and_the_sealed_part_keep_nothing(tmp_path, fields, keep_set):
    args = namespace(**fields)
    with eval_cli._recording(args, (tmp_path, "gold") if keep_set else None) as rec:
        assert rec is None
        assert args.answers is None and args.items is None
    assert not (tmp_path / "runs").exists()


def test_a_run_that_stops_before_asking_leaves_no_folder(tmp_path):
    args = namespace()
    with eval_cli._recording(args, (tmp_path, "gold")) as rec:
        pass
    assert not rec.directory.exists()


def test_eval_pipeline_live_run_keeps_answers_items_and_report(
    gold,  # noqa: F811
    tmp_path,
    fake_inputs,  # noqa: F811
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))

    def make_client(args, settings):
        budget = JobBudget(InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
        client = eval_cli._cached(args, settings, FakeVisionClient(budget=budget))
        return client, budget, "fake-live"

    monkeypatch.setattr(eval_cli, "_make_client", make_client)
    assert main(pipeline_args(gold, tmp_path, "--max-usd", "5")) == 0
    folders = list((tmp_path / "xdg" / "readablestem" / "gold" / "runs").iterdir())
    assert len(folders) == 1
    folder = folders[0]
    assert (folder / "items.jsonl").is_file() and (folder / "report.json").is_file()
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["recording"]["run"] == f"gold/runs/{folder.name}"
    assert report["recording"]["files"][:2] == ["answers.jsonl", "items.jsonl"]
    assert len(report["recording"]["sha"]) >= 40
    assert json.loads((folder / "report.json").read_text()) == report
    assert f"kept: {folder}" in capsys.readouterr().out


def test_eval_pipeline_fake_run_keeps_no_folder(
    gold,  # noqa: F811
    tmp_path,
    fake_inputs,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert main(pipeline_args(gold, tmp_path, "--fake")) == 0
    assert not (tmp_path / "xdg").exists()
    assert "recording" not in json.loads((tmp_path / "report.json").read_text())
