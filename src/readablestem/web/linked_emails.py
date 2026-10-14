"""Other Google accounts an admin allows for one instructor (T3-10).

Why: an instructor may be invited under one address (a university one) and be able to
sign in with Google only under another (a personal one), for example while the university's
Microsoft approval is still pending. The admin links the second address to the instructor;
signing in with it, verified by Google as always, signs in the same person, with the same
documents, courses and quota.

Rules, each tested in `tests/test_web_linked_emails.py`:

- The instructor must be on the allowlist, and their own `is_allowed` decides on every
  request. Removing them from the allowlist ends the linked addresses' access too.
- A linked address is never also another person's own address, and belongs to one
  instructor only. Adding a person to the allowlist under an address that is linked to
  someone else is refused, so a link can never hand one person's account to another.
- At most `MAX_LINKED` addresses per instructor.
- Removing a link ends the instructor's signed-in sessions (the session does not remember
  which address it came from), so a link that was added by mistake or for an account that
  was lost stops working at once.
- Google only. Microsoft sign-in is by directory identity and does not use these.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.web.db import LinkedEmail, User

MAX_LINKED = 5


class LinkRefused(Exception):
    """The link was not made or removed. `code` is a category; `message` is plain words for
    the admin (it names no document content)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def user_for_linked(session: Session, email: str) -> User | None:
    """The user a linked address signs in, if there is one."""
    return session.scalar(
        select(User)
        .join(LinkedEmail, LinkedEmail.user_id == User.id)
        .where(LinkedEmail.email == email)
    )


def linked_to(session: Session, user_id: str) -> list[str]:
    return list(
        session.scalars(
            select(LinkedEmail.email)
            .where(LinkedEmail.user_id == user_id)
            .order_by(LinkedEmail.email)
        )
    )


def link(session: Session, owner_email: str, linked_email: str) -> None:
    """Link `linked_email` to the allowed user `owner_email`. Raises `LinkRefused`."""
    owner = session.scalar(select(User).where(User.email == owner_email))
    if owner is None or not owner.is_allowed:
        raise LinkRefused(
            "unknown_owner",
            "That instructor is not on the allowlist. Add them to the allowlist first.",
        )
    if linked_email == owner.email:
        raise LinkRefused("same_address", "That is already the instructor's own address.")
    if session.scalar(select(User.id).where(User.email == linked_email)) is not None:
        raise LinkRefused(
            "is_a_user",
            "That address is on the allowlist as a person of its own, so it cannot be linked "
            "to someone else.",
        )
    if session.scalar(select(LinkedEmail.id).where(LinkedEmail.email == linked_email)):
        raise LinkRefused("already_linked", "That address is already linked to an instructor.")
    if len(linked_to(session, owner.id)) >= MAX_LINKED:
        raise LinkRefused(
            "too_many", f"An instructor can have at most {MAX_LINKED} other Google accounts."
        )
    session.add(LinkedEmail(user_id=owner.id, email=linked_email))


def unlink(session: Session, linked_email: str) -> bool:
    """Remove a link and end the instructor's sessions. False when there was none."""
    row = session.scalar(select(LinkedEmail).where(LinkedEmail.email == linked_email))
    if row is None:
        return False
    owner = session.get(User, row.user_id)
    if owner is not None:
        owner.session_version += 1
    session.delete(row)
    return True


def is_linked(session: Session, email: str) -> bool:
    return session.scalar(select(LinkedEmail.id).where(LinkedEmail.email == email)) is not None
