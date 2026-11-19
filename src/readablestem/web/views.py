"""Read-only view models for templates: plain data, no ORM objects past the session.

Error codes are categories; the user-facing sentences for them live here so templates and
tests share one wording. The job page's own data (progress, the honest headline, the
before/after card, notices, language) is built by `web.job_page` from what is read here:
the job row, its stored progress and result, the queue ahead of it, and the day of the
last review decision.
"""

from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from readablestem.core import triage
from readablestem.core.types import DocumentResult, Fix, Kind, Status
from readablestem.pipeline import checked
from readablestem.pipeline.checked import CheckedSummary
from readablestem.web import carry as carry_rules
from readablestem.web import combine as combine_rules
from readablestem.web import (
    documents,
    fair_queue,
    job_page,
    notation,
    office_census,
    output,
    states,
)
from readablestem.web.database import as_utc
from readablestem.web.db import Job, JobStatus, ReviewEdit, User, utcnow
from readablestem.web.messages import Explained, explain_check, severity_label
from readablestem.web.outcome import FAILED_MESSAGES

FIX_STATUS_LABELS = {
    Status.ACCEPTED: "Accepted automatically",
    Status.NEEDS_REVIEW: "Needs review",
    Status.ABSTAINED: "Not converted (no confident proposal)",
    Status.FAILED: "Not converted (error)",
    Status.EDITED: "Edited by you",
    Status.REJECTED: "Rejected: original kept",
}
# A reviewer's Accept shares Status.ACCEPTED with the pipeline's (Fix.reviewed differs).
REVIEWER_ACCEPTED_LABEL = "Accepted by you"


def fix_status_label(fix: Fix) -> str:
    if fix.accepted_by_reviewer:
        return REVIEWER_ACCEPTED_LABEL
    return FIX_STATUS_LABELS.get(fix.status, fix.status.value)


# The sentence a stop that "Try again" can help ends with. It is said only while a try is
# left (`web.states`: the message and the offer come from one call), so a job with no tries
# left never says "use Try again" (F-315).
RETRY_HINTS = {
    "budget_daily": "After 00:00 UTC, use “Try again”: what was already converted is kept.",
    "lease_expired": "Use “Try again”: what was already converted is kept.",
    "timeout": "You can use “Try again”: what was already converted is kept. If it stops "
    "again, try splitting the document into smaller parts.",
    "conversion_unavailable": "Use “Try again” in a little while.",
    "store_failed": "Use “Try again”: what was already converted is kept.",
    "internal_error": "You can use “Try again”: what was already converted is kept.",
}

JOB_ERROR_MESSAGES = {
    "budget_daily": "Today's conversion budget ran out while this document was being converted.",
    "budget_job_usd": "This document needed more model work than one conversion is allowed. "
    "Try splitting it into smaller parts.",
    "budget_job_calls": "This document needed more model calls than one conversion is "
    "allowed. Try splitting it into smaller parts.",
    "encrypted": "The PDF is password-protected.",
    "unreadable": "The PDF could not be read.",
    "too_many_pages": "The PDF has too many pages.",
    "no_pages": "The PDF has no pages.",
    "lease_expired": "The conversion was interrupted several times and has been stopped.",
    "timeout": "The conversion took longer than a document of this size is allowed and was "
    "stopped.",
    "conversion_crashed": "The converter stopped while reading this PDF. Try saving the PDF "
    "again from the program that made it, or splitting it into smaller parts.",
    "conversion_unavailable": "The converter could not start. This is on our side, not a "
    "problem with your file.",
    "conversion_oom": "This PDF needed more memory than a conversion may use. Try splitting "
    "it into smaller parts.",
    "store_failed": "The conversion finished but its results could not be saved.",
    "upload_missing": "The uploaded file was no longer available.",
    # LaTeX source and Word files read from the file (readablestem.source): nothing partial
    # is kept.
    "source_failed": "This source file could not be converted. Upload the PDF instead.",
    "source_unreadable": "This source file could not be converted. Upload the PDF instead.",
    "source_timeout": "This source file did not finish in the time one conversion may take, "
    "which usually means a macro that calls itself or a file that is very large. Trying again "
    "will not change that. Fix it, or compile it and upload the PDF.",
    "source_refused": "This source file uses something this tool will not run. Remove it, "
    "or upload the PDF instead.",
    "source_busy": "The server is busy right now and could not start converting this source "
    "file. Upload it again in a few minutes, or upload the PDF instead.",
    "source_unavailable": "Reading source files is not available on this server at the "
    "moment, so this file was not converted. Save it as a PDF and upload that.",
    # A source that would lose content outside a formula (source.report.stop). With the job's
    # `error_detail` the sentence names the file or commands (`source_stop_message`); these are
    # the words without it.
    "source_missing_file": "This source asks for a file that is not in the upload, so nothing "
    "was converted. Zip the whole project folder with that file in it and upload the zip, or "
    "upload the PDF.",
    "source_syntax_error": "LaTeX cannot read this source past an error: a brace or an "
    "environment is not closed. Everything after that point would be missing, so nothing was "
    "converted. Compile it, fix the error and upload it again, or upload the PDF.",
    "source_undefined": "This source uses commands that are not defined. They would appear to "
    "students as raw code, so nothing was converted. Define them in the source, or upload the "
    "PDF.",
    "source_unsupported_class": "This source uses a document class this tool cannot read from "
    "source yet, so nothing was converted. Upload the PDF.",
    "source_unsupported_package": "This source uses commands from a package this tool cannot "
    "read yet. They would appear to students as raw code, so nothing was converted. Copy the "
    "definitions you use into the source, or upload the PDF.",
    "internal_error": "Something went wrong on our side. The error was recorded without any "
    "of your document's content.",
    # Codes with a longer explanation of what to do next (scanned PDFs, no model key,
    # removed access, ...) share their wording with the status box.
    **FAILED_MESSAGES,
}


