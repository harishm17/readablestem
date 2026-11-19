"""What a conversion measures of itself: every request it sent to a model, with the page it
was about, and how long each stage took.

Content-free by construction: a fact is made from a call record (`ModelCall`, `Verification`,
`ReaderCall`), which holds codes, counts, seconds and dollars, and from a page number. No
document text and nobody's id can reach it.

The facts travel on `ConversionOutput.facts`, not in the stored result. The web worker keeps
them for the job (`web.step_facts`), and `ops.step_report` turns the facts of many jobs
into the numbers budgets and public figures are sized from.

A third kind of fact says that a step stopped before it had done all of its work
(`NotFinished`). The rule it serves: a budget counts work (candidates tried, cells tested,
pixels read), which is the same on every machine; a limit on time or on money may only stop
a step, and a step that stops says so with one of these. `capped`, `pages_capped` and `stopped` make
the ones the conversion can see for itself; a step that has a budget of its own writes its
own.

A fourth kind says what a step run in shadow said next to the answer the conversion kept
(`ShadowFact`, made by `pipeline.shadow`).

Two kinds of time:

- a stage's seconds are wall time, and the stages of one conversion do not overlap, so they
  add up to the conversion's wall time (`Stopwatch`);
- a call's seconds are the time of that one request. The steps over a page's elements run
  several at a time, so their calls' seconds add up to more than the `steps` stage.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from readablestem.core.types import (
    CallFact,
    Fix,
    ModelCall,
    NotFinished,
    NotFinishedReason,
    ShadowFact,
    StageFact,
    Status,
    StepFacts,
)

# Steps whose requests are not in a job's call count: they are asked about an answer the
# job already has, and only their dollars count toward its limits (`core.budget.JobBudget`).
SECOND_CHECK = "second_check"
SECOND_READER = "second_reader"
# The two steps the ink analysis's budget can stop: typing a page, and reading the strokes
# of a handwritten page for its pictures.
PAGE_TYPE = "page_type"
INK_STROKES = "ink_strokes"
# The table search, which the document's CPU time for it can stop (tables.DetectorBudget).
TABLE_SEARCH = "table_search"
# The stage a caller adds for what its call of the conversion took outside the conversion's
# own stages (`start_up`).
START_UP = "start_up"


class Stopwatch:
    """Splits a conversion's wall time into named stages. `stage(name)` ends the stage
    before it and starts `name`; a name used twice is one stage with both times."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._since = clock()
        self._name: str | None = None
        self._seconds: dict[str, float] = {}

    def stage(self, name: str) -> None:
        self._close()
        self._name = name

    def _close(self) -> None:
        now = self._clock()
        if self._name is not None:
            self._seconds[self._name] = self._seconds.get(self._name, 0.0) + now - self._since
        self._since = now

    def stop(self) -> list[StageFact]:
        """The stages so far, in the order they first began."""
        self._close()
        self._name = None
        return [StageFact(stage=name, seconds=round(s, 4)) for name, s in self._seconds.items()]


def start_up(facts: StepFacts, call_seconds: float) -> StageFact:
    """What a conversion call of `call_seconds` took outside the conversion's own stages:
    starting its process, loading its code, and passing the file in and the result back."""
    inside = sum(fact.seconds for fact in facts.stages)
    return StageFact(stage=START_UP, seconds=round(max(0.0, call_seconds - inside), 4))


def of_call(call: ModelCall, page: int | None) -> CallFact:
    return CallFact(
        step=call.step,
        page=page,
        model=call.model,
        input_tokens=call.input_tokens,
        cached_input_tokens=call.cached_input_tokens,
        cache_creation_input_tokens=call.cache_creation_input_tokens,
        output_tokens=call.output_tokens,
        reasoning_tokens=call.reasoning_tokens,
        cost_usd=call.cost_usd,
        seconds=call.latency_ms / 1000,
        attempt=call.attempt,
        is_fallback=call.is_fallback,
        outcome=call.outcome,
        billed_unseen_usd=call.billed_unseen_usd,
    )


