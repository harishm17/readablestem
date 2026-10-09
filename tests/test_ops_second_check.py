"""Ops side of the second automated check: aggregate counts, the weekly report, and the
daily Decisions canary (comparison, the once-a-day claim, the CLI and the cron route)."""

from __future__ import annotations

import base64
import json
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest
import structlog
from sqlalchemy import select

from a11y_stem.cli.main import main
from a11y_stem.config import Settings, get_settings
from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard
from a11y_stem.core.types import Fix, Kind, Status, Verification
from a11y_stem.models.decisions_client import (
    Answer,
    DecisionRequest,
    DecisionResult,
    check_questions,
    request_content,
)
from a11y_stem.models.openai_client import PNG_SIGNATURE
from a11y_stem.models.verifier import EQUATION_VERSION, FIGURE_VERSION
from a11y_stem.ops import aggregate, canary
from a11y_stem.ops.aggregate import fix_counts, score_bucket
from a11y_stem.ops.report import render_report
from a11y_stem.web import db
from a11y_stem.web.db import ErrorAggregate, session_scope

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


def test_each_answer_is_counted_by_outcome_flag_score_and_cost():
    flagged = Verification(
        check="figure_alt",
        probabilities={"ok": 0.31, "factual_error": 0.2},
        flagged=True,
        cost_usd=0.00006,
    )
    timed_out = Verification(check="figure_alt", outcome="timeout")
    counts = fix_counts(figure_fix(flagged))
    assert counts[("figure", "verify:figure_alt:final:outcome:ok")] == 1
    assert counts[("figure", "verify:figure_alt:final:flagged")] == 1
    assert counts[("figure", "verify:figure_alt:final:score:0.3")] == 1
    assert counts[("figure", "verify:cost_microusd")] == 60
    counts = fix_counts(figure_fix(timed_out))
    assert counts[("figure", "verify:figure_alt:final:outcome:timeout")] == 1
    assert not any("score" in c or "flagged" in c for _, c in counts)


def test_equation_shadow_stages_are_counted_apart():
    cheap = Verification(
        check="equation_match",
        stage="cheap",
        mode="shadow",
        probabilities={"match": 0.1, "cosmetic": 0.1, "meaning_changing": 0.8},
        flagged=True,
    )
    final = cheap.model_copy(
        update={"stage": "final", "flagged": False, "probabilities": {"meaning_changing": 0.05}}
    )
    fix = Fix(
        element_id="p1-e2", kind=Kind.EQUATION, status=Status.ACCEPTED, verifications=[cheap, final]
    )
    counts = fix_counts(fix)
    assert counts[("equation", "verify:equation_match:cheap:score:0.8")] == 1
    assert counts[("equation", "verify:equation_match:cheap:flagged")] == 1
    assert counts[("equation", "verify:equation_match:final:score:0.0")] == 1
    assert ("equation", "verify:equation_match:final:flagged") not in counts


def test_codes_from_a_stored_record_cannot_carry_text():
    record = Verification(check="Secret quokka sentence", stage="x y", outcome="a b")
    counts = fix_counts(figure_fix(record))
    assert ("figure", "verify:other:other:outcome:other") in counts
    assert all("quokka" not in c for _, c in counts)


@pytest.mark.parametrize(
    ("score", "bucket"), [(0.0, "0.0"), (0.099, "0.0"), (0.5, "0.5"), (0.99, "0.9"), (1.0, "0.9")]
)
def test_score_buckets_are_tenths(score, bucket):
    assert score_bucket(score) == bucket


