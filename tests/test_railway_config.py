"""The Railway infrastructure file (.railway/railway.ts) names the right services and settings,
points at things that exist, keeps the web and cron services apart, and no legacy
railway.json is left to compete with it (docs/deploy-railway.md).

The file is TypeScript run by the Railway CLI, so these tests read it as text. They do not
reach Railway: `railway config plan` is the check against the live project.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from readablestem.cli.main import build_parser
from readablestem.web.routes.pages import router as pages_router

ROOT = Path(__file__).resolve().parents[1]
IAC = ROOT / ".railway" / "railway.ts"


def service_block(name: str) -> str:
    """The text of `service("<name>", { ... });` in railway.ts."""
    text = IAC.read_text()
    match = re.search(rf'service\("{name}", \{{\n(.*?)\n  \}}\);', text, re.DOTALL)
    assert match, f"no service({name!r}) in {IAC}"
    return match.group(1)


def setting(block: str, key: str) -> str | None:
    """The value of a top-level `key: <value>,` line of a service block, unquoted."""
    match = re.search(rf"^    {key}: (.+),$", block, re.MULTILINE)
    if not match:
        return None
    value = match.group(1)
    return value[1:-1] if value[:1] == '"' and value[-1:] == '"' else value


def parses_as_cli(command: str) -> bool:
    """`readablestem <args>` is a command the argument parser accepts."""
    words = shlex.split(command)
    assert words[0] == "readablestem", command
    build_parser().parse_args(words[1:])  # exits (SystemExit) on an unknown command
    return True


def test_the_project_has_the_four_services_and_no_literal_secrets():
    text = IAC.read_text()
    for name in ("web", "cron", "backup"):
        service_block(name)
    assert 'postgres("Postgres"' in text
    assert 'project("readablestem"' in text
    # Production's variables hold secrets: every one stays out of the repository.
    assert "preserve()" in text
    assert not re.search(r'(KEY|SECRET|TOKEN|PASSWORD|URL)\w*: "', text)


def test_no_legacy_config_files_compete_with_the_iac_file():
    # A service managed by a repo config file blocks `railway config plan` for it, and
    # Railway stops reading these files on 2026-12-01.
    assert not (ROOT / "railway.json").exists()
    assert not (ROOT / "railway.toml").exists()
    assert not list((ROOT / "deploy").glob("railway*.json"))


def test_the_repository_dockerfile_exists_and_serves_the_web_port():
    # No builder is set in railway.ts: Railway uses the Dockerfile at the repository root.
    assert (ROOT / "Dockerfile").is_file()
    assert '"--port", "8080"' in (ROOT / "Dockerfile").read_text()
    for name in ("web", "cron"):
        assert "dockerfilePath" not in service_block(name)


def test_healthcheck_path_is_a_route_that_answers_get():
    path = setting(service_block("web"), "healthcheck")
    get_routes = {
        route.path for route in pages_router.routes if "GET" in getattr(route, "methods", set())
    }
    assert path in get_routes
    # Railway's deploy check needs the database only: a worker or provider problem (/readyz)
    # must not stop a deploy that would otherwise go out.
    assert path == "/healthz"
    assert int(setting(service_block("web"), "healthcheckTimeout") or 0) >= 60


def test_web_service_migrates_before_each_deploy_and_is_not_a_cron():
    block = service_block("web")
    command = setting(block, "preDeploy")
    assert command == "readablestem db upgrade"
    assert parses_as_cli(command)
    assert "cronSchedule" not in block  # a cron schedule here would stop the web service
    assert setting(block, "replicas") == "{ iad: 1 }"  # rate limits and uploads are per process


def test_web_service_leaves_the_start_command_to_the_dockerfile():
    # The image's CMD serves on 8080, which is the PORT the service sets.
    assert setting(service_block("web"), "start") is None


def test_cron_service_runs_the_cli_cron_on_a_schedule_and_does_not_migrate():
    block = service_block("cron")
    start = setting(block, "start")
    assert start == "readablestem ops cron"
    assert parses_as_cli(start)
    assert "preDeploy" not in block  # two services migrating at once would race
    assert "healthcheck" not in block
    schedule = re.search(r'cronSchedule: "([^"]+)"', block)
    assert schedule
    fields = schedule.group(1).split()
    assert len(fields) == 5
    assert fields[0].isdigit() and fields[1:] == ["*"] * 4  # hourly, at a fixed minute
    assert 'restartPolicyType: "NEVER"' in block


def test_backup_service_dumps_daily_and_proves_the_restore():
    block = service_block("backup")
    assert 'image("postgres:18")' in block
    assert re.search(r'cronSchedule: "\d+ \d+ \* \* \*"', block)  # once a day
    for needed in ("pg_dump", "restore_check", "alembic_version", "backup_ok"):
        assert needed in block
    assert '"/backups": backupVolume' in block


@pytest.mark.parametrize("name", ["web", "cron"])
def test_services_keep_their_variables_out_of_the_file(name):
    block = service_block(name)
    assert "preserved(" in block
    assert not re.search(r'"[A-Za-z0-9+/=_-]{32,}"', block)
