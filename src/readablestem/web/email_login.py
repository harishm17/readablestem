"""Sign in by an emailed one-time link (T3-10), behind EMAIL_LOGIN_ENABLED (off).

Built so that it is safe to turn on once Harish has chosen an email provider and named it
on the privacy page; until then nothing here runs (the routes answer 404) and no provider
is wired in (`web.email_senders`).

How it works, and why:

- The person types their address. The answer is the same page whatever they typed (on the
  list, linked, or a stranger), so the form cannot be used to learn who is on the list. A
  message is made and sent only for an address that signs in (`auth.allowed_user`'s rule:
  the person's own address or one linked to them, and allowed).
- The link carries a random token (32 bytes, `secrets`). Only its SHA-256 is stored
  (`login_links.token_hash`), so a copy of the database cannot sign anyone in. A newer link
  replaces older unused ones. A link works once and for `EMAIL_LINK_MINUTES` (15 by default).
- Opening the link shows a page with a button; the button (a POST with the session's CSRF
  token) signs in. A mail scanner that fetches every link in a message therefore does not
  use the link up, and a link cannot be used by a page on another site.
- Redeeming is one UPDATE that marks the link used only if it is unused and unexpired, so
  two clicks at once sign in once. The person must still be allowed at that moment.
- Limits: the sign-in rate limit per address, and `EMAIL_LINK_RULE` per target address, so
  nobody can be sent a stream of mail. When the per-target limit is used up the page still
  looks the same and no message is sent.
- Nothing about the address or the token is logged.

Known limits, stated in docs/security.md: the link is in the URL, so Cloud Run's request
logs and the recipient's mail provider hold it until it expires or is used; anyone who can
read the mailbox can sign in (as with any email sign-in).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta
from urllib.parse import quote

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from readablestem.config import Settings
from readablestem.web import linked_emails
from readablestem.web.database import rowcount
from readablestem.web.db import LoginLink, User, session_scope, utcnow
from readablestem.web.email_senders import LinkSender, LoginEmail
from readablestem.web.logsafe import log_event, log_exception

logger = logging.getLogger(__name__)

TOKEN_CHARS = 43  # token_urlsafe(32)
PURGE_AFTER = timedelta(days=1)  # used and expired links are removed this long after
EMAIL_LINK_RULE = "email_link"  # `web.abuse.RULES`: per target address


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def valid_token_shape(token: object) -> bool:
    return (
        isinstance(token, str)
        and len(token) == TOKEN_CHARS
        and all(c.isalnum() or c in "-_" for c in token)
    )


def target_user(session: Session, email: str) -> User | None:
    """The allowed user an address signs in: their own address or a linked one."""
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = linked_emails.user_for_linked(session, email)
    return user if user is not None and user.is_allowed else None


def issue(factory: sessionmaker[Session], email: str, *, minutes: int) -> str | None:
    """A new link token for the person `email` signs in, or None for nobody. Older unused
    links of theirs stop working."""
    with session_scope(factory) as session:
        user = target_user(session, email)
        if user is None:
            return None
        now = utcnow()
        session.execute(
            delete(LoginLink).where(
                or_(
                    and_(LoginLink.user_id == user.id, LoginLink.used_at.is_(None)),
                    LoginLink.expires_at < now - PURGE_AFTER,
                )
            )
        )
        token = secrets.token_urlsafe(32)
        session.add(
            LoginLink(
                user_id=user.id,
                token_hash=_hash(token),
                expires_at=now + timedelta(minutes=minutes),
            )
        )
        return token


def is_usable(factory: sessionmaker[Session], token: str) -> bool:
    """Read only: would this token sign someone in now?"""
    if not valid_token_shape(token):
        return False
    with session_scope(factory) as session:
        row = session.scalar(select(LoginLink).where(LoginLink.token_hash == _hash(token)))
        if row is None or row.used_at is not None:
            return False
        user = session.get(User, row.user_id)
        return user is not None and user.is_allowed and _unexpired(session, row.id)


def _unexpired(session: Session, link_id: str) -> bool:
    return (
        session.scalar(
            select(LoginLink.id).where(LoginLink.id == link_id, LoginLink.expires_at > utcnow())
        )
        is not None
    )


def redeem(factory: sessionmaker[Session], token: str) -> tuple[str, int] | None:
    """Use the token once: (user id, session version) when it signs in, else None."""
    if not valid_token_shape(token):
        return None
    with session_scope(factory) as session:
        now = utcnow()
        used = session.execute(
            update(LoginLink)
            .where(
                LoginLink.token_hash == _hash(token),
                LoginLink.used_at.is_(None),
                LoginLink.expires_at > now,
            )
            .values(used_at=now)
            .execution_options(synchronize_session=False)
        )
        if rowcount(used) != 1:
            return None
        row = session.scalar(select(LoginLink).where(LoginLink.token_hash == _hash(token)))
        user = session.get(User, row.user_id) if row is not None else None
        if user is None or not user.is_allowed:
            return None
        user.last_login_at = now
        return user.id, user.session_version


def message_for(settings: Settings, to: str, token: str) -> LoginEmail:
    link = f"{settings.base_url.rstrip('/')}/login/email/confirm?token={quote(token)}"
    minutes = settings.email_link_minutes
    return LoginEmail(
        to=to,
        subject="Your sign-in link for ReadableSTEM",
        body=(
            "Someone asked to sign in to ReadableSTEM with this email address.\n\n"
            f"To sign in, open this link within {minutes} minutes. It works once:\n\n"
            f"{link}\n\n"
            "If this was not you, ignore this message. Nothing happens unless the link is "
            "opened.\n"
        ),
    )


def send_quietly(sender: LinkSender, message: LoginEmail) -> None:
    """Send; a failure is logged by type and never reaches the person (the page is the same
    whether or not a message went)."""
    try:
        sender.send(message)
    except Exception as exc:
        log_exception(logger, "email_login_send_failed", exc)
    else:
        log_event(logger, "email_login_sent")
