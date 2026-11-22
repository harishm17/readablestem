"""The corpus refresh pays nothing without a stated limit, and never more than a refresh may."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("asked", "said"),
    [
        (["--live"], "--live needs --max-usd"),
        (["--live", "--max-usd", "1.01"], "at most 1.00"),
        (["--live", "--max-usd", "0"], "above 0"),
    ],
)
def test_a_refresh_that_could_pay_is_refused_before_anything_runs(
    asked: list[str], said: str
) -> None:
    done = subprocess.run(
        [sys.executable, "scripts/corpus_refresh.py", *asked],
        cwd=REPO,
        env=os.environ | {"ANTHROPIC_API_KEY": "sk-ant-never-used"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert said in done.stderr
    assert done.stdout == ""  # no test ran, so nothing was asked


def test_the_refresh_can_start_its_single_process_test_run() -> None:
    # The refresh runs the workflow tests with xdist off (one process, one answers file).
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:xdist", "--collect-only", "-q", __file__],
        cwd=REPO,
        env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-2000:]
