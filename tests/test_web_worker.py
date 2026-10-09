"""Worker: claiming, leases and requeueing, failure codes, result storage, spend."""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from a11y_stem.core.budget import BudgetExceeded, JobBudget
from a11y_stem.core.types import ConversionOutput
from a11y_stem.models import make_vision_client
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.verifier import FakeVerifier
from a11y_stem.pipeline.layout import LayoutError
from a11y_stem.web import worker as worker_module
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import Blob, Job, SpendDay, User, session_scope, utcnow
from a11y_stem.web.spend import DbSpendGuard
from a11y_stem.web.views import job_error_message
from a11y_stem.web.worker import (
    MAX_ATTEMPTS,
    JobCancelled,
    Worker,
    db_safe,
    default_converter,
    error_code_for,
    job_caps,
    job_time_limit_s,
    process_next_job,
)
from fixtures.web.support import (
    ALICE,
    SECRETS,
    WebEnv,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    make_settings,
    output_with_nul,
    token,
    upload,
)


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
    # A 1-page job gets the per-page allowance, which is below the per-job cap.
    assert (budget.max_usd, budget.max_calls) == job_caps(env.settings, 1)
    assert budget.max_usd == env.settings.max_usd_per_page < env.settings.max_usd_per_job
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
    [failed] = [r for r in caplog.records if r.getMessage() == "job_failed"]
    assert failed.a11y_fields["error_code"] == code
    assert failed.a11y_fields["error_type"].endswith(type(exc).__name__)
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
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


# --------------------------------------------------------------------------- cancellation


def paged_converter(pages: int, *, after_page: dict[int, Callable[[], None]], sent: list[int]):
    """Imitates `convert_pdf`: per page one model call, then progress. A BudgetExceeded
    from the client stops further model work (as WP4 does) but the pages still finish."""

    def convert(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        client.defaults["equation"] = {"latex": "x"}
        request = VisionRequest(
            step="equation", model=settings.main_model, prompt_version="v", instructions="i"
        )
        stopped = False
        for page in range(1, pages + 1):
            if not stopped:
                try:
                    client.run(request, Out)
                    sent.append(page)
                except BudgetExceeded:
                    stopped = True
            if progress is not None:
                progress(page, pages)
            if page in after_page:
                after_page[page]()
        return fake_output()

    return convert


def test_deleting_a_running_job_stops_its_model_calls_at_once(env, caplog):
    caplog.set_level(logging.INFO)
    browser = env.client()
    login(browser, ALICE)
    job_id = job_id_from(upload(browser, make_pdf(1)))
    csrf = token(browser, f"/jobs/{job_id}")
    sent: list[int] = []

    def delete_now() -> None:
        response = browser.post(
            f"/jobs/{job_id}/delete", data={"csrf_token": csrf, "confirm": "yes"}
        )
        assert response.status_code == 303

    worker = env.worker()
    worker.converter = paged_converter(5, after_page={1: delete_now}, sent=sent)
    assert worker.run_once() == job_id
    assert sent == [1]  # nothing was sent to the model after "Delete now"
    with session_scope(env.ctx.factory) as session:
        assert session.get(Job, job_id) is None
        assert session.scalar(select(Blob).where(Blob.job_id == job_id)) is None
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("job_cancelled") for m in messages)
    assert not any(m.startswith("job_failed") for m in messages)


def test_progress_aborts_a_conversion_whose_job_was_deleted(env):
    job_id = queue_job(env)
    pages_after: list[int] = []
    deleted = threading.Event()

    def delete_it() -> None:
        with session_scope(env.ctx.factory) as session:
            session.delete(session.get(Job, job_id))
        deleted.set()

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        assert progress is not None
        for page in range(1, 6):
            if deleted.is_set():
                pages_after.append(page)
            progress(page, 5)  # raises JobCancelled once the job is gone
            if page == 1:
                delete_it()
        return fake_output()

    worker = env.worker()
    worker.converter = converter
    claim = worker.claim()
    assert claim is not None
    assert worker.process(claim) == "discarded"
    assert pages_after == [2]  # page 2's progress call stopped the run


