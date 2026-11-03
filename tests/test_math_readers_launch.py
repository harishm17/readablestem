"""A third-party handwriting reader changes who receives a document's pictures: production
refuses to start with one on unless the privacy notice names its provider (the same pattern
as the analytics check in test_security_hardening.py)."""

from __future__ import annotations

import pytest

from fixtures.web.support import make_settings
from readablestem.config import ConfigError
from readablestem.ops import launch

PROD = {
    "env": "prod",
    "dev_login_enabled": False,
    "database_url": "postgresql+psycopg://u:p@127.0.0.1:1/never",
    "base_url": "https://beta.example.org",
    "session_secret": "s" * 40,
    "google_client_id": "client-id",
    "google_client_secret": "client-secret",
    "openai_api_key": "sk-test",
}


def prod(tmp_path, **kw):
    return make_settings(tmp_path, **{**PROD, **kw})


def notice(tmp_path, monkeypatch, text: str):
    folder = tmp_path / "templates"
    folder.mkdir(exist_ok=True)
    (folder / "privacy.html").write_text(text)
    monkeypatch.setattr(launch, "TEMPLATES_DIR", folder)


@pytest.mark.parametrize(
    "flag,word",
    [("math_reader_claude_enabled", "Anthropic"), ("math_reader_openai_enabled", "OpenAI")],
)
def test_production_refuses_a_reader_the_notice_does_not_name(tmp_path, monkeypatch, flag, word):
    notice(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    with pytest.raises(ConfigError, match=f"refused in production.*does not name {word}"):
        prod(tmp_path, **{flag: True}).check_production()
    notice(tmp_path, monkeypatch, f"<p>Pictures go to {word}.</p>")
    prod(tmp_path, **{flag: True}).check_production()


def test_both_readers_must_be_named(tmp_path, monkeypatch):
    notice(tmp_path, monkeypatch, "<p>Anthropic only.</p>")
    both = {"math_reader_claude_enabled": True, "math_reader_openai_enabled": True}
    with pytest.raises(ConfigError, match="does not name OpenAI"):
        prod(tmp_path, **both).check_production()


def test_off_by_default_needs_no_words(tmp_path, monkeypatch):
    notice(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    prod(tmp_path).check_production()


def test_outside_production_the_notice_is_not_checked(tmp_path, monkeypatch):
    notice(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    make_settings(tmp_path, math_reader_claude_enabled=True).check_production()


def test_a_missing_notice_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "TEMPLATES_DIR", tmp_path / "nowhere")
    with pytest.raises(ConfigError, match=r"privacy\.html is missing"):
        prod(tmp_path, math_reader_openai_enabled=True).check_production()


def test_launch_check_lists_a_reader_without_its_notice_word(tmp_path, monkeypatch):
    notice(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    items = launch.launch_blockers(
        settings=make_settings(tmp_path, math_reader_claude_enabled=True)
    )
    assert any("claude handwriting reader" in item and "Anthropic" in item for item in items)
    notice(tmp_path, monkeypatch, "<p>Anthropic.</p>")
    items = launch.launch_blockers(
        settings=make_settings(tmp_path, math_reader_claude_enabled=True)
    )
    assert not any("handwriting reader" in item for item in items)
