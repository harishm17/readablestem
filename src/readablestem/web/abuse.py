"""Abuse limits that need no database: a per-address rate limit and the client address.

What this is for: a script guessing at the sign-in or cron endpoints, or hammering the upload
route (each upload that is not a PDF still costs a probe process), or making accounts
(`signup`, counted only when a sign-in would create one). It is a seatbelt for a free beta,
not a defence against a distributed flood (that is the host's and its edge's job):
accounts made from many addresses are bounded by the daily cap on new accounts
(`MAX_NEW_ACCOUNTS_PER_DAY`, kept in the database) and by the open-tier limits, not here.
Limits are kept in memory per instance, so with two instances the real allowance is twice
the numbers below; a restart forgets them. Both are acceptable here and stated in
docs/security.md.

`client_ip` reads the address from `X-Forwarded-For`, counting `trusted_proxy_hops` entries
from the right (the platform's front end appends the address it saw, so one hop, as on
Railway; another proxy in front, such as Cloudflare's, adds a second). Entries further left
are written by the client and are never believed. With no such header the peer address is used.

Nothing here is logged with an address: a limited request logs the rule name only
(`deps.enforce`, which turns a refusal into the 429 page).
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from starlette.requests import Request


@dataclass(frozen=True)
class Rule:
    """At most `limit` attempts per `window_s` seconds for one key."""

    limit: int
    window_s: int


# Rule name -> allowance. Generous for people, tight for scripts: a shared university
# address (a whole department behind one NAT) still has room.
RULES: dict[str, Rule] = {
    "login": Rule(10, 300),  # starting a sign-in: the buttons on /login
    "callback": Rule(20, 300),  # coming back from Google or Microsoft
    # New accounts made by open sign-up (SIGNUP_MODE=open), per address: counted when a
    # sign-in is about to create one, not on every sign-in. A shared university or office
    # address still has room for a class trying it together.
    "signup": Rule(5, 3600),
    "email_link": Rule(3, 900),  # sign-in links sent to one address (T3-10), whoever asks
    "upload": Rule(30, 600),  # uploads, per address; accepted or not
    "upload_user": Rule(15, 600),  # uploads, per signed-in person
    "retry": Rule(20, 600),  # "Try again" on a stopped document, per address
    "retry_user": Rule(10, 600),  # ... per signed-in person
    "cron": Rule(10, 300),  # the scheduler's endpoint, per address
    "sample": Rule(10, 600),  # opening the sample document, per address
    # Review edits that cost something (T3-5, T3-11): a region cut from the PDF and a read
    # by the model, per signed-in person. Generous for a person fixing a document, tight
    # for a script; the job's spend caps are the other bound.
    "edit_region": Rule(60, 600),
    "reread": Rule(20, 600),
}
MAX_KEYS = 20_000  # entries kept; a spoofed header must not grow memory without bound


@dataclass
class RateLimiter:
    rules: dict[str, Rule] = field(default_factory=lambda: dict(RULES))
    clock: Callable[[], float] = time.monotonic
    max_keys: int = MAX_KEYS
    _hits: dict[tuple[str, str], deque[float]] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self, rule_name: str, key: str) -> int | None:
        """Count one attempt. None when allowed, else the seconds until it would be."""
        rule = self.rules[rule_name]
        now = self.clock()
        with self._lock:
            hits = self._hits.get((rule_name, key))
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    self._forget(now)
                hits = self._hits.setdefault((rule_name, key), deque())
            while hits and hits[0] <= now - rule.window_s:
                hits.popleft()
            if len(hits) >= rule.limit:
                return max(1, int(hits[0] + rule.window_s - now) + 1)
            hits.append(now)  # refused attempts are not counted: the wait always ends
            return None

    def _forget(self, now: float) -> None:
        """Drop keys with nothing inside their window; if that is not enough, the oldest."""
        for key in [k for k, hits in self._hits.items() if self._idle(k[0], hits, now)]:
            del self._hits[key]
        overflow = len(self._hits) - self.max_keys + 1
        if overflow > 0:
            oldest = sorted(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0)
            for key in oldest[:overflow]:
                del self._hits[key]

    def _idle(self, rule_name: str, hits: deque[float], now: float) -> bool:
        return not hits or hits[-1] <= now - self.rules[rule_name].window_s


def client_ip(request: Request, trusted_hops: int) -> str:
    """The caller's address as the limiter keys it: IPv4 as is, IPv6 by its /64."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    chosen = peer
    if trusted_hops > 0 and forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            chosen = parts[-trusted_hops]
    try:
        address = ipaddress.ip_address(chosen)
    except ValueError:
        return peer  # not an address: do not let a header choose its own key
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{address}/64", strict=False).network_address)
    return str(address)
