"""Listen as a student: the transcript of what a screen reader says for a converted document.

`GET /jobs/{job_id}/listen` is for the owner and anyone the document was shared with (access
`view`, like the student preview), and `GET /demo/listen` is public, for the stored demo. Both
only read: no POST, nothing stored, nothing logged, and the transcript is made from the same
HTML the student gets (`web.listen`), kept in memory per job result version.
"""

from __future__ import annotations

import json
from functools import cache

from fastapi import APIRouter, Request
from fastapi.responses import Response

from readablestem.core.types import DocumentResult
from readablestem.web import documents, listen
from readablestem.web.db import session_scope
from readablestem.web.deps import UserDep, get_ctx, render
from readablestem.web.routes.jobs import current_html, job_access, require_done
from readablestem.web.routes.pages import DEMO_PAGE, STATIC_DIR
from readablestem.web.shares import VIEW

router = APIRouter()


@router.get("/jobs/{job_id}/listen")
def listen_page(request: Request, job_id: str, user: UserDep) -> Response:
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = job_access(session, job_id, user, VIEW)
        require_done(job)
        html = current_html(ctx, session, job).html or ""
        title = documents.display_title(
            (job.result_json or {}).get("title"), job.filename, job.title_override
        )
        result_json, version = job.result_json, job.result_version or 0

    def speeches() -> dict[str, str]:
        return listen.stored_speeches(DocumentResult.model_validate(result_json or {}))

    items = listen.transcript_for(job_id, version, html, speeches)
    return render(
        request,
        "listen.html",
        {
            "items": items,
            "title": title,
            "back_url": f"/jobs/{job_id}",
            "back_label": "Back to the results",
            "student_url": f"/jobs/{job_id}/view",
        },
    )


@cache
def _demo_items() -> list[listen.Item]:
    """The demo's transcript, from the stored demo file and the readings in its report."""
    html = DEMO_PAGE.read_text(encoding="utf-8")
    report = json.loads((STATIC_DIR / "demo" / "report.json").read_text(encoding="utf-8"))
    result = DocumentResult.model_validate(report["result"])
    return listen.build_transcript(html, listen.stored_speeches(result))


@router.get("/demo/listen")
def demo_listen(request: Request) -> Response:
    if not DEMO_PAGE.is_file():
        return render(request, "demo_pending.html", {"active": "demo"})
    return render(
        request,
        "listen.html",
        {
            "items": _demo_items(),
            "title": "OpenStax Physics 2.3, Position vs. Time Graphs (the demo)",
            "back_url": "/demo",
            "back_label": "Back to the demo",
            "student_url": "",
            "active": "demo",
        },
    )
