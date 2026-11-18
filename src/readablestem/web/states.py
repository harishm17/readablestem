"""One job's state, with its words, tone and next step: `state_of` (SW1).

A job's stored facts (its status, error code, retries, open decisions, the outcome codes of
a finished result, the worker's lease) used to be turned into words in a dozen places: the
job page's badge, heading and tab title, the documents list, the batch page, the parts
navigation, the admin queue, the "Try again" box and the review's finish line. One job could
be "Converted", "Finished", "Stopped" and "Waiting" at once, a stopped job said "N decisions
need you", and a job with no tries left still said "use Try again". Every one of those
surfaces now asks `state_of` and prints what it returns; none keeps its own labels, messages
or conditions.

The table (`TABLE`) has one row per key. A row says when it applies (worked out from stored
facts only, in `state_of`), its label, its tone and its next step. Rows that need facts not
stored yet (a cancel request, checks that answer late, the daily reset of pages, the review's
page cards) are not in it; they are added as rows when their facts exist, never as a surface.

Rules the function owns, so no template decides them:

- The decisions sentence is said only for `needs_decisions` (it is that state's message).
- "Keep for 7 days" is offered only for a state with something to keep (`keeps`).
- A stop message mentions "Try again" only when the next step is "Try again": the message and
  the offer come from the same call, which knows the tries left.
- Gap notes ride on a finished job (part of it was not converted) and never hide open
  decisions. A gap with nothing in it (0 of N not converted) is not a gap.
- The word "Converted" is not a state, and "Stopped" is never said of a finished job.

Messages say what happened and what to do, and never quote the document.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.core.types import DocumentResult
from readablestem.pipeline import checked, checked_words
from readablestem.pipeline.checked import CheckedSummary
from readablestem.web import job_page
from readablestem.web import outcome as words
from readablestem.web.database import as_utc
from readablestem.web.db import Job, JobStatus, utcnow

# --------------------------------------------------------------------------- the table

# Tones, and the badge class (app.css `status-*`) each one is drawn with. The label always
# says the state in words: the color only repeats it.
WAITING, WORKING, FAILED, NEEDS_YOU, GAPS, DONE, SAMPLE = (
    "waiting",
    "working",
    "failed",
    "needs you",
    "done with gaps",
    "done",
    "sample",
)
BADGES = {
    WAITING: "queued",
    WORKING: "running",
    FAILED: "failed",
    NEEDS_YOU: "decisions",
    GAPS: "partial",
    DONE: "done",
    SAMPLE: "sample",
}

# The groups of the documents list, from the tone, in the order the list and its summary
# line give them: what a person has to act on first.
NEEDS, WORK, READY = "needs", "working", "ready"
GROUPS = {WAITING: WORK, WORKING: WORK, FAILED: NEEDS, NEEDS_YOU: NEEDS, GAPS: READY, DONE: READY}
GROUP_HEADINGS = {NEEDS: "Needs you", WORK: "Working", READY: "Ready"}


@dataclass(frozen=True)
class Row:
    """One row of the table: the label (with `{time}` or `{date}` where the row has one),
    the tone, and whether the job will change by itself no more (`final`)."""

    label: str
    tone: str
    final: bool


TABLE: dict[str, Row] = {
    "waiting": Row("Waiting", WAITING, False),
    "waiting_reset": Row("Waiting until {time}", WAITING, False),
    "converting": Row("Converting", WORKING, False),
    "restarting": Row("Restarting", WORKING, False),
    "stopped_file": Row("Stopped: this file cannot be converted as it is", FAILED, True),
    "stopped_service": Row("Stopped: not caused by your file", FAILED, True),
    "stopped_no_tries": Row("Stopped", FAILED, True),
    "stopped_limit": Row("Stopped: too large for one conversion", FAILED, True),
    "stopped_unknown": Row("Stopped", FAILED, True),
    "needs_decisions": Row("Needs your decisions", NEEDS_YOU, True),
    "partly_converted": Row("Partly converted", GAPS, True),
    "ready": Row("Ready to share", DONE, True),
    # Finished with no decision open, but a part is a picture, a page is left out or a
    # structure check failed (or none is stored): "Ready" and what remains (SW7).
    "ready_remains": Row("Ready", GAPS, True),
    "sample": Row("Sample", SAMPLE, True),
    "deleted": Row("Deleted on {date}", WAITING, True),
}
# The states that have something to keep, so "Keep for 7 days" is offered (F-316).
KEEPS = frozenset({"needs_decisions", "partly_converted", "ready", "ready_remains"})
# The finished states with no decision open and no gap note: their message is the
# summary's (`pipeline.checked`), never a template's.
READY_KEYS = frozenset({"ready", "ready_remains"})
# Gap notes that converting again cannot close, so the job can still be shared.
FINAL_GAPS = frozenset({"pages_as_pictures", "source_unread"})

# --------------------------------------------------------------------------- stop codes

# Tries a person may ask for on one job (`web.retry`): each costs only what was not
# finished before, but a job that fails the same way four times will not be helped by a fifth.
MAX_RETRIES_PER_JOB = 3

# Stopped by something that is not about the file, and worth trying again.
# `model_not_configured` is here for the day the person who runs the site has set the key.
RETRYABLE_FAILURES = frozenset(
    {
        "timeout",
        "lease_expired",
        "conversion_unavailable",
        "store_failed",
        "internal_error",
        "model_not_configured",
        "budget_daily",
    }
)
# Stopped by the file itself: trying the same file again cannot help.
FILE_FAILURES = frozenset(
    {
        "encrypted",
        "unreadable",
        "no_pages",
        "too_many_pages",
        "conversion_crashed",
        "no_content",
        "page_too_large",
        "image_too_large",
        "too_complex",
        "scanned",
        "scanned_ocr",
        "handwritten",
        "handwritten_no_text",
        "source_failed",
        "source_unreadable",
        "source_refused",
        "source_missing_file",
        "source_syntax_error",
        "source_undefined",
        "source_unsupported_class",
        "source_unsupported_package",
    }
)
# Stopped by a cap on one document's work: smaller parts each get their own.
LIMIT_FAILURES = frozenset({"budget_job_usd", "budget_job_calls", "conversion_oom"})
# Stopped by something that is not about the file, where "Try again" cannot run: the upload
# is gone, the LaTeX service was busy or missing (nothing partial is kept), or the person's
# access was removed. Said like a stop with no tries left.
NO_RETRY_FAILURES = frozenset(
    {"upload_missing", "source_busy", "source_unavailable", "access_removed"}
)
# Finished jobs that "Convert the missing parts" can complete: stopped early by the day's
# budget (and, read from the result, converted while the AI service was down).
RETRYABLE_STOPS = frozenset({"budget_daily"})
LIMIT_STOPS = frozenset({"budget_job_usd", "budget_job_calls"})

# The words of a stop that can be tried again, said only while it can (F-315).
TRY_AGAIN = "You can use “Try again”: what was already converted is kept."
# The step a conversion stopped in, from its last progress record (F-317).
STEP_WORDS = {
    "layout": "reading the layout of your PDF",
    "equations": "reading the equations",
    "figures": "describing the figures",
    "tables": "looking at the tables",
    "assemble": "building the HTML file",
    "checks": "running the structure checks on the HTML file",
}
UNKNOWN_CAUSE = "The tool cannot tell whether your file or the service caused it."

# --------------------------------------------------------------------------- the answer


@dataclass(frozen=True)
class Step:
    """A next step: its words, where it goes, and whether it is a POST form (with the CSRF
    token) rather than a link. `kind` names it for the surfaces that draw it."""

    kind: str  # "review", "download", "share", "retry", "missing", "upload", "open"
    label: str
    href: str
    form: bool = False
    explain: str = ""  # what the step will and will not do, said beside a form


@dataclass(frozen=True)
class Gap:
    """A note on a finished job: part of it was not converted. `title` is its short name,
    `text` says what and why, `action` is what can be done about it."""

    # "stopped_early", "too_large", "service_outage", "pages_as_pictures", "source_unread"
    code: str
    title: str
    text: str
    action: Step | None = None


@dataclass(frozen=True)
class State:
    key: str
    label: str
    tone: str
    message: str
    steps: tuple[Step, ...] = ()
    final: bool = True
    gaps: tuple[Gap, ...] = ()
    # The documents list group: from the tone, or for the sample, from the state under it.
    group: str = READY
    tries_left: int = 0

    @property
    def badge(self) -> str:
        return BADGES[self.tone]

    @property
    def primary(self) -> Step | None:
        return self.steps[0] if self.steps else None

    @property
    def keeps(self) -> bool:
        """Something to keep, so "Keep for 7 days" is offered (F-316)."""
        return self.key in KEEPS

    @property
    def shareable(self) -> bool:
        """Whole enough to give to students: ready (the student page says what is a picture
        or left out, `pipeline.checked`), or partly converted only because pages are kept as
        pictures or a LaTeX source had parts its reader could not read (converting again
        cannot read either); not a job a cap or an outage stopped part way, whose missing
        parts can still be converted."""
        if self.key in READY_KEYS:
            return True
        return self.key == "partly_converted" and all(g.code in FINAL_GAPS for g in self.gaps)

    @property
    def stopped(self) -> bool:
        return self.tone == FAILED

    @property
    def working(self) -> bool:
        return self.group == WORK

    @property
    def retry(self) -> Step | None:
        """The "Try again" or "Convert the missing parts" form, if this state offers one."""
        steps = [*self.steps, *(g.action for g in self.gaps if g.action is not None)]
        return next((s for s in steps if s.kind in ("retry", "missing")), None)

    @property
    def task(self) -> str:
        """The heading over a finished job's actions: the task, never the label again (F-415)."""
        return "Review" if self.key == "needs_decisions" else "Download"


