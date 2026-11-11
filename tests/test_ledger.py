"""`readablestem eval ledger`: what the quality ledger says of planted records, which lines it
holds against a bar, and what it refuses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from readablestem.cli.main import main
from readablestem.eval import ledger
from readablestem.ops import profile
from readablestem.pipeline.run import PIPELINE_VERSION


def _record(metrics: dict[str, Any], *, side: str = "held-out", **changed: Any) -> dict[str, Any]:
    """A kept record of this release under the production profile."""
    production = profile.load()
    stamps = {
        "set": "made",
        "split": {"name": side, "seed": "made", "set_sha256": "a" * 64, "documents": 3},
        "profile": {"name": production.name, "sha256": production.sha256, "confirmed": False},
        "pipeline_version": PIPELINE_VERSION,
        "commit": "none",
        "not_finished": [],
    }
    return stamps | changed | {"metrics": metrics}


def _whole(silent: tuple[int, ...], pages: int = 10) -> dict[str, Any]:
    """Whole documents with planted silent errors, one count a document."""
    return {"documents": [{"pages": pages, "silent_errors": errors} for errors in silent]}


def _ledger(folder: Path, record: dict[str, Any], *args: str) -> tuple[int, dict[str, Any]]:
    """Keep `record` as the whole-document record of `folder` and make the ledger."""
    (folder / "whole-made.json").write_text(json.dumps(record), encoding="utf-8")
    out = folder / "ledger.json"
    out.unlink(missing_ok=True)
    code = main(["eval", "ledger", "--records", str(folder), "--out", str(out), *args])
    return code, json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}


def _line(made: dict[str, Any], key: str) -> dict[str, Any]:
    (row,) = [row for row in made["rows"] if row["key"] == key]
    (line,) = row["measured"]
    return line


def test_planted_errors_and_flags_are_counted_and_rule_decisions_stand_apart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    metrics = _whole((1, 1, 0))
    one, two = metrics["documents"][:2]
    one |= {"doubt_false": 3, "doubt_true": 1, "rule_required": {"exam_structure": 2}}
    two |= {"doubt_false": 1, "doubt_true": 1, "rule_required": {"stereo": 1}}
    metrics["rule_owners"] = {"stereo": "the structure reader"}

    code, made = _ledger(tmp_path, _record(metrics))
    printed = capsys.readouterr().out

    assert code == 0
    silent = _line(made, "whole.silent_errors")
    assert (silent["k"], silent["n"], silent["label"]) == (2, 30, ledger.HELD_OUT)
    assert "6.7 per 100" in printed and "2 in 30 pages" in printed
    assert "1 of 3 documents" in printed
    flags = _line(made, "whole.false_flags")
    assert flags["value"] == 2.0 and flags["counts"] == "4 false, 2 true"
    assert [(rule["rule"], rule["decisions"]) for rule in made["rules"]] == [
        ("exam_structure", 2),
        ("stereo", 1),
    ]
    assert "the structure reader" in printed and "not named" in printed


@pytest.mark.parametrize(
    ("pages", "side", "goal"),
    [(237, "held-out", "not shown"), (400, "held-out", "met"), (400, "development", "")],
)
def test_a_goal_is_met_only_by_a_held_out_figure_whose_bound_is_inside_it(
    tmp_path: Path, pages: int, side: str, goal: str
) -> None:
    code, made = _ledger(tmp_path, _record(_whole((0,), pages), side=side))
    silent = _line(made, "whole.silent_errors")
    assert code == 0 and silent["value"] == 0
    assert silent["goal_bar"] == goal
    if pages == 237:  # nothing found in 237 pages still allows about 1.3 in 100
        assert 1.2 < silent["bound"] < 1.3


def test_a_held_out_figure_worse_than_its_release_bar_fails_the_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, _ = _ledger(tmp_path, _record(_whole((1, 1, 0))), "--register-bars")
    bars = json.loads((tmp_path / ledger.BARS_FILE).read_text(encoding="utf-8"))["bars"]
    assert first == 0 and bars["whole.silent_errors"]["value"] == pytest.approx(100 * 2 / 30)

    same, made = _ledger(tmp_path, _record(_whole((2, 0, 0))))
    assert same == 0 and _line(made, "whole.silent_errors")["release_bar"] == "met"
    capsys.readouterr()

    worse, _ = _ledger(tmp_path, _record(_whole((2, 1, 0))))
    assert worse == 1 and "The release FAILS" in capsys.readouterr().out

    # A development score is held against no bar, so it cannot stand in for the held-out one.
    development = _record(_whole((2, 1, 0)), side="development")
    assert _ledger(tmp_path, development)[0] == 0
    assert _ledger(tmp_path, development, "--for-release")[0] == 1
    earlier = _record(_whole((0, 0, 0)), pipeline_version="0.0.1")
    code, made = _ledger(tmp_path, earlier, "--for-release")
    assert code == 1 and _line(made, "whole.silent_errors")["label"] == ledger.EARLIER


def test_a_record_without_the_production_profile_hash_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, made = _ledger(tmp_path, _record(_whole((0, 0, 0)), profile={}))
    assert code == 2 and not made
    assert "whole-made.json does not say the profile hash" in capsys.readouterr().err


def test_the_kept_records_feed_every_row_that_names_their_eval(tmp_path: Path) -> None:
    out = tmp_path / "ledger.json"
    assert main(["eval", "ledger", "--out", str(out)]) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))["rows"]
    kept = {row.key for row in ledger.ROWS if row.eval in ("order", "burden", "outputs")}
    assert kept and {row["key"] for row in rows if row["measured"]} >= kept
