"""Background worker: claims queued jobs from the database and converts them.

One worker thread runs inside the web process (Cloud Run, min-instances=1), but the
queue is safe with several workers or instances:

- Claiming is atomic. Postgres: `SELECT ... FOR UPDATE SKIP LOCKED` (two workers never
  wait on, or take, the same row). SQLite: a conditional UPDATE (`... WHERE status =
  'queued'`) whose rowcount says who won.
- A claimed job holds a lease (`lease_until`) that a heartbeat thread keeps extending.
  If the process dies, the lease runs out and the next worker requeues the job; after
  `MAX_ATTEMPTS` claims it is failed with "lease_expired" instead of retried forever.
- Results are stored only if this worker still owns the job (same attempt, still
  running), so a job deleted or requeued meanwhile is never resurrected.

Failures become `Job.error_code` categories: `LayoutError.code`, "budget_<scope>" or
"internal_error". Nothing from the document is logged.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.core.budget import BudgetExceeded, JobBudget, SpendGuard
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

    make_vision_client = models.make_vision_client  # ty: ignore[unresolved-attribute]
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


@dataclass(frozen=True)
class Claim:
    job_id: str
    attempt: int


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
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.converter = converter
        self.client_factory = client_factory
        self.guard: SpendGuard = guard or DbSpendGuard(factory, settings.daily_spend_cap_usd)
        self.lease_s = lease_s
        self.clock = clock
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
        """Take the oldest queued job, or None if the queue is empty."""
        now = now or self.clock()
        claimed = {
            "status": JobStatus.RUNNING,
            "started_at": now,
            "lease_until": self._lease_until(now),
            "attempts": Job.attempts + 1,
            "error_code": None,
        }
        oldest = select(Job.id).where(Job.status == JobStatus.QUEUED)
        oldest = oldest.order_by(Job.created_at, Job.id).limit(1)
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

    def extend_lease(self, claim: Claim, *, pages_done: int | None = None) -> bool:
        values: dict[str, object] = {"lease_until": self._lease_until(self.clock())}
        if pages_done is not None:
            values["pages_done"] = pages_done
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

    @contextmanager
    def _heartbeat(self, claim: Claim) -> Generator[None]:
        """Keep the lease alive while a conversion runs (pages can take minutes)."""
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(self.lease_s / 3):
                try:
                    self.extend_lease(claim)
                except Exception as exc:  # a missed beat is retried; the lease has slack
                    log_exception(logger, "lease_refresh_failed", exc, job_id=claim.job_id)

        thread = threading.Thread(target=beat, name="a11y-stem-lease", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)

    def _progress(self, claim: Claim) -> ProgressFn:
        def progress(pages_done: int, pages_total: int) -> None:
            try:
                self.extend_lease(claim, pages_done=max(0, min(pages_done, pages_total)))
            except Exception as exc:  # progress is cosmetic; never fail the job over it
                log_exception(logger, "progress_update_failed", exc, job_id=claim.job_id)

        return progress

    # ------------------------------------------------------------------ processing

    def process(self, claim: Claim) -> str:
        """Convert one claimed job and store the outcome. Returns the final status."""
        started = time.monotonic()
        with session_scope(self.factory) as session:
            pdf = session.scalar(
                select(Blob.content).where(Blob.job_id == claim.job_id, Blob.kind == "upload")
            )
        if pdf is None:
            return self._finish_failed(claim, "upload_missing", None)
        budget = JobBudget(
            guard=self.guard,
            max_usd=self.settings.max_usd_per_job,
            max_calls=self.settings.max_model_calls_per_job,
        )
        try:
            with self._heartbeat(claim):
                client = self.client_factory(self.settings, budget)
                output = self.converter(
                    pdf, settings=self.settings, client=client, progress=self._progress(claim)
                )
        except Exception as exc:
            code = error_code_for(exc)
            log_exception(logger, "job_failed", exc, job_id=claim.job_id, error_code=code)
            return self._finish_failed(claim, code, budget)
        return self._store(claim, output, budget, time.monotonic() - started)

    def _finish_failed(self, claim: Claim, code: str, budget: JobBudget | None) -> str:
        values: dict[str, object] = {
            "status": JobStatus.FAILED,
            "error_code": code,
            "finished_at": self.clock(),
            "lease_until": None,
        }
        if budget is not None:
            values.update(cost_usd=budget.spent_usd, model_calls=budget.calls)
        with session_scope(self.factory) as session:
            session.execute(
                update(Job)
                .where(*self._owned(claim))
                .values(**values)
                .execution_options(synchronize_session=False)
            )
        return JobStatus.FAILED

    def _store(
        self, claim: Claim, output: ConversionOutput, budget: JobBudget, seconds: float
    ) -> str:
        result = output.result
        try:
            with session_scope(self.factory) as session:
                job = session.get(Job, claim.job_id)
                if job is None or job.status != JobStatus.RUNNING or job.attempts != claim.attempt:
                    log_event(logger, "job_result_discarded", job_id=claim.job_id)
                    return "discarded"
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
                job.result_json = result.model_dump(mode="json", exclude={"html"})
                job.html = result.html
                job.cost_usd = result.stats.cost_usd or budget.spent_usd
                job.model_calls = result.stats.model_calls or budget.calls
                job.pages_done = job.page_count
                job.status = JobStatus.DONE
                job.finished_at = self.clock()
                job.lease_until = None
        except IntegrityError:  # the job was deleted while its result was being written
            log_event(logger, "job_result_discarded", job_id=claim.job_id)
            return "discarded"
        log_event(
            logger,
            "job_done",
            job_id=claim.job_id,
            pages=result.stats.pages,
            fixes=len(result.fixes),
            model_calls=result.stats.model_calls,
            cost_usd=round(result.stats.cost_usd, 6),
            seconds=round(seconds, 2),
        )
        return JobStatus.DONE

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
