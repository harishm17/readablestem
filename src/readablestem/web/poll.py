"""The poll contract of every fragment the pages ask for by themselves (SW8).

A page that waits for something (the job page, My documents, the batch page, the parts
preview) asks the server for a small piece of itself with htmx. Two rules hold for all of them:

- A fragment is never a page. A fragment route answers the piece only to an htmx request; to
  anything else (a bookmarked address, a link followed by mistake, a person without scripts)
  it answers a 303 to the page that contains the piece (`fragment_or_redirect`).
- One interval. Every poller asks every `POLL_S` seconds (`TRIGGER` is the htmx attribute value
  the templates print). "Nothing changed" is a 204, so a screen reader is not interrupted;
  `static/app.js` writes the time of the last answer, 204 included, into the quiet line
  "Last checked" outside the live region, so the page shows it is still looking.
"""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import RedirectResponse

POLL_S = 3
TRIGGER = f"every {POLL_S}s"


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def fragment_or_redirect(request: Request, page_url: str) -> Response | None:
    """None for an htmx request (answer the fragment), else a 303 to the page that holds it."""
    if is_htmx(request):
        return None
    return RedirectResponse(page_url, status_code=303)
