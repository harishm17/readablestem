"""Settings for the content features, Microsoft sign-in, feedback and usage events."""

from __future__ import annotations

import pytest

from a11y_stem.config import ConfigError, Settings

TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
OTHER_TENANT = "00000000-0000-0000-0000-000000000001"


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@pytest.fixture
def clean_env(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def test_features_are_off_and_feedback_and_events_on_by_default(clean_env):
    s = settings()
    assert not (s.feature_tables or s.feature_long_descriptions)
    assert not (s.feature_colour or s.feature_notation_memory)
    assert s.feedback_questions_enabled and s.events_enabled
    assert s.ms_client_id is None and s.ms_client_secret is None
    assert s.ms_allowed_tenants == [] and not s.ms_sign_in_enabled
    s.check_production()  # dev: nothing half-configured


def test_flags_come_from_the_environment(clean_env):
    clean_env.setenv("FEATURE_TABLES", "true")
    clean_env.setenv("FEATURE_NOTATION_MEMORY", "1")
    clean_env.setenv("EVENTS_ENABLED", "false")
    s = settings()
    assert s.feature_tables and s.feature_notation_memory
    assert not s.feature_colour and not s.events_enabled


@pytest.mark.parametrize(
    ("raw", "tenants"),
    [
        (f"{TENANT}", [TENANT]),
        (f" {TENANT.upper()} , {OTHER_TENANT},", [TENANT, OTHER_TENANT]),
        ("", []),  # blank (as in .env.example) means none
    ],
)
def test_tenants_are_a_comma_separated_list(clean_env, raw, tenants):
    clean_env.setenv("MS_ALLOWED_TENANTS", raw)
    assert settings().ms_allowed_tenants == tenants


def test_a_complete_microsoft_setup_turns_the_sign_in_on():
    s = settings(ms_client_id="app", ms_client_secret="secret", ms_allowed_tenants=[TENANT])
    s.check_production()
    assert s.ms_sign_in_enabled


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"ms_client_id": "app"}, "MS_CLIENT_SECRET is not"),
        ({"ms_client_id": "app", "ms_client_secret": " "}, "MS_CLIENT_SECRET is not"),
        ({"ms_client_secret": "secret", "ms_allowed_tenants": [TENANT]}, "MS_CLIENT_ID is not"),
        ({"ms_client_id": "app", "ms_client_secret": "secret"}, "MS_ALLOWED_TENANTS lists no"),
        (
            {"ms_client_id": "app", "ms_client_secret": "s", "ms_allowed_tenants": ["common"]},
            "directory",
        ),
    ],
)
@pytest.mark.parametrize("env", ["dev", "test", "prod"])
def test_a_half_configured_microsoft_sign_in_is_refused_everywhere(kw, message, env):
    with pytest.raises(ConfigError, match=message):
        settings(env=env, **kw).check_production()


def test_tenants_alone_do_not_turn_the_sign_in_on():
    s = settings(ms_allowed_tenants=[TENANT])
    s.check_production()
    assert not s.ms_sign_in_enabled
