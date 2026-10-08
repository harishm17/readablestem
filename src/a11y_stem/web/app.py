"""The FastAPI application: `create_app(settings=None, *, converter=None, client_factory=None)`.

Server-rendered Jinja pages (autoescape on) that work without JavaScript; htmx (vendored,
same-origin) only adds live status updates. One process serves the pages and, when
RUN_WORKER is on, runs the background worker thread (started and stopped with the app's
lifespan).

Run locally: `a11y-stem serve --reload` (see docs/web.md).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import jinja2
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse

from a11y_stem.config import ConfigError, Settings, get_settings
from a11y_stem.observability.logging import configure_logging, request_logging_middleware
from a11y_stem.observability.tracing import NoopTracer, Tracer, make_tracer
from a11y_stem.web.auth import make_oauth, seed_admins
from a11y_stem.web.auth import router as auth_router
from a11y_stem.web.database import as_utc, prepare_database
from a11y_stem.web.db import make_engine, make_sessionmaker
from a11y_stem.web.deps import AppContext, CsrfFailed, LoginRequired, PageError, render
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.routes import admin, cron, jobs, pages, review
from a11y_stem.web.security import (
    RequestSizeLimitMiddleware,
    SafeErrorMiddleware,
    SecurityHeadersMiddleware,
)
from a11y_stem.web.spend import DbSpendGuard, largest_reservation_usd
from a11y_stem.web.worker import (
    ClientFactory,
    Converter,
    Worker,
    default_client_factory,
    default_converter,
)

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

SESSION_COOKIE = "a11y_stem_session"
SESSION_MAX_AGE_S = 7 * 24 * 60 * 60
MIN_SESSION_SECRET_CHARS = 32

ERROR_TEXT = {
    400: ("Something was wrong with that request", "Please go back and try again."),
    401: ("Please sign in", "Sign in to continue."),
    403: ("Not allowed", "You do not have access to this page."),
    404: ("Page not found", "There is nothing at this address, or it is not yours to see."),
    405: ("Not allowed", "That action is not available at this address."),
    413: ("Request too large", "That request was larger than this page accepts."),
}


# --------------------------------------------------------------------------- templates


def _utc(value: datetime | None) -> str:
    value = as_utc(value)
    return f"{value.day} {value:%b %Y, %H:%M} UTC" if value else ""


def _usd(value: float | None) -> str:
    return f"${(value or 0):.4f}" if (value or 0) < 0.01 else f"${(value or 0):.2f}"


def make_templates() -> Jinja2Templates:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,  # every template, whatever its extension
        undefined=jinja2.StrictUndefined,  # a missing variable is a bug, not a blank
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["utc"] = _utc
    env.filters["usd"] = _usd
    return Jinja2Templates(env=env)


# --------------------------------------------------------------------------- errors


def _error_page(request: Request, status: int, title: str, message: str) -> Response:
    context = {"title": title, "message": message, "status": status, "job_id": None}
    return render(request, "error.html", context, status_code=status)


def _on_page_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, PageError)
    if _is_htmx(request):  # e.g. the job was deleted in another tab: reload to show why
        return Response(status_code=exc.status_code, headers={"HX-Refresh": "true"})
    context: dict[str, Any] = {"title": exc.title, "message": exc.message, "job_id": None}
    context |= {"status": exc.status_code} | exc.context
    return render(request, exc.template, context, status_code=exc.status_code, headers=exc.headers)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _on_login_required(request: Request, exc: Exception) -> Response:
    if _is_htmx(request):
        # A background poll after the session ended: the browser's XHR would follow a 303
        # and swap the sign-in page into the status box. Send the whole page there.
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if request.method in {"GET", "HEAD"}:
        return RedirectResponse("/login", status_code=303)
    return _error_page(request, 401, *ERROR_TEXT[401])


def _on_csrf_failed(request: Request, exc: Exception) -> Response:
    log_event(logger, "csrf_rejected", logging.WARNING)
    return _error_page(
        request,
        403,
        "This form has expired",
        "For your security, forms stop working when you sign in again or after a while. "
        "Go back, reload the page and try again.",
    )


def _on_http_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    title, message = ERROR_TEXT.get(exc.status_code, ("Something went wrong", "Please try again."))
    return _error_page(request, exc.status_code, title, message)


def _on_validation_error(request: Request, exc: Exception) -> Response:
    # FastAPI's default echoes the submitted values back; they may be document text.
    return _error_page(request, 400, *ERROR_TEXT[400])


def _plain_500_page() -> Response:
    return HTMLResponse((TEMPLATES_DIR / "500.html").read_text(encoding="utf-8"), status_code=500)


# --------------------------------------------------------------------------- app


def check_web_secrets(settings: Settings) -> None:
    """Production start check for the secrets themselves, on top of `check_production`.

    `Settings.check_production` tests `value in (None, "")`, and a `SecretStr("")` (an
    empty SESSION_SECRET=, say) is not equal to "", so an empty secret passes it. Here an
    empty session secret would silently become a random one per process (users signed
    out on every restart, sign-in failing across instances) and an empty cron token would
    refuse the scheduler for ever, so the 24-hour purge would never run.
    """
    if settings.env != "prod":
        return
    secrets_needed = {
        "SESSION_SECRET": settings.session_secret,
        "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
        "CRON_TOKEN": settings.cron_token,
        "OPENAI_API_KEY": settings.openai_api_key,
    }
    empty = [
        name
        for name, value in secrets_needed.items()
        if value is None or not value.get_secret_value().strip()
    ]
    if empty:
        raise ConfigError("production start refused; empty: " + ", ".join(empty))
    session_secret = settings.session_secret.get_secret_value() if settings.session_secret else ""
    if len(session_secret) < MIN_SESSION_SECRET_CHARS:
        raise ConfigError(
            f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_CHARS} characters in production"
        )


def _session_secret(settings: Settings) -> str:
    if settings.session_secret is not None and settings.session_secret.get_secret_value():
        return settings.session_secret.get_secret_value()
    if settings.env == "prod":  # check_web_secrets already refused this; never fall back
        raise ConfigError("SESSION_SECRET is required in production")
    # dev/test only: sessions end when the process restarts, which is fine locally.
    return secrets.token_urlsafe(32)


def _job_tracer(settings: Settings) -> Tracer | None:
    """The tracer handed to every conversion, or None when tracing is off.

    Built once per app: a Langfuse client per job would open a connection pool each
    time. `make_tracer` falls back to no tracing (logging why) when keys or the
    package are missing.
    """
    tracer = make_tracer(settings)
    return None if isinstance(tracer, NoopTracer) else tracer


def create_app(
    settings: Settings | None = None,
    *,
    converter: Converter | None = None,
    client_factory: ClientFactory | None = None,
) -> FastAPI:
    """Build the web app. Tests pass a fake converter and client factory."""
    settings = settings or get_settings()
    settings.check_production()
    check_web_secrets(settings)
    configure_logging(settings)
    engine = make_engine(settings.database_url)
    factory = make_sessionmaker(engine)
    ctx = AppContext(
        settings=settings,
        engine=engine,
        factory=factory,
        templates=make_templates(),
        converter=converter or default_converter(),
        client_factory=client_factory or default_client_factory,
        guard=DbSpendGuard(factory, settings.daily_spend_cap_usd),
        oauth=make_oauth(settings),
        min_headroom_usd=largest_reservation_usd(settings),
        tracer=_job_tracer(settings),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await run_in_threadpool(prepare_database, engine, settings)
        await run_in_threadpool(seed_admins, factory, settings.admin_email_list)
        if settings.run_worker:
            ctx.worker = Worker(
                settings,
                factory,
                converter=ctx.converter,
                client_factory=ctx.client_factory,
                guard=ctx.guard,
                headroom_usd=ctx.min_headroom_usd,
                tracer=ctx.tracer,
            )
            ctx.worker.start()
        log_event(
            logger,
            "app_started",
            env=settings.env,
            worker=settings.run_worker,
            tracing=ctx.tracer is not None,
            fake_models=settings.fake_models,
        )
        try:
            yield
        finally:
            if ctx.worker is not None:
                await run_in_threadpool(ctx.worker.stop)
            engine.dispose()

    app = FastAPI(
        title="a11y-stem",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.ctx = ctx

    # Added inner-first: SecurityHeaders > SafeError > RequestSizeLimit > Session > routes.
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(settings),
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_S,
        same_site="lax",
        https_only=settings.env == "prod",
    )
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(SafeErrorMiddleware, error_page=_plain_500_page)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.env == "prod")
    # Outermost: logs method, route template, status and latency only (no query, headers).
    app.middleware("http")(request_logging_middleware)

    app.add_exception_handler(PageError, _on_page_error)
    app.add_exception_handler(LoginRequired, _on_login_required)
    app.add_exception_handler(CsrfFailed, _on_csrf_failed)
    app.add_exception_handler(StarletteHTTPException, _on_http_error)
    app.add_exception_handler(RequestValidationError, _on_validation_error)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for module in (pages, jobs, review, admin, cron):
        app.include_router(module.router)
    app.include_router(auth_router)
    return app
