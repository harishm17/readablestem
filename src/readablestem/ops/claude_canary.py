"""Daily canary for the second automated check on Claude (T2-20; the counterpart of
`ops.canary`, which watches the Decisions API).

The Decisions API is deterministic, so its canary compares today's probabilities with
recorded ones. Claude is not: the same request can be answered differently, and a model
alias can be moved to a new snapshot without notice. So this canary does not compare with
yesterday. It asks the check about 12 fixed synthetic cases whose answer is known by
construction (an alt text that is right, one that states a wrong value, a LaTeX
transcription that matches, one with a wrong exponent) and counts the cases the check gets
wrong. A check that starts waving wrong alt text through, or flagging right alt text, shows
up the day the model or the prompt changes.

- Cases: the figure and equation cases of `canary_requests.json` (the Decisions canary's
  synthetic images, never document content) with `SHOULD_FLAG` below as the truth. The
  requests are the production ones (`models.claude_verifier`), so it tests the check as the
  pipeline uses it. The text-only predicate cases are not used.
- Verdict: the same rule as the steps. A figure is flagged when
  min(P(ok), 1 - P(factual error)) < `decisions_figure_flag_below`; an equation when
  P(meaning_changing) >= `decisions_equation_flag_at`.
- Alert: more than `claude_canary_max_misses` (default 1) of the answered cases on the wrong
  side is drift: one ERROR line `claude_canary_drift` (counts, ids, cost; no content) and a
  non-zero exit by hand. One miss is tolerated because a model is not a deterministic
  service: a one-off miss is logged at WARNING, a repeat across days is what to read in the
  daily counts (`ops report`).
- In the HTTP cron route (`maybe_run_daily`; not in `ops cron`, so not in production): at
  most once per UTC day, claimed with an
  insert-if-missing of the aggregate row (day, "canary", "claude:run"); fail-open; the result
  is counted as "claude:<status>" for the day.
- Spend: every request reserves first through a `JobBudget` (`CANARY_MAX_USD`) on the
  caller's spend guard, in dollars only. About $0.04 a run on claude-sonnet-5-5
  (docs/steps.md "Second check on Claude").
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from readablestem.config import Settings
from readablestem.core.budget import JobBudget, SpendGuard
from readablestem.models.verifier import (
    EQUATION_CHECK,
    FIGURE_CHECK,
    MEANING_CHANGING,
    Verifier,
    VerifierVerdict,
    figure_score,
)
from readablestem.observability.logging import log_fields
from readablestem.ops.canary import REQUESTS_FILE

log = logging.getLogger(__name__)

CANARY_MAX_USD = 0.25  # ~5x a run's worst case; a run costs about $0.04
CANARY_DEADLINE_S = 180.0  # 12 sequential requests of a few seconds each
CATEGORY = "claude"
# Known truth by construction: True where the check should flag the proposal.
SHOULD_FLAG: dict[str, bool] = {
    "figure-bars-correct": False,
    "figure-bars-wrong-value": True,
    "figure-bars-other-image": True,
    "figure-arrow-correct": False,
    "figure-arrow-wrong-direction": True,
    "figure-arrow-generic": True,
    "equation-fma-match": False,
    "equation-fma-wrong-operator": True,
    "equation-emc2-match": False,
    "equation-emc2-wrong-exponent": True,
    "equation-circle-match": False,
    "equation-circle-wrong-digits": True,
}


@dataclass(frozen=True)
class Case:
    id: str
    check: str  # FIGURE_CHECK or EQUATION_CHECK
    png: bytes
    alt: str = ""
    caption: str | None = None
    latex: str = ""

    @property
    def should_flag(self) -> bool:
        return SHOULD_FLAG[self.id]


@dataclass
class ClaudeCanaryResult:
    """What one run found. Content-free: case ids, numbers, codes."""

    cases: int = 0
    answered: int = 0
    missed: list[str] = field(default_factory=list)  # answered on the wrong side
    failed: list[tuple[str, str]] = field(default_factory=list)  # (case, reason code)
    cost_usd: float = 0.0
    max_misses: int = 1

    @property
    def status(self) -> str:
        """ "drift" beats "failed" (a case got no answer), then "ok"."""
        if len(self.missed) > self.max_misses:
            return "drift"
        if self.failed:
            return "failed"
        return "ok"


def load_cases(path: Path = REQUESTS_FILE) -> list[Case]:
    """The figure and equation cases of the shared canary file, with their truth."""
    data = json.loads(path.read_text(encoding="utf-8"))
    images = {name: base64.b64decode(png) for name, png in data["images"].items()}
    cases: list[Case] = []
    for entry in data["cases"]:
        if entry["use"] == "figure_alt":
            cases.append(
                Case(
                    entry["id"],
                    FIGURE_CHECK,
                    images[entry["image"]],
                    alt=entry["alt"],
                    caption=entry.get("caption"),
                )
            )
        elif entry["use"] == "equation_match":
            cases.append(
                Case(entry["id"], EQUATION_CHECK, images[entry["image"]], latex=entry["latex"])
            )
    unknown = [c.id for c in cases if c.id not in SHOULD_FLAG]
    if unknown:
        raise ValueError(f"canary cases without a known truth: {unknown}")
    return cases


def flagged(verdict: VerifierVerdict, settings: Settings) -> bool | None:
    """The steps' rule applied to one answer; None without an answer."""
    if not verdict.answered:
        return None
    if verdict.check == FIGURE_CHECK:
        score = figure_score(verdict.probabilities)
        return None if score is None else score < settings.decisions_figure_flag_below
    p = verdict.probabilities.get(MEANING_CHANGING)
    return None if p is None else p >= settings.decisions_equation_flag_at


