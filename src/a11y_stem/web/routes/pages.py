"""Public pages: landing, demo, privacy, terms, known limits, and the health check."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from a11y_stem.web.database import ping
from a11y_stem.web.deps import get_ctx, render
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
    return render(request, "privacy.html", {"active": "privacy"})


@router.get("/terms")
def terms(request: Request) -> Response:
    return render(request, "terms.html", {"active": "terms"})


@router.get("/limits")
def limits(request: Request) -> Response:
    return render(request, "limits.html", {"active": "limits"})


@router.get("/healthz")
def healthz(request: Request) -> Response:
    ok = ping(get_ctx(request).engine)
    return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 503)