def test_the_weekly_report_has_a_second_check_section_and_the_canary():
    counts = Counter(
        {
            ("job", "status:done"): 2,
            ("job", "pages"): 10,
            ("figure", "status:accepted"): 3,
            ("figure", "verify:figure_alt:final:outcome:ok"): 3,
            ("figure", "verify:figure_alt:final:flagged"): 1,
            ("figure", "verify:cost_microusd"): 180,
            ("equation", "status:accepted"): 4,
            ("equation", "verify:equation_match:final:score:0.0"): 4,
            ("canary", "decisions:run"): 1,
            ("canary", "decisions:ok"): 1,
        }
    )
    text = render_report(dict(counts), since=DAY, until=DAY)
    assert "figure: second automated check" in text
    assert "figure_alt:final:flagged" in text
    assert "equation_match:final:score:0.0" in text
    assert "$0.0002 ($0.00002 per page)" in text
    assert "Decisions canary" in text and "run" in text


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


def test_the_canary_requests_are_synthetic_and_about_twenty():
    cases = canary.load_cases()
    assert 15 <= len(cases) <= 30
    data = json.loads(canary.REQUESTS_FILE.read_text())
    for png in data["images"].values():
        assert base64.b64decode(png).startswith(PNG_SIGNATURE)
    uses = Counter(entry["use"] for entry in data["cases"])
    assert uses["figure_alt"] >= 4 and uses["equation_match"] >= 4 and uses["predicate"] >= 4
    for case in cases:
        request_content(case.request)  # valid as sent
        check_questions(case.request.questions)
        assert case.request.purpose == "canary"


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


def test_identical_answers_are_ok():
    result = canary.run_canary(Replay(), model="gpt-6-luna")
    assert result.status == "ok"
    assert result.compared == result.cases == len(canary.load_cases())
    assert result.cost_usd == pytest.approx(0.00003 * result.cases)


def test_a_move_of_two_hundredths_is_tolerated_more_is_drift(caplog):
    small = canary.run_canary(Replay(shift={"text-product-false": 0.02}), model="gpt-6-luna")
    assert small.status == "ok"
    moved = canary.run_canary(Replay(shift={"text-product-false": 0.03}), model="gpt-6-luna")
    assert moved.status == "drift"
    assert moved.drifted == [("text-product-false", "true", 0.03)]
    with caplog.at_level(logging.INFO, logger="a11y_stem.ops.canary"):
        canary.report(moved, source="test")
    (line,) = [r for r in caplog.records if r.getMessage() == "decisions_canary_drift"]
    assert line.levelno == logging.ERROR


def test_a_refusal_appearing_is_drift_a_failed_case_is_not():
    refused = canary.run_canary(Replay(refuse={"figure-bars-correct"}), model="gpt-6-luna")
    assert refused.status == "drift"
    assert {q for c, q, _ in refused.drifted if c == "figure-bars-correct"} == {"ok", "fe"}
    failed = canary.run_canary(Replay(fail={"equation-fma-match"}), model="gpt-6-luna")
    assert failed.status == "failed" and failed.failed == [("equation-fma-match", "timeout")]


def test_a_refused_reservation_stops_the_run_and_fails_the_rest():
    result = canary.run_canary(Replay(budget_after=3), model="gpt-6-luna")
    assert result.status == "failed" and result.compared == 3
    assert {o for _, o in result.failed} == {"budget_exceeded"}


def test_a_slow_run_stops_at_its_deadline():
    ticks = iter(range(1000))
    result = canary.run_canary(
        Replay(), model="gpt-6-luna", deadline_s=4.5, clock=lambda: float(next(ticks))
    )
    assert result.status == "failed" and result.compared == 4
    assert {o for _, o in result.failed} == {"deadline"}


def test_expectations_for_other_requests_are_stale_not_compared():
    expected = canary.load_expected()
    expected["cases"]["text-root-true"]["request_sha"] = "0" * 64
    result = canary.run_canary(Replay(), model="gpt-6-luna", expected=expected)
    assert result.status == "stale" and result.stale == ["text-root-true"]


