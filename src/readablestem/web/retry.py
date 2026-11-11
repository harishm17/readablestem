"""Trying a stopped or partly converted document again (T3-1).

A conversion can stop for reasons that are nobody's fault: the process restarted, the time
limit passed, the day's model budget ran out part way, or the AI service did not answer for
a third of the document. The person should not have to upload the file again, lose the
decisions they made, or pay for the pages a second time.

"Try again" puts the *same* job back in the queue (`start`):

- the upload is not read again by the quota: pages are counted when a file is uploaded, and
  this is not an upload;
- the finished model steps are kept (`web.step_cache`), so the second run asks the model
  only for what was not finished, and spends only on that;
- the decisions made on a partly converted result are kept (`worker.Worker._carry_decisions`
  puts them on the new result, item by item, as for a revised upload);
- every limit still holds: the daily budget (a queued job waits for it), the job's own
  spend caps (they cover all tries together), at most `MAX_ACTIVE_JOBS_PER_USER` documents
  waiting or converting, at most `MAX_RETRIES_PER_JOB` tries, and the per-person and
  per-address rate limits on the route.

Which stops can be tried again is decided by the job's state (`web.states`), once, for the
message, the page that shows the button and the route that does the work (`offer`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from readablestem.web import states
from readablestem.web.db import Job, JobStatus

# The tries and the codes worth trying again are the state table's (`web.states`): the
# message, the offer and this route read the same rows.
MAX_RETRIES_PER_JOB = states.MAX_RETRIES_PER_JOB
RETRYABLE_FAILURES = states.RETRYABLE_FAILURES
RETRYABLE_STOPS = states.RETRYABLE_STOPS


@dataclass(frozen=True)
class Offer:
    """The button on the job page: what it says and what it will and will not do."""

    label: str
    explain: str
    tries_left: int


def offer(
    *, status: str, error_code: str | None, result_json: dict[str, Any] | None, retries: int
) -> Offer | None:
    """The "Try again" offer for a job, or None when trying again cannot help (a file this
    tool cannot convert, no tries left, or the job is not stopped). The job's state says it
    (`states.State.retry`), so the offer and the stop message cannot disagree."""
    facts = states.Facts(
        id="", status=status, error_code=error_code, result_json=result_json, retries=retries
    )
    step = states.state_of(facts).retry
    if step is None:
        return None
    return Offer(step.label, step.explain, states.tries_left(retries))


class RetryRefused(Exception):
    """The job cannot be tried again now. `code` is a category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def start(job: Job, *, active_others: int, max_active: int) -> None:
    """Put `job` back in the queue (the caller holds the row's lock and commits).

    Raises `RetryRefused`: "not_stopped" (nothing to try again), "no_tries_left" or
    "too_many_active" (the person already has `max_active` documents waiting or converting).
    """
    if (
        offer(
            status=str(job.status),
            error_code=job.error_code,
            result_json=job.result_json,
            retries=job.retries or 0,
        )
        is None
    ):
        refused = "no_tries_left" if (job.retries or 0) >= MAX_RETRIES_PER_JOB else "not_stopped"
        raise RetryRefused(refused)
    if active_others >= max_active:
        raise RetryRefused("too_many_active")
    job.status = JobStatus.QUEUED
    job.error_code = None
    job.error_detail = None
    job.finished_at = None
    job.lease_until = None
    job.pages_done = 0
    job.progress_json = None
    job.retries = (job.retries or 0) + 1
    job.attempts_at_retry = job.attempts or 0
