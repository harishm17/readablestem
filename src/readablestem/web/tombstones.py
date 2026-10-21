"""What a deleted account had used today, so that deleting it does not reset today's limits.

Without this, one person could delete their account and sign up again as often as they
liked, starting each time with a whole day's pages and model reads and taking a new place
from the day's cap on new accounts: enough to drain the open-tier spend pool and shut
everyone else out. So when an account is erased (`ops.retention.erase_user`, from the
Account page or the command line), a row of `deleted_accounts` is written for the rest of
that UTC day:

- keyed by an HMAC-SHA256 of the normalised email address under the session secret, never
  the address, so a copy of the table does not list who deleted their account and the key
  cannot be guessed without the secret;
- holding the day's pages and model reads, and whether the account took one of the day's
  places for new accounts (it was made today);
- carried forward when the same address signs up again the same day (`web.identities`):
  the new account starts with those pages and reads already used, and does not take another
  place from the day's cap;
- deleted the next day (`ops.retention.purge_tombstones`, in every purge).

It is scoped to the day because every limit it protects is: pages, reads, accounts per UTC
day. The privacy notice says it.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.config import Settings
from readablestem.web.database import as_utc
from readablestem.web.db import DeletedAccount, QuotaDay, User

# Only used where no session secret is set (development and tests, whose sessions end with
# the process anyway). Production refuses to start without a secret.
_DEV_KEY = b"readablestem-development-tombstone-key"


def tombstone_key(settings: Settings) -> bytes:
    """The key the address hashes are made under: the session secret."""
    secret = settings.session_secret
    value = secret.get_secret_value() if secret is not None else ""
    return b"deleted-account:" + (value.encode() if value else _DEV_KEY)


def email_hash(key: bytes, email: str) -> str:
    return hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()


def write(session: Session, user: User, key: bytes, day: date) -> None:
    """Record what `user` used on `day`, in the caller's transaction (before the account's
    own rows are deleted). Nothing is written for an account that used nothing and did not
    take a place today. A second deletion the same day keeps the larger numbers."""
    used = session.execute(
        select(QuotaDay.pages, QuotaDay.rereads).where(
            QuotaDay.user_id == user.id, QuotaDay.day == day
        )
    ).first()
    pages, rereads = (int(used[0] or 0), int(used[1] or 0)) if used else (0, 0)
    created = as_utc(user.created_at)
    slot_taken = created is not None and created.date() == day
    if not (pages or rereads or slot_taken):
        return
    digest = email_hash(key, user.email)
    row = session.get(DeletedAccount, (digest, day))
    if row is None:
        session.add(
            DeletedAccount(
                email_hash=digest, day=day, pages=pages, rereads=rereads, slot_taken=slot_taken
            )
        )
        return
    row.pages = max(row.pages, pages)
    row.rereads = max(row.rereads, rereads)
    row.slot_taken = row.slot_taken or slot_taken


def find(session: Session, key: bytes, email: str, day: date) -> DeletedAccount | None:
    """The record of an account with this address deleted on `day`, if there is one."""
    return session.get(DeletedAccount, (email_hash(key, email), day))
