"""Sharing one document with named, signed-in people (G1).

The owner of a document can give another person access to that one document by email
address. A share is never a link that works without signing in (the one such link is the
class link, `web.links`, which is not a share and not a role), and nothing is sent: the owner
copies the document's address and gives it to the person.

- The person must be signed in with that exact address, and the sign-in must have proved it:
  Google's verified email, the Microsoft directory's verification of the email claim
  (`xms_edov`, never `preferred_username`), or an emailed single-use link sent to that very
  address (`web.email_login`). A sign-in that proves nothing (an older session) is told to sign
  in again (`routes.jobs.job_access`). The address is matched in full and ignoring letter case;
  there is no domain rule, no `+tag` folding, no alias, and a linked address (`LinkedEmail`)
  does not count.
  Whether they may sign in at all is still the service's own rule (`users.is_allowed`, or open
  sign-up), so a person who cannot sign in cannot use a share.
- Two rights. `view` reads the result: the job page, the student preview, every download and
  the review record. `review` adds the review screen and every decision and edit on it; each
  is recorded with the person who made it (`ReviewEdit.user_id`, listed in the review record).
- The owner stays the only one who can delete, retry, upload a revision, change the title,
  contact line, language or retention, combine parts, share further, or change or remove a
  share. A shared person's quota is untouched by the document: conversion, re-reads and spend
  are the owner's (`reread.read_equation`).
- A second source of the same two rights, behind `FEATURE_COURSE_GRANTS` (WS27.1): the owner of a
  course gives a person the right to `view` or `review` every document whose course it is
  (`CourseGrant`, one row per person and course). It is read by `role_of`, the one function that
  says what a person is to a document, and nothing else decides access: a grant is a share that
  all the course's documents have, now and later. The same proof of the address applies; a person
  who holds a share and a grant on one document has the higher right; a grant is never more than
  `review`. With the setting off no grant row gives any right (the session factory says so: see
  `grants_in_force`). Only the course's owner adds, changes or removes a grant, from the course
  page (`routes.course_people`); the course pages themselves stay owner-only.
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

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from readablestem.web.auth import normalise_email
from readablestem.web.database import rowcount
from readablestem.web.db import (
    COURSE_GRANTS_KEY,
    Course,
    CourseGrant,
    Job,
    JobShare,
    JobStatus,
    User,
    utcnow,
)

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
    VIEW: "Reads the document and downloads it.",
    REVIEW: "Also makes review decisions, recorded under their name.",
}
MAX_SHARES_PER_JOB = 10
MAX_GRANTS_PER_COURSE = 10
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


def grants_in_force(session: Session) -> bool:
    """Whether course grants count right now: the session's factory says so (the app passes
    `FEATURE_COURSE_GRANTS`, read at each request). A session from a factory that says nothing
    (the worker, the commands) has none in force."""
    check = session.info.get(COURSE_GRANTS_KEY)
    return bool(check is not None and check())


def role_of(session: Session, job: Job, user: Any) -> str | None:
    """What this person is to this document: `OWNER`, a shared right, or None (a stranger).
    The highest of the owner's own, a share of this document and, with course grants in force,
    a grant on the document's course. A share or grant applies only when this sign-in proved
    the address it names (`user.email_verified`: Google's verified email, or Microsoft's
    `xms_edov`), never on a sign-in name alone. Never more than `REVIEW` unless the owner."""
    if job.user_id == user.id:
        return OWNER
    if not user.email_verified:
        return None
    address = sign_in_address(user.email)
    found = [
        session.scalar(
            select(JobShare.role).where(JobShare.job_id == job.id, JobShare.email == address)
        )
    ]
    if job.course_id is not None and grants_in_force(session):
        # The course must be the document owner's own: a grant is the course owner's to give.
        found.append(
            session.scalar(
                select(CourseGrant.role)
                .join(Course, Course.id == CourseGrant.course_id)
                .where(
                    CourseGrant.course_id == job.course_id,
                    CourseGrant.email == address,
                    Course.user_id == job.user_id,
                )
            )
        )
    return max((r for r in found if r in ROLES), key=RANK.__getitem__, default=None)


def names_of(
    session: Session, job: Job, reader: Any, user_ids: set[str], *, you: str = "you"
) -> dict[str, str]:
    """How each person is named to `reader` where the document says who decided something,
    on the record and on the review list, so the two cannot differ. The reader is `you`. The
    owner of the document sees every other person by name, or by address when they have no
    name (only the owner sees addresses). Everyone else sees names, and "the owner" or "a
    reviewer" for a person without one. A person whose account is gone is not in the result."""
    named: dict[str, str] = {}
    for person in session.scalars(select(User).where(User.id.in_({job.user_id, *user_ids}))):
        if person.id == reader.id:
            named[person.id] = you
        elif reader.id == job.user_id:
            named[person.id] = person.name or person.email
        else:
            named[person.id] = person.name or (
                "the owner" if person.id == job.user_id else "a reviewer"
            )
    return named


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


