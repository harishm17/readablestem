"""Who a person is to the service: their access tier, and what that tier is allowed.

Two tiers (`users.access_tier`):

- `invited`: an admin allowlisted the person (`/admin`, `readablestem users allow`, or an
  address in `ADMIN_EMAILS`). They keep the limits the service was built with:
  `PER_USER_DAILY_PAGE_QUOTA`, the whole `DAILY_SPEND_CAP_USD`, no cap on review re-reads
  beyond the rate limits.
- `open`: the person signed themselves up while `SIGNUP_MODE=open`. Smaller limits, because
  nobody has met them: `OPEN_TIER_DAILY_PAGE_QUOTA` pages and `OPEN_TIER_DAILY_REREADS`
  model reads from review a day, and their conversions draw on a separate daily spend pool,
  `OPEN_TIER_DAILY_SPEND_CAP_USD`, that sits inside the daily cap (`web.spend`).

Whether a person may sign in at all is `users.is_allowed`, in both tiers and both modes: an
admin can block an open-tier person, and the block holds until an admin lifts it. Blocking
does not change the tier, so lifting it brings the person back with their own limits.
Allowlisting someone (`auth.set_allowed(allowed=True)`) makes them `invited`: it is how an
open-tier person gets the instructors' limits.

This module has no state. The functions take the tier as a string so a caller that already
has the user row does not read it again.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.config import Settings
from readablestem.web.db import User

INVITED = "invited"
OPEN = "open"
TIERS = (INVITED, OPEN)

# The spend pool of a tier: None is the global cap alone, "open" is the pool inside it.
POOL_OPEN = "open"


def tier_of(session: Session, user_id: str) -> str:
    """The tier of a user; "invited" for one that does not exist (a deleted account's jobs
    are gone with it, so nothing is limited by this answer)."""
    return session.scalar(select(User.access_tier).where(User.id == user_id)) or INVITED


def is_open(tier: str) -> bool:
    return tier == OPEN


def page_quota(settings: Settings, tier: str) -> int | None:
    """Pages a person of `tier` may submit per UTC day; None is unlimited (development)."""
    return (
        settings.open_tier_daily_page_quota if is_open(tier) else settings.per_user_daily_page_quota
    )


def reread_allowance(settings: Settings, tier: str) -> int | None:
    """Review re-reads per UTC day for `tier`; None: no allowance beyond the rate limits
    (the invited tier)."""
    return settings.open_tier_daily_rereads if is_open(tier) else None


def spend_pool(tier: str) -> str | None:
    """The pool a job of this tier's owner draws on (`DbSpendGuard.pooled`)."""
    return POOL_OPEN if is_open(tier) else None
