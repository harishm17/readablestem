"""deploy/deploy.sh against a fake gcloud: required settings, idempotence, no secret echo."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "deploy.sh"
FAKE = ROOT / "tests" / "fixtures" / "ops" / "fake_gcloud.py"
CRON_TOKEN = "fake-cron-token-0123456789abcdef"

OPENAI_KEY = "sk-test-0penai-should-never-print"
GOOGLE_SECRET = "GOCSPX-should-never-print"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("openssl") is None,
    reason="needs bash and openssl",
)


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "gcloud"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_GCLOUD_DIR": str(state),
        "PROJECT_ID": "readablestem-test",
        "REGION": "us-central1",
        "BASE_URL": "https://a11y.example.org",
        "DAILY_SPEND_CAP_USD": "5",
        "PER_USER_DAILY_PAGE_QUOTA": "60",
        "GOOGLE_CLIENT_ID": "1234.apps.googleusercontent.com",
        "CONTACT_EMAIL": "contact@example.org",
        "ADMIN_EMAILS": "a@example.org,b@example.org",
        "ALERT_EMAIL": "alerts@example.org",
        "BILLING_ACCOUNT_ID": "000000-000000-000000",
        "IMAGE_TAG": "test",
        "OPENAI_API_KEY": OPENAI_KEY,
        "GOOGLE_CLIENT_SECRET": GOOGLE_SECRET,
        "SOURCE_URL": "https://github.com/o'brien/readablestem",  # quote must survive YAML
    }


def run(env: dict[str, str], *steps: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *steps],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )


def calls(env: dict[str, str]) -> list[dict]:
    path = Path(env["FAKE_GCLOUD_DIR"]) / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def commands(env: dict[str, str]) -> list[str]:
    return [" ".join(c["argv"]) for c in calls(env)]


def test_http_base_url_is_refused(env):
    env["BASE_URL"] = "http://a11y.example.org"
    result = run(env)
    assert result.returncode != 0 and "https://" in result.stderr


def test_full_run_then_rerun_is_idempotent(env):
    first = run(env)
    assert first.returncode == 0, first.stderr
    first_calls = commands(env)
    adds = [c for c in calls(env) if c["argv"][2:5] == ["secrets", "versions", "add"]]
    assert len(adds) == 5  # four app secrets plus the database URL
    assert sum("create" in c.split() for c in first_calls) >= 10

    second = run(env)
    assert second.returncode == 0, second.stderr
    rerun = commands(env)[len(first_calls) :]
    created = [c for c in rerun if " create " in f" {c} " or "versions add" in c]
    assert created == []
    assert any("scheduler jobs update http" in c for c in rerun)

    for result in (first, second):
        shown = result.stdout + result.stderr
        for secret in (OPENAI_KEY, GOOGLE_SECRET, CRON_TOKEN, "postgresql+psycopg://"):
            assert secret not in shown


def test_cloud_sql_backups_do_not_outlive_retention_by_a_week(env):
    """Every daily backup holds the documents present when it ran; Cloud SQL keeps 7 by
    default, so a purged upload would survive for a week. Keep one, and no PITR logs."""
    assert run(env, "database").returncode == 0
    (create,) = [c for c in calls(env) if c["argv"][2:5] == ["sql", "instances", "create"]]
    assert "--retained-backups-count=1" in create["argv"]
    assert not any("point-in-time" in a for a in create["argv"])
    assert not any("transaction-log" in a for a in create["argv"])

    rerun = len(calls(env))
    assert run(env, "database").returncode == 0  # already 1: nothing to patch
    assert not [c for c in commands(env)[rerun:] if "sql instances patch" in c]


MS_TENANT = "11111111-1111-4111-8111-111111111111"


def test_microsoft_sign_in_is_opt_in_and_its_secret_is_a_secret(env):
    first = run(env)
    assert first.returncode == 0, first.stderr
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    assert "MS_CLIENT" not in deploy["env_file"] + " ".join(deploy["argv"])

    env.update(
        MS_CLIENT_ID="00000000-aaaa-4bbb-8ccc-000000000001",
        MS_ALLOWED_TENANTS=f"{MS_TENANT},{MS_TENANT.replace('1', '2')}",
        MS_CLIENT_SECRET="ms-secret-should-never-print",
    )
    result = run(env, "secrets", "service")
    assert result.returncode == 0, result.stderr
    deploys = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    argv, env_file = " ".join(deploys[-1]["argv"]), deploys[-1]["env_file"]
    assert "MS_CLIENT_SECRET=ms-client-secret:latest" in argv
    assert "MS_CLIENT_ID: '00000000-aaaa-4bbb-8ccc-000000000001'" in env_file
    assert f"MS_ALLOWED_TENANTS: '{MS_TENANT},22222222-2222-4222-8222-222222222222'" in env_file
    adds = [c for c in calls(env) if c["argv"][2:5] == ["secrets", "versions", "add"]]
    assert sum("ms-client-secret" in c["argv"] for c in adds) == 1
    assert "ms-secret-should-never-print" not in env_file + result.stdout + result.stderr

    del env["MS_ALLOWED_TENANTS"]
    assert "MS_ALLOWED_TENANTS is not set" in run(env).stderr
    env["MS_ALLOWED_TENANTS"] = "organizations"
    assert "directory (tenant) ids" in run(env).stderr


def policies(env: dict[str, str]) -> dict[str, dict]:
    return {c["policy"]["displayName"]: c["policy"] for c in calls(env) if "policy" in c}


def test_the_budget_alert_is_not_skipped_silently(env):
    del env["BILLING_ACCOUNT_ID"]
    result = run(env, "budget")
    assert result.returncode != 0 and "SKIP_BUDGET=true" in result.stderr
    env["SKIP_BUDGET"] = "true"
    result = run(env, "budget")
    assert result.returncode == 0 and "SKIP_BUDGET=true: no budget alert" in result.stdout
    assert not (Path(env["FAKE_GCLOUD_DIR"]) / "calls.jsonl").exists()  # nothing created


def test_the_anthropic_provider_deploys_its_own_key_and_not_the_openai_one(env):
    key = "sk-ant-should-never-print"
    env.update(
        MODEL_PROVIDER="anthropic",
        ANTHROPIC_API_KEY=key,
        ANTHROPIC_WORKSPACE_ID="wrkspc_test1",
        ANTHROPIC_CHEAP_MODEL="claude-sonnet-5-5",
    )
    del env["OPENAI_API_KEY"]
    result = run(env)
    assert result.returncode == 0, result.stderr
    assert key not in result.stdout + result.stderr
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    secrets = next(a for a in deploy["argv"] if a.startswith("--set-secrets="))
    assert "ANTHROPIC_API_KEY=anthropic-api-key:latest" in secrets
    assert "OPENAI_API_KEY" not in secrets
    env_file = deploy["env_file"]
    assert "MODEL_PROVIDER: 'anthropic'" in env_file
    assert "ANTHROPIC_WORKSPACE_ID: 'wrkspc_test1'" in env_file
    assert "ANTHROPIC_CHEAP_MODEL: 'claude-sonnet-5-5'" in env_file
    assert key not in env_file
    created = [c["argv"][4] for c in calls(env) if c["argv"][2:4] == ["secrets", "create"]]
    assert "anthropic-api-key" in created and "openai-api-key" not in created


def test_a_claude_page_cap_that_cannot_hold_a_call_stops_the_deploy_before_anything(env):
    env.update(MODEL_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-x", MAX_USD_PER_PAGE="0.05")
    result = run(env, "apis")
    assert result.returncode != 0 and "spend limits do not fit" in result.stderr
    assert "MAX_USD_PER_PAGE $0.05" in result.stderr
    assert not (Path(env["FAKE_GCLOUD_DIR"]) / "calls.jsonl").exists()  # nothing was called
    env["MAX_USD_PER_PAGE"] = "1.0"
    assert run(env, "apis").returncode == 0
