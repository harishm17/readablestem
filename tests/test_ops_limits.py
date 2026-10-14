"""Spend limits that fit the model provider: defaults, production refusal, launch-check."""

from __future__ import annotations

import pytest

from a11y_stem.config import ConfigError, Settings, with_provider
from a11y_stem.ops import launch, limits
from a11y_stem.web.worker import job_caps

PROD = {
    "env": "prod",
    "daily_spend_cap_usd": 10,
    "per_user_daily_page_quota": 90,
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


def test_claude_gets_its_own_job_caps_unless_the_environment_sets_them():
    assert (settings().max_usd_per_job, settings().max_usd_per_page) == (2.0, 0.30)
    claude = settings(model_provider="anthropic")
    assert (claude.max_usd_per_job, claude.max_usd_per_page) == (4.0, 2.0)
    set_by_hand = settings(model_provider="anthropic", max_usd_per_job=1.5, max_usd_per_page=0.9)
    assert (set_by_hand.max_usd_per_job, set_by_hand.max_usd_per_page) == (1.5, 0.9)


def test_environment_variable_sets_a_cap_and_a_blank_one_does_not(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MAX_USD_PER_JOB", "")
    monkeypatch.setenv("MAX_USD_PER_PAGE", "1.25")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.max_usd_per_job, s.max_usd_per_page) == (4.0, 1.25)


def test_switching_provider_brings_that_providers_defaults():
    base = settings(max_usd_per_page=0.5)
    claude = with_provider(base, "anthropic")
    assert claude.max_usd_per_job == 4.0  # a default follows the provider
    assert claude.max_usd_per_page == 0.5  # a set value is kept
    assert with_provider(claude, "openai").max_usd_per_job == 2.0


def test_a_short_claude_document_can_hold_four_calls_under_the_default_page_cap():
    claude = settings(model_provider="anthropic")
    usd, _ = job_caps(claude, 1)
    assert usd >= limits.in_flight_usd(claude)
    # ...which OpenAI's page cap would not allow for Claude's reservations:
    assert 0.30 < limits.largest_reservation_usd(claude)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_default_caps_and_the_recommended_beta_numbers_raise_no_finding(provider):
    s = settings(model_provider=provider, daily_spend_cap_usd=10, per_user_daily_page_quota=90)
    assert limits.spend_findings(s) == []


def test_a_normal_30_page_claude_document_never_reaches_the_default_job_cap():
    s = settings(model_provider="anthropic")
    usd, _ = job_caps(s, 30)
    assert usd == s.max_usd_per_job
    # 30 dense pages at the planning rate, plus every concurrent step held at its worst case
    assert usd > 30 * limits.PLANNING_USD_PER_PAGE + limits.in_flight_usd(s)
    # and a runaway is still stopped within a small multiple of it
    assert usd < limits.RUNAWAY_FACTOR * 30 * limits.PLANNING_USD_PER_PAGE


def test_openai_page_cap_on_claude_is_refused_in_production():
    s = settings(**{**PROD, "model_provider": "anthropic", "max_usd_per_page": 0.30})
    with pytest.raises(ConfigError, match=r"spend limits: MAX_USD_PER_PAGE \$0.30"):
        s.check_production()
    settings(**{**PROD, "model_provider": "anthropic"}).check_production()


def test_a_daily_cap_below_the_calls_held_at_once_is_refused_in_production():
    s = settings(**{**PROD, "model_provider": "anthropic", "daily_spend_cap_usd": 1.0})
    with pytest.raises(ConfigError, match="DAILY_SPEND_CAP_USD"):
        s.check_production()
    # the same cap is fine on OpenAI, whose reservations are a tenth of the size
    settings(**{**PROD, "daily_spend_cap_usd": 1.0}).check_production()


def test_limits_are_not_enforced_outside_production():
    settings(model_provider="anthropic", max_usd_per_page=0.01).check_production()


def test_tables_and_long_descriptions_raise_the_worst_case_and_a_warning():
    plain = settings(model_provider="anthropic")
    heavy = settings(model_provider="anthropic", feature_tables=True)
    assert limits.largest_reservation_usd(heavy) > 2 * limits.largest_reservation_usd(plain)
    texts = [f.text for f in limits.spend_findings(heavy)]
    assert any("MAX_USD_PER_PAGE" in t and "held at once" in t for t in texts)


def test_warnings_for_caps_far_from_a_normal_document():
    s = settings(
        model_provider="anthropic",
        max_usd_per_job=50,
        per_user_daily_page_quota=10,
        daily_spend_cap_usd=3,
    )
    kinds = {f.severity for f in limits.spend_findings(s)}
    assert kinds == {"warning"}
    text = " ".join(f.text for f in limits.spend_findings(s))
    assert "runaway" in text and "PER_USER_DAILY_PAGE_QUOTA 10" in text
    assert "DAILY_SPEND_CAP_USD $3.00 is below one job's cap" in text


def test_launch_check_lists_a_refusal_and_the_warnings(capsys):
    refused = settings(**{**PROD, "model_provider": "anthropic", "max_usd_per_page": 0.30})
    items = launch.launch_blockers(settings=refused)
    assert sum("MAX_USD_PER_PAGE $0.30" in i for i in items) == 1  # named once
    assert any(i.startswith("settings: production start refused") for i in items)
    tight = settings(**{**PROD, "model_provider": "anthropic", "per_user_daily_page_quota": 5})
    assert any(
        i.startswith("spend limits: warning: PER_USER_DAILY_PAGE_QUOTA 5")
        for i in launch.launch_blockers(settings=tight)
    )
    good = settings(**{**PROD, "model_provider": "anthropic"})
    assert not any("spend limits" in i for i in launch.launch_blockers(settings=good))
