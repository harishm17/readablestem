"""What the worker process tells the web process, in `ops_heartbeat` rows.

With `WORKER_MODE=service` the conversions run in the `readablestem worker` process, so the
web process cannot read the worker thread, the model provider's recent requests or the sandbox
probe from its own memory. The worker writes them to rows of `ops_heartbeat` (the table the
backup and cron use, `ops.heartbeat`) and the web process reads them, so `/readyz`, `/status`,
the job page and `/admin` answer the same whichever process serves the request:

- `worker.<slot>`: written every `BEAT_S` by `RowWriter` for each conversion slot. `last_ok_at`
  is when it was written; the note is `{"state": "ok" | "stopping" | "stopped", "seen_s": s}`,
  where `seen_s` is how long ago the slot's loop last came round or a running conversion last
  beat (`Worker.seconds_since_seen`). A row older than `GONE_AFTER_S` belongs to a process that
  is gone.
- `provider`: the last requests' outcomes as `{"calls": [[time, failed], ...]}`
  (`provider_health.ProviderHealth.snapshot`).
- `sandbox`: the probe's `{"state", "checks", "checked_at"}` (`sandbox_status.SandboxStatus`).

A row holds words, numbers and times, never anything from a document. In `inline` mode nothing
here is written or read: the web process's own memory answers, as before.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from readablestem.ops import heartbeat
from readablestem.web.database import as_utc
from readablestem.web.db import OpsHeartbeat, session_scope, utcnow
from readablestem.web.logsafe import log_exception
from readablestem.web.provider_health import HEALTH, ProviderHealth

logger = logging.getLogger(__name__)

WORKER = "worker."  # + the slot number
PROVIDER = "provider"
PROVIDER_WEB = "provider.web"
SANDBOX = "sandbox"
BEAT_S = 10.0
# Three missed beats: the process is gone (or its database writes fail).
GONE_AFTER_S = 3 * BEAT_S
# How long the web process keeps what it read, so a polled page costs a read every few seconds.
READ_TTL_S = 5.0
# A conversion beats every third of a lease (120 s), so a slot not seen for three leases is stuck.
STALLED_AFTER_S = 3 * 120.0


def write(factory: sessionmaker[Session], name: str, detail: dict[str, Any]) -> None:
    with session_scope(factory) as session:
        heartbeat.beat(session, name, json.dumps(detail, separators=(",", ":")))


def _parse(detail: str | None) -> dict[str, Any]:
    try:
        value = json.loads(detail or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def worker_word(factory: sessionmaker[Session], now: datetime | None = None) -> str:
    """One word for `/readyz`, from the slots' rows: "ok" when one is beating and its loop is
    moving, "stalled" when the beating ones are stuck, "stopping" when they are draining,
    "stopped" when the rows are old, "not_started" when there are none."""
    moment = now or utcnow()
    with session_scope(factory) as session:
        rows = session.execute(
            select(OpsHeartbeat.last_ok_at, OpsHeartbeat.detail).where(
                OpsHeartbeat.name.like(WORKER + "%")
            )
        ).all()
    if not rows:
        return "not_started"
    states: set[str] = set()
    for last, detail in rows:
        seen = as_utc(last)
        if seen is None or (moment - seen).total_seconds() > GONE_AFTER_S:
            continue
        note = _parse(detail)
        state = str(note.get("state", "stopped"))
        if state == "ok" and float(note.get("seen_s", 0.0)) > STALLED_AFTER_S:
            state = "stalled"
        states.add(state)
    for word in ("ok", "stalled", "stopping", "stopped"):
        if word in states:
            return word
    return "stopped"


class RowReader:
    """The web process's view of the worker's rows (`worker_mode=service`). The provider and
    sandbox answers are kept `READ_TTL_S`; a database that does not answer gives the last
    answer, or nothing seen."""

    def __init__(self, factory: sessionmaker[Session], ttl_s: float = READ_TTL_S) -> None:
        self._factory = factory
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._kept: dict[str, tuple[float, dict[str, Any] | None]] = {}

    def _row(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            kept = self._kept.get(name)
            if kept is not None and time.monotonic() - kept[0] < self._ttl:
                return kept[1]
        try:
            with session_scope(self._factory) as session:
                detail = session.scalar(
                    select(OpsHeartbeat.detail).where(OpsHeartbeat.name == name)
                )
            value = None if detail is None else _parse(detail)
        except Exception as exc:  # `/status` says "database down" itself
            log_exception(logger, "ops_row_unreadable", exc, row=name)
            value = kept[1] if kept is not None else None
        with self._lock:
            self._kept[name] = (time.monotonic(), value)
        return value

    def provider(self) -> ProviderHealth:
        """The worker's requests and the web process's own (the rereads), in time order."""
        calls = [
            (float(t), bool(failed))
            for name in (PROVIDER, PROVIDER_WEB)
            for t, failed in (self._row(name) or {}).get("calls") or []
        ]
        return ProviderHealth.from_snapshot(sorted(calls))

    def sandbox(self) -> dict[str, Any] | None:
        return self._row(SANDBOX)

    def worker(self) -> str:
        try:
            return worker_word(self._factory)
        except Exception:
            return "unreachable"


def write_web_provider(factory: sessionmaker[Session], health: ProviderHealth = HEALTH) -> None:
    """The web process's side of the provider row: the requests its own rereads sent, in a row
    of their own (the worker rewrites `PROVIDER` whole at each beat). Same content and rules
    as the worker's row. Never raises: the next reread writes it again."""
    try:
        write(factory, PROVIDER_WEB, {"calls": health.snapshot()})
    except Exception as exc:
        log_exception(logger, "ops_rows_not_written", exc)


class RowWriter:
    """The `readablestem worker` process's side: one thread that, every `BEAT_S`, writes a row
    for each slot and the provider's recent requests. Separate from the slots' loops, so a loop
    stuck in a database call shows as `seen_s` growing, not as a missing row."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        workers: list[Any],
        *,
        health: ProviderHealth = HEALTH,
        beat_s: float = BEAT_S,
    ) -> None:
        self._factory = factory
        self._workers = workers
        self._health = health
        self._beat_s = beat_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        """Write every row now. Never raises: a failed write is the next beat's to retry."""
        try:
            for slot, worker in enumerate(self._workers):
                state = "stopping" if worker.draining else ("ok" if worker.alive else "stopped")
                write(
                    self._factory,
                    f"{WORKER}{slot}",
                    {"state": state, "seen_s": round(worker.seconds_since_seen(), 1)},
                )
            write(self._factory, PROVIDER, {"calls": self._health.snapshot()})
        except Exception as exc:
            log_exception(logger, "ops_rows_not_written", exc)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.beat()
            self._stop.wait(self._beat_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="readablestem-rows", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread after one last beat, which says "stopping" or "stopped"."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.beat()


def sandbox_publisher(
    factory: sessionmaker[Session],
) -> Callable[[str, dict[str, str], datetime | None], None]:
    """The function a worker process gives its `SandboxStatus` so each probe result is a row."""

    def publish(state: str, checks: dict[str, str], checked_at: datetime | None) -> None:
        try:
            write(
                factory,
                SANDBOX,
                {
                    "state": state,
                    "checks": checks,
                    "checked_at": checked_at.isoformat() if checked_at else None,
                },
            )
        except Exception as exc:
            log_exception(logger, "ops_rows_not_written", exc)

    return publish
