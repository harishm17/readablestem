"""Sign-in: Google OpenID Connect plus an allowlist kept in the database.

- Google sign-in uses Authlib's Starlette client (discovery document, scope "openid email
  profile", nonce-checked ID token). Only a verified email is trusted.
- The allowlist is `users.is_allowed`. People not on it get a polite 403 page with the
  contact address; no row is created for them, so their email is not stored.
- Admin emails from settings are created/updated as allowed admins at startup.
- Sessions are signed cookies (Starlette SessionMiddleware, 7 days, SameSite=Lax, Secure
  in production) and are rotated at sign-in. Every state-changing POST carries a
  per-session CSRF token (`deps.require_csrf`). Sign-out is a POST for the same reason.
- `/dev-login` (any email, no password; the allowlist still applies) exists only when
  DEV_LOGIN_ENABLED is set and ENV is dev or test.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.web.db import User, session_scope, utcnow
from a11y_stem.web.deps import (
    PageError,
    current_user,
    get_ctx,
    render,
    require_csrf,
    start_session,
)
from a11y_stem.web.logsafe import log_event

logger = logging.getLogger(__name__)

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_SCOPE = "openid email profile"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter()


def make_oauth(settings: Settings) -> Any | None:
    """Authlib registry with the "google" client, or None if credentials are not set."""
    if not settings.google_client_id or settings.google_client_secret is None:
        return None
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        server_metadata_url=GOOGLE_METADATA_URL,
        client_kwargs={"scope": GOOGLE_SCOPE},
    )
    return oauth


def normalise_email(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    return email if len(email) <= 320 and _EMAIL.match(email) else None


# --------------------------------------------------------------------------- allowlist


def set_allowed(
    session: Session, email: str, *, allowed: bool, admin: bool | None = None
) -> User | None:
    """Add (allowed=True) or remove someone. Removing an unknown email returns None."""
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        if not allowed:
            return None
        user = User(email=email, is_allowed=True, is_admin=bool(admin))
        session.add(user)
        session.flush()
        return user
    user.is_allowed = allowed
    if not allowed:
        user.is_admin = False
    elif admin is not None:
        user.is_admin = admin
    return user


def seed_admins(factory: sessionmaker[Session], emails: list[str]) -> None:
    with session_scope(factory) as session:
        for raw in emails:
            email = normalise_email(raw)
            if email:
                set_allowed(session, email, allowed=True, admin=True)


def allowed_user_id(factory: sessionmaker[Session], email: str, name: str | None) -> str | None:
    """Record the sign-in for an allowlisted user and return their id, else None."""
    with session_scope(factory) as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None or not user.is_allowed:
            return None
        user.last_login_at = utcnow()
        if name:
            user.name = name[:200]
        return user.id


def finish_login(request: Request, email: str, name: str | None) -> Response:
    ctx = get_ctx(request)
    user_id = allowed_user_id(ctx.factory, email, name)
    if user_id is None:
        log_event(logger, "login_refused", reason="not_allowlisted")
        return render(request, "not_allowed.html", {"email": email}, status_code=403)
    start_session(request, user_id)
    log_event(logger, "login", user_id=user_id)
    return RedirectResponse("/app", status_code=303)


# --------------------------------------------------------------------------- routes


@router.get("/login")
def login_page(request: Request) -> Response:
    if current_user(request) is not None:
        return RedirectResponse("/app", status_code=303)
    return render(request, "login.html")


@router.post("/login", dependencies=[Depends(require_csrf)])
async def login_start(request: Request) -> Response:
    ctx = get_ctx(request)
    if ctx.oauth is None:
        raise PageError(
            503, "Sign-in is not available", "Google sign-in is not configured on this server."
        )
    redirect_uri = f"{ctx.settings.base_url.rstrip('/')}/auth/callback"
    return await ctx.oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def auth_callback(request: Request) -> Response:
    ctx = get_ctx(request)
    if ctx.oauth is None:
        raise PageError(404, "Page not found", "There is nothing at this address.")
    from authlib.integrations.starlette_client import OAuthError

    try:
        token = await ctx.oauth.google.authorize_access_token(request)
    except OAuthError:
        log_event(logger, "login_failed", reason="oauth_error")
        raise PageError(
            400, "Sign-in did not finish", "Google did not complete the sign-in. Please try again."
        ) from None
    info = token.get("userinfo") or {}
    if info.get("email_verified") not in (True, "true"):
        log_event(logger, "login_refused", reason="email_unverified")
        raise PageError(
            403,
            "Email address not verified",
            "Your Google account's email address is not verified, so we cannot sign you in.",
        )
    email = normalise_email(info.get("email"))
    if email is None:
        log_event(logger, "login_refused", reason="no_email")
        raise PageError(403, "No email address", "Google did not share an email address.")
    name = info.get("name") if isinstance(info.get("name"), str) else None
    return await run_in_threadpool(finish_login, request, email, name)


def _require_dev_login(request: Request) -> None:
    if not get_ctx(request).dev_login_enabled:
        raise PageError(404, "Page not found", "There is nothing at this address.")


@router.get("/dev-login", dependencies=[Depends(_require_dev_login)])
def dev_login_page(request: Request) -> Response:
    return render(request, "dev_login.html", {"error": "", "email": ""})


@router.post("/dev-login", dependencies=[Depends(_require_dev_login), Depends(require_csrf)])
def dev_login(request: Request, email: Annotated[str, Form()] = "") -> Response:
    normalised = normalise_email(email)
    if normalised is None:
        return render(
            request,
            "dev_login.html",
            {"error": "Enter an email address, like name@example.org.", "email": email},
            status_code=422,
        )
    return finish_login(request, normalised, None)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/", status_code=303)
