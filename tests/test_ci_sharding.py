"""The CI test shards (tests/_sharding.py, scripts/check_shards.py) lose and repeat nothing."""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

import _sharding

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_shards", ROOT / "scripts" / "check_shards.py")
assert spec and spec.loader
check_shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_shards)

pytest_plugins = ["pytester"]


def items(*nodeids):
    return [SimpleNamespace(nodeid=n) for n in nodeids]


def test_parse_shard():
    assert _sharding.parse_shard("2/4") == (2, 4)
    for bad in ("0/4", "5/4", "x", "1-4", "1/"):
        with pytest.raises(ValueError):
            _sharding.parse_shard(bad)


def test_every_test_lands_in_exactly_one_shard_even_with_no_durations():
    rng = random.Random(7)
    nodeids = [f"tests/test_{rng.randrange(40)}.py::test_{i}" for i in range(500)]
    for total in (1, 3, 4):
        seen = []
        for index in range(1, total + 1):
            kept, _ = _sharding.select(items(*nodeids), index, total, {})
            seen += [i.nodeid for i in kept]
        assert sorted(seen) == sorted(nodeids)


def test_a_file_stays_whole_and_the_split_is_the_same_every_time():
    nodeids = [f"tests/test_{f}.py::test_{i}" for f in "abcdef" for i in range(5)]
    first = _sharding.assign(nodeids, 3, {})
    assert first == _sharding.assign(list(reversed(nodeids)), 3, {})
    assert set(first) == {f"tests/test_{f}.py" for f in "abcdef"}


def test_shards_are_balanced_by_recorded_duration():
    durations = {"tests/test_big.py::test_x": 100.0, "tests/test_mid.py::test_x": 60.0}
    nodeids = [*durations, "tests/test_small.py::test_x", "tests/test_tiny.py::test_x"]
    shard_of = _sharding.assign(nodeids, 2, durations)
    assert shard_of["tests/test_big.py"] != shard_of["tests/test_mid.py"]
    assert shard_of["tests/test_small.py"] == shard_of["tests/test_mid.py"]


def test_a_new_test_file_is_assigned_with_the_default_weight():
    durations = {"tests/test_old.py::test_x": 5.0}
    nodeids = ["tests/test_old.py::test_x", "tests/test_new.py::test_y"]
    assert _sharding.assign(nodeids, 2, durations)["tests/test_new.py"] in (1, 2)


def test_the_committed_durations_are_numbers_keyed_by_test_id():
    durations = _sharding.load_durations()
    assert durations, "tests/shard_durations.json is missing or empty"
    assert all(
        isinstance(v, float | int) and v >= _sharding.MIN_RECORDED for v in durations.values()
    )
    assert all(key.startswith("tests/") and "::" in key for key in durations)


def report(shard, collected, ran):
    return {"shard": shard, "collected": collected, "ran": ran}


def test_check_shards_accepts_a_partition():
    reports = [report("1/2", ["a", "b", "c"], ["a"]), report("2/2", ["a", "b", "c"], ["b", "c"])]
    assert check_shards.check(reports, 2) == []


def test_check_shards_names_a_test_no_shard_ran():
    reports = [report("1/2", ["a", "b"], ["a"]), report("2/2", ["a", "b"], [])]
    assert any("ran in no shard: b" in p for p in check_shards.check(reports, 2))


def test_check_shards_names_a_test_two_shards_ran_and_a_missing_shard():
    reports = [report("1/3", ["a"], ["a"]), report("2/3", ["a"], ["a"])]
    problems = check_shards.check(reports, 3)
    assert any("expected 3 shard reports" in p for p in problems)
    assert any("ran twice" in p for p in problems)


def test_check_shards_notices_shards_that_collected_different_tests():
    reports = [report("1/2", ["a", "b"], ["a"]), report("2/2", ["a"], ["b"])]
    assert any("different set" in p for p in check_shards.check(reports, 2))


def test_a_sharded_run_reports_what_it_collected_and_ran(pytester, tmp_path, monkeypatch):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parent))
    pytester.makepyfile(test_one="def test_a(): pass", test_two="def test_b(): pass")
    seen = []
    for index in (1, 2):
        out = tmp_path / f"r{index}.json"
        result = pytester.runpytest_subprocess(
            f"--shard={index}/2", f"--shard-report={out}", "-p", "no:cacheprovider"
        )
        result.assert_outcomes(passed=1)
        seen.append(json.loads(out.read_text()))
    assert check_shards.check(seen, 2) == []
