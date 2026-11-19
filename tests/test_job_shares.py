"""G1: sharing a document with named, signed-in people (web/shares.py, routes/sharing.py,
`routes.jobs.job_access`), and WS27.1: the same two rights given for a whole course.

The matrix test runs every route that names a job against eight people: the owner, someone it was
shared with to view, someone it was shared with to review, someone whose share was removed, a
stranger, and three people the owner of the document's course gave access to the whole course (to
view, to review, and one whose sign-in did not prove the address). A course grant must answer
exactly as the share of the same right does. The rest check the rules one by one: the exact,
case-insensitive address match, that a removed share works at once, that a shared person cannot
share further or delete, who is recorded for a review edit, and what the owner's quota, clock
and deletion do."""

from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    fetch,
    job_id_from,
    login,
    make_pdf,
    upload,
)
from readablestem.core.types import Status
from readablestem.ops import retention as purge
from readablestem.pipeline import export_docx
from readablestem.web import auth, shares
from readablestem.web.database import as_utc
from readablestem.web.db import (
    Course,
    CourseGrant,
    Job,
    JobLink,
    JobShare,
    JobStatus,
    ReviewEdit,
    User,
    session_scope,
    utcnow,
)
from readablestem.web.security import VIEW_CSP
from readablestem.web.views import audience_of
from test_audit_authz import ROUTES

CAROL = "carol@example.org"  # a viewer
DAVE = "dave@example.org"  # a reviewer
ERIN = "erin@example.org"  # shared, then removed
FRANK = "frank@example.org"  # given the whole course, to view
GINA = "gina@example.org"  # given the whole course, to review
HANK = "hank@example.org"  # given the whole course, to review, but signs in without proof
ANSWER = {"is_equation": True, "latex": "x^2", "confidence": 0.99}
OWNER, VIEWER, REVIEWER, REVOKED, STRANGER = "owner", "viewer", "reviewer", "revoked", "stranger"
COURSE_VIEW, COURSE_REVIEW, COURSE_UNPROVEN = "course_view", "course_review", "course_unproven"
ACTORS = (OWNER, VIEWER, REVIEWER, REVOKED, STRANGER, COURSE_VIEW, COURSE_REVIEW, COURSE_UNPROVEN)
# What each person who has a right has, and who has none (a course grant is as good as the
# share of the same right, and a grant on an unproved sign-in is as good as nothing).
RIGHT = {VIEWER: "view", REVIEWER: "review", COURSE_VIEW: "view", COURSE_REVIEW: "review"}
NOBODY = (REVOKED, STRANGER, COURSE_UNPROVEN)
SAME_AS = {COURSE_VIEW: VIEWER, COURSE_REVIEW: REVIEWER, COURSE_UNPROVEN: STRANGER}

# What each route that names a job needs (`job_access`). Every such route in the app is here:
# `test_every_job_route_has_a_stated_need` fails for one that is not.
NEEDS: dict[tuple[str, str], str] = {
    ("GET", "/jobs/{job_id}"): "view",
    ("GET", "/jobs/{job_id}/status"): "view",
    ("GET", "/jobs/{job_id}/progress"): "view",
    ("GET", "/jobs/{job_id}/record"): "view",
    ("GET", "/jobs/{job_id}/record.csv"): "view",
    # The site's document has decisions open, so it is a draft: a person who can only view is
    # never given one, nor shown or read it (`web.output`); a ready document's need only "view".
    ("GET", "/jobs/{job_id}/view"): "review",
    ("GET", "/jobs/{job_id}/listen"): "review",
    ("GET", "/jobs/{job_id}/report"): "review",
    ("POST", "/jobs/{job_id}/report"): "review",
    ("GET", "/jobs/{job_id}/draft"): "review",
    ("POST", "/jobs/{job_id}/draft"): "review",
    ("GET", "/jobs/{job_id}/download"): "review",
    ("GET", "/jobs/{job_id}/download/canvas"): "review",
    ("GET", "/jobs/{job_id}/download/cartridge"): "review",
    ("GET", "/jobs/{job_id}/download.docx"): "review",
    ("GET", "/jobs/{job_id}/download.epub"): "review",
    ("GET", "/jobs/{job_id}/crops/{element_id}.png"): "view",
    ("GET", "/jobs/{job_id}/feedback"): "owner",
    ("POST", "/jobs/{job_id}/feedback"): "owner",
    ("GET", "/jobs/{job_id}/combine"): "owner",
    ("GET", "/jobs/{job_id}/combine/parts"): "owner",
    ("GET", "/jobs/{job_id}/delete"): "owner",
    ("POST", "/jobs/{job_id}/delete"): "owner",
    ("POST", "/jobs/{job_id}/retry"): "owner",
    ("POST", "/jobs/{job_id}/cancel"): "owner",
    ("POST", "/jobs/{job_id}/language"): "owner",
    ("POST", "/jobs/{job_id}/cut-off"): "owner",
    ("POST", "/jobs/{job_id}/title"): "owner",
    ("POST", "/jobs/{job_id}/contact"): "owner",
    ("POST", "/jobs/{job_id}/keep"): "owner",
    ("GET", "/jobs/{job_id}/share"): "owner",
    ("POST", "/jobs/{job_id}/share"): "owner",
    ("POST", "/jobs/{job_id}/share/{share_id}/revoke"): "owner",
    ("POST", "/jobs/{job_id}/share/link"): "owner",
    ("POST", "/jobs/{job_id}/share/link/pause"): "owner",
    ("POST", "/jobs/{job_id}/share/link/resume"): "owner",
    ("GET", "/jobs/{job_id}/share/link/remove"): "owner",
    ("POST", "/jobs/{job_id}/share/link/remove"): "owner",
    ("POST", "/jobs/{job_id}/share/link/update"): "owner",
    ("GET", "/jobs/{job_id}/review"): "review",
    ("GET", "/jobs/{job_id}/review/list"): "review",
    ("GET", "/jobs/{job_id}/review/marks.css"): "review",
    ("GET", "/jobs/{job_id}/pages/{number}.png"): "review",
    ("GET", "/jobs/{job_id}/regions/{element_id}.png"): "review",
    ("GET", "/jobs/{job_id}/pages/{number}/region.png"): "review",
    ("GET", "/jobs/{job_id}/symbols"): "review",
    ("GET", "/jobs/{job_id}/accept/{slug}"): "review",
    ("POST", "/jobs/{job_id}/accept/{slug}"): "review",
    ("POST", "/jobs/{job_id}/symbols"): "review",
    ("POST", "/jobs/{job_id}/undo"): "review",
    ("POST", "/jobs/{job_id}/review/{element_id}"): "review",
    ("POST", "/jobs/{job_id}/review/{element_id}/check"): "review",
    ("POST", "/jobs/{job_id}/edit/{element_id}"): "review",
    ("POST", "/jobs/{job_id}/pages/{number}/omit"): "review",
    ("POST", "/jobs/{job_id}/pages/{number}/restore"): "review",
    ("POST", "/jobs/{job_id}/pages/{number}/add"): "review",
    ("POST", "/jobs/{job_id}/tables/accept-clean"): "review",
    ("POST", "/jobs/{job_id}/tables/{element_id}/read-cell"): "review",
}
RANK = shares.RANK


def _settled(env) -> None:
    """Leave nothing to decide, so the document is ready and every download is open."""
    for fix in env.converter.output.result.fixes:
        if fix.pending:
            fix.status = Status.REJECTED if fix.status is Status.ABSTAINED else Status.ACCEPTED
            fix.reviewed = True


