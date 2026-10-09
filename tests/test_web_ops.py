"""Cron endpoint, health check, admin page, security headers and content-free logs."""

from __future__ import annotations

import logging
from datetime import date

import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from a11y_stem.observability.logging import FIELDS_ATTR
from a11y_stem.ops.retention import PurgeStats
from a11y_stem.web.db import ErrorAggregate, SpendDay, User, session_scope
from a11y_stem.web.security import APP_CSP, SecurityHeadersMiddleware
from fixtures.web.support import (
    ADMIN,
    ALICE,
    SECRETS,
    WebEnv,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)

CRON = "/internal/cron/aggregate-and-purge"
TOKEN = "c" * 40


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.browser = client
        yield env


@pytest.fixture
def ops(monkeypatch):
    """Stand-ins for WP6's aggregate_finished and purge_expired, recording call order."""
    from a11y_stem.ops import aggregate, retention

    calls: list[str] = []
    state = {"aggregate_raises": None}

    def aggregate_finished(factory, now=None) -> int:
        calls.append("aggregate")
        if state["aggregate_raises"]:
            raise state["aggregate_raises"]
        return 4

    def purge_expired(factory, now=None) -> PurgeStats:
        calls.append("purge")
        return PurgeStats(jobs_deleted=3, blobs_deleted=9, unaggregated_deleted=0)

    monkeypatch.setattr(aggregate, "aggregate_finished", aggregate_finished)
    monkeypatch.setattr(retention, "purge_expired", purge_expired)
    return calls, state


# --------------------------------------------------------------------------- cron


@pytest.mark.parametrize("headers", [{}, {"X-Cron-Token": "wrong"}, {"X-Cron-Token": ""}])
def test_cron_refuses_a_missing_or_wrong_token(web, ops, headers):
    response = web.browser.post(CRON, headers=headers)
    assert response.status_code == 403
    assert ops[0] == []


def test_cron_is_disabled_without_a_configured_token(tmp_path, ops):
    env = WebEnv(tmp_path, cron_token=None)
    with env.running() as client:
        assert client.post(CRON, headers={"X-Cron-Token": ""}).status_code == 403
        assert client.post(CRON, headers={"X-Cron-Token": "None"}).status_code == 403


def test_cron_aggregates_then_purges_and_returns_counts(web, ops):
    response = web.browser.post(CRON, headers={"X-Cron-Token": TOKEN})
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "jobs_aggregated": 4,
        "jobs_deleted": 3,
        "blobs_deleted": 9,
        "unaggregated_deleted": 0,
    }
    assert ops[0] == ["aggregate", "purge"]


def test_cron_purges_even_when_aggregation_fails(web, ops, caplog):
    ops[1]["aggregate_raises"] = RuntimeError("SECRET-DOC-TEXT")
    response = web.browser.post(CRON, headers={"X-Cron-Token": TOKEN})
    assert response.status_code == 500
    assert response.json()["status"] == "aggregate_failed"
    assert response.json()["jobs_deleted"] == 3
    assert ops[0] == ["aggregate", "purge"]
    assert "SECRET" not in caplog.text


# --------------------------------------------------------------------------- health


def test_healthz_pings_the_database(web, monkeypatch):
    assert web.browser.get("/healthz").json() == {"status": "ok"}
    from a11y_stem.web.routes import pages

    monkeypatch.setattr(pages, "ping", lambda engine: False)
    response = web.browser.get("/healthz")
    assert response.status_code == 503 and response.json() == {"status": "error"}


# --------------------------------------------------------------------------- admin


def test_admin_page_shows_numbers_but_no_document_content(web):
    web.allow(ALICE)
    login(web.browser, ALICE)
    job_id = job_id_from(upload(web.browser, make_pdf(1), filename="SECRET-FILENAME.pdf"))
    web.run_worker()
    with session_scope(web.ctx.factory) as session:
        session.add(SpendDay(day=date.today(), spent_usd=1.25, held_usd=0.5))
        session.add(
            ErrorAggregate(
                day=date.today(), step="equation", category="status:needs_review", count=7
            )
        )
    admin = web.client()
    login(admin, ADMIN)
    page = admin.get("/admin").text
    for secret in SECRETS:
        assert secret not in page
    assert job_id not in page
    assert "status:needs_review" in page
    assert "$5.00" in page  # the daily cap
    assert ALICE in page and ADMIN in page


