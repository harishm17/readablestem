"""Spend holds do not outlive the process that made them (`spend_holds`, `web.spend`).

Every hold is a row written in the transaction that makes it and deleted by the settlement
that releases it. A worker that dies leaves rows; the next worker gives them back, by job
when a lease runs out and by age at start-up. Before this, a held amount stayed on the day's
cap (and the open pool) until midnight, so a few crashes could close the day on money nobody
had spent.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.core.budget import BudgetExceeded
from readablestem.models.fake import FakeVisionClient
from readablestem.web import db
from readablestem.web.database import utc_today
from readablestem.web.db import Job, JobStatus, SpendDay, SpendHold, session_scope, utcnow
from readablestem.web.spend import DbSpendGuard

OPEN = "open"


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/holds.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def holds(factory) -> list[SpendHold]:
    with session_scope(factory) as session:
        return list(session.scalars(select(SpendHold).order_by(SpendHold.created_at)))


def books(factory, day: date | None = None) -> tuple[float, float, float, float]:
    with session_scope(factory) as session:
        row = session.get(SpendDay, day or utc_today())
        if row is None:
            return (0.0, 0.0, 0.0, 0.0)
        return (row.spent_usd, row.held_usd, row.open_spent_usd, row.open_held_usd)


def test_every_hold_is_a_row_and_its_settlement_deletes_it(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=2.0)
    plain = guard.reserve(0.3)
    pooled = guard.scoped(OPEN, "j" * 32).reserve(0.2)
    rows = {h.id: h for h in holds(factory)}
    assert set(rows) == {plain.id, pooled.id}
    assert (rows[plain.id].amount_usd, rows[plain.id].in_pool, rows[plain.id].job_id) == (
        0.3,
        False,
        None,
    )
    assert (rows[pooled.id].amount_usd, rows[pooled.id].in_pool, rows[pooled.id].job_id) == (
        0.2,
        True,
        "j" * 32,
    )
    guard.settle(plain, 0.1)
    guard.settle(pooled, 0.05)
    assert holds(factory) == []
    assert books(factory) == pytest.approx((0.15, 0.0, 0.05, 0.0))


def test_a_refused_hold_leaves_no_record(factory):
    guard = DbSpendGuard(factory, 0.1, open_cap_usd=0.05)
    with pytest.raises(BudgetExceeded):
        guard.reserve(0.5)
    with pytest.raises(BudgetExceeded):
        guard.reserve(0.08, pool=OPEN)
    assert holds(factory) == [] and books(factory) == (0.0, 0.0, 0.0, 0.0)


def test_after_a_crash_a_new_guard_gives_back_the_holds_of_a_job(factory):
    dead = DbSpendGuard(factory, 1.0, open_cap_usd=0.5)
    view = dead.scoped(OPEN, "a" * 32)
    for _ in range(3):
        view.reserve(0.1)
    dead.scoped(None, "b" * 32).reserve(0.2)
    assert books(factory) == pytest.approx((0.0, 0.5, 0.0, 0.3))
    # The process is gone: a new one starts with nothing in memory.
    fresh = DbSpendGuard(factory, 1.0, open_cap_usd=0.5)
    assert fresh.release_holds(job_ids=["a" * 32]) == (3, pytest.approx(0.3))
    assert books(factory) == pytest.approx((0.0, 0.2, 0.0, 0.0))
    assert [h.job_id for h in holds(factory)] == ["b" * 32]
    assert fresh.release_holds(job_ids=["a" * 32]) == (0, 0.0)  # once
    assert fresh.release_holds(job_ids=[]) == (0, 0.0)
    # The room is really back: the pool takes what the dead attempt had held.
    for _ in range(5):
        fresh.reserve(0.1, pool=OPEN)
    with pytest.raises(BudgetExceeded):
        fresh.reserve(0.1, pool=OPEN)


def test_holds_older_than_a_job_can_run_are_given_back_at_start_up(factory):
    now = [datetime.now(UTC) - timedelta(hours=3)]
    dead = DbSpendGuard(factory, 1.0, open_cap_usd=0.5, clock=lambda: now[0])
    dead.reserve(0.3, pool=OPEN)
    now[0] = datetime.now(UTC) - timedelta(minutes=1)
    dead.reserve(0.2)  # a minute old: could be a live call of another instance
    fresh = DbSpendGuard(factory, 1.0, open_cap_usd=0.5)
    assert fresh.release_holds(older_than_s=3600) == (1, pytest.approx(0.3))
    assert books(factory) == pytest.approx((0.0, 0.2, 0.0, 0.0))
    with pytest.raises(ValueError):
        fresh.release_holds()


def test_a_holds_late_settlement_counts_its_spend_and_releases_nothing_twice(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=2.0)
    first = guard.scoped(OPEN, "a" * 32).reserve(0.3)
    guard.release_holds(job_ids=["a" * 32])  # the lease ran out; the hold was given back
    second = guard.reserve(0.2)
    guard.settle(first, 0.1)  # the abandoned conversion finishes: the call was paid for
    assert books(factory) == pytest.approx((0.1, 0.2, 0.1, 0.0))  # 0.2 still held, not 0
    guard.settle(first, 0.1)  # settled twice by mistake: spend counts, nothing is released
    assert books(factory)[1] == pytest.approx(0.2)
    guard.settle(second, 0.0)
    assert books(factory)[1] == pytest.approx(0.0)


def test_a_settlement_and_a_release_racing_for_the_same_holds_release_each_once(factory):
    guard = DbSpendGuard(factory, 100.0, open_cap_usd=100.0)
    reservations = [guard.scoped(OPEN, "a" * 32).reserve(0.1) for _ in range(40)]
    barrier = threading.Barrier(5)

    def settle(chunk) -> None:
        barrier.wait()
        for reservation in chunk:
            guard.settle(reservation, 0.05)

    def release() -> None:
        barrier.wait()
        for _ in range(20):
            guard.release_holds(job_ids=["a" * 32])

    threads = [threading.Thread(target=settle, args=(reservations[i::4],)) for i in range(4)]
    threads.append(threading.Thread(target=release))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spent, held, open_spent, open_held = books(factory)
    assert held == pytest.approx(0.0, abs=1e-9) and open_held == pytest.approx(0.0, abs=1e-9)
    assert spent == pytest.approx(0.05 * 40) and open_spent == pytest.approx(0.05 * 40)
    assert holds(factory) == []


# --------------------------------------------------------------------------- the worker


@pytest.fixture
def env(tmp_path):
    e = WebEnv(tmp_path, signup_mode="open", open_tier_daily_spend_cap_usd=2.0)
    with e.running() as client:
        e.browser = client
        yield e


def queue_job(env, email: str = BOB) -> str:
    env.allow(email)
    client = env.client()
    login(client, email)
    return job_id_from(upload(client, make_pdf(1)))


def age_holds(env, *, hours: float) -> None:
    """Make every hold so far `hours` old, on the same day's books."""
    with session_scope(env.ctx.factory) as session:
        session.execute(update(SpendHold).values(created_at=utcnow() - timedelta(hours=hours)))


