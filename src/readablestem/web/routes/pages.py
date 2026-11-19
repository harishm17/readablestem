"""Public pages: landing, demo, privacy, terms, known limits, posting help, the guides,
status, and the health check."""

from __future__ import annotations

import html
import json
import re
import struct
from datetime import date
from functools import cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from readablestem.pipeline import export_docx
from readablestem.web import analytics, office_convert, offices, outcome, proof_facts, seo
from readablestem.web.assets import cached_html, fixed_file, static_url
from readablestem.web.database import ping
from readablestem.web.db import Job, JobStatus, session_scope, utcnow
from readablestem.web.deps import SESSION_FLASH, current_user, get_ctx, render
from readablestem.web.health import ops_freshness, readiness
from readablestem.web.quota import next_day_start
from readablestem.web.recipients import privacy_context
from readablestem.web.sample import DEMO_DIR
from readablestem.web.security import CARRIES_SECRET, VIEW_CSP
from readablestem.web.speech_diff import read_text_layer

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
# The demo is stored data in `web/demo/`, not a static file: the routes below read it, so it
# has one address (`/demo`, with the sandbox header) and none under `/static` (web.dev_only).
DEMO_PAGE = DEMO_DIR / "index.html"
DEMO_PDF = DEMO_DIR / "source.pdf"


@cache
def _hero() -> dict[str, object]:
    """The landing page's "Hear the difference" block (scripts/hero_assets.py): the committed
    crop's size on the page, the PDF's text layer, the page's MathML and the reading, all built
    from the stored demo."""
    data = json.loads((STATIC_DIR / "hero" / "hero.json").read_text(encoding="utf-8"))
    png = (STATIC_DIR / "hero" / "before.png").read_bytes()
    width, height = struct.unpack(">II", png[16:24])
    # The crop is drawn at 4 pixels to the PDF point and shown at 2.4 times the PDF's size, so
    # its equation is about as large as the page's MathML beside it.
    data["crop_display_width"], data["crop_display_height"] = width * 3 // 5, height * 3 // 5
    # How the speech rules read that text layer, for the hero's Play control (the same
    # reading the review screen shows beside an equation).
    data["text_layer_speech"] = read_text_layer(str(data["text_layer"]))
    return data


def _features(request: Request) -> dict[str, object]:
    """Which content features this server has on, so a public page lists only what it does
    (the landing page, /limits and the guides read the same flags)."""
    ctx = get_ctx(request)
    settings = ctx.settings
    return {
        "tables_on": settings.feature_tables,
        "image_pages_on": settings.feature_image_pages,
        "long_on": settings.feature_long_descriptions,
        "colour_on": settings.feature_colour,
        "notation_on": settings.feature_notation_memory,
        "office_on": bool(office_convert.input_kinds(settings)),
        "word_export": export_docx.available(),  # Word downloads need pandoc on the server
        # .tex and project zips: off by default, and only once the sandbox probe has passed
        "latex_source": settings.feature_latex_source and ctx.sandbox.allows_source(),
        # A .docx read from the file itself and kept as uploaded (FEATURE_WORD_SOURCE)
        "word_source": ctx.word_source_on,
    }


def public_page(request: Request, template: str, context: dict[str, object]) -> Response:
    """A public page that is the same for every signed-out visitor: an `ETag` and
    `Cache-Control: no-cache`, so a repeat visit is a 304 with no body (`cached_html`).

    Not cached, and `no-store` like every other page: a signed-in visitor's page (their
    menu, the sign-out form), a page that shows a notice once (a flash or a queued usage
    event are the visitor's own), and any page that carries the CSRF token."""
    session = request.session
    own = (
        current_user(request) is not None
        or session.get(SESSION_FLASH)
        or session.get(analytics.SESSION_KEY)
    )
    page = render(request, template, context)
    if own or getattr(request.state, CARRIES_SECRET, False):
        return page
    return cached_html(request, bytes(page.body).decode("utf-8"), max_age=0, revalidate=True)


@router.get("/")
def landing(request: Request) -> Response:
    return public_page(
        request,
        "landing.html",
        {"hero": _hero(), "proof_facts": proof_facts.load()} | _features(request),
    )


