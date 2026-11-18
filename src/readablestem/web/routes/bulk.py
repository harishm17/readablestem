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

from readablestem.web import bulk_accept, queue
from readablestem.web.deps import PageError, UserDep, flash, render, require_csrf
from readablestem.web.forms import submit_once
from readablestem.web.logsafe import log_event
from readablestem.web.review import (
    ReviewInvalid,
    ReviewStale,
    item_labels,
    item_numbers,
    reading_order,
    review_anchor,
)
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
    request: Request, job_id: str, slug: str, user: UserDep, view: str = "", page: int = 1
) -> Response:
    """The list of what the action would accept, one page of it at a time, and the button
    that accepts the page (`web.bulk_accept.PAGE_SIZE`)."""
    spec = _spec(slug)
    job, result, crop_ids, version = _load_versioned(request, job_id, user)
    elements = {e.id: e for e in result.elements}
    numbers = item_numbers(result)
    labels = item_labels(result)
    found = bulk_accept.candidates(result, slug)
    listed, page, pages = bulk_accept.page_of(found, page)
    items = [
        _item(fix, elements.get(fix.element_id), numbers[fix.element_id], job.id, crop_ids, None,
              labels.get(fix.element_id), origin=result.origin)
        for fix in listed
    ]  # fmt: skip
    split = view == "split"
    shown_view = _view(view)

    def page_href(number: int) -> str:
        return f"/jobs/{job.id}/accept/{slug}?view={shown_view}&page={number}"

    # With nothing to accept at once, the page names the next useful thing to do: the first
    # of this kind still waiting (a check failed, or it was flagged), else the next decision.
    waiting = reading_order(result, [f for f in result.fixes if f.kind is spec.kind and f.pending])
    open_total = sum(d.open for d in queue.decisions(result))
    first = review_anchor(result, waiting[0].element_id) if waiting else ""
    context = {
        "job": job,
        "spec": spec,
        "items": items,
        "count": len(found),
        "counted": f"{len(found)} {spec.one if len(found) == 1 else spec.many}",
        "shown": len(items),
        "page": page,
        "pages": pages,
        "first_shown": (page - 1) * bulk_accept.PAGE_SIZE + 1,
        "prev_href": page_href(page - 1) if page > 1 else "",
        "next_href": page_href(page + 1) if page < pages else "",
        "result_version": version,
        "view": shown_view,
        "back_href": (review_url(job.id) if split else list_url(job.id, "needs-decision")),
        "kind_waiting": len(waiting),
        "kind_href": (
            review_url(job.id, item=waiting[0].element_id)
            if split and waiting
            else list_url(job.id, first)
            if waiting
            else ""
        ),
        "open_total": open_total,
    }
    return render(request, "review_bulk.html", context)


@router.post("/jobs/{job_id}/accept/{slug}", dependencies=[Depends(require_csrf)])
@submit_once("review.accept_kind")
def accept(
    request: Request,
    job_id: str,
    slug: str,
    user: UserDep,
    version: Annotated[str, Form()] = "",
    view: Annotated[str, Form()] = "",
    ids: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """Accept the items the page listed (`ids`: the page the person was shown; without them,
    every candidate), in one write (one Undo takes all back)."""
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
            lambda result: bulk_accept.accept_all(result, slug, ids),
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
    left = len(bulk_accept.candidates(result, slug)) if ids is not None else 0
    if left:  # a page of a longer list: the next page is where the person goes on
        what_left = f"{left} more {spec.one if left == 1 else spec.many}"
        rest = f"{what_left} {'is' if left == 1 else 'are'} listed below."
        flash(request, f"{message} Undo is on the review screen. {rest}", undo=False)
        return RedirectResponse(f"/jobs/{job_id}/accept/{slug}?view={view}", status_code=303)
    flash(request, message, element_id=GROUP_FLASH + slug)
    if view == LIST_VIEW:
        return RedirectResponse(_landing(owned_id, result, changes[-1].element_id), status_code=303)
    return _split_redirect(owned_id, result, None)
