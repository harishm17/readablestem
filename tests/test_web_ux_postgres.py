"""The documents list and usage events on Postgres: the list reads only the title and
the fixes out of each stored result through JSON paths (`->`, `->>` here, `json_extract`
on SQLite), and events are counted, matched and purged the same way on both.

Uses test_web_postgres.py's server fixtures (TEST_POSTGRES_URL, else embedded pgserver).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import test_web_postgres as shared
from fixtures.web.support import fake_result, make_pdf, make_settings
from readablestem.ops.retention import purge_old_events
from readablestem.web import db, documents, events

pytestmark = pytest.mark.postgres

server_url = shared.server_url
pg_url = shared.pg_url
factory = shared.factory


def test_the_documents_list_reads_titles_and_open_decisions_on_postgres(factory):
    user_id = shared.add_user(factory)
    now = db.utcnow()
    result = fake_result()
    result.title = "Week 1: Vectors"
    stored = result.model_dump(mode="json", exclude={"html"})
    with db.session_scope(factory) as session:
        for name, status, json in [
            ("week1.pdf", "done", stored),
            ("queued.pdf", "queued", None),
        ]:
            session.add(
                db.Job(
                    user_id=user_id,
                    status=status,
                    filename=name,
                    page_count=1,
                    retain_until=now + timedelta(hours=24),
                    result_json=json,
                    created_at=now,
                )
            )
    with db.session_scope(factory) as session:
        listing = documents.documents(session, user_id, now=now, budget_closed=False)
        version = documents.list_version(session, user_id, now=now, budget_closed=False)
    (decide,) = listing.group("decide").rows
    assert (decide.title, decide.filename, decide.open_decisions) == (
        "Week 1: Vectors",
        "week1.pdf",
        3,
    )
    assert [row.title for row in listing.group("other").rows] == ["queued.pdf"]
    assert listing.version == version and listing.unfinished == 1


def test_events_are_matched_counted_and_purged_on_postgres(factory, tmp_path):
    settings = make_settings(tmp_path, session_secret="s" * 40)
    user_id = shared.add_user(factory)
    job_ids = [db.new_id() for _ in range(2)]
    pdf = make_pdf(1)
    for job_id in job_ids:
        events.record_upload(factory, settings, user_id=user_id, job_id=job_id, data=pdf, pages=1)
    old = db.utcnow() - timedelta(days=events.EVENT_RETENTION_DAYS + 1)
    with db.session_scope(factory) as session:
        session.add(events.make(user_id, events.SIGNED_IN, at=old))
        assert events.uploaded_at(session, user_id, job_ids[0]) is not None
    with db.session_scope(factory) as session:
        usage = events.usage(session)
        assert usage.by_person[0].cell(events.REUPLOADED) == "1"
        assert purge_old_events(session, db.utcnow()) == 1
    with db.session_scope(factory) as session:
        assert events.usage(session).by_person[0].cell(events.SIGNED_IN) == "0"
