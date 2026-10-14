"""Is the model provider answering? (T3-1)

When the AI service is down, conversions finish with their equations and figures "not
converted", and the person is left to wonder whether the file or the tool is at fault.
This module answers from what the worker already sees: the outcome of each request it
sends (`ModelCall.outcome`), kept for a few minutes in memory.

The service counts as **not answering** when at least `MIN_CALLS` requests were sent in
the last `WINDOW_S` seconds and the most recent `MIN_CALLS` all ended in `api_error` or
`timeout`. A refusal or an unusable answer is the model's, not an outage, and a single
failure among successes is noise. One success ends it at once. Per process (the worker
runs inside the web process), forgotten on restart: it is a hint for a status line, never
a gate, and it holds only times and outcome words, nothing from a document.

The converter reports each request's `ModelCall` (`convert_pdf(call_observer=)`, passed by
the worker); the public `/status` page and the job page read `HEALTH`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

WINDOW_S = 600.0
MIN_CALLS = 5
KEEP = 50
FAILURES = frozenset({"api_error", "timeout"})
# Outcomes that say nothing about the service being up: the request was never sent, or
# the answer was the model's own (a refusal, unusable output).
IGNORED = frozenset({"budget_exceeded", "skipped"})


class ProviderHealth:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: deque[tuple[float, bool]] = deque(maxlen=KEEP)

    def record(self, outcome: str) -> None:
        """One request ended with `outcome` (a `ModelCall.outcome`)."""
        if outcome in IGNORED:
            return
        with self._lock:
            self._calls.append((self._clock(), outcome in FAILURES))

    def _recent(self) -> list[tuple[float, bool]]:
        cutoff = self._clock() - WINDOW_S
        return [c for c in self._calls if c[0] >= cutoff]

    def is_down(self) -> bool:
        with self._lock:
            recent = self._recent()
        return len(recent) >= MIN_CALLS and all(failed for _, failed in recent[-MIN_CALLS:])

    def down_since(self) -> datetime | None:
        """When the run of failures that makes the service "not answering" began (the first
        of the unbroken run, at most `WINDOW_S` ago), else None."""
        with self._lock:
            recent = self._recent()
            if len(recent) < MIN_CALLS or not all(f for _, f in recent[-MIN_CALLS:]):
                return None
            started = recent[-MIN_CALLS][0]
            for at, failed in reversed(recent):
                if not failed:
                    break
                started = at
        return datetime.fromtimestamp(started, UTC)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


# One per process: the worker thread writes, the pages read.
HEALTH = ProviderHealth()
