"""A 429 on one request pauses every request of the process for its retry-after."""

from __future__ import annotations

import threading
import time

import httpx2
import pytest

from readablestem.models import anthropic_client, base
from readablestem.models.base import RateGate, rate_gate
from test_models_anthropic_client import (
    Wire,
    error_body,
    make_request,
    make_settings,
    message_body,
    run,
)


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


def test_nothing_waits_until_somebody_is_refused():
    g, clock = gate()
    assert g.wait() == 0.0 and clock.slept == []


def test_a_pause_makes_the_next_request_wait_it_out_once():
    g, clock = gate()
    g.pause(3.0)
    clock.now += 1.0  # one second of it has passed
    assert g.wait() == pytest.approx(2.0)
    assert g.wait() == 0.0  # the pause is over for everybody
    assert clock.slept == [pytest.approx(2.0)]


def test_the_longest_pause_stays_and_nonsense_is_ignored():
    g, _ = gate()
    g.pause(5.0)
    g.pause(1.0)  # does not shorten it
    g.pause(0.0)
    g.pause(-3.0)
    g.pause(RateGate.MAX_WAIT_S + 1)  # longer than a request would wait anyway
    assert g.remaining() == pytest.approx(5.0)


def test_one_gate_per_provider():
    assert rate_gate("anthropic") is rate_gate("anthropic")
    assert rate_gate("anthropic") is not rate_gate("openai")
    rate_gate("anthropic").pause(5.0)
    assert rate_gate("openai").remaining() == 0.0


def test_a_refusal_pauses_the_other_requests_but_not_the_one_that_waited(monkeypatch):
    """The refused request sleeps its retry-after itself; a request that starts during
    the pause waits for the rest of it before sending."""
    slept: list[float] = []
    monkeypatch.setattr(anthropic_client, "_sleep", slept.append)  # the retry's own wait
    wire = Wire(
        httpx2.Response(429, json=error_body("rate_limit_error"), headers={"retry-after": "7"}),
        message_body(),
    )
    parsed, call = run(wire, make_request(), settings=make_settings(model_max_retries=1))
    assert parsed is not None and call.outcome == "ok" and len(wire.requests) == 2
    g = rate_gate("anthropic")
    assert slept == [7.0]  # the refused request waited its retry-after once, itself
    assert 6.0 < g.remaining() <= 7.0  # and a request that starts now waits the rest


def test_without_a_refusal_no_request_waits(monkeypatch):
    called = []
    monkeypatch.setattr(base.RateGate, "wait", lambda self: called.append(1) or 0.0)
    parsed, _ = run(Wire(message_body()), make_request())
    assert parsed is not None and rate_gate("anthropic").remaining() == 0.0
    assert called  # asked, every time, and nothing was owed


def test_a_server_error_does_not_pause_everybody(monkeypatch):
    monkeypatch.setattr(anthropic_client, "_sleep", lambda s: None)
    wire = Wire(httpx2.Response(500, json=error_body("api_error")), message_body())
    run(wire, make_request(), settings=make_settings(model_max_retries=1))
    assert rate_gate("anthropic").remaining() == 0.0  # only a 429 says "slow down"


def test_threads_that_start_during_a_pause_all_wait_for_it():
    g = RateGate()
    g.pause(0.3)
    started = time.monotonic()
    done: list[float] = []

    def request() -> None:
        g.wait()
        done.append(time.monotonic() - started)

    threads = [threading.Thread(target=request) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(done) == 6 and min(done) >= 0.25
