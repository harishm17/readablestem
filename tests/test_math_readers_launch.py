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
from readablestem.web.recipients import html_text, notice_text, recipients

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
def test_recipients_follow_the_settings(tmp_path, provider, readers):
    settings = make_settings(tmp_path, model_provider=provider, **reader_flags(readers))
    found = recipients(settings, tracer_on=True)
    assert [r.name for r in found if r.gets_documents] == sorted(
        expected_names(provider, readers), key=lambda n: n != COMPANY[provider]
    )
    assert [r.name for r in found if not r.gets_documents] == ["Railway", "Langfuse"]
    assert found[0].purposes[0] == "model"  # the model provider is listed first


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


def test_a_reader_from_the_other_company_gets_its_own_item(tmp_path):
    settings = make_settings(tmp_path, model_provider="openai", math_reader_claude_enabled=True)
    text = notice_text(settings)
    assert "Anthropic. A close-up picture of a handwritten expression" in text
    # The same company as the model provider: one item, with the extra sentence.
    same = notice_text(
        make_settings(tmp_path, model_provider="anthropic", math_reader_claude_enabled=True)
    )
    assert same is not None
    assert same.count("Anthropic.") == 1 and "read a second time" in same


def test_the_account_notes_name_the_same_companies(tmp_path):
    from fixtures.web.support import ALICE, login

    env = WebEnv(
        tmp_path,
        model_provider="openai",
        fake_models=True,
        math_reader_claude_enabled=True,
    )
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = html_text(client.get("/account").text)
    assert "sent to OpenAI and Anthropic stay under their own retention" in page


# ------------------------------------------------------------------ production refusal


@pytest.mark.parametrize(
    "provider,reader", list(itertools.product(PROVIDERS, ("claude", "openai")))
)
def test_production_accepts_every_combination_with_the_real_template(tmp_path, provider, reader):
    # The page is written from the same list, so the real template always names the reader's
    # company: the guard is for a template that stops doing so (next test).
    prod(tmp_path, model_provider=provider, **reader_flags((reader,))).check_production()


@pytest.mark.parametrize("flag,word", [("claude", "Anthropic"), ("openai", "OpenAI")])
def test_production_refuses_a_page_that_does_not_name_the_reader_company(
    tmp_path, monkeypatch, flag, word
):
    stand_in(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    with pytest.raises(ConfigError, match=f"refused in production.*does not name .*{word}"):
        prod(tmp_path, model_provider="openai", **reader_flags((flag,))).check_production()
    stand_in(tmp_path, monkeypatch, "<p>Pictures go to OpenAI and Anthropic.</p>")
    prod(tmp_path, model_provider="openai", **reader_flags((flag,))).check_production()


def test_the_stand_in_only_names_what_it_says(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch, "<p>Anthropic only.</p>")
    both = reader_flags(("claude", "openai"))
    with pytest.raises(ConfigError, match="does not name OpenAI"):
        prod(tmp_path, model_provider="anthropic", **both).check_production()


def test_the_check_reads_text_not_markup_or_comments(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch, "{# OpenAI #}<p class='OpenAI'>nothing</p><!-- OpenAI -->")
    with pytest.raises(ConfigError, match="does not name OpenAI"):
        prod(tmp_path, math_reader_openai_enabled=True).check_production()


def test_off_by_default_the_reader_check_does_not_run(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    prod(tmp_path).check_production()


def test_outside_production_the_notice_is_not_checked(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    make_settings(tmp_path, math_reader_claude_enabled=True).check_production()


def test_a_missing_notice_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "TEMPLATES_DIR", tmp_path / "nowhere")
    with pytest.raises(ConfigError, match=r"privacy\.html is missing"):
        prod(tmp_path, math_reader_openai_enabled=True).check_production()


def test_launch_check_lists_the_model_provider_and_the_reader_separately(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    items = launch.launch_blockers(
        settings=make_settings(tmp_path, model_provider="openai", math_reader_claude_enabled=True)
    )
    assert any("MODEL_PROVIDER=openai sends document images to OpenAI" in i for i in items)
    assert any("handwriting reader" in i and "Anthropic" in i for i in items)
    stand_in(tmp_path, monkeypatch, "<p>OpenAI. Anthropic.</p>")
    items = launch.launch_blockers(
        settings=make_settings(tmp_path, model_provider="openai", math_reader_claude_enabled=True)
    )
    assert not any("privacy.html" in i and "does not name" in i for i in items)


def test_analytics_check_reads_the_rendered_page(tmp_path, monkeypatch):
    stand_in(tmp_path, monkeypatch, "{% if signup_open %}<p>PostHog.</p>{% endif %}")
    with pytest.raises(ConfigError, match="does not name PostHog"):
        prod(tmp_path, analytics_enabled=True).check_production()  # signup is not open
    stand_in(tmp_path, monkeypatch, "<p>PostHog.</p>")
    prod(tmp_path, analytics_enabled=True).check_production()
