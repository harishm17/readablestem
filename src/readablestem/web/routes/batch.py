"""The batch page: what became of the files of one upload, live (`GET /app/batch/{id}`).

An upload of several files, or of a zip of documents (`web.batch_intake`), is answered by a
redirect to this page (`routes.jobs._results_redirect`; `/app/upload/{id}` is the old address and
redirects here). The outcome of each file is stored for an hour under the person
(`web.upload_result`); this page shows each document with its status as it is now, one row per
file in the order they were sent:

- documents waiting or converting, with their progress; finished ones with "N decisions to make"
  or "Ready to download", and the link that is the next step;
- large files kept for choosing their pages, and files that were not uploaded with the reason;
- a summary line in a polite live region (`#batch-summary`, `role="status"`), which says the
  counts again whenever they change, so a screen reader hears "3 of 5 ready to download" without
  moving;
- "Download all" (`/app/batch/{id}/download`): one zip of this batch's finished documents with no
  decision still open, offered once nothing is waiting or converting.

While anything is unfinished, htmx polls this address with the version of what it last drew
(`documents.list_version`); an unchanged batch is a 204 and costs one small query. The poll swaps
the rows and the download block and updates the live region in place (out of band: the region
itself is never replaced). Without JavaScript the page is a plain list: reload it.

Owner only: someone else's id, a stale one and a malformed one all answer the same 404.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from readablestem.web import documents, upload_result
from readablestem.web.database import as_utc
from readablestem.web.db import Job, JobStatus, session_scope, utcnow
from readablestem.web.deps import JOB_ID, PageError, UserDep, conversions_closed, get_ctx, render
from readablestem.web.routes.bundle import _zip_response
from readablestem.web.routes.jobs import (
    UPLOAD_RESULT_GONE,
    Placed,
    upload_error_message,
)

router = APIRouter()


def _batch(request: Request, result_id: str, user) -> tuple[list[Placed], list[str]]:
    """The stored outcomes of this person's upload and the ids of its jobs; the 404 otherwise."""
    if not JOB_ID.match(result_id):
        raise PageError(404, *UPLOAD_RESULT_GONE)
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        items = upload_result.load(session, user.id, result_id)
    if items is None:
        raise PageError(404, *UPLOAD_RESULT_GONE)
    placed = [Placed(**item) for item in items]
    return placed, [item.job_id for item in placed if item.job_id]