def test_starting_the_worker_gives_back_holds_a_dead_process_left(env):
    old = DbSpendGuard(env.ctx.factory, 5.0, open_cap_usd=2.0)
    old.reserve(0.4, pool=OPEN)
    old.reserve(0.3)
    age_holds(env, hours=5)  # what a process that died hours ago left behind
    live = env.ctx.guard.reserve(0.2)  # made just now, perhaps by another instance
    assert books(env.ctx.factory) == pytest.approx((0.0, 0.9, 0.0, 0.4))
    worker = env.worker()
    worker.start()
    worker.stop(timeout=5)
    assert books(env.ctx.factory) == pytest.approx((0.0, 0.2, 0.0, 0.0))
    assert [h.id for h in holds(env.ctx.factory)] == [live.id]


def test_a_lease_that_runs_out_gives_back_what_the_attempt_held(env):
    job_id, other = queue_job(env), queue_job(env, ALICE)
    worker = env.worker()
    claim = worker.claim()
    assert claim is not None
    held = env.ctx.guard.scoped(None, claim.job_id).reserve(0.4)  # the dead attempt's call
    elsewhere = env.ctx.guard.scoped(None, other).reserve(0.1)  # another job's, still queued
    with session_scope(env.ctx.factory) as session:
        session.execute(
            update(Job)
            .where(Job.id == claim.job_id)
            .values(lease_until=utcnow() - timedelta(seconds=5))
        )
    assert worker.requeue_expired() == (1, 0)
    assert [h.id for h in holds(env.ctx.factory)] == [elsewhere.id]
    assert books(env.ctx.factory)[1] == pytest.approx(0.1)
    assert env.job(claim.job_id).status == JobStatus.QUEUED and held.id != elsewhere.id
    assert job_id != other


