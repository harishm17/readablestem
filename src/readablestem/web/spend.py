"""Database-backed daily spend cap: `core.budget.SpendGuard` over the `spend_days` table.

Every model call reserves its worst-case cost first. With several workers (threads or
Cloud Run instances) the check and the hold must be one atomic step, so `reserve` is a
single conditional UPDATE:

    UPDATE spend_days SET held_usd = held_usd + :a
    WHERE day = :d AND spent_usd + held_usd + :a <= :cap

If no row matched, the cap would be exceeded and `BudgetExceeded("daily")` is raised.
Postgres re-checks the WHERE clause against the newest row version when two UPDATEs
collide, and SQLite serialises writers, so concurrent reservations never overshoot.

A process that dies between reserve and settle must not leave its hold in place until the
UTC day ends: a few crashes would close the day on money nobody spent. Every hold is also a
row of `spend_holds`, written in the transaction that makes the hold and deleted by the
settlement that releases it (so a hold is released once, however many settle or release it).
`release_holds` gives back the holds of jobs whose lease ran out or whose process is gone, and
of any hold older than a job can run; the worker calls it when a lease expires and at
start-up. A released hold counts no spend (the call that made it may have been billed; the
provider's own monthly limit is what covers that).

**The open pool.** Jobs of open-tier accounts (`web.access`) also draw on a pool of their
own, `open_cap_usd` (`OPEN_TIER_DAILY_SPEND_CAP_USD`), kept in two more columns of the same
row. A reservation for the pool is still one UPDATE, with one more condition and one more
column to change:

    UPDATE spend_days SET held_usd = held_usd + :a, open_held_usd = open_held_usd + :a
    WHERE day = :d AND spent_usd + held_usd + :a <= :cap
      AND open_spent_usd + open_held_usd + :a <= :open_cap

It must fit both: the pool is a share of the day's cap, not extra money, so the total can
never pass `cap_usd` and strangers can never take more than the pool from the instructors'
budget. Settling changes the totals and the pool's columns in the same statement, and a
reservation remembers its pool, so a call that straddles midnight or is settled through
another view releases the right numbers. The pool's refusal is a `PoolExhausted`, which is
a `BudgetExceeded("daily")` to everything that handles one (a job stopped by it is stopped
for the day, like any other daily stop).

`largest_reservation_usd(settings)` is the threshold below which the day counts as spent:
uploads are refused and the worker stops claiming jobs once less than that is left,
because no element could be converted with it.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, Reservation, SpendGuard
from readablestem.web.access import POOL_OPEN
from readablestem.web.database import insert_ignore, rowcount, utc_today
from readablestem.web.db import SpendDay, SpendHold, session_scope, utcnow

# Float sums of many small costs drift by ~1e-16; this keeps an exact fit under the cap.
_EPSILON = 1e-9


class PoolExhausted(BudgetExceeded):
    """The open-tier pool has no room for the reservation (the day's cap might have).
    Scope "daily", so every handler of a daily stop treats it as one; `pool` says which."""

    def __init__(self, cap_usd: float | None) -> None:
        cap = "" if cap_usd is None else f" ${cap_usd:.2f}"
        super().__init__("daily", f"open-tier daily spend cap{cap} reached")
        self.pool = POOL_OPEN


@dataclass(frozen=True)
class SpendSnapshot:
    day: date
    spent_usd: float
    held_usd: float
    cap_usd: float | None
    # The open-tier pool: the part of spent and held that belongs to open-tier jobs.
    open_spent_usd: float = 0.0
    open_held_usd: float = 0.0
    open_cap_usd: float | None = None

    @property
    def remaining_usd(self) -> float | None:
        if self.cap_usd is None:
            return None
        return max(0.0, self.cap_usd - self.spent_usd - self.held_usd)

    @property
    def open_remaining_usd(self) -> float | None:
        """What is left in the open-tier pool alone (None: the pool has no cap)."""
        if self.open_cap_usd is None:
            return None
        return max(0.0, self.open_cap_usd - self.open_spent_usd - self.open_held_usd)

    def remaining_for(self, pool: str | None) -> float | None:
        """What a job drawing on `pool` can still use: the smaller of the day's remainder
        and, for the open pool, the pool's. None when nothing limits it."""
        left = [
            x
            for x in (self.remaining_usd, self.open_remaining_usd if pool == POOL_OPEN else None)
            if x is not None
        ]
        return min(left) if left else None


class DbSpendGuard:
    """Global daily cap shared by every worker that talks to the same database.

    `cap_usd=None` (local development without a cap) records spend but never refuses.
    `open_cap_usd` is the open-tier pool inside the cap (see the module docstring); None
    records the pool's spend but never refuses on it. `reserve(..., pool="open")` or
    `pooled("open")` is how a job of an open-tier account draws on it.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        cap_usd: float | None,
        *,
        open_cap_usd: float | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.factory = factory
        self.cap_usd = cap_usd
        self.open_cap_usd = open_cap_usd
        self._clock = clock
        # Reservation id -> (day, in the open pool): what this process made. The books are
        # `spend_holds`; this only tells a settlement whose row was already released (a hold
        # given back after a lease ran out) which columns the spend belongs in.
        self._holds: dict[str, tuple[date, bool]] = {}
        self._lock = threading.Lock()

    def _today(self) -> date:
        return utc_today(self._clock())

    @staticmethod
    def _ensure_day(session: Session, day: date) -> None:
        insert_ignore(
            session,
            SpendDay,
            {
                "day": day,
                "spent_usd": 0.0,
                "held_usd": 0.0,
                "open_spent_usd": 0.0,
                "open_held_usd": 0.0,
            },
        )

    def pooled(self, pool: str | None) -> SpendGuard:
        """This guard for jobs that draw on `pool` (None: the day's cap alone). What
        `core.budget.JobBudget` is given, so the budget code needs to know nothing of pools."""
        return self if pool is None else _PooledGuard(self, pool)

    def scoped(self, pool: str | None, job_id: str | None) -> SpendGuard:
        """`pooled`, and every hold made through it is recorded against `job_id`, so
        `release_holds(job_ids=...)` can give back what that job's dead attempt held."""
        return self if pool is None and job_id is None else _PooledGuard(self, pool, job_id)

    def reserve(
        self, amount_usd: float, *, pool: str | None = None, job_id: str | None = None
    ) -> Reservation:
        """One conditional UPDATE. A refusal by the cap and a missing row look the same to
        it, so when it changes nothing the day's row is made if missing (an insert that does
        nothing when the row exists) and the UPDATE runs once more; only then is it a
        refusal. The extra statements run only on a refusal or the first hold of a day.

        No per-process "row exists" cache decides this: another transaction's new row is
        invisible until it commits, so trusting such a cache refused holds there was room
        for when many started at once (20 threads got 61 of 100).

        With `pool="open"` the same UPDATE also needs room in the open pool and holds there
        too. A refusal is `PoolExhausted` when the day's cap alone would have allowed it,
        else the usual daily refusal."""
        if amount_usd < 0:
            raise ValueError("reservation must be >= 0")
        if pool not in (None, POOL_OPEN):
            raise ValueError(f"unknown spend pool {pool!r}")
        in_pool = pool == POOL_OPEN
        day = self._today()
        values: dict[str, Any] = {"held_usd": SpendDay.held_usd + amount_usd}
        if in_pool:
            values["open_held_usd"] = SpendDay.open_held_usd + amount_usd
        stmt = (
            update(SpendDay)
            .where(SpendDay.day == day)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if self.cap_usd is not None:
            stmt = stmt.where(
                SpendDay.spent_usd + SpendDay.held_usd + amount_usd <= self.cap_usd + _EPSILON
            )
        if in_pool and self.open_cap_usd is not None:
            stmt = stmt.where(
                SpendDay.open_spent_usd + SpendDay.open_held_usd + amount_usd
                <= self.open_cap_usd + _EPSILON
            )
        reservation = Reservation(uuid.uuid4().hex, amount_usd)
        with session_scope(self.factory) as session:
            held = rowcount(session.execute(stmt)) == 1
            if not held:
                self._ensure_day(session, day)
                held = rowcount(session.execute(stmt)) == 1
            if not held:
                if in_pool and self._pool_is_the_limit(session, day, amount_usd):
                    raise PoolExhausted(self.open_cap_usd)
                raise BudgetExceeded("daily", f"daily spend cap ${self.cap_usd:.2f} reached")
            # The hold and its record are one transaction: neither exists without the other.
            session.add(
                SpendHold(
                    id=reservation.id,
                    day=day,
                    amount_usd=amount_usd,
                    in_pool=in_pool,
                    job_id=job_id,
                    created_at=self._clock(),
                )
            )
        with self._lock:
            self._holds[reservation.id] = (day, in_pool)
        return reservation

    def _pool_is_the_limit(self, session: Session, day: date, amount_usd: float) -> bool:
        """After a refusal for the pool: would the day's cap alone have allowed it? (Read
        in the refusing transaction, only to word the refusal: it decides nothing.)"""
        if self.cap_usd is None:
            return True
        row = session.execute(
            select(SpendDay.spent_usd, SpendDay.held_usd).where(SpendDay.day == day)
        ).first()
        committed = float(row[0]) + float(row[1]) if row else 0.0
        return committed + amount_usd <= self.cap_usd + _EPSILON

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        """Replace the hold with the actual cost. The hold's record is deleted first and only
        the settlement that deleted it releases the amount, so a hold that was already given
        back (`release_holds`) is not given back twice: the spend is still counted."""
        spent = max(0.0, actual_usd)
        with self._lock:
            hint = self._holds.pop(reservation.id, None)
        with session_scope(self.factory) as session:
            row = session.execute(
                select(SpendHold.day, SpendHold.in_pool, SpendHold.amount_usd).where(
                    SpendHold.id == reservation.id
                )
            ).first()
            won = row is not None and (
                rowcount(
                    session.execute(
                        delete(SpendHold)
                        .where(SpendHold.id == reservation.id)
                        .execution_options(synchronize_session=False)
                    )
                )
                == 1
            )
            if won and row is not None:
                day, in_pool, release = row.day, bool(row.in_pool), float(row.amount_usd)
            else:  # released already, or not ours: the spend still counts, nothing is released
                day, in_pool = hint if hint is not None else (self._today(), False)
                release = 0.0
            held_after = SpendDay.held_usd - release
            values: dict[str, Any] = {
                "held_usd": case((held_after < 0, 0.0), else_=held_after),
                "spent_usd": SpendDay.spent_usd + spent,
            }
            if in_pool:
                open_held_after = SpendDay.open_held_usd - release
                values["open_held_usd"] = case((open_held_after < 0, 0.0), else_=open_held_after)
                values["open_spent_usd"] = SpendDay.open_spent_usd + spent
            stmt = (
                update(SpendDay)
                .where(SpendDay.day == day)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            # The day's row exists: the reservation made it. Only a settlement of a hold this
            # database never saw can find none, and then it is made first.
            if rowcount(session.execute(stmt)) != 1:
                self._ensure_day(session, day)
                session.execute(stmt)

    def release_holds(
        self, *, job_ids: Collection[str] | None = None, older_than_s: float | None = None
    ) -> tuple[int, float]:
        """Give back holds nobody will settle: those recorded against `job_ids` (their
        attempt died or overran its lease) and/or older than `older_than_s` seconds (a job
        cannot run that long). Returns (holds released, dollars). No spend is counted. Safe
        against a live settlement of the same hold: whoever deletes the record releases it."""
        conditions = []
        if job_ids is not None:
            if not job_ids:
                return 0, 0.0
            conditions.append(SpendHold.job_id.in_(list(job_ids)))
        if older_than_s is not None:
            conditions.append(
                SpendHold.created_at < self._clock() - timedelta(seconds=older_than_s)
            )
        if not conditions:
            raise ValueError("release_holds needs job_ids or older_than_s")
        released, dollars = 0, 0.0
        with session_scope(self.factory) as session:
            rows = session.execute(
                select(SpendHold.id, SpendHold.day, SpendHold.in_pool, SpendHold.amount_usd).where(
                    or_(*conditions)
                )
            ).all()
            totals: dict[date, list[float]] = {}
            for row in rows:
                gone = session.execute(
                    delete(SpendHold)
                    .where(SpendHold.id == row.id)
                    .execution_options(synchronize_session=False)
                )
                if rowcount(gone) != 1:
                    continue
                total = totals.setdefault(row.day, [0.0, 0.0])
                total[0] += float(row.amount_usd)
                if row.in_pool:
                    total[1] += float(row.amount_usd)
                released += 1
                dollars += float(row.amount_usd)
            for day, (held, open_held) in totals.items():
                held_after = SpendDay.held_usd - held
                open_after = SpendDay.open_held_usd - open_held
                session.execute(
                    update(SpendDay)
                    .where(SpendDay.day == day)
                    .values(
                        held_usd=case((held_after < 0, 0.0), else_=held_after),
                        open_held_usd=case((open_after < 0, 0.0), else_=open_after),
                    )
                    .execution_options(synchronize_session=False)
                )
        return released, dollars

    def snapshot(self) -> SpendSnapshot:
        day = self._today()
        with session_scope(self.factory) as session:
            row = session.execute(
                select(
                    SpendDay.spent_usd,
                    SpendDay.held_usd,
                    SpendDay.open_spent_usd,
                    SpendDay.open_held_usd,
                ).where(SpendDay.day == day)
            ).first()
        spent, held, open_spent, open_held = (
            (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
            if row
            else (0.0, 0.0, 0.0, 0.0)
        )
        return SpendSnapshot(
            day=day,
            spent_usd=spent,
            held_usd=held,
            cap_usd=self.cap_usd,
            open_spent_usd=open_spent,
            open_held_usd=open_held,
            open_cap_usd=self.open_cap_usd,
        )

    def spent_usd(self) -> float:
        return self.snapshot().spent_usd

    def cap_reached(self, headroom_usd: float = 0.0, *, pool: str | None = None) -> bool:
        """True when not even `headroom_usd` (e.g. one cheap model call) fits today, for a
        job drawing on `pool` (the open pool is also bounded by the day's cap)."""
        remaining = self.snapshot().remaining_for(pool)
        return remaining is not None and remaining <= headroom_usd


class _PooledGuard:
    """A `DbSpendGuard` seen by the jobs of one pool and/or one job: `reserve` holds in that
    pool too and records the hold against that job."""

    def __init__(self, guard: DbSpendGuard, pool: str | None, job_id: str | None = None) -> None:
        self._guard = guard
        self.pool = pool
        self.job_id = job_id

    def reserve(self, amount_usd: float) -> Reservation:
        return self._guard.reserve(amount_usd, pool=self.pool, job_id=self.job_id)

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        self._guard.settle(reservation, actual_usd)

    def spent_usd(self) -> float:
        return self._guard.spent_usd()

    def snapshot(self) -> SpendSnapshot:
        return self._guard.snapshot()

    def cap_reached(self, headroom_usd: float = 0.0) -> bool:
        return self._guard.cap_reached(headroom_usd, pool=self.pool)


def largest_reservation_usd(settings: Settings) -> float:
    """Worst-case cost of the largest single model call the pipeline reserves.

    That is a crop plus its fenced neighbouring text, at the larger output limit of the
    two steps, on the dearer model (`ops.limits.largest_reservation_usd`, without the
    optional steps: about $0.03 on gpt-6.1-sol and about $0.11 on claude-opus-5-5 at
    2026-10 prices). With less than this left under the daily cap the first reservation
    of a typical document fails, so admitting an upload, or claiming a job, would only
    produce a document with everything flagged "not converted".
    """
    from readablestem.ops.limits import largest_reservation_usd as largest

    return largest(settings, optional_steps=False)
