"""My documents as a to-do list (UX review BW5, F-414): what needs the person first, then
what is still working, then what is ready, each document under its own title. A row's words,
badge and next step are its state (`web.states`), and the group is the state's tone:

- "Needs you": finished with decisions still open, oldest first (it is a queue of work, and
  the oldest is deleted first), then every stopped document, newest first, each with its
  reason in its label and its next step ("Try again" as a form). "Review next" opens the
  first one's review screen, which starts at its first open decision.
- "Working": waiting, converting or restarting, newest first.
- "Ready": finished with no decision open, newest first. A result that is only partly
  converted (a budget stop, scanned pages) says so in its label.

A document is led by its title (`document_title`, the one title every file and page of it
carries: the one the person chose, the PDF's title, its first heading, else the file name) with
the pages of a part, and the file name the person uploaded sits under it when it says something
else (SW14, F-368): the batch table leads the same way. Titles and file names are
document-derived: shown to their owner only, never logged.

A job with decisions open needs no stored result: the title and how many decisions are open
are columns of the job (`jobs.result_title`, `jobs.open_decisions`),
set whenever a result is stored (`list_facts`, called from the `Job.result_json` set
event in `web.db`): while anything is waiting or converting, htmx polls
`GET /app/documents` and every change of a page count rebuilds the list, which used to
load and validate the fixes of every finished document each time. `list_version` says
whether anything changed from the jobs' own columns alone, so an unchanged list costs one
small query and a 204. A finished job from before the columns existed (both NULL) is read
once from its result and the two facts are stored. A finished job with no decision open, or
with an outcome code, has its result read for its label (`states.load`): "Ready to share"
or "Ready" is the summary's (`pipeline.checked`), which is not stored (SW7).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.core import triage
from readablestem.core.types import Element, Fix
from readablestem.pipeline import assemble, checked, output_chars
from readablestem.web import states, titles
from readablestem.web.database import as_utc
from readablestem.web.db import Job, JobStatus


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


@dataclass
class DocumentRow:
    id: str
    title: str  # what the list shows: the document's title, else its file name
    filename: str
    pages: int
    pages_done: int
    status: str  # a JobStatus value
    created_at: datetime | None
    retain_until: datetime | None
    open_decisions: int = 0
    # The job's state (`web.states`): its label, badge, group and next step.
    state: states.State | None = None
    # One part of a larger file converted in parts (T3-3): the pages it holds, "31-59".
    part_label: str = ""
    is_sample: bool = False  # the recorded sample document (T3-9)
    exam_mode: bool = False  # converted as an exam or quiz (`web.exam`); the row marks it

    @property
    def name(self) -> str:
        """What the list leads with: the document's title, with the pages of a part."""
        return f"{self.title}, pages {self.part_label}" if self.part_label else self.title

    @property
    def file_note(self) -> str:
        """The file the person uploaded, shown under the title when it says something else:
        two files of a course can share a title, and the file name is what the person
        recognises. One line only when the title is the file name as written; a title made from
        its words ("a" for a.pdf) still shows the file, which says what kind it is. The
        recorded sample has no file of theirs."""
        if self.is_sample or self.title == self.filename:
            return ""
        return self.filename

    @property
    def label(self) -> str:
        """The state's label, with what it is waiting for or how far it has got: "Needs your
        decisions: 3 decisions to make", "Converting: 4 of 10 pages done"."""
        assert self.state is not None
        label = self.state.label
        if self.state.key == "converting":
            return f"{label}: {self.pages_done} of {plural(self.pages, 'page', 'pages')} done"
        if self.decide and self.open_decisions:
            return f"{label}: {plural(self.open_decisions, 'decision', 'decisions')} to make"
        return label

    @property
    def badge(self) -> str:
        """status-<badge> (app.css): the color only repeats the words."""
        return self.state.badge if self.state is not None else ""

    @property
    def group(self) -> str:
        return self.state.group if self.state is not None else states.WORK

    @property
    def action(self) -> states.Step | None:
        """The one next step offered in the row (the state's first)."""
        return self.state.primary if self.state is not None else None

    @property
    def decide(self) -> bool:
        """Finished, with decisions open: a row "Review next" can open."""
        return self.group == states.NEEDS and not (self.state and self.state.stopped)


@dataclass
class Group:
    key: str
    heading: str
    rows: list[DocumentRow] = field(default_factory=list)


