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
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

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
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import ConversionOutput
from readablestem.models import make_vision_client
from readablestem.models.base import VisionRequest
from readablestem.models.fake import FakeVisionClient
from readablestem.models.verifier import FakeVerifier
from readablestem.pipeline.layout import LayoutError
from readablestem.web import worker as worker_module
from readablestem.web.database import as_utc
from readablestem.web.db import Blob, Job, SpendDay, User, session_scope, utcnow
from readablestem.web.worker import (
    MAX_ATTEMPTS,
    JobCancelled,
    Worker,
    default_converter,
    error_code_for,
    job_caps,
    job_time_limit_s,
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
    assert job.html_stale is False
    assert set(blobs(env, job_id, "crop")) == {"p1-e3", "p1-e4", "p1-e5"}
    assert env.converter.calls[0]["pdf"] == pdf


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
    worker = env.worker(lease_s=3.0)
    seen: dict = {}

    def slow() -> None:
        first = as_utc(env.job(job_id).lease_until)
        # Wait for the heartbeat to extend the lease (not for a fixed time: a loaded
        # machine may run the beat late).
        deadline = time.monotonic() + 60
        while as_utc(env.job(job_id).lease_until) <= first and time.monotonic() < deadline:
            time.sleep(0.05)
        seen["requeued"] = env.worker(lease_s=3.0).requeue_expired()
        seen["first"], seen["lease"] = first, as_utc(env.job(job_id).lease_until)

    env.converter.before_return = slow
    worker.run_once()
    assert seen["requeued"] == (0, 0)
    assert seen["lease"] > seen["first"]
    assert env.job(job_id).status == "done"


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


def test_worker_thread_runs_in_the_app_lifespan(tmp_path):
    env = WebEnv(tmp_path, run_worker=True, worker_poll_s=0.05)
    with env.running() as client:
        worker = env.ctx.worker
        assert worker is not None and worker._thread is not None and worker._thread.is_alive()
        env.allow(ALICE)
        login(client, ALICE)
        job_id = upload(client, make_pdf(1)).headers["location"].rsplit("/", 1)[-1]
        deadline = time.monotonic() + 60
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


def test_the_heartbeat_notices_a_deleted_job_without_any_progress(env, caplog):
    caplog.set_level(logging.INFO)
    job_id = queue_job(env)
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        with session_scope(env.ctx.factory) as session:
            session.delete(session.get(Job, job_id))
        deadline = time.monotonic() + 60  # until a heartbeat (every 0.1 s) has found the job gone
        while time.monotonic() < deadline and not any(
            "job_cancelled" in r.getMessage() for r in caplog.records
        ):
            time.sleep(0.02)
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
            release.wait(120)  # stuck (a MuPDF loop, say)
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
    assert time.monotonic() - started < 60  # (the converter is stuck for two minutes)
    job = env.job(hung)
    assert job.status == "failed" and job.error_code == "timeout" and job.lease_until is None
    assert worker.requeue_expired(now=utcnow() + timedelta(hours=1)) == (0, 0)  # not retried
    assert worker.run_once() == after  # the hung document no longer blocks everyone else
    assert env.job(after).status == "done"
    release.set()
    deadline = time.monotonic() + 60
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


# ------------------------------------------------- the second automated check and the caps


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


def test_deleting_a_stuck_job_frees_the_worker_before_the_time_limit(env, caplog):
    caplog.set_level(logging.INFO)
    stuck = queue_job(env, age_s=60)
    release = threading.Event()

    def converter(pdf_bytes, /, *, settings, client, progress=None) -> ConversionOutput:
        with session_scope(env.ctx.factory) as session:
            session.delete(session.get(Job, stuck))
        release.wait(300)  # stuck after the user pressed "Delete now"
        return fake_output()

    worker = env.worker(lease_s=0.3, max_job_s=600.0, timeout_grace_s=0.2)
    worker.converter = converter
    claim = worker.claim()
    assert claim is not None
    started = time.monotonic()
    assert worker.process(claim) == "discarded"
    assert time.monotonic() - started < 120  # not the 600 s limit
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


# --------------------------------------------------------------------------- concurrency


def test_a_job_whose_cap_cannot_hold_that_many_calls_runs_fewer_steps(tmp_path):
    """One page on Claude is capped at $1.00 and a call can hold $0.14: seven at once."""
    from readablestem.ops.limits import largest_reservation_usd

    env = WebEnv(tmp_path, model_provider="anthropic")
    with env.running():
        env.allow(ALICE)
        queue_job(env)
        env.run_worker()
    cap, _ = job_caps(env.settings, 1)
    assert cap == 1.0
    assert env.converter.calls[0]["concurrency"] == int(
        cap // largest_reservation_usd(env.settings)
    )
    assert env.converter.calls[0]["concurrency"] == 7
