"""The Anthropic provider as a whole: settings, the factory, routing and the second check.

The client itself is in test_models_anthropic_client.py; the CLI and proof parts are in
test_eval_cli.py and test_proof_provider.py.
"""

from __future__ import annotations

import pytest

from readablestem.config import (
    ConfigError,
    Settings,
    get_settings,
)
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models import (
    VisionRequest,
    make_vision_client,
)
from readablestem.models.verifier import FakeVerifier, any_check_enabled, verifier_of

HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
PROD = {
    "env": "prod",
    "daily_spend_cap_usd": 5,
    "per_user_daily_page_quota": 50,
    "session_secret": "x" * 40,
    "google_client_id": "id",
    "google_client_secret": "secret",
    "cron_token": "t" * 40,
    "contact_email": "a@example.org",
    "base_url": "https://example.org",
    "database_url": "postgresql+psycopg://u:p@h/db",
}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def budget() -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=1), max_usd=1, max_calls=50)


@pytest.fixture
def clean_env(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


# --- settings -------------------------------------------------------------------------


def test_the_key_is_a_secret_and_never_in_the_repr():
    s = settings(model_provider="anthropic", anthropic_api_key="sk-ant-secret-value")
    assert "sk-ant-secret-value" not in repr(s) and "sk-ant-secret-value" not in str(s)


def test_production_needs_the_key_of_the_selected_provider_only():
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        settings(**PROD).check_production()
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings(**PROD, model_provider="anthropic", openai_api_key="sk-o").check_production()
    settings(**PROD, model_provider="anthropic", anthropic_api_key="sk-ant").check_production()
    settings(**PROD, openai_api_key="sk-o").check_production()
    # A blank Anthropic key is as missing as no key.
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings(**PROD, model_provider="anthropic", anthropic_api_key="  ").check_production()


def test_web_secret_check_follows_the_provider():
    from readablestem.web.app import check_web_secrets

    full = {**PROD, "model_provider": "anthropic", "anthropic_api_key": "sk-ant"}
    check_web_secrets(settings(**full))
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        check_web_secrets(settings(**{**full, "anthropic_api_key": " "}))
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        check_web_secrets(settings(**{**PROD, "openai_api_key": " "}))


# --- factory --------------------------------------------------------------------------


# --- the second automated check: OpenAI Decisions, or the Claude check -----------------


def test_the_decisions_check_is_skipped_with_the_anthropic_provider():
    on = {"decisions_figure_verify": True, "decisions_equation_shadow": True}
    assert any_check_enabled(settings(**on))
    claude = settings(model_provider="anthropic", **on)
    assert not any_check_enabled(claude)
    # Offline too: the Claude provider's check is the Claude one (fake offline), not the
    # Decisions API's, and it names the Claude check model.
    fake = verifier_of(make_vision_client(claude, budget(), fake=True))
    assert isinstance(fake, FakeVerifier)
    assert fake.reported_model == claude.claude_check_model
    # ... and CLAUDE_SECOND_CHECK=off leaves the job without one.
    off = settings(model_provider="anthropic", claude_second_check="off", **on)
    assert verifier_of(make_vision_client(off, budget(), fake=True)) is None
    assert verifier_of(make_vision_client(settings(**on), budget(), fake=True)) is not None


# --- routing --------------------------------------------------------------------------


def request(model: str) -> VisionRequest:
    return VisionRequest(
        step="equation",
        model=model,
        prompt_version="equation-test",
        instructions="i",
        images=[b"\x89PNG\r\n\x1a\nimg"],
    )


ANSWER = {"is_equation": True, "latex": "x", "confidence": 0.95}


# --- privacy page and launch check ------------------------------------------------------


def test_the_privacy_page_names_the_provider_that_receives_the_images(tmp_path):
    from fixtures.web.support import WebEnv

    with WebEnv(tmp_path / "openai").running() as client:
        page = client.get("/privacy").text
    assert "OpenAI." in page and "Anthropic" not in page

    claude = WebEnv(tmp_path / "claude", model_provider="anthropic", fake_models=True)
    with claude.running() as client:
        page = client.get("/privacy").text
    assert "<strong>Anthropic.</strong>" in page and "Anthropic's API" in page
    assert "OpenAI" not in page
    assert "TODO(Harish)" not in page  # the note for Harish is a template comment