@dataclass
class DocumentList:
    groups: list[Group]
    version: str

    @property
    def rows(self) -> list[DocumentRow]:
        return [row for group in self.groups for row in group.rows]

    def group(self, key: str) -> Group:
        return next(g for g in self.groups if g.key == key)

    @property
    def unfinished(self) -> int:
        return sum(row.status in {JobStatus.QUEUED, JobStatus.RUNNING} for row in self.rows)

    @property
    def review_next(self) -> DocumentRow | None:
        rows = [row for row in self.group(states.NEEDS).rows if row.decide]
        return rows[0] if rows else None

    @property
    def summary(self) -> str:
        """The groups in their order: '2 need your decisions (5 in all) · 1 stopped ·
        1 converting · 1 ready to download'."""
        decide = [row for row in self.group(states.NEEDS).rows if row.decide]
        parts = []
        if decide:
            total = sum(row.open_decisions for row in decide)
            verb = "needs" if len(decide) == 1 else "need"
            parts.append(
                f"{plural(len(decide), 'document', 'documents')} {verb} your decisions "
                f"({plural(total, 'decision', 'decisions')} in all)"
            )
        tones = [row.state.tone for row in self.rows if row.state is not None]
        for tone, words in (
            (states.FAILED, "stopped"),
            (states.WORKING, "converting"),
            (states.WAITING, "waiting"),
        ):
            if tones.count(tone):
                parts.append(f"{tones.count(tone)} {words}")
        ready = self.group(states.READY).rows
        if ready:
            parts.append(f"{len(ready)} ready to download")
        return " · ".join(parts)

    @property
    def page_title(self) -> str:
        """The tab title: "My documents (4 of 6 finished)" while some are not."""
        if not self.unfinished:
            return "My documents"
        total = len(self.rows)
        return f"My documents ({total - self.unfinished} of {total} finished)"


def display_title(title: object, filename: str, override: str | None = None) -> str:
    """What a list or a heading calls a document when only the job's columns are at hand
    (`document_title`): the title the instructor chose, else the stored title, else the file
    name."""
    return titles.effective_title(override, title, filename)


def document_title(job: Job) -> assemble.DocumentTitle:
    """The one title of a document and its source, for every place it is named: the student
    page's `<title>`, the Word, EPUB, Canvas and Cartridge files and their names, the download
    name, the list and the batch table, feedback, the record, Listen and the share page
    (SW14). Read from the job's columns, so no result is loaded."""
    found = assemble.document_title(
        job.title_override, "", (stored_title(job) or "",), job.filename
    )
    return assemble.DocumentTitle(output_chars.plain(found.text), found.source)


def stored_title(job: Job) -> str | None:
    """The title in the job's stored result, read from `jobs.result_title` so a route that
    needs only the title does not load the result. A finished job from before that column is
    read once and the column filled (the set event, as for the list)."""
    if job.result_title is None and job.status == JobStatus.DONE:
        job.result_json = job.result_json
    return job.result_title or None


def _fixes(fixes: object) -> list[Fix]:
    """A stored result's fixes; none when this version cannot read them."""
    if not isinstance(fixes, list):
        return []
    try:
        return [Fix.model_validate(f) for f in fixes if isinstance(f, dict)]
    except ValidationError:
        return []


def open_decisions(fixes: object) -> int:
    """Decisions still open in a stored result's fixes (a heading group is one): the
    summary's count (`pipeline.checked.open_required`), which the stored
    `jobs.open_decisions`, the draft gate and the job's state all read."""
    return checked.open_required(_fixes(fixes))


def list_facts(result: object, filename: str = "") -> tuple[int, str]:
    """(decisions still open, title) of a stored result, the two facts the list shows. The
    title is the one the document's page writes without the instructor's choice
    (`assemble.title_plan`, with the file name): "" when there is none, so that the stock
    title is never stored as if it were one."""
    if not isinstance(result, dict):
        return 0, ""
    fixes = _fixes(result.get("fixes"))
    raw = result.get("title")
    headings = []
    for element in result.get("elements") or []:
        if isinstance(element, dict) and element.get("kind") == "heading":
            try:
                headings.append(Element.model_validate(element))
            except ValidationError:
                continue
    plan = assemble.title_plan(
        headings, fixes, title=raw if isinstance(raw, str) else None, filename=filename
    )
    return checked.open_required(fixes), "" if plan.source == assemble.TITLE_STOCK else plan.text


def decided_by_you(fixes: object) -> int:
    """Decisions a reviewer made in a stored result's fixes (a heading group is one)."""
    return len(triage.units(f for f in _fixes(fixes) if f.decided_by_reviewer))


