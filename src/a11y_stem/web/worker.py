"""Background worker: claims queued jobs from the database and converts them.

One worker thread runs inside the web process (Cloud Run, min-instances=1), but the
queue is safe with several workers or instances:

- Claiming is atomic. Postgres: `SELECT ... FOR UPDATE SKIP LOCKED` (two workers never
  wait on, or take, the same row). SQLite: a conditional UPDATE (`... WHERE status =
  'queued'`) whose rowcount says who won. Fresh jobs go before retried ones (fewest
  attempts first, then oldest), so a PDF that keeps killing the process cannot jump
  ahead of everyone else's work after each restart.
- The conversion runs on its own thread. The worker thread waits for it and extends the
  job's lease (`lease_until`) every `lease_s / 3`. If the process dies, the lease runs
  out and the next worker requeues the job; after `MAX_ATTEMPTS` claims it is failed
  with "lease_expired" instead of retried forever.
- Every job has a time limit (`job_time_limit_s`). Past it the worker stops extending
  the lease, cancels the conversion, fails the job with "timeout" and moves on to the
  next job, so one hung document cannot hold the only worker thread for ever. Python
  cannot kill a thread: a conversion stuck inside native code (MuPDF) is abandoned. It
  can no longer spend or store anything, but it keeps its thread until the process
  restarts.
- A conversion is cancelled as soon as this worker no longer owns the job: the user
  deleted it, retention purged it, or another worker requeued it after a lost lease.
  Ownership is checked in the database before every spend reservation (so no further
  crop or text is sent to the model), on every progress callback and on every
  heartbeat. Cancellation raises `JobCancelled` (a `BudgetExceeded`, so the model client
  and the pipeline stop new work the way they do for a spent budget) and the outcome is
  "discarded", not a failure.
- `MAX_USD_PER_JOB` and `MAX_MODEL_CALLS_PER_JOB` cap the job, not one attempt: spend
  and calls so far are written to the job on every heartbeat and progress callback, and
  a retried attempt gets only what is left.
- Results are stored only if this worker still owns the job (same attempt, still
  running), so a job deleted or requeued meanwhile is never resurrected. If storing
  fails (a database error, after one retry of a transient one) the job is failed with
  "store_failed" rather than left running, which would convert (and pay for) it again.

Failures become `Job.error_code` categories: `LayoutError.code`, "budget_<scope>",
"timeout", "store_failed" or "internal_error". Nothing from the document is logged.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.core.budget import BudgetExceeded, JobBudget, Reservation, SpendGuard
from a11y_stem.core.types import ConversionOutput
from a11y_stem.web.database import rowcount
from a11y_stem.web.db import Blob, Job, JobStatus, session_scope, utcnow
from a11y_stem.web.logsafe import log_event, log_exception
from a11y_stem.web.spend import DbSpendGuard

if TYPE_CHECKING:
    from a11y_stem.models.base import VisionClient
    from a11y_stem.pipeline.run import ProgressFn

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
DEFAULT_LEASE_S = 120.0
# Time limit for one conversion: a base (layout, assembly, a slow start) plus a share per
# page for the model calls, with retries and fallback. Generous for real documents; it is
# there so a hung conversion cannot hold the worker for ever. Not a setting yet
# (config.py is not this package's); tune it here.
JOB_BASE_S = 300.0
JOB_PER_PAGE_S = 90.0
# After a timeout, how long to wait for the cancelled conversion to wind down before the
# next job starts (PyMuPDF is not thread-safe, so two conversions should not overlap).
TIMEOUT_GRACE_S = 30.0
# One retry of the result write after a transient database error (connection lost,
# database locked) before the job is failed with "store_failed".
STORE_RETRY_DELAY_S = 1.0
DISCARDED = "discarded"
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class Converter(Protocol):
    """`pipeline.run.convert_pdf`, or a fake with the same keyword arguments."""

    def __call__(
        self,
        pdf_bytes: bytes,
        /,
        *,
        settings: Settings,
        client: VisionClient,
        progress: ProgressFn | None = None,
    ) -> ConversionOutput: ...


ClientFactory = Callable[[Settings, JobBudget], "VisionClient"]


def default_converter() -> Converter:
    from a11y_stem.pipeline.run import convert_pdf

    return convert_pdf


def default_client_factory(settings: Settings, budget: JobBudget) -> VisionClient:
    """The real model client (WP1), imported only when a job actually runs."""
    from a11y_stem import models

    make_vision_client = models.make_vision_client
    return make_vision_client(settings, budget)


def error_code_for(exc: BaseException) -> str:
    """Category code for a failed job. Never derived from the exception message."""
    from a11y_stem.pipeline.layout import LayoutError

    if isinstance(exc, LayoutError):
        code = str(exc.code)
        return code if _CODE.match(code) else "layout_error"
    if isinstance(exc, BudgetExceeded):
        scope = str(exc.scope)
        return f"budget_{scope}" if _CODE.match(scope) else "budget_exceeded"
    return "internal_error"


def job_time_limit_s(page_count: int, settings: Settings) -> float:
    """How long one conversion of `page_count` pages may run before it is stopped."""
    pages = page_count if page_count > 0 else settings.max_pages_per_job
    return JOB_BASE_S + JOB_PER_PAGE_S * min(pages, settings.max_pages_per_job)


def db_safe(value: Any) -> Any:
    """`value` with NUL characters removed from every string (keys included).

    Text extracted from a PDF can contain U+0000, which Postgres refuses in text and
    JSON columns. Left in, the result could never be stored.
    """
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {db_safe(k): db_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [db_safe(v) for v in value]
    return value


class JobCancelled(BudgetExceeded):
    """This worker no longer owns the job (deleted, purged or requeued): stop spending.

    A `BudgetExceeded` with scope "cancelled", so the model client sends nothing and the
    pipeline cancels its queued steps, exactly as when a budget runs out.
    """

    def __init__(self) -> None:
        super().__init__("cancelled", "the job was deleted or taken over by another worker")


@dataclass
class _AttemptBudget(JobBudget):
    """`JobBudget` that asks first whether the job may still spend (`before_reserve`)."""

    before_reserve: Callable[[], None] | None = field(default=None, repr=False)

    def reserve(self, amount_usd: float) -> Reservation:
        if self.before_reserve is not None:
            self.before_reserve()  # raises JobCancelled
        return super().reserve(amount_usd)


@dataclass(frozen=True)
class Claim:
    job_id: str
    attempt: int


@dataclass
class _Attempt:
    """One claim being converted: its budget, the job's spend before it, cancellation."""

    claim: Claim
    prior_usd: float
    prior_calls: int
    budget: JobBudget = field(init=False)
    cancelled: threading.Event = field(default_factory=threading.Event)

    def totals(
        self, cost_usd: float | None = None, model_calls: int | None = None
    ) -> dict[str, Any]:
        """The job's `cost_usd` and `model_calls`: earlier attempts plus this one."""
        cost = self.budget.spent_usd if cost_usd is None else cost_usd
        calls = self.budget.calls if model_calls is None else model_calls
        return {
            "cost_usd": round(self.prior_usd + cost, 8),
            "model_calls": self.prior_calls + calls,
        }