@router.get("/demo/source.pdf")
def demo_source(request: Request) -> Response:
    """Page 3 of the demo's source PDF, which the landing page links (OpenStax, CC BY 4.0)."""
    if not DEMO_PDF.is_file():
        raise HTTPException(status_code=404)
    return fixed_file(
        request, DEMO_PDF, cache_control="public, max-age=3600", media_type="application/pdf"
    )


@router.get("/demo")
def demo(request: Request) -> Response:
    """A converted sample document, once one has been generated into web/demo/.

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
    return render(request, "demo_pending.html")


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
    page = re.sub(r"<head[^>]*>", lambda m: m.group(0) + "\n" + "\n".join(lines), page, count=1)
    # The demo file is sandboxed (no script), so it cannot play anything itself: its note
    # links to the page that can (`/demo/listen`). Only the copy sent for /demo has the link.
    return page.replace(
        '<a href="/">Back to ReadableSTEM</a>.',
        '<a href="/">Back to ReadableSTEM</a>. <a href="/demo/listen">Listen as a student</a>.',
        1,
    )


@router.get("/privacy")
def privacy(request: Request) -> Response:
    ctx = get_ctx(request)
    context = privacy_context(ctx.settings, tracer_on=ctx.tracer is not None)
    context["latex_source"] = _features(request)["latex_source"]
    context["word_source"] = _features(request)["word_source"]
    return public_page(request, "privacy.html", context)


@router.get("/terms")
def terms(request: Request) -> Response:
    ms_on = get_ctx(request).settings.ms_sign_in_enabled
    return render(request, "terms.html", {"microsoft_on": ms_on})


@router.get(offices.PAGE_PATH)
def for_accessibility_offices(request: Request) -> Response:
    """What an accessibility or procurement office needs to decide: the data flow, who
    receives what, retention, student records, what the tool does not do, a pilot checklist.
    Public, and written from the settings (web.offices)."""
    ctx = get_ctx(request)
    context = offices.offices_context(ctx.settings, tracer_on=ctx.tracer is not None)
    context["latex_source"] = _features(request)["latex_source"]
    context["proof_facts"] = proof_facts.load()
    return render(request, "offices.html", context)


@router.get(offices.SUMMARY_PATH)
def for_accessibility_offices_summary(request: Request) -> Response:
    """The same facts on one printable page, as a self-contained HTML file to download. It
    carries inline style and no script, and is served sandboxed like a converted document."""
    ctx = get_ctx(request)
    context = offices.offices_context(ctx.settings, tracer_on=ctx.tracer is not None)
    context["latex_source"] = _features(request)["latex_source"]
    context["proof_facts"] = proof_facts.load()
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
    return public_page(request, "limits.html", limits_context(request))


def limits_context(request: Request) -> dict[str, object]:
    """What /limits and /pricing both state about uploads, from the settings the upload
    routes enforce."""
    settings = get_ctx(request).settings
    return {
        # A bigger file is kept so its pages can be chosen and converted in parts (T3-3).
        "in_parts": settings.upload_limit_mb > settings.max_upload_mb
        or settings.source_page_limit > settings.max_pages_per_job,
        # The daily page limit of invited people; the open tier's is in every page's context.
        "invited_page_quota": settings.per_user_daily_page_quota,
        "proof_facts": proof_facts.load(),
        # What one person may keep stored at once (`web.storage`), by tier.
        "storage_mb": settings.storage_per_user_mb,
        "open_storage_mb": settings.storage_open_tier_mb,
        # What reading scanned and handwritten pages costs: the upload form's words.
        "image_pages_cost": outcome.image_pages_cost(settings),
    } | _features(request)


@router.get("/pricing")
def pricing(request: Request) -> Response:
    """The beta is free, with the limits `/limits` lists; no prices are set (WS25.1). Public."""
    return render(request, "pricing.html", limits_context(request))


@router.get("/security")
def security_page(request: Request) -> Response:
    """What is stored, who receives it, how long, how it is protected, what was and was not
    tested, how to report a problem (WS25.3). Written from the same settings as the offices
    page and the privacy notice (web.offices), and from docs/security.md."""
    ctx = get_ctx(request)
    context = offices.offices_context(ctx.settings, tracer_on=ctx.tracer is not None)
    context["latex_source"] = _features(request)["latex_source"]
    return render(request, "security.html", context)


@router.get("/help")
def help_index(request: Request) -> Response:
    """The help pages and what each answers (WS25.4). Public, like each of them."""
    return render(request, "help_index.html")


@router.get("/help/review")
def review_help(request: Request) -> Response:
    """How to review a converted document: what each decision, button and shortcut does and
    when a document is ready to post (WS25.4). Public, so a colleague can read it first."""
    return render(request, "help_review.html")


@router.get("/help/latex")
def latex_help(request: Request) -> Response:
    """What a LaTeX file or zip may hold and what each message about one means (WS25.4).
    Public. It reads the same limits and lists the upload checks use."""
    return render(request, "help_latex.html", _features(request))


@router.get("/help/posting")
def posting_help(request: Request) -> Response:
    """How to put the file in front of students (UX review MF11): upload it and link it,
    never paste it into an LMS page. Public, so an accessibility office can read it."""
    return render(request, "help_posting.html", _features(request))


def _guide_context(path: str) -> dict[str, object]:
    """The dates a guide shows. They come from seo.PUBLIC_PAGES, which also feeds the
    sitemap and the Article in the page's structured data, so the three cannot differ."""
    page = next(page for page in seo.PUBLIC_PAGES if page.path == path)
    return {
        "updated": page.updated,
        "updated_text": _long_date(page.updated),
        "published": page.published or page.updated,
    }


