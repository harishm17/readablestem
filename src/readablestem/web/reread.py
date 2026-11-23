"""Reading a region of a page with the equation step, from a review click (T3-5, T3-11).

Two review actions ask a model to read something the conversion did not: a region the
reviewer marked as an equation, and a table cell that holds maths. Both are the equation
step (`pipeline.equation_step.transcribe`) on a crop, so the answer carries the checks and
the confidence of any equation, and the reviewer decides on it like any other.

Spending is controlled exactly as in the worker, because the money is the same money:

- every call reserves its worst-case cost first, against the day's cap (`web.spend`) and
  against the job's own caps (`worker.job_caps`: the whole job, the conversion's spend
  included), the job's side as one conditional UPDATE on the job's row, so two clicks
  cannot overshoot (`ReadBudget`);
- a refusal by either cap is `ReadRefused` with a sentence for the reviewer (nothing was
  sent), never an error page;
- the routes count each read against a per-person rate limit (`abuse.RULES["reread"]`) and
  allow one at a time per person (`ReadGate`), so a script cannot queue model calls;
- a read for an open-tier account (`web.access`) also draws on the open spend pool
  (`web.spend`) and counts against their daily allowance of reads
  (`OPEN_TIER_DAILY_REREADS`, `web.quota.try_consume_reread`). The count is made before
  the call, so two clicks cannot start more than the allowance, and given back when nothing
  was read (`give_back`): a read that returned no equation, a cap that stopped it, a model
  that was not there. An invited account has no allowance and no pool, but its reads are
  counted all the same, for the Account page to say how many it used today;
- a question the model has already answered for this document is answered from the record
  (F-093): the answer is kept in the job's step records under `models.question.question_key`
  (the crop, the prompt version, the model, the text sent with it: the same record the
  conversion's page-level passes use, `web.step_cache`), and a read that finds it makes no
  call, reserves nothing, is charged nothing and counts nothing. The count is therefore made
  at the first call that is really sent, not before the lookup. The keys and what is logged
  are hashes and counts, never the crop or its text; the records go with the job
  (`ops.retention`).

The call is made with no database transaction open and no row locked (it takes seconds);
the route locks the job afterwards, to check that nothing changed meanwhile, and applies
the answer. Nothing here is logged but a category code.

Each request that is really sent also tells the model provider's status how it ended
(`AppContext.record_provider_call`), as the worker's requests do: a reread is the only model
call the web process makes, and when the provider is down it is the reader's first sign.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from readablestem.core.budget import BudgetExceeded, JobBudget, Reservation
from readablestem.core.types import Element, Fix, ModelCall
from readablestem.models.base import T, VisionClient, VisionRequest
from readablestem.web import access, notation, strings
from readablestem.web.database import rowcount, utc_today
from readablestem.web.db import Job, JobStatus, session_scope
from readablestem.web.deps import get_ctx
from readablestem.web.logsafe import log_event, log_exception
from readablestem.web.quota import (
    allowance_phrase,
    give_back_reread,
    retry_after,
    try_consume_reread,
)
from readablestem.web.step_cache import DbStepCache

logger = logging.getLogger(__name__)

_EPSILON = 1e-9
# What the reviewer is told when a cap, or the model, did not allow a read. Plain words,
# no codes; the way on is always "type it yourself".
REFUSALS = {
    "daily": "The service has used up today's allowance for reading with the model, so this "
    "could not be read now. Try again tomorrow, or type it yourself.",
    "job_usd": "This document has used its allowance for reading with the model, so this "
    "could not be read. Type it yourself.",
    "job_calls": "This document has used its allowance for reading with the model, so this "
    "could not be read. Type it yourself.",
    "cancelled": "This document was deleted or changed hands, so nothing was read.",
}
OPEN_POOL_REFUSAL = (
    "Reading with the model is paused for new accounts for today, so this could not be read "
    "now. Try again tomorrow, or type it yourself."
)
FAILED = "The tool could not read it just now. Try again in a minute, or type it yourself."
BUSY = "You already have something being read. Wait for it to finish, then try again."


class ReadRefused(Exception):
    """Nothing was read, and `message` says why in plain words (shown to the owner)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReadGate:
    """One read at a time for each person, a few at a time for the instance. Per process:
    like the rate limits, a seatbelt for a beta of a few people."""

    def __init__(self, total: int = 3) -> None:
        self._total = threading.BoundedSemaphore(total)
        self._users: set[str] = set()
        self._lock = threading.Lock()

    def enter(self, user_id: str) -> None:
        with self._lock:
            if user_id in self._users:
                raise ReadRefused(BUSY)
            if not self._total.acquire(blocking=False):
                raise ReadRefused(BUSY)
            self._users.add(user_id)

    def leave(self, user_id: str) -> None:
        with self._lock:
            if user_id in self._users:
                self._users.discard(user_id)
                self._total.release()


GATE = ReadGate()


