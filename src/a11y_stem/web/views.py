"""Read-only view models for templates: plain data, no ORM objects past the session.

Error codes are categories; the user-facing sentences for them live here so templates and
tests share one wording.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from a11y_stem.core import triage
from a11y_stem.core.types import DocumentResult, Fix, Kind, Status
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import Job, JobStatus, User
from a11y_stem.web.messages import Explained, explain_check, severity_label
from a11y_stem.web.outcome import FAILED_MESSAGES

STATUS_LABELS: dict[str, str] = {
    JobStatus.QUEUED.value: "Waiting",
    JobStatus.RUNNING.value: "Converting",
    JobStatus.DONE.value: "Finished",
    JobStatus.FAILED.value: "Stopped",
}

FIX_STATUS_LABELS = {
    Status.ACCEPTED: "Accepted automatically",
    Status.NEEDS_REVIEW: "Needs review",
    Status.ABSTAINED: "Not converted (no confident proposal)",
    Status.FAILED: "Not converted (error)",
    Status.EDITED: "Edited by you",
    Status.REJECTED: "Rejected: original kept",
}
# A reviewer's Accept shares Status.ACCEPTED with the pipeline's (Fix.reviewed differs).
REVIEWER_ACCEPTED_LABEL = "Accepted by you"


def fix_status_label(fix: Fix) -> str:
    if fix.accepted_by_reviewer:
        return REVIEWER_ACCEPTED_LABEL
    return FIX_STATUS_LABELS.get(fix.status, fix.status.value)


KIND_LABELS = {
    Kind.EQUATION: "Equation",
    Kind.FIGURE: "Figure",
    Kind.HEADING: "Heading",
    Kind.PARAGRAPH: "Paragraph",
    Kind.LIST_ITEM: "List item",
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
    "timeout": "The conversion took longer than a document of this size is allowed and was "
    "stopped. Try splitting it into smaller parts.",
    "store_failed": "The conversion finished but its results could not be saved. Please "
    "upload the document again.",
    "upload_missing": "The uploaded file was no longer available.",
    "internal_error": "Something went wrong on our side. The error was recorded without any "
    "of your document's content.",
    # Codes with a longer explanation of what to do next (scanned PDFs, no model key,
    # removed access, ...) share their wording with the status box.
    **FAILED_MESSAGES,
}


def job_error_message(code: str | None) -> str:
    return JOB_ERROR_MESSAGES.get(code or "", "The conversion stopped unexpectedly.")


@dataclass
class CheckLine:
    """One failed check of the output HTML, as the job page lists it."""

    text: str  # what it means (web.messages)
    code: str  # "name: code"
    severity: str  # "Must be fixed" or "Worth checking"


@dataclass
class ResultSummary:
    pages: int
    by_kind: dict[str, int]  # fixes per kind
    needs_review: int
    accepted: int  # accepted automatically by the pipeline
    not_converted: int
    reviewed: int  # accepted, edited or rejected by the reviewer
    # Decisions still waiting (what "done" waits for): every fix waiting for a decision
    # (needs review, failed, not converted), a group of repeated headings counted once.
    # Per kind ("equation" -> 2) for the links into the review screen's groups.
    decisions: int = 0
    review_by_kind: dict[str, int] = field(default_factory=dict)
    review_labels: dict[str, str] = field(default_factory=dict)  # "1 group of headings"
    # Per kind, the first decision still open in the review screen's order (an element id
    # or a heading group's key), for links straight to it.
    review_first: dict[str, str] = field(default_factory=dict)
    # Accepted automatically with an optional note nobody has looked at: never counted
    # toward "done".
    optional: int = 0
    # ... of which the second automated check's (a possible alt text mismatch), which
    # the page calls worth a look rather than "nothing to do".
    second_check: int = 0
    list_items: int = 0  # list item elements (no fixes: lists are rebuilt as they are)
    # Static checks of the stored HTML (re-run after every review decision): how many
    # ran, and the ones that failed. `checks_run` is 0 for results stored without them.
    checks_run: int = 0
    failed_checks: list[CheckLine] = field(default_factory=list)


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


DECISION_NOUNS = {
    Kind.EQUATION: ("equation", "equations"),
    Kind.FIGURE: ("figure", "figures"),
    Kind.HEADING: ("heading", "headings"),
}


def decisions_label(units: list[list[Fix]], kind: Kind) -> str:
    """'2 equations', '1 group of repeated headings', '3 headings and 1 group of
    repeated headings': decisions of one kind (a heading group is one decision)."""
    one, many = DECISION_NOUNS[kind]
    groups = sum(1 for unit in units if unit[0].group_key)
    singles = len(units) - groups
    parts = [f"{singles} {one if singles == 1 else many}"] if singles else []
    if groups:
        parts.append(f"{groups} {'group' if groups == 1 else 'groups'} of repeated {many}")
    return " and ".join(parts)


def _check_line(explained: Explained, severity: str) -> CheckLine:
    return CheckLine(explained.text, explained.code, severity)


def summarise(result_json: dict[str, Any] | None) -> ResultSummary | None:
    if not result_json:
        return None
    result = DocumentResult.model_validate(result_json)
    statuses = Counter(f.status for f in result.fixes)
    waiting = triage.units(f for f in result.fixes if f.pending)
    failed = [c for c in result.html_checks if not c.passed]
    return ResultSummary(
        pages=result.stats.pages,
        by_kind=dict(Counter(f.kind.value for f in result.fixes)),
        needs_review=statuses[Status.NEEDS_REVIEW],
        accepted=sum(f.status is Status.ACCEPTED and not f.reviewed for f in result.fixes),
        not_converted=statuses[Status.ABSTAINED] + statuses[Status.FAILED],
        reviewed=sum(f.decided_by_reviewer for f in result.fixes),
        decisions=triage.open_decisions(result.fixes),
        review_by_kind=dict(Counter(unit[0].kind.value for unit in waiting)),
        review_labels={
            kind.value: decisions_label([u for u in waiting if u[0].kind is kind], kind)
            for kind in DECISION_NOUNS
            if any(u[0].kind is kind for u in waiting)
        },
        review_first=_first_open_by_kind(result),
        optional=sum(f.optional_note for f in result.fixes),
        second_check=sum(triage.second_check_note(f) for f in result.fixes),
        list_items=sum(e.kind is Kind.LIST_ITEM for e in result.elements),
        checks_run=len(result.html_checks),
        failed_checks=[_check_line(explain_check(c), severity_label(c)) for c in failed],
    )


def _first_open_by_kind(result: DocumentResult) -> dict[str, str]:
    from a11y_stem.web import queue

    first: dict[str, str] = {}
    for decision in queue.decisions(result):
        if decision.open:
            first.setdefault(decision.kind.value, decision.key)
    return first


def queue_ahead(session: Session, job: Job) -> int:
    """Queued jobs the worker will claim before this one: the same order as
    `Worker.claim` (fewest attempts, then oldest, then id), and like it only jobs whose
    owner is still allowed."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(Job)
            .join(User, User.id == Job.user_id)
            .where(
                Job.status == JobStatus.QUEUED,
                User.is_allowed.is_(True),
                tuple_(Job.attempts, Job.created_at, Job.id)
                < tuple_(job.attempts, job.created_at, job.id),
            )
        )
        or 0
    )


def job_view(session: Session, job: Job, *, with_summary: bool = False) -> JobView:
    ahead = queue_ahead(session, job) if job.status == JobStatus.QUEUED else 0
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
