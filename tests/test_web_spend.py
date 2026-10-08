"""DbSpendGuard and the page quota on SQLite (Postgres versions: test_web_postgres.py)."""

from __future__ import annotations

import contextlib
import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from a11y_stem.core.budget import BudgetExceeded, JobBudget
from a11y_stem.web import db
from a11y_stem.web.quota import pages_left, pages_used, try_consume_pages
from a11y_stem.web.spend import DbSpendGuard

DAY = date(2026, 10, 7)


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/spend.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def make_user(factory) -> str:
    with db.session_scope(factory) as session:
        user = db.User(email="q@example.org", is_allowed=True)
        session.add(user)
        session.flush()
        return user.id


def day_row(factory, day: date) -> tuple[float, float]:
    with db.session_scope(factory) as session:
        row = session.get(db.SpendDay, day)
        return (row.spent_usd, row.held_usd) if row else (0.0, 0.0)


def test_reserve_holds_and_settle_replaces_the_hold_with_actual_cost(factory):
    guard = DbSpendGuard(factory, 1.0)
    first = guard.reserve(0.6)
    with pytest.raises(BudgetExceeded) as exc:
        guard.reserve(0.5)
    assert exc.value.scope == "daily"
    guard.settle(first, 0.1)  # cheaper than the estimate: the hold is released
    guard.reserve(0.8)
    assert guard.spent_usd() == pytest.approx(0.1)
    snapshot = guard.snapshot()
    assert snapshot.held_usd == pytest.approx(0.8)
    assert snapshot.remaining_usd == pytest.approx(0.1)


def test_a_reservation_that_exactly_fills_the_cap_is_allowed(factory):
    guard = DbSpendGuard(factory, 0.3)
    for _ in range(3):
        guard.reserve(0.1)  # 0.1 + 0.1 + 0.1 is 0.30000000000000004 in floats
    with pytest.raises(BudgetExceeded):
        guard.reserve(0.01)


def test_negative_reservations_are_refused(factory):
    with pytest.raises(ValueError):
        DbSpendGuard(factory, 1.0).reserve(-1)


def test_without_a_cap_spend_is_recorded_but_never_refused(factory):
    guard = DbSpendGuard(factory, None)
    guard.settle(guard.reserve(1000.0), 999.0)
    assert guard.spent_usd() == pytest.approx(999.0)
    assert guard.cap_reached() is False


def test_cap_reached_leaves_room_for_one_more_call(factory):
    guard = DbSpendGuard(factory, 1.0)
    guard.settle(guard.reserve(0.999), 0.999)
    assert guard.cap_reached(headroom_usd=0.0) is False
    assert guard.cap_reached(headroom_usd=0.01) is True


def test_a_call_that_straddles_midnight_releases_the_hold_on_its_own_day(factory):
    now = [datetime(2026, 10, 7, 23, 59, 59, tzinfo=UTC)]
    guard = DbSpendGuard(factory, 1.0, clock=lambda: now[0])
    reservation = guard.reserve(0.5)
    now[0] += timedelta(seconds=2)  # 00:00:01 the next day
    guard.settle(reservation, 0.2)
    assert day_row(factory, date(2026, 10, 7)) == pytest.approx((0.2, 0.0))
    assert day_row(factory, date(2026, 10, 8)) == (0.0, 0.0)
    assert guard.spent_usd() == 0.0  # a new day starts empty


def test_job_budget_on_the_db_guard(factory):
    guard = DbSpendGuard(factory, 1.0)
    budget = JobBudget(guard=guard, max_usd=0.5, max_calls=10)
    budget.settle(budget.reserve(0.4), 0.3)
    with pytest.raises(BudgetExceeded) as exc:
        budget.reserve(0.3)
    assert exc.value.scope == "job_usd"
    assert guard.spent_usd() == pytest.approx(0.3)


def test_threads_never_overshoot_the_cap_on_sqlite(factory):
    guard = DbSpendGuard(factory, 1.0)
    granted: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(20):
            with contextlib.suppress(BudgetExceeded):
                guard.reserve(0.01)
                with lock:
                    granted.append(0.01)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 100  # exactly the cap
    assert guard.snapshot().held_usd == pytest.approx(1.0)


# --------------------------------------------------------------------------- quota


def test_quota_counts_pages_and_refuses_without_changing_anything(factory):
    user_id = make_user(factory)
    with db.session_scope(factory) as session:
        assert try_consume_pages(session, user_id, 8, quota=10, day=DAY)
        assert not try_consume_pages(session, user_id, 3, quota=10, day=DAY)
        assert try_consume_pages(session, user_id, 2, quota=10, day=DAY)
        assert pages_used(session, user_id, DAY) == 10
        assert pages_left(session, user_id, quota=10, day=DAY) == 0
        assert pages_left(session, user_id, quota=None, day=DAY) is None
        assert pages_used(session, user_id, DAY + timedelta(days=1)) == 0


def test_quota_is_rolled_back_with_the_transaction(factory):
    user_id = make_user(factory)
    with contextlib.suppress(RuntimeError), db.session_scope(factory) as session:
        try_consume_pages(session, user_id, 5, quota=10, day=DAY)
        raise RuntimeError("job insert failed")
    with db.session_scope(factory) as session:
        assert pages_used(session, user_id, DAY) == 0


def test_no_quota_still_counts(factory):
    user_id = make_user(factory)
    with db.session_scope(factory) as session:
        assert try_consume_pages(session, user_id, 500, quota=None, day=DAY)
        assert session.scalar(select(db.QuotaDay.pages)) == 500
