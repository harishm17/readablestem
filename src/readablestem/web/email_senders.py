"""Who sends the sign-in emails (T3-10): a small interface and a registry, no provider.

The email sign-in link (EMAIL_LOGIN_ENABLED, `web.email_login`) needs something that can
send one plain-text message to one address. This module is the seam for it: a `LinkSender`
is any object with `send(message)`, registered under a name, and EMAIL_SENDER picks the
name. **No email provider is wired in.** Sending mail is a new data processor (it sees
the address and the time of every sign-in), so which provider, on what terms, and what the
privacy page says about it are Harish's decisions.
Until a sender that is marked `production_ok` is registered, production refuses to start
with EMAIL_LOGIN_ENABLED=true (`Settings.check_production`).

Registered here:

- `console`: writes the message to standard output. For a developer's own machine; never
  `production_ok`.

Tests register their own (`tests/fixtures/web/support.py`, `OutboxSender`).

To add a provider: write a class with `send`, call `register_sender("name", factory,
production_ok=True)` where the app starts, set EMAIL_SENDER=name, and add the provider to
the privacy page and docs/security.md ("Where document data goes" is about documents; the
sign-in address is the new thing to name).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from readablestem.config import Settings


@dataclass(frozen=True)
class LoginEmail:
    """One message: plain text only, no tracking, no images."""

    to: str
    subject: str
    body: str


class LinkSender(Protocol):
    def send(self, message: LoginEmail) -> None:
        """Deliver `message`. Raise on failure; the caller logs the type, never the text."""
        ...


SenderFactory = Callable[["Settings"], LinkSender]


@dataclass(frozen=True)
class _Registered:
    factory: SenderFactory
    production_ok: bool


_REGISTRY: dict[str, _Registered] = {}


def register_sender(name: str, factory: SenderFactory, *, production_ok: bool = False) -> None:
    _REGISTRY[name.strip().lower()] = _Registered(factory, production_ok)


def unregister_sender(name: str) -> None:
    _REGISTRY.pop(name.strip().lower(), None)


def sender_names(*, production: bool = False) -> list[str]:
    return sorted(n for n, r in _REGISTRY.items() if r.production_ok or not production)


def make_sender(settings: Settings) -> LinkSender | None:
    """The configured sender, or None when EMAIL_SENDER is unset or unknown."""
    name = (settings.email_sender or "").strip().lower()
    registered = _REGISTRY.get(name)
    return None if registered is None else registered.factory(settings)


def sender_problem(settings: Settings) -> str | None:
    """Why EMAIL_LOGIN_ENABLED=true cannot start, or None when it can (or is off)."""
    if not settings.email_login_enabled:
        return None
    name = (settings.email_sender or "").strip().lower()
    if not name:
        return "EMAIL_LOGIN_ENABLED is true but EMAIL_SENDER is not set"
    registered = _REGISTRY.get(name)
    if registered is None:
        return f"EMAIL_SENDER={name!r} is not a registered sender"
    if settings.env == "prod" and not registered.production_ok:
        return f"EMAIL_SENDER={name!r} is for development only; production needs a real sender"
    return None


class ConsoleSender:
    """Prints the message. Development only."""

    def send(self, message: LoginEmail) -> None:
        print(f"To: {message.to}\nSubject: {message.subject}\n\n{message.body}", file=sys.stdout)


register_sender("console", lambda settings: ConsoleSender(), production_ok=False)
