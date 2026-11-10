"""The open-tier spend pool: `DbSpendGuard(..., open_cap_usd=...)` on SQLite (the Postgres
concurrency test is in test_web_postgres.py), and what the app does when the pool is spent.

The pool is a share of the day's cap kept in two more columns of the same row, changed by the
same single conditional UPDATE as the totals (web/spend.py). These tests follow
tests/test_web_spend.py, which keeps pinning the guard's behaviour without a pool.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import UTC, date, datetime, timedelta

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)
from readablestem.core.budget import BudgetExceeded
from readablestem.models.fake import FakeVisionClient
from readablestem.web import db, reread
from readablestem.web.database import utc_today
from readablestem.web.db import SpendDay, session_scope
from readablestem.web.spend import DbSpendGuard, PoolExhausted

OPEN = "open"


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/open-spend.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def day_row(factory, day: date) -> tuple[float, float, float, float]:
    """(spent, held, open spent, open held) of a day."""
    with session_scope(factory) as session:
        row = session.get(SpendDay, day)
        if row is None:
            return (0.0, 0.0, 0.0, 0.0)
        return (row.spent_usd, row.held_usd, row.open_spent_usd, row.open_held_usd)


# --------------------------------------------------------------------------- the guard


def test_an_open_reservation_must_fit_the_pool_and_the_daily_cap(factory):
    guard = DbSpendGuard(factory, 1.0, open_cap_usd=0.3)
    first = guard.reserve(0.2, pool=OPEN)
    with pytest.raises(PoolExhausted) as exc:
        guard.reserve(0.2, pool=OPEN)  # the day has room, the pool has not
    assert exc.value.scope == "daily" and exc.value.pool == OPEN
    assert isinstance(exc.value, BudgetExceeded)
    guard.reserve(0.7)  # invited work is not held back by the pool
    with pytest.raises(BudgetExceeded) as full:
        guard.reserve(0.2)  # now the day itself is full
    assert not isinstance(full.value, PoolExhausted)
    guard.settle(first, 0.05)
    assert day_row(factory, utc_today()) == pytest.approx((0.05, 0.7, 0.05, 0.0))


def test_settling_changes_the_pool_and_the_totals_together(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=1.0)
    stranger, instructor = guard.reserve(0.4, pool=OPEN), guard.reserve(0.3)
    assert day_row(factory, utc_today()) == pytest.approx((0.0, 0.7, 0.0, 0.4))
    guard.settle(stranger, 0.1)
    guard.settle(instructor, 0.2)
    assert day_row(factory, utc_today()) == pytest.approx((0.3, 0.0, 0.1, 0.0))
    snapshot = guard.snapshot()
    assert snapshot.open_spent_usd == pytest.approx(0.1)
    assert snapshot.open_remaining_usd == pytest.approx(0.9)
    assert snapshot.remaining_for(None) == pytest.approx(4.7)
    assert snapshot.remaining_for(OPEN) == pytest.approx(0.9)


def test_a_pool_of_zero_closes_it_and_leaves_the_rest_alone(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=0.0)
    with pytest.raises(PoolExhausted):
        guard.reserve(0.01, pool=OPEN)
    guard.reserve(0.01)
    assert guard.cap_reached(0.0, pool=OPEN) is True


def test_a_pool_without_a_daily_cap_still_limits_the_pool(factory):
    guard = DbSpendGuard(factory, None, open_cap_usd=0.2)
    guard.reserve(0.2, pool=OPEN)
    with pytest.raises(PoolExhausted):
        guard.reserve(0.1, pool=OPEN)
    guard.reserve(50.0)  # the instructors have no cap in development


def test_a_call_that_straddles_midnight_releases_the_pool_on_its_own_day(factory):
    now = [datetime(2026, 10, 7, 23, 59, 59, tzinfo=UTC)]
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=1.0, clock=lambda: now[0])
    reservation = guard.reserve(0.5, pool=OPEN)
    now[0] += timedelta(seconds=2)
    guard.settle(reservation, 0.2)
    assert day_row(factory, date(2026, 10, 7)) == pytest.approx((0.2, 0.0, 0.2, 0.0))
    assert day_row(factory, date(2026, 10, 8)) == (0.0, 0.0, 0.0, 0.0)
    assert guard.snapshot().open_spent_usd == 0.0  # a new day starts with an empty pool


# ----------------------------------------------------------------- the first-row fix is kept


# --------------------------------------------------------------------------- concurrency


def test_threads_never_overshoot_the_pool_or_the_daily_cap(factory):
    """Open and invited threads together: at most 50 cents for the pool, at most a dollar
    for the day, and the day fills up with whatever the pool did not take."""
    guard = DbSpendGuard(factory, 1.0, open_cap_usd=0.5)
    granted = {"open": 0, "invited": 0}
    lock = threading.Lock()

    def worker(kind: str) -> None:
        pool = OPEN if kind == "open" else None
        for _ in range(20):
            with contextlib.suppress(BudgetExceeded):
                guard.reserve(0.01, pool=pool)
                with lock:
                    granted[kind] += 1

    threads = [threading.Thread(target=worker, args=("open",)) for _ in range(8)]
    threads += [threading.Thread(target=worker, args=("invited",)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = granted["open"] + granted["invited"]
    assert granted["open"] <= 50 and total <= 100
    assert granted["open"] == 50 or total == 100  # one of the two limits is what stopped them
    assert total == 100  # 160 + 80 tries against room for 100: the day is full either way
    snapshot = guard.snapshot()
    assert snapshot.held_usd == pytest.approx(0.01 * total)
    assert snapshot.open_held_usd == pytest.approx(0.01 * granted["open"])
    assert snapshot.open_held_usd <= 0.5 + 1e-9


# --------------------------------------------------------------------------- the app


def make_env(tmp_path, **overrides) -> WebEnv:
    overrides.setdefault("signup_mode", "open")
    overrides.setdefault("open_tier_daily_spend_cap_usd", 1.0)
    return WebEnv(tmp_path, **overrides)


def spend_pool(env, *, spent: float = 0.0, daily_spent: float = 0.0) -> None:
    """Today's row with the pool (and, on its own, the day) used up as asked."""
    with session_scope(env.ctx.factory) as session:
        session.merge(
            SpendDay(
                day=utc_today(),
                spent_usd=max(spent, daily_spent),
                held_usd=0.0,
                open_spent_usd=spent,
                open_held_usd=0.0,
            )
        )


