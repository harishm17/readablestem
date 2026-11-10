"""The daily canary of the Claude second check (T2-20): known cases, tolerated misses,
fail-open, once a day, the counts and the CLI."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models.verifier import FIGURE_CHECK, FakeVerifier
from readablestem.ops import claude_canary
from readablestem.ops.report import render_report
from readablestem.web import db
from readablestem.web.db import ErrorAggregate, session_scope

DAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
GOOD_FIGURE = {"ok": 1.0, "factual_error": 0.0}
BAD_FIGURE = {"ok": 1.0, "factual_error": 1.0}
GOOD_EQUATION = {"match": 1.0, "cosmetic": 0.0, "meaning_changing": 0.0}
BAD_EQUATION = {"match": 0.0, "cosmetic": 0.0, "meaning_changing": 1.0}


def settings(**kw) -> Settings:
    kw.setdefault("env", "dev")
    kw.setdefault("model_provider", "anthropic")
    kw.setdefault("anthropic_api_key", "sk-ant-test")
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/claude-canary.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def perfect(wrong: set[str] | None = None, **kw) -> FakeVerifier:
    """A check that answers every case right, except the ids in `wrong` (flipped)."""
    by_text: dict[str, str] = {}
    for case in claude_canary.load_cases():
        by_text[case.alt or case.latex] = case.id
    flip = wrong or set()

    def respond(check: str, text: str):
        case_id = by_text[text]
        flag = claude_canary.SHOULD_FLAG[case_id] != (case_id in flip)
        if check == FIGURE_CHECK:
            return BAD_FIGURE if flag else GOOD_FIGURE
        return BAD_EQUATION if flag else GOOD_EQUATION

    return FakeVerifier(responder=respond, reported_model="claude-sonnet-5-5", **kw)


def test_a_refused_reservation_stops_the_run():
    budget = JobBudget(InMemorySpendGuard(cap_usd=0.0), max_usd=0.0, max_calls=0)
    result = claude_canary.run_canary(perfect(budget=budget), settings())
    assert result.status == "failed"
    assert {r for _, r in result.failed} == {"budget_exceeded"} and len(result.failed) == 12


def rows(factory) -> dict[tuple[date, str], int]:
    with session_scope(factory) as s:
        found = s.execute(select(ErrorAggregate).where(ErrorAggregate.step == "canary")).scalars()
        return {(r.day, r.category): r.count for r in found}


def test_the_cron_runs_it_once_a_day_and_counts_the_result(factory):
    made: list[FakeVerifier] = []

    def make(s, budget):
        assert budget.max_usd == claude_canary.CANARY_MAX_USD and budget.max_calls == 0
        made.append(perfect(budget=budget))
        return made[-1]

    guard = InMemorySpendGuard(cap_usd=1.0)
    first = claude_canary.maybe_run_daily(factory, settings(), guard, NOW, verifier_factory=make)
    again = claude_canary.maybe_run_daily(factory, settings(), guard, NOW, verifier_factory=make)
    assert (first, again) == ("ok", "not_due") and len(made) == 1
    assert rows(factory) == {(DAY, "claude:run"): 1, (DAY, "claude:ok"): 1}
    assert guard.spent_usd() > 0  # paid for through the caller's guard
    # The Decisions canary's own rows are separate.
    assert not any(c.startswith("decisions:") for _, c in rows(factory))
    text = render_report(
        {("canary", c): n for (_, c), n in rows(factory).items()}, since=DAY, until=DAY
    )
    assert "claude ok" in text and "claude run" in text


def test_a_canary_problem_never_fails_the_cron(factory, caplog):
    def broken(s, budget):
        raise RuntimeError("secret-detail")

    with caplog.at_level(logging.WARNING):
        status = claude_canary.maybe_run_daily(
            factory, settings(), InMemorySpendGuard(cap_usd=1.0), NOW, verifier_factory=broken
        )
    assert status == "error" and "secret-detail" not in caplog.text
