"""Sharing one document with named, signed-in people (G1).

The owner of a document can give another person access to that one document by email
address. There are no links that work without signing in, and nothing is sent: the owner
copies the document's address and gives it to the person.

- The person must be signed in with that exact address, and the sign-in must have proved it:
  Google's verified email, the Microsoft directory's verification of the email claim
  (`xms_edov`, never `preferred_username`), or an emailed single-use link sent to that very
  address (`web.email_login`). A sign-in that proves nothing (an older session) is told to sign
  in again (`routes.jobs.job_access`). The address is matched in full and ignoring letter case; there is
  no domain rule, no `+tag` folding, no alias, and a linked address (`LinkedEmail`) does not count.
  Whether they may sign in at all is still the service's own rule (`users.is_allowed`, or open
  sign-up), so a person who cannot sign in cannot use a share.
- Two rights. `view` reads the result: the job page, the student preview, every download and
  the review record. `review` adds the review screen and every decision and edit on it; each
  is recorded with the person who made it (`ReviewEdit.user_id`, listed in the review record).
- The owner stays the only one who can delete, retry, upload a revision, change the title,
  contact line, language or retention, combine parts, share further, or change or remove a
  share. A shared person's quota is untouched by the document: conversion, re-reads and spend
  are the owner's (`reread.read_equation`).
- Removing a share takes effect on the next request, because every request reads the share
  (`routes.jobs.job_access`); nothing is cached in the session.
- A share has no expiry of its own. It goes with the document: deleting it, or its retention
  time passing, ends every share (and a document past its time is refused before any share is
  read).

This module holds the rights and the reading and writing of shares. The check on a request is
`routes.jobs.job_access`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from readablestem.web.auth import normalise_email
from readablestem.web.database import rowcount
from readablestem.web.db import Job, JobShare, JobStatus, User, utcnow

VIEW = "view"
REVIEW = "review"
OWNER = "owner"
# What a request needs, lowest to highest. A right covers every lower one.
RANK = {VIEW: 1, REVIEW: 2, OWNER: 3}
# What the owner can give.
ROLES = (VIEW, REVIEW)
ROLE_WORDS = {VIEW: "View", REVIEW: "Review"}
# One line under each right on the sharing page (what the right lets the person do).
ROLE_HELP = {
    VIEW: "Reads the converted document, the student preview, the downloads and the review record.",
    REVIEW: "Also makes review decisions and edits. Each one is recorded under their name.",
}
MAX_SHARES_PER_JOB = 10
# One whole address: the characters an address uses (no `*` or `?`, so no pattern is ever an
# address), then `@`, then a dotted domain. Stricter than the sign-in's own check on purpose:
# an address that cannot be a person's is refused rather than stored.
_ADDRESS = re.compile(r"[a-z0-9.!#$%&'+/=^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")


class ShareError(ValueError):
    """The owner's request cannot be done; `code` is a category, `message` is for the page."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def sign_in_address(user_email: str) -> str:
    """The address a share is matched against: the person's own sign-in address, lower case."""
    return user_email.strip().lower()


def role_of(session: Session, job: Job, user: Any) -> str | None:
    """What this person is to this document: `OWNER`, a shared right, or None (a stranger).
    A share applies only when this sign-in proved the address it names (`user.email_verified`:
    Google's verified email, or Microsoft's `xms_edov`), never on a sign-in name alone."""
    if job.user_id == user.id:
        return OWNER
    if not user.email_verified:
        return None
    return session.scalar(
        select(JobShare.role).where(
            JobShare.job_id == job.id, JobShare.email == sign_in_address(user.email)
        )
    )


def names_unproved(session: Session, job: Job, user: Any) -> bool:
    """A share on this document names this person's address, but their sign-in did not prove
    it (an older session, a Microsoft sign-in name, an email link sent elsewhere). True only
    when such a share exists, so the page that asks them to sign in again tells a stranger
    nothing."""
    if user.email_verified or job.user_id == user.id:
        return False
    return (
        session.scalar(
            select(JobShare.id).where(
                JobShare.job_id == job.id, JobShare.email == sign_in_address(user.email)
            )
        )
        is not None
    )


def shares_of(session: Session, job_id: str) -> list[JobShare]:
    """The document's shares, oldest first."""
    return list(
        session.scalars(
            select(JobShare)
            .where(JobShare.job_id == job_id)
            .order_by(JobShare.created_at, JobShare.id)
        )
    )


def share(session: Session, job: Job, owner_email: str, raw_email: str, role: str) -> JobShare:
    """Give `raw_email` the right `role` on `job` (or change theirs). Raises `ShareError`
    for an address that is not one, the owner's own address, a right that does not exist, a
    document that cannot be shared, or too many people."""
    email = normalise_email(raw_email)
    if email is None or not _ADDRESS.fullmatch(email):
        raise ShareError("bad_email", "Enter the person's full email address.")
    if role not in ROLES:
        raise ShareError("bad_role", "Choose View or Review.")
    if email == sign_in_address(owner_email):
        raise ShareError("self", "That is your own address. The document is already yours.")
    if job.is_sample:
        raise ShareError("sample", "The sample document cannot be shared.")
    if job.status != JobStatus.DONE:
        raise ShareError("not_done", "A document can be shared once it has finished converting.")
    existing = session.scalar(
        select(JobShare).where(JobShare.job_id == job.id, JobShare.email == email)
    )
    if existing is not None:
        existing.role = role
        return existing
    if len(shares_of(session, job.id)) >= MAX_SHARES_PER_JOB:
        raise ShareError(
            "too_many", f"A document can be shared with at most {MAX_SHARES_PER_JOB} people."
        )
    row = JobShare(job_id=job.id, email=email, role=role, created_at=utcnow())
    session.add(row)
    session.flush()
    return row


def revoke(session: Session, job_id: str, share_id: str) -> bool:
    """Remove one share of this document. False when there is no such share."""
    result = session.execute(
        delete(JobShare).where(JobShare.id == share_id, JobShare.job_id == job_id)
    )
    return rowcount(result) > 0


@dataclass(frozen=True)
class SharedDocument:
    id: str
    filename: str
    title_override: str | None
    result_title: str | None
    role: str
    owner: str  # the owner's name, else "The owner": never their address


def shared_with(session: Session, user: Any, now) -> list[SharedDocument]:
    """The finished documents others have shared with this person and that are still kept,
    newest first. Only the columns the list needs: never the result or the HTML."""
    if not user.email_verified:
        return []
    rows = session.execute(
        select(
            Job.id,
            Job.filename,
            Job.title_override,
            Job.result_title,
            JobShare.role,
            User.name,
        )
        .join(JobShare, JobShare.job_id == Job.id)
        .join(User, User.id == Job.user_id)
        .where(
            JobShare.email == sign_in_address(user.email),
            Job.retain_until > now,
            Job.status == JobStatus.DONE,
        )
        .order_by(Job.created_at.desc(), Job.id)
    ).all()
    return [SharedDocument(r[0], r[1], r[2], r[3], r[4], r[5] or "The owner") for r in rows]
