"""The Anthropic provider as a whole: settings, the factory, routing and the second check.

The client itself is in test_models_anthropic_client.py; the CLI and proof parts are in
test_eval_cli.py and test_proof_provider.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from readablestem.config import (
    ANTHROPIC_CHEAP_MODEL,
    ANTHROPIC_MAIN_MODEL,
    CHEAP_MODEL,
    MAIN_MODEL,
    ConfigError,
    Settings,
    get_settings,
    with_provider,
)
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Status
from readablestem.models import (
    FakeVisionClient,
    VisionRequest,
    call_with_fallback,
    make_live_client,
    make_vision_client,
)
from readablestem.models.anthropic_client import AnthropicVisionClient
from readablestem.models.claude_verifier import ClaudeVerifier
from readablestem.models.openai_client import OpenAIVisionClient
from readablestem.models.verifier import FakeVerifier, any_check_enabled, verifier_of
from readablestem.pipeline.equation_step import transcribe
from test_steps_support import equation_element, use_fallback_router

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


def test_openai_is_the_default_and_nothing_about_it_changes():
    s = settings()
    assert s.model_provider == "openai"
    assert (s.main_model, s.cheap_model) == (MAIN_MODEL, CHEAP_MODEL)
    assert s.fallback_for == {MAIN_MODEL: CHEAP_MODEL, CHEAP_MODEL: MAIN_MODEL}
    assert s.provider_key_name == "OPENAI_API_KEY"


def test_anthropic_defaults_are_haiku_cheap_and_opus_main():
    s = settings(model_provider="anthropic")
    assert (ANTHROPIC_MAIN_MODEL, ANTHROPIC_CHEAP_MODEL) == (OPUS, HAIKU)
    assert (s.main_model, s.cheap_model) == (OPUS, HAIKU)
    assert s.fallback_for == {OPUS: HAIKU, HAIKU: OPUS}  # fallback stays on the provider
    assert (s.anthropic_effort_main, s.anthropic_effort_cheap) == ("medium", "low")
    assert s.anthropic_refusal_fallback is True


def test_role_models_are_configurable_and_main_model_env_is_ignored_for_claude():
    s = settings(
        model_provider="anthropic",
        anthropic_cheap_model=SONNET,
        main_model="gpt-6.1-sol",  # the OpenAI setting, left in a .env
    )
    assert (s.main_model, s.cheap_model) == (OPUS, SONNET)
    assert s.fallback_for == {OPUS: SONNET, SONNET: OPUS}
    assert s.anthropic_effort_for(OPUS) == "medium" and s.anthropic_effort_for(SONNET) == "low"


def test_an_unpriced_claude_model_is_refused_at_settings_time():
    with pytest.raises(ValidationError, match="no price for ANTHROPIC_CHEAP_MODEL='claude-x'"):
        settings(model_provider="anthropic", anthropic_cheap_model="claude-x")
    # An OpenAI run does not look at the Anthropic models at all.
    assert settings(anthropic_cheap_model="claude-x").cheap_model == CHEAP_MODEL


def test_unknown_provider_and_effort_are_refused():
    with pytest.raises(ValidationError):
        settings(model_provider="gemini")
    with pytest.raises(ValidationError):
        settings(model_provider="anthropic", anthropic_effort_main="extreme")


def test_blank_anthropic_settings_in_the_environment_mean_unset(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "")
    clean_env.setenv("ANTHROPIC_WORKSPACE_ID", "")
    clean_env.setenv("MODEL_PROVIDER", "anthropic")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is None and s.anthropic_workspace_id is None
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY is not set"):
        s.require_provider_key()
    clean_env.setenv("ANTHROPIC_API_KEY", "  sk-ant-x  ")
    assert Settings(_env_file=None).require_provider_key() == "sk-ant-x"  # type: ignore[call-arg]


def test_the_key_is_a_secret_and_never_in_the_repr():
    s = settings(model_provider="anthropic", anthropic_api_key="sk-ant-secret-value")
    assert "sk-ant-secret-value" not in repr(s) and "sk-ant-secret-value" not in str(s)


@pytest.mark.parametrize("bad", ["wrkspc 1", "a/b", "x" * 200, "é"])
def test_a_workspace_id_that_cannot_be_a_header_value_is_refused(bad):
    with pytest.raises(ValidationError, match="ANTHROPIC_WORKSPACE_ID"):
        settings(anthropic_workspace_id=bad)


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


def test_with_provider_switches_both_ways_and_keeps_the_rest():
    base = settings(openai_api_key="sk-o", anthropic_api_key="sk-a", max_usd_per_job=1.5)
    assert with_provider(base) is base
    claude = with_provider(base, "anthropic", cheap_model=SONNET)
    assert (claude.model_provider, claude.main_model, claude.cheap_model) == (
        "anthropic",
        OPUS,
        SONNET,
    )
    assert claude.max_usd_per_job == 1.5 and claude.require_provider_key() == "sk-a"
    back = with_provider(claude, "openai")
    assert (back.main_model, back.cheap_model) == (MAIN_MODEL, CHEAP_MODEL)
    assert with_provider(back, main_model="gpt-x").main_model == "gpt-x"
    with pytest.raises(ValidationError):
        with_provider(base, "anthropic", main_model="gpt-6.1-sol")


# --- factory --------------------------------------------------------------------------


def test_factory_builds_the_client_of_the_provider_without_needing_a_key_yet():
    claude = make_vision_client(settings(model_provider="anthropic"), budget())
    assert isinstance(claude, AnthropicVisionClient)
    openai_client = make_vision_client(settings(), budget())
    assert isinstance(openai_client, OpenAIVisionClient)


def test_live_factory_checks_the_key_first():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        make_live_client(settings(model_provider="anthropic", openai_api_key="sk-o"), budget())
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        make_live_client(settings(anthropic_api_key="sk-a"), budget())
    claude = make_live_client(
        settings(model_provider="anthropic", anthropic_api_key="sk-a"), budget()
    )
    assert isinstance(claude, AnthropicVisionClient)
    # The Claude second check (T2-20) is on in shadow mode by default.
    assert isinstance(verifier_of(claude), ClaudeVerifier)


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


def test_the_canary_is_not_due_with_the_anthropic_provider():
    from readablestem.ops import canary

    s = settings(model_provider="anthropic", env="dev", openai_api_key="sk-o")
    assert canary.due(s) == "provider_not_openai"


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


def test_fallback_stays_within_the_selected_provider():
    from pydantic import BaseModel

    class Out(BaseModel):
        is_equation: bool
        latex: str
        confidence: float

    s = settings(model_provider="anthropic")
    client = FakeVisionClient(defaults={"equation": ANSWER}, fail_models={HAIKU: "api_error"})
    parsed, calls = call_with_fallback(client, request(HAIKU), Out, settings=s)
    assert parsed is not None
    assert [(c.model, c.outcome, c.attempt) for c in calls] == [
        (HAIKU, "api_error", 1),
        (OPUS, "ok", 2),
    ]
    assert calls[1].is_fallback
    assert all(c.model.startswith("claude-") for c in calls)


def test_a_model_of_the_other_provider_has_no_fallback():
    from pydantic import BaseModel

    class Out(BaseModel):
        latex: str

    s = settings(model_provider="anthropic")
    client = FakeVisionClient(defaults={"equation": None})
    _, calls = call_with_fallback(client, request("gpt-6-luna"), Out, settings=s)
    assert len(calls) == 1


def test_cheap_low_confidence_escalates_to_the_main_model_on_the_anthropic_provider(monkeypatch):
    use_fallback_router(monkeypatch)
    s = settings(model_provider="anthropic")
    table = {
        HAIKU: {"is_equation": True, "latex": r"x^2", "confidence": 0.6},
        OPUS: {"is_equation": True, "latex": r"x^{2}", "confidence": 0.97},
    }
    client = FakeVisionClient(responders={"equation": lambda req: table[req.model]})
    # An inline equation goes to the cheap model first (a display one starts on the main).
    element = equation_element(display="inline")
    fix = transcribe(element, b"\x89PNG crop", client=client, settings=s)
    assert [r.model for r in client.requests] == [HAIKU, OPUS]  # cheap first, then escalated
    assert fix.status == Status.ACCEPTED
    assert [c.model for c in fix.calls] == [HAIKU, OPUS]


def test_confident_cheap_answer_stays_on_the_cheap_model(monkeypatch):
    use_fallback_router(monkeypatch)
    s = settings(model_provider="anthropic", anthropic_cheap_model=SONNET)
    client = FakeVisionClient(
        defaults={"equation": {"is_equation": True, "latex": "x", "confidence": 0.97}}
    )
    fix = transcribe(equation_element(display="inline"), b"\x89PNG crop", client=client, settings=s)
    assert fix.status == Status.ACCEPTED
    assert {r.model for r in client.requests} <= {SONNET, OPUS}
    assert all(m.startswith("claude-") for m in (r.model for r in client.requests))


def test_spend_admission_prices_the_claude_models():
    from readablestem.web.spend import largest_reservation_usd

    openai_largest = largest_reservation_usd(settings())
    claude_largest = largest_reservation_usd(settings(model_provider="anthropic"))
    assert claude_largest > 0
    # Thinking headroom and the dearer main model make the largest hold bigger.
    assert claude_largest > openai_largest


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


def test_launch_check_wants_anthropic_in_the_privacy_notice_once_it_is_the_provider(
    tmp_path, monkeypatch
):
    from readablestem.ops import launch

    templates = tmp_path / "templates"
    templates.mkdir()
    privacy = templates / "privacy.html"
    privacy.write_text("<p>Images go to OpenAI.</p>")
    monkeypatch.setattr(launch, "TEMPLATES_DIR", templates)

    def flagged(s: Settings) -> bool:
        return any(
            "MODEL_PROVIDER=anthropic" in item for item in launch.launch_blockers(settings=s)
        )

    assert flagged(settings(model_provider="anthropic"))
    assert not flagged(settings())
    privacy.write_text("<p>Images go to Anthropic.</p>")
    assert not flagged(settings(model_provider="anthropic"))