def test_losing_the_lease_cancels_the_conversion(env):
    job_id = queue_job(env)
    sent: list[int] = []
    stale = env.worker(lease_s=60)

    def taken_over() -> None:  # this worker stalled; another one requeued the job
        assert env.worker(lease_s=60).requeue_expired(now=utcnow() + timedelta(minutes=5)) == (
            1,
            0,
        )

    stale.converter = paged_converter(4, after_page={2: taken_over}, sent=sent)
    claim = stale.claim()
    assert claim is not None
    assert stale.process(claim) == "discarded"
    assert sent == [1, 2]
    assert env.job(job_id).status == "queued"  # left for the next worker, not failed


def test_the_heartbeat_notices_a_deleted_job_without_any_progress(env, caplog):
    caplog.set_level(logging.INFO)
    job_id = queue_job(env)
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        with session_scope(env.ctx.factory) as session:
            session.delete(session.get(Job, job_id))
        time.sleep(0.4)  # several heartbeats (lease 0.3 s, a beat every 0.1 s)
        seen["cancelled"] = any("job_cancelled" in r.getMessage() for r in caplog.records)
        try:
            client.budget.reserve(0.001)
        except JobCancelled:
            seen["refused"] = True
        return fake_output()

    worker = env.worker(lease_s=0.3)
    worker.converter = converter
    assert worker.run_once() == job_id
    assert seen == {"cancelled": True, "refused": True}


# --------------------------------------------------------------------------- time limit


def test_a_conversion_past_its_time_limit_fails_and_the_queue_moves_on(env, caplog):
    caplog.set_level(logging.INFO)
    hung = queue_job(env, age_s=60)
    after = queue_job(env)
    release = threading.Event()
    late: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        if not env.converter.calls:
            env.converter.calls.append({"pdf": pdf_bytes, "hung": True})
            release.wait(10)  # stuck (a MuPDF loop, say)
            try:
                client.budget.reserve(0.001)
            except JobCancelled:
                late["spend_refused"] = True
            return fake_output()
        return env.converter(pdf_bytes, settings=settings, client=client, progress=progress)

    worker = env.worker(lease_s=0.3, max_job_s=0.5, timeout_grace_s=0.1)
    worker.converter = converter
    started = time.monotonic()
    assert worker.run_once() == hung
    assert time.monotonic() - started < 5
    job = env.job(hung)
    assert job.status == "failed" and job.error_code == "timeout" and job.lease_until is None
    assert worker.requeue_expired(now=utcnow() + timedelta(hours=1)) == (0, 0)  # not retried
    assert worker.run_once() == after  # the hung document no longer blocks everyone else
    assert env.job(after).status == "done"
    release.set()
    deadline = time.monotonic() + 5
    while "spend_refused" not in late and time.monotonic() < deadline:
        time.sleep(0.02)
    assert late == {"spend_refused": True}
    assert env.job(hung).status == "failed"  # its late result was not stored
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "job_timeout" in messages and "conversion_abandoned" in messages


def test_the_time_limit_grows_with_the_page_count(env):
    settings = env.settings
    one, five = job_time_limit_s(1, settings), job_time_limit_s(5, settings)
    assert one < five
    assert job_time_limit_s(0, settings) == job_time_limit_s(settings.max_pages_per_job, settings)
    assert env.worker().time_limit_s(1) == one
    assert env.worker(max_job_s=3.0).time_limit_s(1) == 3.0


def test_retried_jobs_go_behind_fresh_ones(env):
    retried = queue_job(env, age_s=120)
    worker = env.worker(lease_s=60)
    now = utcnow()
    first = worker.claim(now=now)
    assert first is not None and first.job_id == retried
    assert worker.requeue_expired(now=now + timedelta(seconds=61)) == (1, 0)
    fresh = queue_job(env)
    second, third = worker.claim(), worker.claim()
    assert second is not None and second.job_id == fresh
    assert third is not None and third.job_id == retried and third.attempt == 2


# --------------------------------------------------------------------------- storing


def operational_error() -> OperationalError:
    return OperationalError("UPDATE jobs SET html=SECRET-DOC-TEXT", {}, Exception("gone"))


