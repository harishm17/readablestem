"""Launch audit: privacy.

A canary document (every kind of content carries a SECRET-* marker) goes through upload,
conversion, review, download, failure and deletion. The markers must not appear in logs,
usage events, error aggregates, quota rows, cookies or response headers, must be gone from
every table when retention ends or the person deletes the document, and a user's erasure
leaves nothing of theirs behind. Retention is tested with a clock that is moved, through
the upload route, the page that opens a document and the scheduler's endpoint.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from fixtures.web.support import (
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
from readablestem.ops import retention
from readablestem.web import db
from readablestem.web.db import Blob, Job, ReviewEdit, session_scope
from readablestem.web.routes import cron as cron_routes
from readablestem.web.routes import jobs as job_routes

TOKEN_HEADER = {"X-Cron-Token": "c" * 40}
CRON = "/internal/cron/aggregate-and-purge"
CONTENT_TABLES = {"jobs", "blobs", "review_edits"}  # where a document legitimately lives


class Clock:
    """`utcnow` that moves when the test says so."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def dump(env, *, skip: set[str] = frozenset()) -> dict[str, str]:
    """Every column of every row of every table, as text, per table."""
    out: dict[str, str] = {}
    with session_scope(env.ctx.factory) as session:
        for table in db.Base.metadata.sorted_tables:
            if table.name in skip:
                continue
            parts = []
            for row in session.execute(select(table)).all():
                for value in row:
                    parts.append(
                        value.decode("latin-1") if isinstance(value, bytes) else repr(value)
                    )
            out[table.name] = "\n".join(parts)
    return out


def count(env, model) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def cookie_payload(client) -> str:
    """The text inside the signed session cookie (it is readable by its owner)."""
    value = next(iter(client.cookies.jar)).value
    body = value.split(".")[0]
    return base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8", "replace")


@pytest.fixture
def clock(monkeypatch):
    moving = Clock()
    monkeypatch.setattr(job_routes, "utcnow", moving)
    monkeypatch.setattr(cron_routes, "utcnow", moving)
    monkeypatch.setattr(db, "utcnow", moving)
    return moving


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def canary_pdf() -> bytes:
    return make_pdf(1, "SECRET-DOC-TEXT")


# --------------------------------------------------------------------------- nothing leaks


