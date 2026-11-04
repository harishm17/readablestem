"""Public pages: landing, demo, privacy, terms, known limits, posting help, the guides,
status, and the health check."""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from readablestem.web import analytics, office_convert, offices, seo, storage
from readablestem.web.assets import cached_html, static_url
from readablestem.web.database import ping
from readablestem.web.db import Job, JobStatus, session_scope, utcnow
from readablestem.web.deps import get_ctx, render
from readablestem.web.health import ops_freshness, readiness
from readablestem.web.provider_health import HEALTH
from readablestem.web.quota import next_day_start
from readablestem.web.recipients import privacy_context
from readablestem.web.security import VIEW_CSP

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
DEMO_PAGE = STATIC_DIR / "demo" / "index.html"


@router.get("/")
def landing(request: Request) -> Response:
    return render(request, "landing.html", {"active": "home"})


@router.get("/style")
def style_page(request: Request) -> Response:
    """The tokens and components on one page, for the people who build the site and for
    the screenshot and axe checks. Not in `seo.PUBLIC_PAGES`, so it is `noindex` and not in
    the sitemap; nothing links to it."""
    return render(request, "style.html", {"active": "style"})


@router.get("/demo")
def demo(request: Request) -> Response:
    """A converted sample document, once one has been generated into static/demo/.

    It is a converted document like any other (inline styles, data: images), so it gets
    the same sandboxed policy as /jobs/{id}/view.
    """
    if DEMO_PAGE.is_file():
        site = get_ctx(request).settings.base_url.rstrip("/")
        # The demo is a sandboxed document and runs no script, so a visit that arrives by
        # the personal site's link (which carries the visitor's id) is noted from here.
        visitor = analytics.visitor_id(request.query_params.get("hm_id"))
        noted = (
            BackgroundTask(analytics.capture, visitor, "demo_opened", {"direct": True})
            if visitor and analytics.enabled(request)
            else None
        )
        return cached_html(
            request,
            _with_preview_tags(DEMO_PAGE.read_text(encoding="utf-8"), site),
            max_age=DEMO_MAX_AGE,
            revalidate=noted is not None,  # a counted visit (hm_id) must reach the server
            headers={"Content-Security-Policy": VIEW_CSP},
            background=noted,
        )
    return render(request, "demo_pending.html", {"active": "demo"})


# The demo is the same bytes for everyone until a deploy (688 KB): kept an hour, then the
# ETag turns a repeat visit into a 304. Only per-user pages are `no-store` (web.security).
DEMO_MAX_AGE = 3600


DEMO_TITLE = "Demo: a physics textbook section converted | ReadableSTEM"
DEMO_UPDATED = next(page.updated for page in seo.PUBLIC_PAGES if page.path == "/demo")
DEMO_DESCRIPTION = (
    "A real conversion of OpenStax Physics 2.3 (CC BY 4.0): equations as MathML a screen "
    "reader can read, real headings and described figures. Open it with a screen reader."
)


def _with_preview_tags(page: str, site: str) -> str:
    """The demo file with link-preview tags, its address and structured data added after
    <head>, and the demo's title in <title>. The file is a converted student document and
    stays as it is on disk; only the copy sent for /demo carries the tags, so a shared demo
    link (or a search result) shows a title, description and picture."""
    image = f"{site}{static_url('social-card.png')}"
    tags = {
        "description": DEMO_DESCRIPTION,
        "og:type": "website",
        "og:site_name": "ReadableSTEM",
        "og:title": DEMO_TITLE,
        "og:description": DEMO_DESCRIPTION,
        "og:url": f"{site}/demo",
        "og:image": image,
        "og:image:width": "1200",
        "og:image:height": "630",
        "twitter:card": "summary_large_image",
        "twitter:title": DEMO_TITLE,
        "twitter:description": DEMO_DESCRIPTION,
        "twitter:image": image,
    }
    lines = [
        f'<meta {"property" if key.startswith("og:") else "name"}="{key}" '
        f'content="{html.escape(value, quote=True)}">'
        for key, value in tags.items()
    ]
    lines.insert(0, f'<link rel="canonical" href="{html.escape(site, quote=True)}/demo">')
    data = seo.structured_data(site, "/demo", DEMO_TITLE, DEMO_DESCRIPTION, DEMO_UPDATED)
    lines.append(str(seo.json_ld_tag(data)))
    page = re.sub(
        r"<title>.*?</title>",
        f"<title>{html.escape(DEMO_TITLE)}</title>",
        page,
        count=1,
        flags=re.S,
    )
    return re.sub(r"<head[^>]*>", lambda m: m.group(0) + "\n" + "\n".join(lines), page, count=1)


@router.get("/privacy")
def privacy(request: Request) -> Response:
    ctx = get_ctx(request)
    context = privacy_context(ctx.settings, tracer_on=ctx.tracer is not None)
    return render(request, "privacy.html", context)


@router.get("/terms")
def terms(request: Request) -> Response:
    ms_on = get_ctx(request).settings.ms_sign_in_enabled
    return render(request, "terms.html", {"active": "terms", "microsoft_on": ms_on})


