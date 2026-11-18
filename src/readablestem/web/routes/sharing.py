"""Sharing a document with named, signed-in people (G1, `web.shares`).

Three owner-only routes: the page that lists who has access (`GET /jobs/{id}/share`), adding
a person or changing their right (`POST /jobs/{id}/share`), and removing one
(`POST /jobs/{id}/share/{share_id}/revoke`). Both POSTs are CSRF-checked and work without
JavaScript. Nothing is sent to the person: the page shows the document's address for the
owner to copy (a "Copy link" button with script, a selectable field without), and when the
document will be deleted, which ends every share. The address works for nobody who is not signed
in with the shared address.

No address is written to a log, a flash notice (it lives in the session cookie) or an event.

With `FEATURE_CLASS_LINK` the page also has a "Class link" section and five more owner-only POSTs
(`web.links`): make a link, pause it, resume it, remove it, and update the published copy. All
are CSRF-checked and work without JavaScript. Making a link answers with the page itself (not a
redirect), because the address is shown that once and a redirect could carry it only in the
session cookie (signed, not encrypted, so readable by whoever holds it) or the URL; every later
view of the page shows that a link exists and what it has done, never the address. Making one
follows the one submit rule (`web.forms`, `link.make`), so a reload of that answer, which sends
the same form again, is answered with a redirect to this page and makes no second link: the
first link, and its address in the owner's hands, stay. A new link made on purpose is a new
form, so a new token. With the setting off the five routes and the section do not exist for
anyone (404).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from readablestem.web import documents, links, output, retention, shares, storage
from readablestem.web.dates import format_when
from readablestem.web.db import Job, session_scope, utcnow
from readablestem.web.deps import (
    PageError,
    UserDep,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from readablestem.web.forms import submit_once
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import NOT_FOUND, current_html, job_access, require_done
from readablestem.web.shares import OWNER

logger = logging.getLogger(__name__)
router = APIRouter()


# What the owner is told, by what happened. No address is in a notice (the session cookie
# holds it). "Nothing was sent" is said only when a person was added: the one case where the
# owner has something to do next.
SHARE_OUTCOME = {
    shares.ADDED: (
        "Added with the right to {right}. Nothing was sent: copy the link below and give it "
        "to the person yourself."
    ),
    shares.CHANGED: "Changed. Their right is now {right}. Nothing was sent.",
    shares.UNCHANGED: "No change: that person already has the right to {right}.",
}


def _title(job: Job) -> str:
    return documents.document_title(job).text


def _page(
    request: Request,
    job_id: str,
    title: str,
    *,
    errors: list[dict[str, str]] | None = None,
    typed: str = "",
    typed_role: str = shares.VIEW,
    status_code: int = 200,
    link_address: str = "",
    link_errors: list[dict[str, str]] | None = None,
    link_typed: dict[str, str] | None = None,
) -> Response:
    """The sharing page of a document the caller has loaded as its owner (`title` is read
    before the session closes). `link_address`: the class link just made, shown this once."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        rows = shares.shares_of(session, job_id)
        listed = [{"id": r.id, "email": r.email, "role": r.role} for r in rows]
        job = session.get(Job, job_id)
        # The shares go with the document, so its deletion time is the shares' too.
        retain_until = job.retain_until if job is not None else None
        retention_mode = job.retention_mode if job is not None else "day"
        # People who reach it through its course (FEATURE_COURSE_GRANTS), read-only here.
        through = shares.course_people(session, job) if job is not None else None
        class_link = (
            _class_link(ctx, session, job, link_address, link_errors or [], link_typed or {})
            if ctx.settings.feature_class_link and job is not None
            else None
        )
    context = {
        "job_id": job_id,
        "doc_title": title,
        "shares": listed,
        "address": f"{ctx.settings.base_url.rstrip('/')}/jobs/{job_id}",
        "roles": [(role, shares.ROLE_WORDS[role], shares.ROLE_HELP[role]) for role in shares.ROLES],
        "retain_until": retain_until,
        "retention_mode": retention_mode,
        "max_shares": shares.MAX_SHARES_PER_JOB,
        "errors": (errors or []) + (link_errors or []),
        "email_error": next(
            (e["message"] for e in errors or [] if e["field_id"] == "share-email"), ""
        ),
        "role_error": next(
            (e["message"] for e in errors or [] if e["field_id"] == "share-role"), ""
        ),
        "typed": typed,
        "typed_role": typed_role,
        "role_words": shares.ROLE_WORDS,
        "class_link": class_link,
        "course_people": through,
    }
    return render(request, "share.html", context, status_code=status_code)


