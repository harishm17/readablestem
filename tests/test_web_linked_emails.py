"""T3-10 (a): other Google accounts an admin allows for one instructor (offline: the Google
client is stubbed, as in test_web_auth.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from a11y_stem.cli.main import main
from a11y_stem.web import linked_emails
from a11y_stem.web.auth import set_allowed
from a11y_stem.web.db import User, session_scope
from fixtures.web.support import ADMIN, ALICE, BOB, WebEnv, csrf_from, login

PERSONAL = "alice.personal@gmail.com"


@pytest.fixture
def google(tmp_path, monkeypatch):
    env = WebEnv(
        tmp_path,
        dev_login_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        base_url="https://beta.example.org",
    )
    google = env.ctx.oauth.google
    env.userinfo = {"email": PERSONAL, "email_verified": True, "name": "Alice P"}

    async def authorize_access_token(request, **kwargs):
        return {"userinfo": env.userinfo}

    monkeypatch.setattr(google, "authorize_access_token", authorize_access_token)
    with env.running() as client:
        env.browser = client
        env.allow(ALICE)
        env.allow(BOB)
        yield env


def link(env, owner: str = ALICE, other: str = PERSONAL):
    with session_scope(env.ctx.factory) as session:
        linked_emails.link(session, owner, other)


def user_count(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return len(list(session.scalars(select(User))))


def test_a_linked_google_account_signs_in_the_instructor(google):
    link(google)
    response = google.browser.get("/auth/callback?code=c&state=abc")
    assert response.status_code == 303 and response.headers["location"] == "/app"
    assert google.browser.get("/app").status_code == 200
    with session_scope(google.ctx.factory) as session:
        alice = session.scalar(select(User).where(User.email == ALICE))
        assert alice is not None and alice.last_login_at is not None
        assert session.scalar(select(User).where(User.email == PERSONAL)) is None
    assert user_count(google) == 3  # admin, alice, bob: the link made nobody new


def test_an_unlinked_google_account_is_still_refused_and_not_stored(google):
    response = google.browser.get("/auth/callback?code=c&state=abc")
    assert response.status_code == 403
    assert PERSONAL not in response.text or "not on the beta list" in response.text
    with session_scope(google.ctx.factory) as session:
        assert session.scalar(select(User).where(User.email == PERSONAL)) is None


def test_a_linked_account_needs_google_to_have_verified_it(google):
    link(google)
    google.userinfo = {"email": PERSONAL, "email_verified": False}
    assert google.browser.get("/auth/callback?code=c&state=abc").status_code == 403


def test_removing_the_instructor_ends_the_linked_account_too(google):
    link(google)
    with session_scope(google.ctx.factory) as session:
        set_allowed(session, ALICE, allowed=False)
    response = google.browser.get("/auth/callback?code=c&state=abc")
    assert response.status_code == 403
    assert google.browser.get("/app").status_code == 303


def test_unlinking_signs_the_instructor_out_everywhere_and_stops_the_account(google):
    link(google)
    google.browser.get("/auth/callback?code=c&state=abc")
    assert google.browser.get("/app").status_code == 200
    with session_scope(google.ctx.factory) as session:
        assert linked_emails.unlink(session, PERSONAL) is True
    assert google.browser.get("/app").status_code == 303  # that session ended
    assert google.browser.get("/auth/callback?code=c&state=abc").status_code == 403
    with session_scope(google.ctx.factory) as session:
        assert linked_emails.unlink(session, PERSONAL) is False


@pytest.mark.parametrize(
    ("owner", "other", "code"),
    [
        ("nobody@example.org", PERSONAL, "unknown_owner"),
        (ALICE, ALICE, "same_address"),
        (ALICE, BOB, "is_a_user"),
    ],
)
def test_links_that_would_be_wrong_are_refused(google, owner, other, code):
    with (
        session_scope(google.ctx.factory) as session,
        pytest.raises(linked_emails.LinkRefused) as e,
    ):
        linked_emails.link(session, owner, other)
    assert e.value.code == code


def test_an_address_belongs_to_one_instructor_only(google):
    link(google)
    with (
        session_scope(google.ctx.factory) as session,
        pytest.raises(linked_emails.LinkRefused) as e,
    ):
        linked_emails.link(session, BOB, PERSONAL)
    assert e.value.code == "already_linked"


def test_an_instructor_who_was_removed_cannot_gain_links(google):
    with session_scope(google.ctx.factory) as session:
        set_allowed(session, BOB, allowed=False)
    with session_scope(google.ctx.factory) as session, pytest.raises(linked_emails.LinkRefused):
        linked_emails.link(session, BOB, "bob.personal@gmail.com")


def test_there_is_a_limit_per_instructor(google):
    for number in range(linked_emails.MAX_LINKED):
        link(google, other=f"a{number}@gmail.com")
    with (
        session_scope(google.ctx.factory) as session,
        pytest.raises(linked_emails.LinkRefused) as e,
    ):
        linked_emails.link(session, ALICE, "one-too-many@gmail.com")
    assert e.value.code == "too_many"


def test_a_linked_address_cannot_be_allowed_as_a_person_of_its_own(google):
    link(google)
    with (
        session_scope(google.ctx.factory) as session,
        pytest.raises(linked_emails.LinkRefused) as e,
    ):
        set_allowed(session, PERSONAL, allowed=True)
    assert e.value.code == "is_linked"


# --------------------------------------------------------------------------- the admin page


def admin_client(env):
    env.allow(ADMIN, admin=True)
    admin = env.client()
    login(admin, ADMIN)
    return admin


def test_the_admin_links_and_unlinks_from_the_admin_page(google):
    admin = admin_client(google)
    token = csrf_from(admin.get("/admin").text)
    done = admin.post(
        "/admin/linked-emails", data={"csrf_token": token, "email": ALICE, "other": PERSONAL}
    )
    assert done.status_code == 303
    page = admin.get("/admin").text
    assert PERSONAL in page and "Other Google accounts" in page
    assert google.browser.get("/auth/callback?code=c&state=abc").status_code == 303
    token = csrf_from(admin.get("/admin").text)
    removed = admin.post(
        "/admin/linked-emails/remove", data={"csrf_token": token, "other": PERSONAL}
    )
    assert removed.status_code == 303
    assert PERSONAL not in admin.get("/admin").text
    admin.close()


def test_the_admin_page_says_why_a_link_was_refused(google):
    admin = admin_client(google)
    token = csrf_from(admin.get("/admin").text)
    response = admin.post(
        "/admin/linked-emails", data={"csrf_token": token, "email": ALICE, "other": BOB}
    )
    assert response.status_code == 422
    assert "person of its own" in response.text
    assert 'aria-invalid="true"' in response.text
    blank = admin.post("/admin/linked-emails", data={"csrf_token": token, "email": "", "other": ""})
    assert blank.status_code == 422
    admin.close()


def test_the_admin_allowlist_form_refuses_a_linked_address(google):
    link(google)
    admin = admin_client(google)
    token = csrf_from(admin.get("/admin").text)
    response = admin.post("/admin/allowlist", data={"csrf_token": token, "email": PERSONAL})
    assert response.status_code == 422 and "linked to another instructor" in response.text
    admin.close()


# --------------------------------------------------------------------------- the command line


def test_the_command_line_links_and_unlinks(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("ENV", "dev")
    from a11y_stem.config import get_settings

    get_settings.cache_clear()
    try:
        assert main(["users", "allow", ALICE]) == 0
        assert main(["users", "link", ALICE, PERSONAL]) == 0
        assert main(["users", "link", ALICE, PERSONAL]) == 2  # already linked
        assert main(["users", "allow", PERSONAL]) == 2  # linked to another person
        out = capsys.readouterr()
        assert f"{PERSONAL} can now sign in as {ALICE}" in out.out
        assert main(["users", "unlink", PERSONAL]) == 0
        assert main(["users", "unlink", PERSONAL]) == 2
    finally:
        get_settings.cache_clear()