@dataclass
class _Run:
    """What the conversion thread hands back to the worker thread."""

    output: ConversionOutput | None = None
    error: Exception | None = None


class Worker:
    def __init__(
        self,
        settings: Settings,
        factory: sessionmaker[Session],
        *,
        converter: Converter,
        client_factory: ClientFactory,
        guard: SpendGuard | None = None,
        lease_s: float = DEFAULT_LEASE_S,
        clock: Callable[[], datetime] = utcnow,
        max_job_s: float | None = None,
        timeout_grace_s: float = TIMEOUT_GRACE_S,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.converter = converter
        self.client_factory = client_factory
        self.guard: SpendGuard = guard or DbSpendGuard(factory, settings.daily_spend_cap_usd)
        self.lease_s = lease_s
        self.clock = clock
        self.max_job_s = max_job_s  # overrides job_time_limit_s (tests)
        self.timeout_grace_s = timeout_grace_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ queue

    def _lease_until(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.lease_s)

    def requeue_expired(self, now: datetime | None = None) -> tuple[int, int]:
        """Requeue RUNNING jobs whose lease ran out; fail those out of attempts.

        Returns (requeued, failed). Both UPDATEs are conditional, so two workers doing
        this at once cannot double-handle a job.
        """
        now = now or self.clock()
        expired = (Job.status == JobStatus.RUNNING, Job.lease_until < now)
        with session_scope(self.factory) as session:
            failed = rowcount(
                session.execute(
                    update(Job)
                    .where(*expired, Job.attempts >= MAX_ATTEMPTS)
                    .values(
                        status=JobStatus.FAILED,
                        error_code="lease_expired",
                        finished_at=now,
                        lease_until=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            )
            requeued = rowcount(
                session.execute(
                    update(Job)
                    .where(*expired, Job.attempts < MAX_ATTEMPTS)
                    .values(status=JobStatus.QUEUED, lease_until=None, pages_done=0)
                    .execution_options(synchronize_session=False)
                )
            )
        if requeued or failed:
            log_event(logger, "leases_expired", logging.WARNING, requeued=requeued, failed=failed)
        return requeued, failed

    def claim(self, now: datetime | None = None) -> Claim | None:
        """Take the next queued job (fewest attempts, then oldest), or None."""
        now = now or self.clock()
        claimed = {
            "status": JobStatus.RUNNING,
            "started_at": now,
            "lease_until": self._lease_until(now),
            "attempts": Job.attempts + 1,
            "error_code": None,
        }
        oldest = select(Job.id).where(Job.status == JobStatus.QUEUED)
        oldest = oldest.order_by(Job.attempts, Job.created_at, Job.id).limit(1)
        for _ in range(5):  # SQLite: lose a race, try the next job
            with session_scope(self.factory) as session:
                postgres = session.get_bind().dialect.name == "postgresql"
                stmt = oldest.with_for_update(skip_locked=True) if postgres else oldest
                job_id = session.scalar(stmt)
                if job_id is None:
                    return None
                won = rowcount(
                    session.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
                        .values(**claimed)
                        .execution_options(synchronize_session=False)
                    )
                )
                if won == 1:
                    attempt = session.scalar(select(Job.attempts).where(Job.id == job_id))
                    log_event(logger, "job_claimed", job_id=job_id, attempt=attempt)
                    return Claim(job_id=job_id, attempt=int(attempt or 1))
        return None

    def _owned(self, claim: Claim):
        """WHERE clause: the job is still running under this claim."""
        return (
            Job.id == claim.job_id,
            Job.status == JobStatus.RUNNING,
            Job.attempts == claim.attempt,
        )

    def owns(self, claim: Claim) -> bool:
        with session_scope(self.factory) as session:
            return session.scalar(select(Job.id).where(*self._owned(claim))) is not None

    def extend_lease(
        self,
        claim: Claim,
        *,
        pages_done: int | None = None,
        totals: Mapping[str, object] | None = None,
    ) -> bool:
        """Push the lease forward, recording progress and spend so far. False once this
        worker no longer owns the job."""
        values: dict[str, object] = {"lease_until": self._lease_until(self.clock())}
        if pages_done is not None:
            values["pages_done"] = pages_done
        values.update(totals or {})
        with session_scope(self.factory) as session:
            return (
                rowcount(
                    session.execute(
                        update(Job)
                        .where(*self._owned(claim))
                        .values(**values)
                        .execution_options(synchronize_session=False)
                    )
                )
                == 1
            )

    # ------------------------------------------------------------------ one attempt

    def _start_attempt(self, claim: Claim, prior_usd: float, prior_calls: int) -> _Attempt:
        attempt = _Attempt(claim=claim, prior_usd=prior_usd, prior_calls=prior_calls)
        attempt.budget = _AttemptBudget(
            guard=self.guard,
            max_usd=max(0.0, self.settings.max_usd_per_job - prior_usd),
            max_calls=max(0, self.settings.max_model_calls_per_job - prior_calls),
            before_reserve=lambda: self._ensure_owned(attempt),
        )
        return attempt

    def _cancel(self, attempt: _Attempt, reason: str) -> None:
        if not attempt.cancelled.is_set():
            attempt.cancelled.set()
            log_event(logger, "job_cancelled", job_id=attempt.claim.job_id, reason=reason)

    def _ensure_owned(self, attempt: _Attempt) -> None:
        """Before each spend reservation: raise JobCancelled if the job is no longer ours."""
        if attempt.cancelled.is_set():
            raise JobCancelled()
        try:
            owned = self.owns(attempt.claim)
        except Exception as exc:  # cannot tell; the spend guard's own query decides
            log_exception(logger, "ownership_check_failed", exc, job_id=attempt.claim.job_id)
            return
        if not owned:
            self._cancel(attempt, "not_owned")
            raise JobCancelled()

    def _beat(self, attempt: _Attempt, *, pages_done: int | None = None) -> None:
        """Extend the lease and record spend so far; cancel if the job is gone."""
        try:
            owned = self.extend_lease(attempt.claim, pages_done=pages_done, totals=attempt.totals())
        except Exception as exc:  # a missed beat is retried; the lease has slack
            log_exception(logger, "lease_refresh_failed", exc, job_id=attempt.claim.job_id)
            return
        if not owned:
            self._cancel(attempt, "not_owned")

    def _progress(self, attempt: _Attempt) -> ProgressFn:
        """Progress callback. Raising from it aborts `convert_pdf` (queued steps cancelled)."""

        def progress(pages_done: int, pages_total: int) -> None:
            if not attempt.cancelled.is_set():
                self._beat(attempt, pages_done=max(0, min(pages_done, pages_total)))
            if attempt.cancelled.is_set():
                raise JobCancelled()

        return progress

    def _start_conversion(self, attempt: _Attempt, pdf: bytes) -> tuple[threading.Thread, _Run]:
        run = _Run()

        def target() -> None:
            try:
                client = self.client_factory(self.settings, attempt.budget)
                run.output = self.converter(
                    pdf, settings=self.settings, client=client, progress=self._progress(attempt)
                )
            except Exception as exc:  # handed to the worker thread
                run.error = exc

        thread = threading.Thread(target=target, name="a11y-stem-convert", daemon=True)
        thread.start()
        return thread, run

    def _wait(self, attempt: _Attempt, thread: threading.Thread, limit_s: float) -> bool:
        """Heartbeat until the conversion ends (True) or its time limit passes (False).

        Once the attempt is cancelled the conversion should stop at its next reservation
        or progress call, so it gets only `timeout_grace_s` more."""
        deadline = time.monotonic() + limit_s
        while (remaining := deadline - time.monotonic()) > 0:
            thread.join(timeout=min(self.lease_s / 3, remaining))
            if not thread.is_alive():
                return True
            if attempt.cancelled.is_set():
                deadline = min(deadline, time.monotonic() + self.timeout_grace_s)
            else:
                self._beat(attempt)
        return not thread.is_alive()

    def _time_out(self, attempt: _Attempt, thread: threading.Thread, limit_s: float) -> str:
        job_id = attempt.claim.job_id
        attempt.cancelled.set()  # no more spend; the next progress callback aborts the run
        status = self._finish_failed(attempt, "timeout")
        log_event(logger, "job_timeout", logging.WARNING, job_id=job_id, limit_s=round(limit_s))
        thread.join(timeout=self.timeout_grace_s)
        if thread.is_alive():
            log_event(logger, "conversion_abandoned", logging.ERROR, job_id=job_id)
        return status

    # ------------------------------------------------------------------ processing

    def time_limit_s(self, page_count: int) -> float:
        if self.max_job_s is not None:
            return self.max_job_s
        return job_time_limit_s(page_count, self.settings)

    def process(self, claim: Claim) -> str:
        """Convert one claimed job and store the outcome. Returns the final status
        ("done", "failed") or "discarded" when the job stopped being ours meanwhile."""
        started = time.monotonic()
        with session_scope(self.factory) as session:
            job = session.execute(
                select(Job.page_count, Job.cost_usd, Job.model_calls).where(Job.id == claim.job_id)
            ).one_or_none()
            pdf = session.scalar(
                select(Blob.content).where(Blob.job_id == claim.job_id, Blob.kind == "upload")
            )
        if job is None:  # deleted between claim and start
            log_event(logger, "job_result_discarded", job_id=claim.job_id)
            return DISCARDED
        attempt = self._start_attempt(claim, float(job.cost_usd or 0.0), int(job.model_calls or 0))
        if pdf is None:
            return self._finish_failed(attempt, "upload_missing")
        limit_s = self.time_limit_s(int(job.page_count or 0))
        thread, run = self._start_conversion(attempt, pdf)
        finished = self._wait(attempt, thread, limit_s)
        if not finished and not attempt.cancelled.is_set():
            return self._time_out(attempt, thread, limit_s)
        if attempt.cancelled.is_set():  # deleted, purged or requeued: nothing to record
            if not finished:
                log_event(logger, "conversion_abandoned", logging.ERROR, job_id=claim.job_id)
            log_event(logger, "job_result_discarded", job_id=claim.job_id)
            return DISCARDED
        if run.output is None:
            exc = run.error or RuntimeError("conversion ended without a result")
            code = error_code_for(exc)
            log_exception(logger, "job_failed", exc, job_id=claim.job_id, error_code=code)
            return self._finish_failed(attempt, code)
        return self._store(attempt, run.output, time.monotonic() - started)

    def _finish_failed(self, attempt: _Attempt, code: str) -> str:
        """Mark the job FAILED if this worker still owns it; "discarded" if not."""
        values: dict[str, object] = {
            "status": JobStatus.FAILED,
            "error_code": code,
            "finished_at": self.clock(),
            "lease_until": None,
            **attempt.totals(),
        }
        with session_scope(self.factory) as session:
            updated = rowcount(
                session.execute(
                    update(Job)
                    .where(*self._owned(attempt.claim))
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
            )
        return JobStatus.FAILED if updated == 1 else DISCARDED

    def _store(self, attempt: _Attempt, output: ConversionOutput, seconds: float) -> str:
        job_id = attempt.claim.job_id
        try:
            stored = self._write_result_retrying(attempt, output)
        except Exception as exc:
            # Conditional on ownership: a job deleted meanwhile (the usual cause of an
            # IntegrityError here) stays deleted and the result is discarded.
            status = self._finish_failed(attempt, "store_failed")
            if status == JobStatus.FAILED:
                log_exception(logger, "job_store_failed", exc, job_id=job_id)
            else:
                log_event(logger, "job_result_discarded", job_id=job_id)
            return status
        if not stored:
            log_event(logger, "job_result_discarded", job_id=job_id)
            return DISCARDED
        result = output.result
        log_event(
            logger,
            "job_done",
            job_id=job_id,
            pages=result.stats.pages,
            fixes=len(result.fixes),
            model_calls=result.stats.model_calls,
            cost_usd=round(result.stats.cost_usd, 6),
            seconds=round(seconds, 2),
        )
        return JobStatus.DONE

    def _write_result_retrying(self, attempt: _Attempt, output: ConversionOutput) -> bool:
        try:
            return self._write_result(attempt, output)
        except OperationalError as exc:  # transient (connection lost, database locked)
            log_exception(logger, "job_store_retry", exc, job_id=attempt.claim.job_id)
            time.sleep(STORE_RETRY_DELAY_S)
            return self._write_result(attempt, output)

    def _write_result(self, attempt: _Attempt, output: ConversionOutput) -> bool:
        """One transaction: crops, result, html, totals, DONE. False if not ours."""
        result = output.result
        with session_scope(self.factory) as session:
            job = session.get(Job, attempt.claim.job_id, with_for_update=True)
            if (
                job is None
                or job.status != JobStatus.RUNNING
                or job.attempts != attempt.claim.attempt
            ):
                return False
            session.execute(delete(Blob).where(Blob.job_id == job.id, Blob.kind == "crop"))
            for element_id, png in output.crops.items():
                session.add(
                    Blob(
                        job_id=job.id,
                        kind="crop",
                        element_id=element_id,
                        media_type="image/png",
                        content=png,
                    )
                )
            job.result_json = db_safe(result.model_dump(mode="json", exclude={"html"}))
            job.html = db_safe(result.html)
            totals = attempt.totals(
                result.stats.cost_usd or attempt.budget.spent_usd,
                result.stats.model_calls or attempt.budget.calls,
            )
            job.cost_usd = totals["cost_usd"]
            job.model_calls = totals["model_calls"]
            job.pages_done = job.page_count
            job.status = JobStatus.DONE
            job.finished_at = self.clock()
            job.lease_until = None
        return True

    # ------------------------------------------------------------------ loop

    def run_once(self) -> str | None:
        """Requeue expired leases, then claim and process one job. Returns its id."""
        self.requeue_expired()
        claim = self.claim()
        if claim is None:
            return None
        self.process(claim)
        return claim.job_id

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception as exc:  # e.g. database briefly unreachable
                log_exception(logger, "worker_loop_error", exc)
                ran = None
            if ran is None:
                self._stop.wait(self.settings.worker_poll_s)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="a11y-stem-worker", daemon=True)
        self._thread.start()
        log_event(logger, "worker_started")

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the loop to stop after the current job. A job still running when the
        process exits is picked up again once its lease expires."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        log_event(logger, "worker_stopped")


def process_next_job(
    settings: Settings,
    factory: sessionmaker[Session],
    *,
    converter: Converter,
    client_factory: ClientFactory,
    guard: SpendGuard | None = None,
) -> str | None:
    """Process one queued job synchronously (tests, scripts). Returns its id or None."""
    worker = Worker(
        settings, factory, converter=converter, client_factory=client_factory, guard=guard
    )
    return worker.run_once()
