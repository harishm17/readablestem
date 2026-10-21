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


def test_missing_settings_are_named(env):
    del env["DAILY_SPEND_CAP_USD"]
    result = run(env)
    assert result.returncode != 0
    assert "DAILY_SPEND_CAP_USD is not set" in result.stderr


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


def test_service_flags_and_environment(env):
    assert run(env).returncode == 0
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    argv = deploy["argv"]
    for expected in (
        "--min-instances=1",
        "--max-instances=2",
        "--no-cpu-throttling",
        "--concurrency=20",
        "--memory=1Gi",
        "--timeout=300",
        "--port=8080",
        "--set-cloudsql-instances=proj:us-central1:readablestem-db",
    ):
        assert expected in argv
    secrets = next(a for a in argv if a.startswith("--set-secrets="))
    for name in ("OPENAI_API_KEY", "SESSION_SECRET", "GOOGLE_CLIENT_SECRET", "CRON_TOKEN"):
        assert f"{name}={name.lower().replace('_', '-')}:latest" in secrets
    assert "DATABASE_URL=database-url:latest" in secrets
    env_file = deploy["env_file"]
    assert "ENV: 'prod'" in env_file
    assert "ADMIN_EMAILS: 'a@example.org,b@example.org'" in env_file
    assert "SOURCE_URL: 'https://github.com/o''brien/readablestem'" in env_file
    assert OPENAI_KEY not in env_file and GOOGLE_SECRET not in env_file

    (migrate,) = [c for c in calls(env) if c["argv"][2:5] == ["run", "jobs", "deploy"]]
    assert "--args=db,upgrade" in migrate["argv"]
    (cron,) = [c for c in calls(env) if c["argv"][2:5] == ["scheduler", "jobs", "create"]]
    assert (
        "--uri=https://readablestem-abc123-uc.a.run.app/internal/cron/aggregate-and-purge"
        in (cron["argv"])
    )
    assert f"--headers=X-Cron-Token={CRON_TOKEN}" in cron["argv"]


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


def test_an_existing_instance_is_brought_down_to_the_backup_setting(env):
    state = Path(env["FAKE_GCLOUD_DIR"])
    (state / "created.txt").write_text("sql instances\nsql databases\n")  # made with 7
    env["SQL_RETAINED_BACKUPS"] = "2"
    result = run(env, "database")
    assert result.returncode == 0, result.stderr
    (patch,) = [c for c in calls(env) if c["argv"][2:5] == ["sql", "instances", "patch"]]
    assert "--retained-backups-count=2" in patch["argv"]
    assert "7 -> 2" in result.stdout

    env["SQL_RETAINED_BACKUPS"] = "0"
    assert "SQL_RETAINED_BACKUPS must be" in run(env, "database").stderr


def test_external_database_skips_cloud_sql(env):
    env["DB_MODE"] = "external"
    env["EXTERNAL_DATABASE_URL"] = "postgresql+psycopg://u:pw@db.example.org/a11y"
    result = run(env)
    assert result.returncode == 0, result.stderr
    assert not [c for c in commands(env) if " sql " in f" {c} "]
    assert "pw@db.example.org" not in result.stdout + result.stderr


def test_a_single_step_can_be_rerun(env):
    result = run(env, "apis")
    assert result.returncode == 0
    assert [c["argv"][2:4] for c in calls(env)] == [["services", "enable"]]
    assert run(env, "nonsense").returncode != 0


def test_tracing_is_opt_in_and_its_key_is_a_secret(env):
    env.update(
        TRACING_ENABLED="true",
        LANGFUSE_PUBLIC_KEY="pk-lf-public",
        LANGFUSE_SECRET_KEY="sk-lf-should-never-print",
    )
    result = run(env)
    assert result.returncode == 0, result.stderr
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    assert "LANGFUSE_SECRET_KEY=langfuse-secret-key:latest" in " ".join(deploy["argv"])
    assert "TRACING_ENABLED: 'true'" in deploy["env_file"]
    assert "sk-lf-should-never-print" not in deploy["env_file"] + result.stdout + result.stderr

    del env["LANGFUSE_PUBLIC_KEY"]
    assert "LANGFUSE_PUBLIC_KEY is not set" in run(env).stderr


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


