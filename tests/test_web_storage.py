"""Stored-bytes caps: per person (by tier), for the service, and what frees the space.

Uploads live in Postgres on a volume of fixed size, so a person cannot store without limit and
the service stops taking uploads while it holds too much (`web/storage.py`).
"""

from __future__ import annotations

import re
from datetime import timedelta
from html import unescape

import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, BOB, WebEnv, login, make_pdf, token
from readablestem.ops import retention
from readablestem.web import access, pending, storage
from readablestem.web.db import Blob, Job, PendingUpload, User, session_scope, utcnow
from readablestem.web.intake import store_job

MB = 1024 * 1024


def padded(megabytes: float, pages: int = 1) -> bytes:
    """A PDF with trailing bytes, so it weighs what a test needs."""
    return make_pdf(pages) + b"\n%" + b"x" * int(megabytes * MB)


def flat(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


@pytest.fixture
def web(tmp_path):
    env = WebEnv(
        tmp_path,
        storage_per_user_mb=3,
        storage_open_tier_mb=1,
        storage_global_max_mb=5,
        max_upload_mb=2,
        max_large_upload_mb=2,
        per_user_daily_page_quota=100,
        max_source_pages=40,
        max_pages_per_job=5,
    )
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.alice = client
        yield env


def uid(env, email: str) -> str:
    with session_scope(env.ctx.factory) as session:
        return session.scalar(select(User.id).where(User.email == email))


def store(env, email: str, size: int, **kw) -> str:
    return store_job(
        env.ctx.factory, env.settings, uid(env, email), "a.pdf", bytes(size), 1, "day", **kw
    )


def fill_service(env) -> None:
    """Bob's documents, written past his own cap, so the service as a whole is full (5 MB)."""
    with session_scope(env.ctx.factory) as session:
        for _ in range(2):
            job = Job(
                user_id=uid_in(session, BOB),
                status="done",
                filename="b.pdf",
                retain_until=utcnow() + timedelta(hours=24),
            )
            session.add(job)
            session.flush()
            session.add(
                Blob(
                    job_id=job.id,
                    kind="upload",
                    media_type="application/pdf",
                    content=bytes(int(2.6 * MB)),
                )
            )


def uid_in(session, email: str) -> str:
    return session.scalar(select(User.id).where(User.email == email))


def used(env, email: str) -> int:
    with session_scope(env.ctx.factory) as session:
        return storage.user_bytes(session, uid(env, email))


def post(client, data: bytes, name: str = "a.pdf"):
    return client.post(
        "/jobs",
        data={"csrf_token": token(client)},
        files={"pdf": (name, data, "application/pdf")},
    )


# --------------------------------------------------------------------------- per person


def test_the_cap_is_read_from_the_settings_by_tier(web):
    s = web.settings
    assert access.storage_limit_mb(s, access.INVITED) == 3
    assert access.storage_limit_mb(s, access.OPEN) == 1
    from readablestem.config import Settings

    defaults = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (defaults.storage_per_user_mb, defaults.storage_open_tier_mb) == (300, 150)
    assert defaults.storage_global_max_mb == 2000


def test_what_a_person_stores_counts_every_kind_of_content(web):
    job_id = store(web, ALICE, 1000)
    with session_scope(web.ctx.factory) as session:
        session.add(
            Blob(
                job_id=job_id,
                kind="crop",
                element_id="e1",
                media_type="image/png",
                content=bytes(500),
            )
        )
        job = session.get(Job, job_id)
        job.html = "h" * 200
        job.result_json = {"k": "v"}
        session.add(
            PendingUpload(
                user_id=job.user_id,
                filename="big.pdf",
                pages=9,
                size_bytes=4000,
                content=b"",
                retain_until=utcnow() + timedelta(hours=1),
                done_pages=[],
                retention_mode="day",
            )
        )
    assert used(web, ALICE) >= 1000 + 500 + 200 + 4000
    assert used(web, BOB) == 0  # nobody else's


def test_an_upload_over_the_cap_is_refused_and_says_how_much_is_used(web):
    assert post(web.alice, padded(1.2)).status_code == 303
    assert post(web.alice, padded(1.2)).status_code == 303  # 2.4 of 3 MB
    response = post(web.alice, padded(1.2), name="third.pdf")
    assert response.status_code == 413
    text = flat(response.text)
    assert "You are storing 3 MB of the 3 MB you can keep" in text
    assert "third.pdf would go over it" in text
    assert "Delete documents you no longer need" in text and "Nothing was uploaded" in text
    with session_scope(web.ctx.factory) as session:
        assert len(list(session.scalars(select(Job)))) == 2  # nothing stored, no pages taken


def test_deleting_a_document_frees_the_room(web):
    first = post(web.alice, padded(1.2)).headers["location"].rsplit("/", 1)[-1]
    post(web.alice, padded(1.2))
    assert post(web.alice, padded(1.2)).status_code == 413
    with session_scope(web.ctx.factory) as session:
        retention.delete_jobs(session, [first])
    assert post(web.alice, padded(1.2)).status_code == 303


def test_a_refused_upload_takes_no_daily_pages(web):
    store(web, ALICE, int(2.9 * MB))
    from readablestem.web.quota import pages_left

    with session_scope(web.ctx.factory) as session:
        before = pages_left(session, uid(web, ALICE), quota=100, day=utcnow().date())
    assert post(web.alice, padded(0.5)).status_code == 413
    with session_scope(web.ctx.factory) as session:
        after = pages_left(session, uid(web, ALICE), quota=100, day=utcnow().date())
    assert before == after


def test_a_new_account_has_the_smaller_cap(web):
    with session_scope(web.ctx.factory) as session:
        session.get(User, uid(web, BOB)).access_tier = access.OPEN
    with pytest.raises(storage.StorageRefused) as refused:
        store(web, BOB, int(1.5 * MB))
    assert refused.value.limit_mb == 1
    store(web, ALICE, int(1.5 * MB))  # the invited tier has 3


def test_the_check_is_exact_at_the_cap(web):
    store(web, ALICE, 2 * MB)
    store(web, ALICE, MB - 100)  # all but the HTML and result of the first two: under 3 MB
    with pytest.raises(storage.StorageRefused):
        store(web, ALICE, MB)


def test_a_large_file_kept_for_its_pages_counts_and_is_refused_over_the_cap(web):
    with session_scope(web.ctx.factory) as session:
        pending.create(
            session,
            web.settings,
            user_id=uid(web, ALICE),
            filename="big.pdf",
            data=bytes(2 * MB),
            pages=50,
            mode="day",
            course_id=None,
            exam=False,
        )
    assert used(web, ALICE) >= 2 * MB
    with pytest.raises(storage.StorageRefused), session_scope(web.ctx.factory) as session:
        pending.create(
            session,
            web.settings,
            user_id=uid(web, ALICE),
            filename="big2.pdf",
            data=bytes(2 * MB),
            pages=50,
            mode="day",
            course_id=None,
            exam=False,
        )


def test_a_file_past_its_deletion_time_no_longer_counts(web):
    store(web, ALICE, 2 * MB)
    with session_scope(web.ctx.factory) as session:
        for job in session.scalars(select(Job)):
            job.retain_until = utcnow() - timedelta(minutes=1)
    assert used(web, ALICE) == 0


# --------------------------------------------------------------------------- the service


def test_uploads_are_turned_away_while_the_service_holds_too_much(web):
    fill_service(web)  # over STORAGE_GLOBAL_MAX_MB (5)
    response = post(web.alice, make_pdf(1))
    assert response.status_code == 503
    text = flat(response.text)
    assert "Uploads are paused while storage catches up" in text
    assert "deleted 24 hours after" in text and "Try again in an hour" in text
    assert response.headers["retry-after"] == "3600"
    with session_scope(web.ctx.factory) as session:
        assert len(list(session.scalars(select(Job)))) == 2  # only Bob's two


def test_the_store_itself_refuses_when_full_whatever_the_route(web):
    fill_service(web)
    with pytest.raises(storage.StorageFull):
        store(web, ALICE, 10)


def test_room_comes_back_when_the_purge_catches_up(web):
    fill_service(web)
    assert post(web.alice, make_pdf(1)).status_code == 503
    with session_scope(web.ctx.factory) as session:
        for job in session.scalars(select(Job)):
            job.retain_until = utcnow() - timedelta(minutes=1)
    # past its time it no longer counts, before the purge has even run
    assert post(web.alice, make_pdf(1)).status_code == 303
    stats = retention.purge_expired(web.ctx.factory)
    assert stats.jobs_deleted == 2 and stats.blobs_deleted == 2


def test_healthz_ops_shows_the_storage_and_goes_stale_while_full(web):
    with session_scope(web.ctx.factory) as session:
        from readablestem.ops import heartbeat

        for name in heartbeat.MAX_AGE:
            heartbeat.beat(session, name)
    body = web.alice.get("/healthz/ops")
    assert body.status_code == 200
    assert body.json()["storage"] == {"stored_mb": 0, "limit_mb": 5, "full": False}
    fill_service(web)
    body = web.alice.get("/healthz/ops")
    assert body.status_code == 503
    assert body.json()["stale"] == ["storage"]
    assert body.json()["storage"]["full"] is True and body.json()["storage"]["stored_mb"] == 5


# --------------------------------------------------------------------------- the parts page


def test_choosing_parts_is_held_to_the_cap_and_the_file_is_kept(web):
    from fixtures.web.support import csrf_from

    response = post(web.alice, padded(1.5, pages=12))
    assert response.status_code == 303 and response.headers["location"].startswith("/uploads/")
    location = response.headers["location"]
    with session_scope(web.ctx.factory) as session:  # fill all but a few hundred bytes
        left = web.settings.storage_per_user_mb * MB - storage.user_bytes(
            session, uid_in(session, ALICE)
        )
    store(web, ALICE, left - 300)
    page = web.alice.get(location)
    result = web.alice.post(
        location,
        data={"csrf_token": csrf_from(page.text), "parts": "1-5", "shrink": ""},
    )
    assert result.status_code == 413
    text = flat(result.text)
    assert "of the 3 MB you can keep" in text and "this file is kept" in text


# --------------------------------------------------------------------------- freeing space


def test_the_purge_deletes_the_large_blobs_and_the_kept_files(web):
    job_id = store(web, ALICE, int(1.5 * MB))
    with session_scope(web.ctx.factory) as session:
        session.add(
            PendingUpload(
                user_id=uid(web, ALICE),
                filename="big.pdf",
                pages=9,
                size_bytes=2 * MB,
                content=bytes(2 * MB),
                retain_until=utcnow() - timedelta(minutes=1),
                done_pages=[],
                retention_mode="day",
            )
        )
        session.get(Job, job_id).retain_until = utcnow() - timedelta(minutes=1)
    stats = retention.purge_expired(web.ctx.factory)
    assert (stats.jobs_deleted, stats.blobs_deleted, stats.pending_deleted) == (1, 1, 1)
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(Blob.id)) is None
        assert session.scalar(select(PendingUpload.id)) is None


def test_vacuum_runs_after_a_purge_on_postgres_only(web):
    stats = retention.PurgeStats(jobs_deleted=1, blobs_deleted=1, unaggregated_deleted=0)
    assert retention.vacuum_after_purge(web.ctx.factory, stats) is False  # SQLite here

    ran: list[str] = []

    class Conn:
        def exec_driver_sql(self, sql):
            ran.append(sql)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Engine:
        class dialect:
            name = "postgresql"

        def connect(self):
            conn = Conn()
            conn.execution_options = lambda **kw: conn
            return conn

    factory = type("F", (), {"kw": {"bind": Engine()}})()
    assert retention.vacuum_after_purge(factory, stats) is True
    assert ran == ["VACUUM blobs", "VACUUM pending_uploads", "VACUUM jobs"]
    ran.clear()
    nothing = retention.PurgeStats(jobs_deleted=0, blobs_deleted=0, unaggregated_deleted=0)
    assert retention.vacuum_after_purge(factory, nothing) is False and ran == []


def test_the_limits_page_names_the_storage_from_the_settings(web):
    text = flat(web.alice.get("/limits").text)
    assert "keep up to 3 MB of uploads and results at once" in text
