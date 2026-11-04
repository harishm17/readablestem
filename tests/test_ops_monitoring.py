"""Monitoring and recovery additions: heartbeats and /healthz/ops, the erasure ledger and its
replay, per-user daily spend, the sign-up cap warning, and the larger connection pool."""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from sqlalchemy import select

from fixtures.web.support import ADMIN, WebEnv, login
from readablestem.cli.main import main
from readablestem.config import get_settings
from readablestem.core.budget import BudgetExceeded
from readablestem.observability.logging import FIELDS_ATTR
from readablestem.ops import heartbeat
from readablestem.ops.retention import (
    erase_user,
    import_ledger,
    record_erasure,
    replay_erasures,
    user_hash,
)
from readablestem.web import db
from readablestem.web.database import utc_today
from readablestem.web.db import (
    ErasureLedger,
    OpsHeartbeat,
    QuotaDay,
    SignupDay,
    SpendDay,
    User,
    session_scope,
)
from readablestem.web.quota import try_take_signup
from readablestem.web.spend import DbSpendGuard

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/monitoring.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def add_user(factory, email: str = "p@example.org") -> str:
    with session_scope(factory) as session:
        user = User(email=email, is_allowed=True)
        session.add(user)
        session.flush()
        return user.id


# --------------------------------------------------------------------------- heartbeats


def test_nothing_has_run_yet_so_both_tasks_are_stale(factory):
    with session_scope(factory) as session:
        assert heartbeat.stale_names(session, NOW) == ["backup", "cron"]


def test_the_limits_are_26_hours_for_the_backup_and_150_minutes_for_the_cron(factory):
    with session_scope(factory) as session:
        heartbeat.beat(session, heartbeat.BACKUP, "tables=3", NOW - timedelta(hours=25, minutes=50))
        heartbeat.beat(session, heartbeat.CRON, None, NOW - timedelta(hours=1, minutes=50))
    with session_scope(factory) as session:
        assert heartbeat.stale_names(session, NOW) == []
        assert heartbeat.stale_names(session, NOW + timedelta(minutes=50)) == ["backup", "cron"]


def test_a_beat_replaces_the_row_instead_of_adding_one(factory):
    with session_scope(factory) as session:
        heartbeat.beat(session, heartbeat.CRON, "first", NOW - timedelta(hours=5))
        heartbeat.beat(session, heartbeat.CRON, "second", NOW)
    with session_scope(factory) as session:
        rows = list(session.scalars(select(OpsHeartbeat)))
    assert [(r.name, r.detail) for r in rows] == [("cron", "second")]


def test_each_task_goes_stale_on_its_own(factory):
    with session_scope(factory) as session:
        heartbeat.beat(session, heartbeat.BACKUP, None, NOW - timedelta(hours=20))
        heartbeat.beat(session, heartbeat.CRON, None, NOW - timedelta(hours=3))
    with session_scope(factory) as session:
        assert heartbeat.stale_names(session, NOW) == ["cron"]
        assert heartbeat.stale_names(session, NOW + timedelta(hours=7)) == ["backup", "cron"]


