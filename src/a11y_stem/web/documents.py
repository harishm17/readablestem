"""My documents as a to-do list (UX review BW5): what needs a decision first, then what is
ready to download, then the rest, each document under its own title.

- "Needs your decisions": finished, with decisions still open. Oldest first: it is a
  queue of work, and the oldest is deleted first. "Review next" opens the first one's
  review screen, which starts at its first open decision.
- "Ready to download": finished with no decision open, newest first. A result that is
  only partly converted (a budget stop, scanned pages) says so in its status.
- "Converting, waiting or stopped": newest first, as uploaded.

A document is shown by its title (the PDF's title or its first heading, as in the HTML's
`<title>`), with the file name under it; before it is converted, or when the PDF had no
title, by its file name. Titles and file names are document-derived: shown to their
owner only, never logged.

Only what the list needs is read from the stored results, through JSON paths (the title
and the fixes, not the elements or the HTML), and only for finished jobs. While anything
is waiting or converting, htmx polls `GET /app/documents`; `list_version` says whether
anything changed from the jobs' own columns alone, so an unchanged list costs one small
query and a 204.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from a11y_stem.core import triage
from a11y_stem.core.types import Fix
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import Job, JobStatus
from a11y_stem.web.outcome import job_outcome

# The assembler's title for a PDF with no title and no level-1 heading: the file name
# says more.
FALLBACK_TITLE = "Converted document"


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


@dataclass
class DocumentRow:
    id: str
    title: str  # what the list shows: the document's title, else its file name
    filename: str
    pages: int
    pages_done: int
    status: str  # a JobStatus value
    created_at: datetime | None
    retain_until: datetime | None
    label: str = ""  # the status in words
    badge: str = ""  # status-<badge> (app.css): the colour only repeats the words
    open_decisions: int = 0
    partial: bool = False

    @property
    def group(self) -> str:
        if self.status != JobStatus.DONE:
            return "other"
        return "decide" if self.open_decisions else "ready"

    @property
    def action(self) -> str | None:
        """The one next step offered in the row: "review" or "download"."""
        return {"decide": "review", "ready": "download"}.get(self.group)


@dataclass
class Group:
    key: str
    heading: str
    rows: list[DocumentRow] = field(default_factory=list)


@dataclass
class DocumentList:
    groups: list[Group]
    version: str

    @property
    def rows(self) -> list[DocumentRow]:
        return [row for group in self.groups for row in group.rows]

    def group(self, key: str) -> Group:
        return next(g for g in self.groups if g.key == key)

    @property
    def unfinished(self) -> int:
        return sum(row.status in {JobStatus.QUEUED, JobStatus.RUNNING} for row in self.rows)

    @property
    def review_next(self) -> DocumentRow | None:
        rows = self.group("decide").rows
        return rows[0] if rows else None

    @property
    def summary(self) -> str:
        """'2 need your decisions (5 in all) · 1 ready to download · 1 converting'."""
        decide, ready = self.group("decide").rows, self.group("ready").rows
        parts = []
        if decide:
            total = sum(row.open_decisions for row in decide)
            verb = "needs" if len(decide) == 1 else "need"
            parts.append(
                f"{plural(len(decide), 'document', 'documents')} {verb} your decisions "
                f"({plural(total, 'decision', 'decisions')} in all)"
            )
        if ready:
            parts.append(f"{len(ready)} ready to download")
        converting = sum(row.status == JobStatus.RUNNING for row in self.rows)
        waiting = sum(row.status == JobStatus.QUEUED for row in self.rows)
        stopped = sum(row.status == JobStatus.FAILED for row in self.rows)
        if converting:
            parts.append(f"{converting} converting")
        if waiting:
            parts.append(f"{waiting} waiting")
        if stopped:
            parts.append(f"{stopped} stopped")
        return " · ".join(parts)

    @property
    def page_title(self) -> str:
        """The tab title: "My documents (4 of 6 finished)" while some are not."""
        if not self.unfinished:
            return "My documents"
        total = len(self.rows)
        return f"My documents ({total - self.unfinished} of {total} finished)"


def display_title(title: object, filename: str) -> str:
    """The stored result's title, else the file name."""
    if isinstance(title, str) and title.strip() and title.strip() != FALLBACK_TITLE:
        return " ".join(title.split())
    return filename