def test_a_job_that_fails_its_last_attempt_gives_back_its_holds_too(env):
    from readablestem.web.worker import MAX_ATTEMPTS

    queue_job(env)
    worker = env.worker()
    claim = worker.claim()
    assert claim is not None
    env.ctx.guard.scoped(None, claim.job_id).reserve(0.4)
    with session_scope(env.ctx.factory) as session:
        session.execute(
            update(Job)
            .where(Job.id == claim.job_id)
            .values(lease_until=utcnow() - timedelta(seconds=5), attempts=MAX_ATTEMPTS)
        )
    assert worker.requeue_expired() == (0, 1)
    assert holds(env.ctx.factory) == [] and books(env.ctx.factory)[1] == pytest.approx(0.0)


def test_a_conversions_holds_are_recorded_against_its_job_and_leftovers_are_released(env):
    job_id = queue_job(env)
    worker = env.worker()
    seen: dict[str, object] = {}

    def convert(pdf, *, settings, client, **kwargs):
        client.budget.reserve(0.1)  # a call that never settles: the thread died, say
        settled = client.budget.reserve(0.1)
        seen["during"] = [(h.job_id, h.in_pool) for h in holds(env.ctx.factory)]
        client.budget.settle(settled, 0.05)
        return env.converter.output.model_copy(deep=True)

    worker.converter = convert
    worker.client_factory = lambda settings, budget: FakeVisionClient(budget=budget)
    assert worker.run_once() == job_id
    assert seen["during"] == [(job_id, False), (job_id, False)]  # BOB is invited: no pool
    assert holds(env.ctx.factory) == []  # the unsettled one went back with the job
    spent, held, _, _ = books(env.ctx.factory)
    assert held == pytest.approx(0.0) and spent == pytest.approx(0.05)


def test_the_holds_of_a_new_accounts_job_are_in_the_pool_and_recorded_against_it(env):
    client = env.client()
    login(client, "new@example.org")  # open sign-up: an open-tier account
    job_id = job_id_from(upload(client, make_pdf(1)))
    worker = env.worker()
    seen: dict[str, object] = {}

    def convert(pdf, *, settings, client, **kwargs):
        reservation = client.budget.reserve(0.1)
        seen["during"] = [(h.job_id, h.in_pool) for h in holds(env.ctx.factory)]
        client.budget.settle(reservation, 0.02)
        return env.converter.output.model_copy(deep=True)

    worker.converter = convert
    worker.client_factory = lambda settings, budget: FakeVisionClient(budget=budget)
    assert worker.run_once() == job_id
    assert seen["during"] == [(job_id, True)]
    assert books(env.ctx.factory) == pytest.approx((0.02, 0.0, 0.02, 0.0))


def test_the_hourly_backstop_also_gives_back_old_holds(env):
    DbSpendGuard(env.ctx.factory, 5.0).reserve(0.5)
    age_holds(env, hours=9)
    worker = env.worker()
    worker._next_purge = 0.0  # due
    worker.purge_backstop()
    assert holds(env.ctx.factory) == [] and books(env.ctx.factory)[1] == pytest.approx(0.0)
