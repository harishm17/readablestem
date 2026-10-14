"""Launch audit: sign-in cannot be bypassed in production, and an allowlist change takes
effect at once."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fixtures.web.support import ADMIN, ALICE, WebEnv, csrf_from, login, make_settings
from readablestem.config import ConfigError
from readablestem.web.app import create_app
from readablestem.web.auth import set_allowed
from readablestem.web.db import session_scope


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


def test_production_refuses_to_start_with_dev_login_on(tmp_path):
    with pytest.raises(ConfigError, match="DEV_LOGIN_ENABLED"):
        create_app(prod(tmp_path, dev_login_enabled=True))


def test_production_has_no_dev_login_pages_or_buttons(tmp_path):
    client = TestClient(create_app(prod(tmp_path)), base_url="https://beta.example.org")
    assert client.get("/dev-login").status_code == 404
    assert client.post("/dev-login", data={"email": ADMIN, "csrf_token": "x"}).status_code == 404
    assert "dev-login" not in client.get("/login").text


def test_dev_login_is_off_whatever_the_flag_says_outside_dev_and_test(tmp_path):
    """Even if the flag were forced on after the start checks, the context refuses it."""
    app = create_app(prod(tmp_path))
    app.state.ctx.settings.dev_login_enabled = True  # as if validation had been skipped
    client = TestClient(app, base_url="https://beta.example.org")
    assert app.state.ctx.dev_login_enabled is False
    assert client.get("/dev-login").status_code == 404
    assert client.post("/dev-login", data={"email": ADMIN, "csrf_token": "x"}).status_code == 404


def test_the_allowlist_decides_on_every_request_not_at_sign_in(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        assert client.get("/app").status_code == 200
        with session_scope(env.ctx.factory) as session:
            set_allowed(session, ALICE, allowed=False)
        assert client.get("/app").status_code == 303
        token = csrf_from(client.get("/dev-login").text)
        refused = client.post("/dev-login", data={"csrf_token": token, "email": ALICE})
        assert refused.status_code == 403  # and the page says nothing about who else is on it
        assert client.get("/app").status_code == 303


def test_a_demoted_admin_loses_the_admin_pages_at_once(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE, admin=True)
        login(client, ALICE)
        assert client.get("/admin").status_code == 200
        with session_scope(env.ctx.factory) as session:
            set_allowed(session, ALICE, allowed=True, admin=False)
        assert client.get("/admin").status_code == 403
