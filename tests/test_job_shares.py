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

from fixtures.web.support import ALICE, BOB, WebEnv, csrf_from, job_id_from, login, make_pdf, upload
from readablestem.ops import retention as purge
from readablestem.web import shares
from readablestem.web.database import as_utc
from readablestem.web.db import Job, JobShare, ReviewEdit, User, session_scope, utcnow
from test_audit_authz import ROUTES

CAROL = "carol@example.org"  # a viewer
DAVE = "dave@example.org"  # a reviewer
ERIN = "erin@example.org"  # shared, then removed
OWNER, VIEWER, REVIEWER, REVOKED, STRANGER = "owner", "viewer", "reviewer", "revoked", "stranger"
ACTORS = (OWNER, VIEWER, REVIEWER, REVOKED, STRANGER)

# What each route that names a job needs (`job_access`). Every such route in the app is here:
# `test_every_job_route_has_a_stated_need` fails for one that is not.
NEEDS: dict[tuple[str, str], str] = {
    ("GET", "/jobs/{job_id}"): "view",
    ("GET", "/jobs/{job_id}/status"): "view",
    ("GET", "/jobs/{job_id}/progress"): "view",
    ("GET", "/jobs/{job_id}/draft"): "view",
    ("GET", "/jobs/{job_id}/view"): "view",
    ("GET", "/jobs/{job_id}/record"): "view",
    ("GET", "/jobs/{job_id}/record.csv"): "view",
    ("GET", "/jobs/{job_id}/download"): "view",
    ("GET", "/jobs/{job_id}/download/canvas"): "view",
    ("GET", "/jobs/{job_id}/download/cartridge"): "view",
    ("GET", "/jobs/{job_id}/download.docx"): "view",
    ("GET", "/jobs/{job_id}/download.epub"): "view",
    ("GET", "/jobs/{job_id}/crops/{element_id}.png"): "view",
    ("GET", "/jobs/{job_id}/feedback"): "view",
    ("POST", "/jobs/{job_id}/feedback"): "view",
    ("GET", "/jobs/{job_id}/combine"): "owner",
    ("GET", "/jobs/{job_id}/delete"): "owner",
    ("POST", "/jobs/{job_id}/delete"): "owner",
    ("POST", "/jobs/{job_id}/retry"): "owner",
    ("POST", "/jobs/{job_id}/language"): "owner",
    ("POST", "/jobs/{job_id}/title"): "owner",
    ("POST", "/jobs/{job_id}/contact"): "owner",
    ("POST", "/jobs/{job_id}/keep"): "owner",
    ("GET", "/jobs/{job_id}/share"): "owner",
    ("POST", "/jobs/{job_id}/share"): "owner",
    ("POST", "/jobs/{job_id}/share/{share_id}/revoke"): "owner",
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


@pytest.fixture
def site(tmp_path):
    env = WebEnv(tmp_path)
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


def test_a_viewer_reads_and_downloads_but_cannot_write(site):
    viewer = site.who[VIEWER]
    page = viewer.get(f"/jobs/{site.job_id}")
    assert page.status_code == 200
    assert "shared this document with you" in page.text
    for owner_only in ("Delete now", "Keep 7 more days", "/share", "Upload a revised version"):
        assert owner_only not in page.text, owner_only
    assert f"/jobs/{site.job_id}/review" not in page.text  # a viewer is not offered the review
    assert viewer.get(f"/jobs/{site.job_id}/view").status_code == 200
    assert viewer.get(f"/jobs/{site.job_id}/record").status_code == 200
    assert viewer.get(f"/jobs/{site.job_id}/download.epub").status_code == 200
    assert viewer.get(f"/jobs/{site.job_id}/review").status_code == 403
    before = snapshot(site)
    token = page_token(viewer)
    decision = {"csrf_token": token, "view": "list", "action": "accept"}
    assert viewer.post(f"/jobs/{site.job_id}/review/p1-e3", data=decision).status_code == 403
    assert snapshot(site) == before


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


def test_changing_a_right_takes_effect_at_once(site):
    reviewer = site.who[REVIEWER]
    assert reviewer.get(f"/jobs/{site.job_id}/review").status_code == 200
    assert give(site, DAVE, "view").status_code == 303
    assert reviewer.get(f"/jobs/{site.job_id}/review").status_code == 403
    assert reviewer.get(f"/jobs/{site.job_id}").status_code == 200
    assert (DAVE, "view") in stored_shares(site)


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


def test_a_shared_person_sees_it_in_their_list_and_the_owner_does_not(site):
    assert "Shared with you" in site.who[VIEWER].get("/app").text
    page = site.who[REVIEWER].get("/app").text
    assert f"/jobs/{site.job_id}" in page and "you can review it" in page
    assert "Shared with you" not in site.who[OWNER].get("/app").text
    assert "Shared with you" not in site.who[STRANGER].get("/app").text


def test_the_page_says_nothing_was_sent_and_shows_the_address(site):
    page = site.who[OWNER].get(f"/jobs/{site.job_id}/share")
    assert page.status_code == 200
    assert f"/jobs/{site.job_id}" in page.text and CAROL in page.text and DAVE in page.text
    assert ERIN not in page.text  # removed
    assert "Nothing is sent to it" in page.text


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
    assert site.who[VIEWER].get(f"/jobs/{site.job_id}/download.epub").status_code == 200
    assert as_utc(site.job(site.job_id).retain_until) == as_utc(soon)
    assert site.who[OWNER].get(f"/jobs/{site.job_id}/download.epub").status_code == 200
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


def test_a_reread_by_a_reviewer_counts_against_the_owner(monkeypatch):
    """`reread.read_equation` takes the allowance of the job's owner, whoever asked."""
    from types import SimpleNamespace

    from readablestem.web import reread

    seen: list[str] = []

    def stop(request, user_id):
        seen.append(user_id)
        raise reread.ReadRefused("stop")

    monkeypatch.setattr(reread, "_count_against_allowance", stop)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ctx=None)))
    job = SimpleNamespace(id="j" * 32, user_id="the-owner")
    with pytest.raises(reread.ReadRefused):
        reread.read_equation(request, job, "the-reviewer", None, b"")  # type: ignore[arg-type]
    assert seen == ["the-owner"]
    assert not reread.GATE._users  # the reviewer's turn was given back


