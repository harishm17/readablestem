"""The Symbols screen of a document with handwritten equations (`web.inventory`, H11).

GET `/jobs/{id}/symbols` lists every letter and symbol read in the handwritten equations,
grouped with the ones that look alike, each with how often and where it stands. POST
`/jobs/{id}/symbols` writes one symbol as another everywhere (leaving out the places the form
ticked) as one review write: the rules are in `web.inventory`, the write is the review's own
(`routes/edits.write_review`), so it is CSRF-checked and owner-only, refused from an
out-of-date page (409, nothing changed), counted, recorded and taken back by one Undo. The
page after it says what was changed and how many were left as before, and offers that Undo.

Nothing typed or read is logged: the log line has counts only.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from readablestem.web import inventory
from readablestem.web.deps import UserDep, flash, pop_flash, render, require_csrf
from readablestem.web.logsafe import log_event
from readablestem.web.review import ReviewInvalid, ReviewStale
from readablestem.web.routes.edits import write_review
from readablestem.web.routes.review import (
    LIST_VIEW,
    SYMBOLS_FLASH,
    _load_versioned,
    list_url,
    symbols_url,
    undo_offer,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _view(raw: str) -> str:
    return LIST_VIEW if raw != "split" else "split"


def _back(job_id: str, view: str) -> str:
    return list_url(job_id, "needs-decision") if view == LIST_VIEW else f"/jobs/{job_id}/review"


def _page(
    request: Request,
    job_id: str,
    user: Any,
    *,
    view: str,
    errors: list[dict[str, str]] | None = None,
    stale: bool = False,
    status_code: int = 200,
) -> Response:
    job, result, crop_ids, version = _load_versioned(request, job_id, user)
    flashes = pop_flash(request)
    notice = None
    undo = False
    if flashes and flashes[-1].get("element_id") == SYMBOLS_FLASH:
        notice = str(flashes[-1].get("message", ""))
        undo = flashes[-1].get("undo", True) is not False and undo_offer(
            request, job.id, user, version
        )
    context = {
        "job": job,
        "inv": inventory.inventory(result, crop_ids),
        "result_version": version,
        "view": view,
        "back_href": _back(job.id, view),
        "errors": errors or [],
        "stale": {
            "title": "This page is out of date",
            "message": "The document changed after this page was opened, so nothing was changed.",
            "now": "",
            "href": f"/jobs/{job.id}/symbols?view={view}",
        }
        if stale
        else None,
        "notice": notice,
        "undo": undo,
        "flash": [],
    }
    return render(request, "review_inventory.html", context, status_code=status_code)


@router.get("/jobs/{job_id}/symbols")
def symbols_page(request: Request, job_id: str, user: UserDep, view: str = "") -> Response:
    return _page(request, job_id, user, view=_view(view))


@router.post("/jobs/{job_id}/symbols", dependencies=[Depends(require_csrf)])
def relabel(
    request: Request,
    job_id: str,
    user: UserDep,
    symbol: Annotated[str, Form()] = "",
    to: Annotated[str, Form()] = "",
    exclude: Annotated[list[str] | None, Form()] = None,
    version: Annotated[str, Form()] = "",
    view: Annotated[str, Form()] = "",
) -> Response:
    """Write `symbol` as `to` in every place it stands in the equations still waiting, except
    the places in `exclude` ("element id:nth"), in one write."""
    view = _view(view)
    left_out = inventory.excluded_places(exclude or [])
    if not version.strip():  # a form without the version it was drawn at proves nothing
        return _page(request, job_id, user, view=view, stale=True, status_code=409)
    outcome: list[inventory.Relettering] = []

    def make(result):
        done = inventory.relabel(result, symbol, to, left_out)
        outcome.append(done)
        return done.changes

    try:
        changes, _, owned_id = write_review(request, job_id, user, version, make, kind="group")
    except ReviewStale:
        log_event(logger, "review_stale", job_id=job_id)
        return _page(request, job_id, user, view=view, stale=True, status_code=409)
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action="relabel")
        errors = [{"field_id": e.field_id, "message": e.message} for e in exc.errors]
        return _page(request, job_id, user, view=view, errors=errors, status_code=422)
    done = outcome[-1]
    log_event(
        logger,
        "review_relabel",
        job_id=job_id,
        changed=len(changes),
        left=len(done.left),
        left_out=len(left_out),
    )
    flash(request, inventory.done_message(done), element_id=SYMBOLS_FLASH)
    return RedirectResponse(symbols_url(owned_id, view), status_code=303)