def test_open_signup_settings_reach_the_service_only_when_set(env):
    """deploy.env.example sets them (recommended production values); a deploy that does not
    leaves the app's own defaults, which are invitation only."""
    first = run(env, "service")
    assert first.returncode == 0, first.stderr
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    assert "SIGNUP_MODE" not in deploy["env_file"] and "OPEN_TIER" not in deploy["env_file"]

    env.update(
        SIGNUP_MODE="open",
        OPEN_TIER_DAILY_PAGE_QUOTA="20",
        OPEN_TIER_DAILY_REREADS="30",
        MAX_NEW_ACCOUNTS_PER_DAY="100",
        OPEN_TIER_DAILY_SPEND_CAP_USD="4",
    )
    result = run(env, "service")
    assert result.returncode == 0, result.stderr
    deploys = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    for line in (
        "SIGNUP_MODE: 'open'",
        "OPEN_TIER_DAILY_PAGE_QUOTA: '20'",
        "OPEN_TIER_DAILY_REREADS: '30'",
        "MAX_NEW_ACCOUNTS_PER_DAY: '100'",
        "OPEN_TIER_DAILY_SPEND_CAP_USD: '4'",
    ):
        assert line in deploys[-1]["env_file"]


def test_the_example_file_carries_the_recommended_open_signup_values():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "deploy" / "deploy.env.example").read_text()
    for line in (
        "SIGNUP_MODE=open",
        "OPEN_TIER_DAILY_PAGE_QUOTA=20",
        "OPEN_TIER_DAILY_REREADS=30",
        "MAX_NEW_ACCOUNTS_PER_DAY=100",
        "OPEN_TIER_DAILY_SPEND_CAP_USD=4",
    ):
        assert line in text.splitlines()
    railway = (Path(__file__).resolve().parents[1] / "docs" / "deploy-railway.md").read_text()
    for name in (
        "SIGNUP_MODE",
        "OPEN_TIER_DAILY_PAGE_QUOTA",
        "OPEN_TIER_DAILY_REREADS",
        "MAX_NEW_ACCOUNTS_PER_DAY",
        "OPEN_TIER_DAILY_SPEND_CAP_USD",
    ):
        assert f"| `{name}` |" in railway


def policies(env: dict[str, str]) -> dict[str, dict]:
    return {c["policy"]["displayName"]: c["policy"] for c in calls(env) if "policy" in c}


def test_the_hourly_purge_has_alerts_of_its_own(env):
    """/healthz stays 200 when the scheduler stops reaching the app (a 403 after a
    cron-token rotation, a paused job), so the retention promise needs its own alerts."""
    assert run(env, "monitoring").returncode == 0
    metrics = {
        c["argv"][5]: next(a for a in c["argv"] if a.startswith("--log-filter="))
        for c in calls(env)
        if c["argv"][2:5] == ["logging", "metrics", "create"]
    }
    assert set(metrics) == {
        "readablestem-cron-done",
        "readablestem-cron-problems",
        "readablestem-scheduler-errors",
    }
    assert 'jsonPayload.message="cron_done"' in metrics["readablestem-cron-done"]
    for event in ("cron_refused", "aggregate_failed", "purge_backstop_deleted"):
        assert event in metrics["readablestem-cron-problems"]
    assert 'resource.labels.job_id="readablestem-cron"' in metrics["readablestem-scheduler-errors"]

    found = policies(env)
    assert set(found) == {
        "readablestem readyz failing",
        "readablestem cron not running",
        "readablestem cron or purge problem",
    }
    (absent,) = found["readablestem cron not running"]["conditions"]
    assert absent["conditionAbsent"]["duration"] == "7200s"
    # One series across revisions, so a deploy does not look like the cron stopping.
    assert absent["conditionAbsent"]["aggregations"][0]["crossSeriesReducer"] == "REDUCE_SUM"
    channels = {tuple(policy["notificationChannels"]) for policy in found.values()}
    assert len(channels) == 1 and all(channels)

    rerun = len(calls(env))
    assert run(env, "monitoring").returncode == 0
    again = commands(env)[rerun:]
    assert not [c for c in again if " create " in f" {c} "]
    assert len([c for c in again if "logging metrics update" in c]) == 3


