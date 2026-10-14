"""Routes of the email sign-in link (T3-10; `web.email_login` says how it works).

All three answer 404 unless EMAIL_LOGIN_ENABLED is on (a POST is CSRF-checked first, as the
course routes are, so a forged form is a 403 whatever the setting). A sender must be
configured, which the settings check enforces at start. They are public routes:
CSRF-checked POSTs, the sign-in rate limits, no session needed.
"""

from __future__ import annotations

import hashlib
import logging
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from starlette.background import BackgroundTask

from a11y_stem.web import email_login
from a11y_stem.web.auth import normalise_email, signed_in
from a11y_stem.web.deps import PageError, get_ctx, limited, render, require_csrf
from a11y_stem.web.email_senders import make_sender
from a11y_stem.web.logsafe import log_event

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_enabled(request: Request) -> None:
    if not get_ctx(request).settings.email_login_enabled:
        raise PageError(404, "Page not found", "There is nothing at this address.")


@router.post(
    "/login/email",
    dependencies=[limited("login"), Depends(require_csrf), Depends(_require_enabled)],
)
async def start(request: Request, email: Annotated[str, Form()] = "") -> Response:
    """Send a link to the address if it can sign in. The page is the same either way."""
    ctx = get_ctx(request)
    address = normalise_email(email)
    if address is None:
        return render(
            request,
            "login_email.html",
            {
                "sent": False,
                "error": "Enter an email address, like name@example.org.",
                "email": email[:320],
            },
            status_code=422,
        )
    response = render(request, "login_email.html", {"sent": True, "error": "", "email": ""})
    sender = make_sender(ctx.settings)
    if sender is None:  # refused at start-up; defensive
        log_event(logger, "email_login_no_sender")
        return response
    key = hashlib.sha256(address.encode()).hexdigest()
    if ctx.settings.rate_limits_enabled and ctx.limiter.check("email_link", key) is not None:
        log_event(logger, "rate_limited", rule="email_link")
        return response
    token = await run_in_threadpool(
        email_login.issue, ctx.factory, address, minutes=ctx.settings.email_link_minutes
    )
    if token is None:
        log_event(logger, "login_refused", reason="not_allowlisted", provider="email")
        return response
    message = email_login.message_for(ctx.settings, address, token)
    response.background = BackgroundTask(partial(email_login.send_quietly, sender, message))
    return response


@router.get("/login/email/confirm", dependencies=[Depends(_require_enabled), limited("callback")])
async def confirm_page(request: Request, token: str = "") -> Response:
    """The page the emailed link opens: a button, and nothing is used up by opening it."""
    ctx = get_ctx(request)
    usable = await run_in_threadpool(email_login.is_usable, ctx.factory, token)
    if not usable:
        return render(
            request, "login_email_confirm.html", {"token": "", "usable": False}, status_code=400
        )
    return render(request, "login_email_confirm.html", {"token": token, "usable": True})


@router.post(
    "/login/email/confirm",
    dependencies=[limited("callback"), Depends(require_csrf), Depends(_require_enabled)],
)
async def confirm(request: Request, token: Annotated[str, Form()] = "") -> Response:
    ctx = get_ctx(request)
    found = await run_in_threadpool(email_login.redeem, ctx.factory, token)
    if found is None:
        log_event(logger, "login_failed", reason="link_not_usable", provider="email")
        return render(
            request, "login_email_confirm.html", {"token": "", "usable": False}, status_code=400
        )
    user_id, version = found
    return await run_in_threadpool(signed_in, request, user_id, version, "email")
