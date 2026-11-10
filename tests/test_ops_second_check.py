"""Ops side of the second automated check: aggregate counts, the weekly report, and the
daily Decisions canary (comparison, the once-a-day claim, the CLI and the cron route)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import pytest
import structlog
from sqlalchemy import select

from readablestem.cli.main import main
from readablestem.config import Settings, get_settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard
from readablestem.core.types import Fix, Kind, Status, Verification
from readablestem.models.decisions_client import (
    Answer,
    DecisionRequest,
    DecisionResult,
)
from readablestem.models.verifier import EQUATION_VERSION, FIGURE_VERSION
from readablestem.ops import canary
from readablestem.ops.aggregate import fix_counts
from readablestem.web import db
from readablestem.web.db import ErrorAggregate, session_scope

DAY = date(2026, 10, 8)
NOW = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)


def settings(**kw) -> Settings:
    kw.setdefault("env", "dev")
    kw.setdefault("openai_api_key", "sk-test")
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/ops.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


# ----------------------------------------------------------------------- aggregates


def figure_fix(*records: Verification) -> Fix:
    return Fix(
        element_id="p1-e1",
        kind=Kind.FIGURE,
        status=Status.ACCEPTED,
        review="optional",
        verifications=list(records),
    )


def test_codes_from_a_stored_record_cannot_carry_text():
    record = Verification(check="Secret quokka sentence", stage="x y", outcome="a b")
    counts = fix_counts(figure_fix(record))
    assert ("figure", "verify:other:other:outcome:other") in counts
    assert all("quokka" not in c for _, c in counts)


# ---------------------------------------------------------------------- the canary


class Replay:
    """A Decider that answers each canary case from the committed expected file,
    optionally shifted, refused or failing; it records the requests it got."""

    def __init__(
        self,
        *,
        shift: dict[str, float] | None = None,
        fail: set[str] | None = None,
        refuse: set[str] | None = None,
        budget_after: int | None = None,
    ) -> None:
        self.expected = canary.load_expected()["cases"]
        self.by_sha = {
            case.id: canary.request_sha(case.request, "gpt-6-luna") for case in canary.load_cases()
        }
        self.shift, self.fail = shift or {}, fail or set()
        self.refuse, self.budget_after = refuse or set(), budget_after
        self.sent: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> DecisionResult:
        if self.budget_after is not None and len(self.sent) >= self.budget_after:
            raise BudgetExceeded("daily", "cap")
        self.sent.append(request)
        sha = canary.request_sha(request, "gpt-6-luna")
        case_id = next(k for k, v in self.by_sha.items() if v == sha)
        if case_id in self.fail:
            return DecisionResult("timeout", {}, "gpt-6-luna")
        answers: dict[str, Answer] = {}
        for name, value in self.expected[case_id]["answers"].items():
            if case_id in self.refuse or value == canary.REFUSED:
                answers[name] = Answer("refusal")
            elif list(value) == ["true"]:
                answers[name] = Answer("predicate", value["true"] + self.shift.get(case_id, 0))
            else:
                shifted = {k: v + self.shift.get(case_id, 0) for k, v in value.items()}
                answers[name] = Answer("choice", probabilities=shifted)
        outcome = "refused" if all(a.refused for a in answers.values()) else "ok"
        return DecisionResult(outcome, answers, "gpt-6-luna", 300, 0.00003, 150)  # type: ignore[arg-type]


def test_the_committed_expectations_match_the_current_requests():
    """Changing the question wording, the framing or a canary image changes the request
    hashes: re-record canary_expected.json (`ops decisions-canary --record`)."""
    expected = canary.load_expected()
    assert expected["tolerance"] == canary.TOLERANCE
    cases = canary.load_cases()
    assert set(expected["cases"]) == {c.id for c in cases}
    for case in cases:
        assert expected["cases"][case.id]["request_sha"] == canary.request_sha(
            case.request, expected["model"]
        ), case.id
    # The figure and equation cases ask exactly what the pipeline asks.
    versions = {c.request.version for c in cases}
    assert {FIGURE_VERSION, EQUATION_VERSION} <= versions


def test_a_refused_reservation_stops_the_run_and_fails_the_rest():
    result = canary.run_canary(Replay(budget_after=3), model="gpt-6-luna")
    assert result.status == "failed" and result.compared == 3
    assert {o for _, o in result.failed} == {"budget_exceeded"}


# --------------------------------------------------------------- once a day, cron


def canary_rows(factory) -> dict[tuple[date, str], int]:
    with session_scope(factory) as s:
        rows = s.execute(select(ErrorAggregate).where(ErrorAggregate.step == "canary")).scalars()
        return {(r.day, r.category): r.count for r in rows}


def test_the_cron_runs_the_canary_at_most_once_per_utc_day(factory):
    deciders: list[Replay] = []

    def make(settings, budget):
        assert budget.max_usd == canary.CANARY_MAX_USD
        assert budget.max_calls == 0  # Decisions requests hold dollars, not model calls
        deciders.append(Replay())
        return deciders[-1]

    guard = InMemorySpendGuard(cap_usd=1.0)
    run = canary.maybe_run_daily
    assert run(factory, settings(), guard, NOW, decider_factory=make) == "ok"
    assert run(factory, settings(), guard, NOW + timedelta(hours=1), decider_factory=make) == (
        "not_due"
    )
    tomorrow = NOW + timedelta(days=1)
    assert run(factory, settings(), guard, tomorrow, decider_factory=make) == "ok"
    assert len(deciders) == 2
    rows = canary_rows(factory)
    assert rows[(DAY, "decisions:run")] == 1 and rows[(DAY, "decisions:ok")] == 1
    assert rows[(tomorrow.date(), "decisions:run")] == 1


def test_the_cron_canary_fails_open_and_counts_drift(factory, caplog):
    guard = InMemorySpendGuard(cap_usd=1.0)

    def broken(settings, budget):
        raise RuntimeError("SECRET-ish failure text")

    with caplog.at_level(logging.WARNING):
        assert (
            canary.maybe_run_daily(factory, settings(), guard, NOW, decider_factory=broken)
            == "error"
        )
    assert "SECRET" not in caplog.text
    assert canary_rows(factory) == {(DAY, "decisions:run"): 1}  # claimed: not retried today

    def drifting(settings, budget):
        return Replay(shift={"text-power-true": -0.5})

    later = NOW + timedelta(days=1)
    status = canary.maybe_run_daily(factory, settings(), guard, later, decider_factory=drifting)
    assert status == "drift" and canary_rows(factory)[(later.date(), "decisions:drift")] == 1


# ------------------------------------------------------------------------- the CLI


@pytest.fixture
def cli_state(monkeypatch):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    yield monkeypatch
    get_settings.cache_clear()
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()


def use_replay(monkeypatch, replay: Replay) -> None:
    from readablestem.models import decisions_client

    monkeypatch.setattr(decisions_client, "DecisionsClient", lambda settings, budget: replay)


def test_cli_exit_codes(cli_state, capsys):
    use_replay(cli_state, Replay())
    assert main(["ops", "decisions-canary", "--max-usd", "0.01"]) == 0
    assert "status=ok" in capsys.readouterr().out
    use_replay(cli_state, Replay(shift={"figure-bars-correct": -0.1}))
    assert main(["ops", "decisions-canary", "--max-usd", "0.01"]) == 1
    out = capsys.readouterr().out
    assert "drift case=figure-bars-correct question=ok delta=0.1" in out
    use_replay(cli_state, Replay(fail={"text-root-true"}))
    assert main(["ops", "decisions-canary", "--max-usd", "0.01"]) == 2
