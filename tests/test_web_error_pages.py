"""Calm error pages for 404, 500 and 503, whatever is broken behind them."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from fixtures.web.support import ALICE, WebEnv, login
from readablestem.web import deps
from readablestem.web.security import APP_CSP

SECRET = "postgresql://user:SECRET-PASSWORD@10.0.0.5/a11y"


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.browser = client
        yield env


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception(SECRET)),
        InterfaceError("SELECT 1", {}, Exception(SECRET)),
        PoolTimeout(SECRET),
    ],
    ids=["operational", "interface", "pool-timeout"],
)
def test_database_trouble_gives_a_static_503_page_without_the_error(env, caplog, error):
    def fail():
        raise error

    env.app.add_api_route("/db-down", fail)
    caplog.set_level(logging.DEBUG)
    response = env.browser.get("/db-down")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "Not available right now" in response.text
    assert "Nothing is lost" in response.text
    assert 'href="/status"' in response.text and 'href="/"' in response.text
    assert "SECRET" not in response.text and "SECRET" not in caplog.text
    assert response.headers["content-security-policy"] == APP_CSP
    assert "database_unavailable" in caplog.text


def test_a_signed_in_page_still_answers_when_the_user_lookup_cannot(env, monkeypatch):
    """The usual error page looks the user up in the database; with it down that would
    fail again, so the 503 page must not."""
    env.allow(ALICE)
    login(env.browser, ALICE)

    def broken(factory):
        raise OperationalError("SELECT 1", {}, Exception(SECRET))

    monkeypatch.setattr(deps, "session_scope", broken)
    response = env.browser.get("/app")
    assert response.status_code == 503
    assert "Not available right now" in response.text and "SECRET" not in response.text