@router.get(offices.PAGE_PATH)
def for_accessibility_offices(request: Request) -> Response:
    """What an accessibility or procurement office needs to decide: the data flow, who
    receives what, retention, student records, what the tool does not do, a pilot checklist.
    Public, and written from the settings (web.offices)."""
    ctx = get_ctx(request)
    context = offices.offices_context(ctx.settings, tracer_on=ctx.tracer is not None)
    return render(request, "offices.html", context)


@router.get(offices.SUMMARY_PATH)
def for_accessibility_offices_summary(request: Request) -> Response:
    """The same facts on one printable page, as a self-contained HTML file to download. It
    carries inline style and no script, and is served sandboxed like a converted document."""
    ctx = get_ctx(request)
    context = offices.offices_context(ctx.settings, tracer_on=ctx.tracer is not None)
    context["as_of"] = utcnow().date().isoformat()
    context["style_tag"] = offices.summary_style_tag()
    return render(
        request,
        "offices_summary.html",
        context,
        headers={
            "Content-Security-Policy": VIEW_CSP,
            "Content-Disposition": f'attachment; filename="{offices.SUMMARY_FILENAME}"',
        },
    )


@router.get("/limits")
def limits(request: Request) -> Response:
    settings = get_ctx(request).settings
    context = {
        "active": "limits",
        # A bigger file is kept so its pages can be chosen and converted in parts (T3-3).
        "in_parts": settings.upload_limit_mb > settings.max_upload_mb
        or settings.source_page_limit > settings.max_pages_per_job,
        "upload_limit_mb": settings.upload_limit_mb,
        "source_pages": settings.source_page_limit,
        # Which content features are on, so the page describes what this server does.
        "tables_on": settings.feature_tables,
        "colour_on": settings.feature_colour,
        "image_pages_on": settings.feature_image_pages,
        "office_on": bool(office_convert.input_kinds(settings)),
        # The daily page limit of invited people; the open tier's is in every page's context.
        "invited_page_quota": settings.per_user_daily_page_quota,
        # What one person may keep stored at once (`web.storage`), by tier.
        "storage_mb": settings.storage_per_user_mb,
        "open_storage_mb": settings.storage_open_tier_mb,
    }
    return render(request, "limits.html", context)


@router.get("/help/posting")
def posting_help(request: Request) -> Response:
    """How to put the file in front of students (UX review MF11): upload it and link it,
    never paste it into an LMS page. Public, so an accessibility office can read it."""
    return render(request, "help_posting.html", {"active": "posting"})


def _guide_context(path: str, active: str) -> dict[str, object]:
    """The dates a guide shows. They come from seo.PUBLIC_PAGES, which also feeds the
    sitemap and the Article in the page's structured data, so the three cannot differ."""
    page = next(page for page in seo.PUBLIC_PAGES if page.path == path)
    return {
        "active": active,
        "updated": page.updated,
        "updated_text": _long_date(page.updated),
        "published": page.published or page.updated,
    }


def _long_date(iso: str) -> str:
    day = date.fromisoformat(iso)
    return f"{day.day} {day.strftime('%B %Y')}"


@router.get(seo.GUIDES_INDEX)
def guides_index(request: Request) -> Response:
    """The guides, listed (public, like each of them)."""
    guides = []
    for guide in seo.GUIDES:
        page = next(p for p in seo.PUBLIC_PAGES if p.path == guide.path)
        guides.append(
            {"guide": guide, "updated": page.updated, "updated_text": _long_date(page.updated)}
        )
    return render(request, "guides.html", {"active": "guides", "guides": guides})


@router.get(seo.ADA_GUIDE)
def guide_ada_title_ii(request: Request) -> Response:
    """What the Justice Department's web rule asks of STEM course materials, with sources
    and the dates as checked on the day it was written. Public, like /help/posting."""
    path = seo.ADA_GUIDE
    return render(request, "guide_ada_title_ii.html", _guide_context(path, "guide-ada"))


@router.get(seo.COMPARE_GUIDE)
def guide_compare(request: Request) -> Response:
    """ReadableSTEM beside Mathpix and LaTeXML, from each project's own documentation."""
    path = seo.COMPARE_GUIDE
    return render(request, "guide_compare.html", _guide_context(path, "guide-compare"))


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
        # The AI service, as this server's own recent requests saw it (web.provider_health).
        "provider_down_since": HEALTH.down_since(),
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


@router.get("/readyz")
def readyz(request: Request) -> Response:
    """Can the service do its job: database, worker thread, model provider (web.health).
    One word per check, never a setting's value. 503 while any check fails or the process
    is shutting down."""
    ready, checks = readiness(get_ctx(request))
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )


@router.get("/healthz/ops")
def healthz_ops(request: Request) -> Response:
    """Did the daily backup and the hourly cron finish well lately (ops.heartbeat)? 200, or
    503 listing only the names that are stale. Content-free, like /readyz."""
    ctx = get_ctx(request)
    ok, stale = ops_freshness(ctx)
    try:
        with session_scope(ctx.factory) as session:
            stored = storage.report(session, ctx.settings)
    except Exception:
        stored = None
    return JSONResponse(
        {"status": "ok" if ok else "stale", "stale": stale, "storage": stored},
        status_code=200 if ok else 503,
    )
