"""Whether a finished document may leave the product, and how: `export_state` (SW21).

Every way a document's file leaves (the HTML, Canvas, Common Cartridge, Word and EPUB
downloads, "Download all" and the batch and course zips, "Combine parts", and the student
preview) asks `export_state` before it builds anything. The answer is one of:

- `ready`: finished, with no decision open. The file goes as it is.
- `draft`: finished, with decisions still open. The file goes only after the person has
  said so on the draft page (`/jobs/{id}/draft`): that page's form, a POST with the CSRF
  token, mints a confirm token (`mint_token`) that is signed with the session's own secret,
  lives `TOKEN_S` seconds, and names the job and its output version (`Job.result_version`,
  bumped by every review write). A download address typed or bookmarked without one is sent
  to the draft page (`DraftConfirm`), so the question cannot be skipped. The file's name
  ends in `-draft` (`file_name`), and the file itself says so first
  (`pipeline.assemble.mark_draft`). A person the document was shared with to view only is
  never given a draft: they cannot make the decisions, and the route refuses.
  The zips and "Combine parts" never take a draft: they hold it back and say how many.
- `not available`: not finished (or nothing written yet). Nothing leaves.

"Decisions still open" is read in one place, `open_required`: the count of the summary of
what was checked (`pipeline.checked.open_required`, which is `core.triage.review_load`), so
the draft gate, the job's state and the student note never disagree (SW7).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from starlette.requests import Request

from readablestem.pipeline.assemble import mark_draft
from readablestem.web import documents, shares
from readablestem.web.db import Job, JobStatus
from readablestem.web.deps import SESSION_CSRF, PageError, csrf_token

READY = "ready"
DRAFT = "draft"
NOT_AVAILABLE = "not available"

# How long a confirm from the draft page lets its download through.
TOKEN_S = 600
TOKEN_PARAM = "draft"
DRAFT_SUFFIX = "-draft"

# The single downloads the draft page can send a person on to, by file type.
FORMATS = ("html", "canvas", "cartridge", "docx", "epub")
_PATHS = {
    "html": "/download",
    "canvas": "/download/canvas",
    "cartridge": "/download/cartridge",
    "docx": "/download.docx",
    "epub": "/download.epub",
}

NOT_FINISHED = (
    "Not ready yet",
    "This document has not finished converting, so there is nothing to show yet.",
)
DRAFT_NOT_FOR_VIEWERS = (
    "This document is still a draft",
    "Decisions are still open in it, and only the owner or a reviewer can make them. You can "
    "read it here; a file to download is offered once the decisions are made.",
)


@dataclass(frozen=True)
class ExportState:
    key: str  # READY, DRAFT or NOT_AVAILABLE
    open_decisions: int = 0

    @property
    def draft(self) -> bool:
        return self.key == DRAFT


class DraftConfirm(Exception):
    """A draft was asked for without a confirm: the app answers with a redirect to `url`, the
    draft page for this file type."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


def open_required(job: Job) -> int:
    """The decisions still open that make a file a draft: the summary's own count
    (`pipeline.checked.open_required`), read through `documents.open_decisions`."""
    return documents.open_decisions((job.result_json or {}).get("fixes"))


def export_state(job: Job) -> ExportState:
    """What may leave for this job: `ready`, `draft` (with the open count) or `not
    available`. Who is asking is the route's question (`check`): this is the document's."""
    if job.status != JobStatus.DONE or (job.html is None and not job.html_stale):
        return ExportState(NOT_AVAILABLE)
    still_open = open_required(job)
    return ExportState(DRAFT, still_open) if still_open else ExportState(READY)


def check(request: Request, job: Job, role: str, file_type: str, **query: str) -> ExportState:
    """The gate of a single download, before the file is built. Raises the "not ready" page,
    the refusal for a viewer, or `DraftConfirm` for a draft without a valid confirm token;
    otherwise returns the state (a draft with a valid token, or ready). `query` is what the
    draft page passes back to the download (the Canvas course number)."""
    state = export_state(job)
    if state.key == NOT_AVAILABLE:
        raise PageError(409, *NOT_FINISHED, context={"job_id": job.id})
    if not state.draft:
        return state
    if not may_take_draft(role):
        raise PageError(403, *DRAFT_NOT_FOR_VIEWERS, context={"job_id": job.id})
    if not token_valid(request, job, request.query_params.get(TOKEN_PARAM, "")):
        raise DraftConfirm(confirm_url(job.id, file_type, **query))
    return state


def may_take_draft(role: str) -> bool:
    """The owner and a reviewer may take a draft; a person who can only view may not."""
    return shares.RANK.get(role, 0) >= shares.RANK[shares.REVIEW]


def confirm_url(job_id: str, file_type: str, **query: str) -> str:
    params = {"format": file_type} if file_type != "html" else {}
    params |= {k: v for k, v in query.items() if v}
    return f"/jobs/{job_id}/draft" + (f"?{urlencode(params)}" if params else "")


def download_url(job_id: str, file_type: str, token: str = "", **query: str) -> str:
    params = {k: v for k, v in query.items() if v}
    if token:
        params[TOKEN_PARAM] = token
    return f"/jobs/{job_id}{_PATHS[file_type]}" + (f"?{urlencode(params)}" if params else "")


def _signature(key: str, job: Job, expires: int) -> str:
    message = f"draft:{job.id}:{job.result_version or 0}:{expires}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def mint_token(request: Request, job: Job, now: float | None = None) -> str:
    """A confirm for this job at its current output version, for this session only. The
    draft page's POST is the one caller."""
    key = csrf_token(request)
    expires = int(now if now is not None else time.time()) + TOKEN_S
    return f"{expires}.{_signature(key, job, expires)}"


def token_valid(request: Request, job: Job, token: str, now: float | None = None) -> bool:
    key = request.session.get(SESSION_CSRF)
    expires, _, signature = token.partition(".")
    if not isinstance(key, str) or not key or not expires.isdigit() or not signature:
        return False
    if int(expires) < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(_signature(key, job, int(expires)), signature)


def file_name(filename: str, state: ExportState) -> str:
    """The name a file leaves under: a draft's ends in `-draft` ('notes-ab12cd.docx' ->
    'notes-ab12cd-draft.docx'); a ready file's is unchanged."""
    if not state.draft:
        return filename
    stem, dot, ending = filename.rpartition(".")
    return f"{stem}{DRAFT_SUFFIX}.{ending}" if dot else filename + DRAFT_SUFFIX


def marked(html: str, state: ExportState) -> str:
    """The HTML as it leaves: a draft carries its banner and its state (`mark_draft`)."""
    return mark_draft(html, state.open_decisions) if state.draft else html
