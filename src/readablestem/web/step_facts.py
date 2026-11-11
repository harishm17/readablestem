"""A job's step facts in the database: every request it sent to a model and how long each
of its stages took (`job_calls`, `job_stages`; the facts are `pipeline.step_facts`).

The worker keeps them in the transaction that stores the job's result. They are replaced
when the job is converted again and deleted with it. What they hold is codes, counts,
seconds and dollars, so they may be logged and shown to an administrator; they never hold
document text or a person's id.

`measured` gives the jobs the step report is made from (`ops.step_report`).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from readablestem.core.types import CallFact, StageFact, StepFacts
from readablestem.ops import step_report
from readablestem.ops.step_report import Measured
from readablestem.web.db import Job, JobCall, JobStage, JobStatus
from readablestem.web.logsafe import log_event

logger = logging.getLogger(__name__)

_CALL_FIELDS = tuple(CallFact.model_fields)


def keep(session: Session, job_id: str, facts: StepFacts) -> None:
    """Make these the job's facts, in place of any kept for it."""
    forget(session, [job_id])
    session.add_all(
        JobCall(job_id=job_id, seq=seq, **call.model_dump()) for seq, call in enumerate(facts.calls)
    )
    session.add_all(
        JobStage(job_id=job_id, seq=seq, stage=fact.stage, seconds=fact.seconds)
        for seq, fact in enumerate(facts.stages)
    )


def forget(session: Session, job_ids: Sequence[str]) -> None:
    session.execute(delete(JobCall).where(JobCall.job_id.in_(job_ids)))
    session.execute(delete(JobStage).where(JobStage.job_id.in_(job_ids)))


def kept(session: Session, job_ids: Sequence[str]) -> dict[str, StepFacts]:
    """The facts of each of these jobs that has any."""
    found: dict[str, StepFacts] = defaultdict(StepFacts)
    if not job_ids:
        return {}
    for row in session.scalars(
        select(JobCall).where(JobCall.job_id.in_(job_ids)).order_by(JobCall.job_id, JobCall.seq)
    ):
        found[row.job_id].calls.append(
            CallFact(**{name: getattr(row, name) for name in _CALL_FIELDS})
        )
    for stage in session.scalars(
        select(JobStage).where(JobStage.job_id.in_(job_ids)).order_by(JobStage.job_id, JobStage.seq)
    ):
        found[stage.job_id].stages.append(StageFact(stage=stage.stage, seconds=stage.seconds))
    return dict(found)


def measured(
    session: Session,
    *,
    job_ids: Sequence[str] | None = None,
    since: datetime | None = None,
    last: int | None = None,
) -> list[Measured]:
    """The finished jobs that have facts, newest first: these ids, or those finished since
    a time, at most `last` of them. The sample document is never one of them."""
    query = select(Job).where(Job.status == JobStatus.DONE, Job.is_sample.is_(False))
    if job_ids is not None:
        query = query.where(Job.id.in_(job_ids))
    if since is not None:
        query = query.where(Job.finished_at >= since)
    query = query.order_by(Job.finished_at.desc(), Job.id)
    if last is not None:
        query = query.limit(last)
    jobs = list(session.scalars(query))
    facts = kept(session, [job.id for job in jobs])
    return [
        Measured(
            pages=int(job.page_count or 0),
            facts=facts[job.id],
            settled_usd=float(job.cost_usd or 0.0),
            counted_calls=int(job.model_calls or 0),
            wall_s=(job.finished_at - job.started_at).total_seconds()
            if job.started_at and job.finished_at
            else None,
        )
        for job in jobs
        if job.id in facts
    ]


def log_job(job_id: str, pages: int, facts: StepFacts) -> None:
    """The job's facts as log lines: one `job_steps` line for each step and model, and one
    `job_stages` line with the seconds of each stage."""
    made = step_report.report([Measured(pages=pages, facts=facts)])
    for row in made.steps:
        log_event(
            logger,
            "job_steps",
            job_id=job_id,
            step=row.step,
            model=row.model,
            calls=row.calls,
            failed=row.failed,
            input_tokens=round(row.input_per_page * pages),
            cached_input_tokens=round(row.cached_per_page * pages),
            output_tokens=round(row.output_per_page * pages),
            cache_read=None if row.cache_read is None else round(row.cache_read, 3),
            call_s_p50=round(row.call_s_p50, 2),
            call_s_p95=round(row.call_s_p95, 2),
            cost_usd=round(row.usd, 6),
        )
    log_event(
        logger,
        "job_stages",
        job_id=job_id,
        pages=pages,
        **{f"{fact.stage}_s": round(fact.seconds, 2) for fact in facts.stages},
    )