def test_the_budget_alert_is_not_skipped_silently(env):
    del env["BILLING_ACCOUNT_ID"]
    result = run(env, "budget")
    assert result.returncode != 0 and "SKIP_BUDGET=true" in result.stderr
    env["SKIP_BUDGET"] = "true"
    result = run(env, "budget")
    assert result.returncode == 0 and "SKIP_BUDGET=true: no budget alert" in result.stdout
    assert not (Path(env["FAKE_GCLOUD_DIR"]) / "calls.jsonl").exists()  # nothing created


def test_the_default_budget_is_above_the_fixed_monthly_cost(env):
    assert run(env, "budget").returncode == 0
    (create,) = [c for c in calls(env) if "budgets" in c["argv"] and "create" in c["argv"]]
    amount = next(a for a in create["argv"] if a.startswith("--budget-amount="))
    assert float(amount.removeprefix("--budget-amount=").removesuffix("USD")) >= 60


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


def test_an_unknown_model_provider_is_refused(env):
    env["MODEL_PROVIDER"] = "gemini"
    result = run(env)
    assert result.returncode != 0 and "MODEL_PROVIDER must be openai or anthropic" in result.stderr


def test_content_features_reach_the_service_and_a_redeploy_keeps_them(env):
    env.update(
        FEATURE_LONG_DESCRIPTIONS="true",
        FEATURE_NOTATION_MEMORY="true",
        FEATURE_TABLES="false",
        FEATURE_COLOUR="false",
        FEEDBACK_URL="https://forms.example.org/a11y",
        MAX_USD_PER_PAGE="0.9",
        DECISIONS_CANARY="false",
    )
    assert run(env, "service").returncode == 0
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    text = deploy["env_file"]
    for line in (
        "FEATURE_LONG_DESCRIPTIONS: 'true'",
        "FEATURE_NOTATION_MEMORY: 'true'",
        "FEATURE_TABLES: 'false'",
        "FEATURE_COLOUR: 'false'",
        "FEEDBACK_URL: 'https://forms.example.org/a11y'",
        "MAX_USD_PER_PAGE: '0.9'",
        "DECISIONS_CANARY: 'false'",
    ):
        assert line in text, line


def test_unset_features_are_left_to_the_apps_defaults(env):
    assert run(env, "service").returncode == 0
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    assert "FEATURE_" not in deploy["env_file"] and "DECISIONS_" not in deploy["env_file"]


def test_a_feature_that_is_not_true_or_false_is_refused(env):
    env["FEATURE_TABLES"] = "yes"
    result = run(env, "service")
    assert result.returncode != 0 and "FEATURE_TABLES must be true or false" in result.stderr


def test_with_claude_the_openai_second_check_is_written_out_as_off(env):
    env.update(MODEL_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-never-printed")
    env.pop("OPENAI_API_KEY")
    assert run(env, "service").returncode == 0
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    for name in ("DECISIONS_FIGURE_VERIFY", "DECISIONS_EQUATION_SHADOW", "DECISIONS_CANARY"):
        assert f"{name}: 'false'" in deploy["env_file"]


def test_cloud_run_sizing_comes_from_settings_with_measured_defaults(env):
    env.update(CPU="2", MEMORY="2Gi", MAX_INSTANCES="3", CONCURRENCY="10", REQUEST_TIMEOUT="120")
    assert run(env, "service").returncode == 0
    (deploy,) = [c for c in calls(env) if c["argv"][2:4] == ["run", "deploy"]]
    for flag in (
        "--cpu=2",
        "--memory=2Gi",
        "--min-instances=1",
        "--max-instances=3",
        "--concurrency=10",
        "--timeout=120",
    ):
        assert flag in deploy["argv"]
    env["MIN_INSTANCES"] = "0"
    assert "MIN_INSTANCES must be at least 1" in run(env, "service").stderr


def test_a_claude_page_cap_that_cannot_hold_a_call_stops_the_deploy_before_anything(env):
    env.update(MODEL_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-x", MAX_USD_PER_PAGE="0.05")
    result = run(env, "apis")
    assert result.returncode != 0 and "spend limits do not fit" in result.stderr
    assert "MAX_USD_PER_PAGE $0.05" in result.stderr
    assert not (Path(env["FAKE_GCLOUD_DIR"]) / "calls.jsonl").exists()  # nothing was called
    env["MAX_USD_PER_PAGE"] = "1.0"
    assert run(env, "apis").returncode == 0
