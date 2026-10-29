"""What search engines and AI crawlers read: the list of public pages, robots.txt,
sitemap.xml, llms.txt, the IndexNow key, the structured data (JSON-LD) of the public pages,
and the middleware that answers requests the way crawlers make them.

The public pages are the ones a person can read without signing in. They are listed once,
here: the sitemap names them, and every other page carries `noindex` (base.html), so a
sign-in page, an error page or a job page is never offered to a search engine.

The showcase (`/showcase/`, routes/discovery.py) is the static reviewer demo that
`site/build.py` writes (docs/site.md); the Docker image builds it into `SHOWCASE_DIR`.
Without a build (local development, tests) it has no pages and the sitemap leaves it out.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from markupsafe import Markup
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SITE_NAME = "ReadableSTEM"
SOURCE_URL = "https://github.com/harishm17/readablestem"
AUTHOR = "Harish Manoharan"
AUTHOR_URL = "https://harishmanoharan.com"
# The same @id as the Person on harishmanoharan.com, so search engines join the two.
AUTHOR_ID = "https://harishmanoharan.com/#person"
AUTHOR_SAME_AS = ("https://github.com/harishm17", "https://www.linkedin.com/in/harishm17/")
CASE_STUDY_URL = "https://harishmanoharan.com/work/readablestem/"
PROOF_URL = f"{SOURCE_URL}/blob/main/PROOF.md"

DESCRIPTION = (
    "Turns STEM course PDFs into accessible web pages: equations as MathML a screen reader "
    "can read, real headings and described figures. Free and open source."
)

# IndexNow (indexnow.org): a search engine fetches /<key>.txt to check that a ping for this
# site came from its owner. The key is public by design.
INDEXNOW_KEY = "18149cf3a15bfa791042a3df7fdc0388"  # gitleaks:allow (public by design)

SHOWCASE_DIR = Path(__file__).resolve().parent / "showcase"
SHOWCASE_PREFIX = "/showcase/"
# Built showcase pages that stay out of search: the stored conversions repeat OpenStax's own
# text (the Physics 2.3 one is /demo), and the stemcheck sample is a generated report.
SHOWCASE_NOINDEX = ("converted.html", "sample-report.html")
# The date the showcase's text last changed (site/templates, site/examples, PROOF.md).
SHOWCASE_UPDATED = "2026-10-28"


@dataclass(frozen=True)
class PublicPage:
    path: str
    # The date the page's text last changed; update it with the template.
    updated: str


PUBLIC_PAGES = (
    PublicPage("/", "2026-10-28"),
    PublicPage("/demo", "2026-10-14"),
    PublicPage("/limits", "2026-10-22"),
    PublicPage("/help/posting", "2026-10-27"),
    PublicPage("/privacy", "2026-10-27"),
    PublicPage("/terms", "2026-10-20"),
)
PUBLIC_PATHS = frozenset(page.path for page in PUBLIC_PAGES)


def showcase_pages(root: Path | None = None) -> list[str]:
    """The indexable showcase pages, as site paths (`/showcase/method/index.html`)."""
    root = SHOWCASE_DIR if root is None else root
    if not root.is_dir():
        return []
    return sorted(
        SHOWCASE_PREFIX + path.relative_to(root).as_posix()
        for path in root.rglob("*.html")
        if path.name not in SHOWCASE_NOINDEX and path.name != "404.html"
    )


def is_indexable(path: str) -> bool:
    return path in PUBLIC_PATHS


# --------------------------------------------------------------------------- robots, sitemap

# Private areas: signed-in pages, sign-in callbacks and the cron hook. They need a session
# anyway; this only keeps crawlers from spending requests on redirects to /login.
DISALLOW = (
    "/app",
    "/jobs/",
    "/account",
    "/admin",
    "/courses",
    "/uploads/",
    "/auth/",
    "/internal/",
    "/dev-login",
    "/login/email",
    "/logout",
)
# Named so that a crawler which reads only its own group still finds the same rules.
AI_CRAWLERS = (
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-SearchBot",
    "Claude-User",
    "PerplexityBot",
    "Perplexity-User",
    "Google-Extended",
    "Applebot-Extended",
    "CCBot",
    "Bingbot",
)


def robots_txt(site: str) -> str:
    rules = ["Allow: /", *(f"Disallow: {path}" for path in DISALLOW)]
    groups = [["User-agent: *", *rules]]
    groups += [[f"User-agent: {agent}", *rules] for agent in AI_CRAWLERS]
    body = "\n\n".join("\n".join(group) for group in groups)
    return f"{body}\n\nSitemap: {site}/sitemap.xml\n"


def sitemap_xml(site: str, showcase: list[str]) -> str:
    entries = [(f"{site}{page.path}", page.updated) for page in PUBLIC_PAGES]
    entries += [(f"{site}{path}", SHOWCASE_UPDATED) for path in showcase]
    urls = "\n".join(
        f"  <url><loc>{_xml_text(loc)}</loc><lastmod>{updated}</lastmod></url>"
        for loc, updated in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n"
    )


def _xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def llms_txt(site: str, showcase: bool) -> str:
    """llms.txt (llmstxt.org): what the site is and where its readable pages are."""
    lines = [
        f"# {SITE_NAME}",
        "",
        "> ReadableSTEM assists instructors who need to turn a STEM course PDF into an "
        "accessible web page. It reads the PDF, rebuilds equations as MathML a screen reader "
        "can speak, restores headings and lists, writes short alt text for figures, and "
        "returns one self-contained HTML file. Every change it is unsure about goes to a "
        "review screen where the instructor accepts, edits or rejects it.",
        "",
        "It assists; it does not certify. Nothing here establishes conformance with WCAG, the "
        "ADA or any other standard, and the output still needs a person to look at it. "
        f"It is a free, open-source beta run by {AUTHOR}, an individual developer; anyone "
        "with a Google account can sign in, and the demo needs no sign-in.",
        "",
        "## The service",
        "",
        f"- [Home]({site}/): what it does, how long it takes, how accurate it is, who can use it",
        f"- [Demo]({site}/demo): OpenStax Physics 2.3 converted, no sign-in needed",
        f"- [Known limits]({site}/limits): what it does not do yet",
        f"- [Posting in your course]({site}/help/posting): sharing the HTML file with students",
        f"- [Privacy]({site}/privacy): what happens to uploaded documents",
        f"- [Terms]({site}/terms)",
        "",
        "## Evidence",
        "",
    ]
    if showcase:
        lines += [
            f"- [Worked examples]({site}/showcase/examples/index.html): PDF pages beside the "
            "HTML they became, and the review decisions",
            f"- [Before and after]({site}/showcase/before-after/index.html): how equations are "
            "read aloud from the PDF and from the HTML",
            f"- [Method and limits]({site}/showcase/method/index.html): how the numbers were made",
        ]
    lines += [
        f"- [PROOF.md]({PROOF_URL}): the pre-registered evaluation on held-out OpenStax "
        "sections, including where it loses to a general chatbot",
        "",
        "## Source and author",
        "",
        f"- [Source code]({SOURCE_URL}): the code is MIT; the hosted service is offered "
        "under AGPL-3.0 terms",
        f"- [README]({SOURCE_URL}/blob/main/README.md)",
        f"- [{AUTHOR}]({AUTHOR_URL}): the developer",
        f"- [Case study]({CASE_STUDY_URL})",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- JSON-LD


def structured_data(
    site: str, path: str, title: str, description: str, updated: str | None
) -> dict[str, Any]:
    """One @graph for a public page: the site, the app, its author, and the page itself."""
    person = {
        "@type": "Person",
        "@id": AUTHOR_ID,
        "name": AUTHOR,
        "url": AUTHOR_URL,
        "sameAs": list(AUTHOR_SAME_AS),
    }
    organization = {
        "@type": "Organization",
        "@id": f"{site}/#organization",
        "name": SITE_NAME,
        "url": f"{site}/",
        "logo": f"{site}/static/icon-512.png",
        "founder": {"@id": AUTHOR_ID},
        "sameAs": [SOURCE_URL],
    }
    website = {
        "@type": "WebSite",
        "@id": f"{site}/#website",
        "name": SITE_NAME,
        "url": f"{site}/",
        "description": DESCRIPTION,
        "inLanguage": "en",
        "publisher": {"@id": f"{site}/#organization"},
    }
    application = {
        "@type": "SoftwareApplication",
        "@id": f"{site}/#app",
        "name": SITE_NAME,
        "url": f"{site}/",
        "description": DESCRIPTION,
        "applicationCategory": "EducationalApplication",
        "operatingSystem": "Any (web browser)",
        "isAccessibleForFree": True,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "featureList": [
            "Equations as MathML that a screen reader reads aloud",
            "Real headings, in a repaired outline",
            "Short alt text for figures, or a clear flag where a description is still needed",
            "A short review by the instructor of what could change what a student reads",
        ],
        "author": {"@id": AUTHOR_ID},
        "publisher": {"@id": f"{site}/#organization"},
        "license": "https://opensource.org/licenses/MIT",
        "sameAs": [SOURCE_URL],
    }
    page: dict[str, Any] = {
        "@type": "WebPage",
        "@id": f"{site}{path}#webpage",
        "url": f"{site}{path}",
        "name": title,
        "description": description,
        "inLanguage": "en",
        "isPartOf": {"@id": f"{site}/#website"},
        "about": {"@id": f"{site}/#app"},
        "author": {"@id": AUTHOR_ID},
    }
    if updated:
        page["dateModified"] = updated
    return {
        "@context": "https://schema.org",
        "@graph": [website, organization, application, person, page],
    }


def page_json_ld(site: str, path: str, title: str, description: str) -> Markup:
    """The JSON-LD data block for an app page (base.html, public pages only)."""
    updated = next((page.updated for page in PUBLIC_PAGES if page.path == path), None)
    title, description = html.unescape(str(title)), html.unescape(str(description))
    return json_ld_tag(structured_data(site, path, title, description, updated))


def json_ld_tag(data: dict[str, Any]) -> Markup:
    """A `<script type="application/ld+json">` data block. Browsers never run a data block,
    so the app's `script-src 'self'` policy stays as it is. `<`, `>` and `&` are written as
    JSON escapes so no text inside can close the element."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Markup('<script type="application/ld+json">{}</script>').format(Markup(text))


