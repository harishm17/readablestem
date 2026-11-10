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

from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)
from readablestem.ops.retention import purge_expired
from readablestem.web import events
from readablestem.web.db import Event, User, session_scope


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
        data={"csrf_token": token(env.browser), "action": action, "acknowledge": "on"},
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
