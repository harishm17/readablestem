"""Accept many decisions at once (`web.bulk_accept`): a page that says what would be
accepted, and the POST that does it.

GET `/jobs/{id}/accept/{equations|figures}` shows the count and the list of every item the
action would accept, each with a link to open it, and a form. POST makes the decisions:
one write (the review's own, `routes/edits.write_review`), each fix accepted the ordinary
way and marked `bulk` in its record, so the action is one step for Undo. The form carries
the review version the page was drawn at: a document that changed since is refused
(nothing accepted), so what is accepted is exactly what was listed. CSRF-checked and
owner-only like every review route.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from readablestem.web import bulk_accept
from readablestem.web.deps import PageError, UserDep, flash, render, require_csrf
from readablestem.web.logsafe import log_event
from readablestem.web.review import ReviewInvalid, ReviewStale, item_labels, item_numbers
from readablestem.web.routes.edits import write_review
from readablestem.web.routes.jobs import NOT_FOUND
from readablestem.web.routes.review import (
    GROUP_FLASH,
    LIST_VIEW,
    _item,
    _landing,
    _load_versioned,
    _split_redirect,
    _stale_page,
    list_url,
)
from readablestem.web.routes.split import review_url

logger = logging.getLogger(__name__)
router = APIRouter()


def _spec(slug: str) -> bulk_accept.BulkKind:
    spec = bulk_accept.BULK_KINDS.get(slug)
    if spec is None:
        raise PageError(404, *NOT_FOUND)
    return spec


def _view(raw: str) -> str:
    return LIST_VIEW if raw != "split" else "split"


@router.get("/jobs/{job_id}/accept/{slug}")
def confirm_page(
    request: Request, job_id: str, slug: str, user: UserDep, view: str = ""
) -> Response:
    """The list of what the action would accept, and the button that does it."""
    spec = _spec(slug)
    job, result, crop_ids, version = _load_versioned(request, job_id, user)
    elements = {e.id: e for e in result.elements}
    numbers = item_numbers(result)
    labels = item_labels(result)
    items = [
        _item(fix, elements.get(fix.element_id), numbers[fix.element_id], job.id, crop_ids, None,
              labels.get(fix.element_id))
        for fix in bulk_accept.candidates(result, slug)
    ]  # fmt: skip
    context = {
        "job": job,
        "spec": spec,
        "items": items,
        "count": len(items),
        "counted": f"{len(items)} {spec.one if len(items) == 1 else spec.many}",
        "result_version": version,
        "view": _view(view),
        "back_href": (
            review_url(job.id) if view == "split" else list_url(job.id, "needs-decision")
        ),
    }
    return render(request, "review_bulk.html", context)


@router.post("/jobs/{job_id}/accept/{slug}", dependencies=[Depends(require_csrf)])
def accept(
    request: Request,
    job_id: str,
    slug: str,
    user: UserDep,
    version: Annotated[str, Form()] = "",
    view: Annotated[str, Form()] = "",
) -> Response:
    """Accept every item the page listed, in one write (one Undo takes all back)."""
    spec = _spec(slug)
    view = _view(view)
    if not version.strip():  # a form without the version it was drawn at proves nothing
        return _stale_page(request, job_id, user, view, slug)
    try:
        changes, result, owned_id = write_review(
            request,
            job_id,
            user,
            version,
            lambda result: bulk_accept.accept_all(result, slug),
            kind="group",
        )
    except ReviewStale:
        return _stale_page(request, job_id, user, view, slug)
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action=f"bulk_{slug}")
        flash(request, " ".join(e.message for e in exc.errors), undo=False)
        where = (
            list_url(exc.job_id, "needs-decision") if view == LIST_VIEW else review_url(exc.job_id)
        )
        return RedirectResponse(where, status_code=303)
    n = len(changes)
    log_event(logger, "review_accept_bulk", job_id=job_id, group=slug, changed=n)
    what = spec.one if n == 1 else spec.many
    message = f"Accepted {n} {what} {spec.tail}. Undo takes them all back in one step."
    flash(request, message, element_id=GROUP_FLASH + slug)
    if view == LIST_VIEW:
        return RedirectResponse(_landing(owned_id, result, changes[-1].element_id), status_code=303)
    return _split_redirect(owned_id, result, None)
