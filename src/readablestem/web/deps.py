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
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from readablestem.config import Settings
from readablestem.observability.tracing import Tracer
from readablestem.source import intake as source_intake
from readablestem.source import pandoc as pandoc_step
from readablestem.web import (
    analytics,
    dev_only,
    office_convert,
    recipients,
    report_item,
    retention,
    seo,
    signin_words,
    uploads,
)
from readablestem.web.abuse import RateLimiter, client_ip
from readablestem.web.db import User, session_scope
from readablestem.web.logsafe import log_event
from readablestem.web.provider_health import HEALTH, ProviderHealth
from readablestem.web.quota import retry_after
from readablestem.web.sandbox_status import SandboxStatus
from readablestem.web.security import CARRIES_SECRET
from readablestem.web.spend import DbSpendGuard
from readablestem.web.uploads import UploadGate
from readablestem.web.worker import ClientFactory, Converter, Worker
from readablestem.web.worker_rows import RowReader

CSRF_FIELD = "csrf_token"
SESSION_USER = "uid"
SESSION_VERSION = "sv"  # users.session_version when the session was signed in
SESSION_VERIFIED = "ev"  # this sign-in proved the account's own address (shares need it)
SESSION_CSRF = "csrf"
SESSION_FLASH = "flash"
RETENTION_NOTE = "retention_note"  # `request.state`: the new deletion time, said once

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
    # The last sandbox probe of this process (`web.sandbox_status`): LaTeX source needs a PASS.
    sandbox: SandboxStatus = field(default_factory=SandboxStatus)
    # With `WORKER_MODE=service`: the worker process's rows (`web.worker_rows`); None inline.
    rows: RowReader | None = None

    def provider_health(self) -> ProviderHealth:
        """The AI service as the worker's recent requests saw it: this process's own memory
        when the worker is a thread here, the worker process's `ops_heartbeat` row when not."""
        return HEALTH if self.rows is None else self.rows.provider()

    @property
    def dev_login_enabled(self) -> bool:
        return self.settings.dev_login_enabled and dev_only.available(self.settings)

    @property
    def latex_source_on(self) -> bool:
        """LaTeX source (.tex, a project zip) is taken: the flag is on and the sandbox passed."""
        return self.settings.feature_latex_source and self.sandbox.allows_source()

    @property
    def word_source_on(self) -> bool:
        """A `.docx` is read from the file itself and kept as uploaded (`routes.jobs._place_word`):
        the flag is on, pandoc is installed and the sandbox passed."""
        return (
            self.settings.feature_word_source
            and bool(pandoc_step.installed())
            and self.sandbox.allows_source()
        )

    def wake_worker(self) -> None:
        """A job was queued: the worker looks for it now, not at its next poll."""
        if self.worker is not None:
            self.worker.wake()

    def accepted_types(self, *, revision: bool = False) -> office_convert.AcceptedTypes:
        """What this installation takes, worded once (`office_convert.accepted_types`).
        `revision`: a revised version takes no LaTeX source."""
        kinds = office_convert.input_kinds(self.settings)
        return office_convert.accepted_types(kinds, self.latex_source_on and not revision)

    @property
    def model_vendor(self) -> str:
        """The company that gets pictures of equations and figures, from the same list the
        privacy page is written from (`web.recipients`)."""
        return recipients.model_vendor(self.settings)

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
    # This session's sign-in proved the address above: Google said it was verified, or the
    # Microsoft directory did (`xms_edov`). Only then does a share naming it apply
    # (`web.shares`): `preferred_username` and the email-link sign-in do not count.
    email_verified: bool = False


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
                    email_verified=request.session.get(SESSION_VERIFIED) is True,
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


def start_session(
    request: Request, user_id: str, session_version: int = 0, email_verified: bool = False
) -> None:
    """Rotate the session on sign-in (drops any pre-login state, issues a new CSRF token)."""
    request.session.clear()
    request.session[SESSION_USER] = user_id
    request.session[SESSION_VERSION] = session_version
    request.session[SESSION_VERIFIED] = email_verified
    request.session[SESSION_CSRF] = secrets.token_urlsafe(32)
    if hasattr(request.state, "user"):  # drop the cached anonymous lookup
        del request.state.user


# --------------------------------------------------------------------------- CSRF


def csrf_token(request: Request) -> str:
    # The page that prints this is never compressed (`security.CompressionMiddleware`) and
    # never cached for everyone (`routes/pages.py`, `public_page`).
    setattr(request.state, CARRIES_SECRET, True)
    token = request.session.get(SESSION_CSRF)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF] = token
    return token


