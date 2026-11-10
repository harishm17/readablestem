"""A 429 on one request pauses every request of the process for its retry-after."""

from __future__ import annotations

import pytest

from readablestem.models.base import RateGate


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def gate() -> tuple[RateGate, Clock]:
    clock = Clock()
    return RateGate(clock=clock, sleep=clock.sleep), clock


def test_a_pause_makes_the_next_request_wait_it_out_once():
    g, clock = gate()
    g.pause(3.0)
    clock.now += 1.0  # one second of it has passed
    assert g.wait() == pytest.approx(2.0)
    assert g.wait() == 0.0  # the pause is over for everybody
    assert clock.slept == [pytest.approx(2.0)]
