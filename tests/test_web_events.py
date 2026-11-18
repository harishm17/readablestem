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


OUTCOME = {
    "kind": "equation",
    "reason": "style_mismatch",
    "outcome": "kept",
    "origin": "page",
    "source": "pipeline",
    "mode": "single",
    "write_mode": "single",
    "release": "0.5.9",
}


@pytest.mark.parametrize("column", sorted(OUTCOME))
def test_an_outcome_column_takes_only_a_code_from_its_closed_list(column):
    """Each of a decision outcome's eight columns is checked on write: a value outside its
    list (a file name, an element id) or of 65 characters is refused, and so is any outcome
    column on another event."""
    assert events.make("u" * 32, events.DECISION_OUTCOME, value=1, **OUTCOME).kind == "equation"
    for bad in ("Week 3 notes.pdf", "p3-e12", "x" * 65):
        with pytest.raises(ValueError, match="closed list"):
            events.make("u" * 32, events.DECISION_OUTCOME, value=1, **(OUTCOME | {column: bad}))
    with pytest.raises(ValueError, match="only a decision outcome"):
        events.make("u" * 32, events.UPLOADED, **{column: OUTCOME[column]})


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


def test_outcome_cells_count_one_release_per_lane_and_withhold_small_cells(web):
    """`outcome_cells` (decision A15, rule 4 of WS11.18): equations from 5 accounts and 10
    documents are shown with their three measures; tables from 4 accounts are withheld, and
    with them the next smallest lane (figures), so "all" cannot give tables away. An admin's
    rows and another release's rows count nowhere, and no cell holds a user or job id."""
    ids: list[str] = []

    def outcome(user: str, job: str, kind: str, reason: str, what: str, release="0.5.9"):
        fields = OUTCOME | {"kind": kind, "reason": reason, "outcome": what, "release": release}
        source = "browsing" if reason == "none" else "pipeline"
        return events.make(user, events.DECISION_OUTCOME, job_ref=job, value=1,
                           **(fields | {"source": source}))  # fmt: skip

    with session_scope(web.ctx.factory) as session:
        users = [User(email=f"r{n}@example.org", is_admin=n == 9) for n in range(10)]
        session.add_all(users)
        session.flush()
        rows = []
        for n in range(20):  # 10 documents for accounts 0-4, 10 for accounts 5-8
            user = users[n // 2].id if n < 10 else users[5 + n % 4].id
            job = f"{n:032x}"
            ids += [user, job]
            rows.append(events.make(user, events.UPLOADED, job_ref=job, value=10))
            if n < 10:
                rows.append(outcome(user, job, "equation", "style_mismatch", "kept"))
                rows.append(outcome(user, job, "equation", "unknown_command", "edited"))
                rows.append(outcome(user, job, "equation", "none", "edited"))
                rows.append(outcome(user, job, "figure", "below_accept", "kept"))
                rows.append(outcome(user, job, "equation", "none", "edited", release="0.5.8"))
            else:
                rows.append(outcome(user, job, "table", "table_not_rebuilt", "replaced"))
        admin_job = "f" * 32
        rows.append(outcome(users[9].id, admin_job, "equation", "none", "added"))
        session.add_all(rows)
    with session_scope(web.ctx.factory) as session:
        cells = {c.lane: c for c in events.outcome_cells(session, "0.5.9")}
    equations = cells["equations"]
    assert equations.shown and (equations.accounts, equations.documents) == (5, 10)
    assert (equations.false_flags, equations.true_flags, equations.unflagged) == (10, 10, 10)
    assert equations.false_per_true == 1.0
    assert equations.unflagged_per_100_pages == 10.0  # 10 edits nobody was asked for, 100 pages
    assert equations.decisions_per_page_median == 0.3
    assert not cells["tables"].shown and cells["tables"].needs_accounts == 1
    assert cells["tables"].decisions == 0  # withheld: no number is kept
    assert not cells["figures"].shown  # withheld with it
    assert cells["all"].shown and cells["all"].decisions == 50 and cells["all"].accounts == 9
    assert not any(i in repr(cells) for i in [*ids, users[9].id, admin_job])


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
