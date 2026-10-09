"""Public pages: landing, demo, privacy, terms, known limits, posting help, status, and
the health check."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, select

from a11y_stem.web.database import ping
from a11y_stem.web.db import Job, JobStatus, session_scope, utcnow
from a11y_stem.web.deps import get_ctx, render
from a11y_stem.web.events import EVENT_RETENTION_DAYS
from a11y_stem.web.quota import next_day_start
from a11y_stem.web.security import VIEW_CSP

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
DEMO_PAGE = STATIC_DIR / "demo" / "index.html"


@router.get("/")
def landing(request: Request) -> Response:
    return render(request, "landing.html", {"active": "home"})


@router.get("/demo")
def demo(request: Request) -> Response:
    """A converted sample document, once one has been generated into static/demo/.

    It is a converted document like any other (inline styles, data: images), so it gets
    the same sandboxed policy as /jobs/{id}/view.
    """
    if DEMO_PAGE.is_file():
        return FileResponse(
            DEMO_PAGE,
            media_type="text/html; charset=utf-8",
            headers={"Content-Security-Policy": VIEW_CSP},
        )
    return render(request, "demo_pending.html", {"active": "demo"})


@router.get("/privacy")
def privacy(request: Request) -> Response:
    # The page names Langfuse only when traces are actually sent there, and usage events
    # only when they are recorded.
    ctx = get_ctx(request)
    context = {
        "active": "privacy",
        "tracing": ctx.tracer is not None,
        "events_on": ctx.settings.events_enabled,
        "event_days": EVENT_RETENTION_DAYS,
    }
    return render(request, "privacy.html", context)


@router.get("/terms")
def terms(request: Request) -> Response:
    return render(request, "terms.html", {"active": "terms"})


@router.get("/limits")
def limits(request: Request) -> Response:
    return render(request, "limits.html", {"active": "limits"})


@router.get("/help/posting")
def posting_help(request: Request) -> Response:
    """How to put the file in front of students (UX review MF11): upload it and link it,
    never paste it into an LMS page. Public, so an accessibility office can read it."""
    return render(request, "help_posting.html", {"active": "posting"})


@router.get("/status")
def status(request: Request) -> Response:
    """Public service status: is it up, are conversions open today, how long is the queue.

    Numbers and states only, nothing about anyone's documents. "Up" here means the page
    rendered and the database answered.
    """
    ctx = get_ctx(request)
    database_ok = ping(ctx.engine)
    context: dict[str, object] = {
        "active": "status",
        "database_ok": database_ok,
        "conversions_open": None,
        "waiting": None,
        "converting": None,
        "next_reset": next_day_start(utcnow()),
    }
    if database_ok:
        with session_scope(ctx.factory) as session:
            rows = session.execute(
                select(Job.status, func.count())
                .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                .group_by(Job.status)
            ).all()
        counts = {str(state): int(n) for state, n in rows}
        context |= {
            "conversions_open": not ctx.guard.cap_reached(ctx.min_headroom_usd),
            "waiting": counts.get(JobStatus.QUEUED.value, 0),
            "converting": counts.get(JobStatus.RUNNING.value, 0),
        }
    return render(request, "status.html", context, status_code=200 if database_ok else 503)


@router.get("/healthz")
def healthz(request: Request) -> Response:
    ok = ping(get_ctx(request).engine)
    return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 503)