@dataclass
class ReadBudget(JobBudget):
    """`JobBudget` that also holds every reservation on the job's row (as the worker's
    `_AttemptBudget` does for a conversion): the hold is added to `jobs.cost_usd` and
    `jobs.model_calls` only while the job stays under its caps, and replaced by the actual
    cost when the call settles. Whichever cap refuses raises `BudgetExceeded`."""

    factory: sessionmaker[Session] | None = field(default=None, repr=False)
    job_id: str = ""
    job_max_usd: float = 0.0
    job_max_calls: int = 0

    def reserve(self, amount_usd: float, *, count_call: bool = True) -> Reservation:
        calls = 1 if count_call else 0
        self._hold(amount_usd, calls)
        try:
            return super().reserve(amount_usd, count_call=count_call)
        except BaseException:
            self._adjust(-amount_usd, -calls)
            raise

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        super().settle(reservation, actual_usd)
        self._adjust(max(0.0, actual_usd) - reservation.amount_usd, 0)

    def _hold(self, amount_usd: float, calls: int) -> None:
        assert self.factory is not None
        under = [Job.cost_usd + amount_usd <= self.job_max_usd + _EPSILON]
        if calls:
            under.append(Job.model_calls + calls <= self.job_max_calls)
        with session_scope(self.factory) as session:
            won = rowcount(
                session.execute(
                    update(Job)
                    .where(Job.id == self.job_id, Job.status == JobStatus.DONE, *under)
                    .values(cost_usd=Job.cost_usd + amount_usd, model_calls=Job.model_calls + calls)
                    .execution_options(synchronize_session=False)
                )
            )
            used = (
                None
                if won
                else session.execute(
                    select(Job.model_calls).where(Job.id == self.job_id)
                ).one_or_none()
            )
        if won:
            return
        if used is None:
            raise BudgetExceeded("cancelled", "the document is gone")
        if calls and int(used.model_calls or 0) + calls > self.job_max_calls:
            raise BudgetExceeded("job_calls", f"document call cap {self.job_max_calls} reached")
        raise BudgetExceeded("job_usd", f"document spend cap ${self.job_max_usd:.2f} reached")

    def _adjust(self, cost_delta: float, calls_delta: int) -> None:
        if not cost_delta and not calls_delta:
            return
        assert self.factory is not None
        try:
            with session_scope(self.factory) as session:
                session.execute(
                    update(Job)
                    .where(Job.id == self.job_id)
                    .values(
                        cost_usd=Job.cost_usd + cost_delta,
                        model_calls=Job.model_calls + calls_delta,
                    )
                    .execution_options(synchronize_session=False)
                )
        except Exception as exc:  # the hold stays counted: errs on the side of less spend
            log_exception(logger, "job_spend_update_failed", exc, job_id=self.job_id)


# Said beside a button that starts a read, to a person the document was shared with: the owner's
# allowance is the one used, whoever asks. No name: the review page does not show the owner's.
SHARED_NOTE = (
    "This uses the document owner's allowance for reading with the model, not yours. "
    "Asking again for the same picture uses none."
)


def reread_allowance_message(allowance: int) -> str:
    """What the reviewer is told when an open-tier account has started all the reads it may
    today (the number is the setting; a read is counted when it starts; the count starts
    again at midnight UTC)."""
    used_up = allowance_phrase(allowance, allowance, "read", retry_after().words)
    return (
        f"{strings.MODEL_READS_TODAY}: {used_up} "
        "That is the most a new account can start. A read counts when it starts and is given "
        "back when nothing was read. So this could not be read. Type it yourself, or try "
        "again then."
    )


def read_budget(request: Request, job: Job) -> ReadBudget:
    """The budget of a read on this job: what is left of the job's caps and of the day's, and
    of the open pool when the job's owner is an open-tier account."""
    from readablestem.web.worker import job_caps

    ctx = get_ctx(request)
    max_usd, max_calls = job_caps(ctx.settings, job.page_count, bool(job.image_pages))
    max_usd += float(job.cap_base_usd or 0.0)  # after a re-run: the caps count from its start
    max_calls += int(job.cap_base_calls or 0)
    with session_scope(ctx.factory) as session:
        pool = access.spend_pool(access.tier_of(session, job.user_id))
    return ReadBudget(
        guard=ctx.guard.scoped(pool, job.id, job.user_id),
        max_usd=max(0.0, max_usd - float(job.cost_usd or 0.0)),
        max_calls=max(0, max_calls - int(job.model_calls or 0)),
        factory=ctx.factory,
        job_id=job.id,
        job_max_usd=max_usd,
        job_max_calls=max_calls,
    )