class LazyCsrfToken:
    """The page's CSRF token, made only when a template prints it. A page with no form never
    asks, so it makes no session and sets no cookie (the second cookie of the site is the
    time zone's)."""

    def __init__(self, request: Request) -> None:
        self._request = request

    def __str__(self) -> str:
        return csrf_token(self._request)


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
    again = retry_after(wait)  # the wait the limiter knows, said and sent as one number
    title, what, context = REFUSAL_WORDS.get(
        rule_name, ("Too many attempts", "There have been too many attempts.", {})
    )
    raise PageError(
        429,
        title,
        f"{what} Try again {again.words}. Nothing was changed.",
        context=context,
        headers={"Retry-After": again.header},
    )


def give_back(request: Request, rule_name: str, key: str, count: int = 1) -> None:
    """Take `count` attempts back from `rule_name` for `key`: a request that was refused
    used none of the allowance of a rule that counts accepted work only."""
    ctx = get_ctx(request)
    if ctx.settings.rate_limits_enabled and count > 0:
        ctx.limiter.give_back(rule_name, key, count)


# What each limited action says when its allowance is used up, so a person knows which one
# they hit and when it clears. The sign-in rules link back to /login.
_SIGN_IN_BACK = {"back_href": "/login", "back_label": "Back to sign-in"}
_SIGN_IN_LIMITED = (
    "Too many sign-in attempts",
    "There have been several sign-in attempts from this network.",
    _SIGN_IN_BACK,
)
REFUSAL_WORDS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "login_google": _SIGN_IN_LIMITED,
    "login_microsoft": _SIGN_IN_LIMITED,
    "login_email": _SIGN_IN_LIMITED,
    "login_dev": _SIGN_IN_LIMITED,
    "callback": _SIGN_IN_LIMITED,
    "signup": (
        "Too many new accounts",
        "This network has made several new accounts in the last hour. An existing account "
        "can still sign in.",
        _SIGN_IN_BACK,
    ),
    "zip_user": (
        "Too many zip downloads",
        "Several zip files of your documents were made in the last few minutes.",
        {},
    ),
    "report": (
        "Too many reports",
        "Several reports were sent from this account in the last few minutes.",
        {},
    ),
}


def limited(rule_name: str) -> Any:
    """A route dependency: `dependencies=[limited("login")]` counts the call per address."""

    def dependency(request: Request) -> None:
        enforce(request, rule_name)

    return Depends(dependency)


# --------------------------------------------------------------------------- flash


def flash(
    request: Request,
    message: str,
    *,
    element_id: str | None = None,
    undo: bool = True,
    edit: bool = False,
    kind: Literal["info", "success", "warning"] = "info",
) -> None:
    """One-time notice for the next page. Content-free: it lives in the session cookie.
    `kind` is what the notice says (`components/notices.html`): `success` for something saved
    or done, `warning` for a refusal or something that was left as it was and needs a look,
    `info` for anything else (a thank-you, a repeat that changed nothing).
    `undo` False: the notice is not about a review write, so the review screen offers no
    Undo under it (Undo would take back the write before, which the notice is not about).
    `edit`: the write was a change to a page, not a decision, so the notice does not speak of
    decisions and its Undo is "Undo this change"."""
    items = list(request.session.get(SESSION_FLASH, []))
    # An action that restarted the deletion clock says the new time in its first notice
    # (`say_new_time`, web/retention.py).
    note = getattr(request.state, RETENTION_NOTE, "")
    if note:
        message = f"{message} {note}"
        setattr(request.state, RETENTION_NOTE, "")
    entry: dict[str, Any] = {"message": message, "element_id": element_id}
    if not undo:
        entry["undo"] = False
    if edit:
        entry["edit"] = True
    if kind != "info":
        entry["kind"] = kind
    items.append(entry)
    request.session[SESSION_FLASH] = items[-5:]


def say_new_time(request: Request, note: str) -> None:
    """Hand the sentence `retention.restart_clock_and_say` returned to the notice this
    request flashes next. A download has no notice, so its sentence is never used."""
    if note:
        setattr(request.state, RETENTION_NOTE, note)


def pop_flash(request: Request) -> list[dict[str, Any]]:
    items = request.session.pop(SESSION_FLASH, [])
    return items if isinstance(items, list) else []


# --------------------------------------------------------------------------- section

