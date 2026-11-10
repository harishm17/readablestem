"""A handwriting reader changes who receives a document's pictures. One list, built from the
settings (`web.recipients.recipients`), writes the privacy page and the launch checks, and
production refuses to start with a reader on unless the rendered page names its company
(the same pattern as the analytics check in test_security_hardening.py)."""

from __future__ import annotations

import itertools

import pytest

from fixtures.web.support import WebEnv, make_settings
from readablestem.config import ConfigError
from readablestem.ops import launch
from readablestem.web.recipients import html_text, notice_text

PROD = {
    "env": "prod",
    "dev_login_enabled": False,
    "database_url": "postgresql+psycopg://u:p@127.0.0.1:1/never",
    "base_url": "https://beta.example.org",
    "session_secret": "s" * 40,
    "google_client_id": "client-id",
    "google_client_secret": "client-secret",
    "openai_api_key": "sk-test",
    "anthropic_api_key": "sk-ant-test",
}
PROVIDERS = ("anthropic", "openai")
READERS = ((), ("claude",), ("openai",), ("claude", "openai"))
COMPANY = {"anthropic": "Anthropic", "openai": "OpenAI", "claude": "Anthropic"}


def reader_flags(readers) -> dict:
    return {f"math_reader_{name}_enabled": True for name in readers}


def prod(tmp_path, **kw):
    return make_settings(tmp_path, **{**PROD, **kw})


def stand_in(tmp_path, monkeypatch, text: str):
    folder = tmp_path / "templates"
    folder.mkdir(exist_ok=True)
    (folder / "privacy.html").write_text(text)
    monkeypatch.setattr(launch, "TEMPLATES_DIR", folder)


def expected_names(provider, readers) -> set[str]:
    return {COMPANY[provider]} | {COMPANY[r] for r in readers}


# ------------------------------------------------------------------ one list, real template


@pytest.mark.parametrize("provider,readers", list(itertools.product(PROVIDERS, READERS)))
def test_the_real_template_names_exactly_the_companies_that_receive_pictures(
    tmp_path, provider, readers
):
    settings = make_settings(tmp_path, model_provider=provider, **reader_flags(readers))
    text = notice_text(settings)
    assert text is not None
    wanted = expected_names(provider, readers)
    for name in ("Anthropic", "OpenAI"):
        assert (name in text) == (name in wanted), (provider, readers, name)


@pytest.mark.parametrize("provider,readers", list(itertools.product(PROVIDERS, READERS)))
def test_the_real_privacy_response_matches(tmp_path, provider, readers):
    env = WebEnv(tmp_path, model_provider=provider, fake_models=True, **reader_flags(readers))
    with env.running() as client:
        response = client.get("/privacy")
    assert response.status_code == 200
    page = html_text(response.text)
    wanted = expected_names(provider, readers)
    for name in ("Anthropic", "OpenAI"):
        assert (name in page) == (name in wanted), (provider, readers, name)
    # "Where it is processed" names the same companies as the list.
    assert " and ".join(sorted(wanted, key=lambda n: n != COMPANY[provider])) in page
    # What the launch check reads is what the visitor reads.
    check = notice_text(env.settings)
    assert check is not None
    assert {n for n in ("Anthropic", "OpenAI") if n in check} == wanted


# ------------------------------------------------------------------ production refusal


@pytest.mark.parametrize("flag,word", [("claude", "Anthropic"), ("openai", "OpenAI")])
def test_production_refuses_a_page_that_does_not_name_the_reader_company(
    tmp_path, monkeypatch, flag, word
):
    stand_in(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    with pytest.raises(ConfigError, match=f"refused in production.*does not name .*{word}"):
        prod(tmp_path, model_provider="openai", **reader_flags((flag,))).check_production()
    stand_in(tmp_path, monkeypatch, "<p>Pictures go to OpenAI and Anthropic.</p>")
    prod(tmp_path, model_provider="openai", **reader_flags((flag,))).check_production()


def test_the_check_reads_text_not_markup_or_comments(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch, "{# OpenAI #}<p class='OpenAI'>nothing</p><!-- OpenAI -->")
    with pytest.raises(ConfigError, match="does not name OpenAI"):
        prod(tmp_path, math_reader_openai_enabled=True).check_production()


def test_a_missing_notice_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "TEMPLATES_DIR", tmp_path / "nowhere")
    with pytest.raises(ConfigError, match=r"privacy\.html is missing"):
        prod(tmp_path, math_reader_openai_enabled=True).check_production()