ADDED, CHANGED, UNCHANGED = "added", "changed", "unchanged"


def _checked_person(owner_email: str, raw_email: str, role: str, own_words: str) -> str:
    """The one check of who may be given a right and which: a whole address that is not the
    owner's own, and a right the owner can give. Returns the address as stored (lower case).
    A share and a course grant both ask it, so the two cannot differ."""
    email = normalise_email(raw_email)
    if email is None or not _ADDRESS.fullmatch(email):
        raise ShareError("bad_email", "Enter the person's full email address.")
    if role not in ROLES:
        raise ShareError("bad_role", "Choose View or Review.")
    if email == sign_in_address(owner_email):
        raise ShareError("self", own_words)
    return email


def share(
    session: Session, job: Job, owner_email: str, raw_email: str, role: str
) -> tuple[JobShare, str]:
    """Give `raw_email` the right `role` on `job` (or change theirs). Returns the share and
    what happened (`ADDED`, `CHANGED` or `UNCHANGED`: the page says which). Raises
    `ShareError` for an address that is not one, the owner's own address, a right that does
    not exist, a document that cannot be shared, or too many people."""
    email = _checked_person(
        owner_email, raw_email, role, "That is your own address. The document is already yours."
    )
    if job.is_sample:
        raise ShareError("sample", "The sample document cannot be shared.")
    if job.status != JobStatus.DONE:
        raise ShareError("not_done", "A document can be shared once it has finished converting.")
    existing = session.scalar(
        select(JobShare).where(JobShare.job_id == job.id, JobShare.email == email)
    )
    if existing is not None:
        if existing.role == role:
            return existing, UNCHANGED
        existing.role = role
        return existing, CHANGED
    if len(shares_of(session, job.id)) >= MAX_SHARES_PER_JOB:
        raise ShareError(
            "too_many", f"A document can be shared with at most {MAX_SHARES_PER_JOB} people."
        )
    row = JobShare(job_id=job.id, email=email, role=role, created_at=utcnow())
    session.add(row)
    session.flush()
    return row, ADDED


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
    owner: str  # the owner's name, else "": never their address
    course: str = ""  # the course's name when the right comes from a course grant, else ""


