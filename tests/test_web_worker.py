"""Worker: claiming, leases and requeueing, failure codes, result storage, spend."""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from a11y_stem.core.budget import BudgetExceeded, JobBudget
from a11y_stem.core.types import ConversionOutput
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline.layout import LayoutError
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import Blob, Job, SpendDay, User, session_scope, utcnow
from a11y_stem.web.spend import DbSpendGuard
from a11y_stem.web.worker import MAX_ATTEMPTS, error_code_for, process_next_job
from fixtures.web.support import ALICE, SECRETS, WebEnv, login, make_pdf, upload


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path)
    with env.running():
        env.allow(ALICE)
        yield env


def queue_job(env, *, pdf: bytes | None = None, age_s: float = 0.0) -> str:
    with session_scope(env.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == ALICE))
        assert user is not None
        now = utcnow() - timedelta(seconds=age_s)
        job = Job(
            user_id=user.id,
            filename="SECRET-FILENAME.pdf",
            page_count=1,
            retain_until=now + timedelta(hours=24),
            created_at=now,
        )
        session.add(job)
        session.flush()
        session.add(
            Blob(
                job_id=job.id,
                kind="upload",
                media_type="application/pdf",
                content=pdf or make_pdf(1),
            )
        )
        return job.id


def blobs(env, job_id: str, kind: str) -> dict[str | None, bytes]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(select(Blob).where(Blob.job_id == job_id, Blob.kind == kind))
        return {b.element_id: b.content for b in rows}


def test_worker_converts_a_job_and_stores_everything(env):
    pdf = make_pdf(1, text="unique bytes")
    job_id = queue_job(env, pdf=pdf)
    assert env.run_worker() == job_id
    job = env.job(job_id)
    assert job.status == "done"
    assert job.finished_at is not None and job.lease_until is None
    assert job.pages_done == job.page_count == 1
    assert job.cost_usd == pytest.approx(0.012) and job.model_calls == 3
    assert job.result_json is not None and "html" not in job.result_json
    assert job.result_json["fixes"][2]["status"] == "needs_review"
    assert job.html is not None and "SECRET-DOC-TEXT" in job.html
    assert set(blobs(env, job_id, "crop")) == {"p1-e3", "p1-e4", "p1-e5"}
    assert env.converter.calls[0]["pdf"] == pdf


def test_converter_gets_a_client_built_on_a_job_budget_under_the_daily_guard(env):
    queue_job(env)
    env.run_worker()
    client = env.converter.calls[0]["client"]
    assert isinstance(client, FakeVisionClient)
    budget = client.budget
    assert isinstance(budget, JobBudget)
    assert budget.max_usd == env.settings.max_usd_per_job
    assert budget.max_calls == env.settings.max_model_calls_per_job
    assert isinstance(budget.guard, DbSpendGuard)
    assert budget.guard.cap_usd == env.settings.daily_spend_cap_usd


class Out(BaseModel):
    latex: str


def test_model_calls_made_by_a_job_are_counted_in_the_daily_spend(env):
    def spending_converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        request = VisionRequest(
            step="equation", model=settings.main_model, prompt_version="v", instructions="i"
        )
        client.defaults["equation"] = {"latex": "x"}
        for _ in range(3):
            client.run(request, Out)
        return env.converter.output.model_copy(deep=True)

    worker = env.worker()
    worker.converter = spending_converter
    queue_job(env)
    worker.run_once()
    with session_scope(env.ctx.factory) as session:
        day = session.scalar(select(SpendDay))
        assert day is not None
        assert day.spent_usd > 0 and day.held_usd == pytest.approx(0.0)


def test_claims_take_the_oldest_job_first_and_never_the_same_job_twice(env):
    old = queue_job(env, age_s=60)
    new = queue_job(env)
    first, second = env.worker(), env.worker()
    claim = first.claim()
    assert claim is not None and claim.job_id == old and claim.attempt == 1
    other = second.claim()
    assert other is not None and other.job_id == new
    assert first.claim() is None


def test_concurrent_claims_on_sqlite_are_exclusive(env):
    ids = {queue_job(env) for _ in range(12)}
    claimed: list[str] = []
    lock = threading.Lock()

    def run():
        worker = env.worker()
        while (claim := worker.claim()) is not None:
            with lock:
                claimed.append(claim.job_id)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids)


def test_expired_leases_are_requeued_then_failed_after_three_attempts(env):
    job_id = queue_job(env)
    worker = env.worker(lease_s=60)
    now = utcnow()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        claim = worker.claim(now=now)
        assert claim is not None and claim.attempt == attempt
        assert worker.requeue_expired(now=now + timedelta(seconds=30)) == (0, 0)  # still leased
        later = now + timedelta(seconds=61)
        expected = (1, 0) if attempt < MAX_ATTEMPTS else (0, 1)
        assert worker.requeue_expired(now=later) == expected
        now = later
    job = env.job(job_id)
    assert job.status == "failed" and job.error_code == "lease_expired"
    assert job.attempts == MAX_ATTEMPTS and job.lease_until is None


