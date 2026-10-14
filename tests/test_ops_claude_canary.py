"""The daily canary of the Claude second check (T2-20): known cases, tolerated misses,
fail-open, once a day, the counts and the CLI."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from a11y_stem.cli.main import main
from a11y_stem.config import Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.models.verifier import EQUATION_CHECK, FIGURE_CHECK, FakeVerifier
from a11y_stem.observability.logging import FIELDS_ATTR
from a11y_stem.ops import canary, claude_canary
from a11y_stem.ops.report import render_report
from a11y_stem.web import db
from a11y_stem.web.db import ErrorAggregate, session_scope

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


def test_the_cases_are_the_synthetic_figure_and_equation_ones_with_a_known_truth():
    cases = claude_canary.load_cases()
    assert len(cases) == 12
    assert {c.check for c in cases} == {FIGURE_CHECK, EQUATION_CHECK}
    assert set(claude_canary.SHOULD_FLAG) == {c.id for c in cases}
    assert sum(claude_canary.SHOULD_FLAG.values()) == 7  # 5 right, 7 wrong
    # They are cases of the shared canary file, so there is one set of synthetic images.
    shared = {c.id for c in canary.load_cases()}
    assert {c.id for c in cases} <= shared


def test_a_check_that_gets_every_case_right_is_ok(caplog):
    with caplog.at_level(logging.INFO, logger="a11y_stem.ops.claude_canary"):
        result = claude_canary.run_canary(perfect(), settings())
        claude_canary.report(result, source="test")
    assert (result.status, result.answered, result.missed, result.failed) == ("ok", 12, [], [])
    assert [r.getMessage() for r in caplog.records] == ["claude_canary_ok"]


def test_one_miss_is_tolerated_two_are_drift(caplog):
    one = claude_canary.run_canary(perfect({"figure-bars-wrong-value"}), settings())
    assert one.status == "ok" and one.missed == ["figure-bars-wrong-value"]
    with caplog.at_level(logging.INFO, logger="a11y_stem.ops.claude_canary"):
        claude_canary.report(one, source="test")
    (line,) = caplog.records
    assert line.getMessage() == "claude_canary_incomplete" and line.levelno == logging.WARNING

    two = claude_canary.run_canary(
        perfect({"figure-bars-wrong-value", "equation-fma-match"}), settings()
    )
    assert two.status == "drift"
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="a11y_stem.ops.claude_canary"):
        claude_canary.report(two, source="test")
    (line,) = caplog.records
    assert line.getMessage() == "claude_canary_drift" and line.levelno == logging.ERROR
    assert "equation-fma-match" in getattr(line, FIELDS_ATTR)["missed_cases"]  # ids only


def test_the_tolerance_is_a_setting():
    strict = settings(claude_canary_max_misses=0)
    assert claude_canary.run_canary(perfect({"equation-fma-match"}), strict).status == "drift"


def test_a_case_without_an_answer_is_failed_not_missed():
    result = claude_canary.run_canary(perfect(outcomes={FIGURE_CHECK: "timeout"}), settings())
    assert result.status == "failed" and result.missed == []
    assert {r for _, r in result.failed} == {"timeout"} and len(result.failed) == 6


def test_a_refused_reservation_stops_the_run():
    budget = JobBudget(InMemorySpendGuard(cap_usd=0.0), max_usd=0.0, max_calls=0)
    result = claude_canary.run_canary(perfect(budget=budget), settings())
    assert result.status == "failed"
    assert {r for _, r in result.failed} == {"budget_exceeded"} and len(result.failed) == 12


def test_a_slow_run_stops_at_its_deadline():
    ticks = iter(range(1000))
    result = claude_canary.run_canary(
        perfect(), settings(), deadline_s=4.5, clock=lambda: float(next(ticks))
    )
    assert result.answered == 4 and {r for _, r in result.failed} == {"deadline"}


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"claude_canary": False}, "disabled"),
        ({"env": "test"}, "test_env"),
        ({"fake_models": True}, "fake_models"),
        ({"model_provider": "openai", "openai_api_key": "sk-o"}, "provider_not_anthropic"),
        ({"claude_second_check": "off"}, "check_off"),
        ({"anthropic_api_key": None}, "no_key"),
    ],
)
def test_when_the_canary_is_not_due(kw, reason):
    assert claude_canary.due(settings(**kw)) == reason
    assert claude_canary.due(settings()) is None


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


def test_not_due_claims_nothing(factory):
    status = claude_canary.maybe_run_daily(
        factory, settings(claude_canary=False), InMemorySpendGuard(cap_usd=1.0), NOW
    )
    assert status == "disabled" and rows(factory) == {}


def test_the_cli_needs_a_key(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here: a developer's own key must not be found
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from a11y_stem.config import get_settings

    get_settings.cache_clear()
    try:
        assert main(["ops", "claude-canary", "--max-usd", "0.1"]) == 2
    finally:
        get_settings.cache_clear()
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_the_truth_file_is_json_serialisable():
    json.dumps(claude_canary.SHOULD_FLAG)