def shared_with(session: Session, user: Any, now) -> list[SharedDocument]:
    """The finished documents others have shared with this person, one by one or through a
    course grant, and that are still kept. Those shared one by one come first, newest first;
    those reached through a course follow, by course name and then newest first. A document
    that is both shared and granted is listed once, with the higher right (the share's when the
    two are equal). Only the columns the list needs: never the result or the HTML."""
    if not user.email_verified:
        return []
    address = sign_in_address(user.email)
    columns = (Job.id, Job.filename, Job.title_override, Job.result_title)
    direct = session.execute(
        select(*columns, JobShare.role, User.name)
        .join(JobShare, JobShare.job_id == Job.id)
        .join(User, User.id == Job.user_id)
        .where(JobShare.email == address, Job.retain_until > now, Job.status == JobStatus.DONE)
        .order_by(Job.created_at.desc(), Job.id)
    ).all()
    listed = {r[0]: SharedDocument(r[0], r[1], r[2], r[3], r[4], r[5] or "") for r in direct}
    if grants_in_force(session):
        through = session.execute(
            select(*columns, CourseGrant.role, User.name, Course.name)
            .join(Course, Course.id == Job.course_id)
            .join(CourseGrant, CourseGrant.course_id == Course.id)
            .join(User, User.id == Job.user_id)
            .where(
                CourseGrant.email == address,
                Course.user_id == Job.user_id,
                Job.retain_until > now,
                Job.status == JobStatus.DONE,
                Job.is_sample.is_(False),
            )
            .order_by(func.lower(Course.name), Job.created_at.desc(), Job.id)
        ).all()
        for r in through:
            if r[0] in listed and RANK[listed[r[0]].role] >= RANK[r[4]]:
                continue
            listed[r[0]] = SharedDocument(r[0], r[1], r[2], r[3], r[4], r[5] or "", r[6])
    return sorted(listed.values(), key=lambda d: d.course != "")  # stable: the order above


# ----------------------------------------------------------------------------- course grants


def grants_of(session: Session, course_id: str) -> list[CourseGrant]:
    """The course's grants, oldest first."""
    return list(
        session.scalars(
            select(CourseGrant)
            .where(CourseGrant.course_id == course_id)
            .order_by(CourseGrant.created_at, CourseGrant.id)
        )
    )


def grant(
    session: Session, course: Course, owner_email: str, raw_email: str, role: str
) -> tuple[CourseGrant, str]:
    """Give `raw_email` the right `role` on every document of `course` (or change theirs).
    Returns the grant and what happened (`ADDED`, `CHANGED` or `UNCHANGED`). Raises `ShareError`
    as `share` does: an address that is not one, the owner's own, a right that does not exist,
    or too many people. The caller has shown that the course is the person's own."""
    email = _checked_person(
        owner_email, raw_email, role, "That is your own address. The course is already yours."
    )
    existing = session.scalar(
        select(CourseGrant).where(CourseGrant.course_id == course.id, CourseGrant.email == email)
    )
    if existing is not None:
        if existing.role == role:
            return existing, UNCHANGED
        existing.role = role
        return existing, CHANGED
    if len(grants_of(session, course.id)) >= MAX_GRANTS_PER_COURSE:
        raise ShareError(
            "too_many", f"A course can have at most {MAX_GRANTS_PER_COURSE} people with access."
        )
    row = CourseGrant(course_id=course.id, email=email, role=role, created_at=utcnow())
    session.add(row)
    session.flush()
    return row, ADDED


def revoke_grant(session: Session, course_id: str, grant_id: str) -> bool:
    """Remove one grant of this course. False when there is no such grant."""
    result = session.execute(
        delete(CourseGrant).where(CourseGrant.id == grant_id, CourseGrant.course_id == course_id)
    )
    return rowcount(result) > 0


@dataclass(frozen=True)
class CoursePeople:
    """Who reaches a document through its course (the owner's share page lists them)."""

    course_id: str
    course_name: str
    people: list[tuple[str, str]]  # (address, right), oldest first


def course_people(session: Session, job: Job) -> CoursePeople | None:
    """The people with a course grant on this document's course, for the document's owner to
    see beside its own shares. None when grants are not in force, the document has no course
    of its owner's, or the course has nobody."""
    if job.course_id is None or not grants_in_force(session):
        return None
    course = session.get(Course, job.course_id)
    if course is None or course.user_id != job.user_id:
        return None
    rows = grants_of(session, course.id)
    if not rows:
        return None
    return CoursePeople(course.id, course.name, [(r.email, r.role) for r in rows])