def _fixes(fixes: object) -> list[Fix]:
    """A stored result's fixes; none when this version cannot read them."""
    if not isinstance(fixes, list):
        return []
    try:
        return [Fix.model_validate(f) for f in fixes if isinstance(f, dict)]
    except ValidationError:
        return []


def open_decisions(fixes: object) -> int:
    """Decisions still open in a stored result's fixes (a heading group is one)."""
    return triage.open_decisions(_fixes(fixes))


def decided_by_you(fixes: object) -> int:
    """Decisions a reviewer made in a stored result's fixes (a heading group is one)."""
    return len(triage.units(f for f in _fixes(fixes) if f.decided_by_reviewer))


def _live(user_id: str, now: datetime):
    # Expired jobs count as deleted before the purge removes them (`routes.jobs.owned_job`).
    return (Job.user_id == user_id, Job.retain_until > now)


def list_version(session: Session, user_id: str, *, now: datetime, budget_closed: bool) -> str:
    """Changes whenever the list would look different, from the jobs' columns alone."""
    rows = session.execute(
        select(Job.id, Job.status, Job.pages_done, Job.result_version, Job.error_code)
        .where(*_live(user_id, now))
        .order_by(Job.id)
    ).all()
    raw = repr([tuple(row) for row in rows] + [budget_closed])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def documents(
    session: Session, user_id: str, *, now: datetime, budget_closed: bool
) -> DocumentList:
    jobs = session.execute(
        select(
            Job.id,
            Job.filename,
            Job.status,
            Job.page_count,
            Job.pages_done,
            Job.error_code,
            Job.created_at,
            Job.retain_until,
        )
        .where(*_live(user_id, now))
        .order_by(Job.created_at.desc(), Job.id)
    ).all()
    done = [job.id for job in jobs if job.status == JobStatus.DONE]
    stored: dict[str, tuple[Any, Any]] = {}
    if done:
        query = select(
            Job.id, Job.result_json["title"].as_string(), Job.result_json["fixes"]
        ).where(Job.id.in_(done))
        stored = {job_id: (title, fixes) for job_id, title, fixes in session.execute(query)}
    groups = [
        Group("decide", "Needs your decisions"),
        Group("ready", "Ready to download"),
        Group("other", "Converting, waiting or stopped"),
    ]
    by_key = {g.key: g for g in groups}
    for job in jobs:
        title, fixes = stored.get(job.id, (None, None))
        row = DocumentRow(
            id=job.id,
            title=display_title(title, job.filename),
            filename=job.filename,
            pages=job.page_count,
            pages_done=job.pages_done,
            status=str(job.status),
            created_at=as_utc(job.created_at),
            retain_until=as_utc(job.retain_until),
            open_decisions=open_decisions(fixes),
        )
        _label(row, job.error_code, budget_closed)
        by_key[row.group].rows.append(row)
    by_key["decide"].rows.reverse()  # oldest first (module docstring)
    version = list_version(session, user_id, now=now, budget_closed=budget_closed)
    return DocumentList(groups=groups, version=version)


def _label(row: DocumentRow, error_code: str | None, budget_closed: bool) -> None:
    outcome = job_outcome(status=row.status, error_code=error_code, budget_closed=budget_closed)
    row.partial = bool(outcome and outcome.partial)
    if row.status == JobStatus.QUEUED:
        row.label, row.badge = (outcome.label if outcome else "Waiting"), "queued"
    elif row.status == JobStatus.RUNNING:
        row.label = f"Converting: {row.pages_done} of {plural(row.pages, 'page', 'pages')} done"
        row.badge = "running"
    elif row.status == JobStatus.FAILED:
        row.label, row.badge = (outcome.label if outcome else "Stopped"), "failed"
    elif outcome is not None and row.partial:
        # Partly converted (a budget stop, scanned pages): say so first, whatever is open.
        decide = plural(row.open_decisions, "decision", "decisions")
        row.label = outcome.label + (f", {decide} to make" if row.open_decisions else "")
        row.badge = "partial"
    elif row.open_decisions:
        row.label = f"{plural(row.open_decisions, 'decision', 'decisions')} to make"
        row.badge = "decisions"
    else:
        row.label, row.badge = "Ready to download", "done"
