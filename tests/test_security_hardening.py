"""Production settings that guard the cron route, the privacy notice and the session cookie,
and the proxy-hops warning (docs/security.md)."""

from __future__ import annotations

import json
import logging
from base64 import b64encode

import pytest
from itsdangerous import TimestampSigner
from itsdangerous.exc import BadSignature
from starlette.requests import Request

from fixtures.web.support import ALICE, WebEnv, login, make_settings
from readablestem.config import ConfigError
from readablestem.observability.logging import FIELDS_ATTR
from readablestem.ops import launch
from readablestem.web import abuse
from readablestem.web.app import check_web_secrets

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
COOKIE = "readablestem_session"
OLD = "o" * 40
NEW = "n" * 40


def prod(tmp_path, **kw):
    return make_settings(tmp_path, **{**PROD, **kw})


def notice_dir(tmp_path, monkeypatch, text: str):
    folder = tmp_path / "templates"
    folder.mkdir(exist_ok=True)
    (folder / "privacy.html").write_text(text)
    monkeypatch.setattr(launch, "TEMPLATES_DIR", folder)


# ------------------------------------------------------------------ cron token


def test_production_refuses_a_short_cron_token(tmp_path):
    with pytest.raises(ConfigError, match="CRON_TOKEN must be at least 32"):
        prod(tmp_path, cron_token="c" * 31).check_production()
    prod(tmp_path, cron_token="c" * 32).check_production()


def test_a_short_cron_token_is_fine_outside_production(tmp_path):
    make_settings(tmp_path, cron_token="short").check_production()


# ------------------------------------------------------------------ analytics and the notice


def test_production_refuses_analytics_while_the_notice_says_there_is_none(tmp_path):
    with pytest.raises(ConfigError, match="ANALYTICS_ENABLED=true is refused"):
        prod(tmp_path, analytics_enabled=True).check_production()
    prod(tmp_path).check_production()  # off: fine


def test_production_accepts_analytics_once_the_notice_describes_it(tmp_path, monkeypatch):
    notice_dir(tmp_path, monkeypatch, "<p>Page views go to PostHog.</p>")
    prod(tmp_path, analytics_enabled=True).check_production()
    notice_dir(tmp_path, monkeypatch, "<p>PostHog. There are no analytics.</p>")
    with pytest.raises(ConfigError, match="no analytics"):
        prod(tmp_path, analytics_enabled=True).check_production()
    notice_dir(tmp_path, monkeypatch, "<p>Nothing here.</p>")
    with pytest.raises(ConfigError, match="does not name PostHog"):
        prod(tmp_path, analytics_enabled=True).check_production()


def test_analytics_is_allowed_outside_production_whatever_the_notice_says(tmp_path):
    make_settings(tmp_path, analytics_enabled=True).check_production()


def test_launch_check_lists_analytics_without_notice_words(tmp_path, monkeypatch):
    notice_dir(tmp_path, monkeypatch, "<p>Nothing.</p>")
    items = launch.launch_blockers(settings=make_settings(tmp_path, analytics_enabled=True))
    assert any("PostHog" in item for item in items)


# ------------------------------------------------------------------ proxy hops


