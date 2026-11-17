"""Test-wide set-up: process-wide state that one test must not leave for the next (the
model rate gates, and what the worker has seen of the model provider)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

import _routes
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
    group.addoption(
        "--route-coverage",
        action="store_true",
        help="fail the run for a route of the app no test got an answer from (a whole run only)",
    )


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
    _routes.install()  # in every process that runs tests (tests/_routes.py)
    if not _is_worker(config):
        config.pluginmanager.register(_Recorder(config), "shard-recorder")


def pytest_sessionfinish(session):
    if _is_worker(session.config):  # an xdist worker hands what it saw to the controller
        session.config.workeroutput["routes"] = {
            "known": sorted(_routes.KNOWN),
            "requested": sorted(_routes.REQUESTED),
            "answered": sorted(_routes.ANSWERED),
        }


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

    def pytest_testnodedown(self, node, error):
        seen = getattr(node, "workeroutput", {}).get("routes")
        if seen:
            _routes.KNOWN.update(seen["known"])
            _routes.REQUESTED.update(seen["requested"])
            _routes.ANSWERED.update(seen["answered"])

    def _untested_routes(self) -> list[str]:
        return _routes.untested(_routes.KNOWN, _routes.REQUESTED, _routes.ANSWERED)

    def pytest_terminal_summary(self, terminalreporter):
        if self.config.getoption("--route-coverage"):
            missing = self._untested_routes()
            for line in missing:
                terminalreporter.write_line(line, red=True)
            if not missing:
                terminalreporter.write_line(
                    f"routes: {len(_routes.KNOWN)}, each gave a test its own answer"
                )

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session):
        config = self.config
        missing = self._untested_routes()
        if config.getoption("--route-coverage") and session.exitstatus == 0 and missing:
            session.exitstatus = 1
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
                        "routes": {
                            "known": sorted(_routes.KNOWN),
                            "requested": sorted(_routes.REQUESTED),
                            "answered": sorted(_routes.ANSWERED),
                            "allowed": sorted(_routes.ALLOWED),
                        },
                    }
                )
            )
