"""Readiness (`/readyz`) and the SIGTERM hook.

`/healthz/ops` says the scheduled tasks ran: the backup within 26 hours, the cron within 2. It
answers with the status word alone; which task is late, the storage, the sandbox probe and the
production profile's hash are the admin's (`ops_report`, `/admin/health`, `/admin`).
`/healthz` says the process answers and the database does. `/readyz` says the service can
do its job: the database answers and takes a write within a second, the worker thread is
alive and not stuck, and the model provider is set up. Each check is one word; nothing in the
answer comes from a setting's value, so a key or a connection string can never end up in it.

The host sends SIGTERM and later kills the process. On Railway the gap is
`RAILWAY_DEPLOYMENT_DRAINING_SECONDS`, 10 seconds for `web` (.railway/railway.ts). uvicorn
reacts by closing its listeners and waiting for connections, and only then runs the lifespan
shutdown that stops the worker, so a job could be claimed (and model calls paid for) after
the signal. `stop_worker_on_sigterm` puts a handler in front of
uvicorn's that stops the worker at once.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from datetime import timedelta
from types import FrameType

from sqlalchemy import select, text, update

from readablestem.config import Settings
from readablestem.ops import canary, heartbeat, profile
from readablestem.web import storage
from readablestem.web.database import ping
from readablestem.web.db import ErrorAggregate, OpsHeartbeat, session_scope, utcnow
from readablestem.web.deps import AppContext
from readablestem.web.logsafe import log_event

logger = logging.getLogger(__name__)

# Three leases: a conversion beats every third of one, so this allows two missed beats.
STALLED_AFTER_S = 3 * 120.0


def readiness(ctx: AppContext) -> tuple[bool, dict[str, str]]:
    """(ready, one word per check). `ctx` is the web `AppContext`."""
    checks = {
        "database": "ok" if ping(ctx.engine) else "unreachable",
        "database_write": _write_check(ctx.engine),
        "worker": _worker_check(ctx),
        "model_provider": _provider_check(ctx.settings),
    }
    return all(v in {"ok", "disabled", "fake"} for v in checks.values()), checks


# A write that waits longer than this says the database cannot take writes right now: another
# connection holds the write lock (SQLite), or the database is read-only or stuck (Postgres).
WRITE_CHECK_MS = 1000
_WRITE_PROBE = "readyz"  # a name no task writes: the check updates it, finds no row, rolls back


def _write_check(engine) -> str:
    """ "ok" when a write is accepted within `WRITE_CHECK_MS`, else "failed". The write is an
    UPDATE of a row that does not exist, rolled back: it takes the write lock like any write and
    changes nothing. A GET page works while every write waits (a second connection holding the
    lock), so `/readyz` has to try one."""
    try:
        with engine.connect() as conn:
            dialect = conn.dialect.name
            previous = None
            if dialect == "sqlite":
                previous = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
                conn.exec_driver_sql(f"PRAGMA busy_timeout={WRITE_CHECK_MS}")
            elif dialect == "postgresql":
                conn.execute(text(f"SET LOCAL statement_timeout = {WRITE_CHECK_MS}"))
            try:
                conn.execute(
                    update(OpsHeartbeat)
                    .where(OpsHeartbeat.name == _WRITE_PROBE)
                    .values(detail=None)
                )
            finally:
                conn.rollback()
                if previous is not None:
                    conn.exec_driver_sql(f"PRAGMA busy_timeout={int(previous)}")
    except Exception:
        return "failed"
    return "ok"


def canary_days(session, days: int = 7) -> dict[str, dict[str, str] | None]:
    """The latest result of each daily canary (`ops.canary.run_daily`) in the last `days` days:
    {"decisions": {"day": ..., "status": ...}, "claude": ...}, None for one with no result (it
    is off here, or has not run). Read from the day's counts, so it is the same whichever of the
    HTTP cron route and `ops cron` ran it."""
    since = (utcnow() - timedelta(days=days)).date()
    rows = session.execute(
        select(ErrorAggregate.day, ErrorAggregate.category)
        .where(ErrorAggregate.step == canary.CANARY_STEP, ErrorAggregate.day >= since)
        .order_by(ErrorAggregate.day.desc())
    ).all()
    found: dict[str, dict[str, str] | None] = {"decisions": None, "claude": None}
    for day, category in rows:
        kind, _, status = category.partition(":")
        if kind in found and found[kind] is None and status and status != "run":
            found[kind] = {"day": day.isoformat(), "status": status}
    return found


def ops_freshness(ctx: AppContext) -> tuple[bool, list[str]]:
    """(fresh, stale names): the backup ran in the last 26 hours and the cron in the last 2
    (`ops.heartbeat`). Names only. A database that does not answer is reported as
    "database", so the outside check cannot pass on a failed read."""
    try:
        with session_scope(ctx.factory) as session:
            stale = heartbeat.stale_names(session)
            full = storage.global_full(session, ctx.settings)
    except Exception:
        return False, ["database"]
    if full:  # new uploads are being refused until the purge has caught up (`web.storage`)
        stale = sorted([*stale, "storage"])
    return not stale, stale


def ops_report(ctx: AppContext) -> tuple[bool, dict[str, object]]:
    """`(fresh, details)` for the admin: the status word, the stale names, the storage
    report, the daily canaries' latest results, the sandbox probe and the production profile's
    hash. None of it is public (`/healthz/ops` gives the word only)."""
    ok, stale = ops_freshness(ctx)
    canaries: dict[str, dict[str, str] | None] | None
    try:
        with session_scope(ctx.factory) as session:
            stored: dict[str, int | bool] | None = storage.report(session, ctx.settings)
            canaries = canary_days(session)
    except Exception:
        stored, canaries = None, None
    details: dict[str, object] = {
        "status": "ok" if ok else "stale",
        "stale": stale,
        "storage": stored,
        "canaries": canaries,
        "sandbox": ctx.sandbox.public(),
        # The production profile's hash (ops/profile.py): it names no setting and no value,
        # but it says which settings run, so it is the admin's, not the public route's.
        "profile": profile.load().short_hash,
    }
    return ok, details


def _worker_check(ctx: AppContext) -> str:
    if not ctx.settings.run_worker:
        return "disabled"  # another process runs the jobs
    worker = ctx.worker
    if worker is None:
        return "not_started"
    if worker.draining:
        return "stopping"
    if not worker.alive:
        return "stopped"
    return "stalled" if worker.seconds_since_seen() > STALLED_AFTER_S else "ok"


def _provider_check(settings: Settings) -> str:
    """The selected provider has a key and every role model has a price. The word never
    includes the key or a model name."""
    from readablestem.models.pricing import PRICES

    if settings.fake_models:
        return "fake"
    key = settings.provider_key()
    if key is None or not key.get_secret_value().strip():
        return "no_key"
    if settings.main_model not in PRICES or settings.cheap_model not in PRICES:
        return "unpriced_model"
    return "ok"


def stop_worker_on_sigterm(worker) -> Callable[[], None]:
    """Make SIGTERM stop `worker` at once, then carry on with the handler it replaced
    (uvicorn's, which begins its own shutdown). Returns a function that puts the old
    handler back. Does nothing off the main thread (tests), where signals cannot be set."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = signal.getsignal(signal.SIGTERM)

    def handler(signum: int, frame: FrameType | None) -> None:
        log_event(logger, "sigterm_received")
        worker.begin_shutdown()
        if callable(previous):
            previous(signum, frame)
        elif previous == signal.SIG_DFL:  # nothing else was listening: exit as usual
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.raise_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, handler)

    def restore() -> None:
        if signal.getsignal(signal.SIGTERM) is handler:
            signal.signal(signal.SIGTERM, previous)

    return restore
