"""Spend limits that fit the model provider: defaults, production refusal, launch-check."""

from __future__ import annotations

import pytest

from readablestem.config import ConfigError, Settings
from readablestem.ops import launch, limits
from readablestem.web.worker import job_caps

PROD = {
    "env": "prod",
    "daily_spend_cap_usd": 10,
    "per_user_daily_page_quota": 300,
    "session_secret": "x" * 40,
    "google_client_id": "id",
    "google_client_secret": "secret",
    "cron_token": "t" * 40,
    "contact_email": "a@example.org",
    "base_url": "https://example.org",
    "database_url": "postgresql+psycopg://u:p@h/db",
    "openai_api_key": "sk-o",
    "anthropic_api_key": "sk-a",
}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_affordable_concurrency_follows_the_money_that_is_left():
    claude = settings(model_provider="anthropic")
    largest = limits.largest_reservation_usd(claude)
    assert limits.affordable_concurrency(claude, budget_usd=None) == 8  # nothing to hold it to
    assert limits.affordable_concurrency(claude, budget_usd=10.0, daily_left_usd=None) == 8
    assert limits.affordable_concurrency(claude, budget_usd=10.0, daily_left_usd=3 * largest) == 3
    assert limits.affordable_concurrency(claude, budget_usd=2.5 * largest) == 2
    assert limits.affordable_concurrency(claude, budget_usd=0.01) == 1  # never fewer than one
    assert limits.affordable_concurrency(claude, budget_usd=0.0) == 1
    four = settings(model_provider="anthropic", model_concurrency=4)
    assert limits.affordable_concurrency(four, budget_usd=10.0) == 4


def test_a_normal_full_size_claude_document_never_reaches_the_default_job_cap():
    s = settings(model_provider="anthropic")
    assert s.max_pages_per_job == 100
    pages = s.max_pages_per_job
    usd, _ = job_caps(s, pages)
    assert usd == s.max_usd_per_job
    # 100 dense pages at the planning rate, plus every concurrent step held at its worst case
    assert usd > pages * limits.PLANNING_USD_PER_PAGE + limits.in_flight_usd(s)
    # and a runaway is still stopped within a small multiple of it
    assert usd < limits.RUNAWAY_FACTOR * pages * limits.PLANNING_USD_PER_PAGE


def test_openai_page_cap_on_claude_is_refused_in_production():
    s = settings(**{**PROD, "model_provider": "anthropic", "max_usd_per_page": 0.10})
    with pytest.raises(ConfigError, match=r"spend limits: MAX_USD_PER_PAGE \$0.10"):
        s.check_production()
    settings(**{**PROD, "model_provider": "anthropic"}).check_production()


def test_a_daily_cap_below_the_calls_held_at_once_is_refused_in_production():
    s = settings(**{**PROD, "model_provider": "anthropic", "daily_spend_cap_usd": 0.3})
    with pytest.raises(ConfigError, match="DAILY_SPEND_CAP_USD"):
        s.check_production()
    # the same cap is fine on OpenAI, whose reservations are a tenth of the size
    settings(**{**PROD, "daily_spend_cap_usd": 1.0}).check_production()


def test_launch_check_lists_a_refusal_and_the_warnings(capsys):
    refused = settings(**{**PROD, "model_provider": "anthropic", "max_usd_per_page": 0.10})
    items = launch.launch_blockers(settings=refused)
    assert sum("MAX_USD_PER_PAGE $0.10" in i for i in items) == 1  # named once
    assert any(i.startswith("settings: production start refused") for i in items)
    tight = settings(**{**PROD, "model_provider": "anthropic", "per_user_daily_page_quota": 5})
    assert any(
        i.startswith("spend limits: warning: PER_USER_DAILY_PAGE_QUOTA 5")
        for i in launch.launch_blockers(settings=tight)
    )
    good = settings(**{**PROD, "model_provider": "anthropic"})
    assert not any("spend limits" in i for i in launch.launch_blockers(settings=good))
