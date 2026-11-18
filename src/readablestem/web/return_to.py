"""Where to come back to after signing in again (UX review MF19).

A session can end in the middle of a review: signing out on another device ends every
session of the account (`auth.logout`), and a session lasts seven days. The reviewer
then lands on the sign-in page, and used to land on "My documents" after it, so the
decision she was on (or any deep link) was lost. Now:

- a page asked for while signed out (a GET) is remembered as the place to come back to;
- a decision posted while signed out remembers that decision on the review screen
  (`/jobs/{id}/review?item=<id>`), and Undo the review screen;
- signing in (`auth.finish_login`) goes there instead of `/app`.

Only pages of this app are kept: the GET routes whose function takes the signed-in or the
admin user (`build_pages`, run once when the app is made), so a page added later is
remembered without anyone adding it to a list. The path must also be plain (letters,
digits, `_ - . /`), with a query of ids and numbers. Anything else (an image, a
stylesheet, `//elsewhere.example`, `/\\elsewhere`) is never remembered or followed, so
this cannot become an open redirect. A request the browser makes for a page's picture,
stylesheet or status (`Sec-Fetch-Dest` other than `document`) is not remembered either:
signing in would land on a picture. It is ids only, never document
content, and it lives in the signed session cookie, which signing in clears. What was
typed is kept by the browser itself (`static/app.js`: a draft in this tab's
`sessionStorage`), never by the server.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from fastapi import APIRouter, Request
from fastapi.dependencies.models import Dependant
from fastapi.responses import Response
from fastapi.routing import APIRoute

from readablestem.web.deps import render, require_admin, require_user

NEXT_KEY = "next"  # in the session: where signing in goes
DEFAULT = "/app"

_JOB = r"[0-9a-f]{32}"
_PLAIN = re.compile(r"^/(?!/)[A-Za-z0-9_./-]*$")
_QUERY = re.compile(r"^[A-Za-z0-9_=&-]{0,200}$")
_DECISION = re.compile(rf"^/jobs/({_JOB})/review/([A-Za-z0-9_-]{{1,64}})$")
_UNDO = re.compile(rf"^/jobs/({_JOB})/undo$")


def _takes_user(dependant: Dependant) -> bool:
    return any(
        sub.call in {require_user, require_admin} or _takes_user(sub)
        for sub in dependant.dependencies
    )


def build_pages(routers: Iterable[APIRouter]) -> list[re.Pattern[str]]:
    """The addresses of every GET page that needs a signed-in person or an admin, from the
    app's routers: the pages to come back to after signing in."""
    return [
        route.path_regex
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
        and "GET" in (route.methods or ())
        and _takes_user(route.dependant)
    ]


def safe_return(request: Request, path: object) -> str | None:
    """`path` when it is a page of this app to come back to, else None."""
    if not isinstance(path, str):
        return None
    page, _, query = path.partition("?")
    if not _PLAIN.match(page) or ".." in page or not _QUERY.match(query):
        return None
    if not any(pattern.match(page) for pattern in request.app.state.return_pages):
        return None
    return path


def return_path(request: Request) -> str | None:
    """The page to come back to for this request, or None: the page itself for a GET,
    the decision on the review screen for a decision's POST (from either view)."""
    path = request.url.path
    if request.method in {"GET", "HEAD"}:
        # Only a page the person opened: a picture, a stylesheet or a status poll of a page
        # whose session ended says what it is fetched for, and is not somewhere to land.
        if request.headers.get("sec-fetch-dest", "document") != "document":
            return None
        query = request.url.query
        return safe_return(request, f"{path}?{query}" if query else path)
    if request.method != "POST":
        return None
    if match := _DECISION.match(path):
        job_id, key = match.groups()
        return f"/jobs/{job_id}/review?item={key}"
    if match := _UNDO.match(path):
        return f"/jobs/{match.group(1)}/review"
    return None


def remember_return(request: Request) -> str | None:
    """Remember where signing in should go for this request (signed out); the path, or
    None when there is nothing to come back to."""
    target = return_path(request)
    if target is not None:
        request.session[NEXT_KEY] = target
    return target


def take_return(request: Request) -> str:
    """Where signing in goes: the remembered page (forgotten now), else `/app`. Read it
    before the session is rotated (`deps.start_session` clears it)."""
    return safe_return(request, request.session.pop(NEXT_KEY, None)) or DEFAULT


SIGNED_OUT_TITLE = "Please sign in again"
SIGNED_OUT_MESSAGE = (
    "Your session ended, so this was not saved. A session ends when you sign out on "
    "another device, and after 7 days."
)
BACK_TO_DECISION = "Sign in again to come back to the same decision."


def signed_out_page(request: Request, *, status_code: int) -> Response:
    """The page for a form posted after the session ended (401, or 403 when the cookie
    itself had expired and the form's CSRF token with it): what happened, the sign-in
    buttons, and sign-in remembered to come back to the decision. When the form was a
    decision, `app.js` adds a line if this tab kept a draft of what was typed."""
    back = remember_return(request)
    decision = _DECISION.match(request.url.path) if request.method == "POST" else None
    context = {
        "title": SIGNED_OUT_TITLE,
        "message": SIGNED_OUT_MESSAGE + (f" {BACK_TO_DECISION}" if back else ""),
        "status": status_code,
        "job_id": None,
        "sign_in": True,
        # The form's address: the key of the draft app.js may have kept (ids only).
        "draft_for": request.url.path if decision else "",
    }
    return render(request, "error.html", context, status_code=status_code)


def decision_link(request: Request) -> str | None:
    """For a decision's form refused while still signed in (its CSRF token is from before
    the last sign-in): the decision on the review screen, to choose again."""
    return return_path(request) if request.method == "POST" else None