def _where(item: dict[str, Any]) -> str:
    file, line = item.get("file") or "", item.get("line")
    if file and line:
        return f"{file}, line {line}"
    return str(file)


# What to write instead of a special character LaTeX cannot read in running text.
CHARACTER_ADVICE = {
    "^": "If it starts an exponent, put the formula inside $ signs; for the character itself, "
    "write \\^{}. ",
    "_": "If it starts a subscript, put the formula inside $ signs; for the character itself, "
    "write \\_. ",
    "&": "For the character itself, write \\&. ",
    "#": "For the character itself, write \\#. ",
}


# A Word file read from the file itself that could not be converted (`error_detail` kind "docx",
# `source.convert`). The PDF route a Word file takes with the reader off is not taken from a
# stopped job (its upload is the Word file, charged by its own estimate), so the way on is
# said: save the Word file as a PDF and upload that.
WORD_SAVE_AS_PDF = "In Word, choose File, Save As (or Export), pick PDF, and upload that PDF."
WORD_STOP_MESSAGES = {
    "source_failed": "This Word file could not be read, so nothing was converted. "
    + WORD_SAVE_AS_PDF,
    "source_unreadable": "This Word file could not be read, so nothing was converted. "
    + WORD_SAVE_AS_PDF,
    "source_timeout": "This Word file did not finish in the time one conversion may take, "
    "which usually means a very large file. Trying again will not change that. " + WORD_SAVE_AS_PDF,
    "source_refused": "This Word file has something this tool will not read, so nothing was "
    "converted. " + WORD_SAVE_AS_PDF,
}


def source_stop_message(code: str | None, detail: dict[str, Any] | None) -> str:
    """The stop sentence of a source job, naming the file or commands from its
    `error_detail` (`source.report.stop`); the plain sentence when there is no detail."""
    if detail and detail.get("kind") == "docx" and code in WORD_STOP_MESSAGES:
        return WORD_STOP_MESSAGES[code or ""]
    plain = job_error_message(code)
    if not detail:
        return plain
    items = [i for i in detail.get("items") or [] if isinstance(i, dict) and i.get("name")]
    total = len(items) + int(detail.get("more") or 0)
    first = items[0] if items else {}
    where = _where(first)
    at = f" ({where})" if where else ""
    if code == "source_missing_file" and first:
        if total == 1:
            return (
                f"This source asks for the file {first['name']}{at}, and it is not in the "
                "upload. Nothing was converted. Zip the whole project folder with that file in "
                "it and upload the zip, or upload the PDF."
            )
        return (
            f"This source asks for {total} files that are not in the upload, listed below. "
            "Nothing was converted. Zip the whole project folder with those files in it and "
            "upload the zip, or upload the PDF."
        )
    if code == "source_syntax_error" and detail.get("character") and first:
        # ^ _ & # in running text (`source.report.CHARACTERS`): LaTeX reads them only in a
        # formula, a table or a definition
        if total == 1:
            character = str(first["name"])
            advice = CHARACTER_ADVICE.get(character, "")
            return (
                f"This source has the character {character} in its text{at}, where LaTeX "
                "cannot read it, so the words around it would be wrong and nothing was "
                f"converted. {advice}Then upload it again, or upload the PDF."
            )
        return (
            f"This source has {total} special characters in its text where LaTeX cannot read "
            f"them, listed below. The words around them would be wrong, so nothing was "
            "converted. Put each exponent or subscript inside $ signs, and write each "
            "character you mean as itself with a backslash: \\&, \\#, \\_ or \\^{}. Then upload "
            "it again, or upload the PDF."
        )
    if code == "source_syntax_error" and where:
        return (
            f"LaTeX cannot read this source past {where}: a brace or an environment is not "
            "closed. Everything after that point would be missing, so nothing was converted. "
            "Compile it, fix the error and upload it again, or upload the PDF."
        )
    if code == "source_unsupported_class" and detail.get("class"):
        return (
            f"This source uses the document class {detail['class']}, which this tool cannot "
            "read from source yet, so nothing was converted. Upload the PDF."
        )
    if code == "source_unsupported_package" and detail.get("package") and not first:
        # Refused by name in the conversion child (`source.intake.check_support`).
        return (
            f"This source loads the package {detail['package']}, which this tool cannot read "
            "yet, so nothing was converted. Compile it to a PDF and upload that."
        )
    if code in ("source_undefined", "source_unsupported_package") and first:
        names = ", ".join(str(i["name"]) for i in items[:5]) + (", ..." if total > 5 else "")
        first_at = f" (first at {where})" if where else ""
        one = total == 1
        count = "1 command" if one else f"{total} commands"
        they, them = ("It", "it") if one else ("They", "them")
        # Where the package comes from (`source.report._origin`): a TeX package this tool does
        # not read yet, or the instructor's own, whose file in the upload is not read.
        if code == "source_unsupported_package" and detail.get("origin") == "distribution":
            return (
                f"This source uses {count} from the package {detail['package']}, which this "
                f"tool cannot read yet: {names}{first_at}. {they} would appear to students as "
                "raw code, so nothing was converted. Upload the PDF."
            )
        if code == "source_unsupported_package" and detail.get("origin") == "own":
            return (
                f"This source uses {count} from the package {detail['package']}, which is not "
                f"a standard TeX package: {names}{first_at}. This tool does not read package "
                f"files from the upload, so {they.lower()} would appear to students as raw code "
                "and "
                f"nothing was converted. Copy the definitions you use into the source (a file "
                "it reads with \\input counts), or upload the PDF."
            )
        if code == "source_unsupported_package" and detail.get("package"):
            return (
                f"This source uses {count} from the package {detail['package']}, which this "
                f"tool cannot read yet: {names}{first_at}. {they} would appear to students as "
                "raw code, so nothing was converted. A package file in the upload is not read "
                "either: copy the definitions you use into the source, or upload the PDF."
            )
        return (
            f"{count.capitalize()} in this source {'is' if one else 'are'} not defined: "
            f"{names}{first_at}. {they} would appear to students as raw code, so nothing was "
            f"converted. Define {them} in the source (a file it reads with \\input counts), or "
            "upload the PDF."
        )
    return plain


