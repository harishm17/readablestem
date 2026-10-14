"""Daily drift canary for the Decisions API (the second automated check, ADR 0005).

The Decisions API is deterministic: the same request gets the same answers, to the 0.01
it reports (931 of 931 repeats in the research, 2026-10-08). So a fixed set of about 20
requests, answered once a day and compared with answers recorded when the thresholds
were set, shows a silent change of the model or the service the day it happens. The
thresholds (`decisions_figure_flag_below`, `decisions_equation_flag_at`) mean nothing
once the answers under them move.

- Requests: `canary_requests.json` next to this module. Synthetic inputs only (bar
  charts, a force diagram and short equations drawn with PyMuPDF, plain-text
  statements), never document content. The figure and equation cases are built with
  the production builders (`models.verifier.figure_request`, `equation_request`), so
  they ask exactly the questions the pipeline asks; the text cases are generic
  predicates.
- Expected answers: `canary_expected.json`, recorded with
  `a11y-stem ops decisions-canary --record --max-usd 0.02` and committed. Each case
  carries the SHA-256 of its request as sent, so changed wording, framing or images
  cannot be compared with old answers (a test fails until the file is re-recorded).
- Alert: any probability that moved by more than `TOLERANCE` (0.02), or an answer that
  became or stopped being a refusal, is drift: one ERROR line `decisions_canary_drift`
  (counts, the largest move and the case ids, all content-free) and a non-zero exit.
- In the hourly cron (`maybe_run_daily`): at most once per UTC day, claimed with an
  insert-if-missing of the aggregate row (day, "canary", "decisions:run"), so two
  instances or two cron runs cannot both run it. Fail-open: nothing about the canary can
  fail the cron. The result is counted as "decisions:<status>" for the day.
- Spend: every request reserves first through a `JobBudget` (`CANARY_MAX_USD`) on the
  caller's spend guard: the daily cap in the cron, `--max-usd` by hand. Decisions
  requests hold dollars only, not model calls (`core.budget`), so the budget allows no
  model call at all and the dollar cap is what bounds a run. About $0.001 per run.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.core.budget import BudgetExceeded, JobBudget, SpendGuard
from a11y_stem.observability.logging import log_fields

if TYPE_CHECKING:
    from a11y_stem.models.decisions_client import Answer, DecisionRequest, DecisionResult

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
REQUESTS_FILE = HERE / "canary_requests.json"
EXPECTED_FILE = HERE / "canary_expected.json"
TOLERANCE = 0.02
CANARY_MAX_USD = 0.02  # ~50x a run's worst case; a run costs about $0.001
# A run takes about 3 s. If the API hangs (10 s timeout per request), the cases left
# after this long count as failed, so the cron request it runs in stays short.
CANARY_DEADLINE_S = 60.0
SCHEMA = 1
CANARY_STEP = "canary"
REFUSED = "refused"
TEXT_INSTRUCTIONS_DEFAULT = "True if the statement is correct."


class Decider(Protocol):
    def decide(self, request: DecisionRequest) -> DecisionResult: ...


@dataclass(frozen=True)
class Case:
    id: str
    request: DecisionRequest


@dataclass
class CanaryResult:
    """What one run found. Content-free: case ids, question names, numbers, codes."""

    cases: int = 0
    compared: int = 0
    drifted: list[tuple[str, str, float]] = field(default_factory=list)  # (case, question, delta)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (case, outcome)
    stale: list[str] = field(default_factory=list)  # cases whose request changed
    cost_usd: float = 0.0
    observed: dict[str, dict[str, Any]] = field(default_factory=dict)  # for --record

    @property
    def status(self) -> str:
        """ "drift" beats everything; then "stale" (expectations from other requests),
        "failed" (a case got no answer) and "ok"."""
        if self.drifted:
            return "drift"
        if self.stale:
            return "stale"
        if self.failed:
            return "failed"
        return "ok"

    @property
    def max_delta(self) -> float:
        return max((d for _, _, d in self.drifted), default=0.0)


# --------------------------------------------------------------------------- cases


def load_cases(path: Path = REQUESTS_FILE) -> list[Case]:
    """The canary's requests, built as the pipeline builds its own."""
    from a11y_stem.models.decisions_client import DecisionRequest, Question, TextPart
    from a11y_stem.models.verifier import equation_request, figure_request

    data = json.loads(path.read_text(encoding="utf-8"))
    images = {name: base64.b64decode(png) for name, png in data["images"].items()}
    cases = []
    for entry in data["cases"]:
        use = entry["use"]
        if use == "figure_alt":
            request = figure_request(
                images[entry["image"]], alt=entry["alt"], caption=entry.get("caption")
            )
        elif use == "equation_match":
            request = equation_request(images[entry["image"]], latex=entry["latex"])
        elif use == "predicate":
            instructions = entry.get("instructions", TEXT_INSTRUCTIONS_DEFAULT)
            request = DecisionRequest(
                purpose=CANARY_STEP,
                version="canary-text",
                parts=(TextPart(entry["text"]),),
                questions=(Question("true", instructions),),
            )
        else:
            raise ValueError(f"unknown canary case use {use!r}")
        cases.append(Case(entry["id"], dataclasses.replace(request, purpose=CANARY_STEP)))
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("canary case ids must be unique")
    return cases


