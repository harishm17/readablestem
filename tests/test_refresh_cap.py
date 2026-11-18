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
