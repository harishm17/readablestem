"""Deleting an account is not a reset (`web.tombstones`).

A person who deletes their account and signs up again the same UTC day comes back with the
day's pages and model reads already used, and without taking another of the day's places for
new accounts. Without that, one person could loop and drain the open-tier spend pool and the
day's sign-ups. The record is keyed by a hash of the address, scoped to its day, and purged
after it.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fixtures.web.support import (
    ADMIN,
    BOB,
    login,
    make_pdf,
    make_settings,
    upload,
)
from readablestem.ops.retention import erase_user, purge_expired, purge_tombstones
from readablestem.web import tombstones
from readablestem.web.database import utc_today
from readablestem.web.db import DeletedAccount, QuotaDay, User, session_scope, utcnow
from readablestem.web.quota import try_consume_reread
from test_web_open_signup import (
    CAROL,
    DAVE,
    client_for,
    delete_account,
    make_env,
    signups_now,
    user_row,
)


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path, open_tier_daily_page_quota=5, open_tier_daily_rereads=4)
    with e.running() as client:
        e.browser = client
        yield e


def used(env, email: str) -> tuple[int, int]:
    row = user_row(env, email)
    assert row is not None
    with session_scope(env.ctx.factory) as session:
        found = session.execute(
            select(QuotaDay.pages, QuotaDay.rereads).where(
                QuotaDay.user_id == row.id, QuotaDay.day == utc_today()
            )
        ).first()
    return (int(found[0]), int(found[1])) if found else (0, 0)


def records(env) -> list[DeletedAccount]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(DeletedAccount)))


def test_the_days_pages_and_reads_come_back_with_the_address(env):
    carol = client_for(env, CAROL)
    assert upload(carol, make_pdf(3)).status_code == 303
    row = user_row(env, CAROL)
    assert row is not None
    with session_scope(env.ctx.factory) as session:
        assert try_consume_reread(session, row.id, allowance=4, day=utc_today())
        assert try_consume_reread(session, row.id, allowance=4, day=utc_today())
    assert used(env, CAROL) == (3, 2)

    assert delete_account(carol).status_code == 303
    assert user_row(env, CAROL) is None
    again = client_for(env, CAROL)
    assert used(env, CAROL) == (3, 2)  # deleting did not reset the day
    # Two pages are left of the five, not five.
    refused = upload(again, make_pdf(3), filename="more.pdf")
    assert refused.status_code == 429
    assert "you have 2 of the 5 pages a new account can convert today left" in " ".join(
        refused.text.split()
    )
    assert upload(again, make_pdf(2)).status_code == 303
    # And somebody else with a fresh address is not affected.
    assert used(env, CAROL) == (5, 2)
    other = client_for(env, DAVE)
    assert upload(other, make_pdf(5)).status_code == 303


def test_a_loop_of_deleting_and_signing_up_never_gets_more_than_one_days_quota(env):
    for _ in range(4):
        client = client_for(env, CAROL)
        upload(client, make_pdf(1))  # refused once the five pages are used up
        delete_account(client)
    client = client_for(env, CAROL)
    assert used(env, CAROL)[0] == 4  # one page each time, never more than five in all
    assert upload(client, make_pdf(1)).status_code == 303
    assert upload(client, make_pdf(1)).status_code == 429


def test_signing_up_again_takes_no_new_place_from_the_days_count(tmp_path):
    env = make_env(tmp_path, max_new_accounts_per_day=2)
    with env.running():
        for _ in range(5):
            delete_account(client_for(env, CAROL))
        assert signups_now(env) == 1  # one place, used once, however many times she came back
        assert login(env.client(), DAVE).status_code == 303  # the second place is still there
        assert login(env.client(), "late@example.org").status_code == 503
        assert signups_now(env) == 2
        # Coming back is still possible for the address that already has its place today.
        delete_account(client_for(env, CAROL))
        assert login(env.client(), CAROL).status_code == 303
        assert signups_now(env) == 2


def test_an_account_from_an_earlier_day_takes_a_place_when_it_comes_back(tmp_path):
    env = make_env(tmp_path, max_new_accounts_per_day=3)
    with env.running():
        client = client_for(env, CAROL)
        with session_scope(env.ctx.factory) as session:
            row = session.scalar(select(User).where(User.email == CAROL))
            assert row is not None
            row.created_at = utcnow() - timedelta(days=3)  # made long ago
        before = signups_now(env)
        delete_account(client)
        assert signups_now(env) == before  # deleting counts nothing
        login(env.client(), CAROL)
        assert signups_now(env) == before + 1  # she is new today: one place
        delete_account(client_for(env, CAROL))
        login(env.client(), CAROL)
        assert signups_now(env) == before + 1  # and only the one


def test_the_record_holds_a_hash_of_the_address_and_numbers_never_the_address(env):
    client = client_for(env, CAROL)
    upload(client, make_pdf(2))
    delete_account(client)
    (record,) = records(env)
    key = tombstones.tombstone_key(env.settings)
    assert record.email_hash == tombstones.email_hash(key, CAROL)
    assert len(record.email_hash) == 64 and CAROL not in record.email_hash
    assert (record.day, record.pages, record.rereads, record.slot_taken) == (
        utc_today(),
        2,
        0,
        True,
    )
    # Another address, another secret or another spelling: another hash.
    assert tombstones.email_hash(key, DAVE) != record.email_hash
    assert tombstones.email_hash(key + b"x", CAROL) != record.email_hash
    assert tombstones.email_hash(key, " Carol@Example.ORG ") == record.email_hash


def test_the_key_comes_from_the_session_secret():
    a = tombstones.tombstone_key(make_settings(Path("."), session_secret="s" * 40))
    b = tombstones.tombstone_key(make_settings(Path("."), session_secret="t" * 40))
    assert a != b and tombstones.tombstone_key(
        make_settings(Path("."))
    )  # development: a fixed key, so tests and a local server work


def test_nothing_is_written_for_an_account_that_used_nothing_and_took_no_place_today(env):
    client = client_for(env, CAROL)
    with session_scope(env.ctx.factory) as session:
        row = session.scalar(select(User).where(User.email == CAROL))
        assert row is not None
        row.created_at = utcnow() - timedelta(days=2)
    delete_account(client)
    assert records(env) == []


def test_two_deletions_in_a_day_keep_the_larger_numbers(env):
    client = client_for(env, CAROL)
    upload(client, make_pdf(3))
    delete_account(client)
    client = client_for(env, CAROL)  # comes back with 3 used
    upload(client, make_pdf(1))
    delete_account(client)
    (record,) = records(env)
    assert record.pages == 4 and record.slot_taken is True
    assert used_after_return(env) == 4


def used_after_return(env) -> int:
    client_for(env, CAROL)
    return used(env, CAROL)[0]


def test_old_records_are_purged_and_todays_are_kept(env):
    with session_scope(env.ctx.factory) as session:
        today = utc_today()
        for n, day in enumerate((today - timedelta(days=2), today - timedelta(days=1), today)):
            session.add(DeletedAccount(email_hash=f"{n:064d}", day=day, pages=1, rereads=0))
    with session_scope(env.ctx.factory) as session:
        assert purge_tombstones(session, utcnow()) == 2
    assert [r.day for r in records(env)] == [utc_today()]
    stats = purge_expired(env.ctx.factory, utcnow() + timedelta(days=1))
    assert stats.tombstones_deleted == 1 and records(env) == []


def test_the_command_line_deletion_leaves_the_same_record(tmp_path, monkeypatch, capsys):
    from readablestem.cli.main import main
    from readablestem.web import db

    settings = make_settings(tmp_path, env="dev", signup_mode="open")
    monkeypatch.setattr("readablestem.config.get_settings", lambda: settings)
    engine = db.make_engine(settings.database_url)
    factory = db.make_sessionmaker(engine)
    assert main(["users", "allow", BOB]) == 0  # makes the tables and the account
    assert main(["users", "delete", BOB, "--yes"]) == 0
    with db.session_scope(factory) as session:
        (record,) = session.scalars(select(DeletedAccount)).all()
        assert record.email_hash == tombstones.email_hash(tombstones.tombstone_key(settings), BOB)
        assert record.slot_taken is True  # the row was made today
    engine.dispose()
    capsys.readouterr()


def test_erase_without_a_key_writes_nothing(env):
    client = client_for(env, CAROL)
    upload(client, make_pdf(1))
    row = user_row(env, CAROL)
    assert row is not None
    with session_scope(env.ctx.factory) as session:
        erase_user(session, row.id)
    assert records(env) == []


def test_an_invited_person_who_deletes_comes_back_as_an_open_account_with_the_days_use(env):
    env.allow(BOB)
    client = client_for(env, BOB)
    upload(client, make_pdf(4))
    delete_account(client)
    assert login(env.client(), BOB).status_code == 303  # open mode: a new account
    row = user_row(env, BOB)
    assert row is not None and row.access_tier == "open"
    assert used(env, BOB)[0] == 4 and ADMIN != BOB