def _long_date(iso: str) -> str:
    day = date.fromisoformat(iso)
    return f"{day:%B} {day.day}, {day.year}"


@router.get(seo.GUIDES_INDEX)
def guides_index(request: Request) -> Response:
    """The guides, listed (public, like each of them)."""
    guides = []
    for guide in seo.GUIDES:
        page = next(p for p in seo.PUBLIC_PAGES if p.path == guide.path)
        guides.append(
            {"guide": guide, "updated": page.updated, "updated_text": _long_date(page.updated)}
        )
    return render(request, "guides.html", {"guides": guides})


@router.get(seo.ADA_GUIDE)
def guide_ada_title_ii(request: Request) -> Response:
    """What the Justice Department's web rule asks of STEM course materials, with sources
    and the dates as checked on the day it was written. Public, like /help/posting."""
    path = seo.ADA_GUIDE
    context = _guide_context(path) | _features(request)
    return render(request, "guide_ada_title_ii.html", context)


@router.get(seo.COMPARE_GUIDE)
def guide_compare(request: Request) -> Response:
    """ReadableSTEM beside Mathpix, Aelira, chatbots and LaTeXML, each fact with source and date."""
    path = seo.COMPARE_GUIDE
    context = _guide_context(path) | _features(request) | {"proof_facts": proof_facts.load()}
    return render(request, "guide_compare.html", context)


@router.get("/status")
def status(request: Request) -> Response:
    """Public service status: is it up, are conversions open today, how long is the queue.

    Numbers and states only, nothing about anyone's documents. "Up" here means the page
    rendered and the database answered.
    """
    ctx = get_ctx(request)
    database_ok = ping(ctx.engine)
    provider = ctx.provider_health()
    context: dict[str, object] = {
        "database_ok": database_ok,
        "conversions_open": None,
        "waiting": None,
        "converting": None,
        "next_reset": next_day_start(utcnow()),
        # When these facts were read: the page does not update itself.
        "checked_at": utcnow(),
        # The AI service, as this server's own recent requests saw it (web.provider_health).
        "provider_down_since": provider.down_since(),
        # Optional: the page reloads itself every minute while this is on (`?refresh=1`).
        "refresh": request.query_params.get("refresh") == "1",
    }
    requests_seen, requests_failed, last_request = provider.observed()
    context |= {
        "provider_requests": requests_seen,
        "provider_failed": requests_failed,
        "provider_last_at": last_request,
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
    """Did the daily backup and the hourly cron finish well lately (ops.heartbeat)? 200 or
    503 with the status word alone, for an outside check that reads the status code. Which
    task is late, the storage and the sandbox probe are for the admin (`/admin`,
    `/admin/health`)."""
    ok, _ = ops_freshness(get_ctx(request))
    return JSONResponse({"status": "ok" if ok else "stale"}, status_code=200 if ok else 503)
