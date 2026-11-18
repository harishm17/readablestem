"""The support lookup: one account, for the operator who is answering that person.

`readablestem ops lookup ADDRESS` prints what support needs to answer "what happened to my
document?" and nothing the person did not already tell the app: the tier and state, today's
pages and re-reads and the spend against the account, and each document as a row of facts
that hold no content (its id, state, pages, error category, cost, when it was made and when
it goes). Never a file name, a title or any text of a document: the rows are built from the
explicit columns of `DocumentRow`, so a column added to the stored job does not appear here
unless it is added to the row.

Of the review outcomes it shows only the three per-account figures the admin page shows
(`web.events.usage`: review writes, decisions that stand, documents reviewed), never an
outcome by kind, reason or document, so the lookup is not a way around the small-count rules
of `web.events.outcome_cells`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from readablestem.web import events
from readablestem.web.auth import normalise_email
from readablestem.web.database import as_utc
from readablestem.web.db import Job, QuotaDay, User

DOCUMENT_ROWS = 50  # the newest documents; an account has few at a time (they are deleted)


@dataclass(frozen=True)
class DocumentRow:
    """The content-free facts of one document. Add a column here only if it holds no text
    of the document, no title and no file name."""

    id: str
    status: str
    pages: int
    error_code: str | None
    cost_usd: float
    created_at: datetime | None
    delete_after: datetime | None


@dataclass(frozen=True)
class AccountLookup:
    pseudonym: str
    tier: str
    state: str  # "allowed" or "blocked", then ", admin"
    created_at: datetime | None
    last_login_at: datetime | None
    pages_today: int
    rereads_today: int
    spent_today_usd: float
    spent_total_usd: float
    review_writes: int
    decisions: int
    documents_reviewed: int
    documents: tuple[DocumentRow, ...]
    documents_total: int


def lookup(session: Session, address: str, *, today: date) -> AccountLookup | None:
    """The account with this address, or None when nobody has one (or the address is not
    one). Reads only."""
    email = normalise_email(address)
    user = session.scalar(select(User).where(User.email == email)) if email else None
    if user is None:
        return None
    day = session.scalar(select(QuotaDay).where(QuotaDay.user_id == user.id, QuotaDay.day == today))
    spent_total = session.scalar(
        select(func.sum(QuotaDay.spent_usd)).where(QuotaDay.user_id == user.id)
    )
    jobs = session.execute(
        select(
            Job.id,
            Job.status,
            Job.page_count,
            Job.error_code,
            Job.cost_usd,
            Job.created_at,
            Job.retain_until,
        )
        .where(Job.user_id == user.id)
        .order_by(Job.created_at.desc())
        .limit(DOCUMENT_ROWS)
    ).all()
    total = session.scalar(select(func.count()).select_from(Job).where(Job.user_id == user.id))
    mine = events.usage(session, user_id=user.id).by_person
    counts = mine[0].counts if mine else {}
    return AccountLookup(
        pseudonym=events.pseudonym(user.id),
        tier=str(user.access_tier),
        state=("blocked" if not user.is_allowed else "allowed")
        + (", admin" if user.is_admin else ""),
        created_at=as_utc(user.created_at),
        last_login_at=as_utc(user.last_login_at),
        pages_today=day.pages if day else 0,
        rereads_today=day.rereads if day else 0,
        spent_today_usd=float(day.spent_usd) if day else 0.0,
        spent_total_usd=float(spent_total or 0.0),
        review_writes=int(counts.get(events.DECISION_MADE, 0)),
        decisions=int(counts.get(events.DECISIONS, 0)),
        documents_reviewed=int(counts.get(events.DOCUMENTS_REVIEWED, 0)),
        documents=tuple(
            DocumentRow(
                id=str(job_id),
                status=str(status),
                pages=int(pages or 0),
                error_code=error,
                cost_usd=float(cost or 0.0),
                created_at=as_utc(created),
                delete_after=as_utc(retain),
            )
            for job_id, status, pages, error, cost, created, retain in jobs
        ),
        documents_total=int(total or 0),
    )


def render(found: AccountLookup) -> list[str]:
    """The lookup as lines of text."""

    def when(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M UTC") if value else "never"

    lines = [
        f"account {found.pseudonym}: {found.state}, tier {found.tier}",
        f"created {when(found.created_at)}, last sign-in {when(found.last_login_at)}",
        f"today (UTC): {found.pages_today} pages, {found.rereads_today} re-reads, "
        f"${found.spent_today_usd:.2f} spent; ${found.spent_total_usd:.2f} in all",
        f"review: {found.review_writes} review writes, {found.decisions} decisions, "
        f"{found.documents_reviewed} documents reviewed",
        f"documents: {found.documents_total}"
        + (
            f" (the newest {len(found.documents)} follow)"
            if found.documents_total > len(found.documents)
            else ""
        ),
    ]
    for doc in found.documents:
        lines.append(
            f"  {doc.id} {doc.status} {doc.pages} pages"
            f" error={doc.error_code or '-'} ${doc.cost_usd:.2f}"
            f" made {when(doc.created_at)} deleted after {when(doc.delete_after)}"
        )
    return lines