# --------------------------------------------------------------------------- middleware


class CrawlerRequestsMiddleware:
    """Requests the way crawlers and link checkers make them.

    - HEAD is answered as GET without the body (FastAPI routes take GET only, so HEAD was a
      405 on every page).
    - The www host is sent (301) to the canonical host from BASE_URL, and a path with a
      trailing slash to the same path without it, so each page has one address. The
      showcase keeps its slashes: its folders are pages.
    """

    def __init__(self, app: ASGIApp, *, base_url: str) -> None:
        self.app = app
        self.base_url = base_url.rstrip("/")
        self.host = urlsplit(self.base_url).hostname or ""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        target = self._redirect_target(scope)
        if target is not None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 301,
                    "headers": [(b"location", target.encode("latin-1")), (b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        if scope["method"] == "GET":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)

    def _redirect_target(self, scope: Scope) -> str | None:
        path: str = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        suffix = f"?{query}" if query else ""
        host = Headers(scope=scope).get("host", "").split(":", 1)[0].lower()
        keep_slash = path.startswith((SHOWCASE_PREFIX, "/static/"))
        if path != "/" and path.endswith("/") and not keep_slash:
            # "//host/" must not become a protocol-relative "//host" in Location.
            path = "/" + path.strip("/")
        if self.host and host == f"www.{self.host}":
            return f"{self.base_url}{path}{suffix}"
        if path != scope["path"]:
            return f"{path}{suffix}"
        return None
