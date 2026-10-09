"""Usage events (web/events.py): what is recorded, that it is content-free, that it can be
turned off, the admin page's counts, and the 400-day retention rule.

The two questions after a download are in test_web_feedback.py; the documents list and
the deleted-document page (which reads the owner's events) in test_web_documents.py.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from a11y_stem.ops.retention import purge_expired
from a11y_stem.web import events
from a11y_stem.web.db import Event, Job, User, session_scope
from fixtures.web.support import (
    ADMIN,
    ALICE,
    BOB,
    SECRETS,
    WebEnv,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def all_events(env, name: str | None = None) -> list[Event]:
    with session_scope(env.ctx.factory) as session:
        query = select(Event).order_by(Event.id)
        if name is not None:
            query = query.where(Event.name == name)
        return list(session.scalars(query))


def user_id(env, email: str) -> str:
    with session_scope(env.ctx.factory) as session:
        found = session.scalar(select(User.id).where(User.email == email))
        assert found is not None
        return found


def finished(env, *, filename: str = "notes.pdf", data: bytes | None = None) -> str:
    job_id = job_id_from(upload(env.browser, data or make_pdf(1), filename=filename))
    assert env.run_worker() == job_id
    return job_id


def decide(env, job_id: str, element_id: str, action: str = "reject"):
    return env.browser.post(
        f"/jobs/{job_id}/review/{element_id}",
        data={"csrf_token": token(env.browser), "action": action},
    )


# --------------------------------------------------------------------------- the rows


@pytest.mark.parametrize(
    ("kwargs", "problem"),
    [
        ({"name": "clicked"}, "unknown event"),
        ({"name": "uploaded", "code": "Week 3 notes.pdf"}, "event code"),
        ({"name": "uploaded", "code": "x" * 65}, "event code"),
        ({"name": "uploaded", "job_ref": "notes.pdf"}, "job id"),
        ({"name": "minutes_saved", "value": math.nan}, "finite"),
    ],
)
def test_an_event_cannot_carry_text(kwargs, problem):
    """`make` is the one way to build an event row: names are a fixed list and codes
    short lower-case categories, so a file name or a sentence cannot get in."""
    with pytest.raises(ValueError, match=problem):
        events.make("u" * 32, **kwargs)


def test_the_upload_fingerprint_is_keyed_salted_and_not_the_plain_hash(tmp_path):
    from fixtures.web.support import make_settings

    data = make_pdf(1)
    settings = make_settings(tmp_path, session_secret="s" * 40)
    mark = events.fingerprint(settings, "a" * 32, data)
    assert mark.startswith("fp:") and len(mark) == 3 + 24
    assert mark == events.fingerprint(settings, "a" * 32, data)  # same file, same person
    assert hashlib.sha256(data).hexdigest()[:24] not in mark  # not the plain hash
    assert mark != events.fingerprint(settings, "b" * 32, data)  # another person
    assert mark != events.fingerprint(settings, "a" * 32, data + b" ")  # another file
    rotated = make_settings(tmp_path, session_secret="t" * 40)
    assert mark != events.fingerprint(rotated, "a" * 32, data)  # another key


# --------------------------------------------------------------------------- the journey


def test_a_whole_journey_is_recorded_as_content_free_events(web, monkeypatch):
    """Sign-in, upload, finish, review, view, download and re-upload: each is one event
    with a code and a number, and nothing from the document (the fake document carries
    SECRET markers in its text, alt text, LaTeX, title and file name)."""
    from a11y_stem.pipeline import assemble

    monkeypatch.setattr(assemble, "render_html", lambda **kw: "<html>re-rendered</html>")
    alice = user_id(web, ALICE)
    assert [e.name for e in all_events(web)] == ["signed_in"]

    pdf = make_pdf(2)
    job_id = finished(web, filename="SECRET-FILENAME.pdf", data=pdf)
    uploaded = all_events(web, "uploaded")[0]
    assert (uploaded.user_id, uploaded.job_ref, uploaded.value) == (alice, job_id, 2.0)
    assert uploaded.code is not None and uploaded.code.startswith("fp:")
    done = all_events(web, "finished")[0]
    assert done.code == "done" and done.value is not None and done.value >= 0

    for _ in range(2):  # the screen again after a decision is the same visit
        assert web.browser.get(f"/jobs/{job_id}/review").status_code == 200
    assert web.browser.get(f"/jobs/{job_id}/review/list").status_code == 200
    assert [(e.code, e.job_ref) for e in all_events(web, "review_opened")] == [("split", job_id)]

    assert web.browser.get(f"/jobs/{job_id}/download").status_code == 200
    first = all_events(web, "downloaded")[0]
    assert (first.code, first.value) == ("open", 3.0)  # three decisions still open

    for element_id in ("p1-e3", "p1-e4"):
        assert decide(web, job_id, element_id).status_code == 303
    assert not all_events(web, "review_completed")
    assert decide(web, job_id, "p1-e5").status_code == 303  # the last open decision
    made = all_events(web, "decision_made")
    assert [(e.code, e.value) for e in made] == [("single", 1.0)] * 3
    completed = all_events(web, "review_completed")
    assert len(completed) == 1 and completed[0].value is not None
    decide(web, job_id, "p1-e5", "accept")  # changing a decision later: not completed again
    assert len(all_events(web, "review_completed")) == 1

    for _ in range(2):
        assert web.browser.get(f"/jobs/{job_id}/view").status_code == 200
    assert len(all_events(web, "html_viewed")) == 1
    web.browser.get(f"/jobs/{job_id}/download")
    assert all_events(web, "downloaded")[-1].code == "done"

    again = job_id_from(upload(web.browser, pdf, filename="renamed.pdf"))
    reuploaded = all_events(web, "reuploaded")
    assert len(reuploaded) == 1 and reuploaded[0].job_ref == again
    assert all_events(web, "uploaded")[1].code == uploaded.code
    upload(web.browser, make_pdf(3))  # a different file is not a re-upload
    assert len(all_events(web, "reuploaded")) == 1

    rows = all_events(web)
    for row in rows:
        stored = f"{row.name} {row.code} {row.value} {row.job_ref}"
        for secret in SECRETS:
            assert secret not in stored
        assert "notes" not in stored and "renamed" not in stored and "@" not in stored


def test_bob_signing_in_is_bobs_event_and_a_failed_job_says_why(web):
    from a11y_stem.core.budget import BudgetExceeded

    bob = web.client()
    login(bob, BOB)
    assert [e.user_id for e in all_events(web, "signed_in")] == [
        user_id(web, ALICE),
        user_id(web, BOB),
    ]
    web.converter.raises = BudgetExceeded("daily", "cap")
    job_id = finished(web)
    assert web.job(job_id).status == "failed"
    stopped = all_events(web, "finished")[0]
    assert stopped.code == "failed:budget_daily" and stopped.job_ref == job_id


def test_a_bulk_accept_is_one_decision_event(web, monkeypatch):
    from a11y_stem.pipeline import assemble

    monkeypatch.setattr(assemble, "render_html", lambda **kw: "<html>re-rendered</html>")
    job_id = finished(web)
    form = {"action": "accept_confident", "min_confidence": "50", "view": "list"}
    response = web.browser.post(
        f"/jobs/{job_id}/review/equations", data={"csrf_token": token(web.browser), **form}
    )
    assert response.status_code == 303
    made = all_events(web, "decision_made")
    assert [(e.code, e.value) for e in made] == [("bulk", 1.0)]  # p1-e3 has a proposal
    # A bulk accept that changes nothing is not a decision.
    web.browser.post(
        f"/jobs/{job_id}/review/equations", data={"csrf_token": token(web.browser), **form}
    )
    assert len(all_events(web, "decision_made")) == 1


def test_nothing_is_recorded_when_events_are_off(tmp_path, monkeypatch):
    from a11y_stem.pipeline import assemble

    monkeypatch.setattr(assemble, "render_html", lambda **kw: "<html>re-rendered</html>")
    env = WebEnv(tmp_path, events_enabled=False)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        job_id = finished(env)
        client.get(f"/jobs/{job_id}/review")
        decide(env, job_id, "p1-e3")
        client.get(f"/jobs/{job_id}/view")
        client.get(f"/jobs/{job_id}/download")
        assert all_events(env) == []
        privacy = client.get("/privacy").text
    assert "No cookies or scripts are used for this" not in privacy


def test_a_failing_event_never_breaks_what_the_person_was_doing(web, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.ERROR)

    def broken(*args, **kwargs):
        raise RuntimeError("SECRET-DOC-TEXT in an error message")

    monkeypatch.setattr(events, "_since", broken)
    job_id = finished(web)
    assert web.browser.get(f"/jobs/{job_id}/view").status_code == 200
    assert "event_failed" in caplog.messages
    assert "SECRET-DOC-TEXT" not in caplog.text  # the message is never logged


# --------------------------------------------------------------------------- admin


def test_admin_sees_counts_per_day_and_per_pseudonymous_person(web):
    finished(web)
    web.browser.get("/app")
    admin = web.client()
    login(admin, ADMIN)
    page = admin.get("/admin").text
    text = " ".join(page.split())
    assert 'id="usage-heading">Usage</h2>' in page
    assert "Events are kept for 400 days." in text
    today = datetime.now(UTC).date().isoformat()
    assert f"<th scope=\"row\">{today}</th>" in page
    alice = user_id(web, ALICE)
    assert f"<code>u-{alice[:8]}</code>" in page
    usage = page[page.index('id="usage-heading"') :]
    assert ALICE not in usage and "notes.pdf" not in usage  # no email, no file name
    # A table wider than the screen scrolls in a box the keyboard can reach.
    assert 'class="table-scroll" tabindex="0" role="region"' in usage


def test_usage_counts_feedback_and_minutes(tmp_path):
    from a11y_stem.web import db

    engine = db.make_engine(f"sqlite:///{tmp_path}/usage.db")
    db.create_all(engine)
    factory = db.make_sessionmaker(engine)
    now = datetime(2026, 10, 9, 12, tzinfo=UTC)
    with session_scope(factory) as session:
        user = User(email="a@example.org", is_allowed=True)
        session.add(user)
        session.flush()
        uid = user.id
        session.add_all(
            [
                events.make(uid, "downloaded", code="open", value=2, at=now),
                events.make(uid, "downloaded", code="done", value=0, at=now),
                events.make(uid, "will_post", code="yes", at=now),
                events.make(uid, "minutes_saved", value=30, at=now),
                events.make(uid, "minutes_saved", value=15, at=now - timedelta(days=40)),
            ]
        )
    with session_scope(factory) as session:
        usage = events.usage(session, days=28, now=now)
    assert [row.label for row in usage.by_day] == ["2026-10-09"]  # 40 days ago is outside
    day = usage.by_day[0]
    assert (day.cell("downloaded"), day.cell("downloaded:open")) == ("2", "1")
    (person,) = usage.by_person
    assert person.label == f"u-{uid[:8]}"
    assert (person.cell("will_post:yes"), person.cell("minutes"), person.cell("days")) == (
        "1",
        "45",
        "2",
    )
    engine.dispose()


# --------------------------------------------------------------------------- retention


def test_events_older_than_400_days_are_deleted_by_the_purge(web):
    now = datetime.now(UTC)
    alice = user_id(web, ALICE)
    with session_scope(web.ctx.factory) as session:
        session.add_all(
            [
                events.make(alice, "downloaded", value=0, at=now - timedelta(days=401)),
                events.make(alice, "downloaded", value=0, at=now - timedelta(days=399)),
            ]
        )
    stats = purge_expired(web.ctx.factory, now)
    assert stats.events_deleted == 1
    left = [e.created_at for e in all_events(web, "downloaded")]
    assert len(left) == 1
    assert events.EVENT_RETENTION_DAYS == 400


def test_a_jobs_events_outlive_the_job(web):
    job_id = finished(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.retain_until = datetime.now(UTC) - timedelta(minutes=1)
    assert purge_expired(web.ctx.factory).jobs_deleted == 1
    assert {e.name for e in all_events(web) if e.job_ref == job_id} == {"uploaded", "finished"}