@pytest.fixture
def site(tmp_path):
    yield from _site(tmp_path, ready=False)


@pytest.fixture
def ready_site(tmp_path):
    yield from _site(tmp_path, ready=True)


def _site(tmp_path, *, ready: bool):
    env = WebEnv(
        tmp_path,
        feature_class_link=True,
        feature_notation_memory=True,
        feature_course_grants=True,
    )
    with env.running() as owner:
        for email in (ALICE, BOB, CAROL, DAVE, ERIN, FRANK, GINA, HANK):
            env.allow(email)
        login(owner, ALICE)
        env.who = {OWNER: owner}
        people = (
            (VIEWER, CAROL),
            (REVIEWER, DAVE),
            (REVOKED, ERIN),
            (STRANGER, BOB),
            (COURSE_VIEW, FRANK),
            (COURSE_REVIEW, GINA),
        )
        for actor, email in people:
            client = env.client()
            login(client, email)
            env.who[actor] = client
        client = env.client()
        with patch.object(auth, "_is_account_address", return_value=False):
            login(client, HANK)  # a sign-in that did not prove the address (as an older session)
        env.who[COURSE_UNPROVEN] = client
        env.job_id = job_id_from(upload(owner, make_pdf(2)))
        if ready:
            _settled(env)
        assert env.run_worker() == env.job_id
        give(env, CAROL, "view")
        give(env, DAVE, "review")
        give(env, ERIN, "review")
        assert remove(env, ERIN).status_code == 303
        make_course(env)
        yield env
        for actor, client in env.who.items():
            if actor != OWNER:
                client.close()


def page_token(client, path: str = "/app") -> str:
    return csrf_from(client.get(path).text)


def give(env, email: str, role: str, *, raw: str | None = None):
    owner = env.who[OWNER]
    return owner.post(
        f"/jobs/{env.job_id}/share",
        data={
            "csrf_token": page_token(owner, f"/jobs/{env.job_id}/share"),
            "email": raw if raw is not None else email,
            "role": role,
        },
    )


def make_course(env, name: str = "Physics 1") -> None:
    """The owner's course holding the document, with FRANK (view), GINA and HANK (review)."""
    owner = env.who[OWNER]
    made = owner.post("/courses", data={"csrf_token": page_token(owner, "/courses"), "name": name})
    assert made.status_code == 303
    env.course_id = made.headers["location"].rsplit("/", 1)[-1]
    add_to_course(env, env.job_id)
    for email, role in ((FRANK, "view"), (GINA, "review"), (HANK, "review")):
        assert grant_to(env, email, role).status_code == 303


def add_to_course(env, job_id: str, course_id: str | None = None) -> None:
    owner = env.who[OWNER]
    course_id = course_id or env.course_id
    response = owner.post(
        f"/courses/{course_id}/documents",
        data={"csrf_token": page_token(owner, f"/courses/{course_id}"), "job": job_id},
    )
    assert response.status_code == 303
    assert env.job(job_id).course_id == course_id


def grant_to(env, email: str, role: str, course_id: str | None = None):
    owner = env.who[OWNER]
    path = f"/courses/{course_id or env.course_id}"
    return owner.post(
        f"{path}/people",
        data={"csrf_token": page_token(owner, path), "email": email, "role": role},
    )


def stored_grants(env) -> list[tuple[str, str]]:
    with session_scope(env.ctx.factory) as session:
        return sorted((r.email, r.role) for r in session.scalars(select(CourseGrant)))


def stored_shares(env) -> list[tuple[str, str]]:
    with session_scope(env.ctx.factory) as session:
        return [(r.email, r.role) for r in shares.shares_of(session, env.job_id)]


def remove(env, email: str, confirm: str = "yes"):
    """Press Remove for `email` (`confirm` empty is the first press, which only asks)."""
    with session_scope(env.ctx.factory) as session:
        row = next(r for r in shares.shares_of(session, env.job_id) if r.email == email)
        share_id = row.id
    owner = env.who[OWNER]
    return owner.post(
        f"/jobs/{env.job_id}/share/{share_id}/revoke",
        data={"csrf_token": page_token(owner, f"/jobs/{env.job_id}/share"), "confirm": confirm},
    )


def heading(response) -> str:
    """The page's own heading: the rest differs by the person's token and name."""
    found = re.search(r"<h1[^>]*>(.*?)</h1>", response.text, re.S)
    return found.group(1).strip() if found else ""


def fill(env, template: str) -> str:
    ids = {
        "job_id": env.job_id,
        "share_id": "1" * 32,
        "element_id": "p1-e3",
        "number": "1",
        "slug": "equations",
    }
    for key, value in ids.items():
        template = template.replace("{" + key + "}", value)
    return template


FIELDS = {
    "action": "reject",
    "alt": "x",
    "latex": "x",
    "lang": "fr",
    "title": "A title",
    "contact": "help@example.org",
    "confirm": "yes",
    "email": "someone@example.org",
    "role": "view",
    "item": "p1-e3",
    "kind": "picture",
}


def send(env, actor: str, method: str, path: str):
    client = env.who[actor]
    if method == "GET":
        return client.get(path)
    fields = FIELDS | ({"action": "text", "text": "x"} if "/edit/" in path else {})
    return client.post(path, data={"csrf_token": page_token(client), **fields})


def snapshot(env) -> dict:
    """Everything a refused request must leave alone."""
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, env.job_id)
        return {
            "job": None
            if job is None
            else (
                job.result_version,
                job.title_override,
                job.contact_line,
                job.retention_mode,
                job.retain_until,
                job.status,
                job.html_stale,
            ),
            "edits": session.scalar(select(func.count()).select_from(ReviewEdit)),
            "shares": sorted((r.email, r.role) for r in session.scalars(select(JobShare)).all()),
            "grants": sorted((r.email, r.role) for r in session.scalars(select(CourseGrant))),
            "courses": sorted((c.name, c.user_id) for c in session.scalars(select(Course))),
            "jobs": session.scalar(select(func.count()).select_from(Job)),
            "links": session.scalar(select(func.count()).select_from(JobLink)),
        }


# --------------------------------------------------------------------------- the matrix


def test_every_job_route_has_a_stated_need():
    named = {key for key in ROUTES if "{job_id}" in key[1]}
    assert named == set(NEEDS), "state what each job route needs in NEEDS"


def test_the_get_routes_answer_each_person_by_their_right(site):
    """The owner's answer is the control: someone with enough right gets the same one, a
    shared person with too little right a 403, and a stranger or a removed person the 404."""
    for (method, template), need in NEEDS.items():
        if method != "GET":
            continue
        path = fill(site, template)
        answers = {actor: send(site, actor, "GET", path) for actor in ACTORS}
        control = answers[OWNER].status_code
        assert control not in {403}, path
        for actor, role in RIGHT.items():
            expected = control if RANK[role] >= RANK[need] else 403
            assert answers[actor].status_code == expected, (path, actor)
        for actor in NOBODY:
            assert answers[actor].status_code == 404, (path, actor)
            assert heading(answers[actor]) == heading(answers[STRANGER])  # the same page for all
            assert "SECRET-DOC-TEXT" not in answers[actor].text, (path, actor)
        for actor, like in SAME_AS.items():  # a course grant answers as the share would
            assert heading(answers[actor]) == heading(answers[like]), (path, actor)


