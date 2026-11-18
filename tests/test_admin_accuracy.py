"""`readablestem ops accuracy` and `ops lookup`: what reviewers did with each release's output,
read through the one function (`events.outcome_cells`) with its small-count rule, and the one
account the operator looks up for support. Events are written by one builder."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
import structlog

from fixtures.ops.seed import SENTINEL, add_job
from readablestem.cli.main import main
from readablestem.config import get_settings
from readablestem.web import db, events


@pytest.fixture(autouse=True)
def isolated_process_state():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/accuracy.db"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = db.make_engine(url)
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def classed(monkeypatch):
    """Two sources with a class: "pipeline" raises doubt flags, "gate" is a rule."""
    monkeypatch.setattr(events, "SOURCES", (*events.SOURCES, "gate"))
    monkeypatch.setitem(events.SOURCE_CLASSES, "pipeline", events.DOUBT)
    monkeypatch.setitem(events.SOURCE_CLASSES, "gate", events.RULE)


class Builder:
    """Accounts and documents with outcome rows, spread evenly so no account dominates."""

    def __init__(self, factory, accounts: int, documents: int, pages: int, release="0.5.11"):
        self.factory = factory
        self.release = release
        with db.session_scope(factory) as session:
            users = [
                db.User(email=f"p{n}-{release}@example.org", is_allowed=True)
                for n in range(accounts)
            ]
            session.add_all(users)
            session.flush()
            self.users = [u.id for u in users]
        self.jobs = [f"{n + 1:032x}" for n in range(documents)]
        self.owner = {job: self.users[n % accounts] for n, job in enumerate(self.jobs)}
        self.rows: list[db.Event] = []
        for n, job in enumerate(self.jobs):
            share = pages // documents + (1 if n < pages % documents else 0)
            self.rows.append(
                events.make(self.owner[job], events.UPLOADED, job_ref=job, value=share)
            )
        self.at = 0

    def outcome(self, reason, outcome, source, count=1, *, release=None, kind="equation", at=None):
        for _ in range(abs(count)):
            job = self.jobs[(self.at if at is None else at) % len(self.jobs)]
            self.at += 1
            self.rows.append(
                events.make(
                    self.owner[job],
                    events.DECISION_OUTCOME,
                    job_ref=job,
                    value=1 if count > 0 else -1,
                    kind=kind,
                    reason=reason,
                    outcome=outcome,
                    origin="page",
                    source=source,
                    mode="single",
                    write_mode="single",
                    release=release or self.release,
                )
            )

    def save(self):
        with db.session_scope(self.factory) as session:
            session.add_all(self.rows)


def accuracy(capsys) -> str:
    assert main(["ops", "accuracy"]) == 0
    return capsys.readouterr().out


def test_each_release_prints_the_three_measures_with_the_small_count_rule(
    database, classed, capsys
):
    """6 accounts, 12 documents, 200 pages: 30 kept and 15 changed doubt flags are 2.0 false
    flags per true flag; 4 silent edits and 3 added by hand are 3.5 per 100 pages; 40 rule
    decisions (6 changed) are on their own line and leave the ratio alone; a decision made,
    undone and made again counts once; a flag whose source has no class is "not classified";
    one release under two profile hashes is two tables; an admin's rows change nothing."""
    b = Builder(database, accounts=6, documents=12, pages=200)
    b.outcome("below_accept", "kept", "pipeline", 30)
    b.outcome("unknown_command", "edited", "pipeline", 15)
    b.outcome("none", "edited", "browsing", 3)
    b.outcome("none", "added", "browsing", 3)
    b.outcome("table_not_rebuilt", "kept", "gate", 34)
    b.outcome("table_not_rebuilt", "edited", "gate", 6)
    b.outcome("below_accept", "kept", "browsing", 2)  # no class: in no ratio
    b.outcome("none", "edited", "browsing", 1, at=5)  # made ...
    b.outcome("none", "edited", "browsing", -1, at=5)  # ... undone ...
    b.outcome("none", "edited", "browsing", 1, at=5)  # ... and made again: 1 of the 4 edits
    other = Builder(database, accounts=6, documents=12, pages=200, release="0.5.11+aaaaaaaaaaaa")
    other.outcome("below_accept", "kept", "pipeline", 3)
    with db.session_scope(database) as session:
        admin = db.User(email="op@example.org", is_allowed=True, is_admin=True)
        session.add(admin)
        session.flush()
        job = "e" * 32
        for what in ("kept", "edited"):
            session.add(
                events.make(
                    admin.id, events.DECISION_OUTCOME, job_ref=job, value=50, kind="equation",
                    reason="below_accept", outcome=what, origin="page", source="pipeline",
                    mode="single", write_mode="single", release="0.5.11",
                )
            )  # fmt: skip
    b.save()
    other.save()
    out = accuracy(capsys)
    assert "release 0.5.11\n" in out
    assert "2.0 false flags per true flag (45 flags" in out
    assert "found by a reviewer with no flag: 3.5 per 100 pages, of which added by hand: 3" in out
    assert "required by a rule: 40, of which 6 changed" in out
    assert "not classified: 2 " in out
    assert "release 0.5.11+aaaaaaaaaaaa\n" in out  # its own table
    assert "@example.org" not in out and not any(job in out for job in b.jobs)


