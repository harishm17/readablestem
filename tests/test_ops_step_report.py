"""ops.step_report: the report made from the step facts of a set of jobs."""

from __future__ import annotations

import pytest

from readablestem.core.types import CallFact, StageFact, StepFacts
from readablestem.ops.step_report import Measured, report


def call(step: str, seconds: float, usd: float, cached: int = 0, **more) -> CallFact:
    return CallFact(
        step=step,
        model="model-a",
        input_tokens=100,
        cached_input_tokens=cached,
        output_tokens=40,
        seconds=seconds,
        cost_usd=usd,
        **more,
    )


def stages(**seconds: float) -> list[StageFact]:
    return [StageFact(stage=name, seconds=value) for name, value in seconds.items()]


def test_the_report_gives_each_step_and_stage_by_page_and_by_percentile():
    first = Measured(
        pages=2,
        facts=StepFacts(
            calls=[
                call("describe", 1.0, 0.01),
                call("describe", 2.0, 0.02, cached=50),
                call("describe", 10.0, 0.03, cached=100, outcome="timeout"),
                call("second_check", 0.5, 0.004, counted=False),
            ],
            stages=stages(queue_wait=30.0, layout=1.0, steps=3.0),
        ),
        settled_usd=0.07,  # a try that did not finish spent $0.006 more
        counted_calls=3,
        wall_s=4.0,
    )
    second = Measured(
        pages=3,
        facts=StepFacts(calls=[call("describe", 3.0, 0.02)], stages=stages(layout=3.0, steps=9.0)),
        settled_usd=0.02,
        counted_calls=1,
        wall_s=12.0,
    )
    made = report([first, second])

    assert (made.jobs, made.pages, made.calls, made.counted_calls) == (2, 5, 5, 4)
    assert made.usd == pytest.approx(0.084)
    assert made.usd_per_page == pytest.approx(0.0168)
    assert (made.settled_usd, made.settled_calls) == (pytest.approx(0.09), 4)
    assert made.cache_read == pytest.approx(150 / 500)
    # Seconds a page of each job: 2.0 and 4.0. The wait in the queue is in neither.
    assert (made.s_per_page_p50, made.s_per_page_p95) == (2.0, 4.0)
    assert (made.stage_s, made.wall_s) == (16.0, 16.0)

    describe, check = made.steps
    assert (describe.step, describe.calls, describe.failed) == ("describe", 4, 1)
    assert describe.calls_per_page == pytest.approx(0.8)
    assert describe.input_per_page == pytest.approx(80)
    assert describe.cached_per_page == pytest.approx(30)
    assert describe.cache_read == pytest.approx(150 / 400)
    assert (describe.call_s_p50, describe.call_s_p95) == (2.0, 10.0)
    assert describe.call_usd_p99 == 0.03
    assert describe.usd_per_page == pytest.approx(0.016)
    assert (check.step, check.calls, check.cache_read) == ("second_check", 1, 0.0)

    wait, layout, steps = made.stages
    assert (wait.stage, wait.jobs, wait.s_p95, wait.share) == ("queue_wait", 1, 30.0, None)
    assert (layout.jobs, layout.s_p50, layout.s_p95) == (2, 1.0, 3.0)
    assert (layout.s_per_page_p50, layout.s_per_page_p95) == (0.5, 1.0)
    assert layout.share == pytest.approx(0.25)
    assert steps.share == pytest.approx(0.75)


def test_a_report_of_no_jobs_is_empty_and_divides_by_nothing():
    made = report([])
    assert (made.jobs, made.pages, made.usd_per_page, made.calls_per_page) == (0, 0, 0.0, 0.0)
    assert (made.cache_read, made.settled_usd, made.s_per_page_p50, made.wall_s) == (None,) * 4