def request_sha(request: DecisionRequest, model: str) -> str:
    """SHA-256 of the request as sent (parts, images included, questions and model)."""
    from a11y_stem.models.decisions_client import check_questions, request_content

    payload = {
        "model": model,
        "content": request_content(request),
        "questions": check_questions(request.questions),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_expected(path: Path = EXPECTED_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def answers_json(answers: Mapping[str, Answer]) -> dict[str, Any]:
    """Answers as stored: predicate {"true": p}, choice {value: p}, or "refused"."""
    out: dict[str, Any] = {}
    for name, answer in answers.items():
        if answer.refused:
            out[name] = REFUSED
        elif answer.probability is not None:
            out[name] = {"true": round(answer.probability, 4)}
        else:
            out[name] = {k: round(v, 4) for k, v in answer.probabilities.items()}
    return out


def compare(expected: Mapping[str, Any], got: Mapping[str, Any]) -> list[tuple[str, float]]:
    """(question, largest move) for every answer that moved by more than TOLERANCE.

    A refusal on one side and an answer on the other is a move of 1.0, and so is a
    question or a choice value present on one side only.
    """
    moved: list[tuple[str, float]] = []
    for name in sorted(set(expected) | set(got)):
        old, new = expected.get(name), got.get(name)
        if old == new:
            continue
        if not isinstance(old, Mapping) or not isinstance(new, Mapping):
            moved.append((name, 1.0))
            continue
        delta = max(
            abs(float(old.get(k, -1.0)) - float(new.get(k, -1.0))) if k in old and k in new else 1.0
            for k in set(old) | set(new)
        )
        if delta > TOLERANCE + 1e-9:
            moved.append((name, round(delta, 4)))
    return moved


# ------------------------------------------------------------------------------ run


def run_canary(
    decider: Decider,
    *,
    model: str,
    cases: Iterable[Case] | None = None,
    expected: Mapping[str, Any] | None = None,
    deadline_s: float = CANARY_DEADLINE_S,
    clock: Callable[[], float] = time.monotonic,
) -> CanaryResult:
    """Send every case once and compare with the expected answers.

    Stops at the first refused reservation (the remaining cases count as failed with
    "budget_exceeded") and once `deadline_s` has passed ("deadline"). Never raises for
    provider problems (they are failed cases).
    """
    started = clock()
    cases = list(load_cases() if cases is None else cases)
    expected_cases: Mapping[str, Any] = (load_expected() if expected is None else expected).get(
        "cases", {}
    )
    result = CanaryResult(cases=len(cases))
    for index, case in enumerate(cases):
        if clock() - started > deadline_s:
            result.failed += [(c.id, "deadline") for c in cases[index:]]
            break
        sha = request_sha(case.request, model)
        try:
            answer = decider.decide(case.request)
        except BudgetExceeded:
            result.failed += [(c.id, "budget_exceeded") for c in cases[index:]]
            break
        result.cost_usd += answer.cost_usd
        if answer.outcome not in {"ok", "refused"}:
            result.failed.append((case.id, answer.outcome))
            continue
        got = answers_json(answer.answers)
        if answer.outcome == "refused" and not got:
            got = {q.name: REFUSED for q in case.request.questions}
        result.observed[case.id] = {"request_sha": sha, "answers": got}
        want = expected_cases.get(case.id)
        if want is None or want.get("request_sha") != sha:
            result.stale.append(case.id)
            continue
        result.compared += 1
        for question, delta in compare(want.get("answers", {}), got):
            result.drifted.append((case.id, question, delta))
    return result


def report(result: CanaryResult, *, source: str) -> None:
    """The log line for a run: ERROR on drift (the alert), WARNING when it could not
    compare everything, INFO otherwise. Ids, counts and numbers only."""
    fields: dict[str, Any] = {
        "source": source,
        "status": result.status,
        "cases": result.cases,
        "compared": result.compared,
        "drifted": len(result.drifted),
        "failed": len(result.failed),
        "stale": len(result.stale),
        "cost_usd": round(result.cost_usd, 6),
    }
    if result.drifted:
        fields["max_delta"] = result.max_delta
        fields["drifted_cases"] = ",".join(sorted({c for c, _, _ in result.drifted}))[:200]
        log_fields(log, logging.ERROR, "decisions_canary_drift", **fields)
    elif result.status != "ok":
        fields["first_problem"] = (result.stale or [c for c, _ in result.failed] or [""])[0]
        log_fields(log, logging.WARNING, "decisions_canary_incomplete", **fields)
    else:
        log_fields(log, logging.INFO, "decisions_canary_ok", **fields)


def expected_document(result: CanaryResult, *, model: str, day: date) -> dict[str, Any]:
    """The expected-answers file for `--record` (refused when a case got no answer)."""
    if result.failed:
        raise ValueError("cannot record: some cases got no answer; re-run first")
    return {
        "schema": SCHEMA,
        "recorded": day.isoformat(),
        "model": model,
        "tolerance": TOLERANCE,
        "cases": dict(sorted(result.observed.items())),
    }


# ------------------------------------------------------------------------ the cron


def _claim_today(factory: sessionmaker[Session], day: date) -> bool:
    """Insert the day's "decisions:run" aggregate row if missing; True if this caller
    inserted it (and so runs today's canary). One statement, safe across instances."""
    from a11y_stem.web.database import rowcount
    from a11y_stem.web.db import ErrorAggregate, session_scope

    values = {"day": day, "step": CANARY_STEP, "category": "decisions:run", "count": 1}
    with session_scope(factory) as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(ErrorAggregate).values(**values).on_conflict_do_nothing()
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(ErrorAggregate).values(**values).on_conflict_do_nothing()
        return rowcount(session.execute(stmt)) == 1


def _count(factory: sessionmaker[Session], day: date, status: str) -> None:
    from collections import Counter

    from a11y_stem.ops.aggregate import code, upsert_counts
    from a11y_stem.web.db import session_scope

    with session_scope(factory) as session:
        upsert_counts(session, day, Counter({(CANARY_STEP, f"decisions:{code(status)}"): 1}))


def due(settings: Settings) -> str | None:
    """Why the cron should not run the canary, or None when it should."""
    if not settings.decisions_canary:
        return "disabled"
    if settings.env == "test":
        return "test_env"  # offline test runs never reach the network from the cron
    if settings.fake_models:
        return "fake_models"
    if settings.model_provider != "openai":
        return "provider_not_openai"  # the Decisions API is OpenAI's: unused with Claude
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        return "no_key"
    return None


def maybe_run_daily(
    factory: sessionmaker[Session],
    settings: Settings,
    guard: SpendGuard,
    now: datetime | None = None,
    *,
    decider_factory: Callable[[Settings, JobBudget], Decider] | None = None,
) -> str:
    """Run today's canary from the hourly cron if it is on and has not run today (UTC).

    Returns a status code ("ok", "drift", "failed", "stale", "not_due" when another run
    already claimed today, or why it is off). Never raises: a canary problem must not
    fail the cron (fail-open); it is logged and counted instead.
    """
    try:
        reason = due(settings)
        if reason is not None:
            return reason
        day = (now or datetime.now(UTC)).astimezone(UTC).date()
        if not _claim_today(factory, day):
            return "not_due"
        cases = load_cases()
        # Dollars only: Decisions requests are not model calls, and none is made here.
        budget = JobBudget(guard=guard, max_usd=CANARY_MAX_USD, max_calls=0)
        if decider_factory is None:
            from a11y_stem.models.decisions_client import DecisionsClient

            decider: Decider = DecisionsClient(settings, budget)
        else:
            decider = decider_factory(settings, budget)
        result = run_canary(decider, model=settings.decisions_model, cases=cases)
        report(result, source="cron")
        _count(factory, day, result.status)
        return result.status
    except Exception as exc:
        log_fields(
            log,
            logging.WARNING,
            "decisions_canary_failed",
            source="cron",
            error_type=type(exc).__name__,
        )
        return "error"
