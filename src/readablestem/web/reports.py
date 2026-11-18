"""Problem reports to the operator, stored in the app (SW25, the tool report).

"Report this item" on a review card and "Email us about it" on a stopped document used to be
`mailto:` links: nothing for a person with no mail program, and no list for the operator.
They are now a short form (`routes/reports.py`) that writes one row of `db.Report`; the
operator reads the open ones at `/admin/reports`. Nobody else reads them.

What a row holds: the document's id (as text, not a foreign key, so the report outlives the
document), the item's id, its kind and page number, the code of a stopped job, the pipeline
version, and what the sender typed (plain text, at most `MAX_TEXT` characters). No picture, no
text of the document. The row names the sender's account, and the operator's page shows its
email address (the form says so). The ids are read from the stored job here, not from
what the form sent, so the form cannot name a kind or a page it does not have.

Who writes: the owner of a document and a person it was shared with for review (the form's
route asks `job_access` for the review right). Who reads: an admin. Retention
(`ops.retention`): with the sender's account, `CLOSED_DAYS` after it is closed, and
`MAX_DAYS` after it was made, whatever its state. The privacy page quotes these two numbers
from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from readablestem.web.db import Job, JobStatus, Report, User, utcnow

KIND_TOOL = "tool"
OPEN = "open"
CLOSED = "closed"
MAX_TEXT = 1000
CLOSED_DAYS = 30  # a closed report is kept this long, then deleted
MAX_DAYS = 180  # any report is deleted this long after it was made
LIST_ROWS = 200  # the most the operator's page lists at once

ITEM_REF = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
# What the page says for a card that is a group or a page list, not one element.
GROUP_KIND = "group"
GROUP_PREFIXES = ("hg-", "pg-")  # `web.queue`: the card of a heading group, of a page
TEXT_TOO_LONG = f"Write at most {MAX_TEXT} characters, or shorten what you wrote."


@dataclass(frozen=True)
class Where:
    """Where the problem is, as ids and codes only."""

    job_id: str
    item_ref: str | None = None
    item_kind: str | None = None
    page: int | None = None  # 1-based, as people count pages
    code: str | None = None
    pipeline_version: str | None = None


def clean_item(raw: str | None) -> str | None:
    """The item id from the form's query, or None when it is not an id (nothing else is kept)."""
    text = (raw or "").strip()
    return text if ITEM_REF.fullmatch(text) else None


def where_of(job: Job, item: str | None) -> Where:
    """The ids of a report about `item` of `job` (or about the whole job), read from the stored
    job: the element's kind and page, the pipeline version, and, for a stopped job, the error
    code. An id that is not an id is left out."""
    result = job.result_json if isinstance(job.result_json, dict) else {}
    version = result.get("pipeline_version")
    if not isinstance(version, str):  # a stopped job has no result: the code that stopped it
        from readablestem.pipeline.run import PIPELINE_VERSION

        version = PIPELINE_VERSION
    where = Where(
        job_id=job.id,
        item_ref=clean_item(item),
        code=job.error_code if job.status == JobStatus.FAILED else None,
        pipeline_version=version[:40],
    )
    if where.item_ref is None:
        return where
    # A repeated-heading card ("hg-p1-e7") or a page card ("pg-p2-e0") is named by its first
    # element: it is a "group", on that element's page.
    grouped = where.item_ref.startswith(GROUP_PREFIXES)
    wanted = where.item_ref[3:] if grouped else where.item_ref
    for element in result.get("elements") or []:
        if isinstance(element, dict) and element.get("id") == wanted:
            kind = element.get("kind")
            page = element.get("page")
            return replace(
                where,
                item_kind=GROUP_KIND if grouped else kind[:16] if isinstance(kind, str) else None,
                page=page + 1 if isinstance(page, int) and page >= 0 else None,
            )
    return replace(where, item_kind=GROUP_KIND if grouped else None)


def clean_text(raw: str) -> tuple[str, str | None]:
    """What the person typed, as plain text with its line breaks (control characters and
    surrounding blanks dropped), and an error sentence when it is too long. Never a link: the
    page shows it escaped."""
    text = "".join(
        c for c in raw.replace("\r\n", "\n").replace("\r", "\n") if c in "\n\t" or c.isprintable()
    )
    text = text.strip()
    return text, (TEXT_TOO_LONG if len(text) > MAX_TEXT else None)


def add(session: Session, user_id: str, where: Where, text: str) -> Report:
    """Write one tool report in the caller's transaction."""
    report = Report(
        kind=KIND_TOOL,
        from_user_id=user_id,
        job_ref=where.job_id,
        item_ref=where.item_ref,
        item_kind=where.item_kind,
        page=where.page,
        code=where.code,
        pipeline_version=where.pipeline_version,
        text=text or None,
        status=OPEN,
        created_at=utcnow(),
    )
    session.add(report)
    session.flush()
    return report


def listed(session: Session) -> list[tuple[Report, str | None]]:
    """Reports for the operator's page, open ones first (oldest first, so none waits for
    ever), then closed, newest first: with the sender's email."""
    order = (
        case((Report.status == OPEN, 0), else_=1),
        case((Report.status == OPEN, Report.created_at), else_=None),
        Report.created_at.desc(),
    )
    rows = session.execute(
        select(Report, User.email)
        .join(User, User.id == Report.from_user_id)
        .order_by(*order)
        .limit(LIST_ROWS)
    ).all()
    return [(report, email) for report, email in rows]


def close(session: Session, report_id: int, now: datetime | None = None) -> bool:
    """Close one open report. False when there is none open with that id."""
    result = session.execute(
        update(Report)
        .where(Report.id == report_id, Report.status == OPEN)
        .values(status=CLOSED, closed_at=now or utcnow())
    )
    return bool(getattr(result, "rowcount", 0))
