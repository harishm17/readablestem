"""Background worker: claims queued jobs from the database and converts them.

One worker thread runs inside the web process (Cloud Run, min-instances=1), but the
queue is safe with several workers or instances:

- Claiming is atomic. Postgres: `SELECT ... FOR UPDATE SKIP LOCKED` (two workers never
  wait on, or take, the same row). SQLite: a conditional UPDATE (`... WHERE status =
  'queued'`) whose rowcount says who won. Fresh jobs go before retried ones (fewest
  attempts first, then oldest), so a PDF that keeps killing the process cannot jump
  ahead of everyone else's work after each restart. Jobs of someone removed from the
  allowlist are never claimed (removal also stops the ones already queued or running;
  see `web.auth.set_allowed`).
- While less than one model call's worth (`spend.largest_reservation_usd`) is left under
  the daily cap, nothing is claimed: queued jobs wait for the next UTC day instead of
  running into a document with every element "not converted".
- The conversion runs on its own thread. The worker thread waits for it and extends the
  job's lease (`lease_until`) every `lease_s / 3`. If the process dies, the lease runs
  out and the next worker requeues the job; after `MAX_ATTEMPTS` claims it is failed
  with "lease_expired" instead of retried forever. `Worker.stop` (process shutdown)
  cancels the running attempt and ends its lease at once, so another instance can
  requeue the job without waiting for the lease to run out.
- Every job has a time limit (`job_time_limit_s`). Past it the worker stops extending
  the lease, cancels the conversion, fails the job with "timeout" and moves on to the
  next job, so one hung document cannot hold the only worker thread for ever. Python
  cannot kill a thread: a conversion stuck inside native code (MuPDF) is abandoned. It
  can no longer spend or store anything, but it keeps its thread until the process
  restarts.
- A conversion is cancelled as soon as this worker no longer owns the job: the user
  deleted it, retention purged it, or another worker requeued it after a lost lease.
  Ownership is checked in the database on every spend reservation (so no further crop
  or text is sent to the model), on every progress callback and on every heartbeat.
  Cancellation raises `JobCancelled` (a `BudgetExceeded`, so the model client and the
  pipeline stop new work the way they do for a spent budget) and the outcome is
  "discarded", not a failure.
- The job caps (`job_caps`: `MAX_USD_PER_JOB` and `MAX_MODEL_CALLS_PER_JOB`, lowered for
  short documents by the per-page allowances) cover the job, not one attempt, and are
  enforced on the job's row: every spend reservation is one conditional UPDATE that adds
  the hold to `jobs.cost_usd` and `jobs.model_calls` only if this attempt still owns the
  job and the totals stay under the caps; settling replaces the hold with the actual
  cost. An attempt that dies mid-call leaves its holds counted (erring on the side of
  spending less, like the daily cap), so a retry never gets more than what is left and
  the job's totals are always current. A second-check request (ADR 0005) reserves with
  `count_call=False`: it is held against the dollar caps but adds nothing to
  `jobs.model_calls` and is never refused by the call cap (`core.budget`).
- Retention backstop: once an hour the worker loop runs the purge itself
  (`ops.retention.purge_expired`, logged as `purge_done source=worker`). The hourly
  Cloud Scheduler call stays the primary path; this keeps the 24-hour deletion promise
  if the scheduler stops (a rotated token, a paused job). A backstop run that deletes
  anything logs `purge_backstop_deleted`, which means the scheduler is late.
- Results are stored only if this worker still owns the job (same attempt, still
  running), so a job deleted or requeued meanwhile is never resurrected. If storing
  fails (a database error, after one retry of a transient one) the job is failed with
  "store_failed" rather than left running, which would convert (and pay for) it again.
- Live progress for the job page: the converter's `ProgressEvent`s (stage, done, total,
  counts per kind) are kept per stage and stored on `jobs.progress_json`, throttled
  (`PROGRESS_WRITE_S`), only while this attempt owns the job; a requeued job starts
  again from none.

Failures become `Job.error_code` categories: `LayoutError.code`, "budget_<scope>",
"timeout", "store_failed", "model_not_configured" or "internal_error". A finished job
can carry a code too (`result_outcome`): "budget_<scope>" when a spend cap stopped model
work part way (the result is partial), "pages_not_converted" when pages (or handwriting
on typed pages) were kept as images. A result with nothing in it fails as "no_content",
and one the upload probe should have refused (every page handwritten or scanned) with
the probe's code ("handwritten", "scanned", ...). Nothing from the document is logged.
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import ConfigError, Settings
from a11y_stem.core.budget import BudgetExceeded, JobBudget, Reservation, SpendGuard
from a11y_stem.core.types import ConversionOutput, DocumentResult, ProgressEvent
from a11y_stem.web import events
from a11y_stem.web.database import rowcount
from a11y_stem.web.db import Blob, Job, JobStatus, User, session_scope, utcnow
from a11y_stem.web.logsafe import log_event, log_exception
from a11y_stem.web.spend import DbSpendGuard, largest_reservation_usd

if TYPE_CHECKING:
    from a11y_stem.core.types import ConversionOptions
    from a11y_stem.models.base import VisionClient
    from a11y_stem.observability.tracing import Tracer
    from a11y_stem.pipeline.run import ProgressFn, StageProgressFn

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
DEFAULT_LEASE_S = 120.0
# Time limit for one conversion: a base (layout, assembly, a slow start) plus a share per
# page for the model calls, with retries and fallback. Generous for real documents; it is
# there so a hung conversion cannot hold the worker for ever. Not a setting yet; tune it
# here. Measured on 1 vCPU (docs/deploy.md, "Sizing"): 30 dense pages on Claude took 42-51 s
# (1.4-1.7 s a page; 3.6 s a page median and 5.7 at most on OpenAI), so 30 pages get 18
# minutes, 20 times the measurement and 6 times the slowest page rate seen. It was 300 s
# plus 90 s a page (50 minutes for 30 pages), which would hold the only worker that long.
JOB_BASE_S = 180.0
JOB_PER_PAGE_S = 30.0
# After a timeout, how long to wait for the cancelled conversion to wind down before the
# next job starts (PyMuPDF is not thread-safe, so two conversions should not overlap).
TIMEOUT_GRACE_S = 30.0
# One retry of the result write after a transient database error (connection lost,
# database locked) before the job is failed with "store_failed".
STORE_RETRY_DELAY_S = 1.0
# The retention backstop: first run this long after the loop starts, then this often.
PURGE_FIRST_S = 600.0
PURGE_EVERY_S = 3600.0
DISCARDED = "discarded"
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# Float sums of many small costs drift by ~1e-16; this keeps an exact fit under a cap.
_EPSILON = 1e-9
# Codes a finished (DONE) job can carry; see `result_outcome`.
BUDGET_STOPS = frozenset({"budget_daily", "budget_job_usd", "budget_job_calls"})
SCANNED_PAGES = "scanned_pages"  # before page types: some pages were only scanned images
PAGES_NOT_CONVERTED = "pages_not_converted"
# Fix reasons of what the pipeline kept as an image before any model work.
KEPT_IMAGE_REASONS = frozenset(
    {"scanned_page", "handwritten_page", "unread_page", "handwriting_on_page"}
)
# Live progress is written to the job row at most this often (seconds), apart from a
# stage seen for the first time or just finished (`Worker._stage_progress`). The job page
# polls every 3 s.
PROGRESS_WRITE_S = 2.0


class Converter(Protocol):
    """`pipeline.run.convert_pdf`, or a fake with the same keyword arguments.

    The optional arguments (`tracer`, `options`, `stage_progress`) are passed only when
    set (`Worker._converter_kwargs`), so a fake without them works in every other case.
    """

    def __call__(
        self,
        pdf_bytes: bytes,
        /,
        *,
        settings: Settings,
        client: VisionClient,
        progress: ProgressFn | None = None,
        tracer: Tracer | None = None,
        options: ConversionOptions | None = None,
        stage_progress: StageProgressFn | None = None,
    ) -> ConversionOutput: ...


ClientFactory = Callable[[Settings, JobBudget], "VisionClient"]


def default_converter() -> Converter:
    from a11y_stem.pipeline.run import convert_pdf

    return convert_pdf


def default_client_factory(settings: Settings, budget: JobBudget) -> VisionClient:
    """The real model client (WP1), imported only when a job actually runs.

    With FAKE_MODELS=true (dev and test only; `Settings.check_production` refuses it in
    production) the offline client answers instead, still charging `budget`, so the app
    can be tried end to end without an OpenAI key.
    """
    from a11y_stem import models

    make_vision_client = models.make_vision_client
    return make_vision_client(settings, budget, fake=settings.fake_models)


def error_code_for(exc: BaseException) -> str:
    """Category code for a failed job. Never derived from the exception message."""
    from a11y_stem.pipeline.layout import LayoutError

    if isinstance(exc, LayoutError):
        code = str(exc.code)
        return code if _CODE.match(code) else "layout_error"
    if isinstance(exc, BudgetExceeded):
        scope = str(exc.scope)
        return f"budget_{scope}" if _CODE.match(scope) else "budget_exceeded"
    if isinstance(exc, ConfigError):  # e.g. no OpenAI key: every job would fail the same way
        return "model_not_configured"
    return "internal_error"


def result_outcome(result: DocumentResult) -> tuple[JobStatus, str | None]:
    """Status and code for a conversion that returned a result.

    Nothing to convert is a failure: the upload probe's own code when every analysed page
    is certainly handwritten or scanned (`page_type.refusal_code` on the stored page
    types; for a result without them, "scanned" when every page was only a picture of a
    page), "no_content" when the result has no elements at all. Otherwise the job is
    done, with a code when it is incomplete: the spend cap that stopped model work
    ("budget_<scope>"), else "pages_not_converted" when pages or handwriting were kept as
    images. A page the probe could not be sure of (a full-page picture with a short
    title, ink not analysed in time) is kept as an image with a decision, never failed.
    """
    from a11y_stem.pipeline.page_type import refusal_code

    analysed = {page.page for page in result.stats.per_page}
    image_only = set(result.image_only_pages)
    typed = {t.page for t in result.page_types}
    if result.page_types and analysed <= typed:
        refused = refusal_code([t for t in result.page_types if t.page in analysed])
        if refused is not None and refused != "no_content":
            return JobStatus.FAILED, refused
    elif image_only and analysed <= image_only:
        return JobStatus.FAILED, "scanned"
    if not result.elements:
        return JobStatus.FAILED, "no_content"
    if result.stopped_by in BUDGET_STOPS:
        return JobStatus.DONE, result.stopped_by
    if any(f.reason in KEPT_IMAGE_REASONS for f in result.fixes):
        return JobStatus.DONE, PAGES_NOT_CONVERTED
    if image_only:  # a result from before page types, without its fixes' reasons
        return JobStatus.DONE, SCANNED_PAGES
    return JobStatus.DONE, None


def job_time_limit_s(page_count: int, settings: Settings) -> float:
    """How long one conversion of `page_count` pages may run before it is stopped."""
    pages = page_count if page_count > 0 else settings.max_pages_per_job
    return JOB_BASE_S + JOB_PER_PAGE_S * min(pages, settings.max_pages_per_job)


def job_caps(settings: Settings, page_count: int) -> tuple[float, int]:
    """(max USD, max model calls) for a whole job of `page_count` pages, all attempts.

    `MAX_USD_PER_JOB` and `MAX_MODEL_CALLS_PER_JOB`, lowered for short documents to the
    per-page allowances times the pages. Without that, one dense page (layout puts no
    limit on figures per page) could use a long document's whole allowance, and a user's
    daily page quota would not bound what they can spend in a day.
    """
    pages = page_count if page_count > 0 else settings.max_pages_per_job
    return (
        min(settings.max_usd_per_job, pages * settings.max_usd_per_page),
        min(settings.max_model_calls_per_job, pages * settings.max_model_calls_per_page),
    )


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


def _accepts(function: Callable[..., Any], name: str) -> bool:
    """Whether `function` takes the keyword argument `name` (or any keyword). A callable
    whose signature cannot be read is assumed to take it."""
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters)


class JobCancelled(BudgetExceeded):
    """This worker no longer owns the job (deleted, purged or requeued): stop spending.

    A `BudgetExceeded` with scope "cancelled", so the model client sends nothing and the
    pipeline cancels its queued steps, exactly as when a budget runs out.
    """

    def __init__(self) -> None:
        super().__init__("cancelled", "the job was deleted or taken over by another worker")


@dataclass
class _AttemptBudget(JobBudget):
    """`JobBudget` that also holds every reservation on the job's database row.

    `hold_on_job(amount, calls)` runs first: it adds the hold (and `calls`, 1 for a model
    call, 0 for a second-check request) to the job's totals if the job is still this
    attempt's and stays under its caps, else raises `JobCancelled` or `BudgetExceeded`.
    If the in-memory or the daily cap then refuses, `release_on_job(amount, calls)`
    takes the hold off again. `settle_on_job(delta)` replaces the hold with the actual
    cost.
    """

    hold_on_job: Callable[[float, int], None] | None = field(default=None, repr=False)
    release_on_job: Callable[[float, int], None] | None = field(default=None, repr=False)
    settle_on_job: Callable[[float], None] | None = field(default=None, repr=False)

    def reserve(self, amount_usd: float, *, count_call: bool = True) -> Reservation:
        calls = 1 if count_call else 0
        if self.hold_on_job is not None:
            self.hold_on_job(amount_usd, calls)
        try:
            return super().reserve(amount_usd, count_call=count_call)
        except BaseException:
            if self.release_on_job is not None:
                self.release_on_job(amount_usd, calls)
            raise

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        super().settle(reservation, actual_usd)
        if self.settle_on_job is not None:
            self.settle_on_job(max(0.0, actual_usd) - reservation.amount_usd)


@dataclass(frozen=True)
class Claim:
    job_id: str
    attempt: int


@dataclass
class _Attempt:
    """One claim being converted: its budget, the job's caps and spend before it,
    cancellation."""

    claim: Claim
    prior_usd: float
    prior_calls: int
    max_usd: float  # the whole job's caps (every attempt), see `job_caps`
    max_calls: int
    budget: JobBudget = field(init=False)
    cancelled: threading.Event = field(default_factory=threading.Event)


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
        headroom_usd: float | None = None,
        tracer: Tracer | None = None,
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
        # Less than this left under the daily cap: claim nothing (see the module docstring).
        self.headroom_usd = (
            largest_reservation_usd(settings) if headroom_usd is None else headroom_usd
        )
        self.tracer = tracer  # None: the converter's own default (no tracing)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current: _Attempt | None = None
        self._paused = False
        self._next_purge = time.monotonic() + PURGE_FIRST_S
        # When the loop (or a running conversion's heartbeat) last showed life; /readyz.
        self._seen_at = time.monotonic()
        self._shutdown_thread: threading.Thread | None = None

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
                    .values(
                        status=JobStatus.QUEUED, lease_until=None, pages_done=0, progress_json=None
                    )
                    .execution_options(synchronize_session=False)
                )
            )
        if requeued or failed:
            log_event(logger, "leases_expired", logging.WARNING, requeued=requeued, failed=failed)
        return requeued, failed

    def claim(self, now: datetime | None = None) -> Claim | None:
        """Take the next queued job (fewest attempts, then oldest), or None.

        Only jobs whose owner is still on the allowlist are taken."""
        now = now or self.clock()
        claimed = {
            "status": JobStatus.RUNNING,
            "started_at": now,
            "lease_until": self._lease_until(now),
            "attempts": Job.attempts + 1,
            "error_code": None,
        }
        oldest = (
            select(Job.id)
            .join(User, User.id == Job.user_id)
            .where(Job.status == JobStatus.QUEUED, User.is_allowed.is_(True))
            .order_by(Job.attempts, Job.created_at, Job.id)
            .limit(1)
        )
        for _ in range(5):  # SQLite: lose a race, try the next job
            with session_scope(self.factory) as session:
                postgres = session.get_bind().dialect.name == "postgresql"
                stmt = oldest.with_for_update(skip_locked=True, of=Job) if postgres else oldest
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

    def budget_closed(self) -> bool:
        """True while not even one model call fits under today's cap."""
        cap_reached = getattr(self.guard, "cap_reached", None)
        if not callable(cap_reached):  # a guard without a snapshot (tests, CLI)
            return False
        return bool(cap_reached(self.headroom_usd))

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

    def extend_lease(self, claim: Claim, *, pages_done: int | None = None) -> bool:
        """Push the lease forward, recording progress. False once this worker no longer
        owns the job."""
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

    # ------------------------------------------------------------------ spend on the job row

    def _hold_on_job(self, attempt: _Attempt, amount_usd: float, calls: int = 1) -> None:
        """Add one reservation (`amount_usd`, and `calls` model calls: 1, or 0 for a
        second-check request) to the job's totals, or raise.

        One conditional UPDATE: the job must still be running under this attempt, and its
        totals (every attempt's settled spend plus open holds) must stay under the job's
        caps. JobCancelled if the job is no longer ours, BudgetExceeded("job_usd" or
        "job_calls") if a cap would be passed. With `calls=0` the call cap is not read.
        """
        if attempt.cancelled.is_set():
            raise JobCancelled()
        under_caps = [Job.cost_usd + amount_usd <= attempt.max_usd + _EPSILON]
        if calls:
            under_caps.append(Job.model_calls + calls <= attempt.max_calls)
        with session_scope(self.factory) as session:
            won = rowcount(
                session.execute(
                    update(Job)
                    .where(*self._owned(attempt.claim), *under_caps)
                    .values(cost_usd=Job.cost_usd + amount_usd, model_calls=Job.model_calls + calls)
                    .execution_options(synchronize_session=False)
                )
            )
            if won == 1:
                return
            row = session.execute(
                select(Job.cost_usd, Job.model_calls).where(*self._owned(attempt.claim))
            ).one_or_none()
        if row is None:
            self._cancel(attempt, "not_owned")
            raise JobCancelled()
        if calls and int(row.model_calls or 0) + calls > attempt.max_calls:
            raise BudgetExceeded("job_calls", f"job call cap {attempt.max_calls} reached")
        raise BudgetExceeded("job_usd", f"job spend cap ${attempt.max_usd:.2f} reached")

    def _adjust_job(self, attempt: _Attempt, cost_delta: float, calls_delta: int) -> None:
        """Change the job's totals by a delta (a released hold or a settled call), whoever
        owns the job now: the hold was added to this row. Never raises."""
        if not cost_delta and not calls_delta:
            return
        try:
            with session_scope(self.factory) as session:
                session.execute(
                    update(Job)
                    .where(Job.id == attempt.claim.job_id)
                    .values(
                        cost_usd=Job.cost_usd + cost_delta,
                        model_calls=Job.model_calls + calls_delta,
                    )
                    .execution_options(synchronize_session=False)
                )
        except Exception as exc:  # the hold stays counted: errs on the side of less spend
            log_exception(logger, "job_spend_update_failed", exc, job_id=attempt.claim.job_id)

    # ------------------------------------------------------------------ one attempt

    def _start_attempt(
        self, claim: Claim, prior_usd: float, prior_calls: int, page_count: int
    ) -> _Attempt:
        max_usd, max_calls = job_caps(self.settings, page_count)
        attempt = _Attempt(
            claim=claim,
            prior_usd=prior_usd,
            prior_calls=prior_calls,
            max_usd=max_usd,
            max_calls=max_calls,
        )
        attempt.budget = _AttemptBudget(
            guard=self.guard,
            max_usd=max(0.0, max_usd - prior_usd),
            max_calls=max(0, max_calls - prior_calls),
            hold_on_job=lambda amount, calls: self._hold_on_job(attempt, amount, calls),
            release_on_job=lambda amount, calls: self._adjust_job(attempt, -amount, -calls),
            settle_on_job=lambda delta: self._adjust_job(attempt, delta, 0),
        )
        return attempt

    def _cancel(self, attempt: _Attempt, reason: str) -> None:
        if not attempt.cancelled.is_set():
            attempt.cancelled.set()
            log_event(logger, "job_cancelled", job_id=attempt.claim.job_id, reason=reason)

    def _beat(self, attempt: _Attempt, *, pages_done: int | None = None) -> None:
        """Extend the lease and record progress; cancel if the job is gone."""
        self._seen_at = time.monotonic()
        try:
            owned = self.extend_lease(attempt.claim, pages_done=pages_done)
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

    def _converter_kwargs(self, attempt: _Attempt) -> dict[str, Any]:
        """The converter's optional arguments, each passed only when set (`Converter`).

        Content features add theirs here (docs/design/content-features.md), each in its
        own block: `options` (the job's course notation; with none, `convert_pdf` uses
        the settings' feature flags) and `stage_progress` (live progress by stage).
        """
        kwargs: dict[str, Any] = {}
        if self.tracer is not None:
            kwargs["tracer"] = self.tracer

        # options: the job's course notation (FEATURE_NOTATION_MEMORY); without a course
        # with entries, convert_pdf uses the settings' flags as before.
        from a11y_stem.web import notation

        options = notation.conversion_options(self.factory, self.settings, attempt.claim.job_id)
        if options is not None:
            kwargs["options"] = options

        # stage_progress: live progress by stage, stored on the job row for its page
        # (only to a converter that takes it: older fakes and scripts do not).
        if _accepts(self.converter, "stage_progress"):
            kwargs["stage_progress"] = self._stage_progress(attempt)

        return kwargs

    # ------------------------------------------------------------------ live progress

    def _stage_progress(self, attempt: _Attempt) -> StageProgressFn:
        """Receiver for the converter's `ProgressEvent`s (on the conversion thread).

        Keeps the latest event of every stage and stores them on the job as
        `{"stages": {stage: event}}` (`jobs.progress_json`; content-free): a stage seen
        for the first time or just finished at once, anything else at most every
        `PROGRESS_WRITE_S`, so a document with hundreds of equations does not write the
        row hundreds of times. Raises `JobCancelled` once the job is no longer this
        attempt's, which stops the run like the page progress callback does. A failed
        write is logged and skipped: progress is never worth a failed conversion.
        """
        stages: dict[str, dict[str, Any]] = {}
        written = [float("-inf")]

        def stage_progress(event: ProgressEvent) -> None:
            if attempt.cancelled.is_set():
                raise JobCancelled()
            new = event.stage not in stages
            stages[event.stage] = event.model_dump(mode="json")
            finished = event.total > 0 and event.done >= event.total
            now = time.monotonic()
            if not (new or finished) and now - written[0] < PROGRESS_WRITE_S:
                return
            written[0] = now
            try:
                owned = self._write_progress(attempt, {"stages": dict(stages)})
            except Exception as exc:
                log_exception(logger, "progress_write_failed", exc, job_id=attempt.claim.job_id)
                return
            if not owned:
                self._cancel(attempt, "not_owned")
                raise JobCancelled()

        return stage_progress

    def _write_progress(self, attempt: _Attempt, value: dict[str, Any]) -> bool:
        """Store live progress if this attempt still owns the job (False if not)."""
        with session_scope(self.factory) as session:
            return (
                rowcount(
                    session.execute(
                        update(Job)
                        .where(*self._owned(attempt.claim))
                        .values(progress_json=value)
                        .execution_options(synchronize_session=False)
                    )
                )
                == 1
            )

    def _start_conversion(self, attempt: _Attempt, pdf: bytes) -> tuple[threading.Thread, _Run]:
        run = _Run()

        def target() -> None:
            try:
                client = self.client_factory(self.settings, attempt.budget)
                progress = self._progress(attempt)
                run.output = self.converter(
                    pdf,
                    settings=self.settings,
                    client=client,
                    progress=progress,
                    **self._converter_kwargs(attempt),
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
        page_count = int(job.page_count or 0)
        attempt = self._start_attempt(
            claim, float(job.cost_usd or 0.0), int(job.model_calls or 0), page_count
        )
        if pdf is None:
            return self._finish_failed(attempt, "upload_missing")
        self._current = attempt
        try:
            return self._convert_and_store(attempt, pdf, page_count, started)
        finally:
            self._current = None

    def _convert_and_store(
        self, attempt: _Attempt, pdf: bytes, page_count: int, started: float
    ) -> str:
        claim = attempt.claim
        limit_s = self.time_limit_s(page_count)
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
        status, code = result_outcome(run.output.result)
        if status == JobStatus.FAILED:
            log_event(logger, "job_failed", job_id=claim.job_id, error_code=code)
            return self._finish_failed(attempt, code or "internal_error")
        return self._store(attempt, run.output, time.monotonic() - started)

    def _finish_failed(self, attempt: _Attempt, code: str) -> str:
        """Mark the job FAILED if this worker still owns it; "discarded" if not."""
        values: dict[str, object] = {
            "status": JobStatus.FAILED,
            "error_code": code,
            "finished_at": self.clock(),
            "lease_until": None,
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
            outcome=result_outcome(result)[1],
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
        """One transaction: crops, result, html, outcome code, DONE. False if not ours."""
        result = output.result
        _, code = result_outcome(result)
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
            # The row already counts every reservation (`_hold_on_job`). A converter that
            # reports more than went through the budget (a test fake) is counted as well.
            job.cost_usd = max(
                float(job.cost_usd or 0.0), round(attempt.prior_usd + result.stats.cost_usd, 8)
            )
            job.model_calls = max(
                int(job.model_calls or 0), attempt.prior_calls + result.stats.model_calls
            )
            job.pages_done = job.page_count
            job.status = JobStatus.DONE
            job.error_code = code
            job.finished_at = self.clock()
            job.lease_until = None
        return True

    # ------------------------------------------------------------------ loop

    def run_once(self) -> str | None:
        """Requeue expired leases, then claim and process one job. Returns its id.

        Claims nothing while the daily budget is spent (`budget_closed`)."""
        self.requeue_expired()
        if self.budget_closed():
            if not self._paused:
                self._paused = True
                log_event(logger, "claims_paused", logging.WARNING, reason="daily_budget")
            return None
        if self._paused:
            self._paused = False
            log_event(logger, "claims_resumed")
        claim = self.claim()
        if claim is None:
            return None
        outcome = self.process(claim)
        if outcome in {JobStatus.DONE, JobStatus.FAILED}:  # usage event (web/events.py)
            events.record_finished(self.factory, self.settings, claim.job_id)
        return claim.job_id

    def purge_backstop(self) -> int | None:
        """Run the retention purge if it is due (see the module docstring). Returns the
        jobs deleted, or None when it was not due or failed. Never raises."""
        if time.monotonic() < self._next_purge:
            return None
        self._next_purge = time.monotonic() + PURGE_EVERY_S
        from a11y_stem.ops.retention import purge_expired

        try:
            stats = purge_expired(self.factory, self.clock(), source="worker")
        except Exception as exc:
            log_exception(logger, "purge_backstop_failed", exc)
            return None
        if stats.jobs_deleted:
            log_event(
                logger,
                "purge_backstop_deleted",
                logging.WARNING,
                jobs_deleted=stats.jobs_deleted,
            )
        return stats.jobs_deleted

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._seen_at = time.monotonic()
            self.purge_backstop()
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

    @property
    def alive(self) -> bool:
        """The worker thread is running and has not been told to stop."""
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    @property
    def draining(self) -> bool:
        """Stop was asked for (SIGTERM or app shutdown): no new job is claimed."""
        return self._stop.is_set()

    def seconds_since_seen(self) -> float:
        """Seconds since the loop last came round or a running conversion last beat. A
        long conversion beats every `lease_s / 3`, so a value past `lease_s` means the
        thread is stuck (a database call that never returns, say)."""
        return time.monotonic() - self._seen_at

    def begin_shutdown(self, timeout: float = 6.0) -> None:
        """Start `stop` on another thread and return at once. Called from the SIGTERM
        handler: Cloud Run kills the process 10 seconds after the signal, so the claim
        loop must stop and the running job's lease must end now, not after uvicorn has
        finished draining connections. Safe to call twice; `stop` is idempotent."""
        if self._shutdown_thread is not None:
            return
        self._stop.set()
        self._shutdown_thread = threading.Thread(
            target=self.stop, kwargs={"timeout": timeout}, name="a11y-stem-shutdown", daemon=True
        )
        self._shutdown_thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the loop. A job being converted is cancelled (no more model calls) and its
        lease ended now, so another instance requeues it at once instead of after the
        lease runs out."""
        self._stop.set()
        attempt = self._current
        if attempt is not None:
            self._cancel(attempt, "shutdown")
            try:
                with session_scope(self.factory) as session:
                    session.execute(
                        update(Job)
                        .where(*self._owned(attempt.claim))
                        .values(lease_until=self.clock())
                        .execution_options(synchronize_session=False)
                    )
            except Exception as exc:  # the lease still runs out on its own
                log_exception(logger, "lease_release_failed", exc, job_id=attempt.claim.job_id)
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
