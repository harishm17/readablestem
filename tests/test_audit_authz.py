"""Launch audit: who may call which route.

The expected table below is the source of the "Routes and who may call them" section of
docs/security.md. A route added without a line here fails the first test, so a new route is
classified (public, user, admin, cron) on purpose. The other tests check the table against
the running app: anonymous visitors, a second user (IDOR) and a non-admin.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from a11y_stem.web.db import (
    Course,
    CourseNotation,
    Job,
    PendingUpload,
    ReviewEdit,
    User,
    session_scope,
)
from fixtures.web.support import ADMIN, ALICE, BOB, WebEnv, csrf_from, job_id_from, login, upload
from fixtures.web.support import make_pdf as pdf

PUBLIC = "public"
USER = "user"
ADMIN_ONLY = "admin"
CRON = "cron"

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/"): PUBLIC,
    ("GET", "/demo"): PUBLIC,
    ("GET", "/privacy"): PUBLIC,
    ("GET", "/terms"): PUBLIC,
    ("GET", "/limits"): PUBLIC,
    ("GET", "/help/posting"): PUBLIC,
    ("GET", "/status"): PUBLIC,
    ("GET", "/healthz"): PUBLIC,
    ("GET", "/readyz"): PUBLIC,  # is the database reachable; no document data
    ("GET", "/login"): PUBLIC,
    ("POST", "/login"): PUBLIC,
    ("POST", "/login/microsoft"): PUBLIC,
    ("GET", "/auth/callback"): PUBLIC,
    ("GET", "/auth/microsoft/callback"): PUBLIC,
    ("GET", "/dev-login"): PUBLIC,  # 404 unless ENV is dev or test and DEV_LOGIN_ENABLED
    ("POST", "/dev-login"): PUBLIC,
    ("POST", "/logout"): PUBLIC,
    ("POST", "/internal/cron/aggregate-and-purge"): CRON,
    ("GET", "/admin"): ADMIN_ONLY,
    ("POST", "/admin/allowlist"): ADMIN_ONLY,
    ("POST", "/admin/allowlist/remove"): ADMIN_ONLY,
    ("GET", "/app"): USER,
    ("GET", "/app/documents"): USER,
    ("GET", "/app/download"): USER,  # T3-4: a zip of the person's own ready documents
    ("POST", "/jobs"): USER,
    ("POST", "/sample"): USER,  # T3-9: CSRF, rate limited; makes a recorded copy for its owner
    ("GET", "/courses"): USER,
    ("POST", "/courses"): USER,
    ("GET", "/courses/{course_id}"): USER,
    ("GET", "/courses/{course_id}/delete"): USER,
    ("GET", "/courses/{course_id}/download"): USER,  # T3-4: a zip of one course's documents
    ("POST", "/courses/{course_id}/delete"): USER,
    ("POST", "/courses/{course_id}/entries"): USER,
    ("GET", "/courses/{course_id}/entries/{entry_id}"): USER,
    ("POST", "/courses/{course_id}/entries/{entry_id}"): USER,
    ("POST", "/courses/{course_id}/entries/{entry_id}/delete"): USER,
    ("POST", "/courses/{course_id}/remember"): USER,
    ("POST", "/courses/{course_id}/rename"): USER,
    ("GET", "/jobs/{job_id}"): USER,
    ("GET", "/jobs/{job_id}/crops/{element_id}.png"): USER,
    ("GET", "/jobs/{job_id}/delete"): USER,
    ("POST", "/jobs/{job_id}/delete"): USER,
    ("GET", "/jobs/{job_id}/download"): USER,
    ("GET", "/jobs/{job_id}/download/canvas"): USER,  # the same file, packaged for Canvas
    ("GET", "/jobs/{job_id}/draft"): USER,
    ("GET", "/jobs/{job_id}/feedback"): USER,
    ("POST", "/jobs/{job_id}/feedback"): USER,
    ("POST", "/jobs/{job_id}/contact"): USER,  # T1-17: CSRF, owner only, not rate limited
    ("POST", "/jobs/{job_id}/keep"): USER,  # T1-8: CSRF, owner only, per-person kept cap
    ("POST", "/jobs/{job_id}/language"): USER,
    ("GET", "/jobs/{job_id}/pages/{number}.png"): USER,
    ("GET", "/jobs/{job_id}/progress"): USER,
    ("GET", "/jobs/{job_id}/regions/{element_id}.png"): USER,
    ("GET", "/jobs/{job_id}/review"): USER,
    ("GET", "/jobs/{job_id}/review/list"): USER,
    ("GET", "/jobs/{job_id}/review/marks.css"): USER,
    ("POST", "/jobs/{job_id}/review/{element_id}"): USER,
    ("POST", "/jobs/{job_id}/review/{element_id}/check"): USER,
    ("GET", "/jobs/{job_id}/status"): USER,
    ("POST", "/jobs/{job_id}/retry"): USER,  # T3-1: CSRF, owner only, rate limited, tries capped
    ("POST", "/jobs/{job_id}/title"): USER,  # T1-7: CSRF, owner only, not rate limited
    ("POST", "/jobs/{job_id}/undo"): USER,
    ("GET", "/jobs/{job_id}/view"): USER,
    # T3-3: a large file kept while its owner chooses the pages; owner only like a job
    ("GET", "/uploads/{upload_id}"): USER,
    ("POST", "/uploads/{upload_id}"): USER,  # CSRF, rate limited, quota and caps as an upload
    ("POST", "/uploads/{upload_id}/delete"): USER,  # asks first (confirm=yes)
}


def all_routes(routes):
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from all_routes(inner.routes)
        elif getattr(route, "methods", None):
            yield route


@pytest.fixture
def site(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True, max_large_upload_mb=5, max_source_pages=40)
    with env.running() as alice:
        env.allow(ALICE)
        env.allow(BOB)
        login(alice, ALICE)
        env.alice = alice
        env.bob = env.client()
        login(env.bob, BOB)
        env.admin = env.client()
        login(env.admin, ADMIN)
        env.anon = env.client()
        job_id = job_id_from(upload(alice, pdf(2)))
        assert env.run_worker() == job_id
        course = alice.post(
            "/courses", data={"csrf_token": csrf_from(alice.get("/courses").text), "name": "P1"}
        )
        course_id = course.headers["location"].rsplit("/", 1)[-1]
        entry_page = f"/courses/{course_id}"
        alice.post(
            f"{entry_page}/entries",
            data={
                "csrf_token": csrf_from(alice.get(entry_page).text),
                "symbol": "v",
                "meaning": "vector",
                "note": "",
            },
        )
        with session_scope(env.ctx.factory) as session:
            entry_id = session.scalar(select(CourseNotation.id))
        kept = upload(alice, pdf(12))  # over the page limit: kept for its owner to choose pages
        upload_id = kept.headers["location"].rsplit("/", 1)[-1]
        env.ids = {
            "upload_id": upload_id,
            "job_id": job_id,
            "course_id": course_id,
            "entry_id": entry_id,
            "element_id": "p1-e3",
            "number": "1",
        }
        yield env
        for client in (env.bob, env.admin, env.anon):
            client.close()


def fill(env, path: str) -> str:
    for key, value in env.ids.items():
        path = path.replace("{" + key + "}", value)
    return path


def counts(env) -> dict[str, int]:
    with session_scope(env.ctx.factory) as session:
        return {
            model.__tablename__: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (Job, Course, CourseNotation, ReviewEdit, PendingUpload, User)
        }


def test_every_route_is_classified(site):
    actual = {
        (method, route.path)
        for route in all_routes(site.app.routes)
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert actual == set(ROUTES), "classify new routes in ROUTES (and docs/security.md)"


def anonymous_response(env, method: str, path: str):
    if method == "GET":
        return env.anon.get(path)
    return env.anon.post(path, data={"csrf_token": "forged", "email": ALICE, "name": "x"})


def test_signed_out_visitors_get_nothing_from_user_and_admin_routes(site):
    before = counts(site)
    for (method, template), who in ROUTES.items():
        if who not in {USER, ADMIN_ONLY}:
            continue
        response = anonymous_response(site, method, fill(site, template))
        assert response.status_code in {303, 401, 403}, (method, template, response.status_code)
        assert "SECRET" not in response.text, (method, template)
    assert counts(site) == before


def test_non_admins_cannot_use_admin_routes_even_with_a_valid_token(site):
    token = csrf_from(site.bob.get("/app").text)
    assert site.bob.get("/admin").status_code == 403
    for path in ("/admin/allowlist", "/admin/allowlist/remove"):
        data = {"csrf_token": token, "email": "mallory@example.org", "admin": "on"}
        assert site.bob.post(path, data=data).status_code == 403, path
    with session_scope(site.ctx.factory) as session:
        assert session.scalar(select(User).where(User.email == "mallory@example.org")) is None


def test_cron_needs_its_token(site):
    path = "/internal/cron/aggregate-and-purge"
    assert site.anon.post(path).status_code == 403
    assert site.anon.post(path, headers={"X-Cron-Token": "wrong"}).status_code == 403
    assert site.alice.post(path).status_code == 403  # a session is not a token
    assert site.admin.post(path).status_code == 403
    assert site.anon.get(path).status_code == 405
    assert site.anon.post(path, headers={"X-Cron-Token": "c" * 40}).status_code == 200


def other_user_requests(env):
    """Every route that names a job, course or entry, as Bob, with Alice's real ids and
    again with ids that do not exist."""
    token = csrf_from(env.bob.get("/app").text)
    missing = {key: ("0" * 32 if key.endswith("_id") and key != "element_id" else value)
               for key, value in env.ids.items()}  # fmt: skip
    for (method, template), who in ROUTES.items():
        if who != USER or "{" not in template:
            continue
        real, absent = fill(env, template), template
        for key, value in missing.items():
            absent = absent.replace("{" + key + "}", value)
        yield method, template, real, absent, token


def send(client, method: str, path: str, token: str):
    if method == "GET":
        return client.get(path)
    fields = {
        "csrf_token": token,
        "action": "reject",
        "alt": "x",
        "name": "x",
        "symbol": "v",
        "meaning": "vector",
        "note": "",
        "confirm": "yes",
        "lang": "fr",
        "latex": "x",
        "item": "p1-e3",
    }
    return client.post(path, data=fields)


def test_a_second_user_gets_the_same_404_for_someone_elses_ids_as_for_ids_that_do_not_exist(site):
    """No IDOR and no oracle: every route that names a job, course or entry answers a
    stranger exactly as it answers a made-up id, and changes nothing."""
    before = counts(site)
    seen = 0
    for method, template, real, absent, token in other_user_requests(site):
        theirs = send(site.bob, method, real, token)
        nothing = send(site.bob, method, absent, token)
        assert theirs.status_code == nothing.status_code, (method, template)
        assert theirs.status_code in {400, 404}, (method, template, theirs.status_code)
        assert "SECRET" not in theirs.text and "P1" not in theirs.text, (method, template)
        seen += 1
    assert seen >= 25
    assert counts(site) == before
    assert site.job(site.ids["job_id"]).status == "done"


def test_the_owner_can_use_the_same_routes(site):
    """The control for the test above: Alice reaches her own job page, review and course."""
    for path in ("/jobs/{job_id}", "/jobs/{job_id}/status", "/jobs/{job_id}/review"):
        assert site.alice.get(fill(site, path)).status_code == 200, path
    assert site.alice.get(fill(site, "/courses/{course_id}")).status_code == 200


def test_an_admin_is_not_an_owner(site):
    token = csrf_from(site.admin.get("/admin").text)
    for method, template, real, _absent, _token in other_user_requests(site):
        response = send(site.admin, method, real, token)
        assert response.status_code in {400, 404}, (method, template, response.status_code)
        assert "SECRET" not in response.text, (method, template)
