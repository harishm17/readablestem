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
from sqlalchemy import event, select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import (
    ADMIN,
    ALICE,
    BOB,
    WebEnv,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)
from readablestem.core.budget import BudgetExceeded, JobBudget
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


def test_the_pool_is_inside_the_daily_cap_and_never_extra_money(factory):
    guard = DbSpendGuard(factory, 0.5, open_cap_usd=4.0)  # a pool larger than the day
    guard.reserve(0.3, pool=OPEN)
    with pytest.raises(BudgetExceeded) as exc:
        guard.reserve(0.3, pool=OPEN)
    assert not isinstance(exc.value, PoolExhausted)  # the day's cap is what stopped it
    guard.reserve(0.2)
    snapshot = guard.snapshot()
    assert snapshot.held_usd == pytest.approx(0.5) and snapshot.open_held_usd == pytest.approx(0.3)


def test_open_spend_counts_toward_the_daily_cap_for_everyone_else(factory):
    guard = DbSpendGuard(factory, 1.0, open_cap_usd=0.6)
    guard.settle(guard.reserve(0.6, pool=OPEN), 0.6)
    with pytest.raises(BudgetExceeded):
        guard.reserve(0.5)  # 0.6 spent by strangers leaves the others 0.4
    guard.reserve(0.4)
    snapshot = guard.snapshot()
    assert snapshot.remaining_usd == pytest.approx(0.0)
    assert snapshot.open_remaining_usd == pytest.approx(0.0)


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


def test_a_pooled_view_reserves_in_the_pool_and_settles_through_any_view(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=1.0)
    assert guard.pooled(None) is guard
    view = guard.pooled(OPEN)
    held = JobBudget(guard=view, max_usd=1.0, max_calls=5).reserve(0.3)
    assert day_row(factory, utc_today()) == pytest.approx((0.0, 0.3, 0.0, 0.3))
    guard.settle(held, 0.1)  # settled through the plain guard: the pool is still released
    assert day_row(factory, utc_today()) == pytest.approx((0.1, 0.0, 0.1, 0.0))
    assert view.spent_usd() == pytest.approx(0.1)
    assert view.snapshot().open_spent_usd == pytest.approx(0.1)


def test_a_view_knows_when_its_pool_is_closed_and_the_day_is_not(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=0.5)
    guard.settle(guard.reserve(0.45, pool=OPEN), 0.45)
    assert guard.cap_reached(0.1, pool=OPEN) is True
    assert guard.cap_reached(0.1) is False
    assert guard.pooled(OPEN).cap_reached(0.1) is True  # type: ignore[attr-defined]


def test_without_an_open_cap_the_pool_is_recorded_but_never_refuses(factory):
    guard = DbSpendGuard(factory, 5.0)
    guard.settle(guard.reserve(2.0, pool=OPEN), 1.5)
    assert day_row(factory, utc_today()) == pytest.approx((1.5, 0.0, 1.5, 0.0))
    assert guard.cap_reached(0.0, pool=OPEN) is False


def test_a_pool_of_zero_closes_it_and_leaves_the_rest_alone(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=0.0)
    with pytest.raises(PoolExhausted):
        guard.reserve(0.01, pool=OPEN)
    guard.reserve(0.01)
    assert guard.cap_reached(0.0, pool=OPEN) is True


def test_an_unknown_pool_is_refused(factory):
    with pytest.raises(ValueError):
        DbSpendGuard(factory, 1.0).reserve(0.1, pool="vip")


def test_a_pool_without_a_daily_cap_still_limits_the_pool(factory):
    guard = DbSpendGuard(factory, None, open_cap_usd=0.2)
    guard.reserve(0.2, pool=OPEN)
    with pytest.raises(PoolExhausted):
        guard.reserve(0.1, pool=OPEN)
    guard.reserve(50.0)  # the instructors have no cap in development


