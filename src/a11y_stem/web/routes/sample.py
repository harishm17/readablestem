"""Open the sample document (T3-9, `web.sample`).

`POST /sample` puts a recorded, already converted document in the signed-in person's list
(or returns the one they have) and sends them to its page, where the review screen is one
button away. It is a POST with the token like every action that creates something, rate
limited, and cheap: no model call, no pages, no spend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response

from a11y_stem.web import sample
from a11y_stem.web.deps import PageError, UserDep, get_ctx, limited, require_csrf
from a11y_stem.web.logsafe import log_event

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sample", dependencies=[limited("sample"), Depends(require_csrf)])
async def open_sample(request: Request, user: UserDep) -> Response:
    ctx = get_ctx(request)
    if not sample.available():
        raise PageError(
            404,
            "No sample document",
            "The sample document is not part of this installation.",
        )
    job_id = await run_in_threadpool(sample.open_for, ctx.factory, ctx.settings, user.id)
    log_event(logger, "sample_requested", job_id=job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
