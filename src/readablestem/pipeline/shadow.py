"""Shadow: a new step run beside the conversion, which changes nothing.

A step that is not yet trusted (a new check, a new reader) runs "in shadow" first: it is
given the same input as the answer the conversion keeps, what it says is compared with that
answer, and the comparison is all that is kept. The conversion's result is the same with
the step and without it, so the step can be measured on real documents before anything
depends on it. It leaves shadow only when an eval run meets its bar (docs/eval.md).

`Shadow.beside` is the one way to run such a step:

- it returns nothing, so the caller cannot use the step's answer by mistake;
- whatever the step raises is caught here and counted as "failed" by the exception's type.
  This is the one place a step's failure, a spend limit included, must not reach the
  conversion: a job never fails, stops or changes because of a step nobody reads;
- what is kept is a `ShadowFact`: the step's name, the page, and one of "same", "differs"
  and "failed". It holds neither answer, so it is content-free like every other step fact
  and travels with them (`StepFacts.shadow`).

Money: a step in shadow that asks a model sends its requests the way the second checks do
(`core.budget`, `count_call=False`), so they are not in the job's call count, and gives its
calls to the step facts as rows that are not counted (`CallFact.counted`). What a person is
charged never depends on dollars spent, so a shadow step cannot change it.

`agreement` turns the facts of one or many conversions into the counts by step that the
worker logs (`job_shadow`) and an eval reads.
"""

from __future__ import annotations

import operator
import threading
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from readablestem.core.types import ShadowFact

T = TypeVar("T")


class Shadow:
    """The shadow steps of one conversion. Safe to use from the threads its steps run on."""

    def __init__(self) -> None:
        self._facts: list[ShadowFact] = []
        self._lock = threading.Lock()

    def beside(
        self,
        step: str,
        kept: T,
        tried: Callable[[], T],
        *,
        page: int | None = None,
        same: Callable[[T, T], Any] = operator.eq,
    ) -> None:
        """Run `tried` and note whether its answer is the one the conversion `kept`
        (`same` says so: equality unless the step gives its own test)."""
        error = ""
        try:
            outcome = "same" if same(kept, tried()) else "differs"
        except Exception as exc:  # every failure: the module's note says why
            outcome, error = "failed", type(exc).__name__
        fact = ShadowFact.model_validate(
            {"step": step, "page": page, "outcome": outcome, "error": error}
        )
        with self._lock:
            self._facts.append(fact)

    def facts(self) -> list[ShadowFact]:
        """What the steps said, ordered by step and page so that two runs of one document
        give the same list whatever order their threads finished in."""
        with self._lock:
            return sorted(
                self._facts, key=lambda f: (f.step, f.page is None, f.page or 0, f.outcome, f.error)
            )


def agreement(facts: Iterable[ShadowFact]) -> dict[str, dict[str, int]]:
    """For each step, how often it said the same as the kept answer, said something else,
    and failed: `{step: {"same": n, "differs": n, "failed": n}}`, steps in name order."""
    counts: dict[str, dict[str, int]] = {}
    for fact in facts:
        row = counts.setdefault(fact.step, {"same": 0, "differs": 0, "failed": 0})
        row[fact.outcome] += 1
    return dict(sorted(counts.items()))