def test_a_ready_document_opens_for_a_viewer_and_stops_for_a_stranger_and_after_its_time(
    ready_site,
):
    """The same rule on a document with nothing left to decide, where a viewer may download:
    the reading and downloading routes open for a viewer, the review ones do not, a stranger
    gets the 404 of an id that is not there, and past its deletion time nobody gets in."""
    job = ready_site.job_id
    paths = {
        "page": f"/jobs/{job}",
        "student": f"/jobs/{job}/view",
        "record": f"/jobs/{job}/record.csv",
        "zip": f"/jobs/{job}/download/canvas",
        "review": f"/jobs/{job}/review",
    }
    for name, path in paths.items():
        answers = {actor: send(ready_site, actor, "GET", path) for actor in ACTORS}
        assert answers[OWNER].status_code == 200, name
        for actor in (VIEWER, COURSE_VIEW):
            assert answers[actor].status_code == (403 if name == "review" else 200), (name, actor)
        for actor in (REVIEWER, COURSE_REVIEW):
            assert answers[actor].status_code == 200, (name, actor)
        for actor in NOBODY:
            assert answers[actor].status_code == 404, (name, actor)
    with session_scope(ready_site.ctx.factory) as session:
        session.get(Job, job).retain_until = utcnow() - timedelta(minutes=1)
    for name, path in paths.items():
        for actor in (OWNER, VIEWER, REVIEWER, COURSE_VIEW, COURSE_REVIEW):
            assert send(ready_site, actor, "GET", path).status_code == 404, (name, actor)


def test_words_written_to_the_owner_do_not_reach_a_person_the_document_was_shared_with(
    ready_site,
):
    """Listen is titled and worded for what a reader does (listen), not for what the owner
    does (test with their own screen reader), and the owner's two questions about posting
    the file are asked of nobody else."""
    job = ready_site.job_id
    owner = send(ready_site, OWNER, "GET", f"/jobs/{job}/listen").text
    assert "Listen as a student" in owner and "test with yours" in owner
    for actor in (VIEWER, REVIEWER):
        listen = send(ready_site, actor, "GET", f"/jobs/{job}/listen").text
        assert "Listen to this document" in listen
        assert "Listen as a student" not in listen and "test with yours" not in listen
        page = send(ready_site, actor, "GET", f"/jobs/{job}").text
        assert "Two quick questions" not in page
        assert send(ready_site, actor, "GET", f"/jobs/{job}/feedback").status_code == 403
    assert audience_of("owner") == "owner" and audience_of("review") == "reviewer"
    assert audience_of("view") == audience_of("anything else") == audience_of(None) == "reader"


# What the owner's page has that nobody else is given: where to post it, the checks before
# sharing, the record for the office, the student file's settings, deleting. Ids the templates
# own, so the test does not pin any sentence.
OWNER_BLOCKS = (
    "route-web",
    "route-canvas",
    "before-heading",
    "record-link-heading",
    "for-students",
    "settings",
    "delete-heading",
)


def test_a_reader_gets_the_reader_page_and_a_reviewer_the_owner_s_page_without_its_blocks(
    ready_site,
):
    job = ready_site.job_id
    pages = {a: send(ready_site, a, "GET", f"/jobs/{job}").text for a in (OWNER, REVIEWER, VIEWER)}
    for block in OWNER_BLOCKS:
        assert f'id="{block}"' in pages[OWNER] or f'id="{block}-' in pages[OWNER], block
        assert f'id="{block}"' not in pages[REVIEWER], block
        assert f'id="{block}"' not in pages[VIEWER], block
    # A reviewer keeps the review page; a reader has a page of their own, with the way in.
    assert 'id="results-heading"' in pages[REVIEWER] and 'id="shared-note"' in pages[REVIEWER]
    assert 'id="results-heading"' not in pages[VIEWER] and 'id="shared-note"' in pages[VIEWER]
    for path in ("view", "listen", "download", "download.epub"):
        assert f"/jobs/{job}/{path}" in pages[VIEWER], path
    # The owner's two questions and the record are not for them; the routes say so too.
    for actor in (REVIEWER, VIEWER):
        assert send(ready_site, actor, "GET", f"/jobs/{job}/feedback").status_code == 403, actor
        assert 'id="feedback"' not in pages[actor], actor


def test_a_reviewer_sees_the_owners_one_total_and_a_reader_is_given_none(site):
    """While a decision is open, a reviewer's page counts what the owner's counts (one
    function); a reader is told only that the owner is still checking, with no number."""
    job = site.job_id
    with session_scope(site.ctx.factory) as session:
        open_now = session.get(Job, job).open_decisions
    assert open_now
    asked = {a: send(site, a, "GET", f"/jobs/{job}").text for a in (OWNER, REVIEWER, VIEWER)}
    totals = {a: re.findall(r"Review (\d+) decisions?", asked[a]) for a in (OWNER, REVIEWER)}
    assert totals[OWNER] == totals[REVIEWER] != []
    assert 'data-state="being_checked"' in asked[VIEWER]
    assert not re.search(rf"\b{open_now}\b[^<]{{0,12}}decision", asked[VIEWER])
    assert 'id="ready"' not in asked[VIEWER] and f"/jobs/{job}/view" not in asked[VIEWER]


@pytest.mark.parametrize(("which", "answer"), [("site", 403), ("ready_site", 200)])
def test_a_reader_gets_no_draft_from_any_route_while_a_decision_is_open(request, which, answer):
    """Every route that gives a reader the document, or a page made of it: refused while the
    owner has a decision open (nothing of a draft is shown, read out or sent), given once it
    is ready. The owner's answer to the same route is the control."""
    env = request.getfixturevalue(which)
    job = env.job_id
    routes = [
        f"/jobs/{job}/view",
        f"/jobs/{job}/listen",
        f"/jobs/{job}/download",
        f"/jobs/{job}/download/canvas",
        f"/jobs/{job}/download/cartridge",
        f"/jobs/{job}/download.epub",
    ]
    if export_docx.available():
        routes.append(f"/jobs/{job}/download.docx")
    for path in routes:
        reader = send(env, VIEWER, "GET", path)
        assert reader.status_code == answer, path
        assert send(env, OWNER, "GET", path).status_code in {200, 303}, path
        if answer == 403:
            assert "attachment" not in reader.headers.get("content-disposition", ""), path
            assert "SECRET-DOC-TEXT" not in reader.text, path
    # The draft page is no way round it.
    drafted = send(env, VIEWER, "GET", f"/jobs/{job}/draft")
    assert drafted.status_code == (403 if answer == 403 else 303)


def test_the_post_routes_refuse_everyone_without_the_right_and_change_nothing(site):
    before = snapshot(site)
    for (method, template), need in NEEDS.items():
        if method != "POST":
            continue
        path = fill(site, template)
        for actor in (*RIGHT, *NOBODY):
            role = RIGHT.get(actor)
            if role is not None and RANK[role] >= RANK[need]:
                continue  # allowed to write: test_a_reviewer_decides_and_is_recorded
            response = send(site, actor, "POST", path)
            expected = 403 if role is not None else 404
            assert response.status_code in {expected, 400, 422}, (path, actor)
            assert response.status_code < 300 or response.status_code >= 400, (path, actor)
            assert snapshot(site) == before, (path, actor)


# --------------------------------------------------------------------------- the rules


def test_a_stranger_gets_the_404_of_an_id_that_does_not_exist(site):
    real = site.who[STRANGER].get(f"/jobs/{site.job_id}")
    absent = site.who[STRANGER].get(f"/jobs/{'0' * 32}")
    assert real.status_code == absent.status_code == 404
    assert real.text == absent.text