def test_a_store_failure_fails_the_job_instead_of_converting_it_again(env, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(worker_module, "STORE_RETRY_DELAY_S", 0.0)
    job_id = queue_job(env)
    worker = env.worker(lease_s=60)

    def broken(attempt, output):
        raise operational_error()

    monkeypatch.setattr(worker, "_write_result", broken)
    assert worker.run_once() == job_id
    job = env.job(job_id)
    assert job.status == "failed" and job.error_code == "store_failed"
    assert job.lease_until is None and job.html is None
    assert worker.requeue_expired(now=utcnow() + timedelta(hours=1)) == (0, 0)
    assert worker.run_once() is None
    assert len(env.converter.calls) == 1  # converted (and paid for) once
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "job_store_retry" in text and "job_store_failed" in text
    for secret in SECRETS:
        assert secret not in text


def test_a_transient_store_error_is_retried_once(env, monkeypatch):
    monkeypatch.setattr(worker_module, "STORE_RETRY_DELAY_S", 0.0)
    job_id = queue_job(env)
    worker = env.worker()
    real = worker._write_result
    calls = {"n": 0}

    def flaky(attempt, output):
        calls["n"] += 1
        if calls["n"] == 1:
            raise operational_error()
        return real(attempt, output)

    monkeypatch.setattr(worker, "_write_result", flaky)
    assert worker.run_once() == job_id
    assert env.job(job_id).status == "done" and calls["n"] == 2
    assert len(env.converter.calls) == 1


def test_a_job_deleted_while_its_result_is_written_stays_deleted(env, monkeypatch):
    job_id = queue_job(env)
    worker = env.worker()

    def deleted_meanwhile(attempt, output):
        with session_scope(env.ctx.factory) as session:
            session.delete(session.get(Job, job_id))
        raise IntegrityError("INSERT INTO blobs", {}, Exception("FOREIGN KEY"))

    monkeypatch.setattr(worker, "_write_result", deleted_meanwhile)
    claim = worker.claim()
    assert claim is not None
    assert worker.process(claim) == "discarded"
    with session_scope(env.ctx.factory) as session:
        assert session.get(Job, job_id) is None


def test_nul_characters_from_the_pdf_are_removed_before_storing(env):
    job_id = queue_job(env)
    env.converter.output = output_with_nul()
    assert env.run_worker() == job_id
    job = env.job(job_id)
    assert job.status == "done"
    assert job.html is not None and "\x00" not in job.html and "SECRET-DOC-TEXTtail" in job.html
    assert "\x00" not in json.dumps(job.result_json)
    assert job.result_json is not None and job.result_json["elements"][1]["text"] == "paragraph"


def test_db_safe_cleans_nested_strings_and_keys():
    assert db_safe({"a\x00": ["b\x00", {"c": "d\x00e"}], "n": 1, "x": None}) == {
        "a": ["b", {"c": "de"}],
        "n": 1,
        "x": None,
    }


# --------------------------------------------------------------------------- caps per job


def test_job_caps_cover_every_attempt_not_each_one(env):
    job_id = queue_job(env)
    settings = env.settings
    spent: dict = {}

    def first_attempt(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        assert progress is not None
        client.defaults["equation"] = {"latex": "x"}
        request = VisionRequest(
            step="equation", model=settings.main_model, prompt_version="v", instructions="i"
        )
        for _ in range(3):
            client.run(request, Out)
        progress(1, 2)  # records the spend so far on the job
        spent["usd"] = client.budget.spent_usd
        # The process stalls; another worker finds the lease expired and requeues the job.
        env.worker(lease_s=60).requeue_expired(now=utcnow() + timedelta(minutes=5))
        progress(2, 2)  # raises JobCancelled
        raise AssertionError("not reached")

    stale = env.worker(lease_s=60)
    stale.converter = first_attempt
    assert stale.run_once() == job_id
    job = env.job(job_id)
    assert job.status == "queued" and job.attempts == 1
    assert job.model_calls == 3 and job.cost_usd == pytest.approx(spent["usd"])

    assert env.run_worker() == job_id
    budget = env.converter.calls[-1]["client"].budget
    max_usd, max_calls = job_caps(settings, 1)
    assert budget.max_calls == max_calls - 3
    assert budget.max_usd == pytest.approx(max_usd - spent["usd"])
    job = env.job(job_id)
    assert job.status == "done" and job.attempts == 2
    assert job.model_calls == 3 + 3  # earlier attempt + this one (fake result: 3 calls)
    assert job.cost_usd == pytest.approx(spent["usd"] + 0.012)


def test_a_job_that_used_its_whole_budget_gets_no_more(env):
    job_id = queue_job(env)
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.cost_usd = env.settings.max_usd_per_job
        job.model_calls = 10
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        seen["max_usd"] = client.budget.max_usd
        try:
            client.budget.reserve(0.001)
        except BudgetExceeded as exc:
            seen["refused"] = exc.scope
        return fake_output()

    worker = env.worker()
    worker.converter = converter
    assert worker.run_once() == job_id
    assert seen == {"max_usd": 0.0, "refused": "job_usd"}


# ------------------------------------------------- the second automated check and the caps


def row_totals(env, job_id: str) -> tuple[float, int]:
    job = env.job(job_id)
    return round(float(job.cost_usd), 6), int(job.model_calls)


def test_a_second_check_request_holds_dollars_on_the_job_row_but_no_model_call(env):
    job_id = queue_job(env)
    _, max_calls = job_caps(env.settings, 1)
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.model_calls = max_calls  # the steps have used every model call
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        budget = client.budget
        try:
            budget.reserve(0.001)
        except BudgetExceeded as exc:
            seen["model_call"] = exc.scope
        reservation = budget.reserve(0.002, count_call=False)
        seen["held"] = row_totals(env, job_id)
        budget.settle(reservation, 0.001)
        seen["settled"] = row_totals(env, job_id)
        with session_scope(env.ctx.factory) as session:  # the day is all but spent
            session.execute(update(SpendDay).values(spent_usd=settings.daily_spend_cap_usd))
        try:
            budget.reserve(0.002, count_call=False)
        except BudgetExceeded as exc:
            seen["daily"] = exc.scope
        seen["released"] = row_totals(env, job_id)
        return fake_output()

    worker = env.worker()
    worker.converter = converter
    assert worker.run_once() == job_id
    assert seen == {
        "model_call": "job_calls",
        "held": (0.002, max_calls),
        "settled": (0.001, max_calls),
        "daily": "daily",
        "released": (0.001, max_calls),  # the refused hold came off, no call taken off
    }


LECTURE_NOTES = Path(__file__).parent / "fixtures" / "layout" / "lecture_notes.pdf"
ALARM = {"match": 0.01, "cosmetic": 0.01, "meaning_changing": 0.98}


def offline_client_with_accepted_figures(settings, budget):
    """The worker's offline client (`make_vision_client(fake=True)`, with its verifier on
    the job's budget), answering figures well enough to be accepted, so the figure check
    runs too, and with a second check that flags every figure and every equation."""
    client = make_vision_client(settings, budget, fake=True)
    assert isinstance(client, FakeVisionClient)
    client.defaults["figure"] = {
        "figure_type": "diagram",
        "decorative": False,
        "alt": "A labelled diagram of the forces on a block.",
        "confidence": 0.95,
    }
    if isinstance(client.verifier, FakeVerifier):
        client.verifier.figure = {"ok": 0.1, "factual_error": 0.9}
        client.verifier.equation = ALARM
    return client


def convert_on_the_worker(env, settings) -> Job:
    job_id = queue_job(env, pdf=LECTURE_NOTES.read_bytes())
    worker = Worker(
        settings,
        env.ctx.factory,
        converter=default_converter(),
        client_factory=offline_client_with_accepted_figures,
    )
    assert worker.run_once() == job_id
    job = env.job(job_id)
    assert job.status == "done" and job.result_json is not None
    return job


def test_the_checks_change_nothing_on_a_document_at_the_generator_only_call_cap(env, tmp_path):
    # The production path: `job_caps`, `_AttemptBudget` -> `_hold_on_job` on the job row,
    # the real pipeline and the offline client the worker builds. The call cap is set to
    # exactly what the document's steps need. Before the fix the checks used it up and
    # the last elements failed "budget_job_calls".
    def caps(calls: int, checks: bool):
        return make_settings(
            tmp_path,
            max_model_calls_per_job=calls,
            max_model_calls_per_page=1000,
            max_usd_per_page=2.0,
            decisions_figure_verify=checks,
            decisions_equation_shadow=checks,
        )

    needed = convert_on_the_worker(env, caps(400, checks=False)).model_calls
    off = convert_on_the_worker(env, caps(needed, checks=False))
    on = convert_on_the_worker(env, caps(needed, checks=True))

    def outcome(job: Job) -> list[tuple]:
        assert job.result_json is not None
        return [
            (
                f["element_id"],
                f["status"],
                f.get("reason"),
                f.get("confidence"),
                [c["model"] for c in f.get("calls", [])],
            )
            for f in job.result_json["fixes"]
        ]

    assert outcome(on) == outcome(off)
    assert on.html == off.html
    assert on.model_calls == off.model_calls == needed
    assert on.error_code is None and off.error_code is None
    assert on.result_json is not None
    fixes = on.result_json["fixes"]
    assert not any((f.get("reason") or "").startswith("budget_") for f in fixes)
    checks = [(v["check"], v["outcome"]) for f in fixes for v in f.get("verifications", [])]
    assert ("figure_alt", "ok") in checks and ("equation_match", "ok") in checks
    assert any(f.get("review") == "optional" for f in fixes if f["kind"] == "figure")
    assert on.cost_usd > off.cost_usd  # the checks are paid for, in dollars


@pytest.mark.parametrize(
    "code",
    ["timeout", "store_failed", "lease_expired", "upload_missing", "internal_error"],
)
def test_every_worker_failure_code_has_a_plain_explanation(code):
    assert job_error_message(code) != job_error_message("no-such-code")


def test_deleting_a_stuck_job_frees_the_worker_before_the_time_limit(env, caplog):
    caplog.set_level(logging.INFO)
    stuck = queue_job(env, age_s=60)
    release = threading.Event()

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        with session_scope(env.ctx.factory) as session:
            session.delete(session.get(Job, stuck))
        release.wait(10)  # stuck after the user pressed "Delete now"
        return fake_output()

    worker = env.worker(lease_s=0.3, max_job_s=30.0, timeout_grace_s=0.2)
    worker.converter = converter
    claim = worker.claim()
    assert claim is not None
    started = time.monotonic()
    assert worker.process(claim) == "discarded"
    assert time.monotonic() - started < 5  # not the 30 s limit
    release.set()
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "conversion_abandoned" in messages and "job_timeout" not in messages


# --------------------------------------------------------------------------- tracing


class RecordingTracer:
    """A tracer that keeps span names and attributes (content-free by contract)."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict]] = []
        self.flushed = 0

    @contextlib.contextmanager
    def span(self, name: str, **attrs):
        record = (name, dict(attrs))
        self.spans.append(record)

        class Handle:
            def set(self, **more) -> None:
                record[1].update(more)

            update = set

        yield Handle()

    def flush(self) -> None:
        self.flushed += 1


def test_with_tracing_on_the_app_hands_one_tracer_to_every_conversion(tmp_path, monkeypatch):
    """make_tracer used to have no caller: TRACING_ENABLED did nothing."""
    from a11y_stem.pipeline.run import convert_pdf
    from a11y_stem.web import app as app_module

    tracer = RecordingTracer()
    monkeypatch.setattr(app_module, "make_tracer", lambda settings: tracer)
    env = WebEnv(tmp_path, run_worker=True, worker_poll_s=60, tracing_enabled=True)
    with env.running():
        assert env.ctx.tracer is tracer
        assert env.ctx.worker is not None and env.ctx.worker.tracer is tracer
        env.allow(ALICE)
        job_id = queue_job(env)
        worker = Worker(
            env.settings,
            env.ctx.factory,
            converter=convert_pdf,
            client_factory=lambda s, b: FakeVisionClient(budget=b),
            tracer=env.ctx.tracer,
        )
        assert worker.run_once() == job_id
    assert env.job(job_id).status == "done"
    names = [name for name, _ in tracer.spans]
    assert names[0] == "document" and "assemble" in names
    assert tracer.flushed >= 1  # flushed when the app shuts down


def test_without_tracing_the_converter_gets_no_tracer_argument(env):
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None, **extra):
        seen.update(extra)
        return fake_output()

    assert env.ctx.tracer is None
    queue_job(env)
    worker = env.worker()
    worker.converter = converter
    worker.run_once()
    assert seen == {}
