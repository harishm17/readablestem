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


def test_a_refused_reservation_costs_the_owner_nothing(factory):
    alice = add_user(factory)
    guard = DbSpendGuard(factory, 0.1)
    with pytest.raises(BudgetExceeded):
        guard.scoped(None, "j" * 32, alice).reserve(0.5)
    assert spent(factory, alice) == 0.0


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


# --------------------------------------------------------------------------- pool
