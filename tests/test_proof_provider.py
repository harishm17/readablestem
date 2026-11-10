"""The proof with the Anthropic provider: recorded with Claude, replayed from the recording.

Offline, like test_proof_engine.py: the model is the fake client, the layout and scoring code
are real. What is checked here is which provider and models the recording carries and that a
replay follows them, while a recording that names no provider (every one made before) keeps
meaning OpenAI.
"""

from __future__ import annotations

import pytest

from proof_support import SET_ID, PdfMedia, fake_client, make_project
from readablestem.config import Settings
from readablestem.core.types import Check
from readablestem.pipeline import html_checks
from readablestem.proof import engine, runner
from readablestem.proof.errors import ProofError

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
    # The recordings name a 30-page limit; the production default is 100 (2026-11-03) and
    # neither it nor MAX_PAGES_PER_JOB in the environment may change what a replay is.
    assert identity["max_pages_per_job"] == 30
    monkeypatch.setenv("MAX_PAGES_PER_JOB", "250")
    assert runner.proof_settings().max_pages_per_job == 30
    assert runner.proof_settings("anthropic").max_pages_per_job == 30
    # An old recording has no provider in it and means OpenAI.
    assert runner.recorded_settings(identity).model_provider == "openai"
    assert runner.check_identity({"settings": identity, **_same(settings)}, _full(settings)) == []


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
