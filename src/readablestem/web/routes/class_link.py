"""The one address that shows a document to someone who is not signed in: `GET /s/{token}`
(WS27.2, `web.links`, behind `FEATURE_CLASS_LINK`).

It serves the document's published copy of the student version and nothing else. It does not
ask who the visitor is: no `job_access`, no session, no user, so a signed-in stranger who holds
the token gets what a signed-out one gets, and the page never reaches a `/jobs/...` address.
Because it never reads or writes the session, the session middleware sets no cookie on it
(`tests/test_class_link.py` checks the answer carries no `Set-Cookie`). The answer is the bare
student HTML that `/jobs/{id}/view` serves (`routes.jobs.student_page`: the same sandboxing
policy, so no script runs in it and it cannot reach the app), with headers that keep it out of
search engines, caches and referrers.

A token that opens nothing (wrong, removed, paused, not yet open, closed, or the setting off)
gets one fixed page and the status 404, the same for every case, so it tells a person guessing
nothing. Requests are counted per client address (`class_link`: every request, generous for a
class behind one address; `class_link_miss`: the ones that opened nothing, tight), so tokens
cannot be tried at speed. Nothing about the token is logged: the request log holds the route
template, not the path.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from readablestem.web import links
from readablestem.web.abuse import client_ip
from readablestem.web.db import session_scope, utcnow
from readablestem.web.deps import get_ctx
from readablestem.web.email_login import hash_token, valid_token_shape
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import student_page
from readablestem.web.seo import is_head

logger = logging.getLogger(__name__)
router = APIRouter()

PAGE = Path(__file__).resolve().parent.parent / "templates" / "class_link_page.html"
NOT_OPEN = (
    "This link is not open",
    "Ask the instructor who gave you the link.",
)
TOO_MANY = (
    "Too many tries",
    "Wait a few minutes, then open the link again.",
)
# What every answer from here carries. `Cache-Control` and `Referrer-Policy` replace the app's
# own defaults (`web.security.BASE_HEADERS`: `same-origin`); there is no `Set-Cookie`.
HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, no-store",
}


@cache
def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _plain(words: tuple[str, str], status: int, extra: dict[str, str] | None = None) -> Response:
    title, text = words
    html = _page().replace("{{title}}", title).replace("{{text}}", text)
    return Response(
        html,
        status_code=status,
        media_type="text/html; charset=utf-8",
        headers=HEADERS | (extra or {}),
    )


def _limited(request: Request, rule: str, address: str) -> Response | None:
    ctx = get_ctx(request)
    if not ctx.settings.rate_limits_enabled:
        return None
    wait = ctx.limiter.check(rule, address)
    if wait is None:
        return None
    log_event(logger, "rate_limited", logging.WARNING, rule=rule)
    return _plain(TOO_MANY, 429, {"Retry-After": str(wait)})


@router.get("/s/{token}")
def open_link(request: Request, token: str) -> Response:
    ctx = get_ctx(request)
    if not ctx.settings.feature_class_link:
        return _plain(NOT_OPEN, 404)
    address = client_ip(request, ctx.settings.trusted_proxy_hops)
    refused = _limited(request, "class_link", address)
    if refused is not None:
        return refused
    now = utcnow()
    html: str | None = None
    if valid_token_shape(token):
        with session_scope(ctx.factory) as session:
            found = links.find(session, hash_token(token), now)
            copy = links.published(session, found[1].id) if found is not None else None
            if found is not None and copy is not None:
                html = copy.html
                if not is_head(request):  # a HEAD (served as a GET by `seo`) is not an open
                    links.record_open(session, found[0], now)
    if html is None:
        return _limited(request, "class_link_miss", address) or _plain(NOT_OPEN, 404)
    return student_page(html, HEADERS)
