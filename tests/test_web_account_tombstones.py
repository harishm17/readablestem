"""Deleting an account is not a reset (`web.tombstones`).

A person who deletes their account and signs up again the same UTC day comes back with the
day's pages and model reads already used, and without taking another of the day's places for
new accounts. Without that, one person could loop and drain the open-tier spend pool and the
day's sign-ups. The record is keyed by a hash of the address, scoped to its day, and purged
after it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from fixtures.web.support import (
    make_pdf,
    upload,
)
from readablestem.web import tombstones
from readablestem.web.database import utc_today
from readablestem.web.db import DeletedAccount, QuotaDay, session_scope
from readablestem.web.quota import try_consume_reread
from test_web_open_signup import (
    CAROL,
    DAVE,
    client_for,
    delete_account,
    make_env,
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
