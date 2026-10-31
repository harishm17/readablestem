"""Product analytics: PostHog through this app's own `/m/` path.

What it is for: seeing whether a visitor who followed a link to the site only looked at the
landing page, signed up, or tried the tool. Not product analytics in depth.

The browser loads PostHog's script from `/m/static/array.js` and sends events to `/m/`, both
on this origin, so the strict Content-Security-Policy (`script-src 'self'`, `default-src
'self'`) stays as it is and nothing is requested from another host. `router` is the reverse
proxy to PostHog Cloud (US). The loader that starts it is `static/analytics.js`; it is
included by `base.html` on the app's own pages only, never in a converted document, the
demo, a download or an export (those are built from other templates and served under
`VIEW_CSP`, which allows no script).

Everything is behind `Settings.analytics_enabled` (`ANALYTICS_ENABLED`, off by default): with
it off `/m/` does not exist, the loader is not included and no event is queued.

What is captured (the loader's `posthog.init` options are the spec): page views and page
leaves, and the named funnel events below. No autocapture, no session replay, no heatmaps,
no exception or performance capture, no copied text, no element text, no form values. Event
properties are counts, booleans and short categories, never a file name, a title, document
text or an email address; nobody is ever identified. A server-rendered marker
(`<span hidden data-ph-event=...>`) or a link attribute tells the loader what happened, so no
secret is needed and an analytics failure cannot fail a request.

- `signup_completed` / `login_completed` (`provider`): the page after a sign-in, for an
  account that was just made / any other.
- `document_uploaded` (`page_count`, `source` = `pdf` or `image`): the page after an
  accepted upload.
- `conversion_completed` (`page_count`, `duration_s`, `status`, `sample`): the job status
  box, when the person sees the job finish (once per job per browser, within two hours).
- `result_opened`: a click on a "preview / open the HTML" link.
- `demo_opened`: a click on a link to `/demo`; and, from the server, a direct visit to
  `/demo` that carries the personal site's `hm_id` (the demo runs no script).

The proxy follows the personal site's (`edge/worker.js`): `/m/static/*` goes to PostHog's
assets host and is cacheable; `/m/array/<token>/config(.js)` goes there too but is never
cached and carries the browser's `Origin`; everything else goes to the API host with cookies
removed, only a `Bearer phc_...` authorization kept, and the caller's address in
`X-Forwarded-For`.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from readablestem.web.abuse import client_ip
from readablestem.web.database import as_utc

logger = logging.getLogger(__name__)

# The project token is public by design (PostHog ships it to every browser).
TOKEN = "phc_nT2JxCxLcAiSQGP4d4iJ2mWef6k3gNa6ptFaEJmz5cJ9"
API_HOST = "https://us.i.posthog.com"
ASSET_HOST = "https://us-assets.i.posthog.com"
SITE = "readablestem"
PREFIX = "/m"
UPSTREAM_TIMEOUT_S = 10.0
ASSET_CACHE = "public, max-age=3600"
# Request headers worth passing on. Cookies and the referrer never are.
FORWARDED_HEADERS = ("content-type", "user-agent", "accept", "accept-language")

SESSION_KEY = "analytics_events"
MAX_QUEUED = 3

router = APIRouter(include_in_schema=False)


def new_client() -> httpx.AsyncClient:
    """The client for one proxied request (tests replace this with a mock transport)."""
    return httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_S, follow_redirects=False)


def enabled(request: Request) -> bool:
    ctx = getattr(request.app.state, "ctx", None)
    return bool(ctx is not None and ctx.settings.analytics_enabled)


# --------------------------------------------------------------------------- proxy


def _upstream_headers(request: Request) -> dict[str, str]:
    headers = {name: request.headers[name] for name in FORWARDED_HEADERS if name in request.headers}
    if request.headers.get("authorization", "").startswith("Bearer phc_"):
        headers["authorization"] = request.headers["authorization"]
    return headers


def _reply(upstream: httpx.Response, extra: dict[str, str], *, body: bool = True) -> Response:
    """The upstream answer with only its status, type and body (httpx has already decoded
    the body, so its length and encoding headers do not apply)."""
    headers = dict(extra)
    kind = upstream.headers.get("content-type")
    return Response(
        upstream.content if body else b"",
        status_code=upstream.status_code,
        headers=headers,
        media_type=kind.split(";")[0] if kind else None,
    )


@router.api_route(PREFIX + "/{path:path}", methods=["GET", "HEAD", "POST"])
async def proxy(request: Request, path: str) -> Response:
    ctx = request.app.state.ctx
    if ".." in path or "\\" in path or path.startswith("/"):
        return Response(status_code=404)
    query = f"?{request.url.query}" if request.url.query else ""
    headers = _upstream_headers(request)
    head = request.method == "HEAD"

    if path.startswith("array/"):
        # Per-site settings: PostHog answers by the request's Origin, so it is passed on
        # and the answer is never stored.
        if request.method == "POST":
            return Response(status_code=405)
        if origin := request.headers.get("origin"):
            headers["origin"] = origin
        return await _fetch(
            "GET",
            f"{ASSET_HOST}/{path}{query}",
            headers,
            {
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": request.headers.get("origin") or "*",
                "Vary": "Origin",
            },
            media_type="application/javascript" if path.endswith(".js") else "application/json",
            head=head,
        )
    if path.startswith("static/"):
        if request.method == "POST":
            return Response(status_code=405)
        return await _fetch(
            "GET",
            f"{ASSET_HOST}/{path}{query}",
            headers,
            {"Access-Control-Allow-Origin": "*"},
            cache=ASSET_CACHE,
            head=head,
        )
    # Everything else is the API: events, and any call the script makes.
    headers["x-forwarded-for"] = client_ip(request, ctx.settings.trusted_proxy_hops)
    body = await request.body() if request.method == "POST" else None
    return await _fetch(
        request.method if not head else "GET",
        f"{API_HOST}/{path}{query}",
        headers,
        {"Cache-Control": "no-store"},
        body=body,
        head=head,
    )


async def _fetch(
    method: str,
    url: str,
    headers: dict[str, str],
    extra: dict[str, str],
    *,
    body: bytes | None = None,
    media_type: str | None = None,
    cache: str | None = None,
    head: bool = False,
) -> Response:
    try:
        async with new_client() as client:
            upstream = await client.request(method, url, headers=headers, content=body)
    except httpx.HTTPError as exc:
        # Only the type: the URL can carry the visitor's query.
        logger.warning("analytics_upstream_failed type=%s", type(exc).__name__)
        return Response(status_code=502, headers={"Cache-Control": "no-store"})
    reply = _reply(upstream, extra, body=not head)
    if media_type and upstream.status_code == 200:
        reply.headers["content-type"] = media_type
    if cache and upstream.status_code == 200:
        reply.headers["Cache-Control"] = cache
    elif "Cache-Control" not in reply.headers:
        reply.headers["Cache-Control"] = "no-store"
    return reply


# --------------------------------------------------------------------------- events


def queue_event(request: Request, name: str, /, **props: Any) -> None:
    """Remember a funnel event for the next full page, which carries it to the loader.
    Properties must be numbers, booleans or short categories. Does nothing when analytics
    is off."""
    if not enabled(request):
        return
    for value in props.values():
        if not isinstance(value, bool | int | float | str) or len(str(value)) > 24:
            raise ValueError("an analytics property is a number, a boolean or a short category")
    queued = [e for e in request.session.get(SESSION_KEY, []) if isinstance(e, dict)]
    queued.append({"name": name, "props": props})
    request.session[SESSION_KEY] = queued[-MAX_QUEUED:]


def pop_events(request: Request) -> list[dict[str, Any]]:
    """The queued events, once. A partial page (htmx) leaves them for the next full one."""
    if not enabled(request) or request.headers.get("HX-Request") == "true":
        return []
    items = request.session.pop(SESSION_KEY, [])
    return items if isinstance(items, list) else []


RECENT_S = 2 * 60 * 60


def conversion_props(job: Any) -> dict[str, Any] | None:
    """The properties of `conversion_completed` for a finished job's status box, or None for
    a job that is not finished or finished long ago (opening an old document is not a new
    conversion). Counts, a status and a boolean."""
    if not job.finished or job.finished_at is None or job.created_at is None:
        return None
    now = datetime.now(UTC)
    finished, created = as_utc(job.finished_at), as_utc(job.created_at)
    if finished is None or created is None or (now - finished).total_seconds() > RECENT_S:
        return None
    return {
        "page_count": int(job.page_count),
        "duration_s": max(0, round((finished - created).total_seconds())),
        "status": str(job.status),
        "sample": bool(job.is_sample),
    }


_VISITOR = re.compile(r"[A-Za-z0-9_-]{8,64}")


def visitor_id(raw: str | None) -> str | None:
    """`hm_id` from the personal site's link when it is a plausible id, else None."""
    return raw if raw and _VISITOR.fullmatch(raw) else None


async def capture(distinct_id: str, event: str, props: dict[str, Any] | None = None) -> None:
    """Send one event from the server (a visit the page's script cannot see). Never
    raises: analytics must not fail a request."""
    body = {
        "api_key": TOKEN,
        "event": event,
        "distinct_id": distinct_id,
        "properties": {
            "site": SITE,
            "$lib": "readablestem-server",
            "$geoip_disable": True,
            "$process_person_profile": False,
            **(props or {}),
        },
    }
    try:
        async with new_client() as client:
            await client.post(f"{API_HOST}/i/v0/e/", json=body)
    except httpx.HTTPError as exc:
        logger.warning("analytics_capture_failed type=%s", type(exc).__name__)