def test_heartbeat_keeps_a_long_conversion_leased(env):
    job_id = queue_job(env)
    worker = env.worker(lease_s=0.3)
    seen: dict = {}

    def slow() -> None:
        time.sleep(0.7)  # longer than the lease; the heartbeat must extend it
        seen["requeued"] = env.worker(lease_s=0.3).requeue_expired()
        seen["lease"] = as_utc(env.job(job_id).lease_until)

    env.converter.before_return = slow
    started = utcnow()
    worker.run_once()
    assert seen["requeued"] == (0, 0)
    assert seen["lease"] > started + timedelta(seconds=0.6)
    assert env.job(job_id).status == "done"


def test_progress_updates_pages_done(env):
    job_id = queue_job(env)
    seen = {}
    env.converter.before_return = lambda: seen.setdefault("pages_done", env.job(job_id).pages_done)
    env.run_worker()
    assert seen["pages_done"] == 1  # FakeConverter reports progress(1, 1)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (LayoutError("encrypted", "SECRET-DOC-TEXT cannot decrypt"), "encrypted"),
        (LayoutError("Weird Code with SECRET-DOC-TEXT", "x"), "layout_error"),
        (BudgetExceeded("daily", "daily spend cap $5.00 reached"), "budget_daily"),
        (BudgetExceeded("job_usd", "job spend cap reached"), "budget_job_usd"),
        (RuntimeError("SECRET-DOC-TEXT \\SECRETLATEX broke"), "internal_error"),
    ],
)
def test_failures_become_category_codes_and_logs_stay_content_free(env, caplog, exc, code):
    caplog.set_level(logging.DEBUG)
    job_id = queue_job(env)
    env.converter.raises = exc
    env.run_worker()
    job = env.job(job_id)
    assert job.status == "failed" and job.error_code == code
    assert job.finished_at is not None and job.lease_until is None
    assert error_code_for(exc) == code
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert f"error_code={code}" in text and type(exc).__name__ in text
    for secret in SECRETS:
        assert secret not in text


def test_a_job_deleted_while_converting_is_not_resurrected(env):
    job_id = queue_job(env)

    def delete_it() -> None:
        with session_scope(env.ctx.factory) as session:
            session.delete(session.get(Job, job_id))

    env.converter.before_return = delete_it
    worker = env.worker()
    claim = worker.claim()
    assert claim is not None
    assert worker.process(claim) == "discarded"
    with session_scope(env.ctx.factory) as session:
        assert session.get(Job, job_id) is None
        assert session.scalar(select(Blob).where(Blob.job_id == job_id)) is None


def test_a_stale_worker_cannot_overwrite_a_requeued_job(env):
    job_id = queue_job(env)
    stale, fresh = env.worker(lease_s=60), env.worker(lease_s=60)
    now = utcnow()
    old_claim = stale.claim(now=now)
    assert old_claim is not None
    stale.requeue_expired(now=now + timedelta(seconds=120))  # the stale worker "crashed"
    new_claim = fresh.claim()
    assert new_claim is not None and new_claim.attempt == 2
    assert stale.process(old_claim) == "discarded"
    assert env.job(job_id).status == "running"
    assert fresh.process(new_claim) == "done"


def test_process_next_job_on_an_empty_queue(env):
    assert (
        process_next_job(
            env.settings,
            env.ctx.factory,
            converter=env.converter,
            client_factory=lambda s, b: FakeVisionClient(budget=b),
        )
        is None
    )


def test_worker_thread_runs_in_the_app_lifespan(tmp_path):
    env = WebEnv(tmp_path, run_worker=True, worker_poll_s=0.05)
    with env.running() as client:
        worker = env.ctx.worker
        assert worker is not None and worker._thread is not None and worker._thread.is_alive()
        env.allow(ALICE)
        login(client, ALICE)
        job_id = upload(client, make_pdf(1)).headers["location"].rsplit("/", 1)[-1]
        deadline = time.monotonic() + 10
        while env.job(job_id).status != "done" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert env.job(job_id).status == "done"
    assert not worker._thread.is_alive()


def test_worker_loop_survives_errors(env, caplog):
    worker = env.worker()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database went away")
        worker._stop.set()
        return None

    worker.run_once = flaky  # type: ignore[method-assign]
    worker.settings = env.settings.model_copy(update={"worker_poll_s": 0.01})
    worker._loop()
    assert calls["n"] == 2
    assert any("worker_loop_error" in r.getMessage() for r in caplog.records)
