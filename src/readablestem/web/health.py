"""Readiness (`/readyz`) and the SIGTERM hook for Cloud Run.

`/healthz` says the process answers and the database does. `/readyz` says the service can
do its job: the database answers, the worker thread is alive and not stuck, and the model
provider is set up. Each check is one word; nothing in the answer comes from a setting's
value, so a key or a connection string can never end up in it.

Cloud Run sends SIGTERM and stops the process 10 seconds later. uvicorn reacts by closing
its listeners and waiting for connections, and only then runs the lifespan shutdown that
stops the worker, so a job could be claimed (and model calls paid for) after the signal.
`stop_worker_on_sigterm` puts a handler in front of uvicorn's that stops the worker at once.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from types import FrameType

from readablestem.config import Settings
from readablestem.web.database import ping
from readablestem.web.deps import AppContext
from readablestem.web.logsafe import log_event

logger = logging.getLogger(__name__)

# Three leases: a conversion beats every third of one, so this allows two missed beats.
STALLED_AFTER_S = 3 * 120.0


def readiness(ctx: AppContext) -> tuple[bool, dict[str, str]]:
    """(ready, one word per check). `ctx` is the web `AppContext`."""
    checks = {
        "database": "ok" if ping(ctx.engine) else "unreachable",
        "worker": _worker_check(ctx),
        "model_provider": _provider_check(ctx.settings),
    }
    return all(v in {"ok", "disabled", "fake"} for v in checks.values()), checks


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
