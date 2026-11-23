"""The step report: what model work and how much time a set of jobs took, by step and by
stage (`readablestem ops step-report`).

It is made from step facts (`pipeline.step_facts`), which hold codes, counts, seconds and
dollars only. Budgets, time limits and the figures the public pages quote are sized from
this report and not from estimates:

- by step: calls and tokens per page, how much of the input was read from the provider's
  prompt cache, how long one call takes, what one call and one page cost;
- by stage: how long each stage of a job takes, for a job and for a page;
- in all: dollars and calls, beside what the job rows settled, and seconds per page.

A percentile is the nearest rank: the value at or above that share of the list. With few
jobs the 95th percentile is the largest value, and the report prints how many it had.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from readablestem.core.types import CallFact, StepFacts

# Stages that are not part of a job's own wall time: the wait before a worker took it.
OUTSIDE_THE_JOB = frozenset({"queue_wait"})


@dataclass(frozen=True)
class Measured:
    """One job: its facts, and what its row says where there is one."""

    pages: int
    facts: StepFacts
    settled_usd: float | None = None  # `jobs.cost_usd`
    counted_calls: int | None = None  # `jobs.model_calls`
    wall_s: float | None = None  # from the job's start to its end


@dataclass(frozen=True)
class StepRow:
    step: str
    model: str
    calls: int
    failed: int  # calls that did not end "ok"
    calls_per_page: float
    input_per_page: float
    cached_per_page: float
    output_per_page: float
    cache_read: float | None  # cached input tokens over all input tokens
    call_s_p50: float
    call_s_p95: float
    call_usd_p99: float
    usd: float  # settled: calls that came back with their usage
    usd_per_page: float
    billed_unseen: int = 0  # calls charged at their reservation (`CallFact.billed_unseen_usd` > 0)
    billed_unseen_usd: float = 0.0


@dataclass(frozen=True)
class StageRow:
    stage: str
    jobs: int  # jobs that have the stage
    s_p50: float
    s_p95: float
    s_per_page_p50: float
    s_per_page_p95: float
    share: float | None  # of the wall time of the jobs that have it; None outside the job


@dataclass(frozen=True)
class Report:
    jobs: int
    pages: int
    steps: list[StepRow] = field(default_factory=list)
    stages: list[StageRow] = field(default_factory=list)
    usd: float = 0.0  # settled: without the calls charged at their reservation
    usd_per_page: float = 0.0
    # Calls charged at their reservation because no usage came back, and their dollars: apart
    # from `usd`, which is what the calls cost.
    billed_unseen_calls: int = 0
    billed_unseen_usd: float = 0.0
    calls: int = 0  # every request
    counted_calls: int = 0  # those in a job's call count
    calls_per_page: float = 0.0
    cache_read: float | None = None
    # What the job rows hold, for the jobs that have a row (None when none has).
    settled_usd: float | None = None
    settled_calls: int | None = None
    s_per_page_p50: float | None = None
    s_per_page_p95: float | None = None
    stage_s: float = 0.0  # every stage inside the jobs, summed
    wall_s: float | None = None  # the jobs' wall time, summed

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def percentile(values: Sequence[float], share: float) -> float:
    """The nearest-rank percentile of a list that is not empty."""
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole else None


def _step_rows(calls: Iterable[CallFact], pages: int) -> list[StepRow]:
    by_step: dict[tuple[str, str], list[CallFact]] = defaultdict(list)
    for call in calls:
        by_step[(call.step, call.model)].append(call)
    per_page = 1 / pages if pages else 0.0
    rows = []
    for (step, model), group in sorted(by_step.items()):
        unseen = [c for c in group if c.billed_unseen_usd]
        usd = sum(c.cost_usd - c.billed_unseen_usd for c in group)
        asked = sum(c.input_tokens for c in group)
        cached = sum(c.cached_input_tokens for c in group)
        seconds = [c.seconds for c in group]
        rows.append(
            StepRow(
                step=step,
                model=model,
                calls=len(group),
                failed=sum(c.outcome != "ok" for c in group),
                calls_per_page=len(group) * per_page,
                input_per_page=asked * per_page,
                cached_per_page=cached * per_page,
                output_per_page=sum(c.output_tokens for c in group) * per_page,
                cache_read=_ratio(cached, asked),
                call_s_p50=percentile(seconds, 0.5),
                call_s_p95=percentile(seconds, 0.95),
                call_usd_p99=percentile([c.cost_usd - c.billed_unseen_usd for c in group], 0.99),
                usd=usd,
                usd_per_page=usd * per_page,
                billed_unseen=len(unseen),
                billed_unseen_usd=sum(c.billed_unseen_usd for c in unseen),
            )
        )
    return rows


def _stage_rows(jobs: Sequence[Measured]) -> list[StageRow]:
    order: list[str] = []
    seconds: dict[str, list[float]] = defaultdict(list)
    per_page: dict[str, list[float]] = defaultdict(list)
    walls: dict[str, float] = defaultdict(float)
    for job in jobs:
        inside = sum(s.seconds for s in job.facts.stages if s.stage not in OUTSIDE_THE_JOB)
        for fact in job.facts.stages:
            if fact.stage not in seconds:
                order.append(fact.stage)
            seconds[fact.stage].append(fact.seconds)
            per_page[fact.stage].append(fact.seconds / job.pages if job.pages else 0.0)
            walls[fact.stage] += inside
    return [
        StageRow(
            stage=stage,
            jobs=len(seconds[stage]),
            s_p50=percentile(seconds[stage], 0.5),
            s_p95=percentile(seconds[stage], 0.95),
            s_per_page_p50=percentile(per_page[stage], 0.5),
            s_per_page_p95=percentile(per_page[stage], 0.95),
            share=None if stage in OUTSIDE_THE_JOB else _ratio(sum(seconds[stage]), walls[stage]),
        )
        for stage in order
    ]


def report(jobs: Sequence[Measured]) -> Report:
    """The report over these jobs. Jobs with no facts (converted before they were kept)
    are to be left out by the caller: they would count pages that nothing was measured for."""
    pages = sum(job.pages for job in jobs)
    calls = [call for job in jobs for call in job.facts.calls]
    unseen = [c for c in calls if c.billed_unseen_usd]
    usd = sum(c.cost_usd - c.billed_unseen_usd for c in calls)
    with_row = [job for job in jobs if job.settled_usd is not None]
    timed = [job for job in jobs if job.wall_s is not None and job.pages]
    rates = [job.wall_s / job.pages for job in timed if job.wall_s is not None]
    return Report(
        jobs=len(jobs),
        pages=pages,
        steps=_step_rows(calls, pages),
        stages=_stage_rows(jobs),
        usd=usd,
        usd_per_page=usd / pages if pages else 0.0,
        billed_unseen_calls=len(unseen),
        billed_unseen_usd=sum(c.billed_unseen_usd for c in unseen),
        calls=len(calls),
        counted_calls=sum(c.counted for c in calls),
        calls_per_page=len(calls) / pages if pages else 0.0,
        cache_read=_ratio(
            sum(c.cached_input_tokens for c in calls), sum(c.input_tokens for c in calls)
        ),
        settled_usd=sum(job.settled_usd or 0.0 for job in with_row) if with_row else None,
        settled_calls=sum(job.counted_calls or 0 for job in with_row) if with_row else None,
        s_per_page_p50=percentile(rates, 0.5) if rates else None,
        s_per_page_p95=percentile(rates, 0.95) if rates else None,
        stage_s=sum(
            s.seconds for job in jobs for s in job.facts.stages if s.stage not in OUTSIDE_THE_JOB
        ),
        wall_s=sum(job.wall_s or 0.0 for job in timed) if timed else None,
    )


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def _table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(str(row[i])) for row in (head, *rows)) for i in range(len(head))]
    lines = []
    for row in (head, *rows):
        cells = [
            str(c).ljust(w) if i < 2 else str(c).rjust(w)
            for i, (c, w) in enumerate(zip(row, widths, strict=True))
        ]
        lines.append("  " + "  ".join(cells).rstrip())
    return lines


def render(made: Report) -> str:
    """The report as text for a terminal."""
    if not made.jobs:
        return "no document with step facts"
    lines = [
        f"{made.jobs} documents, {made.pages} pages",
        "",
        "By step (a page is a page of a document):",
    ]
    lines += _table(
        (
            "step",
            "model",
            "calls",
            "failed",
            "calls/page",
            "input/page",
            "cached/page",
            "output/page",
            "cache read",
            "call s p50",
            "p95",
            "call $ p99",
            "$/page",
        ),
        [
            (
                row.step,
                row.model,
                str(row.calls),
                str(row.failed),
                f"{row.calls_per_page:.2f}",
                f"{row.input_per_page:.0f}",
                f"{row.cached_per_page:.0f}",
                f"{row.output_per_page:.0f}",
                _percent(row.cache_read),
                f"{row.call_s_p50:.2f}",
                f"{row.call_s_p95:.2f}",
                f"{row.call_usd_p99:.4f}",
                f"{row.usd_per_page:.4f}",
            )
            for row in made.steps
        ],
    )
    lines += ["", "By stage (seconds of wall time):"]
    lines += _table(
        ("stage", "documents", "doc p50", "doc p95", "page p50", "page p95", "share"),
        [
            (
                row.stage,
                str(row.jobs),
                f"{row.s_p50:.2f}",
                f"{row.s_p95:.2f}",
                f"{row.s_per_page_p50:.3f}",
                f"{row.s_per_page_p95:.3f}",
                _percent(row.share),
            )
            for row in made.stages
        ],
    )
    lines += [
        "",
        f"dollars: {made.usd:.4f} ({made.usd_per_page:.4f} a page)",
        f"charged at their reservation, no usage came back: {made.billed_unseen_calls} calls, "
        f"${made.billed_unseen_usd:.4f} (not in the dollars above)",
        f"calls: {made.calls} ({made.calls_per_page:.2f} a page), "
        f"{made.counted_calls} in the documents' call counts",
        f"input read from the prompt cache: {_percent(made.cache_read)}",
    ]
    if made.settled_usd is not None:
        apart = made.settled_usd - made.usd - made.billed_unseen_usd
        lines.append(
            f"settled on the document rows: ${made.settled_usd:.4f} and {made.settled_calls} calls"
            + (
                f" (${apart:.4f} more than the steps: spent by tries that did not finish)"
                if apart >= 0.00005
                else ""
            )
        )
    if made.s_per_page_p50 is not None and made.wall_s is not None:
        lines.append(
            f"seconds a page: p50 {made.s_per_page_p50:.2f}, p95 {made.s_per_page_p95:.2f}; "
            f"the stages hold {made.stage_s:.1f} s of the documents' {made.wall_s:.1f} s"
        )
    return "\n".join(lines)