def _count_against_allowance(request: Request, user_id: str) -> None:
    """One more read started today for the document's owner. `ReadRefused` when an open-tier
    account has used the day's allowance; an invited account has none, but its reads are
    counted all the same (the Account page says how many)."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        allowance = access.reread_allowance(ctx.settings, access.tier_of(session, user_id))
        counted = try_consume_reread(session, user_id, allowance=allowance, day=utc_today())
    if not counted:
        log_event(logger, "reread_refused", scope="allowance")
        assert allowance is not None
        raise ReadRefused(reread_allowance_message(allowance))


class _CountsWhenSent:
    """The client of a read, behind the record of answers (`_PageAnswers`): the first request
    that is really sent counts the read against the owner's day, before the model is asked.
    A request the record answers never gets here, so it counts nothing. The request itself
    passes on as it is."""

    def __init__(
        self,
        inner: VisionClient,
        count: Callable[[], None],
        observe: Callable[[ModelCall], None] | None = None,
    ) -> None:
        self._inner = inner
        self._count = count
        self._observe = observe
        self.counted = False

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        if not self.counted:
            self._count()
            self.counted = True
        parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
        if self._observe is not None:
            self._observe(call)
        return parsed, call

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def give_back(request: Request, user_id: str) -> None:
    """Take the read counted for this start off today's count (`quota.give_back_reread`).
    A failure here leaves it counted: it errs on the side of fewer reads."""
    ctx = get_ctx(request)
    try:
        with session_scope(ctx.factory) as session:
            give_back_reread(session, user_id, day=utc_today())
    except Exception as exc:
        log_exception(logger, "reread_give_back_failed", exc)


def is_empty(fix: Fix) -> bool:
    """A read that proposes no equation (the model said it is none, or could not be read)."""
    return fix.equation is None or not fix.equation.latex.strip()


def read_equation(
    request: Request,
    job: Job,
    user_id: str,
    element: Element,
    png: bytes,
    context_text: str | None = None,
    *,
    escalate: bool = False,
) -> tuple[Fix, bool]:
    """Read the crop `png` of `element` (an equation) with the equation step. Returns its
    fix (possibly FAILED or ABSTAINED: the reviewer decides) and whether the model was asked
    (False: the record answered, so the page must not say it was read anew). The read counts
    against the job's owner (the day's allowance, spend pool and caps), whoever asked;
    `user_id` is who is waiting for it, so one person has one read going at a time. A read
    that returns no
    equation, or is stopped by a cap or the model's availability, is given back against the
    day's allowance. Raises ReadRefused when a cap or the model's availability stopped it.
    The caller holds no database lock.

    `escalate`: a read that came back empty from a cheaper model than the main one is asked
    once more of the main model (a re-read from the review card). It is the same step with
    the request the main model gets in the pipeline, drawn from the same caps and the same
    count, and the person sees the answer of the last one only."""
    from readablestem.pipeline import equation_step
    from readablestem.pipeline.run import _PageAnswers

    ctx = get_ctx(request)
    job_id = job.id
    GATE.enter(user_id)
    sending: _CountsWhenSent | None = None
    kept = False
    try:
        budget = read_budget(request, job)
        # The owner's allowance, not the reviewer's (G1), counted when a request is sent.
        sending = _CountsWhenSent(
            ctx.client_factory(ctx.settings, budget),
            lambda: _count_against_allowance(request, job.user_id),
            # How each request ended counts toward the model provider's status, as the worker's do.
            lambda call: ctx.record_provider_call(call.outcome),
        )
        # A question this document's model has already answered is answered from the record.
        client = _PageAnswers(sending, DbStepCache(ctx.factory, job_id), ctx.settings)
        try:
            with notation.in_force(ctx.factory, ctx.settings, job_id):
                fix = equation_step.transcribe(
                    element, png, client=client, settings=ctx.settings, context_text=context_text
                )
                if escalate and is_empty(fix) and _asked_cheaper(fix, ctx.settings.main_model):
                    first = fix
                    fix = equation_step.transcribe(
                        element,
                        png,
                        client=client,
                        settings=ctx.settings,
                        context_text=context_text,
                        first_model=ctx.settings.main_model,
                    )
                    fix = fix.model_copy(update={"calls": [*first.calls, *fix.calls]})
        except BudgetExceeded as exc:
            log_event(logger, "reread_refused", job_id=job_id, scope=exc.scope)
            if getattr(exc, "pool", None) == access.POOL_OPEN:
                raise ReadRefused(OPEN_POOL_REFUSAL) from None
            raise ReadRefused(REFUSALS.get(exc.scope, FAILED)) from None
        except ReadRefused:
            raise
        except Exception as exc:  # a model or configuration problem: never an error page
            log_exception(logger, "reread_failed", exc, job_id=job_id)
            raise ReadRefused(FAILED) from None
        kept = not is_empty(fix)
    finally:
        if sending is not None and sending.counted and not kept:
            give_back(request, job.user_id)
        GATE.leave(user_id)
    asked = sending.counted
    log_event(
        logger,
        "reread",
        job_id=job_id,
        status=fix.status.value,
        cost=round(fix.cost_usd, 4) if asked else 0.0,
        asked=asked,
    )
    return fix, asked


def _asked_cheaper(fix: Fix, main_model: str) -> bool:
    """The read was made, and not by the main model: there is a stronger one to ask."""
    return bool(fix.calls) and all(c.model != main_model for c in fix.calls)
