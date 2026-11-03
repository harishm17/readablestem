"""Combine the parts of a long source into one file (`pipeline.combine`), for one person.

A source over the page limit is converted in parts, each a document of its own
(`web.routes.parts`); the parts of one source share `Job.part_group` and say which pages they
hold in `Job.part_label`. This module finds the parts a person may join:

- only the person's own (`Job.user_id`), whatever group id is asked about;
- only finished ones (`JobStatus.DONE`);
- only ones still kept: a part past its `retain_until` counts as deleted even before the
  hourly purge removes it (as `owned_job` treats it), so a deleted part is never in the file
  and the file is not made from anything the retention promise says is gone;
- only ones whose label names as many pages as the part has (otherwise a page cannot be put
  back where it was in the source).

It reads, never writes: no retention clock is restarted, no row changes, so the route needs
no CSRF token (it is a GET like the other downloads, for the owner only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.core.types import DocumentResult
from readablestem.pipeline import combine
from readablestem.web import documents, ranges
from readablestem.web.db import Blob, Job, JobStatus, utcnow

# A part's file name is "<name> (pages 31-59).pdf" (`web.routes.parts.part_filename`).
_PART_SUFFIX = re.compile(r"\s*\(pages [^)]*\)\s*$")
DEFAULT_TITLE = "Combined document"


@dataclass(frozen=True)
class KeptPart:
    job: Job
    pages: tuple[int, ...]  # 0-based pages of the source this part holds


def kept_parts(session: Session, job: Job, now: datetime | None = None) -> list[KeptPart]:
    """The finished parts of `job`'s source that its owner still keeps, in page order.
    `job` must be a part; one that is not has none. A part with a decision still open is a
    draft and is held back, as "Download all" holds it back (`web.routes.bundle`). When two
    parts name a page in common (the same pages converted twice) the newest is kept and the
    older one left out, so the file never holds a page twice."""
    if not job.part_group:
        return []
    rows = session.scalars(
        select(Job)
        .where(
            Job.part_group == job.part_group,
            Job.user_id == job.user_id,  # never another person's, even in the same group
            Job.status == JobStatus.DONE,
            Job.retain_until > (now or utcnow()),
            Job.result_json.is_not(None),
        )
        .order_by(Job.created_at.desc(), Job.id)
    ).all()
    kept: list[KeptPart] = []
    taken: set[int] = set()
    for row in rows:  # newest first
        pages = ranges.pages_of_label(row.part_label)
        if pages is None or len(pages) != row.page_count or taken & set(pages):
            continue
        if documents.open_decisions((row.result_json or {}).get("fixes")):
            continue
        taken.update(pages)
        kept.append(KeptPart(row, pages))
    return sorted(kept, key=lambda k: k.pages[0])


def title_of(first: Job) -> str:
    """What the file is called: the title the instructor chose for the first part, else the
    source's file name without the part tag and extension."""
    if first.title_override and first.title_override.strip():
        return first.title_override.strip()
    stem = _PART_SUFFIX.sub("", first.filename.rpartition(".")[0] or first.filename)
    return " ".join(stem.split()) or DEFAULT_TITLE


def combined_html(
    session: Session, kept: list[KeptPart], *, math_alttext: bool | None = None
) -> tuple[str, str]:
    """(the one HTML file, its title) for `kept` parts. Raises `combine.CombineError`."""
    parts = []
    for item in kept:
        try:
            result = DocumentResult.model_validate(item.job.result_json or {})
        except ValidationError:
            raise combine.CombineError("unreadable_part") from None
        blobs = session.scalars(
            select(Blob).where(Blob.job_id == item.job.id, Blob.kind == "crop")
        ).all()
        crops = {b.element_id: b.content for b in blobs if b.element_id}
        parts.append(combine.Part(result, crops, item.pages))
    title = title_of(kept[0].job)
    contact = next((k.job.contact_line for k in kept if k.job.contact_line), None)
    return combine.combine(parts, title=title, contact=contact, math_alttext=math_alttext), title


def number_part_pages(job: Job, result: DocumentResult) -> bool:
    """For a part of a longer file: record which page of that file each page of the part is
    (`DocumentResult.source_pages`), so the part's page markers, like the combined file's,
    say "PDF page 31" for a page that has no printed label. Printed labels (the PDF's own,
    kept when the part was cut out, or a number read from the page) are not touched. True if
    this changed the result (the file written before is then out of date)."""
    pages = ranges.pages_of_label(job.part_label)
    if pages is None or result.source_pages == list(pages):
        return False
    result.source_pages = list(pages)
    return True
