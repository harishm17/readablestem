"""An eval's set is split by document, and only a held-out score can meet a bar."""

from __future__ import annotations

import hashlib
import json

import pytest

from readablestem.cli.main import main
from readablestem.eval import split
from readablestem.ops import profile


def documents(count: int = 40) -> dict[str, str]:
    return {f"doc-{n:02d}": hashlib.sha256(f"file {n}".encode()).hexdigest() for n in range(count)}


def report(tmp_path, side: str, **changes):
    chosen = split.split("seed-a", documents())
    body = {
        "metrics": {"order": {"clean": 0.93}},
        "pipeline_version": "0.5.10",
        "profile": {"name": "production", "sha256": profile.load().sha256},
        "split": chosen.stamp(side),
    } | changes
    path = tmp_path / "report.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def bar(path, capsys) -> tuple[int, str]:
    code = main(["eval", "bar", str(path), "--measure", "order.clean", "--at-least", "0.9"])
    return code, capsys.readouterr().out


def test_no_document_is_on_both_sides_and_none_is_lost():
    chosen = split.split("seed-a", documents())
    assert chosen.development and chosen.held_out
    assert not set(chosen.development) & set(chosen.held_out)
    assert set(chosen.development) | set(chosen.held_out) == set(documents())


def test_another_seed_moves_documents_and_changes_the_set_hash():
    first, second = split.split("seed-a", documents()), split.split("seed-b", documents())
    assert first.held_out != second.held_out
    assert first.set_hash(split.HELD_OUT) != second.set_hash(split.HELD_OUT)


def test_a_new_document_moves_no_other():
    before = split.split("seed-a", documents(40))
    after = split.split("seed-a", documents(41))
    assert set(before.held_out) <= set(after.held_out)
    assert set(before.development) <= set(after.development)


def test_a_spent_held_out_document_is_development_from_then_on():
    first = split.split("seed-a", documents())
    spent = first.held_out[:3]
    second = split.split("seed-a", documents(), spent=spent)
    assert set(spent) <= set(second.development)
    assert set(second.held_out) == set(first.held_out) - set(spent)


def test_an_edited_held_out_file_is_caught_before_a_run():
    first = split.split("seed-a", documents())
    registered = first.set_hash(split.HELD_OUT)
    first.verify(split.HELD_OUT, registered)
    edited = documents() | {first.held_out[0]: "0" * 64}
    with pytest.raises(split.SetChanged):
        split.split("seed-a", edited).verify(split.HELD_OUT, registered)


def test_a_held_out_score_under_the_production_profile_meets_its_bar(tmp_path, capsys):
    code, out = bar(report(tmp_path, split.HELD_OUT), capsys)
    assert code == 0
    assert out.startswith("bar met")


def test_a_held_out_score_below_the_bar_fails(tmp_path, capsys):
    path = report(tmp_path, split.HELD_OUT, metrics={"order": {"clean": 0.5}})
    code, out = bar(path, capsys)
    assert code == 1
    assert out.startswith("bar not met")


@pytest.mark.parametrize(
    "side, settings",
    [(split.DEVELOPMENT, "production"), (split.HELD_OUT, profile.DEVELOPMENT)],
    ids=["development documents", "development settings"],
)
def test_a_development_score_meets_no_bar(tmp_path, capsys, side, settings):
    path = report(tmp_path, side, profile={"name": settings, "sha256": profile.load().sha256})
    code, out = bar(path, capsys)
    assert code != 0
    assert out.startswith("development score")
    assert "bar met" not in out


@pytest.mark.parametrize("field", ["split", "profile", "pipeline_version"])
def test_a_report_that_lacks_a_stamp_is_refused(tmp_path, capsys, field):
    code, out = bar(report(tmp_path, split.HELD_OUT, **{field: None}), capsys)
    assert code == 2
    assert out.startswith("refused")