@dataclass(frozen=True)
class Facts:
    """What `state_of` reads about a job: stored columns, plus the result when a finished
    job has an outcome code or may have been converted during an outage."""

    id: str
    status: str
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    result_json: Mapping[str, Any] | None = None
    progress: Mapping[str, Any] | None = None
    retries: int = 0
    attempts: int = 0
    attempts_at_retry: int = 0
    lease_until: datetime | None = None
    is_sample: bool = False
    deleted: bool = False
    deleted_on: datetime | None = None

    @classmethod
    def of(cls, job: Job) -> Facts:
        return cls(
            id=job.id,
            status=str(job.status),
            error_code=job.error_code,
            error_detail=job.error_detail,
            # Only a finished job's state reads its result (`gaps_of`): a poll of one that is
            # waiting, converting or stopped does not load it.
            result_json=job.result_json if job.status == JobStatus.DONE else None,
            progress=job.progress_json,
            retries=job.retries or 0,
            attempts=job.attempts or 0,
            attempts_at_retry=job.attempts_at_retry or 0,
            lease_until=job.lease_until,
            is_sample=bool(job.is_sample),
        )


@dataclass(frozen=True)
class Context:
    """What is true around the job rather than of it: today's budget is spent, the place in
    the queue (the waiting sentence), the deployment offers to read scanned pages, the time."""

    budget_closed: bool = False
    waiting_line: str = ""
    image_pages_offered: bool = False
    now: datetime | None = None


