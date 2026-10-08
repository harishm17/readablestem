"""Builders for ops tests: a user, jobs in every state, and a result full of sentinel text.

`SENTINEL` is planted in every content field (element text, LaTeX, MathML, speech, alt
text, captions, headings, the filename, the user's email) and in category-code fields as
a sentence, which the aggregator must record as "other". Tests then assert the sentinel
appears nowhere in aggregate rows, logs or traces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.types import (
    Check,
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    ModelCall,
    Status,
)
from a11y_stem.web import db

SENTINEL = "quokka"  # lower case on purpose: codes are lower-cased before matching
SENTENCE = f"Secret {SENTINEL} sentence with spaces"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
FINISHED = datetime(2026, 10, 6, 22, 30, tzinfo=UTC)  # day 2026-10-06


def call(model: str, outcome: str = "ok", *, fallback: bool = False) -> ModelCall:
    return ModelCall(
        step="equation",
        model=model,
        prompt_version="equation-1a2b3c4d",
        input_tokens=800,
        output_tokens=60,
        cost_usd=0.001,
        latency_ms=900,
        outcome=outcome,  # type: ignore[arg-type]
        is_fallback=fallback,
    )


def sentinel_result() -> DocumentResult:
    """Five fixes whose expected counts are spelled out in `EXPECTED_DONE_COUNTS`."""
    equation = EquationFix(
        latex=rf"\frac{{{SENTINEL}}}{{b}}",
        mathml=f"<math><mi>{SENTINEL}</mi></math>",
        speech=f"{SENTINEL} over b",
    )
    fixes = [
        Fix(  # escalated: luna answered, sol re-asked (not a fallback)
            element_id="p1-e1",
            kind=Kind.EQUATION,
            status=Status.NEEDS_REVIEW,
            confidence=0.6,
            equation=equation,
            checks=[
                Check(name="latex.allowed_commands", passed=False, code="unknown_command"),
                Check(name="mathml.well_formed", passed=True),
            ],
            calls=[call(CHEAP_MODEL), call(MAIN_MODEL)],
        ),
        Fix(  # luna timed out, sol answered as the fallback
            element_id="p1-e2",
            kind=Kind.EQUATION,
            status=Status.FAILED,
            reason="low_confidence",
            calls=[call(CHEAP_MODEL, "timeout"), call(MAIN_MODEL, fallback=True)],
        ),
        Fix(  # a check code that is really a sentence: must be stored as "other"
            element_id="p2-e3",
            kind=Kind.EQUATION,
            status=Status.NEEDS_REVIEW,
            equation=equation,
            checks=[Check(name="latex.allowed_commands", passed=False, code=SENTENCE)],
            reason=None,
        ),
        Fix(
            element_id="p2-e1",
            kind=Kind.FIGURE,
            status=Status.ACCEPTED,
            confidence=0.95,
            figure=FigureFix(figure_type="chart", alt=f"A chart of {SENTINEL} sightings"),
            calls=[call(MAIN_MODEL)],
        ),
        Fix(
            element_id="p1-e0",
            kind=Kind.HEADING,
            status=Status.ACCEPTED,
            heading=HeadingFix(level=2, text=f"Chapter on {SENTINEL}"),
        ),
    ]
    elements = [
        Element(
            id=fix.element_id,
            kind=fix.kind,
            page=0,
            bbox=(0, 0, 10, 10),
            order=i,
            text=f"{SENTINEL} source text",
            caption=f"Figure 1 {SENTINEL}" if fix.kind == Kind.FIGURE else None,
        )
        for i, fix in enumerate(fixes)
    ]
    return DocumentResult(
        pipeline_version="0.2.0",
        prompt_versions={"equation": "equation-1a2b3c4d"},
        title=f"Notes on {SENTINEL}",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=2, cost_usd=0.006, latency_ms=5000, model_calls=6),
    )


EXPECTED_DONE_COUNTS = {
    ("job", "status:done"): 1,
    ("job", "pages"): 2,
    ("equation", "status:needs_review"): 2,
    ("equation", "status:failed"): 1,
    ("equation", "check:latex.allowed_commands:unknown_command"): 1,
    ("equation", "check:latex.allowed_commands:other"): 1,
    ("equation", "reason:low_confidence"): 1,
    ("equation", "call:gpt-6-luna:outcome:ok"): 1,
    ("equation", "call:gpt-6-luna:outcome:timeout"): 1,
    ("equation", "call:gpt-6.1-sol:outcome:ok"): 2,
    ("equation", "call:fallback"): 1,
    ("equation", "call:escalated"): 1,
    ("figure", "status:accepted"): 1,
    ("figure", "call:gpt-6.1-sol:outcome:ok"): 1,
    ("heading", "status:accepted"): 1,
}


def add_user(session: Session, email: str = f"{SENTINEL}@example.org") -> db.User:
    user = db.User(email=email, is_allowed=True)
    session.add(user)
    session.flush()
    return user


def add_job(
    session: Session,
    user: db.User,
    *,
    status: str = db.JobStatus.DONE,
    finished_at: datetime | None = FINISHED,
    retain_until: datetime = NOW + timedelta(hours=1),
    result: DocumentResult | None = None,
    error_code: str | None = None,
    page_count: int = 2,
    aggregated: bool = False,
    blobs: int = 0,
    edits: list[tuple[str, str]] | None = None,
) -> str:
    """Insert a job (plus `blobs` crops and review `edits` as (element_id, action))."""
    job = db.Job(
        user_id=user.id,
        status=status,
        filename=f"{SENTINEL} lecture notes.pdf",
        page_count=page_count,
        retain_until=retain_until,
        created_at=(finished_at or NOW) - timedelta(minutes=5),
        finished_at=finished_at,
        error_code=error_code,
        result_json=None if result is None else result.model_dump(mode="json", exclude={"html"}),
        html=None if result is None else f"<p>{SENTINEL}</p>",
        aggregated=aggregated,
    )
    session.add(job)
    session.flush()
    for i in range(blobs):
        session.add(
            db.Blob(
                job_id=job.id,
                kind="crop",
                element_id=f"p1-e{i}",
                media_type="image/png",
                content=SENTINEL.encode(),
            )
        )
    for element_id, action in edits or []:
        session.add(
            db.ReviewEdit(
                job_id=job.id,
                user_id=user.id,
                element_id=element_id,
                action=action,
                before={"latex": SENTINEL},
                after={"latex": SENTINEL},
            )
        )
    session.flush()
    return job.id


REVIEW_STATUS = {"accept": Status.ACCEPTED, "edit": Status.EDITED, "reject": Status.REJECTED}


def review(
    session: Session,
    job_id: str,
    element_id: str,
    action: str,
    *,
    checks: list[Check] | None = None,
    at: datetime | None = None,
) -> None:
    """What the web app's review does (wp/5-web web/review.py `apply_review` and
    routes/review.py): rewrite the fix in the stored result in place (status; an edit also
    replaces the checks) and store a ReviewEdit with before/after snapshots."""
    job = session.get(db.Job, job_id)
    assert job is not None and job.result_json is not None
    result = DocumentResult.model_validate(job.result_json)
    fix = result.fix_for(element_id)
    assert fix is not None

    def snapshot() -> dict:
        return fix.model_dump(mode="json", include={"status", "equation", "figure", "heading"})

    before = snapshot()
    if action == "edit":
        fix.checks = checks or []
    fix.status = REVIEW_STATUS[action]
    job.result_json = result.model_dump(mode="json", exclude={"html"})
    session.add(
        db.ReviewEdit(
            job_id=job_id,
            user_id=job.user_id,
            element_id=element_id,
            action=action,
            before=before,
            after=snapshot(),
            created_at=at or NOW,
        )
    )
    session.flush()


def aggregate_rows(session: Session) -> dict[tuple[str, str], int]:
    """Every (step, category) -> count, summed over days."""
    totals: dict[tuple[str, str], int] = {}
    for row in session.query(db.ErrorAggregate).all():
        totals[(row.step, row.category)] = totals.get((row.step, row.category), 0) + row.count
    return totals
