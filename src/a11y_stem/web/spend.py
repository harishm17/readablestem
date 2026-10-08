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
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.core.budget import BudgetExceeded, Reservation
from a11y_stem.web.database import insert_ignore, rowcount, utc_today
from a11y_stem.web.db import SpendDay, session_scope, utcnow

# Float sums of many small costs drift by ~1e-16; this keeps an exact fit under the cap.
_EPSILON = 1e-9


@dataclass(frozen=True)
class SpendSnapshot:
    day: date
    spent_usd: float
    held_usd: float
    cap_usd: float | None

    @property
    def remaining_usd(self) -> float | None:
        if self.cap_usd is None:
            return None
        return max(0.0, self.cap_usd - self.spent_usd - self.held_usd)


class DbSpendGuard:
    """Global daily cap shared by every worker that talks to the same database.

    `cap_usd=None` (local development without a cap) records spend but never refuses.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        cap_usd: float | None,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.factory = factory
        self.cap_usd = cap_usd
        self._clock = clock
        # Reservation id -> day it was held on, so a call that straddles midnight releases
        # its hold from the right row.
        self._days: dict[str, date] = {}
        self._lock = threading.Lock()

    def _today(self) -> date:
        return utc_today(self._clock())

    @staticmethod
    def _ensure_day(session: Session, day: date) -> None:
        insert_ignore(session, SpendDay, {"day": day, "spent_usd": 0.0, "held_usd": 0.0})

    def reserve(self, amount_usd: float) -> Reservation:
        if amount_usd < 0:
            raise ValueError("reservation must be >= 0")
        day = self._today()
        with session_scope(self.factory) as session:
            self._ensure_day(session, day)
            stmt = (
                update(SpendDay)
                .where(SpendDay.day == day)
                .values(held_usd=SpendDay.held_usd + amount_usd)
                .execution_options(synchronize_session=False)
            )
            if self.cap_usd is not None:
                stmt = stmt.where(
                    SpendDay.spent_usd + SpendDay.held_usd + amount_usd <= self.cap_usd + _EPSILON
                )
            if rowcount(session.execute(stmt)) != 1:
                raise BudgetExceeded("daily", f"daily spend cap ${self.cap_usd:.2f} reached")
        reservation = Reservation(uuid.uuid4().hex, amount_usd)
        with self._lock:
            self._days[reservation.id] = day
        return reservation

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        with self._lock:
            day = self._days.pop(reservation.id, None)
        if day is None:  # not ours (should not happen); count the spend, release nothing
            day, release = self._today(), 0.0
        else:
            release = reservation.amount_usd
        held_after = SpendDay.held_usd - release
        with session_scope(self.factory) as session:
            self._ensure_day(session, day)
            session.execute(
                update(SpendDay)
                .where(SpendDay.day == day)
                .values(
                    held_usd=case((held_after < 0, 0.0), else_=held_after),
                    spent_usd=SpendDay.spent_usd + max(0.0, actual_usd),
                )
                .execution_options(synchronize_session=False)
            )

    def snapshot(self) -> SpendSnapshot:
        day = self._today()
        with session_scope(self.factory) as session:
            row = session.execute(
                select(SpendDay.spent_usd, SpendDay.held_usd).where(SpendDay.day == day)
            ).first()
        spent, held = (float(row[0]), float(row[1])) if row else (0.0, 0.0)
        return SpendSnapshot(day=day, spent_usd=spent, held_usd=held, cap_usd=self.cap_usd)

    def spent_usd(self) -> float:
        return self.snapshot().spent_usd

    def cap_reached(self, headroom_usd: float = 0.0) -> bool:
        """True when not even `headroom_usd` (e.g. one cheap model call) fits today."""
        remaining = self.snapshot().remaining_usd
        return remaining is not None and remaining <= headroom_usd
