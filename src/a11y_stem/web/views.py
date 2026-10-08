"""Read-only view models for templates: plain data, no ORM objects past the session.

Error codes are categories; the user-facing sentences for them live here so templates and
tests share one wording.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from a11y_stem.core.types import DocumentResult, Kind, Status
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import Job, JobStatus

STATUS_LABELS: dict[str, str] = {
    JobStatus.QUEUED.value: "Waiting",
    JobStatus.RUNNING.value: "Converting",
    JobStatus.DONE.value: "Finished",
    JobStatus.FAILED.value: "Stopped",
}

FIX_STATUS_LABELS = {
    Status.ACCEPTED: "Accepted",
    Status.NEEDS_REVIEW: "Needs review",
    Status.ABSTAINED: "Not converted (no confident proposal)",
    Status.FAILED: "Not converted (error)",
    Status.EDITED: "Edited by you",
    Status.REJECTED: "Rejected: original kept",
}

KIND_LABELS = {
    Kind.EQUATION: "Equation",
    Kind.FIGURE: "Figure",
    Kind.HEADING: "Heading",
    Kind.PARAGRAPH: "Paragraph",
}

JOB_ERROR_MESSAGES = {
    "budget_daily": "Today's conversion budget ran out while this document was being "
    "converted. Please upload it again tomorrow.",
    "budget_job_usd": "This document needed more model work than one conversion is allowed. "
    "Try splitting it into smaller parts.",
    "budget_job_calls": "This document needed more model calls than one conversion is "
    "allowed. Try splitting it into smaller parts.",
    "encrypted": "The PDF is password-protected.",
    "unreadable": "The PDF could not be read.",
    "too_many_pages": "The PDF has too many pages.",
    "no_pages": "The PDF has no pages.",
    "lease_expired": "The conversion was interrupted several times and has been stopped.",
    "upload_missing": "The uploaded file was no longer available.",
    "internal_error": "Something went wrong on our side. The error was recorded without any "
    "of your document's content.",
}


def job_error_message(code: str | None) -> str:
    return JOB_ERROR_MESSAGES.get(code or "", "The conversion stopped unexpectedly.")


@dataclass
class ResultSummary:
    pages: int
    by_kind: dict[str, int]
    needs_review: int
    accepted: int
    not_converted: int
    reviewed: int
    cost_usd: float


@dataclass
class JobView:
    id: str
    filename: str
    status: str
    page_count: int
    pages_done: int
    created_at: datetime | None
    finished_at: datetime | None
    retain_until: datetime | None
    opt_in: bool
    error_code: str | None
    cost_usd: float
    model_calls: int
    queue_ahead: int = 0
    summary: ResultSummary | None = None

    @property
    def finished(self) -> bool:
        return self.status in {JobStatus.DONE, JobStatus.FAILED}

    @property
    def version(self) -> str:
        """Changes whenever the status partial would look different (htmx polling)."""
        return f"{self.status}-{self.pages_done}-{self.queue_ahead}"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_detail(self) -> str:
        if self.status == JobStatus.QUEUED:
            if self.queue_ahead == 0:
                return "Your document is next in line."
            noun = "document" if self.queue_ahead == 1 else "documents"
            return f"{self.queue_ahead} {noun} ahead of yours."
        if self.status == JobStatus.RUNNING:
            return f"{self.pages_done} of {self.page_count} pages done."
        if self.status == JobStatus.DONE:
            return "The accessible HTML is ready."
        return job_error_message(self.error_code)


def summarise(result_json: dict[str, Any] | None) -> ResultSummary | None:
    if not result_json:
        return None
    result = DocumentResult.model_validate(result_json)
    statuses = Counter(f.status for f in result.fixes)
    return ResultSummary(
        pages=result.stats.pages,
        by_kind=dict(Counter(f.kind.value for f in result.fixes)),
        needs_review=statuses[Status.NEEDS_REVIEW],
        accepted=statuses[Status.ACCEPTED],
        not_converted=statuses[Status.ABSTAINED] + statuses[Status.FAILED],
        reviewed=statuses[Status.EDITED] + statuses[Status.REJECTED],
        cost_usd=result.stats.cost_usd,
    )


def job_view(session: Session, job: Job, *, with_summary: bool = False) -> JobView:
    ahead = 0
    if job.status == JobStatus.QUEUED:
        ahead = int(
            session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.status == JobStatus.QUEUED, Job.created_at < job.created_at)
            )
            or 0
        )
    return JobView(
        id=job.id,
        filename=job.filename,
        status=str(job.status),
        page_count=job.page_count,
        pages_done=job.pages_done,
        created_at=as_utc(job.created_at),
        finished_at=as_utc(job.finished_at),
        retain_until=as_utc(job.retain_until),
        opt_in=job.opt_in_retention,
        error_code=job.error_code,
        cost_usd=job.cost_usd,
        model_calls=job.model_calls,
        queue_ahead=ahead,
        summary=summarise(job.result_json) if with_summary else None,
    )
