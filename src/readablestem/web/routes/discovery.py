"""Files for search engines and AI crawlers (robots.txt, sitemap.xml, llms.txt, the IndexNow
key, the favicon) and the showcase: the static reviewer demo under /showcase/ (web.seo)."""

from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from readablestem.web import seo
from readablestem.web.assets import STATIC_DIR, static_url
from readablestem.web.deps import get_ctx
from readablestem.web.security import VIEW_CSP

router = APIRouter()

# Crawl files change only with a deploy.
CRAWL_CACHE = "public, max-age=3600"


def _site(request: Request) -> str:
    return get_ctx(request).settings.base_url.rstrip("/")


@router.get("/robots.txt")
def robots(request: Request) -> Response:
    return PlainTextResponse(seo.robots_txt(_site(request)), headers={"Cache-Control": CRAWL_CACHE})


@router.get("/sitemap.xml")
def sitemap(request: Request) -> Response:
    body = seo.sitemap_xml(_site(request), seo.showcase_pages())
    return Response(body, media_type="application/xml", headers={"Cache-Control": CRAWL_CACHE})


@router.get("/llms.txt")
def llms(request: Request) -> Response:
    body = seo.llms_txt(_site(request), showcase=bool(seo.showcase_pages()))
    return PlainTextResponse(body, headers={"Cache-Control": CRAWL_CACHE})


@router.get(f"/{seo.INDEXNOW_KEY}.txt")
def indexnow_key() -> Response:
    return PlainTextResponse(seo.INDEXNOW_KEY, headers={"Cache-Control": CRAWL_CACHE})


@router.get("/favicon.ico")
def favicon() -> Response:
    return FileResponse(
        STATIC_DIR / "favicon.ico", headers={"Cache-Control": "public, max-age=86400"}
    )


# --------------------------------------------------------------------------- showcase

# The stored conversion of Physics 2.3 is the app's own demo: one address for it.
SHOWCASE_CANONICAL = {"examples/physics-2-3/converted.html": "/demo"}


@router.get("/showcase")
def showcase_root() -> Response:
    return Response(status_code=301, headers={"Location": seo.SHOWCASE_PREFIX})


@router.get("/showcase/{rel:path}")
def showcase(request: Request, rel: str) -> Response:
    path = _showcase_file(seo.SHOWCASE_DIR, rel)
    headers = {"Cache-Control": CRAWL_CACHE}
    if path.suffix != ".html":
        return FileResponse(path, headers=headers)
    name = path.relative_to(seo.SHOWCASE_DIR.resolve()).as_posix()
    page = _with_head_tags(path.read_text(encoding="utf-8"), _site(request), name)
    if path.name in seo.SHOWCASE_NOINDEX:
        # A stored conversion carries inline styles and data: images: sandboxed, as /demo.
        headers["Content-Security-Policy"] = VIEW_CSP
    return HTMLResponse(page, headers=headers)


def _showcase_file(root: Path, rel: str) -> Path:
    """The built file for a showcase address; a folder means its index.html. 404 for
    anything outside the build, hidden files, or when there is no build."""
    root = root.resolve()
    path = (root / rel).resolve()
    if path.is_dir():
        path = path / "index.html"
    parts = Path(rel).parts
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or any(part.startswith(".") for part in parts)
        or path.name == "404.html"
    ):
        raise HTTPException(status_code=404)
    return path


def _with_head_tags(page: str, site: str, name: str) -> str:
    """A built showcase page with its address, link-preview tags and structured data added
    after <head>; the page on disk is the static build, unchanged."""
    title_match = re.search(r"<title>(.*?)</title>", page, re.S)
    title = html.unescape(title_match.group(1).strip()) if title_match else seo.SITE_NAME
    desc_match = re.search(r'<meta name="description" content="([^"]*)"', page)
    description = html.unescape(desc_match.group(1)) if desc_match else seo.DESCRIPTION
    path = seo.SHOWCASE_PREFIX + name
    canonical = SHOWCASE_CANONICAL.get(name, path)
    image = f"{site}{static_url('social-card.png')}"
    tags = [f'<link rel="canonical" href="{html.escape(site + canonical, quote=True)}">']
    meta = {
        "og:type": "website",
        "og:site_name": seo.SITE_NAME,
        "og:title": title,
        "og:description": description,
        "og:url": site + canonical,
        "og:image": image,
        "twitter:card": "summary_large_image",
        "twitter:title": title,
        "twitter:description": description,
        "twitter:image": image,
    }
    tags += [
        f'<meta {"property" if key.startswith("og:") else "name"}="{key}" '
        f'content="{html.escape(value, quote=True)}">'
        for key, value in meta.items()
    ]
    if Path(name).name in seo.SHOWCASE_NOINDEX:
        tags.append('<meta name="robots" content="noindex, follow">')
    else:
        data = seo.structured_data(site, path, title, description, seo.SHOWCASE_UPDATED)
        tags.append(str(seo.json_ld_tag(data)))
    return re.sub(r"<head[^>]*>", lambda m: m.group(0) + "\n" + "\n".join(tags), page, count=1)
