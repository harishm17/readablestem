"""Per-app state, the signed-in user, CSRF tokens, flash messages and page rendering.

Everything a route needs hangs off one `AppContext` on `app.state`, so tests can build
several independent apps (each with its own database) in one process.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from readablestem.config import Settings
from readablestem.observability.tracing import Tracer
from readablestem.web import seo
from readablestem.web.abuse import RateLimiter, client_ip
from readablestem.web.db import User, session_scope
from readablestem.web.logsafe import log_event
from readablestem.web.spend import DbSpendGuard
from readablestem.web.uploads import UploadGate
from readablestem.web.worker import ClientFactory, Converter, Worker

CSRF_FIELD = "csrf_token"
SESSION_USER = "uid"
SESSION_VERSION = "sv"  # users.session_version when the session was signed in
SESSION_CSRF = "csrf"
SESSION_FLASH = "flash"

logger = logging.getLogger(__name__)

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
ELEMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class AppContext:
    settings: Settings
    engine: Engine
    factory: sessionmaker[Session]
    templates: Jinja2Templates
    converter: Converter
    client_factory: ClientFactory
    guard: DbSpendGuard
    oauth: Any | None = None  # Authlib registry when Google or Microsoft sign-in is set up
    worker: Worker | None = None
    # Below this much headroom under the daily cap, new uploads are turned away and the
    # worker claims nothing: not even the largest single model call would fit
    # (`spend.largest_reservation_usd`).
    min_headroom_usd: float = 0.0
    # Handed to every conversion when tracing is on (`observability.tracing.make_tracer`).
    tracer: Tracer | None = None
    # Uploads in progress (total and per user); `create_app` sizes it from settings.
    uploads: UploadGate = field(default_factory=lambda: UploadGate(limit=3))
    # Attempts per address and person on sign-in, upload and cron (`web.abuse`).
    limiter: RateLimiter = field(default_factory=RateLimiter)

    @property
    def dev_login_enabled(self) -> bool:
        return self.settings.dev_login_enabled and self.settings.env in {"dev", "test"}

    def sign_in_client(self, name: str) -> Any | None:
        """The Authlib client "google" or "microsoft", or None when it is not set up."""
        return None if self.oauth is None else self.oauth.create_client(name)


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    name: str | None
    is_admin: bool
    # "invited" or "open" (`web.access`): which daily limits and spend pool apply.
    access_tier: str = "invited"


class PageError(Exception):
    """Render an error page (status, title, message) instead of the normal response."""

    def __init__(
        self,
        status_code: int,
        title: str,
        message: str,
        *,
        template: str = "error.html",
        context: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status_code = status_code
        self.title = title
        self.message = message
        self.template = template
        self.context = context or {}
        self.headers = headers


class LoginRequired(Exception):
    """No signed-in, allowed user: browsers are sent to the sign-in page."""


class CsrfFailed(Exception):
    pass


# --------------------------------------------------------------------------- user


def current_user(request: Request) -> CurrentUser | None:
    """The signed-in user, if they are still allowed (`users.is_allowed`, checked on every
    request, so removing or blocking someone takes effect immediately) and the session is
    not older than their last sign-out (`users.session_version`), so a copied cookie stops
    working when its owner signs out."""
    if hasattr(request.state, "user"):
        return request.state.user
    user: CurrentUser | None = None
    uid = request.session.get(SESSION_USER)
    # Sessions signed in before the version existed carry none: version 0, still valid.
    version = request.session.get(SESSION_VERSION, 0)
    if isinstance(uid, str):
        with session_scope(get_ctx(request).factory) as session:
            row = session.scalar(select(User).where(User.id == uid, User.is_allowed.is_(True)))
            if row is not None and row.session_version == version:
                user = CurrentUser(
                    id=row.id,
                    email=row.email,
                    name=row.name,
                    is_admin=row.is_admin,
                    access_tier=row.access_tier,
                )
        if user is None:  # deleted, removed from the allowlist, blocked or signed out since
            request.session.pop(SESSION_USER, None)
    request.state.user = user
    return user


def require_user(request: Request) -> CurrentUser:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    return user


def require_admin(request: Request) -> CurrentUser:
    user = require_user(request)
    if not user.is_admin:
        raise PageError(403, "Admins only", "This page is only for the people who run the beta.")
    return user


UserDep = Annotated[CurrentUser, Depends(require_user)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]


def start_session(request: Request, user_id: str, session_version: int = 0) -> None:
    """Rotate the session on sign-in (drops any pre-login state, issues a new CSRF token)."""
    request.session.clear()
    request.session[SESSION_USER] = user_id
    request.session[SESSION_VERSION] = session_version
    request.session[SESSION_CSRF] = secrets.token_urlsafe(32)
    if hasattr(request.state, "user"):  # drop the cached anonymous lookup
        del request.state.user


# --------------------------------------------------------------------------- CSRF


def csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF] = token
    return token


def csrf_valid(request: Request, submitted: object) -> bool:
    expected = request.session.get(SESSION_CSRF)
    if not isinstance(expected, str) or not expected or not isinstance(submitted, str):
        return False
    return hmac.compare_digest(expected.encode(), submitted.encode())


async def require_csrf(request: Request) -> None:
    """Dependency for every state-changing POST that uses a normal form body."""
    form = await request.form()
    if not csrf_valid(request, form.get(CSRF_FIELD)):
        raise CsrfFailed()


# --------------------------------------------------------------------------- spend


def conversions_closed(ctx: AppContext, user: CurrentUser) -> str | None:
    """Why no new conversion can start for `user` today, or None while one can: "daily"
    (the day's spend cap is used up: for everybody) or "open" (the pool for new accounts
    is, and the day's cap is not: only for open-tier accounts, `web.spend`). One read."""
    snapshot = ctx.guard.snapshot()
    headroom = ctx.min_headroom_usd
    if snapshot.remaining_usd is not None and snapshot.remaining_usd <= headroom:
        return "daily"
    pool_left = snapshot.open_remaining_usd
    if user.access_tier == "open" and pool_left is not None and pool_left <= headroom:
        return "open"
    return None


# --------------------------------------------------------------------------- rate limits


def enforce(request: Request, rule_name: str, key: str | None = None) -> None:
    """Count one attempt against `rule_name` (for this address, or `key`); 429 page when
    the allowance is used up. Logs the rule name only, never the address."""
    ctx = get_ctx(request)
    if not ctx.settings.rate_limits_enabled:
        return
    key = key or client_ip(request, ctx.settings.trusted_proxy_hops)
    wait = ctx.limiter.check(rule_name, key)
    if wait is None:
        return
    log_event(logger, "rate_limited", logging.WARNING, rule=rule_name)
    minutes = max(1, round(wait / 60))
    raise PageError(
        429,
        "Too many attempts",
        f"Please wait about {minutes} minute{'' if minutes == 1 else 's'} and try again. "
        "Nothing was changed.",
        headers={"Retry-After": str(wait)},
    )


def limited(rule_name: str) -> Any:
    """A route dependency: `dependencies=[limited("login")]` counts the call per address."""

    def dependency(request: Request) -> None:
        enforce(request, rule_name)

    return Depends(dependency)


# --------------------------------------------------------------------------- flash


def flash(
    request: Request, message: str, *, element_id: str | None = None, undo: bool = True
) -> None:
    """One-time notice for the next page. Content-free: it lives in the session cookie.
    `undo` False: the notice is not about a review write, so the review screen offers no
    Undo under it (Undo would take back the write before, which the notice is not about)."""
    items = list(request.session.get(SESSION_FLASH, []))
    entry: dict[str, Any] = {"message": message, "element_id": element_id}
    if not undo:
        entry["undo"] = False
    items.append(entry)
    request.session[SESSION_FLASH] = items[-5:]


def pop_flash(request: Request) -> list[dict[str, Any]]:
    items = request.session.pop(SESSION_FLASH, [])
    return items if isinstance(items, list) else []


# --------------------------------------------------------------------------- render


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    ctx = get_ctx(request)
    base: dict[str, Any] = {
        "user": current_user(request),
        "csrf_token": csrf_token(request),
        "csrf_field": CSRF_FIELD,
        "flash": pop_flash(request),
        "source_url": ctx.settings.source_url,
        "contact_email": ctx.settings.contact_email,
        "feedback_url": ctx.settings.feedback_url,  # the footer's Feedback link
        "google_login": ctx.sign_in_client("google") is not None,
        "microsoft_login": ctx.sign_in_client("microsoft") is not None,
        "dev_login": ctx.dev_login_enabled,
        "email_login": ctx.settings.email_login_enabled,
        "email_link_minutes": ctx.settings.email_link_minutes,
        "max_upload_mb": ctx.settings.max_upload_mb,
        "max_pages": ctx.settings.max_pages_per_job,
        "retention_hours": ctx.settings.retention_hours,
        "opt_in_days": ctx.settings.opt_in_retention_days,
        # Course notation (FEATURE_NOTATION_MEMORY): its pages are in the main navigation.
        "courses_enabled": ctx.settings.feature_notation_memory,
        # Sign-up mode and the limits of the open tier, for the pages whose words differ
        # (docs/web.md "Sign-up modes").
        "signup_open": ctx.settings.signup_open,
        "open_page_quota": ctx.settings.open_tier_daily_page_quota,
        "open_rereads": ctx.settings.open_tier_daily_rereads,
        # Search engines: only the public pages are indexed (web.seo).
        "indexable": seo.is_indexable(request.url.path),
        "showcase_on": seo.SHOWCASE_DIR.is_dir(),
    }
    return ctx.templates.TemplateResponse(
        request, template, base | (context or {}), status_code=status_code, headers=headers
    )
