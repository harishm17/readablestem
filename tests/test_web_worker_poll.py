"""What the worker's loop asks of the database when nothing is going on, and what it asks
before it claims a job (T3-28): one read for an idle poll, no write."""

from __future__ import annotations

from datetime import timedelta

from readablestem.web.db import utcnow
from test_web_worker import env, queue_job  # noqa: F401  (the fixture and the helper)


def test_a_poll_with_a_queued_job_claims_it_and_an_expired_lease_is_still_requeued(env):
    job_id = queue_job(env)
    worker = env.worker(lease_s=60)
    claim = worker.claim()
    assert claim is not None and claim.job_id == job_id
    later = utcnow() + timedelta(minutes=5)
    assert worker.pending(later) == (False, True)  # running, its lease run out
    assert worker.requeue_expired(later) == (1, 0)
    assert worker.pending() == (True, False)
