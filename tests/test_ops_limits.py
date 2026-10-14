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
    assert (claude.max_usd_per_job, claude.max_usd_per_page) == (3.0, 1.0)
    set_by_hand = settings(model_provider="anthropic", max_usd_per_job=1.5, max_usd_per_page=0.9)
    assert (set_by_hand.max_usd_per_job, set_by_hand.max_usd_per_page) == (1.5, 0.9)


def test_environment_variable_sets_a_cap_and_a_blank_one_does_not(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MAX_USD_PER_JOB", "")
    monkeypatch.setenv("MAX_USD_PER_PAGE", "1.25")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.max_usd_per_job, s.max_usd_per_page) == (3.0, 1.25)


def test_switching_provider_brings_that_providers_defaults():
    base = settings(max_usd_per_page=0.5)
    claude = with_provider(base, "anthropic")
    assert claude.max_usd_per_page == 0.5  # a set value is kept
    assert claude.max_usd_per_job == 3.0
    assert with_provider(claude, "openai").max_usd_per_job == 2.0


def test_a_short_claude_document_runs_as_many_steps_as_its_cap_can_hold():
    """Eight steps at once is the default; a one-page job on Claude has $1.00, and a call
    holds up to $0.14, so it runs seven: a refused reservation would stop its model work."""
    claude = settings(model_provider="anthropic")
    assert claude.model_concurrency == 8
    usd, _ = job_caps(claude, 1)
    largest = limits.largest_reservation_usd(claude)
    held = limits.affordable_concurrency(claude, budget_usd=usd)
    assert held == int(usd // largest) == 7 and held * largest <= usd
    # OpenAI's $0.30 page cap would have been above one call but under four:
    assert largest < 0.30 < usd
    # a 30-page document has room for all eight, and so does a short one on OpenAI
    assert limits.affordable_concurrency(claude, budget_usd=job_caps(claude, 30)[0]) == 8
    openai = settings()
    assert limits.affordable_concurrency(openai, budget_usd=job_caps(openai, 1)[0]) == 8


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


def test_the_concurrency_is_a_setting_with_a_range():
    assert settings().model_concurrency == 8
    assert settings(model_concurrency=4).model_concurrency == 4
    for bad in (0, 17):
        with pytest.raises(ValueError, match="model_concurrency"):
            settings(model_concurrency=bad)


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


def test_limits_are_not_enforced_outside_production():
    settings(model_provider="anthropic", max_usd_per_page=0.01).check_production()


def test_tables_and_long_descriptions_raise_the_worst_case():
    plain = settings(model_provider="anthropic")
    heavy = settings(model_provider="anthropic", feature_tables=True)
    assert limits.largest_reservation_usd(heavy) > 2 * limits.largest_reservation_usd(plain)
    # a page cap that cannot hold eight of them is no finding: such a job runs fewer steps
    assert not any("MAX_USD_PER_PAGE" in f.text for f in limits.spend_findings(heavy))
    usd, _ = job_caps(heavy, 1)
    assert limits.affordable_concurrency(heavy, budget_usd=usd) < heavy.model_concurrency
    assert limits.largest_reservation_usd(heavy) <= usd  # but one call still fits


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


def test_the_beta_feature_set_raises_no_finding_on_the_default_claude_caps():
    s = settings(
        model_provider="anthropic",
        daily_spend_cap_usd=10,
        per_user_daily_page_quota=90,
        feature_long_descriptions=True,
        feature_notation_memory=True,
    )
    assert limits.spend_findings(s) == []
    usd, _ = job_caps(s, 1)
    assert usd >= limits.largest_reservation_usd(s)  # one call fits; a short job runs fewer steps
    assert limits.affordable_concurrency(s, budget_usd=usd) >= 1


def test_a_small_page_limit_is_judged_by_the_job_cap_it_really_gives():
    """With 5 pages allowed, a $2 job cap is really 5 x the page cap, not $2."""
    s = settings(max_pages_per_job=5)
    assert limits.spend_findings(s) == []
    wide = settings(model_provider="anthropic", max_pages_per_job=30, max_usd_per_job=500)
    assert any("runaway" in f.text for f in limits.spend_findings(wide))


def test_the_claude_second_check_counts_when_it_is_the_dearest_reservation():
    """The check holds its own reservation on its own model (T2-20); the concurrency the
    worker computes from the largest reservation must cover it, whatever models read the page."""
    cheap_pair = {
        "model_provider": "anthropic",
        "anthropic_main_model": "claude-haiku-5-5",
        "anthropic_cheap_model": "claude-haiku-5-5",
    }
    on = settings(claude_second_check="shadow", **cheap_pair)
    off = settings(claude_second_check="off", **cheap_pair)
    assert limits.largest_reservation_usd(on) > limits.largest_reservation_usd(off)
    # with the default models the first pass is dearer, so nothing changes
    default_on = settings(model_provider="anthropic", claude_second_check="shadow")
    default_off = settings(model_provider="anthropic", claude_second_check="off")
    assert limits.largest_reservation_usd(default_on) == limits.largest_reservation_usd(default_off)
    # OpenAI never runs the Claude check
    assert limits.largest_reservation_usd(settings()) == limits.largest_reservation_usd(
        settings(claude_second_check="off")
    )