def test_a_reviewer_decides_and_is_recorded(site):
    reviewer = site.who[REVIEWER]
    page = reviewer.get(f"/jobs/{site.job_id}/review")
    assert page.status_code == 200
    review_page = f"/jobs/{site.job_id}/review"
    response = reviewer.post(
        f"{review_page}/p1-e3",
        data={"csrf_token": page_token(reviewer, review_page), "view": "list", "action": "accept"},
    )
    assert response.status_code == 303
    with session_scope(site.ctx.factory) as session:
        dave = session.scalar(select(User).where(User.email == DAVE))
        edits = list(session.scalars(select(ReviewEdit).where(ReviewEdit.job_id == site.job_id)))
        assert len(edits) == 1 and edits[0].user_id == dave.id  # not the owner's id
    # The record, read by the owner and by a viewer, names who decided.
    for actor in (OWNER, VIEWER, REVIEWER):
        record = site.who[actor].get(f"/jobs/{site.job_id}/record")
        assert record.status_code == 200 and ("a reviewer" in record.text or DAVE in record.text), (
            actor
        )
    csv_text = site.who[OWNER].get(f"/jobs/{site.job_id}/record.csv").text
    assert DAVE in csv_text


def test_a_reviewer_cannot_delete_share_or_change_settings(site):
    reviewer = site.who[REVIEWER]
    before = snapshot(site)
    token = page_token(reviewer)
    base = f"/jobs/{site.job_id}"
    for path, data in (
        (f"{base}/delete", {"confirm": "yes"}),
        (f"{base}/share", {"email": "mallory@example.org", "role": "review"}),
        (f"{base}/title", {"title": "mine"}),
        (f"{base}/keep", {}),
        (f"{base}/language", {"lang": "fr"}),
        (f"{base}/retry", {}),
    ):
        response = reviewer.post(path, data={"csrf_token": token, **data})
        assert response.status_code == 403, path
    assert reviewer.get(f"{base}/share").status_code == 403
    assert reviewer.get(f"{base}/delete").status_code == 403
    assert snapshot(site) == before
    assert not any(email == "mallory@example.org" for email, _ in stored_shares(site))


def test_a_removed_share_stops_working_on_the_next_request(site):
    viewer = site.who[VIEWER]
    assert viewer.get(f"/jobs/{site.job_id}").status_code == 200  # signed in, session warm
    # The first press of Remove only asks, and names the person: nothing is taken away yet.
    question = remove(site, CAROL, confirm="")
    assert question.status_code == 200 and CAROL in heading(question)
    assert (CAROL, "view") in stored_shares(site)
    assert viewer.get(f"/jobs/{site.job_id}").status_code == 200
    assert remove(site, CAROL).status_code == 303
    for path in ("", "/view", "/download", "/record", "/status", "/download.epub"):
        assert viewer.get(f"/jobs/{site.job_id}{path}").status_code == 404, path
    page = viewer.get("/app")
    assert "Shared with you" not in page.text


def test_the_address_is_matched_in_lower_case_and_in_full(site):
    owner_page = f"/jobs/{site.job_id}/share"
    owner = site.who[OWNER]
    # Upper case in the form is stored lower case, and matches the person who signs in.
    assert give(site, "", "view", raw="  Frank@Example.ORG ").status_code == 303
    assert ("frank@example.org", "view") in stored_shares(site)
    site.allow("frank@example.org")
    frank = site.client()
    login(frank, "frank@example.org")
    assert frank.get(f"/jobs/{site.job_id}").status_code == 200
    frank.close()
    # No domain rule: neither a bare domain nor a pattern is an address.
    before = stored_shares(site)
    for bad in ("@example.org", "*@example.org", "example.org", "carol", "a@b", "x y@example.org"):
        response = give(site, "", "view", raw=bad)
        assert response.status_code == 422, bad
        assert "full email address" in response.text
    assert stored_shares(site) == before
    # Someone else at the same domain, and a `+tag` of a shared address, are strangers.
    site.allow("carol+tag@example.org")
    tagged = site.client()
    login(tagged, "carol+tag@example.org")
    assert tagged.get(f"/jobs/{site.job_id}").status_code == 404
    tagged.close()
    assert owner.get(owner_page).status_code == 200


def test_the_owner_cannot_share_with_themselves_or_with_a_bad_right(site):
    assert give(site, ALICE, "view").status_code == 422
    assert give(site, "grace@example.org", "owner").status_code == 422
    assert give(site, "grace@example.org", "").status_code == 422
    assert ("grace@example.org", "view") not in stored_shares(site)


def test_the_owner_is_told_what_the_share_did(site):
    """Added, changed and already so are three different answers, and none holds the address."""
    owner = site.who[OWNER]

    def told(email: str, role: str) -> str:
        assert give(site, email, role).status_code == 303
        return owner.get(f"/jobs/{site.job_id}/share").text

    assert "Nothing was sent: copy the link" in told("hana@example.org", "view")
    again = told("hana@example.org", "view")
    assert "already has the right to View" in again and "Nothing was sent: copy" not in again
    changed = told("hana@example.org", "review")
    assert "Their right is now Review" in changed and "hana@example.org</strong>: Review" in changed


def test_at_most_ten_people(site):
    for n in range(shares.MAX_SHARES_PER_JOB - len(stored_shares(site))):
        assert give(site, f"p{n}@example.org", "view").status_code == 303
    assert len(stored_shares(site)) == shares.MAX_SHARES_PER_JOB
    response = give(site, "eleven@example.org", "view")
    assert response.status_code == 422 and "at most 10 people" in response.text
    # changing a right of someone already there is not a new person
    assert give(site, "p0@example.org", "review").status_code == 303


def test_a_sample_or_unfinished_document_cannot_be_shared(site):
    from readablestem.web.shares import ShareError

    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, site.job_id)
        job.status = "queued"
        with pytest.raises(ShareError, match="not_done"):
            shares.share(session, job, ALICE, "x@example.org", "view")
        job.status = "done"
        job.is_sample = True
        with pytest.raises(ShareError, match="sample"):
            shares.share(session, job, ALICE, "x@example.org", "view")
        job.is_sample = False


def test_no_address_goes_into_the_session_cookie_or_the_logs(site, caplog):
    caplog.set_level("DEBUG")
    response = give(site, "", "view", raw="secretperson@example.org")
    assert response.status_code == 303
    assert "secretperson" not in response.headers.get("set-cookie", "")
    assert "secretperson" not in caplog.text


def test_only_the_owner_s_use_keeps_a_document_longer(site):
    soon = utcnow() + timedelta(hours=2)
    with session_scope(site.ctx.factory) as session:
        session.get(Job, site.job_id).retain_until = soon
    assert fetch(site.who[REVIEWER], f"/jobs/{site.job_id}/download.epub").status_code == 200
    assert as_utc(site.job(site.job_id).retain_until) == as_utc(soon)
    assert fetch(site.who[OWNER], f"/jobs/{site.job_id}/download.epub").status_code == 200
    assert as_utc(site.job(site.job_id).retain_until) > as_utc(soon)


def test_a_document_past_its_time_is_refused_to_everyone(site):
    with session_scope(site.ctx.factory) as session:
        session.get(Job, site.job_id).retain_until = utcnow() - timedelta(minutes=1)
    for actor in (VIEWER, REVIEWER, COURSE_VIEW, COURSE_REVIEW):
        assert site.who[actor].get(f"/jobs/{site.job_id}").status_code == 404
    assert site.who[OWNER].get(f"/jobs/{site.job_id}").status_code == 404