def _word(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary(rows: list[dict[str, Any]], kept: int, refused: int) -> str:
    """The batch in one sentence: "5 documents: 2 ready to download, 1 needs your decisions, 1
    converting, 1 waiting." Counts only, no names."""
    states = [r["state"] for r in rows]
    parts = []
    ready = states.count("ready")
    decide = states.count("decide")
    if ready:
        parts.append(f"{ready} ready to download")
    if decide:
        parts.append(f"{decide} {'needs' if decide == 1 else 'need'} your decisions")
    for key, label in (
        ("converting", "converting"),
        ("waiting", "waiting"),
        ("stopped", "stopped"),
    ):
        if states.count(key):
            parts.append(f"{states.count(key)} {label}")
    if states.count("deleted"):
        parts.append(f"{states.count('deleted')} deleted")
    text = _word(len(rows), "document", "documents")
    text += ": " + ", ".join(parts) + "." if parts else "."
    extra = []
    if kept:
        extra.append(f"{_word(kept, 'file waits', 'files wait')} for you to choose pages")
    if refused:
        extra.append(f"{_word(refused, 'file was', 'files were')} not uploaded")
    return text + (" " + "; ".join(extra).capitalize() + "." if extra else "")


def _state(row: documents.DocumentRow | None) -> str:
    if row is None:
        return "deleted"
    if row.status == JobStatus.QUEUED:
        return "waiting"
    if row.status == JobStatus.RUNNING:
        return "converting"
    if row.status == JobStatus.FAILED:
        return "stopped"
    return "decide" if row.open_decisions else "ready"


def _elapsed(minutes: int) -> str:
    """How long ago the batch was uploaded, in words: "less than a minute", "4 minutes",
    "1 hour 5 minutes". The time since the upload, not an estimate of what is left."""
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return _word(minutes, "minute", "minutes")
    hours, rest = divmod(minutes, 60)
    text = _word(hours, "hour", "hours")
    return text + (f" {_word(rest, 'minute', 'minutes')}" if rest else "")


def _next_step(rows: list[dict[str, Any]]) -> dict[str, str]:
    """What to do next to finish the batch: wait (nothing yet), review the documents that
    need decisions (the first one), look at the stopped ones, or download. `href` and `label`
    are set when there is a link."""
    states = [r["state"] for r in rows]
    if any(s in {"waiting", "converting"} for s in states):
        return {"text": "Nothing to do yet: you can leave, and documents keep converting."}
    decide = [r for r in rows if r["state"] == "decide"]
    if decide:
        many = len(decide) > 1
        return {
            "text": f"{_word(len(decide), 'document needs', 'documents need')} your decisions "
            "before sharing.",
            "href": f"/jobs/{decide[0]['id']}/review",
            "label": "Review the first one" if many else "Review it",
        }
    stopped = states.count("stopped")
    if stopped:
        return {"text": f"{_word(stopped, 'document', 'documents')} stopped. Open it to see why."}
    if states.count("ready"):
        return {"text": "Every document is ready."}
    return {}


def _context(request: Request, result_id: str, user) -> dict[str, Any] | None:
    """Everything the page draws; None when the batch is unchanged since `?v=`."""
    ctx = get_ctx(request)
    placed, ids = _batch(request, result_id, user)
    closed = conversions_closed(ctx, user) is not None
    now = utcnow()
    seen = request.query_params.get("v", "")
    with session_scope(ctx.factory) as session:
        version = documents.list_version(
            session, user.id, now=now, budget_closed=closed, job_ids=ids
        )
        began = as_utc(
            session.scalar(select(func.min(Job.created_at)).where(Job.id.in_(ids))) if ids else None
        )
        minutes = int((now - began).total_seconds() // 60) if began else 0
        # The minute is part of the version, so a batch still converting is drawn again once a
        # minute and "Started 4 minutes ago" is never stale (a finished one is not polled).
        version = hashlib.sha256(f"{version}.{minutes}".encode()).hexdigest()[:16]
        if seen and seen == version and request.headers.get("HX-Request") == "true":
            return None
        listing = documents.documents(session, user.id, now=now, budget_closed=closed, job_ids=ids)
    by_id = {row.id: row for row in listing.rows}
    titles = [r.title for r in listing.rows]
    rows: list[dict[str, Any]] = []
    for item in placed:
        if not item.job_id:
            continue
        row = by_id.get(item.job_id)
        name = row.title if row else item.filename
        # Files that came out with the same title (a slide deck's every part, say) are told
        # apart by their file names: the file name leads and the title follows it.
        same = bool(row) and titles.count(name) > 1 and name != item.filename
        rows.append(
            {
                "id": item.job_id,
                "name": item.filename if same else name,
                "also": name if same else (item.filename if name != item.filename else ""),
                "pages": item.pages,
                "label": row.label if row else "Deleted",
                "badge": row.badge if row else "failed",
                "state": _state(row),
                "action": row.action if row else None,
            }
        )
    choose = [i for i in placed if i.pending_id]
    refused = [
        {
            "message": upload_error_message(
                i.code,
                ctx,
                i.filename,
                i.pages or None,
                i.left,
                office=i.office,
                tier=user.access_tier,
            )
        }
        for i in placed
        if i.code
    ]
    unfinished = any(r["state"] in {"waiting", "converting"} for r in rows)
    ready = sum(r["state"] == "ready" for r in rows)
    return {
        "elapsed": _elapsed(minutes) if began else "",
        "next_step": {"text": "", "href": "", "label": "", **_next_step(rows)},
        "result_id": result_id,
        "rows": rows,
        "choose": [{"id": i.pending_id, "filename": i.filename, "pages": i.pages} for i in choose],
        "refused": refused,
        "received": len(rows) + len(choose),  # a large file kept for its pages is uploaded
        "total": len(placed),
        "summary": summary(rows, len(choose), len(refused)),
        "unfinished": unfinished,
        "ready": ready,
        "held_back": sum(r["state"] == "decide" for r in rows),
        "version": version,
        "active": "app",
    }


@router.get("/app/batch/{result_id}")
def batch_page(request: Request, result_id: str, user: UserDep) -> Response:
    """The batch page, or, to htmx's poll, just its rows (204 while nothing changed)."""
    context = _context(request, result_id, user)
    if context is None:
        return Response(status_code=204)
    if request.headers.get("HX-Request") == "true":
        return render(request, "_batch_rows.html", {**context, "oob": True})
    return render(request, "batch.html", context)


@router.get("/app/batch/{result_id}/download")
def batch_download(request: Request, result_id: str, user: UserDep) -> Response:
    """One zip of this batch's documents that are ready to share (`routes.bundle`): finished,
    no decision open. The same rules as "Download all", for this batch's documents only."""
    _, ids = _batch(request, result_id, user)
    if not ids:
        raise PageError(404, *UPLOAD_RESULT_GONE)
    return _zip_response(request, user, None, "batch", job_ids=ids)
