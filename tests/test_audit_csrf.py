"""Launch audit: CSRF, session fixation and GET safety, on every route of the app.

The route list is `test_audit_authz.ROUTES`, which fails when a route is added without a
classification, so a new state-changing route cannot skip these checks.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from a11y_stem.web.db import Course, CourseNotation, Job, ReviewEdit, User, session_scope
from fixtures.web.support import ALICE, WebEnv, csrf_from, login, make_pdf
from test_audit_authz import CRON, ROUTES, fill, site  # noqa: F401  (site is a fixture)

STATE_CHANGING = [
    (method, path)
    for (method, path), who in ROUTES.items()
    if method == "POST" and who != CRON and path not in {"/login", "/login/microsoft"}
]


def snapshot(env) -> dict:
    """Everything a forged request could change that the owner would notice."""
    with session_scope(env.ctx.factory) as session:
        return {
            "jobs": sorted(
                (j.id, j.status, j.result_version, j.course_id, j.retain_until.isoformat())
                for j in session.scalars(select(Job))
            ),
            "courses": sorted((c.id, c.name) for c in session.scalars(select(Course))),
            "notation": sorted(
                (n.id, n.symbol, n.meaning, n.note) for n in session.scalars(select(CourseNotation))
            ),
            "edits": session.query(ReviewEdit).count(),
            "users": sorted(
                (u.email, u.is_allowed, u.is_admin, u.session_version)
                for u in session.scalars(select(User))
            ),
        }


def forged_attempts(env, path: str):
    """Ways a page on another site (or a script) could try to post without the token."""
    alice = env.alice
    bobs_token = csrf_from(env.bob.get("/app").text)
    fields = {"email": "mallory@example.org", "name": "x", "action": "reject", "confirm": "yes"}
    yield "no token", alice.post(path, data=fields)
    yield "empty token", alice.post(path, data=fields | {"csrf_token": ""})
    yield "another session's token", alice.post(path, data=fields | {"csrf_token": bobs_token})
    yield (
        "token in the query string only",
        alice.post(path, params={"csrf_token": csrf_from(alice.get("/app").text)}, data=fields),
    )
    yield (
        "token in a header only",
        alice.post(path, data=fields, headers={"X-CSRF-Token": csrf_from(alice.get("/app").text)}),
    )
    yield (
        "json body",
        alice.post(
            path,
            content=json.dumps(fields | {"csrf_token": csrf_from(alice.get("/app").text)}),
            headers={"Content-Type": "application/json"},
        ),
    )


def test_every_state_changing_route_refuses_a_request_without_the_session_token(site):  # noqa: F811
    before = snapshot(site)
    for _method, template in STATE_CHANGING:
        for how, response in forged_attempts(site, fill(site, template)):
            if template == "/jobs":
                continue  # multipart: covered below
            assert response.status_code == 403, (template, how, response.status_code)
    assert snapshot(site) == before


def test_the_upload_refuses_a_forged_token_after_reading_nothing_it_keeps(site):  # noqa: F811
    before = snapshot(site)
    files = {"pdf": ("a.pdf", make_pdf(1), "application/pdf")}
    for data in ({}, {"csrf_token": ""}, {"csrf_token": csrf_from(site.bob.get("/app").text)}):
        response = site.alice.post("/jobs", data=data, files=files)
        assert response.status_code == 403, data
    assert snapshot(site) == before


def test_get_requests_change_nothing(site):  # noqa: F811
    """GET is not protected by the token, so no GET may change what a person would notice."""
    before = snapshot(site)
    for (method, template), who in ROUTES.items():
        if method != "GET" or who not in {"user", "public"}:
            continue
        site.alice.get(fill(site, template))
    for path in ("/jobs/{job_id}/delete", "/courses/{course_id}/delete"):
        assert site.alice.get(fill(site, path)).status_code == 200  # asks first
    assert snapshot(site) == before


def test_a_token_from_before_signing_in_is_worthless_afterwards(tmp_path):
    """Session fixation: an attacker who plants their own pre-login cookie (and so knows its
    token) gets nothing, because signing in starts a new session with a new token."""
    env = WebEnv(tmp_path)
    with env.running() as attacker:
        env.allow(ALICE)
        planted = csrf_from(attacker.get("/dev-login").text)
        victim = env.client()
        for name, value in attacker.cookies.items():
            victim.cookies.set(name, value)  # the planted cookie reaches the victim's browser
        assert login(victim, ALICE).status_code == 303
        assert attacker.get("/app").status_code == 303  # the planted cookie is not signed in
        forged = victim.post("/logout", data={"csrf_token": planted})
        assert forged.status_code == 403
        assert victim.get("/app").status_code == 200  # still signed in
        victim.close()


def test_a_session_cookie_cannot_be_forged_or_altered(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        good = next(iter(client.cookies.jar))
        for tampered in (good.value[:-2] + "xx", "x" + good.value, "", "e30=.AAAA.BBBB"):
            other = env.client()
            other.cookies.set(good.name, tampered)
            assert other.get("/app").status_code == 303, tampered
            other.close()
