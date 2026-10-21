"""The public pages in both sign-up modes, and the launch-check wording checks.

With `SIGNUP_MODE=open` no public page may say sign-in is by invitation; without it the
pages say what they always said. The pages render from the setting (`signup_open` in the
templates), so one test per mode over every public page is what keeps a new sentence honest.
"""

from __future__ import annotations

import shutil

import pytest

from fixtures.web.support import ALICE, OutboxSender, WebEnv, csrf_from, login, make_settings
from readablestem.ops import launch

# Sentences that say sign-in is by invitation: true in allowlist mode, false in open mode.
STALE_WHEN_OPEN = (
    "by invitation",
    "small group of instructors",
    "beta list",
    "Not invited yet",
    "your invitation",
    "invitation was sent",
    "invite-only",
)
PUBLIC_PAGES = ("/", "/login", "/privacy", "/terms", "/limits", "/help/posting", "/status")
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


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_no_public_page_says_sign_in_is_by_invitation_when_sign_up_is_open(tmp_path, path):
    env = WebEnv(tmp_path, signup_mode="open", **GOOGLE)
    text = page(env, path)
    for phrase in STALE_WHEN_OPEN:
        assert phrase not in text, (path, phrase)


@pytest.mark.parametrize("path", ["/", "/login", "/privacy", "/terms"])
def test_the_invitation_wording_is_unchanged_in_allowlist_mode(tmp_path, path):
    env = WebEnv(tmp_path, signup_mode="allowlist", **GOOGLE)
    text = page(env, path)
    expected = {
        "/": "The beta is open to a small group of instructors, by invitation.",
        "/login": "Sign in with the Google account your invitation was sent to.",
        "/privacy": "For people on the beta list we store the email address",
        "/terms": "Access is by invitation.",
    }[path]
    assert expected in text


def test_the_login_page_says_a_first_sign_in_makes_the_account_and_links_the_demo(tmp_path):
    env = WebEnv(tmp_path, signup_mode="open", open_tier_daily_page_quota=17, **GOOGLE)
    text = page(env, "/login")
    assert "If you do not have an account yet, signing in makes one." in text
    assert "free beta run by Harish Manoharan, open to anyone" in text
    assert "daily limits (17 pages a day)" in text
    assert 'href="/demo"' in text and "needs no sign-in" in text
    assert "Not invited yet" not in text


def test_the_landing_page_describes_a_free_beta_anyone_can_try(tmp_path):
    env = WebEnv(tmp_path, signup_mode="open", open_tier_daily_page_quota=20, **GOOGLE)
    text = page(env, "/")
    assert "free beta run by Harish Manoharan" in text
    assert "Anyone with a Google account can sign in and try it" in text
    assert "New accounts have daily limits: 20 pages a day" in text
    assert "The example conversion needs no sign-in" in text
    assert 'href="/demo"' in text


def test_the_terms_and_the_limits_say_what_a_new_account_gets(tmp_path):
    env = WebEnv(
        tmp_path,
        signup_mode="open",
        per_user_daily_page_quota=90,
        open_tier_daily_page_quota=20,
        open_tier_daily_rereads=30,
        **GOOGLE,
    )
    terms = page(env, "/terms")
    assert "It is open to anyone with a Google account." in terms
    assert "New accounts have lower daily limits than invited ones" in terms
    assert "Anyone can make an account by signing in with their own Google account" in terms
    assert "You can delete your account yourself at any time, from the Account page" in terms
    limits = page(env, "/limits")
    assert "A new account (one you made by signing in) can convert 20 pages" in limits
    assert "start 30 reads with the model from the review screen" in limits
    assert "an invited account can convert 90 pages a day" in limits
    assert "conversions for new accounts pause until midnight UTC while others go on" in limits


def test_the_limits_page_names_the_daily_pages_in_allowlist_mode_too(tmp_path):
    env = WebEnv(tmp_path, signup_mode="allowlist", per_user_daily_page_quota=90, **GOOGLE)
    limits = page(env, "/limits")
    assert "You can convert 90 pages a day." in limits
    assert "new account" not in limits.split("Daily limits.")[1].split("Size.")[0]


def test_the_privacy_notice_says_the_first_sign_in_makes_an_account_and_how_to_delete_it(tmp_path):
    env = WebEnv(tmp_path, signup_mode="open", **GOOGLE)
    text = page(env, "/privacy")
    assert "The first time you sign in, we make an account for you." in text
    assert "your email address, your name, the time of your last sign-in" in text
    assert 'To delete your account,</strong> sign in, open <a href="/account">Account</a>' in text
    assert 'choose "Delete my account"' in text
    assert "If a sign-in is turned away, nothing is stored about it" in text
    assert "An account that an administrator has blocked is kept" in text
    assert "help@example.org" in text  # the address to write to if you cannot sign in


def test_the_privacy_notice_in_allowlist_mode_keeps_its_words_and_points_to_the_account_page(
    tmp_path,
):
    env = WebEnv(tmp_path, signup_mode="allowlist", **GOOGLE)
    text = page(env, "/privacy")
    assert "If you sign in and are not on the list, nothing is stored about you." in text
    assert "You can delete your account yourself from the" in text
    assert "we make an account for you" not in text


def test_the_notices_about_changes_name_the_beta_list_only_when_there_is_one(tmp_path):
    closed = page(WebEnv(tmp_path / "a", signup_mode="allowlist", **GOOGLE), "/privacy")
    opened = page(WebEnv(tmp_path / "b", signup_mode="open", **GOOGLE), "/privacy")
    assert "email the people on the beta list" in closed
    assert "email the people" not in opened
    assert "before the change takes effect." in opened


