"""Test-wide set-up: process-wide state that one test must not leave for the next (the
model rate gates, and what the worker has seen of the model provider)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

import _sharding
from readablestem.models import base
from readablestem.web import provider_health


@pytest.fixture(autouse=True)
def _no_rate_pause_between_tests():
    """A 429 in one test pauses that provider's gate (`models.base.RateGate`); a pause
    must not make the next test wait."""
    base._GATES.clear()
    provider_health.HEALTH.reset()
    yield
    base._GATES.clear()
    provider_health.HEALTH.reset()


# ---- CI sharding (tests/_sharding.py) -----------------------------------------------------


def pytest_addoption(parser):
    group = parser.getgroup("shard", "split the run across CI jobs")
    group.addoption("--shard", metavar="INDEX/TOTAL", help="run only this shard, e.g. 2/4")
    group.addoption("--shard-report", metavar="FILE", help="write what this run collected and ran")
    group.addoption("--shard-record", metavar="FILE", help="write per-test durations after the run")


def _is_worker(config) -> bool:
    return hasattr(config, "workerinput")


@pytest.hookimpl(trylast=True)  # after the -m / -k deselection, so the split sees the final set
def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--shard")
    config._shard_collected = [item.nodeid for item in items]
    report = config.getoption("--shard-report")
    # Under xdist the controller never collects; one worker saves the full collection.
    if report and _is_worker(config) and config.workerinput["workerid"] == "gw0":
        Path(report + ".collected").write_text(json.dumps(config._shard_collected))
    if not spec:
        return
    index, total = _sharding.parse_shard(spec)
    kept, dropped = _sharding.select(items, index, total, _sharding.load_durations())
    items[:] = kept
    config.hook.pytest_deselected(items=dropped)


def pytest_configure(config):
    if not _is_worker(config):
        config.pluginmanager.register(_Recorder(config), "shard-recorder")


class _Recorder:
    """Runs in the pytest process that reports results (the xdist controller, or the only
    process): notes which tests ran and how long they took."""

    def __init__(self, config):
        self.config = config
        self.ran: set[str] = set()
        self.seconds: dict[str, float] = defaultdict(float)

    def pytest_runtest_logreport(self, report):
        self.seconds[report.nodeid] += report.duration
        # Count a test as run once its call finished, or it was skipped or errored in setup.
        if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
            self.ran.add(report.nodeid)

    def pytest_sessionfinish(self, session):
        config = self.config
        record = config.getoption("--shard-record")
        if record:
            slow = {
                nodeid: round(seconds, 3)
                for nodeid, seconds in sorted(self.seconds.items())
                if seconds >= _sharding.MIN_RECORDED
            }
            Path(record).write_text(json.dumps(slow, indent=0, sort_keys=True) + "\n")
        report = config.getoption("--shard-report")
        if report:
            collected_file = Path(report + ".collected")
            if collected_file.exists():
                collected = json.loads(collected_file.read_text())
                collected_file.unlink()
            else:  # a run without xdist collects in this process
                collected = getattr(config, "_shard_collected", [])
            Path(report).write_text(
                json.dumps(
                    {
                        "shard": config.getoption("--shard"),
                        "collected": collected,
                        "ran": sorted(self.ran),
                    }
                )
            )
