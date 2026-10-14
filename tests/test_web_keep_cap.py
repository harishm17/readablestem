"""Keep 7 more days (T1-8) and the audit's cap on the documents one person keeps.

Uploads are refused at `MAX_LIVE_JOBS_PER_USER` live documents (test_audit_uploads.py).
Keeping a document longer must not be a way around that number: it is refused, with a
message and nothing changed, when it would put the person over it. A document that is
already live is already one of the counted, so at the cap the button still works; the
refusal matters when the number is lower than it was (a smaller cap after a release) or
when two requests raced past the upload check.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token, upload
from readablestem.web.db import Job, JobStatus, User, session_scope, utcnow
from readablestem.web.routes import job_settings
from readablestem.web.routes.jobs import MAX_LIVE_JOBS_PER_USER, live_job_count


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, make_pdf(1)))
        env.run_worker()
        yield env


def add_documents(env, email: str, count: int, *, expired: int = 0) -> None:
    """`count` more live documents for the person, and `expired` that are past their
    deletion time (waiting for the purge: they do not count)."""
    now = utcnow()
    with session_scope(env.ctx.factory) as session:
        user = session.query(User).filter(User.email == email).one()
        for number in range(count + expired):
            live = number < count
            session.add(
                Job(
                    user_id=user.id,
                    status=JobStatus.DONE,
                    filename=f"other-{number}.pdf",
                    page_count=1,
                    retention_mode="day",
                    retain_until=now + timedelta(hours=5) if live else now - timedelta(hours=1),
                    created_at=now,
                )
            )


def keep(env):
    csrf = token(env.browser, f"/jobs/{env.job_id}")
    return env.browser.post(f"/jobs/{env.job_id}/keep", data={"csrf_token": csrf})


def stored(env) -> Job:
    return env.job(env.job_id)


def test_the_cap_the_upload_uses_is_the_one_counted(env):
    add_documents(env, ALICE, 3, expired=2)
    with session_scope(env.ctx.factory) as session:
        user = session.query(User).filter(User.email == ALICE).one()
        assert live_job_count(session, user.id, utcnow()) == 4  # the upload above, and 3
        assert live_job_count(session, user.id, utcnow(), excluding=env.job_id) == 3


def test_a_person_at_the_cap_can_still_keep_a_document_they_already_hold(env):
    add_documents(env, ALICE, MAX_LIVE_JOBS_PER_USER - 1)
    response = keep(env)
    assert response.status_code == 303
    assert stored(env).retention_mode == "week"


def test_keeping_is_refused_over_the_cap_and_changes_nothing(env, monkeypatch):
    add_documents(env, ALICE, 4)
    monkeypatch.setattr(job_settings, "MAX_LIVE_JOBS_PER_USER", 3)
    before = stored(env)
    mode, until = before.retention_mode, before.retain_until
    response = keep(env)
    assert response.status_code == 429
    assert "You are keeping 5 documents, and the most allowed is 3." in response.text
    assert "Delete one you no longer need" in response.text
    assert "Nothing was changed." in response.text
    after = stored(env)
    assert (after.retention_mode, after.retain_until) == (mode, until)


def test_other_peoples_documents_and_expired_ones_do_not_count(env, monkeypatch):
    add_documents(env, BOB, 10)
    add_documents(env, ALICE, 1, expired=5)
    monkeypatch.setattr(job_settings, "MAX_LIVE_JOBS_PER_USER", 2)  # hers: this one and one
    assert keep(env).status_code == 303
    assert stored(env).retention_mode == "week"


def test_a_developer_look_document_is_not_extended_and_not_refused(env, monkeypatch):
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, env.job_id)
        assert job is not None
        job.retention_mode, job.opt_in_retention = "dev", True
    add_documents(env, ALICE, 4)
    monkeypatch.setattr(job_settings, "MAX_LIVE_JOBS_PER_USER", 3)
    before = stored(env).retain_until
    assert keep(env).status_code == 303  # "already kept for longer": the same notice as before
    assert stored(env).retain_until == before
