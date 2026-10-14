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

Which stops can be tried again is decided here, once (`offer`), for the page that shows the
button and for the route that does the work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from readablestem.web.db import Job, JobStatus
from readablestem.web.outcome import is_outage

# Tries a person may ask for on one job. Each one costs only what was not finished before,
# but a job that fails the same way four times is not going to be helped by a fifth.
MAX_RETRIES_PER_JOB = 3

# Failed jobs worth trying again: stopped by something that is not about the file.
# `model_not_configured` is here for the day the person who runs the site has set the key.
RETRYABLE_FAILURES = frozenset(
    {
        "timeout",
        "lease_expired",
        "store_failed",
        "internal_error",
        "model_not_configured",
        "budget_daily",
    }
)
# Finished jobs worth trying again: stopped early by the day's budget, or converted while
# the AI service was down (read from the result).
RETRYABLE_STOPS = frozenset({"budget_daily"})


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
    tool cannot convert, no tries left, or the job is not stopped)."""
    if retries >= MAX_RETRIES_PER_JOB:
        return None
    left = MAX_RETRIES_PER_JOB - retries
    if status == JobStatus.FAILED and error_code in RETRYABLE_FAILURES:
        return Offer(
            "Try again",
            "Your document is queued again. Nothing is counted against your daily pages, "
            "and anything that was already converted is kept.",
            left,
        )
    if status == JobStatus.DONE and (error_code in RETRYABLE_STOPS or is_outage(result_json)):
        return Offer(
            "Convert the missing parts",
            "Only the parts that were not converted are sent again. Nothing is counted "
            "against your daily pages, and your decisions are kept.",
            left,
        )
    return None


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
    job.finished_at = None
    job.lease_until = None
    job.pages_done = 0
    job.progress_json = None
    job.retries = (job.retries or 0) + 1
    job.attempts_at_retry = job.attempts or 0