def test_admin_can_allow_and_remove_people_but_not_themselves(web):
    login(web.browser, ADMIN)
    csrf = token(web.browser, "/admin")
    response = web.browser.post(
        "/admin/allowlist", data={"csrf_token": csrf, "email": "New@Example.org", "admin": "on"}
    )
    assert response.status_code == 303
    with session_scope(web.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == "new@example.org"))
        assert user is not None and user.is_allowed and user.is_admin

    web.browser.post(
        "/admin/allowlist/remove", data={"csrf_token": csrf, "email": "new@example.org"}
    )
    web.browser.post("/admin/allowlist/remove", data={"csrf_token": csrf, "email": ADMIN})
    with session_scope(web.ctx.factory) as session:
        removed = session.scalar(select(User).where(User.email == "new@example.org"))
        me = session.scalar(select(User).where(User.email == ADMIN))
        assert removed is not None and not removed.is_allowed and not removed.is_admin
        assert me is not None and me.is_allowed
    assert "You cannot remove yourself" in web.browser.get("/admin").text


def test_re_adding_someone_without_the_admin_box_keeps_their_role(web):
    login(web.browser, ADMIN)
    csrf = token(web.browser, "/admin")
    web.browser.post("/admin/allowlist", data={"csrf_token": csrf, "email": ADMIN})
    web.browser.post("/admin/allowlist", data={"csrf_token": csrf, "email": ALICE})
    with session_scope(web.ctx.factory) as session:
        me = session.scalar(select(User).where(User.email == ADMIN))
        alice = session.scalar(select(User).where(User.email == ALICE))
        assert me is not None and me.is_admin  # not demoted
        assert alice is not None and alice.is_allowed and not alice.is_admin
    assert web.browser.get("/admin").status_code == 200


def test_admin_allow_validates_the_email(web):
    login(web.browser, ADMIN)
    response = web.browser.post(
        "/admin/allowlist", data={"csrf_token": token(web.browser, "/admin"), "email": "nope"}
    )
    assert response.status_code == 422
    assert 'href="#allow-email"' in response.text


# --------------------------------------------------------------------------- headers


def test_security_headers_on_app_pages(web):
    response = web.browser.get("/")
    headers = response.headers
    assert headers["content-security-policy"] == (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self' https://accounts.google.com https://login.microsoftonline.com"
    )
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "same-origin"
    assert headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in headers  # only in production
    for path in ("/healthz", "/nope", "/login"):
        assert web.browser.get(path).headers["content-security-policy"] == APP_CSP


def test_static_files_are_cacheable_and_nosniff(web):
    response = web.browser.get("/static/app.css")
    assert response.status_code == 200
    assert "no-store" not in response.headers.get("cache-control", "")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_hsts_is_added_in_production_mode():
    app = Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])
    client = TestClient(SecurityHeadersMiddleware(app, hsts=True))
    assert client.get("/").headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_404_and_405_pages_are_html_and_friendly(web):
    response = web.browser.get("/no-such-page")
    assert response.status_code == 404 and "Page not found" in response.text
    response = web.browser.delete("/")
    assert response.status_code == 405


# --------------------------------------------------------------------------- privacy


