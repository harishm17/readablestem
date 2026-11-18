"""Launch audit: who may call which route.

The expected table below is the source of the "Routes and who may call them" section of
docs/security.md. A route added without a line here fails the first test, so a new route is
classified (public, user, admin, cron) on purpose. The other tests check the table against
the running app: anonymous visitors, a second user (IDOR) and a non-admin.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from fixtures.web.support import ADMIN, ALICE, BOB, WebEnv, csrf_from, job_id_from, login, upload
from fixtures.web.support import make_pdf as pdf
from readablestem import web as web_package
from readablestem.web.db import (
    Course,
    CourseNotation,
    Job,
    PendingUpload,
    ReviewEdit,
    User,
    session_scope,
)

PUBLIC = "public"
USER = "user"
ADMIN_ONLY = "admin"
CRON = "cron"

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/"): PUBLIC,
    ("GET", "/demo"): PUBLIC,
    ("GET", "/demo/listen"): PUBLIC,  # the stored demo's listening transcript; no user data
    ("GET", "/demo/source.pdf"): PUBLIC,  # page 3 of the demo's OpenStax source, CC BY 4.0
    ("GET", "/style"): PUBLIC,
    ("GET", "/privacy"): PUBLIC,
    ("GET", "/terms"): PUBLIC,
    ("GET", "/limits"): PUBLIC,
    ("GET", "/pricing"): PUBLIC,
    ("GET", "/security"): PUBLIC,
    ("GET", "/for-accessibility-offices"): PUBLIC,
    ("GET", "/for-accessibility-offices/summary"): PUBLIC,
    ("GET", "/help/posting"): PUBLIC,
    ("GET", "/guides"): PUBLIC,
    ("GET", "/guides/ada-title-ii-stem-course-materials"): PUBLIC,
    ("GET", "/guides/readablestem-mathpix-latexml"): PUBLIC,
    ("GET", "/status"): PUBLIC,
    ("GET", "/healthz"): PUBLIC,
    ("GET", "/readyz"): PUBLIC,  # is the database reachable; no document data
    ("GET", "/healthz/ops"): PUBLIC,  # the status word only
    # Crawler files and the static showcase (routes/discovery.py): no user data.
    ("GET", "/robots.txt"): PUBLIC,
    ("GET", "/sitemap.xml"): PUBLIC,
    ("GET", "/llms.txt"): PUBLIC,
    ("GET", "/18149cf3a15bfa791042a3df7fdc0388.txt"): PUBLIC,  # IndexNow key
    ("GET", "/favicon.ico"): PUBLIC,
    ("GET", "/showcase"): PUBLIC,
    ("GET", "/showcase/{rel:path}"): PUBLIC,
    ("GET", "/login"): PUBLIC,
    ("POST", "/login"): PUBLIC,
    ("POST", "/login/microsoft"): PUBLIC,
    (
        "POST",
        "/login/email",
    ): PUBLIC,  # T3-10: 404 unless EMAIL_LOGIN_ENABLED; same page for every address
    ("GET", "/login/email/confirm"): PUBLIC,  # shows a button; opening the link uses nothing up
    ("POST", "/login/email/confirm"): PUBLIC,  # CSRF-checked; the token is single-use
    ("GET", "/auth/callback"): PUBLIC,
    ("GET", "/auth/microsoft/callback"): PUBLIC,
    ("GET", "/dev-login"): PUBLIC,  # 404 unless ENV is dev or test and DEV_LOGIN_ENABLED
    ("POST", "/dev-login"): PUBLIC,
    ("POST", "/logout"): PUBLIC,
    ("POST", "/internal/cron/aggregate-and-purge"): CRON,
    ("GET", "/admin"): ADMIN_ONLY,
    ("GET", "/admin/reports"): ADMIN_ONLY,  # SW25: problem reports; ids, codes and typed text
    ("POST", "/admin/reports/{report_id}/close"): ADMIN_ONLY,
    ("GET", "/admin/health"): ADMIN_ONLY,  # stale task names, storage, sandbox probe
    ("POST", "/admin/allowlist"): ADMIN_ONLY,
    ("POST", "/admin/allowlist/remove"): ADMIN_ONLY,
    ("POST", "/admin/users/block"): ADMIN_ONLY,  # open sign-up: stop an account
    ("POST", "/admin/users/unblock"): ADMIN_ONLY,
    ("POST", "/admin/linked-emails"): ADMIN_ONLY,  # T3-10: another Google account per instructor
    ("POST", "/admin/linked-emails/remove"): ADMIN_ONLY,
    ("GET", "/account"): USER,  # open sign-up: what is held, the limits, delete my account
    ("GET", "/account/delete"): USER,
    ("POST", "/account/delete"): USER,  # CSRF; erases the caller's own account only
    ("POST", "/account/keep-default"): USER,  # CSRF; the caller's own setting only
    ("POST", "/account/name"): USER,  # CSRF; the caller's own name only
    ("GET", "/account/export"): USER,  # the caller's own documents, records, courses, settings
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
    ("POST", "/courses/{course_id}/suggestions"): USER,  # T3-12: CSRF, owner of course (and job)
    ("POST", "/courses/{course_id}/starter"): USER,  # CSRF, owner of the course; adds ticked rows
    ("POST", "/courses/{course_id}/documents"): USER,  # CSRF, owner of course and of each document
    ("POST", "/courses/{course_id}/rename"): USER,
    ("GET", "/jobs/{job_id}"): USER,
    ("GET", "/jobs/{job_id}/crops/{element_id}.png"): USER,
    ("GET", "/jobs/{job_id}/delete"): USER,
    ("POST", "/jobs/{job_id}/delete"): USER,
    ("GET", "/jobs/{job_id}/download"): USER,
    ("GET", "/jobs/{job_id}/download/canvas"): USER,  # the same file, packaged for Canvas
    ("GET", "/jobs/{job_id}/download/cartridge"): USER,  # T3-8: the same file as a cartridge
    ("GET", "/jobs/{job_id}/download.docx"): USER,  # W3: the same file as Word
    ("GET", "/jobs/{job_id}/download.epub"): USER,  # W3: the same file as an EPUB
    ("GET", "/jobs/{job_id}/combine"): USER,  # T3-3: the finished parts of a source, joined
    ("GET", "/jobs/{job_id}/combine/parts"): USER,  # the parts of a source and what each needs
    ("GET", "/jobs/{job_id}/draft"): USER,
    ("POST", "/jobs/{job_id}/draft"): USER,  # SW21: the draft page's confirm
    ("GET", "/jobs/{job_id}/report"): USER,  # SW25: review right; the problem-report form
    ("POST", "/jobs/{job_id}/report"): USER,  # CSRF, review right, 5 per 10 minutes per person
    ("GET", "/jobs/{job_id}/feedback"): USER,
    ("POST", "/jobs/{job_id}/feedback"): USER,
    ("POST", "/jobs/{job_id}/contact"): USER,  # T1-17: CSRF, owner only, not rate limited
    ("POST", "/jobs/{job_id}/keep"): USER,  # T1-8: CSRF, owner only, per-person kept cap
    ("POST", "/jobs/{job_id}/language"): USER,
    ("GET", "/jobs/{job_id}/pages/{number}.png"): USER,
    # T3-16: leave a page out of review and of the student file, and put it back. CSRF,
    # owner only, review version checked; not rate limited (like a decision).
    ("POST", "/jobs/{job_id}/pages/{number}/omit"): USER,
    ("POST", "/jobs/{job_id}/pages/{number}/restore"): USER,
    # T3-5: change what the tool did not propose. CSRF, owner only, review version checked.
    # Adding a region is rate limited (the crop; and the model's read of an equation, which
    # is also one at a time per person and under the job's and the day's spend caps).
    ("POST", "/jobs/{job_id}/edit/{element_id}"): USER,  # the words; a paragraph made a heading
    ("POST", "/jobs/{job_id}/pages/{number}/add"): USER,  # rate limited, spend capped
    ("GET", "/jobs/{job_id}/pages/{number}/region.png"): USER,  # the region as it would be cut
    # T3-11: accept the clean tables in one write; read a table's maths cell with the model
    # (rate limited, one at a time, spend capped).
    ("POST", "/jobs/{job_id}/tables/accept-clean"): USER,
    # Accept many at once: the list of what would be accepted, and the decision. CSRF, owner
    # only, review version checked.
    ("GET", "/jobs/{job_id}/accept/{slug}"): USER,
    ("POST", "/jobs/{job_id}/accept/{slug}"): USER,
    ("POST", "/jobs/{job_id}/tables/{element_id}/read-cell"): USER,
    ("GET", "/jobs/{job_id}/progress"): USER,
    ("GET", "/jobs/{job_id}/record"): USER,  # T3-6: owner only, changes nothing
    ("GET", "/jobs/{job_id}/record.csv"): USER,  # T3-6
    ("GET", "/jobs/{job_id}/regions/{element_id}.png"): USER,
    ("GET", "/jobs/{job_id}/review"): USER,
    ("GET", "/jobs/{job_id}/review/list"): USER,
    ("GET", "/jobs/{job_id}/review/marks.css"): USER,
    ("POST", "/jobs/{job_id}/review/{element_id}"): USER,
    ("POST", "/jobs/{job_id}/review/{element_id}/check"): USER,
    ("GET", "/jobs/{job_id}/status"): USER,
    ("POST", "/jobs/{job_id}/retry"): USER,  # T3-1: CSRF, owner only, rate limited, tries capped
    ("POST", "/jobs/{job_id}/cancel"): USER,  # WS11.3a: CSRF, owner only, rate limited
    ("POST", "/jobs/{job_id}/title"): USER,
    # H11: the Symbols screen; the POST is a review write (CSRF, owner only, version-checked)
    ("GET", "/jobs/{job_id}/symbols"): USER,
    ("POST", "/jobs/{job_id}/symbols"): USER,  # T1-7: CSRF, owner only, not rate limited
    ("POST", "/jobs/{job_id}/undo"): USER,
    ("GET", "/jobs/{job_id}/view"): USER,
    # Listen as a student: the transcript of what a screen reader says. Read-only (no POST),
    # access `view`: the owner and anyone it is shared with; a stranger gets the 404.
    ("GET", "/jobs/{job_id}/listen"): USER,
    # G1: who has access to a document. The owner only; a person it is shared with gets a 403,
    # a stranger the same 404 as for a made-up id (tests/test_job_shares.py has the matrix).
    ("GET", "/jobs/{job_id}/share"): USER,
    ("POST", "/jobs/{job_id}/share"): USER,  # CSRF, owner only
    ("POST", "/jobs/{job_id}/share/{share_id}/revoke"): USER,  # CSRF, owner only
    # T3-3: a large file kept while its owner chooses the pages; owner only like a job
    ("GET", "/uploads/{upload_id}"): USER,
    ("GET", "/uploads/{upload_id}/preview"): USER,  # owner only: the parts list for typed pages
    ("POST", "/uploads/{upload_id}"): USER,  # CSRF, rate limited, quota and caps as an upload
    ("POST", "/uploads/{upload_id}/delete"): USER,  # asks first (confirm=yes)
    # T3-4: the answer to an upload of several files, a page of its own for an hour: the batch
    # page (live status, "Download all"); the old address redirects to it
    ("GET", "/app/upload/{result_id}"): USER,
    ("GET", "/app/batch/{result_id}"): USER,
    ("GET", "/app/batch/{result_id}/download"): USER,
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
    env = WebEnv(
        tmp_path,
        feature_notation_memory=True,
        email_login_enabled=True,
        email_sender="outbox",
        max_large_upload_mb=5,
        max_source_pages=40,
    )
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
            "/courses",
            data={"csrf_token": csrf_from(alice.get("/courses").text), "name": "SECRET-COURSE"},
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
        several = alice.post(
            "/jobs",
            data={"csrf_token": csrf_from(alice.get("/app").text)},
            files=[
                ("pdf", ("a.pdf", pdf(1), "application/pdf")),
                ("pdf", ("b.pdf", b"not a pdf", "application/pdf")),
            ],
        )
        assert several.status_code == 303 and "/app/batch/" in several.headers["location"]
        result_id = several.headers["location"].rsplit("/", 1)[-1]
        # The old address of that page still leads its owner to it.
        assert alice.get(f"/app/upload/{result_id}").headers["location"] == (
            f"/app/batch/{result_id}"
        )
        env.ids = {
            "upload_id": upload_id,
            "result_id": result_id,
            "job_id": job_id,
            "course_id": course_id,
            "entry_id": entry_id,
            "share_id": "1" * 32,
            "report_id": "1",
            "element_id": "p1-e3",
            "slug": "equations",
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
        # The live LaTeX check answers a signed-out session with an empty 204 (F-088).
        quiet = response.status_code == 204 and response.text == ""
        assert quiet or response.status_code in {303, 401, 403}, (
            method,
            template,
            response.status_code,
        )
        assert "SECRET" not in response.text, (method, template)
    assert counts(site) == before


def test_non_admins_cannot_use_admin_routes_even_with_a_valid_token(site):
    token = csrf_from(site.bob.get("/app").text)
    assert site.bob.get("/admin").status_code == 403
    for path in (
        "/admin/allowlist",
        "/admin/allowlist/remove",
        "/admin/users/block",
        "/admin/users/unblock",
        "/admin/linked-emails",
        "/admin/linked-emails/remove",
    ):
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
        "email": "someone@example.org",
        "role": "view",
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
        # The course is named SECRET-COURSE: a short marker like "P1" also turns up by chance in
        # random tokens (the sign-out form's CSRF token) and made this test flaky.
        assert "SECRET" not in theirs.text, (method, template)
        seen += 1
    assert seen >= 25
    assert counts(site) == before
    assert site.job(site.ids["job_id"]).status == "done"


def test_an_admin_is_not_an_owner(site):
    token = csrf_from(site.admin.get("/admin").text)
    for method, template, real, _absent, _token in other_user_requests(site):
        response = send(site.admin, method, real, token)
        assert response.status_code in {400, 404}, (method, template, response.status_code)
        assert "SECRET" not in response.text, (method, template)


# --------------------------------------------------------------------------- partial audiences

TEMPLATES = Path(web_package.__file__).parent / "templates"
EVERYONE = frozenset({"owner", "reviewer", "reader"})
# The pages that include partials, and who is ever given each (`views.audience_of`). A reader
# never gets `job.html`; the review templates are for a Review right or more.
AUDIENCE_ROOTS = {
    "job.html": frozenset({"owner", "reviewer"}),
    "job_reader.html": frozenset({"reader"}),
    "listen.html": EVERYONE,
    "_job_status.html": EVERYONE,  # the job page's poll answers, rendered by their own routes
    "_job_progress.html": EVERYONE,
    **{path.name: frozenset({"owner", "reviewer"}) for path in TEMPLATES.glob("review*.html")},
}
_TAG = re.compile(r"\{%-?\s*(if|elif|else|endif|include|from)\b(.*?)-?%\}", re.S)


def _narrowed(condition: str, reach: frozenset[str]) -> frozenset[str] | None:
    """The audiences an `if` keeps, when it says who is reading (None: it does not)."""
    if " or " in f" {condition} ":
        return None
    kept = reach
    known = False
    if "not job.is_owner" in condition:
        kept, known = kept & {"reviewer", "reader"}, True
    elif "job.is_owner" in condition or re.search(r"\bowns\b", condition):
        kept, known = kept & {"owner"}, True
    if "not job.can_review" in condition:
        kept, known = kept & {"reader"}, True
    elif "job.can_review" in condition:
        kept, known = kept & {"owner", "reviewer"}, True
    if match := re.search(r'job\.audience (==|!=) "(\w+)"', condition):
        same = frozenset({match.group(2)})
        kept, known = (kept & same if match.group(1) == "==" else kept - same), True
    return frozenset(kept) if known else None


def _reached(name: str, reach: frozenset[str], found: dict[str, set[str]]) -> None:
    """Add to `found` every partial that `name` includes, and the audiences that reach it:
    those of the page, narrowed by the `if` around the include when that says who it is for."""
    # Each open `if`: the audiences before it, whether it was a plain who-is-reading test,
    # and the audiences it kept.
    stack: list[tuple[frozenset[str], bool, frozenset[str]]] = []
    here = reach
    for tag in _TAG.finditer((TEMPLATES / name).read_text(encoding="utf-8")):
        word, rest = tag.group(1), tag.group(2)
        if word == "if":
            kept = _narrowed(rest, here)
            here_in = here if kept is None else kept
            stack.append((here, kept is not None and " and " not in rest, here_in))
            here = here_in
        elif word == "elif" and stack:
            before = stack[-1][0]
            kept = _narrowed(rest, before)
            here = before if kept is None else kept
            stack[-1] = (before, False, here)
        elif word == "else" and stack:
            before, plain, kept = stack[-1]
            here = before - kept if plain else before
        elif word == "endif" and stack:
            here = stack.pop()[0]
        elif word in ("include", "from") and (target := re.match(r"\s*[\"']([^\"']+)[\"']", rest)):
            partial = target.group(1)
            if partial.startswith("components/"):  # building blocks that say nothing to anyone
                continue
            if not here <= found.get(partial, set()):
                found.setdefault(partial, set()).update(here)
                _reached(partial, here, found)


def partial_audiences() -> dict[str, set[str]]:
    """Every partial that the pages above include or import macros from, directly or through
    another, with the audiences that are ever given it."""
    found: dict[str, set[str]] = {}
    for name, reach in AUDIENCE_ROOTS.items():
        if name.startswith("_"):  # a partial that a route renders on its own
            found.setdefault(name, set()).update(reach)
        _reached(name, reach, found)
    return found


def test_a_partial_is_reached_only_by_the_audiences_it_names():
    """SW16: each partial starts with `{# Audiences: owner, reviewer #}`, and the include
    tree of the job page, the reader page, Listen and the review pages never gives it to
    anyone it does not name. A partial written for the owner is not included, unguarded, in a
    page that a reviewer or a reader gets."""
    faults = []
    for partial, reached in sorted(partial_audiences().items()):
        first = (TEMPLATES / partial).read_text(encoding="utf-8").splitlines()[0]
        named = re.fullmatch(r"\{#\s*Audiences:\s*([a-z, ]+?)\s*#\}", first)
        said = set(re.split(r",\s*", named.group(1))) if named else set()
        if not said <= EVERYONE or not reached <= said:
            faults.append(f"{partial}: reached by {sorted(reached)}, names {sorted(said) or '-'}")
    assert not faults, "\n".join(faults)
