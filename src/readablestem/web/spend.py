"""Database-backed daily spend cap: `core.budget.SpendGuard` over the `spend_days` table.

Every model call reserves its worst-case cost first. With several workers (threads or
Cloud Run instances) the check and the hold must be one atomic step, so `reserve` is a
single conditional UPDATE:

    UPDATE spend_days SET held_usd = held_usd + :a
    WHERE day = :d AND spent_usd + held_usd + :a <= :cap

If no row matched, the cap would be exceeded and `BudgetExceeded("daily")` is raised.
Postgres re-checks the WHERE clause against the newest row version when two UPDATEs
collide, and SQLite serialises writers, so concurrent reservations never overshoot.

A process that dies between reserve and settle leaves its hold in place until the UTC day
ends. That errs on the side of spending less, which is the point of the cap.

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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session, sessionmaker

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, Reservation, SpendGuard
from readablestem.web.access import POOL_OPEN
from readablestem.web.database import insert_ignore, rowcount, utc_today
from readablestem.web.db import SpendDay, session_scope, utcnow

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
        # Reservation id -> (day it was held on, whether it is in the open pool), so a call
        # that straddles midnight releases its hold from the right row and columns.
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

    def reserve(self, amount_usd: float, *, pool: str | None = None) -> Reservation:
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
        with session_scope(self.factory) as session:
            held = rowcount(session.execute(stmt)) == 1
            if not held:
                self._ensure_day(session, day)
                held = rowcount(session.execute(stmt)) == 1
            if not held:
                if in_pool and self._pool_is_the_limit(session, day, amount_usd):
                    raise PoolExhausted(self.open_cap_usd)
                raise BudgetExceeded("daily", f"daily spend cap ${self.cap_usd:.2f} reached")
        reservation = Reservation(uuid.uuid4().hex, amount_usd)
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
        with self._lock:
            hold = self._holds.pop(reservation.id, None)
        if hold is None:  # not ours (should not happen); count the spend, release nothing
            day, in_pool, release = self._today(), False, 0.0
        else:
            (day, in_pool), release = hold, reservation.amount_usd
        spent = max(0.0, actual_usd)
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
        with session_scope(self.factory) as session:
            # The row exists: this reservation made it. Only a settlement of a hold this
            # process did not make (above) can find none, and then it is made first.
            if rowcount(session.execute(stmt)) != 1:
                self._ensure_day(session, day)
                session.execute(stmt)

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
    """A `DbSpendGuard` seen by jobs of one pool: `reserve` holds in that pool too."""

    def __init__(self, guard: DbSpendGuard, pool: str) -> None:
        self._guard = guard
        self.pool = pool

    def reserve(self, amount_usd: float) -> Reservation:
        return self._guard.reserve(amount_usd, pool=self.pool)

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
