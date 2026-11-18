"""Pages for the people who build the site. Included only where `web.dev_only.available`
is true, so a production server answers 404 for every address here."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from readablestem.web.deps import render

router = APIRouter()


@router.get("/style")
def style_page(request: Request) -> Response:
    """The tokens and components on one page, for the people who build the site and for
    the screenshot and axe checks. Not in `seo.PUBLIC_PAGES`, so it is `noindex` and not in
    the sitemap; nothing links to it."""
    return render(request, "style.html", {"active": "style"})
