"""Sharing a document with named, signed-in people (G1, `web.shares`).

Three owner-only routes: the page that lists who has access (`GET /jobs/{id}/share`), adding
a person or changing their right (`POST /jobs/{id}/share`), and removing one
(`POST /jobs/{id}/share/{share_id}/revoke`). Both POSTs are CSRF-checked and work without
JavaScript. Nothing is sent to the person: the page shows the document's address for the
owner to copy (a "Copy link" button with script, a selectable field without), and when the
document will be deleted, which ends every share. The address works for nobody who is not signed in with the shared address.

No address is written to a log, a flash notice (it lives in the session cookie) or an event.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from readablestem.web import documents, shares
from readablestem.web.db import Job, session_scope
from readablestem.web.deps import (
    PageError,
    UserDep,
    flash,
    get_ctx,
    render,
    require_csrf,
)
from readablestem.web.logsafe import log_event
from readablestem.web.routes.jobs import NOT_FOUND, job_access, require_done
from readablestem.web.shares import OWNER

logger = logging.getLogger(__name__)
router = APIRouter()


def _title(job: Job) -> str:
    return documents.display_title(
        (job.result_json or {}).get("title"), job.filename, job.title_override
    )


def sign_in_words(*, google: bool, microsoft: bool, email_link: bool) -> str:
    """The ways a person can sign in on this server, for the sharing page: "Google", "Google
    or Microsoft", "Google, Microsoft or an emailed sign-in link". Empty when none is on
    (development sign-in only), so the page does not name a provider that is not there."""
    names = [
        name
        for name, on in (
            ("Google", google),
            ("Microsoft", microsoft),
            ("an emailed sign-in link", email_link),
        )
        if on
    ]
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


def _page(
    request: Request,
    job_id: str,
    title: str,
    *,
    errors: list[dict[str, str]] | None = None,
    typed: str = "",
    typed_role: str = shares.VIEW,
    status_code: int = 200,
) -> Response:
    """The sharing page of a document the caller has loaded as its owner (`title` is read
    before the session closes)."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        rows = shares.shares_of(session, job_id)
        listed = [{"id": r.id, "email": r.email, "role": r.role} for r in rows]
        job = session.get(Job, job_id)
        # The shares go with the document, so its deletion time is the shares' too.
        retain_until = job.retain_until if job is not None else None
        retention_mode = job.retention_mode if job is not None else "day"
    context = {
        "job_id": job_id,
        "doc_title": title,
        "shares": listed,
        "address": f"{ctx.settings.base_url.rstrip('/')}/jobs/{job_id}",
        "roles": [
            (role, shares.ROLE_WORDS[role], shares.ROLE_HELP[role]) for role in shares.ROLES
        ],
        "sign_in_words": sign_in_words(
            google=ctx.sign_in_client("google") is not None,
            microsoft=ctx.sign_in_client("microsoft") is not None,
            email_link=ctx.settings.email_login_enabled,
        ),
        "retain_until": retain_until,
        "retention_mode": retention_mode,
        "max_shares": shares.MAX_SHARES_PER_JOB,
        "errors": errors or [],
        "email_error": next(
            (e["message"] for e in errors or [] if e["field_id"] == "share-email"), ""
        ),
        "role_error": next(
            (e["message"] for e in errors or [] if e["field_id"] == "share-role"), ""
        ),
        "typed": typed,
        "typed_role": typed_role,
        "role_words": shares.ROLE_WORDS,
    }
    return render(request, "share.html", context, status_code=status_code)


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
            row = shares.share(session, job, user.email, email, role)
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
        "Saved. Nothing was sent: copy the document's address below and give it to the person.",
        undo=False,
    )
    return RedirectResponse(f"/jobs/{owned_id}/share", status_code=303)


@router.post("/jobs/{job_id}/share/{share_id}/revoke", dependencies=[Depends(require_csrf)])
def revoke_share(request: Request, job_id: str, share_id: str, user: UserDep) -> Response:
    """Take a person's access away. It stops on their next request."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, OWNER, lock=True)
        owned_id = job.id
        removed = shares.revoke(session, owned_id, share_id)
    if not removed:
        raise PageError(404, *NOT_FOUND)
    log_event(logger, "share_revoked", job_id=owned_id)
    flash(request, "Access removed. They cannot open this document any more.", undo=False)
    return RedirectResponse(f"/jobs/{owned_id}/share", status_code=303)