def test_document_content_never_reaches_the_logs(tmp_path, monkeypatch, caplog):
    """Upload, convert, review (with an edit) and fail a job, all with SECRET-* content;
    no log record, argument or structured field may contain any of it, or the email."""
    from a11y_stem.pipeline import assemble, speech, validate

    monkeypatch.setattr(validate, "check_latex", lambda latex: [])
    monkeypatch.setattr(validate, "safe_latex_to_mathml", lambda latex, display="block": "<math/>")
    monkeypatch.setattr(validate, "check_mathml", lambda mathml: [])
    monkeypatch.setattr(
        speech, "mathml_to_speech", lambda m: (_ for _ in ()).throw(ValueError("SECRET-DOC-TEXT"))
    )
    monkeypatch.setattr(assemble, "render_html", lambda **kw: "<html>SECRET-DOC-TEXT</html>")
    caplog.set_level(logging.DEBUG)

    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(
            upload(client, make_pdf(1, "SECRET-DOC-TEXT"), filename="SECRET-FILENAME.pdf")
        )
        env.run_worker()
        csrf = token(client, f"/jobs/{job_id}/review")
        client.post(
            f"/jobs/{job_id}/review/p1-e3",
            data={"csrf_token": csrf, "action": "edit", "latex": "\\SECRETLATEX{2}"},
        )
        client.post(
            f"/jobs/{job_id}/review/p1-e4",
            data={"csrf_token": csrf, "action": "edit", "alt": "SECRET-ALT"},
        )
        env.converter.raises = RuntimeError("SECRET-DOC-TEXT in a parser error")
        upload(client, make_pdf(1), filename="SECRET-FILENAME-2.pdf")
        env.run_worker()
        upload(client, b"SECRET-DOC-TEXT not a pdf", filename="SECRET-FILENAME.txt")

    assert caplog.records, "expected some log lines"
    logged = "\n".join(f"{r.getMessage()} {r.args} {r.__dict__}" for r in caplog.records)
    for secret in (*SECRETS, ALICE):
        assert secret not in logged, secret
    for event in ("job_created", "job_done", "review", "job_failed", "upload_rejected"):
        assert event in logged


def test_unhandled_errors_give_a_500_page_and_log_only_the_type(web, monkeypatch, caplog):
    from a11y_stem.web.routes import pages

    def boom(request):
        raise ValueError("SECRET-DOC-TEXT leaked into an exception")

    monkeypatch.setattr(pages, "render", lambda *a, **k: boom(None))
    caplog.set_level(logging.DEBUG)
    response = web.browser.get("/privacy")
    assert response.status_code == 500
    assert "Something went wrong" in response.text and "SECRET" not in response.text
    assert response.headers["content-security-policy"] == APP_CSP
    errors = [r for r in caplog.records if getattr(r, FIELDS_ATTR, {}).get("route")]
    assert errors, "the unhandled error is logged with its route"
    fields = getattr(errors[-1], FIELDS_ATTR)
    assert fields["error_type"] == "builtins.ValueError" and fields["route"] == "/privacy"
    assert "SECRET" not in caplog.text and "SECRET" not in str(fields)


def test_validation_errors_do_not_echo_input(web):
    web.allow(ALICE)
    login(web.browser, ALICE)
    response = web.browser.get(
        "/jobs/" + "0" * 32 + "/status", params={"v": ["a", "SECRET-DOC-TEXT"]}
    )
    assert "SECRET" not in response.text


# --------------------------------------------------------------------------- body size


def test_request_bodies_are_capped_before_any_form_is_parsed(web):
    declared = web.browser.post(
        "/logout",
        content=b"x" * 10,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "content-length": "2000000",
        },
    )
    assert declared.status_code == 413

    def endless():  # chunked, no Content-Length: counted while it streams in
        for _ in range(64):
            yield b"a" * 65536

    streamed = web.browser.post(
        "/logout", content=endless(), headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert streamed.status_code == 413
    assert "Request too large" in streamed.text


def test_the_upload_route_has_its_own_larger_limit(web):
    web.allow(ALICE)
    login(web.browser, ALICE)
    pdf = make_pdf(1)
    padded = pdf + b"\n%" + b"x" * (1024 * 1024 - len(pdf) - 600) + b"\n"
    assert len(padded) <= 1024 * 1024 < len(padded) + 600  # the file fits; the body does not
    response = upload(web.browser, padded)
    assert response.status_code == 303, response.text[:300]