def _live(
    user_id: str,
    now: datetime,
    course_id: str | None = None,
    job_ids: Sequence[str] | None = None,
):
    # Expired jobs count as deleted before the purge removes them (`routes.jobs.job_access`).
    # With `course_id`, only the documents converted for that course (T3-4); with `job_ids`,
    # only those (the batch page, `routes/batch.py`: still only this person's).
    where = (Job.user_id == user_id, Job.retain_until > now)
    if course_id:
        where = (*where, Job.course_id == course_id)
    if job_ids is not None:
        where = (*where, Job.id.in_(list(job_ids)))
    return where


def list_version(
    session: Session,
    user_id: str,
    *,
    now: datetime,
    budget_closed: bool,
    course_id: str | None = None,
    job_ids: Sequence[str] | None = None,
) -> str:
    """Changes whenever the list would look different, from the jobs' columns alone."""
    rows = session.execute(
        select(Job.id, Job.status, Job.pages_done, Job.result_version, Job.error_code)
        .where(*_live(user_id, now, course_id, job_ids))
        .order_by(Job.id)
    ).all()
    return _version([tuple(row) for row in rows], budget_closed)


def _version(rows: list[tuple[Any, ...]], budget_closed: bool) -> str:
    """The digest of (id, status, pages_done, result_version, error_code) per job, by id."""
    raw = repr([*rows, budget_closed])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def documents(
    session: Session,
    user_id: str,
    *,
    now: datetime,
    budget_closed: bool,
    course_id: str | None = None,
    job_ids: Sequence[str] | None = None,
) -> DocumentList:
    jobs = session.execute(
        select(
            Job.id,
            Job.filename,
            Job.status,
            Job.page_count,
            Job.pages_done,
            Job.error_code,
            Job.created_at,
            Job.retain_until,
            Job.result_version,
            Job.part_label,
            Job.is_sample,
            Job.exam_mode,
        )
        .where(*_live(user_id, now, course_id, job_ids))
        .order_by(Job.created_at.desc(), Job.id)
    ).all()
    stored = _stored_facts(session, [job.id for job in jobs if job.status == JobStatus.DONE])
    found = states.load(
        session, [job.id for job in jobs], context=states.Context(budget_closed=budget_closed)
    )
    groups = [Group(key, heading) for key, heading in states.GROUP_HEADINGS.items()]
    by_key = {g.key: g for g in groups}
    for job in jobs:
        title, open_count, own = stored.get(job.id, (None, 0, None))
        row = DocumentRow(
            id=job.id,
            title=display_title(title, job.filename, own),
            filename=job.filename,
            pages=job.page_count,
            pages_done=job.pages_done,
            status=str(job.status),
            created_at=as_utc(job.created_at),
            retain_until=as_utc(job.retain_until),
            open_decisions=open_count,
            part_label=job.part_label or "",
            is_sample=bool(job.is_sample),
            exam_mode=bool(job.exam_mode),
            state=found.get(job.id),
        )
        by_key[row.group].rows.append(row)
    # Decisions oldest first, then the stopped documents newest first (module docstring).
    needs = by_key[states.NEEDS].rows
    by_key[states.NEEDS].rows = [r for r in reversed(needs) if r.decide] + [
        r for r in needs if not r.decide
    ]
    # The same digest `list_version` makes, from the rows already read: no second query.
    keys = sorted((j.id, j.status, j.pages_done, j.result_version, j.error_code) for j in jobs)
    return DocumentList(groups=groups, version=_version(keys, budget_closed))


def _stored_facts(session: Session, done: list[str]) -> dict[str, tuple[str, int, str | None]]:
    """job id -> (result title, open decisions, title override) of the finished jobs. The
    columns are read for all of them; a job from before they existed has its result read
    (just its title and fixes) and the facts stored, once."""
    if not done:
        return {}
    rows = session.execute(
        select(Job.id, Job.result_title, Job.open_decisions, Job.title_override).where(
            Job.id.in_(done)
        )
    ).all()
    stored: dict[str, tuple[str, int, str | None]] = {}
    missing: list[str] = []
    for job_id, title, count, own in rows:
        if count is None or title is None:
            missing.append(job_id)
        else:
            stored[job_id] = (title, int(count), own)
    if missing:
        for job in session.scalars(select(Job).where(Job.id.in_(missing))):
            job.result_json = job.result_json  # the set event stores the two facts
            stored[job.id] = (job.result_title or "", job.open_decisions or 0, job.title_override)
    return stored