def test_a_cell_with_too_few_accounts_or_one_dominant_account_is_withheld(
    database, classed, capsys
):
    """4 accounts: "needs 1 more account", and nothing of its numbers is printed; with 5
    accounts of which one supplies 60% of the decisions it is withheld too."""
    four = Builder(database, accounts=4, documents=12, pages=100, release="0.5.10")
    four.outcome("below_accept", "kept", "pipeline", 20)
    four.outcome("unknown_command", "edited", "pipeline", 10)
    four.save()
    five = Builder(database, accounts=5, documents=10, pages=100, release="0.5.11")
    five.outcome("below_accept", "kept", "pipeline", 6)
    five.owner = dict.fromkeys(five.jobs, five.users[0])  # one account has every document
    five.outcome("below_accept", "kept", "pipeline", 12)
    five.save()
    out = accuracy(capsys)
    ten = out.split("release 0.5.10\n")[1]
    assert "too few to show (needs 1 more account)" in ten
    assert "false flags per true flag" not in ten.split("release 0.5.11")[0]
    eleven = out.split("release 0.5.11\n")[1]
    assert "too few to show" in eleven and "false flags per true flag" not in eleven


def test_the_support_lookup_shows_one_account_without_a_title_or_an_outcome_by_kind(
    database, capsys
):
    """The account's tier, state, today's use and spend, its documents as content-free
    facts, and the three review counts: never the file name, a kind or a reason."""
    now = datetime.now(UTC)
    with db.session_scope(database) as session:
        user = db.User(email="student@example.org", is_allowed=True, access_tier="open")
        session.add(user)
        session.flush()
        job = add_job(session, user, retain_until=now + timedelta(days=1), page_count=7)
        session.add(db.QuotaDay(user_id=user.id, day=now.date(), pages=7, rereads=1, spent_usd=0.5))
        session.add(events.make(user.id, events.DECISION_MADE, job_ref=job, code="single", value=2))
        session.add(
            events.make(
                user.id, events.DECISION_OUTCOME, job_ref=job, value=2, kind="equation",
                reason="below_accept", outcome="edited", origin="page", source="pipeline",
                mode="group", write_mode="single", release="0.5.11",
            )
        )  # fmt: skip
    assert main(["ops", "lookup", " Student@Example.org "]) == 0
    out = capsys.readouterr().out
    assert "allowed, tier open" in out
    assert "7 pages, 1 re-reads, $0.50 spent" in out
    assert "1 review writes, 2 decisions, 1 documents reviewed" in out
    assert job in out and "7 pages" in out
    for forbidden in (SENTINEL, "equation", "below_accept", "edited", "0.5.11"):
        assert forbidden not in out
    assert main(["ops", "lookup", "nobody@example.org"]) == 1
    assert "Nobody has an account under that address." in capsys.readouterr().out
