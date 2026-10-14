"""The proof with the Anthropic provider: recorded with Claude, replayed from the recording.

Offline, like test_proof_engine.py: the model is the fake client, the layout and scoring code
are real. What is checked here is which provider and models the recording carries and that a
replay follows them, while a recording that names no provider (every one made before) keeps
meaning OpenAI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from a11y_stem.config import Settings
from a11y_stem.core.types import Check
from a11y_stem.pipeline import html_checks
from a11y_stem.proof import engine, runner
from a11y_stem.proof.bundle import MANIFEST_NAME, Bundle
from a11y_stem.proof.errors import ProofError
from proof_support import SET_ID, PdfMedia, fake_client, make_project

DATE = "2026-10-14"
OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-5-5"


@pytest.fixture(scope="module")
def claude_run(tmp_path_factory):
    root = make_project(tmp_path_factory.mktemp("project"))
    work = tmp_path_factory.mktemp("work")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(html_checks, "axe_checks", lambda html: [Check(name="axe", passed=True)])
        result = engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=5.0,
                date=DATE,
                work_dir=work,
                client_factory=fake_client,
                media_factory=PdfMedia,
                out=root / "PROOF-claude.md",
                n_boot=200,
                provider="anthropic",
            ),
            say=lambda _msg: None,
        )
    return root, work, work / "bundles" / f"{SET_ID}-{DATE}", result


def test_the_recording_names_the_provider_and_the_models(claude_run):
    _root, _work, bundle_dir, result = claude_run
    assert result.exit_code == 0
    manifest = json.loads((bundle_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["client"] == "anthropic"
    recorded = manifest["code"]["settings"]
    assert recorded["model_provider"] == "anthropic"
    assert (recorded["main_model"], recorded["cheap_model"]) == (OPUS, HAIKU)
    reports = sorted((bundle_dir / "reports").glob("*.json"))
    assert reports
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        assert report["client"] == "anthropic"
        assert report["models"] == {"main": OPUS, "cheap": HAIKU} | (
            {"judge": OPUS} if report["task"] == "figures" else {}
        )


def test_a_replay_follows_the_recording_without_flags_or_drift(claude_run, monkeypatch):
    root, work, bundle_dir, result = claude_run
    # The environment's provider does not matter: the recording says what it was made with.
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    replay = engine.run_proof(
        engine.ProofOptions(
            root=root, work_dir=work, bundle_dir=bundle_dir, out=root / "again.md", n_boot=200
        ),
        say=lambda _m: None,
    )
    assert replay.drift == [] and replay.exit_code == 0
    assert "Replay compared with the recorded reports: **identical**" in replay.text
    assert result.text.count("claude-opus-5-5") == replay.text.count("claude-opus-5-5") > 0


def test_a_live_run_on_another_provider_never_overwrites_proof_md(tmp_path):
    root = make_project(tmp_path / "project")
    options = engine.ProofOptions(
        root=root,
        live=True,
        max_usd=1.0,
        date=DATE,
        work_dir=tmp_path / "work",
        client_factory=fake_client,
        media_factory=PdfMedia,
        provider="anthropic",
    )
    with pytest.raises(ProofError, match="needs --out"):
        engine.run_proof(options, say=lambda _m: None)
    assert not (root / "PROOF.md").exists()


def test_identity_of_an_openai_recording_is_what_it_always_was(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")  # a proof does not follow the environment
    settings = runner.proof_settings()
    assert settings.model_provider == "openai"
    identity = runner.code_identity(settings)["settings"]
    assert set(identity) == set(runner.IDENTITY_SETTINGS)
    assert identity["main_model"] == "gpt-6.1-sol"
    # An old recording has no provider in it and means OpenAI.
    assert runner.recorded_settings(identity).model_provider == "openai"
    assert runner.check_identity({"settings": identity, **_same(settings)}, _full(settings)) == []


def test_identity_of_a_claude_recording_adds_the_provider_and_effort():
    settings = runner.proof_settings("anthropic", cheap_model="claude-sonnet-5-5")
    identity = runner.code_identity(settings)["settings"]
    assert identity["model_provider"] == "anthropic"
    assert (identity["main_model"], identity["cheap_model"]) == (OPUS, "claude-sonnet-5-5")
    back = runner.recorded_settings(identity)
    assert (back.model_provider, back.main_model, back.cheap_model) == (
        "anthropic",
        OPUS,
        "claude-sonnet-5-5",
    )
    assert runner.code_identity(back)["settings"] == identity
    # A recording on one provider is not the code identity of the other.
    other = runner.code_identity(runner.proof_settings())
    assert runner.check_identity({"settings": identity}, other)


def _full(settings: Settings) -> dict:
    return runner.code_identity(settings)


def _same(settings: Settings) -> dict:
    full = runner.code_identity(settings)
    return {k: v for k, v in full.items() if k != "settings"}


def test_the_api_check_names_a_missing_workspace_and_hides_the_message():
    import anthropic
    import httpx2

    settings = runner.proof_settings("anthropic")

    def refused() -> None:
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx2.Response(
            400,
            request=request,
            json={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "SECRET-TEXT"},
            },
        )
        raise anthropic.BadRequestError("SECRET-TEXT", response=response, body=response.json())

    with pytest.raises(ProofError) as raised:
        runner.probe_api(settings, send=refused)
    text = str(raised.value)
    assert "HTTP 400" in text and "invalid_request_error" in text
    assert "ANTHROPIC_WORKSPACE_ID" in text and "SECRET-TEXT" not in text
    assert "Nothing was recorded" in text


def test_a_missing_claude_key_stops_a_live_run_before_any_work(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from a11y_stem.config import ConfigError
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget

    budget = JobBudget(InMemorySpendGuard(cap_usd=1), max_usd=1, max_calls=5)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        runner.live_client(runner.proof_settings("anthropic"), budget, fake=False)


def test_the_bundle_of_a_claude_run_verifies(claude_run):
    bundle = Bundle.open(claude_run[2])
    assert bundle.verify() == []
    assert Path(claude_run[3].out).is_file()