@dataclass(frozen=True)
class Counts:
    """The open required decisions of a finished job (a group of repeated headings counted
    once), and the statement of what was converted, checked and reviewed
    (`pipeline.checked.CheckedSummary`). `open` is the summary's own count when there is one;
    the documents list passes the stored `jobs.open_decisions` alone for a job with decisions
    open, made by the same function (`core.triage.review_load`)."""

    open: int = 0
    summary: CheckedSummary | None = None


def counts_of(summary: CheckedSummary | None) -> Counts:
    """`Counts` from a finished result's summary (`pipeline.checked.of_result`)."""
    if summary is None:
        return Counts()
    return Counts(summary.open_required, summary)


def tries_left(retries: int) -> int:
    return max(0, MAX_RETRIES_PER_JOB - (retries or 0))


def state_of(
    job: Facts,
    counts: Counts | None = None,
    tries: int | None = None,
    viewer: str = "owner",
    context: Context | None = None,
) -> State:
    """The state of a job from its stored facts. `counts` are a finished job's open
    decisions; `tries` the tries left (from `job.retries` when not given); `viewer` is
    "owner", "review" or "view" (which steps they may take)."""
    ctx = context or Context()
    counts = counts or Counts()
    left = tries_left(job.retries) if tries is None else max(0, tries)
    if job.deleted:
        return _deleted(job)
    if job.is_sample and job.status == JobStatus.DONE:
        under = _done(job, counts, left, viewer)
        steps = (Step("open", "Open", f"/jobs/{job.id}"),)
        return _make("sample", under.message, steps, under.gaps, group=under.group)
    if job.status == JobStatus.QUEUED:
        if job.attempts > job.attempts_at_retry:  # put back after a lost attempt
            return _make("restarting", _restart_message())
        if ctx.budget_closed:
            reset = _next_reset(ctx.now)
            return _make("waiting_reset", words.WAITING_BUDGET, time=f"{reset:%H:%M} UTC")
        return _make("waiting", ctx.waiting_line or "Waiting for its turn.")
    if job.status == JobStatus.RUNNING:
        lease = as_utc(job.lease_until)
        if lease is not None and lease < (ctx.now or utcnow()):
            return _make("restarting", _restart_message())
        return _make("converting", job_page.stage_sentence(dict(job.progress or {})))
    if job.status == JobStatus.FAILED:
        return _stopped(job, left, viewer, ctx)
    return _done(job, counts, left, viewer)


