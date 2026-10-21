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
  with the same address only when that user is allowed (the token's tenant was already
  checked: `web/microsoft.py`); after that the address in the token no longer matters, so a
  changed or reused address cannot move the identity to someone else. It does not stop a
  second identity with that user's address, from an allowed tenant, from being linked to
  the same user as well (docs/web.md "Identities and nOAuth"). With open sign-up
  (`SIGNUP_MODE=open`) an identity whose address belongs to nobody yet makes the account
  (`OpenSignup`), within the day's limit on new accounts, and is linked to it.
- **Google** still signs in by verified email, as before: its identity is only recorded,
  on the first sign-in after this release, and changes nobody's access.
- Nothing is recorded for people who are refused: not on the allowlist (SIGNUP_MODE=
  allowlist), or blocked, or turned away because the day's sign-ups are full.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from readablestem.web import access, linked_emails, tombstones
from readablestem.web.database import utc_today
from readablestem.web.db import AuthIdentity, QuotaDay, User, session_scope, utcnow
from readablestem.web.quota import try_take_signup

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
    """Who signs in (`user_id` and their session version), or why nobody does. `refused` is
    "no_email"; "not_allowlisted" (the address is not on the list and sign-up is not open);
    "use_google" (open sign-up, Microsoft: the address is not one the directory vouches for,
    or it belongs to an invited person or an admin: sign in with Google); "blocked" (sign-up
    is open, and an admin has blocked this person, or the address is another person's linked
    Google address); or "signups_full" (sign-up is open, and today's limit on new accounts is
    used up). For "not_allowlisted", "blocked" and "signups_full" `email` is the address the
    refusal page shows the person. `created` is True when this sign-in made the account."""

    user_id: str | None = None
    session_version: int = 0
    refused: str | None = None
    email: str | None = None
    created: bool = False


@dataclass(frozen=True)
class OpenSignup:
    """Sign-up is open (`SIGNUP_MODE=open`): a verified identity whose address belongs to
    nobody yet makes an account of the open tier, while fewer than `max_per_day` were made
    today (UTC). `before_create` runs just before an account would be made and before it is
    counted, and may raise to refuse (the per-address rate rule: `web.auth`)."""

    max_per_day: int
    before_create: Callable[[], None] = field(default=lambda: None, compare=False)
    # The key of `web.tombstones`: with it, an address whose account was deleted today comes
    # back with the day's pages and reads already used, and without taking another place.
    tombstone_key: bytes | None = None


def create_open_user(
    session: Session, open_signup: OpenSignup, email: str, name: str | None
) -> User | None:
    """The new open-tier user for `email`, or None when today's accounts are used up.

    The count and the row are one transaction (the caller's): the count is only spent if
    the account is committed. Two sign-ups of one address at once both get here; the second
    one's insert fails on the unique email and the caller tries again, finding the row."""
    open_signup.before_create()
    day = utc_today()
    earlier = (
        tombstones.find(session, open_signup.tombstone_key, email, day)
        if open_signup.tombstone_key is not None
        else None
    )
    # An address whose account was made and deleted today already has its place in the day's
    # count: coming back takes no new one, so deleting and signing up again cannot use up the
    # day's places. An account from an earlier day does take one now.
    if (earlier is None or not earlier.slot_taken) and not try_take_signup(
        session, limit=open_signup.max_per_day, day=day
    ):
        return None
    user = User(
        email=email,
        name=name[:200] if name else None,
        is_allowed=True,
        is_admin=False,
        access_tier=access.OPEN,
        last_login_at=utcnow(),
    )
    session.add(user)
    session.flush()
    if earlier is not None and (earlier.pages or earlier.rereads):
        # What the deleted account used today is still used: deleting is not a reset.
        session.add(
            QuotaDay(user_id=user.id, day=day, pages=earlier.pages, rereads=earlier.rereads)
        )
    return user


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
    factory: sessionmaker[Session],
    identity: Identity,
    email: str | None,
    name: str | None,
    *,
    open_signup: OpenSignup | None = None,
    email_verified: bool = False,
) -> SignIn:
    """Sign in by identity; the first time, link it to the allowed user with `email`, or,
    with `open_signup`, make that user first.

    With `open_signup` the address must be one the directory vouches for (`email_verified`,
    `MicrosoftAccount.verified_email`), and it may link only to an account made by open
    sign-up that is not an admin: an invited person or an admin is reached by Google (a
    verified address) or by an identity linked before sign-up opened. Anything else is
    refused as "use_google". Without `open_signup` the rules are those of the allowlist.

    Two first sign-ins of one identity at once (two tabs) both try to link it; the
    second one's insert fails on the unique key and is tried again, finding the link.
    """
    try:
        return _microsoft_sign_in(factory, identity, email, name, open_signup, email_verified)
    except IntegrityError:
        return _microsoft_sign_in(factory, identity, email, name, open_signup, email_verified)


def _microsoft_sign_in(
    factory: sessionmaker[Session],
    identity: Identity,
    email: str | None,
    name: str | None,
    open_signup: OpenSignup | None,
    email_verified: bool = False,
) -> SignIn:
    # Who is refused by `is_allowed` is "blocked" when sign-up is open (the person has an
    # account and an admin stopped it), "not_allowlisted" otherwise: as it always was.
    no_access = "blocked" if open_signup is not None else "not_allowlisted"
    with session_scope(factory) as session:
        created = False
        user = user_with(session, identity)
        if user is None:
            if email is None:
                return SignIn(refused="no_email")
            if open_signup is not None and not email_verified:
                # nOAuth: an address a directory merely states proves nothing, and with open
                # sign-up it would make an account, or take over one, for any address.
                return SignIn(refused="use_google", email=email)
            user = session.scalar(select(User).where(User.email == email))
            if user is None and open_signup is not None:
                if linked_emails.is_linked(session, email):
                    # Another person's extra Google address (Microsoft sign-in does not use
                    # those, and an account is never made under one).
                    return SignIn(refused="blocked", email=email)
                user = create_open_user(session, open_signup, email, name)
                if user is None:
                    return SignIn(refused="signups_full", email=email)
                created = True
            if user is None:
                return SignIn(refused="not_allowlisted", email=email)
            if not user.is_allowed:
                return SignIn(refused=no_access, email=email)
            if open_signup is not None and (user.is_admin or user.access_tier == access.INVITED):
                return SignIn(refused="use_google", email=email)
            link(session, user.id, identity)
        elif not user.is_allowed:
            return SignIn(refused=no_access, email=user.email)
        user.last_login_at = utcnow()
        if name:
            user.name = name[:200]
        return SignIn(user_id=user.id, session_version=user.session_version, created=created)


def _key(identity: Identity) -> tuple[ColumnElement[bool], ...]:
    return (
        AuthIdentity.provider == identity.provider,
        AuthIdentity.issuer == identity.issuer,
        AuthIdentity.subject == identity.subject,
    )
