"""Sign-in identities on Postgres: first sign-ins of one identity racing each other (two
tabs, a double click) end with one link and everyone signed in as the same person.

Uses the Postgres fixtures of test_web_postgres.py (TEST_POSTGRES_URL or pgserver).
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select

from readablestem.web import db
from readablestem.web.identities import (
    GOOGLE,
    GOOGLE_ISSUER,
    MICROSOFT,
    Identity,
    microsoft_sign_in,
    remember,
)
from test_web_postgres import factory as factory  # fixture, re-exported for pytest
from test_web_postgres import pg_url as pg_url  # fixture, re-exported for pytest
from test_web_postgres import server_url as server_url  # fixture, re-exported for pytest

pytestmark = pytest.mark.postgres

TENANT = "11111111-1111-4111-8111-111111111111"
THREADS = 20


def allowed_user(factory, email: str) -> str:
    with db.session_scope(factory) as session:
        user = db.User(email=email, is_allowed=True)
        session.add(user)
        session.flush()
        return user.id


def identity_rows(factory) -> int:
    with db.session_scope(factory) as session:
        return session.scalar(select(func.count()).select_from(db.AuthIdentity)) or 0


def at_once(work) -> list:
    """Run `work()` in THREADS threads released together; returns results or errors."""
    barrier = threading.Barrier(THREADS)
    results: list = []
    lock = threading.Lock()

    def run() -> None:
        barrier.wait()
        try:
            outcome = work()
        except Exception as exc:  # collected and asserted on below
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=run) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def test_racing_first_microsoft_sign_ins_link_once(factory):
    alice = allowed_user(factory, "alice@example.org")
    identity = Identity(
        MICROSOFT, f"https://login.microsoftonline.com/{TENANT}/v2.0", "sub-alice", TENANT
    )
    results = at_once(lambda: microsoft_sign_in(factory, identity, "alice@example.org", None))
    assert len(results) == THREADS
    assert all(getattr(r, "user_id", None) == alice for r in results), results
    assert identity_rows(factory) == 1


def test_racing_google_sign_ins_record_the_identity_once(factory):
    alice = allowed_user(factory, "alice@example.org")
    identity = Identity(GOOGLE, GOOGLE_ISSUER, "google-sub-alice")
    results = at_once(lambda: remember(factory, alice, identity))
    assert results == [None] * THREADS
    assert identity_rows(factory) == 1