def test_deleting_the_document_deletes_its_shares_and_erasing_a_person_those_that_name_them(site):
    # A second document of the owner's, shared with Carol, so one erasure has both kinds.
    with session_scope(site.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(JobShare)) == 2
        purge.delete_jobs(session, [site.job_id])
        assert session.scalar(select(func.count()).select_from(JobShare)) == 0
    # a share that names an address is removed with the person it names
    site2 = site
    job_id = job_id_from(upload(site2.who[OWNER], make_pdf(1)))
    assert site2.run_worker() == job_id
    site2.job_id = job_id
    assert give(site2, CAROL, "view").status_code == 303
    with session_scope(site.ctx.factory) as session:
        carol = session.scalar(select(User.id).where(User.email == CAROL))
        purge.erase_user(session, carol)
        assert session.scalar(select(func.count()).select_from(JobShare)) == 0


@pytest.mark.parametrize(("actor", "email"), [(REVIEWER, DAVE), (COURSE_REVIEW, GINA)])
def test_a_reread_by_a_reviewer_counts_against_the_owner(site, actor, email):
    """The allowance used is the document owner's, whoever asks, and the reviewer is told so
    before they ask (F-093)."""
    from html import unescape

    from readablestem.models.fake import FakeVisionClient
    from readablestem.web import reread
    from readablestem.web.database import utc_today
    from readablestem.web.quota import rereads_used

    asked: list[FakeVisionClient] = []

    def client(settings, budget):
        asked.append(FakeVisionClient(budget=budget, defaults={"equation": ANSWER}))
        return asked[-1]

    site.ctx.client_factory = client
    box = {"left": "10", "top": "55", "right": "50", "bottom": "70"}
    owner_id = site.job(site.job_id).user_id

    def counts() -> tuple[int, int]:
        with session_scope(site.ctx.factory) as session:
            reviewer_id = session.scalar(select(User.id).where(User.email == email))
            return (
                rereads_used(session, owner_id, utc_today()),
                rereads_used(session, reviewer_id, utc_today()),
            )

    def tools(actor: str) -> str:
        page = site.who[actor].get(
            f"/jobs/{site.job_id}/review",
            params={"page": "1", "tools": "1", "add": "equation"} | box,
        )
        return unescape(page.text)

    assert reread.SHARED_NOTE in tools(actor)
    assert reread.SHARED_NOTE not in tools(OWNER)

    reviewer = site.who[actor]
    path = f"/jobs/{site.job_id}/review"
    data = {"csrf_token": page_token(reviewer, path), "kind": "equation"} | box
    version = str(site.job(site.job_id).result_version or 0)
    assert counts() == (0, 0)
    assert (
        reviewer.post(
            f"/jobs/{site.job_id}/pages/1/add", data=data | {"version": version}
        ).status_code
        == 303
    )
    assert counts() == (1, 0)  # the owner's day, not the reviewer's
    assert len(asked) == 1 and len(asked[0].requests) == 1


# --------------------------------------------------------------------------- security review


def decide(env, actor: str, element: str = "p1-e3", action: str = "accept"):
    client = env.who[actor]
    page = f"/jobs/{env.job_id}/review"
    return client.post(
        f"{page}/{element}",
        data={"csrf_token": page_token(client, page), "view": "list", "action": action},
    )


def test_only_the_owner_sees_email_addresses_in_the_record(site):
    assert decide(site, REVIEWER).status_code == 303
    owner_page = site.who[OWNER].get(f"/jobs/{site.job_id}/record").text
    assert DAVE in owner_page and ALICE in owner_page
    for actor in (VIEWER, REVIEWER):
        page = site.who[actor].get(f"/jobs/{site.job_id}/record")
        csv_text = site.who[actor].get(f"/jobs/{site.job_id}/record.csv").text
        for text in (page.text, csv_text):
            assert ALICE not in text, actor
            if actor == VIEWER:  # a reviewer sees their own address in the page header
                assert DAVE not in text
        # the page names people as its reader may see them
        assert ("a reviewer" if actor == VIEWER else "you") in page.text, actor
        assert "the owner" in page.text, actor
    # the review list says who decided with the record's words: "you" only to the one who did
    listed = {
        a: site.who[a].get(f"/jobs/{site.job_id}/review/list").text for a in (OWNER, REVIEWER)
    }
    assert f"by {DAVE}" in listed[OWNER] and "by you" not in listed[OWNER]
    assert "by you" in listed[REVIEWER] and f"by {ALICE}" not in listed[REVIEWER]
    # the banner and the list name the owner without an address
    assert ALICE not in site.who[VIEWER].get(f"/jobs/{site.job_id}").text
    assert ALICE not in site.who[VIEWER].get("/app").text
    listing = site.who[VIEWER].get("/app").text
    assert "shared with you, you can" in listing and "The owner" not in listing
    banner = site.who[VIEWER].get(f"/jobs/{site.job_id}").text
    assert "</strong> shared this document with you" not in banner  # no name, no placeholder
    assert "This document was shared with you." in banner


def test_a_reviewer_s_decision_does_not_restart_the_deletion_clock(site):
    soon = utcnow() + timedelta(hours=2)
    with session_scope(site.ctx.factory) as session:
        session.get(Job, site.job_id).retain_until = soon
    assert decide(site, REVIEWER).status_code == 303
    assert as_utc(site.job(site.job_id).retain_until) == as_utc(soon)
    assert decide(site, OWNER, "p1-e4").status_code == 303
    assert as_utc(site.job(site.job_id).retain_until) > as_utc(soon)


def test_a_reread_is_rate_limited_by_the_owner_not_the_reviewer(site, monkeypatch):
    from readablestem.web.routes import edits

    keys: list[str] = []
    monkeypatch.setattr(edits, "enforce", lambda request, rule, key=None: keys.append((rule, key)))
    path = f"/jobs/{site.job_id}/tables/p1-e3/read-cell"
    reviewer = site.who[REVIEWER]
    reviewer.post(path, data={"csrf_token": page_token(reviewer), "row": "0", "col": "0"})
    owner_id = site.job(site.job_id).user_id
    assert keys and all(key == owner_id for rule, key in keys if rule == "reread")
    assert any(rule == "reread" for rule, _ in keys)


def test_undo_takes_back_only_a_write_the_person_made(site):
    assert decide(site, REVIEWER).status_code == 303
    version = site.job(site.job_id).result_version
    before = snapshot(site)
    owner = site.who[OWNER]
    response = owner.post(
        f"/jobs/{site.job_id}/undo",
        data={"csrf_token": page_token(owner), "version": str(version), "view": "list"},
    )
    assert response.status_code == 303
    assert snapshot(site) == before  # nothing was undone: it was the reviewer's write
    assert "nothing to undo" in owner.get(f"/jobs/{site.job_id}/review/list").text.lower()
    # the reviewer can undo their own
    reviewer = site.who[REVIEWER]
    response = reviewer.post(
        f"/jobs/{site.job_id}/undo",
        data={"csrf_token": page_token(reviewer), "version": str(version), "view": "list"},
    )
    assert response.status_code == 303
    assert snapshot(site) != before


def test_a_share_needs_a_proved_address(site):
    from types import SimpleNamespace

    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, site.job_id)
        proved = SimpleNamespace(id="x", email=CAROL, email_verified=True)
        unproved = SimpleNamespace(id="x", email=CAROL, email_verified=False)
        assert shares.role_of(session, job, proved) == "view"
        assert shares.role_of(session, job, unproved) is None
        assert shares.shared_with(session, unproved, utcnow()) == []
        assert len(shares.shared_with(session, proved, utcnow())) == 1


