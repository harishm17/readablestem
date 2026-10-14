"""Launch audit: security headers on every kind of response, and the cookie's flags.

Every response, whatever produced it (a page, a redirect, an error, a refused form, an
oversized body, a static file, the sandboxed document), carries the same baseline headers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from a11y_stem.web.app import SESSION_COOKIE, create_app
from a11y_stem.web.security import APP_CSP, VIEW_CSP
from fixtures.web.support import (
    ALICE,
    WebEnv,
    csrf_from,
    job_id_from,
    login,
    make_pdf,
    make_settings,
)
from fixtures.web.support import upload as upload_pdf

BASELINE = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}


def check_headers(response, *, hsts: bool = False, csp: str | None = None, where: str = ""):
    headers = response.headers
    for name, value in BASELINE.items():
        assert headers.get(name) == value, (where, name)
    assert "camera=()" in headers.get("permissions-policy", ""), where
    assert "microphone=()" in headers["permissions-policy"], where
    policy = headers.get("content-security-policy", "")
    assert policy == (csp or APP_CSP), where
    assert ("strict-transport-security" in headers) is hsts, where
    if hsts:
        assert "max-age=31536000" in headers["strict-transport-security"], where


def test_the_app_policy_allows_no_inline_script_or_style():
    directives = dict(part.strip().split(" ", 1) for part in APP_CSP.split(";"))
    assert directives["script-src"] == "'self'"
    assert directives["style-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert "unsafe-inline" not in APP_CSP and "unsafe-eval" not in APP_CSP
    assert "*" not in APP_CSP.replace("https://", "")


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def test_every_kind_of_response_carries_the_baseline(web):
    browser = web.browser
    token = csrf_from(browser.get("/app").text)
    job = job_id_from(upload_pdf(browser, make_pdf(1)))
    web.run_worker()
    cases = {
        "page": browser.get("/privacy"),
        "signed-in page": browser.get("/app"),
        "redirect": browser.get(f"/jobs/{job}/download/../.."),
        "404 page": browser.get("/no-such-page"),
        "404 job": browser.get("/jobs/" + "0" * 32),
        "405": browser.delete("/privacy"),
        "csrf refused": browser.post("/logout", data={"csrf_token": "forged"}),
        "validation error": browser.get("/jobs/" + "0" * 32 + "/status?v=a&v=b"),
        "too large": browser.post("/logout", content=b"x" * (2 * 1024 * 1024)),
        "healthz": browser.get("/healthz"),
        "status": browser.get("/status"),
        "static file": browser.get("/static/app.css"),
        "static js": browser.get("/static/app.js"),
        "static missing": browser.get("/static/nope.js"),
        "static traversal": browser.get("/static/../config.py"),
        "crop image": browser.get(f"/jobs/{job}/crops/p1-e3.png"),
        "anonymous redirect": web.client().get("/app"),
        "HEAD": browser.head("/privacy"),
    }
    for where, response in cases.items():
        check_headers(response, where=where)
    assert cases["too large"].status_code == 413
    assert cases["static missing"].status_code == 404
    assert cases["static traversal"].status_code in {400, 404}
    assert token  # the signed-in session was real


def test_the_converted_document_is_sandboxed_whichever_way_it_is_opened(web):
    job = job_id_from(upload_pdf(web.browser, make_pdf(1)))
    web.run_worker()
    for path in (f"/jobs/{job}/view", f"/jobs/{job}/download"):
        response = web.browser.get(path)
        assert response.status_code == 200, path
        check_headers(response, csp=VIEW_CSP, where=path)
    assert VIEW_CSP.startswith("sandbox;")
    assert "allow-scripts" not in VIEW_CSP and "allow-same-origin" not in VIEW_CSP
    assert "script-src" not in VIEW_CSP and "default-src 'none'" in VIEW_CSP
    web_demo = web.browser.get("/demo")
    check_headers(web_demo, csp=VIEW_CSP, where="/demo")


def production_app(tmp_path):
    settings = make_settings(
        tmp_path,
        env="prod",
        dev_login_enabled=False,
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/never",
        base_url="https://beta.example.org",
        session_secret="s" * 40,
        google_client_id="client-id",
        google_client_secret="client-secret",
        openai_api_key="sk-test",
    )
    return create_app(settings)  # no lifespan: nothing connects to the database


def test_production_adds_hsts_to_every_response_even_a_crash(tmp_path, monkeypatch):
    client = TestClient(production_app(tmp_path), base_url="https://beta.example.org")
    check_headers(client.get("/"), hsts=True, where="/")
    check_headers(client.get("/no-such-page"), hsts=True, where="404")
    check_headers(client.get("/static/app.css"), hsts=True, where="static")
    check_headers(client.get("/static/missing"), hsts=True, where="static 404")
    check_headers(client.delete("/"), hsts=True, where="405")
    check_headers(client.get("/status"), hsts=True, where="database down")
    from a11y_stem.web.routes import pages

    def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(pages, "render", boom)
    crashed = TestClient(client.app, base_url="https://beta.example.org").get("/privacy")
    assert crashed.status_code == 500
    check_headers(crashed, hsts=True, where="500")


def test_production_session_cookie_is_secure_httponly_lax_and_host_locked(tmp_path):
    client = TestClient(production_app(tmp_path), base_url="https://beta.example.org")
    response = client.get("/login")
    cookie = response.headers["set-cookie"]
    lowered = cookie.lower()
    assert "secure" in lowered and "httponly" in lowered and "samesite=lax" in lowered
    assert "path=/" in lowered and "domain" not in lowered
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure, has Path=/
    # and no Domain, and keeps a sibling subdomain from setting a cookie of the same name.
    assert cookie.startswith("__Host-"), cookie
    assert SESSION_COOKIE in cookie


def test_the_development_cookie_has_no_prefix_that_would_break_http(web):
    with web.client() as fresh:
        response = fresh.get("/login")
    assert response.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=")
