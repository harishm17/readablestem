"""railway.json (the web service) and deploy/railway.cron.json (the cron service) parse, point
at things that exist, and keep the two services apart (docs/deploy-railway.md).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from readablestem.cli.main import build_parser
from readablestem.web.routes.pages import router as pages_router

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "railway.json"
CRON = ROOT / "deploy" / "railway.cron.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def parses_as_cli(command: str) -> bool:
    """`readablestem <args>` is a command the argument parser accepts."""
    words = shlex.split(command)
    assert words[0] == "readablestem", command
    build_parser().parse_args(words[1:])  # exits (SystemExit) on an unknown command
    return True


@pytest.mark.parametrize("path", [WEB, CRON], ids=lambda p: p.name)
def test_builds_the_repository_dockerfile(path):
    build = load(path)["build"]
    assert build["builder"] == "DOCKERFILE"
    assert (ROOT / build["dockerfilePath"]).is_file()


def test_healthcheck_path_is_a_route_that_answers_get():
    path = load(WEB)["deploy"]["healthcheckPath"]
    get_routes = {
        route.path for route in pages_router.routes if "GET" in getattr(route, "methods", set())
    }
    assert path in get_routes
    # Railway's deploy check needs the database only: a worker or provider problem (/readyz)
    # must not stop a deploy that would otherwise go out.
    assert path == "/healthz"
    assert load(WEB)["deploy"]["healthcheckTimeout"] >= 60


def test_web_service_migrates_before_each_deploy_and_restarts_on_failure():
    deploy = load(WEB)["deploy"]
    commands = deploy["preDeployCommand"]
    assert commands == ["readablestem db upgrade"]
    assert all(parses_as_cli(command) for command in commands)
    assert deploy["restartPolicyType"] == "ON_FAILURE"
    assert "cronSchedule" not in deploy  # a cron schedule here would stop the web service


def test_web_service_leaves_the_start_command_to_the_dockerfile():
    # The image's CMD serves on 8080, which is the PORT the service sets.
    assert "startCommand" not in load(WEB)["deploy"]
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert '"--port", "8080"' in dockerfile


def test_cron_service_runs_the_cli_cron_on_a_schedule_and_does_not_migrate():
    deploy = load(CRON)["deploy"]
    assert deploy["startCommand"] == "readablestem ops cron"
    assert parses_as_cli(deploy["startCommand"])
    assert "preDeployCommand" not in deploy  # two services migrating at once would race
    assert "healthcheckPath" not in deploy
    fields = deploy["cronSchedule"].split()
    assert len(fields) == 5
    assert fields[0].isdigit() and fields[1:] == ["*"] * 4  # hourly, at a fixed minute
    assert deploy["restartPolicyType"] == "NEVER"


def test_the_two_services_use_different_config_files():
    assert WEB != CRON
    assert load(WEB) != load(CRON)