def _make(
    key: str,
    message: str,
    steps: Iterable[Step] = (),
    gaps: Iterable[Gap] = (),
    *,
    group: str | None = None,
    tries: int = 0,
    **fill: str,
) -> State:
    row = TABLE[key]
    return State(
        key=key,
        label=row.label.format(**fill) if fill else row.label,
        tone=row.tone,
        message=message,
        steps=tuple(steps),
        final=row.final,
        gaps=tuple(gaps),
        group=group or GROUPS.get(row.tone, READY),
        tries_left=tries,
    )


def _restart_message() -> str:
    return "The converter was interrupted, and this document is being started again."


def _next_reset(now: datetime | None) -> datetime:
    moment = as_utc(now) or utcnow()
    return (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _deleted(job: Facts) -> State:
    when = as_utc(job.deleted_on)
    upload = Step("upload", "Upload it again", "/app#upload-heading")
    if when is None:  # purged: the date is no longer known
        return State(
            "deleted", "Deleted", WAITING, "This document was deleted.", (upload,), True, (), NEEDS
        )
    return _make(
        "deleted",
        "This document was deleted.",
        (upload,),
        group=NEEDS,
        date=f"{when:%b} {when.day}, {when.year}",
    )


# --------------------------------------------------------------------------- stopped


def _retry_step(job: Facts, left: int) -> Step:
    return Step(
        "retry",
        f"Try again ({left} left)",
        f"/jobs/{job.id}/retry",
        form=True,
        explain="Try again puts your document back in the queue. Nothing is counted against "
        "your daily pages, and anything that was already converted is kept.",
    )


def _upload(label: str = "Upload it again") -> Step:
    return Step("upload", label, "/app#upload-heading")


def _stopped(job: Facts, left: int, viewer: str, ctx: Context) -> State:
    from readablestem.web.views import RETRY_HINTS, job_error_message, source_stop_message

    code = job.error_code or ""
    owner = viewer == "owner"
    if code in RETRYABLE_FAILURES:
        cause = job_error_message(code, with_hint=False)
        if left:
            message = f"{cause} {RETRY_HINTS[code]}" if code in RETRY_HINTS else cause
            steps = (_retry_step(job, left), _upload()) if owner else ()
            return _make("stopped_service", message, steps, tries=left)
        steps = (_upload("Upload it again as a new document"),) if owner else ()
        return _make("stopped_no_tries", _no_tries(cause, job.retries), steps)
    if code in NO_RETRY_FAILURES:
        steps = (_upload("Upload it again as a new document"),) if owner else ()
        return _make("stopped_no_tries", job_error_message(code), steps)
    if code in LIMIT_FAILURES:
        return _make("stopped_limit", job_error_message(code), (_upload(),) if owner else ())
    if code in FILE_FAILURES:
        message = words.failed_message(code, image_pages_offered=ctx.image_pages_offered)
        message = message or source_stop_message(code, job.error_detail)
        return _make("stopped_file", message, (_upload(),) if owner else ())
    # An unknown or missing code: say the step it stopped in, and that the cause is not known.
    stage = job_page.current_stage(dict(job.progress or {}))
    parts = [job_error_message(None)]
    if stage in STEP_WORDS:
        parts.append(f"It stopped while {STEP_WORDS[stage]}.")
    parts.append(UNKNOWN_CAUSE)
    if left:
        parts.append(TRY_AGAIN)
        steps = (_retry_step(job, left), _upload()) if owner else ()
        return _make("stopped_unknown", " ".join(parts), steps, tries=left)
    return _make("stopped_unknown", " ".join(parts), (_upload(),) if owner else ())


def _no_tries(cause: str, retries: int) -> str:
    if not retries:
        return cause
    times = "1 time" if retries == 1 else f"{retries} times"
    return f"{cause} You have already tried this document {times}."


# --------------------------------------------------------------------------- finished


def gaps_of(job: Facts, left: int, viewer: str = "owner") -> list[Gap]:
    """The gap notes of a finished job: what of it was not converted, with what to do. A
    gap with nothing in it is not produced (F-319)."""
    code, result = job.error_code, job.result_json
    owner = viewer == "owner"
    missing = (
        Step(
            "missing",
            "Convert the missing parts",
            f"/jobs/{job.id}/retry",
            form=True,
            explain="Only the parts that were not converted are sent again. Nothing is counted "
            "against your daily pages, and your decisions are kept.",
        )
        if left and owner
        else None
    )
    out: list[Gap] = []
    data = dict(result) if result else None
    offered = missing is not None
    if not code and (outage := words.outage_message(data, offered=offered)):
        out.append(Gap("service_outage", "Service outage", outage, missing))
    if code in RETRYABLE_STOPS and (text := words.budget_message(data, code, offered=offered)):
        out.append(Gap("stopped_early", "Stopped early", text, missing))
    if code in LIMIT_STOPS and (text := words.budget_message(data, code)):
        out.append(Gap("too_large", "Stopped early", text))
    if code in words.PAGES_NOT_CONVERTED and (text := words.pages_message(data)):
        out.append(Gap("pages_as_pictures", "Pages kept as pictures", text))
    if not code and (text := words.unread_message(data)):
        out.append(Gap(words.SOURCE_UNREAD, "Parts not read", text))
    return out


def _unanalysed_pages(result: Mapping[str, Any] | None) -> int:
    """Pages of a stored result whose drawings were not analysed (a notice each)."""
    notices = (result or {}).get("notices") or []
    return len({n.get("page") for n in notices if n.get("code") == "drawings_not_analysed"})


# Required notices that no decision stands behind, so the summary cannot count them: each keeps a
# finished job at "Ready", with its sentence. A required notice whose fix is the decision (a
# table kept as an image, a formula LaTeX could not read) is not here: the summary counts that.
_HOLDS = {
    # `web.office_census`: the PDF may show deleted words, so it is not known to be the final text
    "office_tracked_changes": (
        "The Word file still has tracked changes, so check the notices before you share it."
    ),
}


def _held_by_notice(result: Mapping[str, Any] | None) -> str:
    """The sentence of a stored result's notice that holds it back (`_HOLDS`), "" when none."""
    codes = {n.get("code") for n in (result or {}).get("notices") or []}
    return next((message for code, message in _HOLDS.items() if code in codes), "")


def _unanalysed_message(pages: int) -> str:
    return (
        f"The drawings on {checked_words.plural(pages, 'page')} were not analyzed, so check the "
        "notices before you share it."
    )


def _done(job: Facts, counts: Counts, left: int, viewer: str) -> State:
    gaps = gaps_of(job, left, viewer)
    review_ok = viewer in ("owner", "review")
    if counts.open:
        steps = (Step("review", "Review", f"/jobs/{job.id}/review"),) if review_ok else ()
        return _make(
            "needs_decisions", job_page.open_decisions_line(counts.open, viewer), steps, gaps
        )
    download = Step("download", "Download", f"/jobs/{job.id}/download")
    share = Step("share", "Share", f"/jobs/{job.id}#sharing-heading")
    if gaps:
        actions = [g.action for g in gaps if g.action is not None]
        steps = (*actions, download) if actions else (download,)
        return _make("partly_converted", " ".join(g.text for g in gaps), steps, gaps)
    steps = (download, share) if viewer == "owner" else (download,)
    summary = counts.summary
    if summary is not None and summary.ready_to_share:
        if pages := _unanalysed_pages(job.result_json):
            # The ink analysis ran out of work on these pages (`drawings_not_analysed`): the
            # page cannot say everything was converted.
            return _make("ready_remains", _unanalysed_message(pages), steps)
        if held := _held_by_notice(job.result_json):
            return _make("ready_remains", held, steps)
        return _make("ready", checked_words.READY_ALL, steps)
    # Not known to be whole (no summary), or a part is a picture, a page is left out or a
    # structure check failed: "Ready", and what remains, never "Ready to share".
    return _make("ready_remains", checked_words.remains(summary) if summary else "", steps)


# --------------------------------------------------------------------------- loading


# The columns `load` reads; the result only for a finished job that can have a gap note.
_COLUMNS = (
    Job.id,
    Job.status,
    Job.error_code,
    Job.retries,
    Job.attempts,
    Job.attempts_at_retry,
    Job.lease_until,
    Job.is_sample,
    Job.open_decisions,
)


def load(
    session: Session, job_ids: Iterable[str], *, context: Context | None = None
) -> dict[str, State]:
    """The state of each job, from its columns. A finished job with no decision open, or
    with an outcome code, has its result read: its label is the summary's
    (`pipeline.checked`), and only a job with a code can carry a gap note. A job with
    decisions open needs only the stored count. A finished job from before
    `jobs.open_decisions` existed has the column filled once (`documents.list_facts`)."""
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        return {}
    rows = session.execute(select(*_COLUMNS).where(Job.id.in_(ids))).all()
    with_result = [
        r.id for r in rows if r.status == JobStatus.DONE and (r.error_code or not r.open_decisions)
    ]
    results: dict[str, tuple[Any, int | None]] = {}
    if with_result:
        for job in session.scalars(select(Job).where(Job.id.in_(with_result))):
            if job.open_decisions is None:
                job.result_json = job.result_json  # the set event stores the list's facts
            results[job.id] = (job.result_json, job.open_decisions)
    out: dict[str, State] = {}
    for r in rows:
        result, opened = results.get(r.id, (None, r.open_decisions))
        counts = Counts(open=int(opened or 0))
        if result:
            counts = counts_of(checked.of_result(DocumentResult.model_validate(result)))
        facts = Facts(
            id=r.id,
            status=str(r.status),
            error_code=r.error_code,
            result_json=result,
            retries=r.retries or 0,
            attempts=r.attempts or 0,
            attempts_at_retry=r.attempts_at_retry or 0,
            lease_until=r.lease_until,
            is_sample=bool(r.is_sample),
        )
        out[r.id] = state_of(facts, counts, context=context)
    return out