def _class_link(
    ctx, session, job: Job, address: str, errors: list[dict[str, str]], typed: dict[str, str]
) -> dict:
    """What the "Class link" section prints: the link's state in words, what it has done, which
    version is published and which is current, and what the owner may do now."""
    now = utcnow()
    row = links.current_link(session, job.id)
    ready = output.export_state(job).key == output.READY
    current = output.version_id(job)
    published = links.published_version(session, job.id)
    kept = row is not None and links.is_live(row, now, serving=False)
    shown = None
    if row is not None:
        if not kept:
            state = f"Closed on {format_when(row.closes_at)}"
        elif row.paused_at is not None:
            state = "Paused"
        elif not links.is_live(row, now):
            state = f"Opens on {format_when(row.opens_at)}"
        else:
            state = "Open"
        shown = {
            "label": row.label,
            "state": state,
            "paused": row.paused_at is not None,
            "kept": kept,
            "opens_at": row.opens_at,
            "closes_at": row.closes_at,
            "open_count": row.open_count,
            "first_opened_at": row.first_opened_at,
        }
    latest = links.last_day(now)
    return {
        "link": shown,
        "address": address,
        "ready": ready,
        "published": published,
        "current": current,
        "out_of_date": kept and published is not None and published != current,
        "can_update": kept and ready and published != current,
        "today": now.date().isoformat(),
        "latest": latest.isoformat(),
        "max_days": links.MAX_DAYS,
        "after": retention.after_link_words(ctx.settings, job.retention_mode),
        "typed": typed,
        "errors": {e["field_id"]: e["message"] for e in errors},
    }


def _owned(request: Request, job_id: str, user) -> tuple[str, str]:
    """(id, title) of the owner's finished document; the id is the owned row's."""
    with session_scope(get_ctx(request).factory) as session:
        job = job_access(session, job_id, user, OWNER)
        require_done(job)
        if job.is_sample:
            raise PageError(404, *NOT_FOUND)
        return job.id, _title(job)


@router.get("/jobs/{job_id}/share")
def share_page(request: Request, job_id: str, user: UserDep) -> Response:
    owned_id, title = _owned(request, job_id, user)
    return _page(request, owned_id, title)


