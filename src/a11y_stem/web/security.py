"""Security headers and privacy-safe error handling (pure ASGI middleware).

Headers: a strict Content-Security-Policy on every app page (no inline script or style
anywhere, so injected markup cannot run), nosniff, same-origin referrers, no framing,
`Cache-Control: no-store` on everything that is not a static asset (pages show document
content), and HSTS in production. A route that sets its own CSP keeps it: the converted
document is served under a `sandbox` policy instead (`VIEW_CSP`).

Errors: Starlette's own error middleware re-raises after responding, so the server would
log the exception message, which can contain document text. `SafeErrorMiddleware` sits
inside it, logs only the exception type and code location, and answers with a plain 500
page.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from starlette.datastructures import MutableHeaders
from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from a11y_stem.web.logsafe import log_exception

logger = logging.getLogger(__name__)

APP_CSP = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' data:",
        "style-src 'self'",
        "script-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self' https://accounts.google.com",
    ]
)

# The converted document (and the demo) may carry inline styles and data: images, and is
# shown from our origin, so it runs sandboxed: no scripts, no forms, no same-origin access.
VIEW_CSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"

HSTS = "max-age=31536000; includeSubDomains"

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_static = str(scope.get("path", "")).startswith("/static/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", APP_CSP)
                for name, value in BASE_HEADERS.items():
                    headers.setdefault(name, value)
                if not is_static:
                    headers.setdefault("Cache-Control", "no-store")
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class SafeErrorMiddleware:
    """Turn any unhandled exception into a 500 page without logging its message."""

    def __init__(self, app: ASGIApp, *, error_page: Callable[[], Response] | None = None) -> None:
        self.app = app
        self.error_page = error_page or _plain_500

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            route = getattr(scope.get("route"), "path", "unmatched")
            log_exception(logger, "request_failed", exc, method=scope.get("method"), route=route)
            if not started:
                await self.error_page()(scope, receive, send)


def _plain_500() -> Response:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Something went wrong | a11y-stem</title></head><body><main>"
        "<h1>Something went wrong</h1><p>The error has been recorded without any of your "
        "document's content. Please try again, or go back to <a href='/'>the home page</a>."
        "</p></main></body></html>",
        status_code=500,
    )