def test_the_apps_guard_has_the_open_pool_from_the_settings(tmp_path):
    env = make_env(tmp_path, open_tier_daily_spend_cap_usd=2.5)
    assert env.ctx.guard.open_cap_usd == 2.5 and env.ctx.guard.cap_usd == 5.0
    assert env.worker().guard.open_cap_usd == 2.5  # the worker's own default guard too


def test_when_the_pool_is_spent_a_new_account_is_told_and_an_invited_person_is_not(tmp_path):
    env = make_env(tmp_path)
    with env.running() as client:
        env.allow(BOB)
        spend_pool(env, spent=1.0)
        login(client, ALICE)  # signs up: an open-tier account
        refused = upload(client, make_pdf(1))
        assert refused.status_code == 503 and refused.headers["retry-after"]
        assert "Conversions for new accounts are paused for today" in refused.text
        assert "Accounts that were invited are not affected" in refused.text
        page = " ".join(client.get("/app").text.split())
        assert "Conversions for new accounts are paused for today." in page
        assert 'action="/jobs"' not in page

        invited = env.client()
        login(invited, BOB)
        assert upload(invited, make_pdf(1)).status_code == 303
        assert "paused" not in invited.get("/app").text


def test_the_worker_leaves_jobs_of_new_accounts_waiting_while_the_pool_is_spent(tmp_path):
    env = make_env(tmp_path)
    with env.running() as client:
        env.allow(BOB)
        login(client, ALICE)  # open tier, queues first
        opened = job_id_from(upload(client, make_pdf(1)))
        invited_client = env.client()
        login(invited_client, BOB)
        invited = job_id_from(upload(invited_client, make_pdf(1)))
        spend_pool(env, spent=1.0)
        worker = env.worker()
        assert worker.open_pool_closed() is True
        assert worker.run_once() == invited  # the older job is skipped, not the newer one
        assert worker.run_once() is None
        assert env.job(opened).status == "queued" and env.job(invited).status == "done"
        # The next day the pool is empty again and the job is taken.
        with session_scope(env.ctx.factory) as session:
            session.query(SpendDay).delete()
        assert worker.run_once() == opened and env.job(opened).status == "done"


def test_a_conversion_for_a_new_account_reserves_in_the_pool_and_an_invited_one_does_not(tmp_path):
    env = make_env(tmp_path)
    with env.running() as client:
        env.allow(BOB)
        login(client, ALICE)
        upload(client, make_pdf(1))
        invited_client = env.client()
        login(invited_client, BOB)
        upload(invited_client, make_pdf(1))
        worker = env.worker()
        seen: dict[str, float] = {}

        def convert(pdf, *, settings, client, **kwargs):
            reservation = client.budget.reserve(0.1)  # what every model call does first
            seen[str(len(seen))] = env.ctx.guard.snapshot().open_held_usd
            client.budget.settle(reservation, 0.05)
            return env.converter.output.model_copy(deep=True)

        worker.converter = convert
        worker.client_factory = lambda settings, budget: FakeVisionClient(budget=budget)
        assert worker.run_once() and worker.run_once()
        # The first job is ALICE's (queued first): 0.1 was held in the pool; BOB's held none.
        assert seen == {"0": pytest.approx(0.1), "1": pytest.approx(0.0)}
        snapshot = env.ctx.guard.snapshot()
        assert snapshot.open_spent_usd == pytest.approx(0.05)
        assert snapshot.spent_usd == pytest.approx(0.1)


def test_a_review_read_for_a_new_account_draws_on_the_pool(tmp_path):
    env = make_env(tmp_path, open_tier_daily_rereads=10)
    env.converter.output = review_output()
    with env.running() as client:
        env.browser = client
        login(client, ALICE)
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        env.ctx.client_factory = lambda settings, budget: FakeVisionClient(
            budget=budget,
            defaults={"equation": {"is_equation": True, "latex": "x^2", "confidence": 0.99}},
        )
        before = env.ctx.guard.snapshot().open_spent_usd
        marked = mark_region(env)
        assert marked.status_code == 303
        assert env.ctx.guard.snapshot().open_spent_usd > before

        spend_pool(env, spent=1.0)
        refused = mark_region(env)
        assert refused.status_code == 422
        text = " ".join(refused.text.split())
        assert reread.OPEN_POOL_REFUSAL in text.replace("&#39;", "'")


def mark_region(env):
    box = {"left": "10", "top": "55", "right": "50", "bottom": "70"}
    path = f"/jobs/{env.job_id}/review"
    version = str(env.job(env.job_id).result_version or 0)
    data = {"csrf_token": token(env.browser, path), "version": version, "kind": "equation"}
    return env.browser.post(f"/jobs/{env.job_id}/pages/1/add", data=data | box)