# Which part of the site a path belongs to, for the navigation's and the footer's current-page
# mark (`aria-current="page"` in `base.html`). A path belongs to the longest prefix here that it
# equals or continues after a "/"; a path in no section marks nothing. Pages inside a document
# (its page, review, share, Listen, record, draft, an upload) are "My documents".
SECTIONS: dict[str, str] = {
    "/": "home",
    "/app": "app",
    "/jobs": "app",
    "/uploads": "app",
    "/courses": "courses",
    "/account": "account",
    "/admin": "admin",
    "/demo": "demo",
    "/help": "help",
    "/help/review": "help-review",
    "/help/latex": "help-latex",
    "/help/posting": "posting",
    "/status": "status",
    "/privacy": "privacy",
    "/terms": "terms",
    "/limits": "limits",
    "/pricing": "pricing",
    "/security": "security",
    "/for-accessibility-offices": "offices",
    seo.GUIDES_INDEX: "guides",
    seo.ADA_GUIDE: "guide-ada",
    seo.COMPARE_GUIDE: "guide-compare",
    "/style": "style",
}


def current_section(path: str) -> str:
    """The section of the site `path` belongs to ("" when it belongs to none)."""
    if path == "/":
        return SECTIONS["/"]
    best = ""
    for prefix in SECTIONS:
        if prefix != "/" and (path == prefix or path.startswith(prefix + "/")):
            best = max(best, prefix, key=len)
    return SECTIONS.get(best, "")


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
        "active": current_section(request.url.path),  # the nav's and footer's current page
        "csrf_token": LazyCsrfToken(request),
        "csrf_field": CSRF_FIELD,
        "flash": pop_flash(request),
        "source_url": ctx.settings.source_url,
        "contact_email": ctx.settings.contact_email,
        "feedback_url": ctx.settings.feedback_url,  # the footer's Feedback link
        # "Report this item" on a review card and the stopped document's report link: the
        # app's own form, with the job and item ids.
        "report_url": report_item.item_url,
        "report_job_url": report_item.job_url,
        "google_login": ctx.sign_in_client("google") is not None,
        "microsoft_login": ctx.sign_in_client("microsoft") is not None,
        "dev_login": ctx.dev_login_enabled,
        "email_login": ctx.settings.email_login_enabled,
        "email_link_minutes": ctx.settings.email_link_minutes,
        # The methods in words, from one place (`web.signin_words`).
        "sign_in_words": signin_words.sign_in_words(
            google=ctx.sign_in_client("google") is not None,
            microsoft=ctx.sign_in_client("microsoft") is not None,
            email_link=ctx.settings.email_login_enabled,
        ),
        "signup_words": signin_words.account_making_words(
            google=ctx.sign_in_client("google") is not None,
            microsoft=ctx.sign_in_client("microsoft") is not None,
        ),
        # One statement of what is accepted, and who receives the pictures (the upload form,
        # the landing page, /limits and the guides read these).
        "accepted": ctx.accepted_types(),
        "accepted_revision": ctx.accepted_types(revision=True),
        "model_vendor": ctx.model_vendor,
        # What is sent to the model, from one list (web/recipients.py `sent_material`): the
        # vendor line, the upload form's scanned-pages box and the deletion notes read it.
        **recipients.material_context(ctx.settings),
        "recipients": recipients.recipients(ctx.settings),
        "max_upload_mb": ctx.settings.max_upload_mb,
        "max_pages": ctx.settings.max_pages_per_job,
        # The two sizes an upload is held to, each with one name (`web.uploads`).
        "one_document": uploads.one_document(ctx.settings),
        "one_file": uploads.one_file(ctx.settings),
        # What a LaTeX project zip may be, and how many characters of LaTeX count as a page.
        "project_zip": uploads.zip_limits(),
        "chars_per_page": f"{source_intake.CHARS_PER_PAGE:,}",
        "retention_hours": ctx.settings.retention_hours,
        "retention_rules": retention.rules_line(ctx.settings),
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
        # Signed-in pages (everything that is not a public page) get the compact footer.
        "compact_footer": current_user(request) is not None
        and not seo.is_indexable(request.url.path),
        "showcase_on": seo.SHOWCASE_DIR.is_dir(),
        # Product analytics (web.analytics): off unless ANALYTICS_ENABLED; the events queued
        # by the page before are handed to its loader once.
        "analytics_on": ctx.settings.analytics_enabled,
        "analytics_events": analytics.pop_events(request),
    }
    return ctx.templates.TemplateResponse(
        request, template, base | (context or {}), status_code=status_code, headers=headers
    )
