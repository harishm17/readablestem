"""The public pages in both sign-up modes, and the launch-check wording checks.

With `SIGNUP_MODE=open` no public page may say sign-in is by invitation; without it the
pages say what they always said. The pages render from the setting (`signup_open` in the
templates), so one test per mode over every public page is what keeps a new sentence honest.
"""

from __future__ import annotations

import shutil

import pytest

from fixtures.web.support import ALICE, WebEnv, login, make_settings
from readablestem.ops import launch

PUBLIC_PAGES = (
    "/",
    "/for-accessibility-offices",
    "/login",
    "/privacy",
    "/terms",
    "/limits",
    "/help/posting",
    "/guides",
    "/guides/ada-title-ii-stem-course-materials",
    "/guides/readablestem-mathpix-latexml",
    "/status",
)
GOOGLE = {
    "dev_login_enabled": False,
    "google_client_id": "client-id",
    "google_client_secret": "client-secret",
    "base_url": "https://beta.example.org",
}


def page(env: WebEnv, path: str) -> str:
    with env.running() as client:
        response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return " ".join(response.text.split())


# --------------------------------------------------------------------------- ops launch-check


@pytest.mark.parametrize(
    "phrase, what",
    [
        (
            "The first time you sign in, we make an account for you.",
            "first sign-in makes an account",
        ),
        ("Delete my account", "how to delete"),
    ],
)
def test_launch_check_wants_the_privacy_notice_to_say_what_open_sign_up_does(
    tmp_path, monkeypatch, phrase, what
):
    templates = tmp_path / "templates"
    shutil.copytree(launch.TEMPLATES_DIR, templates)
    monkeypatch.setattr(launch, "TEMPLATES_DIR", templates)
    open_settings = make_settings(tmp_path, signup_mode="open")
    closed_settings = make_settings(tmp_path, signup_mode="allowlist")
    assert not [i for i in launch.launch_blockers(settings=open_settings) if "SIGNUP_MODE" in i]
    privacy = templates / "privacy.html"
    privacy.write_text(
        privacy.read_text(encoding="utf-8").replace(phrase, "removed words"), encoding="utf-8"
    )
    items = [i for i in launch.launch_blockers(settings=open_settings) if "SIGNUP_MODE=open" in i]
    assert len(items) == 1 and what.split()[0] in items[0].replace("first time", "first")
    # The notice for allowlist mode is not held to it.
    assert not [i for i in launch.launch_blockers(settings=closed_settings) if "SIGNUP_MODE" in i]


# --------------------------------------------------------------------------- deleting an account


def account_pages(
    env: WebEnv, email: str, *, invited: bool = False, admin: bool = False
) -> tuple[str, str]:
    with env.running() as client:
        if invited:
            env.allow(email, admin=admin)
        login(client, email)
        page = " ".join(client.get("/account").text.split())
        confirm = " ".join(client.get("/account/delete").text.split())
    return page, confirm


@pytest.mark.parametrize("which", [0, 1], ids=["account page", "confirmation"])
def test_deleting_does_not_promise_more_than_it_does(tmp_path, which):
    """Backups keep a copy for about 6 days (docs/deploy-railway.md, the privacy notice) and
    what the model provider already received is under its own retention."""
    env = WebEnv(tmp_path / "a", signup_mode="open", model_provider="anthropic")
    text = account_pages(env, ALICE)[which]
    assert "Not reached by deleting:" in text
    assert "Backups keep a copy for about a week." in text
    assert "already sent to Anthropic stay under its own retention" in text
    assert "delete API inputs and outputs within 30 days, longer for material" in text
    assert "scrambled fingerprint of your address, until tomorrow" in text
    assert 'href="/privacy">privacy notice</a>' in text
    other = WebEnv(tmp_path / "b", signup_mode="open", model_provider="openai")
    text = account_pages(other, ALICE)[which]
    assert "OpenAI stay under its own retention" in text and "Anthropic" not in text


def test_the_privacy_notice_names_what_deletion_does_not_reach(tmp_path):
    text = page(WebEnv(tmp_path, signup_mode="open", **GOOGLE), "/privacy")
    assert "Deleting does not reach" in text and "database backups (about 6 days)" in text
    assert "what the model provider has already received" in text
    assert "a keyed hash, not the address" in text and "it is deleted the next day" in text
