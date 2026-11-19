"""Problem reports to the operator (SW25, `web.reports`): who writes one, who reads it, the
limit, and what the stored row holds. The retention clocks are in test_ops_retention.py, the
table's migration in test_web_migrations.py, and the student file's link to the instructor in
test_assemble_title_contact.py."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from fixtures.web.support import (
    ADMIN,
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    job_id_from,
    login,
    make_pdf,
    upload,
)
from readablestem.web import reports, shares
from readablestem.web.db import JobShare, Report, User, session_scope

READER = "reader@example.org"
REVIEWER = "reviewer@example.org"
TYPED = "The equation is cut off <script>alert(1)</script> https://x.example/"


@pytest.fixture
def site(tmp_path):
    env = WebEnv(tmp_path, rate_limits_enabled=True, contact_email="help@example.org")
    with env.running() as owner:
        for email in (ALICE, BOB, READER, REVIEWER):
            env.allow(email)
        env.allow(ADMIN, admin=True)
        login(owner, ALICE)
        env.who = {"owner": owner}
        for actor, email in (
            ("reader", READER),
            ("reviewer", REVIEWER),
            ("stranger", BOB),
            ("admin", ADMIN),
        ):
            env.who[actor] = env.client()
            login(env.who[actor], email)
        env.job_id = job_id_from(upload(owner, make_pdf(2)))
        assert env.run_worker() == env.job_id
        with session_scope(env.ctx.factory) as session:
            for email, role in ((READER, shares.VIEW), (REVIEWER, shares.REVIEW)):
                session.add(JobShare(job_id=env.job_id, email=email, role=role))
        yield env


def send(env, actor: str, text: str = TYPED, item: str = "p1-e1"):
    client = env.who[actor]
    path = f"/jobs/{env.job_id}/report"
    token = csrf_from(client.get("/app").text)
    return client.post(path, data={"csrf_token": token, "item": item, "text": text})


def stored(env) -> list[Report]:
    with session_scope(env.ctx.factory) as session:
        rows = list(session.scalars(select(Report).order_by(Report.id)))
        session.expunge_all()
        return rows


def test_an_owner_and_a_reviewer_write_a_report_a_reader_and_a_stranger_cannot(site):
    page = site.who["owner"].get(f"/jobs/{site.job_id}/report?item=p1-e1")
    assert page.status_code == 200 and "Do not type student names" in page.text
    # Who reads it and for how long, before the send button: the clocks the purge runs on.
    assert f"{reports.CLOSED_DAYS} days after that" in page.text
    assert f"{reports.MAX_DAYS} days after" in page.text
    for actor, status in (("owner", 303), ("reviewer", 303), ("reader", 403), ("stranger", 404)):
        assert send(site, actor).status_code == status, actor
    rows = stored(site)
    assert len(rows) == 2 and {r.status for r in rows} == {"open"}
    with session_scope(site.ctx.factory) as session:
        emails = [session.get(User, r.from_user_id).email for r in rows]  # ty: ignore[unresolved-attribute]
    assert sorted(emails) == [ALICE, REVIEWER]
    row = rows[0]
    # Ids and codes from the stored document, not from the form: nothing of the document.
    assert (row.kind, row.job_ref, row.item_ref, row.item_kind, row.page) == (
        "tool",
        site.job_id,
        "p1-e1",
        row.item_kind,
        row.page,
    )
    assert row.text == TYPED and row.pipeline_version
    # What was sent from the form can only name an id: a made-up kind or page is not stored.
    forged = site.who["owner"].post(
        f"/jobs/{site.job_id}/report",
        data={
            "csrf_token": csrf_from(site.who["owner"].get("/app").text),
            "item": "x y",
            "kind": "equation",
            "page": "9",
        },
    )
    assert forged.status_code == 303 and stored(site)[-1].item_ref is None
    assert forged.headers["location"] == f"/jobs/{site.job_id}"  # built from the stored job


def test_only_an_admin_reads_the_reports_and_the_text_is_escaped(site):
    assert send(site, "owner").status_code == 303
    for actor in ("owner", "reviewer", "reader", "stranger"):
        assert site.who[actor].get("/admin/reports").status_code == 403, actor
    listing = site.who["admin"].get("/admin/reports")
    assert listing.status_code == 200
    assert ALICE in listing.text and site.job_id in listing.text and "p1-e1" in listing.text
    assert "&lt;script&gt;" in listing.text and "<script>alert(1)" not in listing.text
    assert '<a href="https://x.example' not in listing.text  # never made into a link
    report_id = stored(site)[0].id
    admin = site.who["admin"]
    closed = admin.post(
        f"/admin/reports/{report_id}/close",
        data={"csrf_token": csrf_from(admin.get("/admin/reports").text)},
    )
    assert closed.status_code == 303
    row = stored(site)[0]
    assert row.status == "closed" and row.closed_at is not None
    owner = site.who["owner"]
    refused = owner.post(
        f"/admin/reports/{report_id}/close", data={"csrf_token": csrf_from(owner.get("/app").text)}
    )
    assert refused.status_code == 403


def test_the_sixth_report_in_ten_minutes_is_refused_and_a_long_text_is_not_stored(site):
    too_long = send(site, "owner", text="x" * 1001)
    assert too_long.status_code == 422 and "report-text" in too_long.text and not stored(site)
    assert [send(site, "owner").status_code for _ in range(5)] == [303] * 5
    sixth = send(site, "owner")
    assert sixth.status_code == 429 and len(stored(site)) == 5
    assert re.search(r"Retry-After", str(sixth.headers), re.I)
    assert send(site, "reviewer").status_code == 303  # the allowance is per person