def test_the_settled_cost_is_the_owner_s_alone_and_is_the_total_after_a_rerun(ready_site):
    """WS10.9: once the job is done, the owner's page says what the document has cost in all
    (after a re-run, both runs), from the stored total; a person it was shared with is told
    nothing of it, and nobody is told a figure while it is converting."""
    job_id = ready_site.job_id
    with session_scope(ready_site.ctx.factory) as session:
        job = session.get(Job, job_id)
        job.cost_usd, job.cap_base_usd = 1.5, 1.0  # a re-run began when it had spent $1.00
    asked = {
        a: send(ready_site, a, "GET", f"/jobs/{job_id}").text for a in (OWNER, REVIEWER, VIEWER)
    }
    assert 'id="settled-cost"' in asked[OWNER] and "$1.50" in asked[OWNER]
    for actor in (REVIEWER, VIEWER):
        assert "settled-cost" not in asked[actor] and "$1.50" not in asked[actor], actor
    with session_scope(ready_site.ctx.factory) as session:
        session.get(Job, job_id).status = JobStatus.RUNNING
    running = send(ready_site, OWNER, "GET", f"/jobs/{job_id}").text
    assert "settled-cost" not in running and "$1.50" not in running


# --------------------------------------------------------------------------- the class link
# WS27.2 (web/links.py, routes/class_link.py): one link that opens the published student
# version for whoever holds it. These use the matrix's five people, with the setting on.


def _day(days: int) -> str:
    return (utcnow() + timedelta(days=days)).date().isoformat()


def make_link(
    env,
    *,
    closes: str | None = None,
    opens: str = "",
    label: str = "PHYS 201",
    request_token: str = "",
):
    owner = env.who[OWNER]
    data = {
        "csrf_token": page_token(owner, f"/jobs/{env.job_id}/share"),
        "label": label,
        "closes": closes if closes is not None else _day(30),
        "opens": opens,
    }
    if request_token:  # a browser's reload of the answer re-sends the form and its Referer
        data["request_token"] = request_token
    referer = {"referer": f"http://testserver/jobs/{env.job_id}/share"}
    return owner.post(f"/jobs/{env.job_id}/share/link", data=data, headers=referer)


def link_token(env, **kwargs) -> str:
    response = make_link(env, **kwargs)
    assert response.status_code == 200, response.text[:300]
    return re.search(r"/s/([A-Za-z0-9_-]{43})", response.text).group(1)


def link_post(env, action: str, actor: str = OWNER):
    client = env.who[actor]
    return client.post(
        f"/jobs/{env.job_id}/share/link/{action}",
        data={"csrf_token": page_token(client, f"/jobs/{env.job_id}/share")},
    )


def stored_link(env) -> JobLink | None:
    with session_scope(env.ctx.factory) as session:
        return session.scalars(select(JobLink).order_by(JobLink.created_at.desc())).first()


def test_a_link_holder_gets_the_published_copy_and_no_job_address(ready_site):
    """The link as a kind of visitor: signed out, or signed in as a stranger, the token opens
    `/s/{token}` and nothing under `/jobs/...`, where each is answered as without it."""
    token = link_token(ready_site)
    shown = ready_site.who[OWNER].get(f"/jobs/{ready_site.job_id}/view")
    signed_out = ready_site.client()
    for name, client in (("signed out", signed_out), ("stranger", ready_site.who[STRANGER])):
        page = client.get(f"/s/{token}")
        assert page.status_code == 200 and page.text == shown.text, name
        assert page.headers["content-security-policy"] == VIEW_CSP
        for (method, template), _need in NEEDS.items():
            if method == "GET":
                answer = client.get(fill(ready_site, template))
                assert answer.status_code in {303, 404}, (name, template)
                assert "SECRET-DOC-TEXT" not in answer.text, (name, template)
    assert ready_site.who[STRANGER].get(f"/s/{token}/listen").status_code == 404
    signed_out.close()


def test_the_link_page_has_no_cookie_and_the_headers_that_keep_it_private(ready_site):
    token = link_token(ready_site)
    for client in (ready_site.client(), ready_site.who[STRANGER]):  # the stranger holds a session
        page = client.get(f"/s/{token}")
        assert page.status_code == 200
        assert "set-cookie" not in page.headers
        assert page.headers["x-robots-tag"] == "noindex, nofollow"
        assert page.headers["referrer-policy"] == "no-referrer"
        assert page.headers["cache-control"] == "private, no-store"
        assert 'class="site-header"' not in page.text
    head = ready_site.client().head(f"/s/{token}")  # a link checker's HEAD is not an open
    assert head.status_code == 200 and head.content == b""
    row = stored_link(ready_site)
    assert row.open_count == 2 and row.first_opened_at is not None
    assert row.token_hash != token and token not in row.token_hash
    # The page the owner comes back to counts the opens and no longer holds the address.
    again = ready_site.who[OWNER].get(f"/jobs/{ready_site.job_id}/share")
    assert "2 times" in again.text and token not in again.text
    # Both pages say the rule: kept past the link's last day for the document's usual window.
    assert "to the link's last day and for 24 hours after that" in again.text
    job_page = ready_site.who[OWNER].get(f"/jobs/{ready_site.job_id}").text
    assert "its class link works until" in job_page and "kept for 24 hours after that" in job_page


def test_only_the_owner_makes_pauses_or_removes_a_link(ready_site):
    token = link_token(ready_site)
    before = snapshot(ready_site)
    for actor in (VIEWER, REVIEWER):
        assert make_link_as(ready_site, actor).status_code == 403
        for action in ("pause", "resume", "remove", "update"):
            assert link_post(ready_site, action, actor).status_code == 403, (actor, action)
    for actor in (STRANGER, REVOKED):
        assert link_post(ready_site, "remove", actor).status_code == 404
    for actor in (VIEWER, REVIEWER, STRANGER, REVOKED):  # the question is the owner's too
        asked = ready_site.who[actor].get(f"/jobs/{ready_site.job_id}/share/link/remove")
        assert asked.status_code == link_post(ready_site, "remove", actor).status_code, actor
    assert snapshot(ready_site) == before
    assert ready_site.client().get(f"/s/{token}").status_code == 200


def make_link_as(env, actor: str):
    client = env.who[actor]
    return client.post(
        f"/jobs/{env.job_id}/share/link",
        data={
            "csrf_token": page_token(client, "/app"),
            "closes": _day(30),
            "label": "x",
        },
    )


def test_a_closed_removed_paused_or_wrong_link_gets_one_404_page(ready_site, monkeypatch):
    token = link_token(ready_site)
    anyone = ready_site.client()
    wrong = anyone.get("/s/" + "a" * 43)
    assert (
        wrong.status_code == 404 and "not open" in wrong.text and "set-cookie" not in wrong.headers
    )

    def same_answer() -> None:
        answer = anyone.get(f"/s/{token}")
        assert (answer.status_code, answer.text) == (404, wrong.text)

    assert link_post(ready_site, "pause").status_code == 303
    same_answer()
    assert link_post(ready_site, "resume").status_code == 303
    assert anyone.get(f"/s/{token}").status_code == 200
    with session_scope(ready_site.ctx.factory) as session:  # not yet open
        session.scalars(select(JobLink)).first().opens_at = utcnow() + timedelta(days=1)
    same_answer()
    with session_scope(ready_site.ctx.factory) as session:  # closed
        link = session.scalars(select(JobLink)).first()
        link.opens_at, link.closes_at = None, utcnow() - timedelta(minutes=1)
    same_answer()
    other = link_token(ready_site)  # a new link removes the old; removing it closes the new
    assert anyone.get(f"/s/{other}").status_code == 200
    owner = ready_site.who[OWNER]
    path = f"/jobs/{ready_site.job_id}/share/link/remove"
    assert f'href="{path}"' in owner.get(f"/jobs/{ready_site.job_id}/share").text
    question = owner.get(path)  # "Remove the link" only asks; the link still works
    assert question.status_code == 200 and f'action="{path}"' in question.text
    assert anyone.get(f"/s/{other}").status_code == 200
    assert link_post(ready_site, "remove").status_code == 303
    assert anyone.get(f"/s/{other}").status_code == 404
    # Tokens cannot be tried at speed: after the allowance, a different, plain refusal.
    monkeypatch.setattr(ready_site.settings, "rate_limits_enabled", True)
    answers = [anyone.get("/s/" + "b" * 43).status_code for _ in range(25)]
    assert answers[:20] == [404] * 20 and set(answers[20:]) == {429}