def test_healthz_ops_is_503_listing_only_names_until_both_have_reported(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as web:
        response = web.get("/healthz/ops")
        assert response.status_code == 503
        assert response.json() | {"storage": None, "sandbox": None} == {
            "status": "stale",
            "stale": ["backup", "cron"],
            "storage": None,
            "sandbox": None,
        }
        with session_scope(env.ctx.factory) as session:
            heartbeat.beat(session, heartbeat.CRON, "aggregated=0 jobs_deleted=0")
        body = web.get("/healthz/ops").json()
        assert (body["status"], body["stale"]) == ("stale", ["backup"])
        with session_scope(env.ctx.factory) as session:
            heartbeat.beat(session, heartbeat.BACKUP, "tables=30 alembic=abc")
        response = web.get("/healthz/ops")
    assert response.status_code == 200
    body = response.json()
    assert body.pop("sandbox")["state"] == "not_run"  # the start-up probe is off in tests
    assert body == {
        "status": "ok",
        "stale": [],
        "storage": {"stored_mb": 0, "limit_mb": 2000, "full": False},
    }
    assert "tables" not in response.text  # the notes stay in the database


def test_healthz_ops_is_503_and_names_the_database_when_it_cannot_be_read(tmp_path, monkeypatch):
    env = WebEnv(tmp_path)

    def broken(*_args, **_kwargs):
        raise RuntimeError("postgresql://user:secret@host/db")

    monkeypatch.setattr(heartbeat, "stale_names", broken)
    with env.running() as web:
        response = web.get("/healthz/ops")
    assert response.status_code == 503
    assert response.json()["stale"] == ["database"]
    assert "secret" not in response.text


def test_healthz_ops_needs_no_sign_in(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as web:
        assert web.get("/healthz/ops", follow_redirects=False).status_code in {200, 503}


# --------------------------------------------------------------------------- cron CLI


@pytest.fixture
def cli_database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = db.make_engine(url)
    db.create_all(engine)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    get_settings.cache_clear()
    yield db.make_sessionmaker(engine)
    get_settings.cache_clear()
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()
    engine.dispose()


def test_a_successful_cron_run_records_its_heartbeat(cli_database, capsys):
    assert main(["ops", "cron"]) == 0
    with session_scope(cli_database) as session:
        rows = {r.name: r for r in session.scalars(select(OpsHeartbeat))}
    assert set(rows) == {"cron"} and rows["cron"].detail == "aggregated=0 jobs_deleted=0"
    assert "aggregated_jobs=0" in capsys.readouterr().out


def test_a_cron_run_whose_aggregation_failed_records_no_heartbeat(
    cli_database, capsys, monkeypatch
):
    from readablestem.ops import aggregate

    def broken(*_args, **_kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(aggregate, "job_counts", broken)
    with session_scope(cli_database) as session:
        session.add(
            db.Job(
                user_id=add_user(cli_database),
                filename="a.pdf",
                status="done",
                finished_at=NOW,
                retain_until=NOW + timedelta(days=1),
            )
        )
    assert main(["ops", "cron"]) == 1
    with session_scope(cli_database) as session:
        assert list(session.scalars(select(OpsHeartbeat))) == []


# --------------------------------------------------------------------------- erasure ledger


def test_erasing_a_user_writes_a_content_free_ledger_row(factory):
    user_id = add_user(factory)
    with session_scope(factory) as session:
        erase_user(session, user_id, NOW)
    with session_scope(factory) as session:
        rows = list(session.scalars(select(ErasureLedger)))
        assert session.get(User, user_id) is None
    assert [r.user_hash for r in rows] == [user_hash(user_id)]
    assert len(rows[0].user_hash) == 64 and user_id not in rows[0].user_hash
    assert rows[0].erased_at.replace(tzinfo=UTC) == NOW


def test_a_second_erasure_keeps_the_first_time(factory):
    with session_scope(factory) as session:
        record_erasure(session, "a" * 32, NOW - timedelta(days=3))
        record_erasure(session, "a" * 32, NOW)
    with session_scope(factory) as session:
        [row] = session.scalars(select(ErasureLedger))
    assert row.erased_at.replace(tzinfo=UTC) == NOW - timedelta(days=3)


def test_replay_erases_a_resurrected_account_and_only_that_one(factory):
    gone = add_user(factory, "gone@example.org")
    stays = add_user(factory, "stays@example.org")
    with session_scope(factory) as session:
        session.add(QuotaDay(user_id=gone, day=utc_today(), pages=2))
        # The ledger as a restored backup has it: it names `gone`, which the backup also has.
        record_erasure(session, gone, NOW)
    with session_scope(factory) as session:
        assert replay_erasures(session, NOW) == 1
    with session_scope(factory) as session:
        assert session.get(User, gone) is None
        assert session.get(User, stays) is not None
        assert list(session.scalars(select(QuotaDay).where(QuotaDay.user_id == gone))) == []
        assert replay_erasures(session, NOW) == 0  # nothing left to do: safe to run twice


def test_the_ledger_exported_before_a_restore_is_merged_then_replayed(
    factory, tmp_path, capsys, monkeypatch
):
    resurrected = add_user(factory, "back@example.org")
    export = tmp_path / "ledger.csv"
    with export.open("w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["user_hash", "erased_at"])
        out.writerow([user_hash(resurrected), "2026-11-01 08:00:00.5+00"])
        out.writerow([user_hash("f" * 32), "2026-11-01 09:00:00+00"])  # nobody here
    with session_scope(factory) as session:
        assert import_ledger(session, [(user_hash(resurrected), NOW)]) == 1
        assert import_ledger(session, [(user_hash(resurrected), NOW)]) == 0
        session.execute(ErasureLedger.__table__.delete())
    # Through the command, against the same database.
    monkeypatch.setenv("DATABASE_URL", str(factory.kw["bind"].url))
    get_settings.cache_clear()
    try:
        assert main(["ops", "replay-erasures", "--ledger-csv", str(export)]) == 0
    finally:
        get_settings.cache_clear()
    assert capsys.readouterr().out.strip() == "ledger_imported=2 accounts_erased=1"
    with session_scope(factory) as session:
        assert session.get(User, resurrected) is None
        assert len(list(session.scalars(select(ErasureLedger)))) == 2


def test_replay_refuses_a_ledger_file_that_is_not_hashes(tmp_path, capsys, monkeypatch):
    bad = tmp_path / "bad.csv"
    bad.write_text("alice@example.org,2026-11-01 08:00:00+00\n")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/x.db")
    get_settings.cache_clear()
    try:
        assert main(["ops", "replay-erasures", "--ledger-csv", str(bad)]) == 2
    finally:
        get_settings.cache_clear()
    assert "alice" not in capsys.readouterr().err


# --------------------------------------------------------------------------- per-user spend


def spent(factory, user_id: str) -> float:
    with session_scope(factory) as session:
        row = session.get(QuotaDay, (user_id, utc_today()))
        return row.spent_usd if row else 0.0


def test_a_settlement_adds_the_cost_to_the_owners_day(factory):
    alice, bob = add_user(factory, "a@example.org"), add_user(factory, "b@example.org")
    guard = DbSpendGuard(factory, 5.0)
    first = guard.scoped(None, "j" * 32, alice).reserve(0.5)
    guard.settle(first, 0.2)
    guard.settle(guard.scoped("open", "k" * 32, alice).reserve(0.5), 0.1)
    guard.settle(guard.scoped(None, "l" * 32, bob).reserve(0.3), 0.3)
    guard.settle(guard.reserve(0.4), 0.4)  # no owner: only the global total
    assert spent(factory, alice) == pytest.approx(0.3)
    assert spent(factory, bob) == pytest.approx(0.3)
    with session_scope(factory) as session:
        assert session.get(SpendDay, utc_today()).spent_usd == pytest.approx(1.0)  # type: ignore[union-attr]


def test_a_refused_reservation_costs_the_owner_nothing(factory):
    alice = add_user(factory)
    guard = DbSpendGuard(factory, 0.1)
    with pytest.raises(BudgetExceeded):
        guard.scoped(None, "j" * 32, alice).reserve(0.5)
    assert spent(factory, alice) == 0.0


def test_the_spend_shares_a_row_with_the_pages_and_survives_a_late_settlement(factory):
    alice = add_user(factory)
    with session_scope(factory) as session:
        session.add(QuotaDay(user_id=alice, day=utc_today(), pages=7))
    guard = DbSpendGuard(factory, 5.0)
    held = guard.scoped(None, "j" * 32, alice).reserve(0.5)
    guard.release_holds(job_ids=["j" * 32])  # the lease ran out: the hold is given back
    guard.settle(held, 0.25)  # the abandoned call finishes late; its spend still counts
    with session_scope(factory) as session:
        row = session.get(QuotaDay, (alice, utc_today()))
        assert row is not None and row.pages == 7 and row.spent_usd == pytest.approx(0.25)


def test_a_person_erased_while_their_call_ran_does_not_break_the_settlement(tmp_path):
    # Foreign keys are on in SQLite here, as in Postgres.
    engine = db.make_engine(f"sqlite:///{tmp_path}/fk.db")
    db.create_all(engine)
    factory = db.make_sessionmaker(engine)
    alice = add_user(factory)
    guard = DbSpendGuard(factory, 5.0)
    held = guard.scoped(None, "j" * 32, alice).reserve(0.5)
    with session_scope(factory) as session:
        erase_user(session, alice, NOW)
    guard.settle(held, 0.2)  # must not raise
    assert spent(factory, alice) == 0.0
    with session_scope(factory) as session:
        assert session.get(SpendDay, utc_today()).spent_usd == pytest.approx(0.2)  # type: ignore[union-attr]
    engine.dispose()


def test_the_admin_page_lists_spend_by_pseudonym_never_by_email(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as web:
        login(web, ADMIN)
        with session_scope(env.ctx.factory) as session:
            admin = session.scalar(select(User).where(User.email == ADMIN))
            assert admin is not None
            admin_id = admin.id
            session.add(QuotaDay(user_id=admin_id, day=utc_today(), pages=1, spent_usd=1.25))
        page = " ".join(web.get("/admin").text.split())
    assert "Spend by person" in page
    assert (
        f'<th scope="row">u-{admin_id[:8]}</th><td class="num">$1.25</td><td class="num">$1.25</td>'
        in page
    )
    assert f"u-{admin_id[:8]}" in page and ADMIN not in page.split("Spend by person")[1][:400]


# --------------------------------------------------------------------------- sign-up cap


def take(factory, limit: int) -> bool:
    with session_scope(factory) as session:
        return try_take_signup(session, limit=limit, day=utc_today())


def test_the_cap_warning_is_logged_once_per_day_with_a_code_only(factory, caplog):
    caplog.set_level(logging.WARNING)
    assert take(factory, 1) is True
    assert not [r for r in caplog.records if "signup_cap_reached" in r.getMessage()]
    assert take(factory, 1) is False
    assert take(factory, 1) is False
    assert take(factory, 1) is False
    warnings = [r for r in caplog.records if "signup_cap_reached" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    assert getattr(warnings[0], FIELDS_ATTR) == {"code": "daily_cap"}
    with session_scope(factory) as session:
        row = session.get(SignupDay, utc_today())
        assert row is not None and row.accounts == 1 and row.cap_warned is True


def test_the_admin_page_says_when_the_days_new_accounts_are_used_up(tmp_path):
    env = WebEnv(tmp_path, signup_mode="open", max_new_accounts_per_day=1)
    with env.running() as web:
        login(web, ADMIN)
        assert "limit on new accounts is reached" not in web.get("/admin").text
        with session_scope(env.ctx.factory) as session:
            assert try_take_signup(session, limit=1, day=utc_today())
        page = " ".join(web.get("/admin").text.split())
    assert "Today's limit on new accounts is reached (1 of 1)" in page


# --------------------------------------------------------------------------- pool


def test_the_postgres_pool_is_five_plus_five():
    engine = db.make_engine("postgresql+psycopg://u:p@localhost:5432/x")
    try:
        assert engine.pool.size() == 5  # type: ignore[attr-defined]
        assert engine.pool._max_overflow == 5  # type: ignore[attr-defined]
    finally:
        engine.dispose()


def test_the_migration_seeds_both_heartbeats_so_the_first_deploy_is_healthy(tmp_path):
    from sqlalchemy import create_engine, text

    from test_web_migrations import migrate

    url = f"sqlite:///{tmp_path}/seed.db"
    migrate(url, "head")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("select name, detail from ops_heartbeat order by name")).all()
    finally:
        engine.dispose()
    assert [tuple(r) for r in rows] == [
        ("backup", "seeded at migration"),
        ("cron", "seeded at migration"),
    ]
