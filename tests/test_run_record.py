"""A real eval run keeps its answers and rows; a fake run, a replay and the sealed part do not."""

from __future__ import annotations

import argparse
import json

import pytest

from readablestem.cli import eval_cli
from readablestem.eval import run_record
from test_eval_cli import (  # noqa: F401  (fixtures)
    fake_inputs,
    gold,
    offline_settings,
    pipeline_args,
)


def test_describe_never_holds_a_home_path(tmp_path):
    rec = run_record.start(tmp_path, "dev-v2", repo=tmp_path)
    said = rec.describe(answers=True, items=False)
    assert said["files"] == ["answers.jsonl", "report.json", "run.json"]
    assert str(tmp_path) not in json.dumps(said)


def namespace(**fields):
    base = {"fake": False, "answers": None, "answers_offline": False, "items": None, "task": "x"}
    return argparse.Namespace(**{**base, **fields})


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
