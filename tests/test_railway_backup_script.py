"""The backup service's start command (`backupScript` in .railway/railway.ts): it is valid
bash, passes shellcheck, and a dump is kept only when every check of it passed.

The script runs here against stub `pg_dump` and `psql` programs, with /backups pointed at a
temporary directory. The stubs answer what the script asks (tables, schema version) from
environment variables, so each test breaks one thing and looks at the exit code, the
`backup_failed reason=...` line, what is left on the volume and whether the heartbeat was
written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_railway_config import backup_script

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

COMPLETE = "SELECT 1;\n--\n-- PostgreSQL database dump complete\n--\n\n\\unrestrict abc\n"
TRUNCATED = "CREATE TABLE a (id int);\nINSERT INTO a VALUES (1);\n"

PG_DUMP = """#!/bin/bash
cat "$DUMP_SRC"
exit "${PG_DUMP_EXIT:-0}"
"""

# Answers by the URL it is given: the scratch database has "restore_check" in it.
PSQL = """#!/bin/bash
args="$*"
scratch=live
[[ "$args" == *restore_check* ]] && scratch=restored
if [[ "$args" == *ON_ERROR_STOP* ]]; then
  cat > /dev/null
  [ "${RESTORE_FAIL:-0}" = 1 ] && exit 3
  touch "$STUB_LOG.restored"
  exit 0
fi
if [[ "$args" == *"DROP DATABASE"* && "${DROP_FAIL_LATE:-0}" = 1 && -e "$STUB_LOG.restored" ]]; then
  exit 5
fi
case "$args" in
  *pg_tables*)
    if [ "$scratch" = restored ]; then printf '%s\n' $RESTORED_TABLES
    else printf '%s\n' $LIVE_TABLES; fi ;;
  *version_num*)
    if [ "$scratch" = restored ]; then echo "$RESTORED_VERSION"; else echo "$LIVE_VERSION"; fi ;;
  *ops_heartbeat*)
    [ "${HEARTBEAT_FAIL:-0}" = 1 ] && exit 4
    echo "$args" >> "$STUB_LOG" ;;
esac
exit 0
"""


@pytest.fixture
def run_backup(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("pg_dump", PG_DUMP), ("psql", PSQL)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    volume = tmp_path / "backups"
    volume.mkdir()
    script = tmp_path / "backup.sh"
    script.write_text(backup_script().replace("/backups", str(volume)))

    def run(dump: str = COMPLETE, **env: str) -> subprocess.CompletedProcess[str]:
        source = tmp_path / "dump.sql"
        source.write_text(dump)
        environment = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "PGURL": "postgresql://u:p@db.internal:5432/railway?sslmode=disable",
            "DUMP_SRC": str(source),
            "LIVE_TABLES": "users jobs alembic_version",
            "RESTORED_TABLES": "users jobs alembic_version",
            "LIVE_VERSION": "e1a7c3b95d24",
            "RESTORED_VERSION": "e1a7c3b95d24",
            "STUB_LOG": str(tmp_path / "psql.log"),
        } | env
        return subprocess.run(
            ["bash", str(script)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    run.volume = volume  # type: ignore[attr-defined]
    run.heartbeat_log = tmp_path / "psql.log"  # type: ignore[attr-defined]
    return run


def test_the_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n"], input=backup_script(), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")
def test_the_script_passes_shellcheck(tmp_path):
    path = tmp_path / "backup.sh"
    path.write_text("#!/bin/bash\n" + backup_script())
    result = subprocess.run(
        ["shellcheck", "--shell=bash", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


def test_the_script_is_passed_to_bash_in_one_piece():
    text = (Path(__file__).resolve().parents[1] / ".railway" / "railway.ts").read_text()
    assert "${" not in backup_script() and "`" not in backup_script()
    assert 'start: "bash -c " + shellQuote(backupScript),' in text


def test_a_proved_dump_is_kept_and_the_heartbeat_written(run_backup):
    result = run_backup()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "backup_ok" in result.stdout and "tables=3 alembic=e1a7c3b95d24" in result.stdout
    dumps = list(run_backup.volume.glob("readablestem-*.sql.gz"))
    assert len(dumps) == 1 and not list(run_backup.volume.glob("*.part"))
    log = run_backup.heartbeat_log.read_text()
    assert "ops_heartbeat" in log and "'backup'" in log


@pytest.mark.parametrize(
    ("dump", "env", "reason"),
    [
        (TRUNCATED, {}, "dump_incomplete"),
        (COMPLETE, {"PG_DUMP_EXIT": "1"}, "dump"),  # pipefail: the dump failed after output
        (COMPLETE, {"RESTORE_FAIL": "1"}, "restore"),
        (COMPLETE, {"RESTORED_TABLES": "users alembic_version"}, "missing_tables"),
        (COMPLETE, {"RESTORED_VERSION": "older"}, "alembic_mismatch"),
        (COMPLETE, {"LIVE_TABLES": ""}, "no_tables"),
    ],
)
def test_a_dump_that_fails_a_check_is_not_kept(run_backup, dump, env, reason):
    result = run_backup(dump, **env)
    assert result.returncode == 1
    assert f"backup_failed reason={reason}" in result.stdout
    assert "backup_ok" not in result.stdout
    assert list(run_backup.volume.iterdir()) == []  # neither the dump nor a .part file
    assert not run_backup.heartbeat_log.exists()  # no heartbeat for a failed backup


def test_a_failed_heartbeat_fails_the_run_but_keeps_the_proved_dump(run_backup):
    result = run_backup(HEARTBEAT_FAIL="1")
    assert result.returncode == 1
    assert "backup_failed reason=heartbeat" in result.stdout
    assert len(list(run_backup.volume.glob("readablestem-*.sql.gz"))) == 1


def test_a_failed_cleanup_of_the_scratch_database_does_not_lose_the_proved_dump(run_backup):
    result = run_backup(DROP_FAIL_LATE="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "backup_warning reason=restore_cleanup" in result.stdout
    assert len(list(run_backup.volume.glob("readablestem-*.sql.gz"))) == 1
    assert "'backup'" in run_backup.heartbeat_log.read_text()