def test_job_access_knows_its_needs():
    from readablestem.web.routes.jobs import job_access

    with pytest.raises(ValueError, match="unknown access"):
        job_access(None, "0" * 32, None, "admin")  # type: ignore[arg-type]


def test_the_migration_adds_the_table_and_the_downgrade_removes_it(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    from readablestem.web import db
    from test_web_migrations import migrate

    url = f"sqlite:///{tmp_path}/shares.db"
    migrate(url, "head")
    engine = create_engine(url)
    try:
        assert "job_shares" in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
    finally:
        engine.dispose()
    migrate(url, "e1a7c3b95d24", down=True)
    engine = create_engine(url)
    try:
        assert "job_shares" not in inspect(engine).get_table_names()
        assert "jobs" in inspect(engine).get_table_names()  # the documents are untouched
    finally:
        engine.dispose()


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
            assert "a reviewer" in text and "the owner" in text, actor
    # the banner and the list name the owner without an address
    assert ALICE not in site.who[VIEWER].get(f"/jobs/{site.job_id}").text
    assert ALICE not in site.who[VIEWER].get("/app").text
    assert "shared by The owner" in site.who[VIEWER].get("/app").text


def test_a_name_is_shown_when_there_is_one(site):
    with session_scope(site.ctx.factory) as session:
        session.scalar(select(User).where(User.email == DAVE)).name = "Dave Reviewer"
    decide(site, REVIEWER)
    page = site.who[VIEWER].get(f"/jobs/{site.job_id}/record").text
    assert "Dave Reviewer" in page and DAVE not in page
    owner = site.who[OWNER].get(f"/jobs/{site.job_id}/record").text
    assert f"Dave Reviewer ({DAVE})" in owner


def test_a_reviewer_s_decision_does_not_restart_the_deletion_clock(site):
    soon = utcnow() + timedelta(hours=2)
    with session_scope(site.ctx.factory) as session:
        session.get(Job, site.job_id).retain_until = soon
    assert decide(site, REVIEWER).status_code == 303
    assert as_utc(site.job(site.job_id).retain_until) == as_utc(soon)
    assert decide(site, OWNER, "p1-e4").status_code == 303
    assert as_utc(site.job(site.job_id).retain_until) > as_utc(soon)


def test_no_restart_of_the_clock_is_reachable_without_the_owner_check():
    """Every restart in a route that a reviewer can reach goes through `restart_owner_clock`;
    the ones left are owner-only routes (`job_access(..., "owner")`) or check the owner."""
    import pathlib

    import readablestem

    root = pathlib.Path(readablestem.__file__).parent / "web" / "routes"
    for name in ("review.py", "edits.py", "split.py", "inventory.py", "bulk.py"):
        assert "retention.restart_clock" not in (root / name).read_text(), name


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


def test_the_undo_offer_is_for_the_writer_only(site):
    from readablestem.web.routes import review

    assert decide(site, REVIEWER).status_code == 303
    version = site.job(site.job_id).result_version
    with session_scope(site.ctx.factory) as session:
        edits = review.last_write(session, site.job_id, version)
        dave = session.scalar(select(User.id).where(User.email == DAVE))
        alice = session.scalar(select(User.id).where(User.email == ALICE))
    assert review.can_undo(edits, dave) and not review.can_undo(edits, alice)


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
