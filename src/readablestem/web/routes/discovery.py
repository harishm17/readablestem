"""Files for search engines and AI crawlers (robots.txt, sitemap.xml, llms.txt, the IndexNow
key, the favicon) and the showcase: the static reviewer demo under /showcase/ (web.seo)."""

from __future__ import annotations

import functools
import html
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from readablestem.web import seo, signin_words
from readablestem.web.assets import STATIC_DIR, cached_html, static_url
from readablestem.web.deps import get_ctx
from readablestem.web.security import VIEW_CSP

router = APIRouter()

# Crawl files change only with a deploy.
CRAWL_MAX_AGE = 3600
CRAWL_CACHE = f"public, max-age={CRAWL_MAX_AGE}"


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
    ctx = get_ctx(request)
    words = signin_words.sign_in_words(
        google=ctx.sign_in_client("google") is not None,
        microsoft=ctx.sign_in_client("microsoft") is not None,
        email_link=False,  # the link signs in an account that exists; it makes none
    )
    body = seo.llms_txt(
        _site(request), showcase=bool(seo.showcase_pages()), sign_in=words or "Google"
    )
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
    """The folder's address. Where the showcase was not built (the folder is absent) there is
    nothing to redirect to: a clean 404, not a redirect to one."""
    if not seo.showcase_pages():
        raise HTTPException(status_code=404)
    return Response(status_code=301, headers={"Location": seo.SHOWCASE_PREFIX})


@router.get("/showcase/{rel:path}")
def showcase(request: Request, rel: str) -> Response:
    path, name = _showcase_file(seo.SHOWCASE_DIR, rel)
    if path.suffix != ".html":
        return FileResponse(path, headers={"Cache-Control": CRAWL_CACHE})
    page = _with_head_tags(path.read_text(encoding="utf-8"), _site(request), name)
    headers = {}
    if path.name in seo.SHOWCASE_NOINDEX:
        # A stored conversion carries inline styles and data: images: sandboxed, as /demo.
        headers["Content-Security-Policy"] = VIEW_CSP
    return cached_html(request, page, max_age=CRAWL_MAX_AGE, headers=headers)


@functools.cache
def _showcase_files(root: Path) -> dict[str, tuple[Path, str]]:
    """Every file of the build that may be served, by the address it answers to under
    `/showcase/` (a folder's address, with or without its slash, is its index.html):
    (the file, its name inside the build). Listed from the build itself, once: a request
    only picks one of these, it never builds a path. Left out: hidden files, 404.html and
    anything that resolves outside the build (a symlink)."""
    root = root.resolve()
    served: dict[str, tuple[Path, str]] = {}
    for found in sorted(root.rglob("*")) if root.is_dir() else []:
        path = found.resolve()
        relative = found.relative_to(root)
        if (
            not path.is_file()
            or not path.is_relative_to(root)
            or any(part.startswith(".") for part in relative.parts)
            or found.name == "404.html"
        ):
            continue
        entry = (path, relative.as_posix())
        served[relative.as_posix()] = entry
        if found.name == "index.html":
            folder = relative.parent.as_posix().removeprefix(".")
            served[folder] = served[folder + "/" if folder else ""] = entry
    return served


def _showcase_file(root: Path, rel: str) -> tuple[Path, str]:
    """The built file for a showcase address, and its name in the build. 404 for anything
    that is not one of the build's files (`_showcase_files`), or when there is no build."""
    entry = _showcase_files(root).get(rel)
    if entry is None:
        raise HTTPException(status_code=404)
    return entry


def _with_head_tags(page: str, site: str, name: str) -> str:
    """A built showcase page with its address, link-preview tags and structured data added
    after <head>; the page on disk is the static build, unchanged."""
    title_match = re.search(r"<title>(.*?)</title>", page, re.S)
    title = html.unescape(title_match.group(1).strip()) if title_match else seo.SITE_NAME
    desc_match = re.search(r'<meta name="description" content="([^"]*)"', page)
    description = html.unescape(desc_match.group(1)) if desc_match else seo.DESCRIPTION
    path = seo.showcase_canonical(seo.SHOWCASE_PREFIX + name)
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
        data = seo.structured_data(site, path, title, description, seo.showcase_updated())
        tags.append(str(seo.json_ld_tag(data)))
    return re.sub(r"<head[^>]*>", lambda m: m.group(0) + "\n" + "\n".join(tags), page, count=1)
