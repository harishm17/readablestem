"""What the worker's loop asks of the database when nothing is going on, and what it asks
before it claims a job (T3-28): one read for an idle poll, no write."""

from __future__ import annotations

from datetime import timedelta

from readablestem.web.db import utcnow
from test_web_worker import env, queue_job  # noqa: F401  (the fixture and the helper)


class Statements:
    """The SQL statements an engine runs while it is attached."""

    def __init__(self, engine) -> None:
        from sqlalchemy import event

        self.engine = engine
        self.sql: list[str] = []
        self._listener = self._record
        event.listen(engine, "before_cursor_execute", self._listener)

    def _record(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.sql.append(statement.strip().split(None, 1)[0].upper())

    def stop(self) -> list[str]:
        from sqlalchemy import event

        event.remove(self.engine, "before_cursor_execute", self._listener)
        return self.sql


def test_an_idle_poll_is_one_read_and_writes_nothing(env):
    worker = env.worker()
    seen = Statements(env.ctx.factory.kw["bind"])
    assert worker.run_once() is None
    assert seen.stop() == ["SELECT"]


def test_a_poll_with_a_queued_job_claims_it_and_an_expired_lease_is_still_requeued(env):
    job_id = queue_job(env)
    worker = env.worker(lease_s=60)
    claim = worker.claim()
    assert claim is not None and claim.job_id == job_id
    later = utcnow() + timedelta(minutes=5)
    assert worker.pending(later) == (False, True)  # running, its lease run out
    assert worker.requeue_expired(later) == (1, 0)
    assert worker.pending() == (True, False)


def test_a_closed_daily_budget_is_not_looked_at_while_nothing_waits(env):
    class Guard:
        looked = 0

        def cap_reached(self, headroom):
            self.looked += 1
            return True

    guard = Guard()
    worker = env.worker(guard=guard)  # ty: ignore[invalid-argument-type]
    assert worker.run_once() is None
    assert guard.looked == 0
    queue_job(env)
    assert worker.run_once() is None  # a job waits and the day is spent: it stays queued
    assert guard.looked == 1