def test_reloading_the_page_that_showed_the_address_makes_no_second_link(ready_site):
    """The answer to making a link is the page with its address, so a reload sends the same
    form again. It must not make another link and end the first: the address the owner has
    handed out keeps working, and the repeat lands on the share page, which has no address."""
    owner = ready_site.who[OWNER]
    form = owner.get(f"/jobs/{ready_site.job_id}/share").text
    sent = re.search(
        r'data-submit-once="link.make".*?name="request_token" value="([^"]+)"', form, re.S
    )
    first = make_link(ready_site, request_token=sent.group(1))
    token = re.search(r"/s/([A-Za-z0-9_-]{43})", first.text).group(1)
    again = make_link(ready_site, request_token=sent.group(1))  # the reload
    share = f"/jobs/{ready_site.job_id}/share"
    assert (again.status_code, again.headers["location"]) == (303, share)
    assert token not in owner.get(share).text
    with session_scope(ready_site.ctx.factory) as session:
        rows = session.scalars(select(JobLink)).all()
    assert len(rows) == 1 and rows[0].revoked_at is None
    assert ready_site.client().get(f"/s/{token}").status_code == 200
    # A new link made on purpose is another form, so another token: it replaces the first.
    assert make_link(ready_site).status_code == 200
    assert ready_site.client().get(f"/s/{token}").status_code == 404


def test_a_link_can_last_at_most_180_days_and_needs_an_end(ready_site):
    for closes in (_day(181), "", "not a day", _day(-1)):
        refused = make_link(ready_site, closes=closes)
        assert refused.status_code == 422, closes
    assert stored_link(ready_site) is None
    assert make_link(ready_site, closes=_day(179)).status_code == 200
    assert make_link(ready_site, closes=_day(30), opens=_day(40)).status_code == 422


def test_a_link_needs_a_ready_document(site):
    refused = make_link(site)
    assert refused.status_code == 422 and stored_link(site) is None
    assert site.client().get("/s/" + "c" * 43).status_code == 404


def test_an_edit_after_publishing_changes_nothing_a_class_sees_until_the_copy_is_updated(
    ready_site,
):
    token = link_token(ready_site)
    anyone = ready_site.client()
    published = anyone.get(f"/s/{token}").text
    owner = ready_site.who[OWNER]
    renamed = owner.post(
        f"/jobs/{ready_site.job_id}/title",
        data={"csrf_token": page_token(owner, f"/jobs/{ready_site.job_id}"), "title": "A new name"},
    )
    assert renamed.status_code == 303
    assert "A new name" in owner.get(f"/jobs/{ready_site.job_id}/view").text
    assert anyone.get(f"/s/{token}").text == published  # still the copy that was published
    share = owner.get(f"/jobs/{ready_site.job_id}/share").text
    assert "is now version" in share and "Update the published copy" in share
    assert link_post(ready_site, "update").status_code == 303
    updated = anyone.get(f"/s/{token}").text
    assert "A new name" in updated and updated != published
    assert "Update the published copy" not in owner.get(f"/jobs/{ready_site.job_id}/share").text


def test_the_class_link_does_not_exist_while_the_setting_is_off(ready_site, monkeypatch):
    token = link_token(ready_site)
    monkeypatch.setattr(ready_site.settings, "feature_class_link", False)
    owner = ready_site.who[OWNER]
    assert ready_site.client().get(f"/s/{token}").status_code == 404
    page = owner.get(f"/jobs/{ready_site.job_id}/share")
    assert page.status_code == 200 and "Class link" not in page.text
    assert make_link(ready_site).status_code == 404
    assert link_post(ready_site, "remove").status_code == 404


# --------------------------------------------------------------------------- course grants


def second_document(env) -> str:
    """Another finished document of the owner's, in no course."""
    job_id = job_id_from(upload(env.who[OWNER], make_pdf(1)))
    assert env.run_worker() == job_id
    return job_id


def opens(env, actor: str, job_id: str) -> int:
    return env.who[actor].get(f"/jobs/{job_id}").status_code


def test_a_course_grant_gives_nothing_while_the_setting_is_off(site, monkeypatch):
    """The setting was on, grant rows exist, and it went off: every job route answers the
    grantees as it answers a stranger, the People section and its routes do not exist, and the
    owner's own page shows nothing of them."""
    monkeypatch.setattr(site.settings, "feature_course_grants", False)
    before = snapshot(site)
    for (method, template), _need in NEEDS.items():
        path = fill(site, template)
        control = send(site, STRANGER, method, path)
        for actor in (COURSE_VIEW, COURSE_REVIEW):
            answer = send(site, actor, method, path)
            assert answer.status_code == control.status_code == 404, (method, path, actor)
            assert heading(answer) == heading(control), (method, path, actor)
    assert snapshot(site) == before
    owner = site.who[OWNER]
    course = f"/courses/{site.course_id}"
    assert 'id="people-heading"' not in owner.get(course).text
    assert grant_to(site, "new@example.org", "view").status_code == 404
    assert len(stored_grants(site)) == 3
    path = f"{course}/people/{'1' * 32}/remove"
    removal = owner.post(path, data={"csrf_token": page_token(owner, course), "confirm": "yes"})
    assert removal.status_code == 404
    assert "Course: " not in site.who[COURSE_REVIEW].get("/app").text
    assert site.who[COURSE_REVIEW].get(f"/jobs/{site.job_id}/share").status_code == 404
    assert len(stored_grants(site)) == 3  # nothing was removed or changed
    monkeypatch.setattr(site.settings, "feature_course_grants", True)
    assert opens(site, COURSE_REVIEW, site.job_id) == 200  # the rows still stand: on again


