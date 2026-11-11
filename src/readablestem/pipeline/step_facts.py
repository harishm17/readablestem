"""What a conversion measures of itself: every request it sent to a model, with the page it
was about, and how long each stage took.

Content-free by construction: a fact is made from a call record (`ModelCall`, `Verification`,
`ReaderCall`), which holds codes, counts, seconds and dollars, and from a page number. No
document text and nobody's id can reach it.

The facts travel on `ConversionOutput.facts`, not in the stored result. The web worker keeps
them for the job (`web.step_facts`), and `ops.step_report` turns the facts of many jobs
into the numbers budgets and public figures are sized from.

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

from readablestem.core.types import CallFact, Fix, ModelCall, StageFact, StepFacts

# Steps whose requests are not in a job's call count: they are asked about an answer the
# job already has, and only their dollars count toward its limits (`core.budget.JobBudget`).
SECOND_CHECK = "second_check"
SECOND_READER = "second_reader"


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


def gathered(calls: Iterable[CallFact], watch: Stopwatch) -> StepFacts:
    """The facts of one conversion, its calls ordered by page and step so that two runs of
    one document give the same list whatever order their threads finished in."""
    ordered = sorted(calls, key=lambda c: (c.page is None, c.page or 0, c.step, c.attempt))
    return StepFacts(calls=ordered, stages=watch.stop())
