"""A sample document to practise on before risking a real file (T3-9).

An instructor deciding whether to upload a course PDF wants to see the review screen first:
what the tool proposes, what it asks, what a decision looks like. The sample is a real
conversion made once, ahead of time (`static/demo/`: OpenStax *Physics* 2.3 "Position vs.
Time Graphs", CC BY 4.0, its source PDF and its stored result, 13 pages, converted with
the real models), put in the person's list as a finished document of their own.

What makes it safe and free:

- **No model call, for anyone.** The result is the recorded one. The pictures cut from the
  pages (the crops the review screen shows) are made from the source PDF by the layout
  code, which reads no model; they are made once per process and shared.
- **Outside every limit.** It uses none of the daily pages (no `quota_days` row), none of
  the daily spend (cost 0, no `spend_days` row), and takes no place from the documents a
  person may wait on or keep (`Job.is_sample`, `intake.live_job_count`). A person has at
  most one at a time; opening it again returns the same one.
- **Outside the numbers.** It is created already counted (`aggregated`), writes no usage
  events (`events` skips a sample) and is not in the review counts of a deleted document
  (`ops.retention`), so the beta's figures are about real documents.
- **Theirs to keep, change and delete.** Decisions made on it are saved like any other,
  and it is deleted by the same clock (a day after the last activity) or by "Delete now".

The source and licence are on the job page (`_job_sample.html`) and in `THIRD_PARTY_NOTICES.md`.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.core.types import DocumentResult
from a11y_stem.web import retention
from a11y_stem.web.db import Blob, Job, JobStatus, session_scope, utcnow

logger = logging.getLogger(__name__)

DEMO_DIR = Path(__file__).resolve().parent / "static" / "demo"
SAMPLE_FILENAME = "sample-openstax-physics-2-3.pdf"


@dataclass(frozen=True)
class Sample:
    """The recorded document, ready to be copied into a person's list."""

    result: DocumentResult
    html: str
    pdf: bytes
    crops: dict[str, bytes]
    title: str
    source_url: str
    licence: str
    licence_url: str
    authors: list[str]


_LOCK = threading.Lock()
_CACHE: list[Sample] = []


def available() -> bool:
    """The sample's files are in this build."""
    return (DEMO_DIR / "report.json").is_file() and (DEMO_DIR / "source.pdf").is_file()


def load() -> Sample:
    """The recorded sample (read and cropped on first use, then kept for the process)."""
    with _LOCK:
        if not _CACHE:
            _CACHE.append(_build())
        return _CACHE[0]


def forget() -> None:
    """Drop the cached sample (tests)."""
    with _LOCK:
        _CACHE.clear()


def _build() -> Sample:
    from a11y_stem.pipeline.run import recrop
    from a11y_stem.web.review import rerender_html

    report = json.loads((DEMO_DIR / "report.json").read_text(encoding="utf-8"))
    meta = report["demo"]
    result = DocumentResult.model_validate({**report["result"], "html": ""})
    pdf = (DEMO_DIR / "source.pdf").read_bytes()
    # The pictures are cut again exactly as a conversion cuts them (an unconverted equation
    # sharp for the size of the file's text), and the file is written by today's assembler:
    # the sample shows the current output, not the output of the day it was recorded.
    crops = recrop(pdf, result.elements)
    html = rerender_html(result, crops)
    result.html = ""
    return Sample(
        result=result,
        html=html,
        pdf=pdf,
        crops=crops,
        title=str(meta.get("page_title") or result.title or "Sample document"),
        source_url=str(meta.get("page_url") or ""),
        licence=str(meta.get("license_name") or ""),
        licence_url=str(meta.get("license_url") or ""),
        authors=[str(a) for a in meta.get("authors") or []],
    )


def live_sample(session: Session, user_id: str, now: datetime | None = None) -> str | None:
    """The id of the person's sample document, if they have one that is not past its
    deletion time (only the id is read: the list reads no stored result)."""
    moment = now or utcnow()
    return session.scalars(
        select(Job.id)
        .where(Job.user_id == user_id, Job.is_sample.is_(True), Job.retain_until > moment)
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()


def open_for(factory: sessionmaker[Session], settings: Settings, user_id: str) -> str:
    """The id of the person's sample document: the one they have, else a new one made now
    from the recorded result (no model, no pages, no spend)."""
    sample = load()  # outside the transaction: the first call crops the pages
    now = utcnow()
    with session_scope(factory) as session:
        existing = live_sample(session, user_id, now)
        if existing is not None:
            return existing
        job = Job(
            user_id=user_id,
            status=JobStatus.DONE,
            filename=SAMPLE_FILENAME,
            page_count=sample.result.stats.pages,
            pages_done=sample.result.stats.pages,
            opt_in_retention=False,
            retention_mode="day",
            retain_until=retention.retain_until_at_upload(settings, "day", now),
            created_at=now,
            started_at=now,
            finished_at=now,
            attempts=1,
            cost_usd=0.0,
            model_calls=0,
            aggregated=True,  # counted nowhere: the beta's numbers are about real documents
            is_sample=True,
        )
        job.result_json = sample.result.model_dump(mode="json", exclude={"html"})
        job.html = sample.html
        session.add(job)
        session.flush()
        session.add(
            Blob(job_id=job.id, kind="upload", media_type="application/pdf", content=sample.pdf)
        )
        for element_id, png in sample.crops.items():
            session.add(
                Blob(
                    job_id=job.id,
                    kind="crop",
                    element_id=element_id,
                    media_type="image/png",
                    content=png,
                )
            )
        logger.info("sample_opened")
        return job.id