def test_a_pool_reservation_that_exactly_fills_the_pool_is_allowed(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=0.3)
    for _ in range(3):
        guard.reserve(0.1, pool=OPEN)  # 0.1 + 0.1 + 0.1 is 0.30000000000000004 in floats
    with pytest.raises(PoolExhausted):
        guard.reserve(0.01, pool=OPEN)


def test_a_call_that_straddles_midnight_releases_the_pool_on_its_own_day(factory):
    now = [datetime(2026, 10, 7, 23, 59, 59, tzinfo=UTC)]
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=1.0, clock=lambda: now[0])
    reservation = guard.reserve(0.5, pool=OPEN)
    now[0] += timedelta(seconds=2)
    guard.settle(reservation, 0.2)
    assert day_row(factory, date(2026, 10, 7)) == pytest.approx((0.2, 0.0, 0.2, 0.0))
    assert day_row(factory, date(2026, 10, 8)) == (0.0, 0.0, 0.0, 0.0)
    assert guard.snapshot().open_spent_usd == 0.0  # a new day starts with an empty pool


# --------------------------------------------------------------------------- the first-row fix is kept


def test_the_statements_of_a_pool_reservation_are_those_of_any_reservation(factory):
    """The day's row is made by the first reservation of the day, found by the UPDATE that
    refused, and after that a call is one UPDATE to hold and one to settle, with or without
    the pool (the sequence tests/test_web_spend.py pins for the plain guard)."""
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=2.0)
    engine = factory.kw["bind"]
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement.strip().split(None, 1)[0].upper())

    event.listen(engine, "before_cursor_execute", record)
    try:
        first = guard.reserve(0.1, pool=OPEN)
        assert statements == ["UPDATE", "INSERT", "UPDATE"]
        statements.clear()
        second = guard.reserve(0.1, pool=OPEN)
        guard.settle(first, 0.05)
        guard.settle(second, 0.02)
        assert statements == ["UPDATE", "UPDATE", "UPDATE"]
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert day_row(factory, utc_today()) == pytest.approx((0.07, 0.0, 0.07, 0.0))


def test_a_refusal_by_the_pool_leaves_no_row_and_changes_nothing(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=0.1)
    with pytest.raises(PoolExhausted):
        guard.reserve(0.5, pool=OPEN)
    with session_scope(factory) as session:
        assert session.scalars(select(SpendDay)).all() == []
    held = guard.reserve(0.05, pool=OPEN)
    with pytest.raises(PoolExhausted):
        guard.reserve(0.06, pool=OPEN)
    assert day_row(factory, utc_today()) == pytest.approx((0.0, 0.05, 0.0, 0.05))
    guard.settle(held, 0.05)


def test_a_pool_reservation_is_not_refused_because_another_process_made_the_row(factory):
    """The regression the first-row fix closed: a per-process "the row exists" belief, or a
    row made by another transaction, must never turn a hold there was room for into a
    refusal. A second guard on the same database, with empty memory, finds the row."""
    first = DbSpendGuard(factory, 1.0, open_cap_usd=0.5)
    second = DbSpendGuard(factory, 1.0, open_cap_usd=0.5)
    a = first.reserve(0.2, pool=OPEN)
    b = second.reserve(0.2, pool=OPEN)  # its own memory is empty and the row exists
    first.settle(a, 0.2)
    second.settle(b, 0.2)
    with pytest.raises(PoolExhausted):
        second.reserve(0.2, pool=OPEN)
    assert first.snapshot().open_spent_usd == pytest.approx(0.4)


