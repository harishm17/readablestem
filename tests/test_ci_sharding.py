"""The CI test shards (tests/_sharding.py, scripts/check_shards.py) lose and repeat nothing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_shards", ROOT / "scripts" / "check_shards.py")
assert spec and spec.loader
check_shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_shards)

pytest_plugins = ["pytester"]


def items(*nodeids):
    return [SimpleNamespace(nodeid=n) for n in nodeids]


def report(shard, collected, ran):
    return {"shard": shard, "collected": collected, "ran": ran}


def test_check_shards_names_a_test_no_shard_ran():
    reports = [report("1/2", ["a", "b"], ["a"]), report("2/2", ["a", "b"], [])]
    assert any("ran in no shard: b" in p for p in check_shards.check(reports, 2))
