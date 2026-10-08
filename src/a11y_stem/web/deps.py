"""Per-app state, the signed-in user, CSRF tokens, flash messages and page rendering.

Everything a route needs hangs off one `AppContext` on `app.state`, so tests can build
several independent apps (each with its own database) in one process.
"""

from __future__ import annotations

import hmac
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

from a11y_stem.config import Settings
from a11y_stem.observability.tracing import Tracer
from a11y_stem.web.db import User, session_scope
from a11y_stem.web.spend import DbSpendGuard
from a11y_stem.web.uploads import UploadGate
from a11y_stem.web.worker import ClientFactory, Converter, Worker

CSRF_FIELD = "csrf_token"
SESSION_USER = "uid"
SESSION_CSRF = "csrf"
SESSION_FLASH = "flash"

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
    oauth: Any | None = None  # Authlib registry when Google sign-in is configured
    worker: Worker | None = None
    # Below this much headroom under the daily cap, new uploads are turned away and the
    # worker claims nothing: not even the largest single model call would fit
    # (`spend.largest_reservation_usd`).
    min_headroom_usd: float = 0.0
    # Handed to every conversion when tracing is on (`observability.tracing.make_tracer`).
    tracer: Tracer | None = None
    # Uploads in progress (total and per user); `create_app` sizes it from settings.
    uploads: UploadGate = field(default_factory=lambda: UploadGate(limit=3))

    @property
    def dev_login_enabled(self) -> bool:
        return self.settings.dev_login_enabled and self.settings.env in {"dev", "test"}


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    name: str | None
    is_admin: bool


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
    """No signed-in, allowlisted user: browsers are sent to the sign-in page."""


class CsrfFailed(Exception):
    pass


# --------------------------------------------------------------------------- user


def current_user(request: Request) -> CurrentUser | None:
    """The signed-in user, if they are still on the allowlist (checked on every request,
    so removing someone takes effect immediately)."""
    if hasattr(request.state, "user"):
        return request.state.user
    user: CurrentUser | None = None
    uid = request.session.get(SESSION_USER)
    if isinstance(uid, str):
        with session_scope(get_ctx(request).factory) as session:
            row = session.scalar(select(User).where(User.id == uid, User.is_allowed.is_(True)))
            if row is not None:
                user = CurrentUser(id=row.id, email=row.email, name=row.name, is_admin=row.is_admin)
        if user is None:  # deleted or removed from the allowlist since signing in
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


def start_session(request: Request, user_id: str) -> None:
    """Rotate the session on sign-in (drops any pre-login state, issues a new CSRF token)."""
    request.session.clear()
    request.session[SESSION_USER] = user_id
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


# --------------------------------------------------------------------------- flash


def flash(request: Request, message: str, *, element_id: str | None = None) -> None:
    """One-time notice for the next page. Content-free: it lives in the session cookie."""
    items = list(request.session.get(SESSION_FLASH, []))
    items.append({"message": message, "element_id": element_id})
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
        "google_login": ctx.oauth is not None,
        "dev_login": ctx.dev_login_enabled,
        "max_upload_mb": ctx.settings.max_upload_mb,
        "max_pages": ctx.settings.max_pages_per_job,
        "retention_hours": ctx.settings.retention_hours,
        "opt_in_days": ctx.settings.opt_in_retention_days,
    }
    return ctx.templates.TemplateResponse(
        request, template, base | (context or {}), status_code=status_code, headers=headers
    )