def request_with(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": ("10.0.0.9", 1)})


def test_extra_forwarded_entries_warn_once_without_addresses(caplog, monkeypatch):
    monkeypatch.setattr(abuse, "_hops_warned", False)
    caplog.set_level(logging.WARNING, logger="readablestem.web.abuse")
    three = request_with({"X-Forwarded-For": "198.51.100.1, 198.51.100.2, 203.0.113.5"})
    assert abuse.client_ip(three, 1) == "203.0.113.5"
    abuse.client_ip(three, 1)
    assert caplog.text.count("proxy_hops_unexpected") == 1
    assert "198.51.100" not in caplog.text and "203.0.113" not in caplog.text
    record = next(r for r in caplog.records if r.getMessage() == "proxy_hops_unexpected")
    assert getattr(record, FIELDS_ATTR) == {"trusted_proxy_hops": 1, "forwarded_entries": 3}


def test_the_expected_entry_counts_do_not_warn(caplog, monkeypatch):
    monkeypatch.setattr(abuse, "_hops_warned", False)
    caplog.set_level(logging.WARNING, logger="readablestem.web.abuse")
    abuse.client_ip(request_with({"X-Forwarded-For": "203.0.113.5"}), 1)
    abuse.client_ip(request_with({"X-Forwarded-For": "198.51.100.1, 203.0.113.5"}), 1)
    abuse.client_ip(request_with({"X-Forwarded-For": "a, 203.0.113.4, 203.0.113.5"}), 2)
    assert "proxy_hops_unexpected" not in caplog.text


def test_launch_check_flags_zero_hops_in_production(tmp_path):
    items = launch.launch_blockers(settings=prod(tmp_path, trusted_proxy_hops=0))
    assert any("TRUSTED_PROXY_HOPS=0" in item for item in items)
    items = launch.launch_blockers(settings=prod(tmp_path, trusted_proxy_hops=1))
    assert not any("TRUSTED_PROXY_HOPS" in item for item in items)


# ------------------------------------------------------------------ session secret rotation


def cookie_from(secret: str, data: dict) -> str:
    payload = b64encode(json.dumps(data).encode())
    return TimestampSigner(secret).sign(payload).decode()


def session_signer(app):
    node = app.build_middleware_stack()
    while node is not None:
        if hasattr(node, "signer"):
            return node.signer
        node = getattr(node, "app", None)
    raise AssertionError("no session middleware")


def test_a_cookie_signed_with_a_previous_secret_is_still_read(tmp_path):
    env = WebEnv(tmp_path, session_secret=NEW, session_secret_previous=f" {OLD} , ")
    assert env.settings.previous_session_secrets == [OLD]
    signer = session_signer(env.app)
    assert signer.unsign(cookie_from(OLD, {"a": 1}).encode(), max_age=60)
    assert signer.unsign(cookie_from(NEW, {"a": 1}).encode(), max_age=60)


def test_new_cookies_are_signed_with_the_current_secret_only(tmp_path):
    env = WebEnv(tmp_path, session_secret=NEW, session_secret_previous=OLD)
    signed = session_signer(env.app).sign(b"payload")
    assert TimestampSigner(NEW).unsign(signed)
    with pytest.raises(BadSignature):
        TimestampSigner(OLD).unsign(signed)


def test_an_unknown_secret_is_refused(tmp_path):
    env = WebEnv(tmp_path, session_secret=NEW, session_secret_previous=OLD)
    with pytest.raises(BadSignature):
        session_signer(env.app).unsign(cookie_from("x" * 40, {"a": 1}).encode(), max_age=60)


def test_a_signed_in_person_stays_signed_in_through_a_rotation(tmp_path):
    env = WebEnv(tmp_path, session_secret=OLD)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        cookie = client.cookies.get(COOKIE)
        assert client.get("/app").status_code == 200
    assert cookie
    rotated = WebEnv(tmp_path, session_secret=NEW, session_secret_previous=OLD)
    with rotated.running() as client:
        client.cookies.set(COOKIE, cookie)
        assert client.get("/app").status_code == 200
    dropped = WebEnv(tmp_path, session_secret=NEW)
    with dropped.running() as client:
        client.cookies.set(COOKIE, cookie)
        assert client.get("/app").status_code != 200  # the old secret is gone: signed out


def test_production_checks_the_previous_secrets_too(tmp_path):
    cron = "c" * 40
    with pytest.raises(ConfigError, match="SESSION_SECRET_PREVIOUS"):
        check_web_secrets(prod(tmp_path, cron_token=cron, session_secret_previous=f"{OLD},short"))
    check_web_secrets(prod(tmp_path, cron_token=cron, session_secret_previous=OLD))
    check_web_secrets(prod(tmp_path, cron_token=cron))