class _Clock(Protocol):
    def __call__(self) -> float: ...


def run_canary(
    verifier: Verifier,
    settings: Settings,
    *,
    cases: list[Case] | None = None,
    deadline_s: float = CANARY_DEADLINE_S,
    clock: _Clock = time.monotonic,
) -> ClaudeCanaryResult:
    """Ask every case once and count the ones answered on the wrong side. Stops at the
    first refused reservation and at the deadline (the rest count as failed). Never raises
    for provider problems (they are failed cases)."""
    started = clock()
    todo = load_cases() if cases is None else cases
    result = ClaudeCanaryResult(cases=len(todo), max_misses=settings.claude_canary_max_misses)
    for index, case in enumerate(todo):
        if clock() - started > deadline_s:
            result.failed += [(c.id, "deadline") for c in todo[index:]]
            break
        if case.check == FIGURE_CHECK:
            verdict = verifier.figure_alt(case.png, alt=case.alt, caption=case.caption)
        else:
            verdict = verifier.equation_match(case.png, latex=case.latex)
        result.cost_usd += verdict.cost_usd
        if verdict.reason_code == "budget_exceeded":
            result.failed += [(c.id, "budget_exceeded") for c in todo[index:]]
            break
        got = flagged(verdict, settings)
        if got is None:
            result.failed.append((case.id, verdict.reason_code))
            continue
        result.answered += 1
        if got != case.should_flag:
            result.missed.append(case.id)
    return result


def report(result: ClaudeCanaryResult, *, source: str) -> None:
    """The log line for a run: ERROR on drift (the alert), WARNING for a tolerated miss or
    a case without an answer, INFO otherwise. Ids, counts and numbers only."""
    fields: dict[str, Any] = {
        "source": source,
        "status": result.status,
        "cases": result.cases,
        "answered": result.answered,
        "missed": len(result.missed),
        "failed": len(result.failed),
        "cost_usd": round(result.cost_usd, 6),
    }
    if result.missed:
        fields["missed_cases"] = ",".join(sorted(result.missed))[:200]
    if result.status == "drift":
        log_fields(log, logging.ERROR, "claude_canary_drift", **fields)
    elif result.status != "ok" or result.missed:
        if result.failed:
            fields["first_problem"] = result.failed[0][1]
        log_fields(log, logging.WARNING, "claude_canary_incomplete", **fields)
    else:
        log_fields(log, logging.INFO, "claude_canary_ok", **fields)


# ------------------------------------------------------------------------ the cron


def due(settings: Settings) -> str | None:
    """Why the cron should not run the canary, or None when it should."""
    if not settings.claude_canary:
        return "disabled"
    if settings.env == "test":
        return "test_env"
    if settings.fake_models:
        return "fake_models"
    if settings.model_provider != "anthropic":
        return "provider_not_anthropic"
    if settings.claude_second_check == "off":
        return "check_off"
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        return "no_key"
    return None


def maybe_run_daily(
    factory: sessionmaker[Session],
    settings: Settings,
    guard: SpendGuard,
    now: datetime | None = None,
    *,
    verifier_factory: Callable[[Settings, JobBudget], Verifier] | None = None,
) -> str:
    """Run today's canary from the HTTP cron route if it is on and has not run today (UTC).

    Returns a status code ("ok", "drift", "failed", "not_due" when another run already
    claimed today, or why it is off). Never raises: a canary problem must not fail the
    cron (fail-open); it is logged and counted instead."""
    from readablestem.ops import canary

    try:
        reason = due(settings)
        if reason is not None:
            return reason
        day = (now or datetime.now(UTC)).astimezone(UTC).date()
        if not canary.claim_today(factory, day, f"{CATEGORY}:run"):
            return "not_due"
        budget = JobBudget(guard=guard, max_usd=CANARY_MAX_USD, max_calls=0)
        if verifier_factory is None:
            from readablestem.models.claude_verifier import make_claude_verifier

            verifier = make_claude_verifier(settings, budget)
        else:
            verifier = verifier_factory(settings, budget)
        result = run_canary(verifier, settings)
        report(result, source="cron")
        canary.count_status(factory, day, f"{CATEGORY}:{result.status}")
        return result.status
    except Exception as exc:
        log_fields(
            log,
            logging.WARNING,
            "claude_canary_failed",
            source="cron",
            error_type=type(exc).__name__,
        )
        return "error"