def test_the_canary_reaches_no_log_event_aggregate_cookie_or_header(web, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    client = web.browser
    job_id = job_id_from(upload(client, canary_pdf(), filename="SECRET-FILENAME.pdf", keep=True))
    web.run_worker()
    csrf = token(client, f"/jobs/{job_id}/review")
    seen_headers = []
    for path in (f"/jobs/{job_id}", f"/jobs/{job_id}/review", f"/jobs/{job_id}/download"):
        seen_headers.append(dict(client.get(path).headers))
    client.post(
        f"/jobs/{job_id}/review/p1-e4",
        data={"csrf_token": csrf, "action": "edit", "alt": "SECRET-ALT edited"},
    )
    web.converter.raises = RuntimeError("SECRET-DOC-TEXT in a parser error")
    failed = job_id_from(upload(client, canary_pdf(), filename="SECRET-FILENAME-2.pdf"))
    web.run_worker()
    assert web.job(failed).status == "failed"
    client.post(f"/jobs/{job_id}/delete", data={"csrf_token": token(client), "confirm": "yes"})
    # The scheduler's aggregation step, which reads finished jobs.
    web.browser.post(CRON, headers=TOKEN_HEADER)

    where = {
        "log records": "\n".join(f"{r.getMessage()} {r.args} {r.__dict__}" for r in caplog.records),
        "stdout": capsys.readouterr().out,
        "cookie": cookie_payload(client),
        "headers": json.dumps(seen_headers),
        **dump(web, skip=CONTENT_TABLES),
    }
    for place, text in where.items():
        for marker in SECRETS:
            assert marker not in text, (place, marker)
    assert ALICE not in where["log records"] + where["stdout"], "the email address was logged"
    assert caplog.records and where["stdout"]


def test_failed_jobs_keep_a_category_not_the_error(web):
    web.converter.raises = RuntimeError("SECRET-DOC-TEXT quoted by a parser")
    failed = job_id_from(upload(web.browser, canary_pdf()))
    web.run_worker()
    job = web.job(failed)
    assert job.status == "failed"
    assert job.error_code and "SECRET" not in job.error_code and len(job.error_code) <= 64
    page = web.browser.get(f"/jobs/{failed}").text
    assert "SECRET" not in page


def test_the_model_sdk_loggers_cannot_be_switched_to_debug_by_the_environment(monkeypatch):
    """The Anthropic and OpenAI SDKs log whole request bodies (document text and page
    images) at DEBUG when ANTHROPIC_LOG or OPENAI_LOG is set to debug."""
    from readablestem.config import Settings
    from readablestem.observability.logging import configure_logging

    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    monkeypatch.setenv("OPENAI_LOG", "debug")
    for name in ("anthropic", "anthropic._base_client", "openai", "httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    configure_logging(Settings(_env_file=None, log_level="DEBUG"))  # type: ignore[call-arg]
    import os

    assert "ANTHROPIC_LOG" not in os.environ and "OPENAI_LOG" not in os.environ
    for name in ("anthropic", "openai", "httpx", "httpx2"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name
    assert not logging.getLogger("anthropic._base_client").isEnabledFor(logging.DEBUG)


# --------------------------------------------------------------------------- retention


def tables_without_job_content(env, *, marker: str = "SECRET-") -> list[str]:
    return [name for name, text in dump(env).items() if marker in text]


def test_retention_follows_the_clock_through_the_whole_app(web, clock):
    client = web.browser
    short = job_id_from(upload(client, canary_pdf(), filename="SECRET-FILENAME.pdf"))
    kept = job_id_from(upload(client, canary_pdf(), filename="SECRET-FILENAME.pdf", keep=True))
    while web.run_worker():
        pass
    csrf = token(client, f"/jobs/{short}/review")
    client.post(f"/jobs/{short}/review/p1-e4", data={"csrf_token": csrf, "action": "reject"})
    assert count(web, ReviewEdit) == 1 and count(web, Blob) > 2

    clock.advance(hours=23, minutes=59)
    assert client.get(f"/jobs/{short}").status_code == 200
    assert client.post(CRON, headers=TOKEN_HEADER).json()["jobs_deleted"] == 0

    clock.advance(minutes=2)  # 24 h 1 min after upload
    gone = client.get(f"/jobs/{short}")
    assert gone.status_code == 404 and "SECRET" not in gone.text  # closed before the purge runs
    assert client.get(f"/jobs/{short}/download").status_code == 404
    assert client.get(f"/jobs/{short}/view").status_code == 404
    assert client.get(f"/jobs/{kept}").status_code == 200  # the opt-in one stays
    assert count(web, Job) == 2, "rows wait for the hourly purge"

    body = client.post(CRON, headers=TOKEN_HEADER).json()
    assert body["jobs_deleted"] == 1 and body["blobs_deleted"] >= 2
    assert count(web, Job) == 1 and count(web, ReviewEdit) == 0
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.id == short)) == 0
        assert (
            session.scalar(select(func.count()).select_from(Blob).where(Blob.job_id == short)) == 0
        )

    clock.advance(days=29)  # 30 days and 1 minute after the opt-in upload
    assert client.get(f"/jobs/{kept}").status_code == 404
    assert client.post(CRON, headers=TOKEN_HEADER).json()["jobs_deleted"] == 1
    assert count(web, Job) == 0 and count(web, Blob) == 0 and count(web, ReviewEdit) == 0
    assert tables_without_job_content(web) == []


def test_delete_now_removes_every_trace_at_once(web):
    client = web.browser
    job_id = job_id_from(upload(client, canary_pdf(), filename="SECRET-FILENAME.pdf"))
    web.run_worker()
    csrf = token(client, f"/jobs/{job_id}/review")
    client.post(
        f"/jobs/{job_id}/review/p1-e4",
        data={"csrf_token": csrf, "action": "edit", "alt": "SECRET-ALT again"},
    )
    assert "SECRET" in "".join(dump(web).values())
    response = client.post(
        f"/jobs/{job_id}/delete", data={"csrf_token": token(client), "confirm": "yes"}
    )
    assert response.status_code == 303
    assert count(web, Job) == 0 and count(web, Blob) == 0 and count(web, ReviewEdit) == 0
    assert tables_without_job_content(web) == []
    assert client.get(f"/jobs/{job_id}/download").status_code == 404


def test_erasing_a_person_removes_everything_of_theirs_and_nothing_of_anyone_else(web):
    mine = job_id_from(upload(web.browser, canary_pdf(), filename="SECRET-FILENAME.pdf"))
    bob = web.client()
    login(bob, BOB)
    theirs = job_id_from(upload(bob, make_pdf(1, "Bob's notes"), filename="bob.pdf"))
    while web.run_worker():
        pass
    csrf = token(web.browser, f"/jobs/{mine}/review")
    web.browser.post(f"/jobs/{mine}/review/p1-e4", data={"csrf_token": csrf, "action": "reject"})
    web.browser.post(f"/jobs/{mine}/feedback", data={"csrf_token": csrf, "saved_minutes": "30"})
    with session_scope(web.ctx.factory) as session:
        alice_id = session.scalar(select(db.User.id).where(db.User.email == ALICE))
        retention.erase_user(session, alice_id)
    assert web.job(theirs).status in {"done", "queued"}
    everything = dump(web)
    assert ALICE not in "".join(everything.values())
    assert "SECRET-FILENAME" not in "".join(everything.values())  # her file's name
    assert mine not in "".join(everything.values()), "even the id of her job is gone"
    assert web.browser.get("/app").status_code == 303  # the old session is worth nothing
    with session_scope(web.ctx.factory) as session:
        remaining = {row.user_id for row in session.execute(select(Job.user_id))}
        assert len(remaining) == 1 and alice_id not in remaining
    bob.close()


def test_the_purge_also_runs_in_the_worker_loop_so_cron_is_not_the_only_guard(web, clock):
    """If the scheduler stops, the worker's own backstop still deletes expired documents."""
    job_id = job_id_from(upload(web.browser, canary_pdf()))
    web.run_worker()
    clock.advance(hours=25)
    retention.purge_expired(web.ctx.factory, clock.now, source="worker")
    assert count(web, Job) == 0 and web.browser.get(f"/jobs/{job_id}").status_code == 404
