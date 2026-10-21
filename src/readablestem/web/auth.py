"""Sign-in: Google and Microsoft OpenID Connect, with who may sign in kept in the database.

- Google sign-in uses Authlib's Starlette client (discovery document, scope "openid email
  profile", nonce-checked ID token). Only a verified email is trusted. The Google account
  is recorded in `auth_identities` (`web/identities.py`); access is still by email.
- Microsoft (Entra ID) sign-in, when MS_CLIENT_ID, MS_CLIENT_SECRET and MS_ALLOWED_TENANTS
  are set: work and school accounts of the allowed directories only, the ID token checked
  by `web/microsoft.py`, and people recognised by their identity once it is linked.
- Who may sign in is `users.is_allowed`, and `SIGNUP_MODE` says what happens to a person
  with no row. `allowlist` (the default): they get a polite 403 page with the contact
  address, and no row is created, so their email is not stored. `open`: the first sign-in
  with a verified identity creates the row, allowed, in the "open" access tier (`web.access`:
  smaller daily limits), unless today's limit on new accounts is used up (`signups_full`)
  or the per-address sign-up rate rule says wait. A row with `is_allowed` false is a person
  an admin blocked: refused in both modes (with the blocked wording in open mode), never
  made again by a sign-in.
- Admin emails from settings are created/updated as allowed admins at startup.
- Sessions are signed cookies (Starlette SessionMiddleware, 7 days, SameSite=Lax, Secure
  in production) and are rotated at sign-in. Every state-changing POST carries a
  per-session CSRF token (`deps.require_csrf`). Sign-out is a POST for the same reason.
- Signing in goes back to the page (or the review decision) the session ended on, when
  there was one (`web.return_to`), else to `/app`.
- `/dev-login` (any email, no password; the mode and the allowlist still apply) exists only
  when DEV_LOGIN_ENABLED is set and ENV is dev or test.
- A signed-in person can delete their own account (`/account`): `delete_account`.
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from readablestem.config import Settings
from readablestem.web import access, events, linked_emails, microsoft
from readablestem.web.database import rowcount
from readablestem.web.db import Job, JobStatus, User, session_scope, utcnow
from readablestem.web.deps import (
    PageError,
    current_user,
    enforce,
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
    OpenSignup,
    SignIn,
    create_open_user,
    microsoft_sign_in,
    remember,
)
from readablestem.web.logsafe import log_event, log_exception
from readablestem.web.quota import next_day_start
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

    Adding makes the person `invited` (`web.access`): a new row, or an existing one, which is
    how an open-tier person gets the instructors' limits, and how a blocked one is let back
    in as an instructor. (Lifting a block and keeping the tier is `set_blocked`.)

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
        user = User(email=email, is_allowed=True, is_admin=bool(admin), access_tier=access.INVITED)
        session.add(user)
        session.flush()
        return user
    user.is_allowed = allowed
    if allowed:
        user.access_tier = access.INVITED
    if not allowed:
        user.is_admin = False
        # Signed-in sessions end now and stay ended if they are allowed back later.
        user.session_version += 1
        stop_jobs_of(session, user.id, "access_removed")
    elif admin is not None:
        user.is_admin = admin
    return user


def set_blocked(session: Session, email: str, *, blocked: bool) -> User | None:
    """Block or unblock the person with this email; None when nobody has it.

    Blocking is `set_allowed(allowed=False)`: they are signed out everywhere, their queued
    and running jobs are stopped, they lose admin rights, and a sign-in is refused in either
    sign-up mode, without making a new row. Unblocking only lets them sign in again: their
    tier stays what it was (an open-tier person keeps the open limits; allowlisting them
    is what makes them `invited`) and an admin they were is not an admin again."""
    if blocked:
        return set_allowed(session, email, allowed=False)
    user = session.scalar(select(User).where(User.email == email))
    if user is not None:
        user.is_allowed = True
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


def open_signup_for(settings: Settings, before_create=None) -> OpenSignup | None:
    """The sign-up rules when `SIGNUP_MODE=open`, else None (nobody is made by signing in)."""
    if not settings.signup_open:
        return None
    return OpenSignup(settings.max_new_accounts_per_day, before_create or (lambda: None))


def sign_in_by_email(
    factory: sessionmaker[Session],
    settings: Settings,
    email: str,
    name: str | None,
    *,
    before_create=None,
) -> SignIn:
    """Record the sign-in of the person `email` names and say who they are, or why nobody.

    `email` is the person's own address or one an admin linked to them
    (`web.linked_emails`). An allowed user signs in. With no row, sign-up open and room in
    today's count, the account is made (`created`). A row that is not allowed is a blocked
    person: refused, and never made again. `before_create` runs just before an account would
    be made (the per-address rate rule) and may raise."""
    open_signup = open_signup_for(settings, before_create)
    try:
        return _sign_in_by_email(factory, email, name, open_signup)
    except IntegrityError:  # the same address signing up in two tabs: the second finds the row
        return _sign_in_by_email(factory, email, name, open_signup)


def _sign_in_by_email(
    factory: sessionmaker[Session], email: str, name: str | None, open_signup: OpenSignup | None
) -> SignIn:
    with session_scope(factory) as session:
        created = False
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = linked_emails.user_for_linked(session, email)
        if user is None and open_signup is not None:
            user = create_open_user(session, open_signup, email, name)
            if user is None:
                return SignIn(refused="signups_full", email=email)
            created = True
        if user is None:
            return SignIn(refused="not_allowlisted", email=email)
        if not user.is_allowed:
            return SignIn(
                refused="blocked" if open_signup is not None else "not_allowlisted", email=email
            )
        user.last_login_at = utcnow()
        if name and not created:
            user.name = name[:200]
        return SignIn(user_id=user.id, session_version=user.session_version, created=created)


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
    """Sign in by email (Google, dev login), recording the Google identity. A person who
    has no account yet gets one when sign-up is open."""
    ctx = get_ctx(request)
    result = sign_in_by_email(
        ctx.factory,
        ctx.settings,
        email,
        name,
        before_create=lambda: enforce(request, "signup"),
    )
    if result.user_id is None:
        return refused_page(request, result, provider)
    if identity is not None:
        remember(ctx.factory, result.user_id, identity)
    return signed_in(request, result.user_id, result.session_version, provider, result.created)


def refused_page(request: Request, result: SignIn, provider: str) -> Response:
    """The page for a sign-in that signs nobody in: the day's sign-ups are full, or the
    person has no access (not on the list, or blocked). Nothing about them is stored."""
    log_event(logger, "login_refused", reason=result.refused, provider=provider)
    if result.refused == "signups_full":
        return render(
            request,
            "signups_full.html",
            {"email": result.email, "next_reset": next_day_start(utcnow())},
            status_code=503,
            headers={"Retry-After": "3600"},
        )
    return render(
        request,
        "not_allowed.html",
        {"email": result.email, "blocked": result.refused == "blocked"},
        status_code=403,
    )


def finish_microsoft_login(request: Request, account: microsoft.MicrosoftAccount) -> Response:
    """Sign in by Microsoft identity, linking it by email the first time (and, when
    sign-up is open, making the account if the address has none)."""
    ctx = get_ctx(request)
    identity = Identity(MICROSOFT, account.issuer, account.subject, tenant=account.tenant)
    email = next(filter(None, map(normalise_email, account.email_claims)), None)
    result = microsoft_sign_in(
        ctx.factory,
        identity,
        email,
        account.name,
        open_signup=open_signup_for(ctx.settings, lambda: enforce(request, "signup")),
    )
    if result.refused == "no_email":
        log_event(logger, "login_refused", reason="no_email", provider=MICROSOFT)
        raise PageError(
            403,
            "No email address",
            "Microsoft did not share an email address for your account, so we cannot tell "
            "which account is yours.",
        )
    if result.user_id is None:
        return refused_page(request, result, MICROSOFT)
    return signed_in(request, result.user_id, result.session_version, MICROSOFT, result.created)


def signed_in(
    request: Request, user_id: str, version: int, provider: str, created: bool = False
) -> Response:
    """The end of every sign-in (Google, Microsoft, dev): back to the page that asked for
    it (`web.return_to`), and the usage event. `created`: the sign-in made the account."""
    ctx = get_ctx(request)
    target = take_return(request)  # before the session is rotated (web.return_to)
    start_session(request, user_id, version)
    log_event(logger, "login", user_id=user_id, provider=provider)
    if created:
        log_event(logger, "signup", user_id=user_id, provider=provider)
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
