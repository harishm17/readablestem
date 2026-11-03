"""The chemistry report block names no items and no gate reads it; the class script refuses
held-out and sealed runs."""

from __future__ import annotations

import inspect
import json
import sys

import pytest

from readablestem.eval import gate, run_record
from readablestem.eval import pipeline_eval as pe
from test_chem_report_fields import PAGE, dcc, run_rows  # noqa: F401


def test_the_report_names_no_item_ids(run_rows):  # noqa: F811
    _, _, item = run_rows
    block = pe.document_metrics([item])["chemistry"]
    assert "gold_ids" not in block["silent_harm"]
    assert PAGE not in json.dumps(block)


def test_the_gate_does_not_read_the_chemistry_block():
    source = inspect.getsource(gate)
    assert "chem_right_excl_times" not in source and "silent_harm" not in source


@pytest.mark.parametrize("set_name", ["heldout", "chem-heldout", "dev-v2-check"])
def test_the_class_script_refuses_held_out_and_sealed_runs(
    run_rows,  # noqa: F811
    tmp_path,
    capsys,
    monkeypatch,
    set_name,
):
    gold_dir, folder, _ = run_rows
    rec = run_record.start(tmp_path / "r", set_name, repo=tmp_path)
    rec.items.write_text((folder / "items.jsonl").read_text())
    monkeypatch.setattr(sys, "argv", ["x", "--gold", str(gold_dir), f"run={rec.directory}"])
    assert dcc.main() == 2
    assert "refused" in capsys.readouterr().err


def test_the_class_script_refuses_a_held_out_gold_folder(
    run_rows,  # noqa: F811
    tmp_path,
    capsys,
    monkeypatch,
):
    gold_dir, folder, _ = run_rows
    held = tmp_path / "chem-heldout" / "gold"
    held.mkdir(parents=True)
    (held / "g.jsonl").write_text((gold_dir / "g.jsonl").read_text())
    monkeypatch.setattr(sys, "argv", ["x", "--gold", str(held), f"run={folder}"])
    assert dcc.main() == 2
    assert "refused" in capsys.readouterr().err