def test_compare_moves():
    assert canary.compare({"q": {"true": 0.5}}, {"q": {"true": 0.52}}) == []
    assert canary.compare({"q": {"true": 0.5}}, {"q": {"true": 0.53}}) == [("q", 0.03)]
    assert canary.compare({"q": "refused"}, {"q": {"true": 0.5}}) == [("q", 1.0)]
    assert canary.compare({"q": {"a": 0.5}}, {"q": {"b": 0.5}}) == [("q", 1.0)]
    assert canary.compare({"q": {"true": 0.5}}, {}) == [("q", 1.0)]


def test_record_refuses_when_a_case_failed():
    result = canary.run_canary(Replay(fail={"text-root-true"}), model="gpt-6-luna")
    with pytest.raises(ValueError):
        canary.expected_document(result, model="gpt-6-luna", day=DAY)
    ok = canary.run_canary(Replay(), model="gpt-6-luna")
    document = canary.expected_document(ok, model="gpt-6-luna", day=DAY)
    assert document["cases"] == canary.load_expected()["cases"]


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


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"decisions_canary": False}, "disabled"),
        ({"env": "test"}, "test_env"),
        ({"fake_models": True}, "fake_models"),
        ({"openai_api_key": None}, "no_key"),
    ],
)
def test_the_cron_canary_needs_a_real_key_outside_tests(factory, overrides, reason):
    guard = InMemorySpendGuard(cap_usd=1.0)

    def never(settings, budget):  # pragma: no cover - must not be reached
        raise AssertionError("no canary expected")

    status = canary.maybe_run_daily(
        factory, settings(**overrides), guard, NOW, decider_factory=never
    )
    assert status == reason and canary_rows(factory) == {}


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


def test_the_cron_route_runs_the_canary_after_the_purge(tmp_path, monkeypatch, caplog):
    from a11y_stem.ops import retention
    from a11y_stem.ops.retention import PurgeStats
    from fixtures.web.support import WebEnv

    order: list[str] = []
    monkeypatch.setattr(aggregate, "aggregate_finished", lambda f, now=None: order.append("a") or 0)
    monkeypatch.setattr(
        retention,
        "purge_expired",
        lambda f, now=None: order.append("p") or PurgeStats(0, 0, 0),
    )

    def fake_daily(factory, settings, guard, now=None, **kw):
        order.append("canary")
        return "ok"

    monkeypatch.setattr(canary, "maybe_run_daily", fake_daily)
    env = WebEnv(tmp_path)
    with env.running() as client, caplog.at_level(logging.INFO):
        response = client.post(
            "/internal/cron/aggregate-and-purge", headers={"X-Cron-Token": "c" * 40}
        )
    assert response.status_code == 200 and order == ["a", "p", "canary"]
    assert "decisions_canary" not in response.json()  # the response contract is unchanged


def test_the_real_cron_in_tests_never_reaches_the_network(tmp_path):
    from fixtures.web.support import WebEnv

    env = WebEnv(tmp_path)  # env=test, no key
    with env.running() as client:
        response = client.post(
            "/internal/cron/aggregate-and-purge", headers={"X-Cron-Token": "c" * 40}
        )
    assert response.status_code == 200
    assert canary_rows(env.ctx.factory) == {}


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
    from a11y_stem.models import decisions_client

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


def test_cli_needs_a_cap_and_a_key(cli_state, tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["ops", "decisions-canary"])
    cli_state.delenv("OPENAI_API_KEY")
    cli_state.chdir(tmp_path)  # no .env here
    assert main(["ops", "decisions-canary", "--max-usd", "0.01"]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_cli_record_writes_the_expected_file(cli_state, tmp_path, capsys):
    use_replay(cli_state, Replay())
    target = tmp_path / "expected.json"
    assert (
        main(
            ["ops", "decisions-canary", "--max-usd", "0.01", "--record", "--expected", str(target)]
        )
        == 0
    )
    written = json.loads(target.read_text())
    assert written["cases"] == canary.load_expected()["cases"]
    assert "recorded=expected.json" in capsys.readouterr().out