def test_the_first_holds_of_a_day_racing_to_make_its_row_are_all_granted(factory):
    """20 threads start with no row for the day: the pool has room for every one of them."""
    guard = DbSpendGuard(factory, 100.0, open_cap_usd=100.0)
    granted: list[object] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def go() -> None:
        barrier.wait()
        reservation = guard.reserve(0.01, pool=OPEN)
        with lock:
            granted.append(reservation)

    threads = [threading.Thread(target=go) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 20
    assert guard.snapshot().open_held_usd == pytest.approx(0.2)


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


def test_settling_races_keep_the_pool_and_the_totals_exact(factory):
    guard = DbSpendGuard(factory, 5.0, open_cap_usd=5.0)
    holds = [guard.reserve(0.01, pool=OPEN if n % 2 else None) for n in range(100)]

    def settle(chunk) -> None:
        for reservation in chunk:
            guard.settle(reservation, 0.005)

    threads = [threading.Thread(target=settle, args=(holds[i::10],)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spent, held, open_spent, open_held = day_row(factory, utc_today())
    assert spent == pytest.approx(0.5) and held == pytest.approx(0.0, abs=1e-9)
    assert open_spent == pytest.approx(0.25) and open_held == pytest.approx(0.0, abs=1e-9)


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


def test_when_the_whole_day_is_spent_everyone_gets_the_ordinary_message(tmp_path):
    env = make_env(tmp_path)
    with env.running() as client:
        spend_pool(env, spent=0.0, daily_spent=5.0)
        login(client, ALICE)
        refused = upload(client, make_pdf(1))
        assert refused.status_code == 503
        assert "Today&#39;s budget for conversions has been used up" in refused.text
        assert "for new accounts" not in refused.text
        assert "Conversions are paused for today." in " ".join(client.get("/app").text.split())


def test_a_waiting_document_of_a_new_account_says_it_waits_for_the_budget(tmp_path):
    env = make_env(tmp_path)
    with env.running() as client:
        login(client, ALICE)
        job_id = job_id_from(upload(client, make_pdf(1)))
        spend_pool(env, spent=1.0)
        page = " ".join(client.get(f"/jobs/{job_id}").text.split())
        assert "budget" in page.lower()


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


def test_a_new_accounts_job_stops_for_the_day_when_the_pool_runs_out_mid_conversion(tmp_path):
    env = make_env(tmp_path, open_tier_daily_spend_cap_usd=0.15)
    with env.running() as client:
        login(client, ALICE)
        job_id = job_id_from(upload(client, make_pdf(1)))
        worker = env.worker()

        def convert(pdf, *, settings, client, **kwargs):
            client.budget.reserve(0.1)  # held, never settled
            client.budget.reserve(0.1)  # the pool has 0.05 left

        worker.converter = convert
        worker.client_factory = lambda settings, budget: FakeVisionClient(budget=budget)
        worker.run_once()
        job = env.job(job_id)
        assert job.status == "failed" and job.error_code == "budget_daily"


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


def test_a_review_read_for_an_invited_person_is_not_held_back_by_the_pool(tmp_path):
    env = make_env(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.browser = client
        env.allow(ALICE)
        login(client, ALICE)
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        env.ctx.client_factory = lambda settings, budget: FakeVisionClient(
            budget=budget,
            defaults={"equation": {"is_equation": True, "latex": "x^2", "confidence": 0.99}},
        )
        spend_pool(env, spent=1.0)
        assert mark_region(env).status_code == 303
        assert env.ctx.guard.snapshot().open_spent_usd == pytest.approx(1.0)  # none added


def mark_region(env):
    box = {"left": "10", "top": "55", "right": "50", "bottom": "70"}
    path = f"/jobs/{env.job_id}/review"
    version = str(env.job(env.job_id).result_version or 0)
    data = {"csrf_token": token(env.browser, path), "version": version, "kind": "equation"}
    return env.browser.post(f"/jobs/{env.job_id}/pages/1/add", data=data | box)


def test_the_admin_page_and_the_public_status_keep_working_with_the_pool_spent(tmp_path):
    env = make_env(tmp_path)
    with env.running() as client:
        spend_pool(env, spent=1.0)
        login(client, ADMIN)
        assert client.get("/admin").status_code == 200
        assert client.get("/status").status_code == 200
