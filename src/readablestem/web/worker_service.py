"""`readablestem worker`: the conversion worker with no web server (`WORKER_MODE=service`).

The same `Worker` the web process runs inline, started `WORKER_SLOTS` times in a process of its
own, so a conversion and a request no longer share one process's memory and CPU (a conversion
child is still its own process, `web.isolated_convert`). The database is the only queue and the
only thing the web process and this one share:

- A job is claimed by one conditional UPDATE (`Worker.claim`: `WHERE status = 'queued'`, after
  `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres), so any number of slots and processes can
  poll the same table and each job goes to one of them.
- A job whose worker died is requeued when its lease runs out (`Worker.requeue_expired`, run by
  every slot's loop), the same rule as when the worker was a thread.
- Liveness, the AI service's health and the sandbox probe are rows of `ops_heartbeat`
  (`web.worker_rows`), which `/readyz`, `/status`, the job page and `/admin` read.

SIGTERM or SIGINT: no new job is claimed, a job being converted is cancelled and its lease ends
at once (`Worker.stop`), so another worker takes it up without waiting for the lease; this
gets `DRAIN_S` before the process exits. The host's draining window (`drainingSeconds` of the
worker service, docs/deploy-railway.md) must be longer than that.
"""

from __future__ import annotations

import logging
import signal
import threading

from readablestem.config import Settings
from readablestem.core import sandbox
from readablestem.observability.logging import configure_logging
from readablestem.observability.tracing import NoopTracer, make_tracer
from readablestem.web import page_images, worker_rows
from readablestem.web.database import prepare_database
from readablestem.web.db import make_engine, make_sessionmaker
from readablestem.web.logsafe import log_event
from readablestem.web.sandbox_status import SandboxStatus, run_probe
from readablestem.web.spend import DbSpendGuard, conversion_headroom_usd
from readablestem.web.worker import Worker, default_client_factory, default_converter

logger = logging.getLogger(__name__)

# How long a stopping slot may take to end its job's lease and leave (`Worker.stop`). Shorter
# than the worker service's `drainingSeconds`.
DRAIN_S = 20.0


def run(settings: Settings, *, stop: threading.Event | None = None) -> int:
    """Run `settings.worker_slots` workers until `stop` is set (or SIGTERM / SIGINT arrives
    when `stop` is not given). Returns the exit status."""
    configure_logging(settings)
    settings.check_production()
    engine = make_engine(settings.database_url)
    factory = make_sessionmaker(engine)
    prepare_database(engine, settings)
    page_images.CACHE.configure(settings.page_image_cache_mb * 1024 * 1024)
    guard = DbSpendGuard(
        factory, settings.daily_spend_cap_usd, open_cap_usd=settings.open_tier_daily_spend_cap_usd
    )
    headroom = conversion_headroom_usd(settings)
    tracer = make_tracer(settings)  # as `web.app._job_tracer`: None when tracing is off
    if isinstance(tracer, NoopTracer):
        tracer = None
    sandbox.make_undumpable()  # TeX of the same user cannot read our /proc/<pid>/environ
    if settings.sandbox_probe_at_startup:
        # The probe runs where LaTeXML does: here, not in the web process, which reads the row.
        status = SandboxStatus(publish=worker_rows.sandbox_publisher(factory))
        run_probe(status, timeout_s=settings.sandbox_probe_timeout_s)

    workers = [
        Worker(
            settings,
            factory,
            converter=default_converter(),
            client_factory=default_client_factory,
            guard=guard,
            headroom_usd=headroom,
            tracer=tracer,
            purge=slot == 0,  # the retention backstop once an hour, not once a slot
        )
        for slot in range(settings.worker_slots)
    ]
    rows = worker_rows.RowWriter(factory, workers)
    for worker in workers:
        worker.start()
    rows.start()
    log_event(logger, "worker_service_started", slots=len(workers), env=settings.env)

    own_stop = stop or threading.Event()
    if stop is None and threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: own_stop.set())
    own_stop.wait()

    log_event(logger, "worker_service_stopping")
    stoppers = [
        threading.Thread(target=worker.stop, kwargs={"timeout": DRAIN_S}) for worker in workers
    ]
    for thread in stoppers:
        thread.start()
    for thread in stoppers:
        thread.join()
    rows.stop()
    if tracer is not None:
        tracer.flush()
    engine.dispose()
    log_event(logger, "worker_service_stopped")
    return 0
