"""/internal/cron/aggregate-and-purge, called with X-Cron-Token by a scheduler (Cloud Scheduler
on the Cloud Run path). Production's Railway cron service runs `readablestem ops cron` instead
and does not call it.

Aggregation runs first so the content-free counts survive deletion, but deletion always
runs, even when aggregation fails (privacy beats statistics). A failed aggregation still
answers 500 so the scheduler and monitoring see it.

Then, at most once per UTC day, the Decisions API drift canary (`ops.canary`) and the
Claude second-check canary (`ops.claude_canary`). Each is fail-open: whatever happens to
it, the response is the aggregate-and-purge result. Its status goes in the `cron_done`
line; drift logs its own ERROR line.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from readablestem.web.db import utcnow
from readablestem.web.deps import get_ctx, limited
from readablestem.web.logsafe import log_event, log_exception

logger = logging.getLogger(__name__)
router = APIRouter()


def token_ok(expected: str | None, given: str | None) -> bool:
    if not expected or not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))


@router.post("/internal/cron/aggregate-and-purge", dependencies=[limited("cron")])
def aggregate_and_purge(
    request: Request, x_cron_token: str | None = Header(default=None)
) -> Response:
    ctx = get_ctx(request)
    secret = ctx.settings.cron_token
    if not token_ok(secret.get_secret_value() if secret else None, x_cron_token):
        log_event(logger, "cron_refused", logging.WARNING)
        return JSONResponse({"status": "forbidden"}, status_code=403)
    from readablestem.ops import aggregate, retention

    now = utcnow()
    aggregated: int | None = None
    try:
        aggregated = aggregate.aggregate_finished(ctx.factory, now)
    except Exception as exc:
        log_exception(logger, "aggregate_failed", exc)
    purged = retention.purge_expired(ctx.factory, now)
    from readablestem.ops import canary

    canary_status = canary.maybe_run_daily(ctx.factory, ctx.settings, ctx.guard, now)
    from readablestem.ops import claude_canary

    claude_status = claude_canary.maybe_run_daily(ctx.factory, ctx.settings, ctx.guard, now)
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
        decisions_canary=canary_status,
        claude_canary=claude_status,
    )
    return JSONResponse(body, status_code=200 if aggregated is not None else 500)
