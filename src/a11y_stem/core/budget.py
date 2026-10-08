"""Spend control: every model call reserves its worst-case cost before it is sent.

Three layers, all enforced before a request leaves the machine:

1. `SpendGuard` - the global daily cap. The web app uses a Postgres-backed guard (one row
   per UTC day, conditional UPDATE, so concurrent workers cannot overshoot). CLI runs use
   `InMemorySpendGuard` with an explicit `--max-usd`.
2. `JobBudget` - per-document caps on dollars and number of calls.
3. The provider's own monthly limit (set in the OpenAI dashboard; documented in
   docs/deploy.md). Only that one survives a bug in this code.

Flow for one call: `reservation = budget.reserve(estimate)` -> call the model ->
`budget.settle(reservation, actual_cost)`. If the call raises, `settle(reservation, 0)`
releases the hold (the provider may still bill a failed call; the estimate is an upper
bound, so settling at the real usage when it is known is preferred).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Protocol


class BudgetExceeded(RuntimeError):
    """A cap would be exceeded. `scope` is "daily", "job_usd" or "job_calls"."""

    def __init__(self, scope: str, message: str) -> None:
        super().__init__(message)
        self.scope = scope


@dataclass(frozen=True)
class Reservation:
    id: str
    amount_usd: float


class SpendGuard(Protocol):
    def reserve(self, amount_usd: float) -> Reservation:
        """Hold `amount_usd` against the cap or raise BudgetExceeded."""
        ...

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        """Replace the hold with the actual cost (0 releases it)."""
        ...

    def spent_usd(self) -> float:
        """Settled spend in the current period (excludes open holds)."""
        ...


@dataclass
class InMemorySpendGuard:
    """Process-local guard for CLI runs and tests. Thread-safe."""

    cap_usd: float
    _spent: float = 0.0
    _held: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, amount_usd: float) -> Reservation:
        if amount_usd < 0:
            raise ValueError("reservation must be >= 0")
        with self._lock:
            committed = self._spent + sum(self._held.values())
            if committed + amount_usd > self.cap_usd + 1e-12:
                raise BudgetExceeded(
                    "daily",
                    f"spend cap ${self.cap_usd:.2f} reached "
                    f"(spent ${self._spent:.4f}, held ${sum(self._held.values()):.4f})",
                )
            res = Reservation(uuid.uuid4().hex, amount_usd)
            self._held[res.id] = amount_usd
            return res

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        with self._lock:
            self._held.pop(reservation.id, None)
            self._spent += max(0.0, actual_usd)

    def spent_usd(self) -> float:
        with self._lock:
            return self._spent


@dataclass
class JobBudget:
    """Per-job caps layered on top of a global `SpendGuard`."""

    guard: SpendGuard
    max_usd: float
    max_calls: int
    spent_usd: float = 0.0
    calls: int = 0
    _held: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, amount_usd: float) -> Reservation:
        with self._lock:
            if self.calls + 1 > self.max_calls:
                raise BudgetExceeded("job_calls", f"job call cap {self.max_calls} reached")
            if self.spent_usd + self._held + amount_usd > self.max_usd + 1e-12:
                raise BudgetExceeded("job_usd", f"job spend cap ${self.max_usd:.2f} reached")
            reservation = self.guard.reserve(amount_usd)  # may raise "daily"
            self.calls += 1
            self._held += amount_usd
            return reservation

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        with self._lock:
            self._held = max(0.0, self._held - reservation.amount_usd)
            self.spent_usd += max(0.0, actual_usd)
        self.guard.settle(reservation, actual_usd)