@router.post("/jobs/{job_id}/share", dependencies=[Depends(require_csrf)])
@submit_once("share.add")
def add_share(
    request: Request,
    job_id: str,
    user: UserDep,
    email: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
) -> Response:
    """Give a person access, or change the right they have. A mistake is the page again
    (422) with the reason beside the field and what was typed kept."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, OWNER, lock=True)
        owned_id = job.id  # redirects are built from the owned row, not the request
        title = _title(job)
        try:
            row, outcome = shares.share(session, job, user.email, email, role)
            kept_role = row.role
        except shares.ShareError as exc:
            log_event(logger, "share_refused", job_id=owned_id, code=exc.code)
            field = "share-role" if exc.code == "bad_role" else "share-email"
            return _page(
                request,
                owned_id,
                title,
                errors=[{"field_id": field, "message": exc.message}],
                typed=email[:320],
                typed_role=role if role in shares.ROLES else shares.VIEW,
                status_code=422,
            )
    log_event(logger, "share_set", job_id=owned_id, role=kept_role)
    flash(
        request,
        SHARE_OUTCOME[outcome].format(right=shares.ROLE_WORDS[kept_role]),
        undo=False,
        kind="info" if outcome == shares.UNCHANGED else "success",
    )
    return RedirectResponse(f"/jobs/{owned_id}/share", status_code=303)


@router.post("/jobs/{job_id}/share/{share_id}/revoke", dependencies=[Depends(require_csrf)])
@submit_once("share.revoke")
def revoke_share(request: Request, job_id: str, share_id: str, user: UserDep) -> Response:
    """Take a person's access away. It stops on their next request."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, OWNER, lock=True)
        owned_id = job.id
        removed = shares.revoke(session, owned_id, share_id)
    if removed:
        log_event(logger, "share_revoked", job_id=owned_id)
    # Not there (often a second click on Remove): the access is gone either way, so the
    # answer is the one the first click got (SW43, "already in that state").
    flash(
        request,
        "Access removed. They cannot open this document any more.",
        undo=False,
        kind="success",
    )
    return RedirectResponse(f"/jobs/{owned_id}/share", status_code=303)


# --------------------------------------------------------------------------- the class link


def _link_ctx(request: Request):
    """The app's context, for a class link route: 404 for everyone, as for any address that is
    not there, when the setting is off. Asked before the document, so the answer does not say
    whether the document exists."""
    ctx = get_ctx(request)
    if not ctx.settings.feature_class_link:
        raise PageError(404, *NOT_FOUND)
    return ctx


def _publish(ctx, session, job: Job, user) -> str:
    """Make the published copy from the ready, up-to-date student file; its version id.
    `StorageRefused` or `StorageFull` when the copy does not fit the person's allowance."""
    job = current_html(ctx, session, job)
    html = job.html or ""
    old = links.published(session, job.id)
    storage.check_room(
        session,
        ctx.settings,
        user.id,
        max(0, len(html.encode()) - (len(old.html.encode()) if old else 0)),
    )
    return links.store_copy(session, job, html)


NOT_READY_FOR_LINK = (
    "A class link can be made once this document is ready, with nothing left to check."
)
NO_ROOM_FOR_LINK = (
    "There is no room to keep a published copy. Delete a document you no longer need."
)


@router.post("/jobs/{job_id}/share/link", dependencies=[Depends(require_csrf)])
@submit_once("link.make")
def make_link(
    request: Request,
    job_id: str,
    user: UserDep,
    label: Annotated[str, Form()] = "",
    opens: Annotated[str, Form()] = "",
    closes: Annotated[str, Form()] = "",
) -> Response:
    """Make the class link (the one the document had is removed) and show its address, once."""
    ctx = _link_ctx(request)
    typed = {"label": label[:200], "opens": opens[:10], "closes": closes[:10]}
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, OWNER, lock=True)
        require_done(job)
        if job.is_sample:
            raise PageError(404, *NOT_FOUND)
        owned_id, title = job.id, _title(job)
        problem: dict[str, str] | None = None
        token = ""
        if output.export_state(job).key != output.READY:
            problem = {"field_id": "class-link-heading", "message": NOT_READY_FOR_LINK}
        else:
            try:
                token = links.make(
                    session,
                    job,
                    ctx.settings,
                    label=label,
                    opens=_day(opens, "link-opens"),
                    closes=_day(closes, "link-closes"),
                )
                _publish(ctx, session, job, user)
            except links.LinkError as exc:
                problem = {"field_id": exc.field, "message": exc.message}
            except (storage.StorageRefused, storage.StorageFull):
                problem = {"field_id": "class-link-heading", "message": NO_ROOM_FOR_LINK}
            if problem is not None:
                session.rollback()
    if problem is not None:
        log_event(logger, "link_refused", job_id=owned_id)
        return _page(
            request, owned_id, title, status_code=422, link_errors=[problem], link_typed=typed
        )
    log_event(logger, "link_made", job_id=owned_id)
    address = f"{ctx.settings.base_url.rstrip('/')}/s/{token}"
    return _page(request, owned_id, title, link_address=address)


