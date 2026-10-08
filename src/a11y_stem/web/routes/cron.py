"""/internal/cron/aggregate-and-purge, called by Cloud Scheduler with X-Cron-Token.

Aggregation runs first so the content-free counts survive deletion, but deletion always
runs, even when aggregation fails (privacy beats statistics). A failed aggregation still
answers 500 so the scheduler and monitoring see it.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from a11y_stem.web.db import utcnow
from a11y_stem.web.deps import get_ctx
from a11y_stem.web.logsafe import log_event, log_exception

logger = logging.getLogger(__name__)
router = APIRouter()


def token_ok(expected: str | None, given: str | None) -> bool:
    if not expected or not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))


@router.post("/internal/cron/aggregate-and-purge")
def aggregate_and_purge(
    request: Request, x_cron_token: str | None = Header(default=None)
) -> Response:
    ctx = get_ctx(request)
    secret = ctx.settings.cron_token
    if not token_ok(secret.get_secret_value() if secret else None, x_cron_token):
        log_event(logger, "cron_refused", logging.WARNING)
        return JSONResponse({"status": "forbidden"}, status_code=403)
    from a11y_stem.ops import aggregate, retention

    now = utcnow()
    aggregated: int | None = None
    try:
        aggregated = aggregate.aggregate_finished(ctx.factory, now)
    except Exception as exc:
        log_exception(logger, "aggregate_failed", exc)
    purged = retention.purge_expired(ctx.factory, now)
    body = {
        "status": "ok" if aggregated is not None else "aggregate_failed",
        "jobs_aggregated": aggregated,
        "jobs_deleted": purged.jobs_deleted,
        "blobs_deleted": purged.blobs_deleted,
        "unaggregated_deleted": purged.unaggregated_deleted,
    }
    log_event(
        logger,
        "cron_done",
        jobs_aggregated=aggregated,
        jobs_deleted=purged.jobs_deleted,
        blobs_deleted=purged.blobs_deleted,
        unaggregated_deleted=purged.unaggregated_deleted,
    )
    return JSONResponse(body, status_code=200 if aggregated is not None else 500)
