"""T3-10 (b): the emailed sign-in link, behind EMAIL_LOGIN_ENABLED (offline: a sender that
keeps its messages in memory; no provider exists)."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from a11y_stem.config import ConfigError
from a11y_stem.web import email_login, linked_emails
from a11y_stem.web.app import create_app
from a11y_stem.web.auth import set_allowed
from a11y_stem.web.db import LoginLink, session_scope, utcnow
from a11y_stem.web.email_senders import sender_names
from fixtures.web.support import ALICE, BOB, OutboxSender, WebEnv, csrf_from, make_settings

PERSONAL = "alice.personal@gmail.com"
LINK = re.compile(r"https://beta\.example\.org(/login/email/confirm\?token=[A-Za-z0-9_-]+)")


@pytest.fixture(autouse=True)
def _empty_outbox():
    OutboxSender.reset()
    yield
    OutboxSender.reset()


@pytest.fixture
def web(tmp_path):
    env = WebEnv(
        tmp_path,
        email_login_enabled=True,
        email_sender="outbox",
        base_url="https://beta.example.org",
        rate_limits_enabled=True,
    )
    with env.running() as client:
        env.browser = client
        env.allow(ALICE)
        yield env


def ask(env, address: str, client=None):
    client = client or env.browser
    token = csrf_from(client.get("/login").text)
    return client.post("/login/email", data={"csrf_token": token, "email": address})


def emailed_path() -> str:
    [message] = OutboxSender.sent
    found = LINK.search(message.body)
    assert found, message.body
    return found.group(1)


# --------------------------------------------------------------------------- the settings


def test_it_is_off_by_default_and_the_routes_answer_404(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        assert "sign-in link" not in client.get("/login").text
        assert client.get("/login/email/confirm?token=" + "a" * 43).status_code == 404
        assert client.post(
            "/login/email", data={"csrf_token": "x", "email": ALICE}
        ).status_code in {
            403,
            404,
        }


def test_no_email_provider_is_registered_for_production():
    assert sender_names(production=True) == []


def test_turning_it_on_without_a_sender_is_refused_everywhere(tmp_path):
    with pytest.raises(ConfigError, match="EMAIL_SENDER is not set"):
        create_app(make_settings(tmp_path, email_login_enabled=True))
    with pytest.raises(ConfigError, match="not a registered sender"):
        create_app(make_settings(tmp_path, email_login_enabled=True, email_sender="mailgun"))


def prod(tmp_path, **overrides):
    values = {
        "env": "prod",
        "dev_login_enabled": False,
        "database_url": "postgresql+psycopg://u:p@127.0.0.1:1/never",
        "base_url": "https://beta.example.org",
        "session_secret": "s" * 40,
        "google_client_id": "client-id",
        "google_client_secret": "client-secret",
        "openai_api_key": "sk-test",
    }
    return make_settings(tmp_path, **(values | overrides))


def test_production_refuses_email_sign_in_without_a_real_sender(tmp_path):
    with pytest.raises(ConfigError, match="EMAIL_SENDER is not set"):
        create_app(prod(tmp_path, email_login_enabled=True))
    # A development sender (the console, or a test's outbox) is not a sender for production.
    for name in ("console", "outbox"):
        with pytest.raises(ConfigError, match="development only"):
            create_app(prod(tmp_path, email_login_enabled=True, email_sender=name))
    create_app(prod(tmp_path))  # off: starts as before


# --------------------------------------------------------------------------- the flow


def test_asking_for_a_link_sends_one_message_and_the_link_signs_in_once(web):
    response = ask(web, ALICE)
    assert response.status_code == 200 and "Check your email" in response.text
    [message] = OutboxSender.sent
    assert message.to == ALICE and "15 minutes" in message.body
    path = emailed_path()

    page = web.browser.get(path)  # a scanner opening the link uses nothing up
    assert page.status_code == 200 and "Finish signing in" in page.text
    assert web.browser.get(path).status_code == 200
    assert web.browser.get("/app").status_code == 303  # not signed in by a GET

    token = path.split("token=")[1]
    done = web.browser.post(
        "/login/email/confirm", data={"csrf_token": csrf_from(page.text), "token": token}
    )
    assert done.status_code == 303 and done.headers["location"] == "/app"
    assert web.browser.get("/app").status_code == 200

    again = web.client()
    page2 = again.get(path)
    assert page2.status_code == 400 and "does not work" in page2.text
    reuse = again.post(
        "/login/email/confirm",
        data={"csrf_token": csrf_from(again.get("/login").text), "token": token},
    )
    assert reuse.status_code == 400
    assert again.get("/app").status_code == 303
    again.close()


def test_the_page_is_the_same_for_a_stranger_and_nothing_is_sent_or_stored(web):
    known = ask(web, ALICE)
    OutboxSender.reset()
    stranger = ask(web, "stranger@example.org")
    assert stranger.status_code == known.status_code == 200
    assert "Check your email" in stranger.text
    assert OutboxSender.sent == []
    with session_scope(web.ctx.factory) as session:
        assert len(list(session.scalars(select(LoginLink)))) == 1  # Alice's only


def test_a_removed_person_gets_nothing_and_an_old_link_stops_working(web):
    ask(web, ALICE)
    path = emailed_path()
    with session_scope(web.ctx.factory) as session:
        set_allowed(session, ALICE, allowed=False)
    assert web.browser.get(path).status_code == 400
    OutboxSender.reset()
    ask(web, ALICE)
    assert OutboxSender.sent == []


def test_a_linked_google_address_can_get_a_link_to_that_address(web):
    with session_scope(web.ctx.factory) as session:
        linked_emails.link(session, ALICE, PERSONAL)
    ask(web, PERSONAL)
    [message] = OutboxSender.sent
    assert message.to == PERSONAL


def test_a_link_expires(web):
    ask(web, ALICE)
    path = emailed_path()
    with session_scope(web.ctx.factory) as session:
        session.execute(update(LoginLink).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert web.browser.get(path).status_code == 400
    token = path.split("token=")[1]
    assert email_login.redeem(web.ctx.factory, token) is None


def test_a_newer_link_replaces_the_older_one(web):
    ask(web, ALICE)
    first = emailed_path()
    OutboxSender.reset()
    ask(web, ALICE)
    second = emailed_path()
    assert first != second
    assert web.browser.get(first).status_code == 400
    assert web.browser.get(second).status_code == 200


def test_only_a_hash_of_the_token_is_stored(web):
    ask(web, ALICE)
    token = emailed_path().split("token=")[1]
    with session_scope(web.ctx.factory) as session:
        row = session.scalar(select(LoginLink))
        assert row is not None and token not in row.token_hash
        assert row.token_hash == email_login._hash(token)


@pytest.mark.parametrize("junk", ["", "x", "a" * 43, "a" * 44, "../" * 15, "%00" * 20])
def test_tokens_that_are_not_tokens_never_sign_in(web, junk):
    assert web.browser.get("/login/email/confirm", params={"token": junk}).status_code == 400
    page = web.browser.get("/login")
    response = web.browser.post(
        "/login/email/confirm", data={"csrf_token": csrf_from(page.text), "token": junk}
    )
    assert response.status_code == 400
    assert web.browser.get("/app").status_code == 303


def test_confirming_needs_the_session_token(web):
    ask(web, ALICE)
    token = emailed_path().split("token=")[1]
    assert web.browser.post("/login/email/confirm", data={"token": token}).status_code == 403
    assert (
        web.browser.post(
            "/login/email/confirm", data={"csrf_token": "forged", "token": token}
        ).status_code
        == 403
    )
    assert email_login.is_usable(web.ctx.factory, token)  # still unused


def test_asking_needs_the_session_token(web):
    assert web.browser.post("/login/email", data={"email": ALICE}).status_code == 403
    assert OutboxSender.sent == []


def test_a_bad_address_is_told_so_and_sends_nothing(web):
    response = ask(web, "not-an-address")
    assert response.status_code == 422 and "Enter an email address" in response.text
    assert OutboxSender.sent == []


def test_one_address_cannot_be_flooded_with_mail(web):
    for _ in range(6):
        response = ask(web, ALICE)
        assert response.status_code == 200 and "Check your email" in response.text
    assert len(OutboxSender.sent) == 3  # the per-address allowance; the page never changes


def test_a_failing_sender_is_not_shown_and_leaks_nothing(web, caplog):
    OutboxSender.fails = True
    response = ask(web, ALICE)
    assert response.status_code == 200 and "Check your email" in response.text
    assert "SECRET-SMTP-DETAIL" not in response.text
    assert "SECRET-SMTP-DETAIL" not in caplog.text and ALICE not in caplog.text


def test_nothing_about_the_address_or_token_is_logged(web, caplog):
    caplog.set_level("DEBUG")
    ask(web, ALICE)
    token = emailed_path().split("token=")[1]
    web.browser.get(f"/login/email/confirm?token={token}")
    assert ALICE not in caplog.text and token not in caplog.text


def test_the_login_page_offers_it_only_when_it_is_on(web):
    page = web.browser.get("/login").text
    assert "Email me a sign-in link" in page and 'name="email"' in page
    assert BOB not in page