def test_the_privacy_notice_and_the_terms_with_microsoft_in_both_modes(tmp_path):
    ms = {
        "ms_client_id": "00000000-aaaa-4bbb-8ccc-000000000001",
        "ms_client_secret": "secret",
        "ms_allowed_tenants": ["11111111-1111-4111-8111-111111111111"],
    }
    env = WebEnv(tmp_path, signup_mode="open", **GOOGLE, **ms)
    assert "or their Microsoft school or work account" in page(env, "/terms")
    assert "from a university that is set up for it" in page(env, "/terms")
    login_page = page(env, "/login")
    assert (
        "Sign in with your Google account, or with your university's Microsoft account"
        in login_page
    )
    assert "if your university is set up for it" in login_page
    assert "and so can people with a Microsoft account from a university that is set up" in page(
        env, "/"
    )
    closed = WebEnv(tmp_path / "c", signup_mode="allowlist", **GOOGLE, **ms)
    assert "your university's Microsoft account, or with the Google account" in page(
        closed, "/login"
    )


def test_the_not_allowed_page_has_two_wordings_and_the_blocked_one_never_says_invitation(tmp_path):
    from readablestem.web.auth import set_blocked
    from readablestem.web.db import session_scope

    for mode in ("allowlist", "open"):
        env = WebEnv(tmp_path / mode, signup_mode=mode)
        with env.running():
            env.allow(ALICE)
            with session_scope(env.ctx.factory) as session:
                set_blocked(session, ALICE, blocked=True)
            text = " ".join(login(env.client(), ALICE).text.split())
            if mode == "open":
                assert (
                    "That account has been blocked, so it cannot sign in or convert documents."
                    in text
                )
                assert "If you think this is a mistake, email" in text
                assert "invitation" not in text and "beta list" not in text
            else:
                assert "You are not on the beta list yet" in text
                assert "by invitation" in text


def test_the_sign_in_link_pages_say_who_gets_one_in_both_modes(tmp_path):
    OutboxSender.reset()
    for mode, who in (("allowlist", "is on the beta list"), ("open", "has an account")):
        env = WebEnv(
            tmp_path / mode,
            signup_mode=mode,
            email_login_enabled=True,
            email_sender="outbox",
            dev_login_enabled=False,
            **{k: v for k, v in GOOGLE.items() if k != "dev_login_enabled"},
        )
        with env.running() as client:
            sign_in = " ".join(client.get("/login").text.split())
            assert (
                f"We send a link to addresses that {'already ' if mode == 'open' else ''}"
                in sign_in
            )
            assert who.replace("is on", "are on").replace("has an", "already have an") in sign_in
            sent = client.post(
                "/login/email",
                data={"csrf_token": csrf_from(client.get("/login").text), "email": ALICE},
            )
            body = " ".join(sent.text.split())
            assert f"If that address {who}, we have sent it a sign-in link." in body
            assert "this page does not tell anyone who" in body


def test_the_microsoft_organisation_page_in_both_modes(tmp_path):
    from fixtures.web.oidc import CLIENT_ID, CLIENT_SECRET, OTHER_TENANT, TENANT, FakeMicrosoft

    for mode in ("allowlist", "open"):
        env = WebEnv(
            tmp_path / mode,
            signup_mode=mode,
            dev_login_enabled=False,
            ms_client_id=CLIENT_ID,
            ms_client_secret=CLIENT_SECRET,
            ms_allowed_tenants=[TENANT],
            google_client_id="google-client",
            google_client_secret="google-secret",
            base_url="https://beta.example.org",
        )
        fake = FakeMicrosoft(ALICE)
        fake.install(env.ctx)
        fake.claims["tid"] = OTHER_TENANT
        with env.running() as client:
            token = csrf_from(client.get("/login").text)
            start = client.post("/login/microsoft", data={"csrf_token": token})
            response = client.get(fake.authorize(start.headers["location"]))
            text = " ".join(response.text.split())
            assert response.status_code == 403
            if mode == "open":
                assert "not set up for ReadableSTEM yet" in text
                assert "You do not need an invitation" in text
                assert "part of this beta yet" not in text
            else:
                assert "not part of this beta yet" in text
                assert "If your invitation was sent to a Google account" in text


# --------------------------------------------------------------------------- ops launch-check


def test_launch_check_passes_the_shipped_pages_in_both_modes(tmp_path):
    for mode in ("allowlist", "open"):
        settings = make_settings(tmp_path, signup_mode=mode)
        items = launch.launch_blockers(settings=settings)
        assert not any("privacy" in i or "invitation" in i for i in items), (mode, items)


def test_launch_check_names_a_public_page_with_invitation_wording_that_ignores_the_mode(
    tmp_path, monkeypatch
):
    templates = tmp_path / "templates"
    shutil.copytree(launch.TEMPLATES_DIR, templates)
    monkeypatch.setattr(launch, "TEMPLATES_DIR", templates)
    assert not [i for i in launch.launch_blockers() if "invitation" in i]
    stale = templates / "landing.html"
    stale.write_text(
        stale.read_text(encoding="utf-8").replace("signup_open", "something_else"), encoding="utf-8"
    )
    items = [i for i in launch.launch_blockers() if "invitation" in i]
    assert len(items) == 1
    assert items[0].startswith("web/templates/landing.html: says sign-in is by invitation")
    assert "render it from signup_open" in items[0]


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
