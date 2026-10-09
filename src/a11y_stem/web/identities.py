"""Sign-in identities (`auth_identities`): which Google or Microsoft account is which user.

An identity is keyed by (provider, issuer, subject). The subject is the ID token's `sub`,
for both providers: OpenID Connect promises that `iss` and `sub` together are the one
stable identifier of a person, never reassigned (Core 1.0, section 5.7). Microsoft also
sends `oid`, the account's object id in its directory, the same for every app; `sub` is
pairwise, different for every app registration, so a token minted for another app can
never match one of ours. The price: if the app registration is ever replaced (a new
client id), every Microsoft `sub` changes, and people link again by email on their next
sign-in, under the same rules as the first time.

- **Microsoft** signs in by identity. The first time, the identity is linked to the user
  with the same address only when that user is on the allowlist (the token's tenant was
  already checked: `web/microsoft.py`); after that the address in the token no longer
  matters, so a changed or reused address cannot move the identity to someone else.
  It does not stop a second identity with that user's address, from an allowed tenant,
  from being linked to the same user as well (docs/web.md "Identities and nOAuth").
- **Google** still signs in by verified email, as before: its identity is only recorded,
  on the first sign-in after this release, and changes nobody's access.
- Nothing is recorded for people who are not on the allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.web.db import AuthIdentity, User, session_scope, utcnow

GOOGLE = "google"
MICROSOFT = "microsoft"
GOOGLE_ISSUER = "https://accounts.google.com"


@dataclass(frozen=True)
class Identity:
    provider: str  # GOOGLE or MICROSOFT
    issuer: str
    subject: str
    tenant: str | None = None  # Microsoft directory id (`tid`)


@dataclass(frozen=True)
class SignIn:
    """Who signs in (`user_id` and their session version), or why nobody does:
    `refused` is "no_email" or "not_allowlisted" (then `email` is the address the
    not-allowed page shows the person)."""

    user_id: str | None = None
    session_version: int = 0
    refused: str | None = None
    email: str | None = None


def user_with(session: Session, identity: Identity) -> User | None:
    """The user an identity is linked to, if it is."""
    return session.scalar(
        select(User).join(AuthIdentity, AuthIdentity.user_id == User.id).where(*_key(identity))
    )


def link(session: Session, user_id: str, identity: Identity) -> None:
    session.add(
        AuthIdentity(
            user_id=user_id,
            provider=identity.provider,
            issuer=identity.issuer,
            subject=identity.subject,
            tenant=identity.tenant,
        )
    )


def remember(factory: sessionmaker[Session], user_id: str, identity: Identity) -> None:
    """Record the identity of someone who has just signed in, unless it is recorded
    already (for them, or for another user: it is then left where it is)."""
    try:
        with session_scope(factory) as session:
            if session.scalar(select(AuthIdentity.id).where(*_key(identity))) is None:
                link(session, user_id, identity)
    except IntegrityError:  # recorded by a sign-in running at the same moment
        pass


def microsoft_sign_in(
    factory: sessionmaker[Session], identity: Identity, email: str | None, name: str | None
) -> SignIn:
    """Sign in by identity; the first time, link it to the allowlisted user with `email`.

    Two first sign-ins of one identity at once (two tabs) both try to link it; the
    second one's insert fails on the unique key and is tried again, finding the link.
    """
    try:
        return _microsoft_sign_in(factory, identity, email, name)
    except IntegrityError:
        return _microsoft_sign_in(factory, identity, email, name)


def _microsoft_sign_in(
    factory: sessionmaker[Session], identity: Identity, email: str | None, name: str | None
) -> SignIn:
    with session_scope(factory) as session:
        user = user_with(session, identity)
        if user is None:
            if email is None:
                return SignIn(refused="no_email")
            user = session.scalar(select(User).where(User.email == email))
            if user is None or not user.is_allowed:
                return SignIn(refused="not_allowlisted", email=email)
            link(session, user.id, identity)
        elif not user.is_allowed:
            return SignIn(refused="not_allowlisted", email=user.email)
        user.last_login_at = utcnow()
        if name:
            user.name = name[:200]
        return SignIn(user_id=user.id, session_version=user.session_version)


def _key(identity: Identity) -> tuple[ColumnElement[bool], ...]:
    return (
        AuthIdentity.provider == identity.provider,
        AuthIdentity.issuer == identity.issuer,
        AuthIdentity.subject == identity.subject,
    )