def of_calls(calls: Iterable[ModelCall], page: int | None) -> list[CallFact]:
    return [of_call(call, page) for call in calls]


def of_fix(fix: Fix, page: int | None) -> list[CallFact]:
    """The requests behind one fix: the calls that made its proposal, then the second
    checks asked about it."""
    return of_calls(fix.calls, page) + [
        CallFact(
            step=SECOND_CHECK,
            page=page,
            model=check.model,
            counted=False,
            input_tokens=check.input_tokens,
            cost_usd=check.cost_usd,
            seconds=check.latency_ms / 1000,
            outcome=check.outcome,
        )
        for check in fix.verifications
    ]


def of_reader_call(call: Any, page: int | None) -> CallFact:
    """A second reader's read of one equation (`models.math_readers.base.ReaderCall`)."""
    return CallFact(
        step=SECOND_READER,
        page=page,
        model=call.model,
        counted=False,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cost_usd=call.cost_usd,
        seconds=call.latency_ms / 1000,
        attempt=max(1, call.sends),
        outcome=call.outcome,
    )


def capped(fixes: Iterable[Fix]) -> list[NotFinished]:
    """The steps the job's limit on dollars or on calls stopped: for each kind of element,
    how many were done when the limit was reached (a fix that failed with a `budget_` reason
    is one that was not)."""
    done: dict[str, int] = {}
    stopped: dict[str, int] = {}
    for fix in fixes:
        step = fix.kind.value
        if fix.status is Status.FAILED and (fix.reason or "").startswith("budget_"):
            stopped[step] = stopped.get(step, 0) + 1
        else:
            done[step] = done.get(step, 0) + 1
    return [
        NotFinished(
            step=step,
            reason="cap_reached",
            done=done.get(step, 0),
            of=done.get(step, 0) + count,
        )
        for step, count in sorted(stopped.items())
    ]


def pages_capped(
    step: str, pages: Iterable[int], reason: NotFinishedReason = "cap_reached"
) -> list[NotFinished]:
    """The facts of a step that reads whole pages, for the pages it did not read: the ones
    the job's limit kept it from, or with another `reason` the ones its clock did."""
    return [NotFinished(step=step, page=page, reason=reason, of=1) for page in pages]


def ink_stopped(step: str, pages: dict[int, str]) -> list[NotFinished]:
    """The facts of an ink step (`PAGE_TYPE`, `INK_STROKES`) for the pages its budget stopped,
    `pages` mapping each to its cause (`page_type.InkBudget`): a count of work is
    "work_budget", the clock guard "time_limit"."""
    from readablestem.pipeline.page_type import INK_TIME

    return [
        NotFinished(
            step=step,
            page=page,
            reason="time_limit" if cause == INK_TIME else "work_budget",
            of=1,
        )
        for page, cause in sorted(pages.items())
    ]


def stopped(step: str, reason: NotFinishedReason, *, done: int, of: int) -> list[NotFinished]:
    """The fact of a step over the whole document that stopped with `of - done` units of
    its work left; nothing when it did them all."""
    if done >= of:
        return []
    return [NotFinished(step=step, reason=reason, done=done, of=of)]


def gathered(
    calls: Iterable[CallFact],
    watch: Stopwatch,
    not_finished: Iterable[NotFinished] = (),
    shadow: Iterable[ShadowFact] = (),
) -> StepFacts:
    """The facts of one conversion, its calls ordered by page and step so that two runs of
    one document give the same list whatever order their threads finished in. `shadow` is
    what the steps run in shadow said (`pipeline.shadow.Shadow.facts`, already in order)."""
    ordered = sorted(calls, key=lambda c: (c.page is None, c.page or 0, c.step, c.attempt))
    stops = sorted(not_finished, key=lambda n: (n.step, n.page is None, n.page or 0, n.reason))
    return StepFacts(calls=ordered, stages=watch.stop(), not_finished=stops, shadow=list(shadow))