def test_a_document_is_covered_by_its_course_from_the_moment_it_joins_until_it_leaves(site):
    """Added later: covered at once. Moved to another course, taken out of any, or its course
    deleted: not covered at once. The grantee's page changes with the next request."""
    later = second_document(site)
    assert opens(site, COURSE_REVIEW, later) == opens(site, COURSE_VIEW, later) == 404
    add_to_course(site, later)
    assert opens(site, COURSE_REVIEW, later) == opens(site, COURSE_VIEW, later) == 200
    assert opens(site, STRANGER, later) == opens(site, COURSE_UNPROVEN, later) == 404
    # No route takes a document out of a course: the row is changed as a migration would.
    with session_scope(site.ctx.factory) as session:
        other = Course(user_id=session.get(Job, later).user_id, name="Another")
        session.add(other)
        session.flush()
        session.get(Job, later).course_id = other.id  # a course with nobody given access
        session.get(Job, site.job_id).course_id = None
    for job in (later, site.job_id):
        assert opens(site, COURSE_REVIEW, job) == opens(site, COURSE_VIEW, job) == 404, job
        assert opens(site, OWNER, job) == 200
    add_to_course(site, site.job_id)  # back in the course: covered again
    assert opens(site, COURSE_REVIEW, site.job_id) == 200
    # Deleting the course ends the grants and leaves the document with its owner.
    owner = site.who[OWNER]
    course = f"/courses/{site.course_id}"
    deleted = owner.post(
        f"{course}/delete", data={"csrf_token": page_token(owner, course), "confirm": "yes"}
    )
    assert deleted.status_code == 303
    assert stored_grants(site) == []
    assert opens(site, COURSE_REVIEW, site.job_id) == opens(site, COURSE_VIEW, site.job_id) == 404
    assert opens(site, OWNER, site.job_id) == 200


def test_an_expired_document_is_refused_before_any_grant_is_read(site):
    from sqlalchemy import event

    with session_scope(site.ctx.factory) as session:
        session.get(Job, site.job_id).retain_until = utcnow() - timedelta(minutes=1)
    statements: list[str] = []

    def record(conn, cursor, statement, *rest):
        statements.append(statement)

    event.listen(site.ctx.engine, "before_cursor_execute", record)
    try:
        assert opens(site, COURSE_REVIEW, site.job_id) == 404
    finally:
        event.remove(site.ctx.engine, "before_cursor_execute", record)
    assert not any("course_grants" in text or "job_shares" in text for text in statements)


def test_a_grant_is_given_by_the_course_owner_alone(site, caplog):
    """Everyone else gets the 404 of a course that does not exist and changes nothing; the
    owner's answers carry no address in the log, the session cookie or a URL."""
    caplog.set_level("DEBUG")
    course = f"/courses/{site.course_id}"
    grants = stored_grants(site)
    absent = f"/courses/{'0' * 32}"
    row = next(iter(grants))
    with session_scope(site.ctx.factory) as session:
        grant_id = session.scalar(select(CourseGrant.id).where(CourseGrant.email == row[0]))
    for actor in (STRANGER, REVIEWER, COURSE_REVIEW, COURSE_VIEW, COURSE_UNPROVEN, REVOKED):
        client = site.who[actor]
        token = page_token(client)
        for base, ids in ((course, grant_id), (absent, "1" * 32)):
            add = client.post(
                f"{base}/people",
                data={"csrf_token": token, "email": "x@example.org", "role": "view"},
            )
            gone = client.post(
                f"{base}/people/{ids}/remove", data={"csrf_token": token, "confirm": "yes"}
            )
            assert add.status_code == gone.status_code == 404, (base, actor)
        assert client.get(course).status_code == 404, actor  # the course page stays the owner's
        assert heading(client.get(course)) == heading(client.get(absent))
    assert stored_grants(site) == grants
    # The owner: an address that is not one, their own, a right that does not exist.
    for email, role in (("not an address", "view"), (ALICE, "view"), (CAROL, "owner")):
        assert grant_to(site, email, role).status_code == 422, (email, role)
    # Changing a right, then removing: the question first, as the answer to the POST.
    assert grant_to(site, FRANK, "review").status_code == 303
    assert (FRANK, "review") in stored_grants(site)
    owner = site.who[OWNER]
    with session_scope(site.ctx.factory) as session:
        frank_id = session.scalar(select(CourseGrant.id).where(CourseGrant.email == FRANK))
    path = f"{course}/people/{frank_id}/remove"
    asked = owner.post(path, data={"csrf_token": page_token(owner, course)})
    assert asked.status_code == 200 and FRANK in asked.text and "location" not in asked.headers
    assert (FRANK, "review") in stored_grants(site)
    done = owner.post(path, data={"csrf_token": page_token(owner, course), "confirm": "yes"})
    assert done.status_code == 303 and FRANK not in done.headers["location"]
    assert opens(site, COURSE_VIEW, site.job_id) == 404  # at once, on their next request
    assert grant_to(site, FRANK, "view", "f" * 32).status_code == 404
    secret = "secretgrantee@example.org"
    given = grant_to(site, secret, "view")
    assert given.status_code == 303
    assert secret not in given.headers.get("set-cookie", "") + given.headers["location"]
    assert "secretgrantee" not in caplog.text


def test_at_most_ten_people_have_a_grant_on_one_course(site):
    for n in range(shares.MAX_GRANTS_PER_COURSE - len(stored_grants(site))):
        assert grant_to(site, f"p{n}@example.org", "view").status_code == 303
    assert len(stored_grants(site)) == shares.MAX_GRANTS_PER_COURSE
    response = grant_to(site, "eleven@example.org", "view")
    assert response.status_code == 422 and "at most 10 people" in response.text
    assert grant_to(site, "p0@example.org", "review").status_code == 303  # not a new person


def test_the_owner_sees_everyone_who_can_open_a_document_and_a_grantee_sees_no_addresses(site):
    page = site.who[OWNER].get(f"/jobs/{site.job_id}/share").text
    assert "Through the course Physics 1" in page
    for email in (FRANK, GINA, HANK, CAROL, DAVE):
        assert email in page, email
    for actor in (COURSE_REVIEW, COURSE_VIEW, REVIEWER):
        assert site.who[actor].get(f"/jobs/{site.job_id}/share").status_code == 403, actor
        record = site.who[actor].get(f"/jobs/{site.job_id}/record").text
        assert HANK not in record and CAROL not in record and ALICE not in record, actor


def test_shared_with_me_lists_a_course_grant_under_its_course_and_each_document_once(site):
    from types import SimpleNamespace

    now = utcnow()

    def listed(email: str, *, proved: bool = True) -> list[tuple[str, str, str]]:
        with session_scope(site.ctx.factory) as session:
            user_id = session.scalar(select(User.id).where(User.email == email))
            user = SimpleNamespace(id=user_id, email=email, email_verified=proved)
            return [(d.id, d.role, d.course) for d in shares.shared_with(session, user, now)]

    job = site.job_id
    assert listed(GINA) == [(job, "review", "Physics 1")]
    assert listed(GINA, proved=False) == listed(ERIN) == []
    # DAVE has a review share and gets a view grant: once, as the share, with review.
    assert grant_to(site, DAVE, "view").status_code == 303
    assert listed(DAVE) == [(job, "review", "")]
    # CAROL has a view share and gets a review grant: once, under the course, with review.
    assert grant_to(site, CAROL, "review").status_code == 303
    assert listed(CAROL) == [(job, "review", "Physics 1")]
    page = site.who[COURSE_REVIEW].get("/app").text
    assert "Course: Physics 1" in page and page.count(f'href="/jobs/{job}"') == 1
    assert "Course: " not in site.who[STRANGER].get("/app").text


def test_a_grant_goes_with_the_person_it_names_and_with_its_course_s_owner(site):
    with session_scope(site.ctx.factory) as session:
        gina = session.scalar(select(User.id).where(User.email == GINA))
        purge.erase_user(session, gina)
        assert (GINA, "review") not in [
            (r.email, r.role) for r in session.scalars(select(CourseGrant))
        ]
        assert session.scalar(select(func.count()).select_from(CourseGrant)) == 2
        alice = session.scalar(select(User.id).where(User.email == ALICE))
        purge.erase_user(session, alice)
        assert session.scalar(select(func.count()).select_from(CourseGrant)) == 0
        assert session.scalar(select(func.count()).select_from(Course)) == 0
