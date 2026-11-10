"""deploy/backups.sh against a stub gcloud: it flags stale, missing and leftover backups."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "backups.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

STUB = """#!/bin/sh
case "$*" in
  *"sql instances describe"*"retainedBackups"*) echo "$STUB_RETAINED" ;;
  *"sql instances describe"*) echo "$STUB_ENABLED" ;;
  *"sql backups list"*"type=ON_DEMAND"*) printf '%s' "$STUB_MANUAL" ;;
  *"sql backups list"*"AUTOMATED"*) echo "$STUB_NEWEST" ;;
  *"sql backups create"*) echo "created" ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
"""


def stamp(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(STUB)
    gcloud.chmod(0o755)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PROJECT_ID": "p",
        "STUB_ENABLED": "True",
        "STUB_RETAINED": "1",
        "STUB_NEWEST": stamp(5),
        "STUB_MANUAL": "",
    }


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_healthy_instance_passes(env):
    result = run(env, "status")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "backups: ok" in result.stdout and "5 hours old" in result.stdout


def test_an_old_manual_backup_is_named_because_it_outlives_retention(env):
    env["STUB_MANUAL"] = f"1700000000000\t{stamp(24 * 10)}\n1700000000001\t{stamp(24)}\n"
    result = run(env, "status")
    assert result.returncode == 1
    assert "manual backup 1700000000000 is 10 days old: delete it" in result.stdout
    assert "gcloud sql backups delete 1700000000000" in result.stdout
    assert "manual backup 1700000000001 is 1 days old" in result.stdout
