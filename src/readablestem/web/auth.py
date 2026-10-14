"""Sign-in: Google and Microsoft OpenID Connect plus an allowlist kept in the database.

- Google sign-in uses Authlib's Starlette client (discovery document, scope "openid email
  profile", nonce-checked ID token). Only a verified email is trusted. The Google account
  is recorded in `auth_identities` (`web/identities.py`); access is still by email.
- Microsoft (Entra ID) sign-in, when MS_CLIENT_ID, MS_CLIENT_SECRET and MS_ALLOWED_TENANTS
  are set: work and school accounts of the allowed directories only, the ID token checked
  by `web/microsoft.py`, and people recognised by their identity once it is linked.
- The allowlist is `users.is_allowed`. People not on it get a polite 403 page with the
  contact address; no row is created for them, so their email is not stored.
- Admin emails from settings are created/updated as allowed admins at startup.
- Sessions are signed cookies (Starlette SessionMiddleware, 7 days, SameSite=Lax, Secure
  in production) and are rotated at sign-in. Every state-changing POST carries a
  per-session CSRF token (`deps.require_csrf`). Sign-out is a POST for the same reason.
- Signing in goes back to the page (or the review decision) the session ended on, when
  there was one (`web.return_to`), else to `/app`.
- `/dev-login` (any email, no password; the allowlist still applies) exists only when
  DEV_LOGIN_ENABLED is set and ENV is dev or test.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from readablestem.config import Settings
from readablestem.web import events, linked_emails, microsoft
from readablestem.web.database import rowcount
from readablestem.web.db import Job, JobStatus, User, session_scope, utcnow
from readablestem.web.deps import (
    PageError,
    current_user,
    flash,
    get_ctx,
    limited,
    render,
    require_csrf,
    start_session,
)
from readablestem.web.identities import (
    GOOGLE,
    GOOGLE_ISSUER,
    MICROSOFT,
    Identity,
    microsoft_sign_in,
    remember,
)
from readablestem.web.logsafe import log_event, log_exception
from readablestem.web.return_to import take_return

logger = logging.getLogger(__name__)

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_SCOPE = "openid email profile"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter()


def make_oauth(settings: Settings) -> Any | None:
    """Authlib registry with a "google" client and a "microsoft" one, each only when its
    settings are set; None when neither is (`AppContext.sign_in_client` asks for one)."""
    google_secret = settings.google_client_secret
    google = bool(settings.google_client_id) and google_secret is not None
    if not google and not settings.ms_sign_in_enabled:
        return None
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    if google_secret is not None and settings.google_client_id:
        oauth.register(
            name=GOOGLE,
            client_id=settings.google_client_id,
            client_secret=google_secret.get_secret_value(),
            server_metadata_url=GOOGLE_METADATA_URL,
            client_kwargs={"scope": GOOGLE_SCOPE},
        )
    if settings.ms_sign_in_enabled:
        microsoft.register_microsoft(oauth, settings)
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
    """Add (allowed=True) or remove someone. Removing an unknown email returns None.

    Removing someone also stops their work: their queued and running jobs are failed with
    "access_removed", so nothing more of theirs is converted or charged to the shared
    daily budget (a running conversion stops at its next spend reservation, which checks
    the job is still running). Their documents are still deleted on schedule.
    """
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        if not allowed:
            return None
        if linked_emails.is_linked(session, email):
            raise linked_emails.LinkRefused(
                "is_linked",
                "That address is linked to another instructor as an extra Google account. "
                "Remove the link first.",
            )
        user = User(email=email, is_allowed=True, is_admin=bool(admin))
        session.add(user)
        session.flush()
        return user
    user.is_allowed = allowed
    if not allowed:
        user.is_admin = False
        # Signed-in sessions end now and stay ended if they are allowed back later.
        user.session_version += 1
        stop_jobs_of(session, user.id, "access_removed")
    elif admin is not None:
        user.is_admin = admin
    return user


def stop_jobs_of(session: Session, user_id: str, code: str) -> int:
    """Fail the user's queued and running jobs with `code`. Returns how many."""
    result = session.execute(
        update(Job)
        .where(Job.user_id == user_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .values(status=JobStatus.FAILED, error_code=code, finished_at=utcnow(), lease_until=None)
        .execution_options(synchronize_session=False)
    )
    return rowcount(result)


def seed_admins(factory: sessionmaker[Session], emails: list[str]) -> None:
    with session_scope(factory) as session:
        for raw in emails:
            email = normalise_email(raw)
            if email:
                try:
                    set_allowed(session, email, allowed=True, admin=True)
                except linked_emails.LinkRefused:
                    log_event(logger, "admin_seed_skipped", reason="linked_address")


def allowed_user(
    factory: sessionmaker[Session], email: str, name: str | None
) -> tuple[str, int] | None:
    """Record the sign-in for an allowlisted user and return (id, session version),
    else None. `email` is the person's own address or one an admin linked to them
    (`web.linked_emails`)."""
    with session_scope(factory) as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = linked_emails.user_for_linked(session, email)
        if user is None or not user.is_allowed:
            return None
        user.last_login_at = utcnow()
        if name:
            user.name = name[:200]
        return user.id, user.session_version


def end_sessions(factory: sessionmaker[Session], user_id: str) -> None:
    """Sign the user out everywhere: every session signed in before now stops working."""
    with session_scope(factory) as session:
        session.execute(
            update(User)
            .where(User.id == user_id)
            .values(session_version=User.session_version + 1)
            .execution_options(synchronize_session=False)
        )


def finish_login(
    request: Request,
    email: str,
    name: str | None,
    *,
    provider: str = "dev",
    identity: Identity | None = None,
) -> Response:
    """Sign in by allowlisted email (Google, dev login), recording the Google identity."""
    ctx = get_ctx(request)
    found = allowed_user(ctx.factory, email, name)
    if found is None:
        log_event(logger, "login_refused", reason="not_allowlisted", provider=provider)
        return render(request, "not_allowed.html", {"email": email}, status_code=403)
    user_id, version = found
    if identity is not None:
        remember(ctx.factory, user_id, identity)
    return signed_in(request, user_id, version, provider)


def finish_microsoft_login(request: Request, account: microsoft.MicrosoftAccount) -> Response:
    """Sign in by Microsoft identity, linking it by email the first time."""
    ctx = get_ctx(request)
    identity = Identity(MICROSOFT, account.issuer, account.subject, tenant=account.tenant)
    email = next(filter(None, map(normalise_email, account.email_claims)), None)
    result = microsoft_sign_in(ctx.factory, identity, email, account.name)
    if result.refused == "no_email":
        log_event(logger, "login_refused", reason="no_email", provider=MICROSOFT)
        raise PageError(
            403,
            "No email address",
            "Microsoft did not share an email address for your account, so we cannot find "
            "you on the beta list.",
        )
    if result.user_id is None:
        log_event(logger, "login_refused", reason="not_allowlisted", provider=MICROSOFT)
        return render(request, "not_allowed.html", {"email": result.email}, status_code=403)
    return signed_in(request, result.user_id, result.session_version, MICROSOFT)


def signed_in(request: Request, user_id: str, version: int, provider: str) -> Response:
    """The end of every sign-in (Google, Microsoft, dev): back to the page that asked for
    it (`web.return_to`), and the usage event."""
    ctx = get_ctx(request)
    target = take_return(request)  # before the session is rotated (web.return_to)
    start_session(request, user_id, version)
    log_event(logger, "login", user_id=user_id, provider=provider)
    events.record(ctx.factory, ctx.settings, user_id, events.SIGNED_IN)  # usage event
    return RedirectResponse(target, status_code=303)


# --------------------------------------------------------------------------- routes


@router.get("/login")
def login_page(request: Request) -> Response:
    if current_user(request) is not None:
        return RedirectResponse("/app", status_code=303)
    return render(request, "login.html")


@router.post("/login", dependencies=[limited("login"), Depends(require_csrf)])
async def login_start(request: Request) -> Response:
    ctx = get_ctx(request)
    google = ctx.sign_in_client(GOOGLE)
    if google is None:
        raise PageError(
            503, "Sign-in is not available", "Google sign-in is not configured on this server."
        )
    redirect_uri = f"{ctx.settings.base_url.rstrip('/')}/auth/callback"
    return await google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", dependencies=[limited("callback")])
async def auth_callback(request: Request) -> Response:
    ctx = get_ctx(request)
    google = ctx.sign_in_client(GOOGLE)
    if google is None:
        raise PageError(404, "Page not found", "There is nothing at this address.")
    from authlib.integrations.starlette_client import OAuthError

    try:
        token = await google.authorize_access_token(request)
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
    subject = info.get("sub")
    identity = (
        Identity(GOOGLE, GOOGLE_ISSUER, subject)
        if isinstance(subject, str) and 0 < len(subject) <= microsoft.MAX_SUBJECT_CHARS
        else None
    )
    return await run_in_threadpool(
        finish_login, request, email, name, provider=GOOGLE, identity=identity
    )


# --------------------------------------------------------------------------- Microsoft

MS_DID_NOT_FINISH = (
    "Microsoft did not complete the sign-in. Please try again. If Microsoft asked for an "
    "administrator's approval, your university's IT team needs to approve this app first."
)


@router.post("/login/microsoft", dependencies=[limited("login"), Depends(require_csrf)])
async def microsoft_login_start(request: Request) -> Response:
    ctx = get_ctx(request)
    client = ctx.sign_in_client(MICROSOFT)
    if client is None:
        raise PageError(
            503, "Sign-in is not available", "Microsoft sign-in is not set up on this server."
        )
    redirect_uri = f"{ctx.settings.base_url.rstrip('/')}/auth/microsoft/callback"
    try:
        return await client.authorize_redirect(request, redirect_uri)
    except Exception as exc:  # Microsoft's discovery document could not be fetched
        log_exception(logger, "login_failed", exc, reason="provider_error", provider=MICROSOFT)
        raise PageError(
            503,
            "Sign-in is not available right now",
            "Microsoft did not answer. Please try again in a few minutes.",
        ) from None


@router.get("/auth/microsoft/callback", dependencies=[limited("callback")])
async def microsoft_callback(request: Request) -> Response:
    ctx = get_ctx(request)
    client = ctx.sign_in_client(MICROSOFT)
    if client is None:
        raise PageError(404, "Page not found", "There is nothing at this address.")
    if request.query_params.get("admin_consent"):
        return _back_from_admin_consent(request)
    from authlib.integrations.starlette_client import OAuthError

    try:
        id_token, nonce = await microsoft.exchange_code(request, client)
        account = await microsoft.verified_account(
            client, id_token, nonce=nonce, settings=ctx.settings, now=time.time()
        )
    except microsoft.TokenRefused as refused:
        event = "login_failed" if refused.reason in microsoft.FLOW_FAILURES else "login_refused"
        log_event(logger, event, reason=refused.reason, provider=MICROSOFT)
        if refused.reason == "tenant_not_allowed":
            raise PageError(
                403,
                "Your organisation is not set up for this beta",
                "",
                template="org_not_set_up.html",
                context={"tenant": refused.tenant},
            ) from None
        raise PageError(400, "Sign-in did not finish", MS_DID_NOT_FINISH) from None
    except OAuthError:
        log_event(logger, "login_failed", reason="oauth_error", provider=MICROSOFT)
        raise PageError(400, "Sign-in did not finish", MS_DID_NOT_FINISH) from None
    except Exception as exc:  # Microsoft unreachable, or an answer that is not JSON
        log_exception(logger, "login_failed", exc, reason="provider_error", provider=MICROSOFT)
        raise PageError(400, "Sign-in did not finish", MS_DID_NOT_FINISH) from None
    return await run_in_threadpool(finish_microsoft_login, request, account)


def _back_from_admin_consent(request: Request) -> Response:
    """A university's IT admin coming back from the admin consent link (deploy.md, step
    3b): Microsoft sends them to the same callback, with `admin_consent` and no code. Not a
    sign-in; the `tenant` Microsoft adds decides nothing (anyone can type the URL), so
    the page only thanks them, or says the approval did not finish."""
    approved = not request.query_params.get("error")
    log_event(logger, "ms_admin_consent_returned", approved=approved)
    flash(
        request,
        "Thank you. Microsoft has sent you back from the approval step. Once your "
        "organisation is set up for this beta, people there can sign in with their "
        "university account."
        if approved
        else "Microsoft did not finish the approval. You can open the approval link again.",
    )
    return RedirectResponse("/login", status_code=303)


def _require_dev_login(request: Request) -> None:
    if not get_ctx(request).dev_login_enabled:
        raise PageError(404, "Page not found", "There is nothing at this address.")


@router.get("/dev-login", dependencies=[Depends(_require_dev_login)])
def dev_login_page(request: Request) -> Response:
    return render(request, "dev_login.html", {"error": "", "email": ""})


@router.post(
    "/dev-login",
    dependencies=[Depends(_require_dev_login), limited("login"), Depends(require_csrf)],
)
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
    """Sign out, here and everywhere: the cookie is signed, not stored, so clearing it
    in this browser would leave a copy of it valid for 7 days. Bumping the user's
    session version ends every session they have (other browsers too)."""
    user = current_user(request)
    if user is not None:
        end_sessions(get_ctx(request).factory, user.id)
        log_event(logger, "logout", user_id=user.id)
    request.session.clear()
    return RedirectResponse("/", status_code=303)