def _day(value: str, field: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise links.LinkError(field, "Enter the day as year-month-day.") from None


@router.post("/jobs/{job_id}/share/link/pause", dependencies=[Depends(require_csrf)])
@submit_once("link.pause")
def pause_link(request: Request, job_id: str, user: UserDep) -> Response:
    return _link_change(request, job_id, user, "pause")


@router.post("/jobs/{job_id}/share/link/resume", dependencies=[Depends(require_csrf)])
@submit_once("link.resume")
def resume_link(request: Request, job_id: str, user: UserDep) -> Response:
    return _link_change(request, job_id, user, "resume")


@router.get("/jobs/{job_id}/share/link/remove")
def confirm_remove_link(request: Request, job_id: str, user: UserDep) -> Response:
    """The question the share page's "Remove the link" opens: the address cannot be got back."""
    ctx = _link_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, OWNER)
        require_done(job)
        if job.is_sample:
            raise PageError(404, *NOT_FOUND)
        owned_id, title = job.id, _title(job)
        row = links.current_link(session, owned_id)
        context = (
            None
            if row is None
            else {
                "job_id": owned_id,
                "doc_title": title,
                "label": row.label,
                "opened": row.open_count,
            }
        )
    if context is None:
        flash(request, "There is no class link to change.", undo=False)
        return RedirectResponse(f"/jobs/{owned_id}/share", status_code=303)
    return render(request, "class_link_remove_confirm.html", context)


@router.post("/jobs/{job_id}/share/link/remove", dependencies=[Depends(require_csrf)])
@submit_once("link.remove")
def remove_link(request: Request, job_id: str, user: UserDep) -> Response:
    return _link_change(request, job_id, user, "remove")


@router.post("/jobs/{job_id}/share/link/update", dependencies=[Depends(require_csrf)])
@submit_once("link.update")
def update_copy(request: Request, job_id: str, user: UserDep) -> Response:
    return _link_change(request, job_id, user, "update")


def _link_change(request: Request, job_id: str, user, action: str) -> Response:
    """Pause, resume or remove the link, or update the published copy: owner only. The answer is
    a notice on the share page; a change that is already made says so and changes nothing."""
    ctx = _link_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, OWNER, lock=True)
        require_done(job)
        if job.is_sample:
            raise PageError(404, *NOT_FOUND)
        owned_id = job.id
        row = links.current_link(session, owned_id)
        words = "There is no class link to change."
        kind: Literal["info", "success", "warning"] = "info"
        if action == "remove":
            if links.remove(session, job, ctx.settings):
                words = "The class link is removed. It cannot be opened any more."
                kind = "success"
        elif action in ("pause", "resume"):
            if links.pause(session, job, paused=action == "pause"):
                words = (
                    "The class link is paused. Nobody can open it until you resume it."
                    if action == "pause"
                    else "The class link is resumed."
                )
                kind = "success"
            elif row is not None:
                words = "No change: the link is already " + (
                    "paused." if action == "pause" else "not paused."
                )
        elif row is not None and links.is_live(row, serving=False):
            if output.export_state(job).key != output.READY:
                words = NOT_READY_FOR_LINK
                kind = "warning"
            else:
                try:
                    version = _publish(ctx, session, job, user)
                    words = f"The published copy is now version {version}."
                    kind = "success"
                except (storage.StorageRefused, storage.StorageFull):
                    session.rollback()
                    words = NO_ROOM_FOR_LINK
                    kind = "warning"
    log_event(logger, "link_changed", job_id=owned_id, action=action)
    flash(request, words, undo=False, kind=kind)
    return RedirectResponse(f"/jobs/{owned_id}/share", status_code=303)