def source_stop_names(detail: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(name, where) for the list under a stopped source job's sentence, and a last line
    for the names left out."""
    if not detail:
        return []
    items = [i for i in detail.get("items") or [] if isinstance(i, dict) and i.get("name")]
    rows = [(str(i["name"]), _where(i)) for i in items]
    more = int(detail.get("more") or 0)
    if rows and more:
        rows.append(("", f"and {more} more"))
    return rows


def job_error_message(code: str | None, *, with_hint: bool = True) -> str:
    """The stop sentence of an error code; with its "Try again" hint (`RETRY_HINTS`) unless
    `with_hint` is off. The job page asks `web.states`, which adds the hint only while a try
    is left."""
    plain = JOB_ERROR_MESSAGES.get(
        code or "", "The conversion stopped, and the tool has no more specific reason to show."
    )
    hint = RETRY_HINTS.get(code or "") if with_hint else None
    return f"{plain} {hint}" if hint else plain


@dataclass
class CheckLine:
    """One failed check of the output HTML, as the job page lists it."""

    text: str  # what it means (web.messages)
    code: str  # "name: code"
    severity: str  # "Must be fixed" or "Worth checking"


@dataclass
class ResultSummary:
    pages: int
    by_kind: dict[str, int]  # fixes per kind
    needs_review: int
    accepted: int  # accepted automatically by the pipeline
    not_converted: int
    reviewed: int  # accepted, edited or rejected by the reviewer
    # Decisions still waiting (what "done" waits for): every fix waiting for a decision
    # (needs review, failed, not converted), a group of repeated headings counted once.
    # Per kind ("equation" -> 2) for the links into the review screen's groups.
    decisions: int = 0
    review_by_kind: dict[str, int] = field(default_factory=dict)
    review_labels: dict[str, str] = field(default_factory=dict)  # "1 group of headings"
    # Per kind, the first decision still open in the review screen's order (an element id
    # or a heading group's key), for links straight to it.
    review_first: dict[str, str] = field(default_factory=dict)
    # Accepted automatically with an optional note nobody has looked at: never counted
    # toward "done".
    optional: int = 0
    # ... of which the second automated check's (a possible alt text mismatch), which
    # the page calls worth a look rather than "nothing to do".
    second_check: int = 0
    list_items: int = 0  # list item elements (no fixes: lists are rebuilt as they are)
    # Static checks of the stored HTML (re-run after every review decision): how many
    # ran, and the ones that failed. `checks_run` is 0 for results stored without them.
    checks_run: int = 0
    failed_checks: list[CheckLine] = field(default_factory=list)
    # One breakdown of every item, the same on every screen (`job_page.breakdown`).
    counts: job_page.Breakdown = field(default_factory=job_page.Breakdown)
    # What was converted, checked and reviewed (`pipeline.checked`): the state, the checks
    # line and every claim on the job page read it.
    checked: CheckedSummary | None = None


@dataclass
class JobView:
    id: str
    filename: str
    status: str
    page_count: int
    pages_done: int
    created_at: datetime | None
    finished_at: datetime | None
    retain_until: datetime | None
    opt_in: bool
    error_code: str | None
    cost_usd: float
    model_calls: int
    queue_ahead: int = 0
    # A stopped source job: the files and commands that stopped it (`Job.error_detail`).
    error_detail: dict[str, Any] | None = None
    summary: ResultSummary | None = None
    # `jobs.result_version` as loaded with the result: review forms send it back, so a
    # form made before another tab changed the review is refused (web.review).
    result_version: int = 0
    # While queued: how many of the jobs ahead are this user's, and the estimated wait.
    ahead_yours: int = 0
    wait_s: float | None = None
    # While waiting or converting: since when (the upload for a waiting job that was never
    # tried again, the start of the attempt for a running one), and after how many seconds
    # that counts as longer than usual (`job_page.slow_after`). The page counts the elapsed
    # time itself; no percentage or finish time is made up.
    waiting_since: datetime | None = None
    slow_after_s: float = 0.0
    # How many tries it has had ("Try again", T3-1, `web.retry`).
    retries: int = 0
    # While converting: the stored live progress (`jobs.progress_json`).
    progress: dict[str, Any] | None = None
    # The job page's data from the result (`job_page.PageData`), when asked for.
    page: job_page.PageData | None = None
    # What the instructor chose for the student file (T1-7, T1-17) and how long the job is
    # kept (T1-8). `title` is what this page calls the document: the instructor's title,
    # else the PDF's without its section number, else the file name.
    title: str = ""
    title_override: str | None = None
    contact_line: str | None = None
    retention_mode: str = "day"
    # Converted as an exam or quiz (`Job.exam_mode`, the words are `web.exam`).
    exam_mode: bool = False
    # What happened to the decisions of the earlier version this one revises (T3-2):
    # `carry.CarrySummary` and the earlier file's name; None for a job that revises nothing.
    carry: CarryView | None = None
    # Elements whose decision was copied from the earlier version (the review screen says so).
    carried_ids: frozenset[str] = frozenset()
    # A part of a larger file converted in parts (T3-3): the pages this one holds and the
    # parts of the same file still kept (this one included), in page order.
    part_label: str = ""
    parts: list[PartLink] = field(default_factory=list)
    # A Word or PowerPoint upload (T3-30): how the file was read, in sentences
    # (`web.office_census.read_lines`); empty for a PDF.
    office_lines: list[str] = field(default_factory=list)
    # How many parts "Combine parts" would join (`web.combine.kept_parts`: finished, kept,
    # no decision open, none overlapping); the link is offered from two.
    combinable: int = 0
    # The recorded sample document (T3-9, `web.sample`) and, for its page, where it comes from.
    is_sample: bool = False
    sample: SampleView | None = None
    # What the person looking at it is to it (`web.shares`): "owner", or the right the owner
    # gave them, and who shared it. The page shows only what that right allows.
    role: str = "owner"
    shared_by: str = ""
    # No decision is open, so the file may leave for anyone (`web.output.export_state`): the
    # one answer the reader page asks before it offers Open, Listen or a download.
    exportable: bool = False
    # Which version of the document its file is ("Version b9487b-12, November 10, 2026", WS20.1,
    # `web.output.stamp_of`): for the owner and a reviewer of a finished document; empty for
    # anyone else and while it converts.
    version_line: str = ""
    # The course symbols the conversion was handed (`DocumentResult.notation_applied`), as
    # "bold d is a vector"; None where there is no record. Set with the page of a finished job.
    notation_applied: list[str] | None = None
    # The job's state (`web.states`): its label, tone, message, next steps and gap notes.
    # Every word about where the job stands on the job page comes from it.
    state: states.State | None = None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def can_review(self) -> bool:
        return self.role in {"owner", "review"}

    @property
    def finished(self) -> bool:
        return self.status in {JobStatus.DONE, JobStatus.FAILED}

    @property
    def stage(self) -> str | None:
        """The step a running conversion is on (`job_page.current_stage`)."""
        if self.status != JobStatus.RUNNING:
            return None
        return job_page.current_stage(self.progress)

    @property
    def stage_lines(self) -> list[job_page.StageLine]:
        if self.status != JobStatus.RUNNING:
            return []
        return job_page.stage_lines(self.progress)

    @property
    def version(self) -> str:
        """Changes whenever the status partial would look different (htmx polling). The
        status box is a live region, so it changes per step, not per element."""
        return f"{self.status}-{self.stage or 'none'}-{self.queue_ahead}"

    @property
    def progress_version(self) -> str:
        """Changes whenever the list of steps would look different (its own poll)."""
        lines = "|".join(f"{line.text}:{line.state}" for line in self.stage_lines)
        return f"{self.status}-{zlib.crc32(lines.encode()):08x}"

    @property
    def waited_s(self) -> float | None:
        """Seconds since the wait began, as of now; None when it is not a wait to count."""
        if self.waiting_since is None or self.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return None
        return max(0.0, (utcnow() - self.waiting_since).total_seconds())

    @property
    def waited_text(self) -> str:
        waited = self.waited_s
        return job_page.elapsed_words(waited) if waited is not None else ""

    @property
    def slow(self) -> bool:
        """The wait has gone on longer than usual for a document like this."""
        waited = self.waited_s
        return waited is not None and waited > self.slow_after_s

    @property
    def audience(self) -> str:
        """Who is reading, for the state's words and steps: "owner", "reviewer" or "reader"."""
        return audience_of(self.role)

    @property
    def retry(self) -> states.Step | None:
        """The "Try again" or "Convert the missing parts" form the state offers, if any."""
        return self.state.retry if self.state is not None else None

    @property
    def rerun(self) -> states.Step | None:
        """ "Convert it again" on a finished document made by an older converter (WS10.15)."""
        return self.state.rerun if self.state is not None else None

    @property
    def stop_names(self) -> list[tuple[str, str]]:
        """The list under a stopped source job's sentence (`source_stop_names`)."""
        return source_stop_names(self.error_detail) if self.status == JobStatus.FAILED else []


@dataclass
class SampleView:
    """Where the sample document comes from, for the notice on its page."""

    title: str
    source_url: str
    licence: str
    licence_url: str
    authors: str


@dataclass
class PartLink:
    """One part of a larger file, for the list on each part's page."""

    id: str
    label: str
    status: str
    current: bool
    state: states.State | None = None  # the part's own state (`web.states`), for its words


@dataclass
class CarryView:
    """The carry-over card of a revised document (`templates/_job_carry.html`)."""

    from_name: str
    summary: carry_rules.CarrySummary
    first_waiting: str | None = None  # review link target: the first changed or new item


DECISION_NOUNS = {
    Kind.EQUATION: ("equation", "equations"),
    Kind.FIGURE: ("figure", "figures"),
    Kind.TABLE: ("table", "tables"),
    Kind.HEADING: ("heading", "headings"),
    Kind.PARAGRAPH: ("piece of text", "pieces of text"),
    Kind.LIST_ITEM: ("list item", "list items"),
}


# The pages (or handwriting) kept as images for one reason are one decision, named by
# what they are, never as "repeated figures" (pipeline.page_type; web.review).
KEPT_IMAGE_NOUNS = {
    "handwritten_page": ("the handwritten page", "the handwritten pages"),
    "scanned_page": ("the scanned page", "the scanned pages"),
    "unread_page": ("the page kept as an image", "the pages kept as images"),
    "page_read_failed": ("the page that could not be read", "the pages that could not be read"),
    "handwriting_on_page": ("the handwriting on a typed page", "the handwriting on typed pages"),
}


def decisions_label(units: list[list[Fix]], kind: Kind) -> str:
    """'2 equations', '1 group of repeated headings', '3 headings and 1 group of
    repeated headings': decisions of one kind (a heading group is one decision). Pages
    kept as images are named by why ('the handwritten pages and the scanned page')."""
    one, many = DECISION_NOUNS[kind]
    kept: list[list[Fix]] = []
    rest: list[list[Fix]] = []
    for unit in units:
        (kept if job_page.word_kind(unit[0]) in ("page", "handwriting") else rest).append(unit)
    units = rest
    groups = sum(1 for unit in units if unit[0].group_key)
    singles = len(units) - groups
    parts = [f"{singles} {one if singles == 1 else many}"] if singles else []
    if groups:
        parts.append(f"{groups} {'group' if groups == 1 else 'groups'} of repeated {many}")
    for unit in kept:
        nouns = KEPT_IMAGE_NOUNS.get(unit[0].reason or "", KEPT_IMAGE_NOUNS["unread_page"])
        parts.append(nouns[0] if len(unit) == 1 else nouns[1])
    return job_page.join_words(parts)


def _check_line(explained: Explained, severity: str) -> CheckLine:
    return CheckLine(explained.text, explained.code, severity)


def summarise(result_json: dict[str, Any] | None) -> ResultSummary | None:
    if not result_json:
        return None
    result = DocumentResult.model_validate(result_json)
    statuses = Counter(f.status for f in result.fixes)
    waiting = triage.units(f for f in result.fixes if f.pending)
    failed = [c for c in result.html_checks if not c.passed]
    # Kinds as the job page names them: pages and handwriting kept as images are figures
    # in the data model, but not in words (`job_page.word_kind`).
    word = job_page.word_kind
    by_word: dict[str, list[list[Fix]]] = {}
    for unit in waiting:
        by_word.setdefault(word(unit[0]), []).append(unit)
    return ResultSummary(
        pages=result.stats.pages,
        by_kind=dict(Counter(word(f) for f in result.fixes)),
        needs_review=statuses[Status.NEEDS_REVIEW],
        accepted=sum(f.status is Status.ACCEPTED and not f.reviewed for f in result.fixes),
        not_converted=statuses[Status.ABSTAINED] + statuses[Status.FAILED],
        reviewed=sum(f.decided_by_reviewer for f in result.fixes),
        decisions=triage.open_decisions(result.fixes),
        review_by_kind={kind: len(units) for kind, units in by_word.items()},
        review_labels={
            kind: decisions_label(units, units[0][0].kind) for kind, units in by_word.items()
        },
        review_first=_first_open_by_kind(result),
        optional=sum(f.optional_note for f in result.fixes),
        second_check=sum(triage.second_check_note(f) for f in result.fixes),
        list_items=sum(e.kind is Kind.LIST_ITEM for e in result.elements),
        checks_run=len(result.html_checks),
        failed_checks=[_check_line(explain_check(c), severity_label(c)) for c in failed],
        counts=job_page.breakdown(result.fixes, result.notices),
        checked=checked.of_result(result),
    )


def _first_open_by_kind(result: DocumentResult) -> dict[str, str]:
    from readablestem.web import queue

    first: dict[str, str] = {}
    for decision in queue.decisions(result):
        if decision.open:
            first.setdefault(job_page.word_kind(decision.lead), decision.key)
    return first


def queue_ahead(session: Session, job: Job) -> int:
    """Queued jobs the worker will claim before this one."""
    return len(_jobs_ahead(session, job))


def _jobs_ahead(session: Session, job: Job) -> list[fair_queue.Queued]:
    """The queued jobs the worker claims before this one: the same order as `Worker.claim`
    (`fair_queue`: fewest attempts, people taking turns, then oldest), replayed claim by
    claim, and like it only jobs whose owner is still allowed."""
    rows = session.execute(
        select(Job.id, Job.user_id, Job.attempts, Job.created_at, Job.page_count)
        .join(User, User.id == Job.user_id)
        .where(Job.status == JobStatus.QUEUED, User.is_allowed.is_(True))
    ).all()
    queued = {
        row.id: fair_queue.Queued(
            row.id,
            row.user_id,
            int(row.attempts or 0),
            as_utc(row.created_at) or utcnow(),
            int(row.page_count or 0),
        )
        for row in rows
    }
    if job.id not in queued:  # the owner is no longer allowed: still say where it would be
        queued[job.id] = fair_queue.Queued(
            job.id,
            job.user_id,
            int(job.attempts or 0),
            as_utc(job.created_at) or utcnow(),
            int(job.page_count or 0),
        )
    served = fair_queue.last_started(session, {q.user_id for q in queued.values()})
    ordered = fair_queue.queue_order(list(queued.values()), served)
    return ordered[: [q.id for q in ordered].index(job.id)]


# Estimates of the wait use the last few finished jobs (seconds per page, start to end);
# with none to go by, this many seconds per page (a little slower than measured: a
# 16-page section took 51 s, a 1-page handout about 30 s). Not a setting yet.
RECENT_JOBS = 20
DEFAULT_SECONDS_PER_PAGE = 10.0


def seconds_per_page(session: Session) -> float:
    """Recent finished jobs' conversion time per page (content-free: times and counts)."""
    rows = session.execute(
        select(Job.started_at, Job.finished_at, Job.page_count)
        .where(
            Job.status == JobStatus.DONE,
            Job.started_at.is_not(None),
            Job.finished_at.is_not(None),
            Job.page_count > 0,
        )
        .order_by(Job.finished_at.desc())
        .limit(RECENT_JOBS)
    ).all()
    seconds = pages = 0.0
    for started, finished, count in rows:
        start, end = as_utc(started), as_utc(finished)
        if start is not None and end is not None and end > start:
            seconds += (end - start).total_seconds()
            pages += count
    return seconds / pages if pages else DEFAULT_SECONDS_PER_PAGE


def queue_wait(session: Session, job: Job) -> tuple[int, float]:
    """(jobs ahead that are this user's, estimated seconds until this one is done) for a
    queued job: the pages ahead (in `Worker.claim` order, like `queue_ahead`), what is
    left of the jobs converting now, and its own pages, at the recent pace."""
    ahead = [(q.user_id, q.page_count) for q in _jobs_ahead(session, job)]
    running = session.execute(
        select(Job.page_count, Job.pages_done).where(Job.status == JobStatus.RUNNING)
    ).all()
    pages = sum(count or 0 for _, count in ahead) + (job.page_count or 0)
    pages += sum(max(0, (count or 0) - (done or 0)) for count, done in running)
    yours = sum(1 for user_id, _ in ahead if user_id == job.user_id)
    return yours, pages * seconds_per_page(session)


def last_decision(session: Session, job: Job) -> date | None:
    """The day (UTC) of the job's most recent review decision, if any."""
    when = as_utc(
        session.scalar(select(func.max(ReviewEdit.created_at)).where(ReviewEdit.job_id == job.id))
    )
    return when.date() if when is not None else None


def job_view(
    session: Session,
    job: Job,
    *,
    with_summary: bool = False,
    with_page: bool = False,
    role: str = "owner",
    context: states.Context | None = None,
) -> JobView:
    """The job's view model. `with_summary`: the counts from its result (the job page and
    the review list); `with_page`: also the job page's own data (`job_page.PageData`),
    the queue estimate and the live progress. `role` is what the person looking is to the
    document (`web.shares`); someone it was shared with sees none of the owner's other parts
    and is told who shared it. `context` is what the state reads beyond the job (today's
    budget, the deployment's options); with `with_page` the state is worked out."""
    owner = role == "owner"
    ahead = queue_ahead(session, job) if job.status == JobStatus.QUEUED else 0
    yours, wait_s = (
        queue_wait(session, job) if with_page and job.status == JobStatus.QUEUED else (0, None)
    )
    since = None
    slow_s = 0.0
    if with_page and job.status == JobStatus.QUEUED and not job.retries:
        since = as_utc(job.created_at)
        slow_s = job_page.slow_after(wait_s)
    elif with_page and job.status == JobStatus.RUNNING:
        since = as_utc(job.started_at) or as_utc(job.created_at)
        pace = seconds_per_page(session)
        slow_s = job_page.slow_after((job.page_count or 0) * pace)
    page = None
    carried = None
    applied = None
    if with_page and job.status == JobStatus.DONE and job.result_json:
        result = DocumentResult.model_validate(job.result_json)
        applied = notation.applied_phrases(result.notation_applied)
        page = job_page.page_data(result, last_decision(session, job), job.filename)
        carried = carry_view(job, result)
    # Only a finished job has a summary (the job page shows it for no other); a poll of one
    # that is waiting or converting does not load its result.
    done = job.status == JobStatus.DONE
    summary = summarise(job.result_json) if (with_summary or with_page) and done else None
    view = JobView(
        id=job.id,
        filename=job.filename,
        status=str(job.status),
        page_count=job.page_count,
        pages_done=job.pages_done,
        created_at=as_utc(job.created_at),
        finished_at=as_utc(job.finished_at),
        retain_until=as_utc(job.retain_until),
        opt_in=job.opt_in_retention,
        error_code=job.error_code,
        error_detail=job.error_detail if job.status == JobStatus.FAILED else None,
        cost_usd=job.settled_usd,  # spent; what its calls in flight hold is `job.held_usd`
        model_calls=job.model_calls,
        queue_ahead=ahead,
        retries=job.retries or 0,
        summary=summary,
        result_version=job.result_version or 0,
        ahead_yours=yours,
        wait_s=wait_s,
        waiting_since=since,
        slow_after_s=slow_s,
        progress=job.progress_json if job.status == JobStatus.RUNNING else None,
        page=page,
        title=documents.document_title(job).text,
        title_override=job.title_override,
        contact_line=job.contact_line,
        retention_mode=job.retention_mode,
        exam_mode=bool(job.exam_mode),
        carry=carried,
        carried_ids=_carried_ids(job),
        part_label=job.part_label or "",
        office_lines=office_census.read_lines(office_census.parse(job.office_json))
        if with_page
        else [],
        is_sample=bool(job.is_sample),
        sample=sample_view() if job.is_sample and with_page else None,
        parts=part_links(session, job, context) if with_page and job.part_group and owner else [],
        combinable=len(combine_rules.kept_parts(session, job)) if with_page and owner else 0,
        role=role,
        shared_by=_owner_name(session, job) if not owner else "",
        exportable=with_page and output.export_state(job).key == output.READY,
        notation_applied=applied,
        version_line=output.stamp_of(job).line if done and output.may_take_draft(role) else "",
    )
    if with_page:
        # Counts are read only for a finished job: a stopped or waiting job may still hold an
        # earlier result, and its decisions are not open while it is not finished (F-314).
        counts = states.counts_of(summary.checked if summary and done else None)
        line = (
            job_page.queue_sentence(ahead, yours, wait_s) if job.status == JobStatus.QUEUED else ""
        )
        base = context or states.Context()
        view.state = states.state_of(
            states.Facts.of(job),
            counts,
            viewer=view.audience,
            context=states.Context(
                budget_closed=base.budget_closed,
                waiting_line=line,
                settings=base.settings,
                now=base.now,
            ),
        )
    return view


def audience_of(role: str | None) -> str:
    """Who is reading a document's pages, from what they are to it (`web.shares`): `owner`
    (words written to the person who converted it: where to post it, what they decided),
    `reviewer` (a Review right: reads it and checks it with the owner) or `reader` (a View
    right, or a role this function does not know: the stricter reading). A page or notice
    written for the owner is shown only to the `owner` audience. The fourth audience, the
    student, is the one the student file and its exports are written for."""
    return {"owner": "owner", "review": "reviewer"}.get(role or "", "reader")


def _owner_name(session: Session, job: Job) -> str:
    """Who owns the document, for the person it was shared with: their name only (an address
    is for the owner to see), else "" (the pages then say "the owner" or "shared with you",
    never a name that looks like a placeholder)."""
    person = session.get(User, job.user_id)
    return (person.name if person is not None and person.name else None) or ""


def carry_view(job: Job, result: DocumentResult) -> CarryView | None:
    """The carry-over card's data for a revised document, or None (it revises nothing, or
    nothing was recorded: the earlier version was gone when this one finished)."""
    if not job.revises_job_id:
        return None
    data = job.carried_json
    name = data.get("from_name") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(name, str):
        return CarryView("", carry_rules.summary(carry_rules.CarryReport(), result))
    report = carry_rules.CarryReport.from_json(data)
    summary = carry_rules.summary(report, result)
    fixes = {f.element_id: f for f in result.fixes}
    waiting = [
        i for i in [*summary.changed_ids, *summary.new_ids] if i in fixes and fixes[i].pending
    ]
    return CarryView(name, summary, waiting[0] if waiting else None)


def _carried_ids(job: Job) -> frozenset[str]:
    data = job.carried_json
    ids = data.get("carried") if isinstance(data, dict) else None
    return frozenset(i for i in ids if isinstance(i, str)) if isinstance(ids, list) else frozenset()


def part_links(session: Session, job: Job, context: states.Context | None = None) -> list[PartLink]:
    """The parts of the same larger file that the person still keeps, in page order (the
    pages are the label's first number), each with its state as the list shows it."""
    rows = session.execute(
        select(Job.id, Job.part_label, Job.status, Job.created_at)
        .where(
            Job.part_group == job.part_group,
            Job.user_id == job.user_id,
            Job.retain_until > utcnow(),
        )
        .order_by(Job.created_at, Job.id)
    ).all()

    def first_page(label: str | None) -> int:
        digits = "".join(ch if ch.isdigit() else " " for ch in (label or "")).split()
        return int(digits[0]) if digits else 0

    ordered = sorted(rows, key=lambda r: (first_page(r.part_label), r.created_at))
    found = states.load(
        session,
        [r.id for r in ordered],
        context=states.Context(budget_closed=bool(context and context.budget_closed)),
    )
    return [
        PartLink(r.id, r.part_label or "", str(r.status), r.id == job.id, found.get(r.id))
        for r in ordered
    ]


@dataclass
class CombineRow:
    """One part on the combine page: its pages, its state (SW1), how many decisions are open,
    where to make the first one, and whether the combined file would hold it."""

    id: str
    label: str
    state: states.State | None
    open: int
    review_href: str
    ready: bool


@dataclass
class CombineView:
    """The combine page's facts for one source: a row per part the person keeps, in page
    order, what the file would hold (`ready`, `left_out`) and, when it could not yet be made,
    what is still needed (`needed`)."""

    rows: list[CombineRow]
    ready: int
    left_out: int
    needed: str
    language: str = ""  # the one check on the file's language (`web.combine.language_check`)


def combine_view(session: Session, job: Job) -> CombineView:
    """The rows of the combine page, for the owner of `job`, a part of a larger file. Every
    part is a row, whatever its state: the page answers even when nothing can be joined yet.
    The state and the order are `part_links`'; the decisions of a part that needs some are
    counted from its stored count, and the first one is the review screen's own queue."""
    from readablestem.web import queue

    links = part_links(session, job)
    kept = combine_rules.kept_parts(session, job)
    ready_ids = {k.job.id for k in kept}
    waiting = [p.id for p in links if p.state is not None and p.state.key == "needs_decisions"]
    first: dict[str, str] = {}
    counts: dict[str, int] = {}
    if waiting:
        for row in session.execute(
            select(Job.id, Job.open_decisions, Job.result_json).where(Job.id.in_(waiting))
        ):
            counts[row.id] = int(row.open_decisions or 0)
            if row.result_json:
                found = queue.first_open(
                    queue.decisions(DocumentResult.model_validate(row.result_json))
                )
                if found is not None:
                    first[row.id] = found.key
    rows = [
        CombineRow(
            p.id,
            p.label,
            p.state,
            counts.get(p.id, 0),
            f"/jobs/{p.id}/review" + (f"?item={first[p.id]}" if p.id in first else "")
            if p.id in counts
            else "",
            p.id in ready_ids,
        )
        for p in links
    ]
    ready = sum(1 for r in rows if r.ready)
    return CombineView(
        rows, ready, len(rows) - ready, _still_needed(rows), combine_rules.language_check(kept)
    )


def _still_needed(rows: list[CombineRow]) -> str:
    """What the parts that are not ready still need, in one sentence ("" when none is
    missing): decisions to make, conversions still running, stopped parts."""
    from readablestem.pipeline.checked_words import plural

    left = [r for r in rows if not r.ready]
    decisions = sum(1 for r in left if r.state is not None and r.state.key == "needs_decisions")
    working = sum(1 for r in left if r.state is not None and r.state.group == states.WORK)
    stopped = sum(1 for r in left if r.state is not None and r.state.tone == states.FAILED)
    needs = []
    if decisions:
        needs.append(f"make the decisions in {plural(decisions, 'part')}")
    if working:
        needs.append(f"wait for {plural(working, 'part')} still converting")
    if stopped:
        needs.append(f"look at {plural(stopped, 'stopped part')}")
    if not needs and left:
        needs.append("open each part that is not ready to see what it needs")
    return (
        (needs[0].capitalize() + "".join(f"; then {n}" for n in needs[1:]) + ".") if needs else ""
    )


def sample_view() -> SampleView | None:
    """The sample's attribution, or None when its files are not in this build."""
    from readablestem.web import sample

    if not sample.available():
        return None
    recorded = sample.load()
    return SampleView(
        title=recorded.title,
        source_url=recorded.source_url,
        licence=recorded.licence,
        licence_url=recorded.licence_url,
        authors=", ".join(recorded.authors),
    )
