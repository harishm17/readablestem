"""G1: sharing a document with named, signed-in people (web/shares.py, routes/sharing.py,
`routes.jobs.job_access`).

The matrix test runs every route that names a job against five people: the owner, someone it was
shared with to view, someone it was shared with to review, someone whose share was removed, and a
stranger. The rest check the rules one by one: the exact, case-insensitive address match, that a
removed share works at once, that a shared person cannot share further or delete, who is
recorded for a review edit, and what the owner's quota, clock and deletion do."""

from __future__ import annotations

import re
from datetime import timedelta

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
from readablestem.web import shares
from readablestem.web.database import as_utc
from readablestem.web.db import (
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
ANSWER = {"is_equation": True, "latex": "x^2", "confidence": 0.99}
OWNER, VIEWER, REVIEWER, REVOKED, STRANGER = "owner", "viewer", "reviewer", "revoked", "stranger"
ACTORS = (OWNER, VIEWER, REVIEWER, REVOKED, STRANGER)

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
    env = WebEnv(tmp_path, feature_class_link=True)
    with env.running() as owner:
        for email in (ALICE, BOB, CAROL, DAVE, ERIN):
            env.allow(email)
        login(owner, ALICE)
        env.who = {OWNER: owner}
        for actor, email in ((VIEWER, CAROL), (REVIEWER, DAVE), (REVOKED, ERIN), (STRANGER, BOB)):
            client = env.client()
            login(client, email)
            env.who[actor] = client
        env.job_id = job_id_from(upload(owner, make_pdf(2)))
        if ready:
            _settled(env)
        assert env.run_worker() == env.job_id
        give(env, CAROL, "view")
        give(env, DAVE, "review")
        give(env, ERIN, "review")
        remove(env, ERIN)
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


def stored_shares(env) -> list[tuple[str, str]]:
    with session_scope(env.ctx.factory) as session:
        return [(r.email, r.role) for r in shares.shares_of(session, env.job_id)]


def remove(env, email: str) -> None:
    with session_scope(env.ctx.factory) as session:
        row = next(r for r in shares.shares_of(session, env.job_id) if r.email == email)
        share_id = row.id
    owner = env.who[OWNER]
    response = owner.post(
        f"/jobs/{env.job_id}/share/{share_id}/revoke",
        data={"csrf_token": page_token(owner, f"/jobs/{env.job_id}/share")},
    )
    assert response.status_code == 303


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
        for actor in (VIEWER, REVIEWER):
            role = "review" if actor == REVIEWER else "view"
            expected = control if RANK[role] >= RANK[need] else 403
            assert answers[actor].status_code == expected, (path, actor)
        for actor in (REVOKED, STRANGER):
            assert answers[actor].status_code == 404, (path, actor)
            assert heading(answers[actor]) == heading(answers[STRANGER])  # the same page for both
        for actor in (REVOKED, STRANGER):
            assert "SECRET-DOC-TEXT" not in answers[actor].text, (path, actor)


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
        assert answers[VIEWER].status_code == (403 if name == "review" else 200), name
        assert answers[REVIEWER].status_code == 200, name
        assert answers[STRANGER].status_code == answers[REVOKED].status_code == 404, name
    with session_scope(ready_site.ctx.factory) as session:
        session.get(Job, job).retain_until = utcnow() - timedelta(minutes=1)
    for name, path in paths.items():
        for actor in (OWNER, VIEWER, REVIEWER):
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
        for actor in (VIEWER, REVIEWER, REVOKED, STRANGER):
            role = {VIEWER: "view", REVIEWER: "review"}.get(actor)
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
    remove(site, CAROL)
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
    for actor in (VIEWER, REVIEWER):
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


def test_a_reread_by_a_reviewer_counts_against_the_owner(site):
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
            reviewer_id = session.scalar(select(User.id).where(User.email == DAVE))
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

    assert reread.SHARED_NOTE in tools(REVIEWER)
    assert reread.SHARED_NOTE not in tools(OWNER)

    reviewer = site.who[REVIEWER]
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
    assert "at least until the link's last day and then for 24 hours" in again.text
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
