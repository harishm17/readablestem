"""The review record of a document, for the accessibility office (T3-6).

A page to print and a CSV, made from what the app has stored about one finished job: the
stored result, the review decisions (`ReviewEdit`: who, when, what the tool had and what the
decision made of it) and the settings the instructor made. It says what the tool did, what a
person decided and when, which parts were not converted, which automatic checks ran, and,
in plain words, what was not checked.

It is a record, not a verdict. It never says the document meets a standard or a law, and
the word "certification" is not used: the statement at its head says what it is. Nothing
here is invented to fill a gap: a count is a count of the stored fixes, a name is the
owner's sign-in name, a time is the time the decision was stored (UTC).

The record is made on request and not stored. It is built from data that goes with the
document (`ReviewEdit` rows are deleted with the job), so the page tells its reader to
keep a copy: print it or save the CSV before the document is deleted.

Document-derived text: the title and file name are the owner's and are shown only to them;
cells that a spreadsheet would run as a formula are written with a quote in front.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from a11y_stem import __version__
from a11y_stem.core import triage
from a11y_stem.core.types import (
    DocumentResult,
    Element,
    Fix,
    Kind,
    Status,
)
from a11y_stem.web import job_page
from a11y_stem.web.database import as_utc
from a11y_stem.web.messages import explain_check, explain_reason, severity_label
from a11y_stem.web.review import UNDO_ACTION, item_labels
from a11y_stem.web.views import KIND_LABELS

STATEMENT = (
    "This record lists what the tool did to this document, what a person decided and when, "
    "which parts were not converted, and which automatic checks ran. It does not say that "
    "the document meets any standard or law, and nobody at the tool has looked at the "
    "document. The accessibility office makes that judgement."
)
KEEP_NOTE = (
    "This record is made from what the tool keeps about the document, and that is deleted "
    "with the document. Print this page or save the CSV file to keep it."
)
# The decision words, by the `ReviewEdit.action` and the status the decision left.
ACTION_WORDS = {
    "accept": "Accepted",
    "edit": "Edited",
    "reject": "Rejected, original kept",
    "add_data_table": "Added a data table",
    UNDO_ACTION: "Undid the decision before",
}
TOOL_STATUS_WORDS = {
    "needs_review": "Asked for a decision",
    "accepted": "Accepted by the tool",
    "abstained": "No proposal made",
    "failed": "Not converted (error)",
    "edited": "Already edited",
    "rejected": "Already rejected",
}
# What the tool does not know, whatever the document. Plain facts about the method.
ALWAYS_NOT_CHECKED = (
    "Whether the file reads well with a screen reader. Nobody has listened to it with NVDA, "
    "JAWS or VoiceOver.",
    "The axe accessibility test. It runs on the tool's own test documents, not on yours.",
    "Whether each part says what the PDF meant. The automatic checks look at the structure "
    "of the file (language, title, headings, lists, tables, image descriptions), not at "
    "the meaning.",
    "How the file looks in your course site (for example Canvas). It has not been tried "
    "in a live course.",
    "The PDF itself. This record is about the HTML file made from it.",
)


@dataclass(frozen=True)
class DecisionRow:
    when: datetime
    item: str  # "Equation 3"
    page: int | None  # 1-based
    tool_had: str
    decision: str
    by: str


@dataclass(frozen=True)
class ImageRow:
    item: str
    page: int | None
    why: str


@dataclass(frozen=True)
class CheckRow:
    severity: str
    text: str
    code: str


@dataclass(frozen=True)
class Record:
    title: str
    filename: str
    job_ref: str
    pages: int
    converted_at: datetime | None
    made_at: datetime
    tool: str
    owner: str
    language: str
    title_chosen: bool
    contact_set: bool
    counts: job_page.Breakdown
    by_kind: dict[str, int]
    decisions: list[DecisionRow] = field(default_factory=list)
    kept_as_images: list[ImageRow] = field(default_factory=list)
    checks_run: int = 0
    failed_checks: list[CheckRow] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)


def long_when(value: datetime | None) -> str:
    """ "9 October 2026, 14:03 UTC"."""
    if value is None:
        return ""
    return f"{value.day} {value:%B %Y}, {value:%H:%M} UTC"


def iso_when(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else ""


def _page_of(elements: dict[str, Element], element_id: str) -> int | None:
    element = elements.get(element_id)
    return element.page + 1 if element is not None else None


def _decision_words(action: str) -> str:
    return ACTION_WORDS.get(action, "Changed")


def not_checked_list(result: DocumentResult, counts: job_page.Breakdown) -> list[str]:
    """What was not checked: the fixed facts plus the parts a person did not look at."""
    out = list(ALWAYS_NOT_CHECKED)
    by_tool = Counter(
        f.kind for f in result.fixes if f.status is Status.ACCEPTED and not f.decided_by_reviewer
    )
    for kind, words in (
        (Kind.EQUATION, "equations"),
        (Kind.FIGURE, "figure descriptions"),
        (Kind.HEADING, "headings"),
        (Kind.TABLE, "tables"),
    ):
        if by_tool[kind]:
            out.append(
                f"{by_tool[kind]} {words} were accepted by the tool after its own automatic "
                "checks, with no decision from a person."
            )
    if counts.waiting:
        out.append(
            f"{counts.decisions} decisions were still open when this record was made. "
            "The file has the tool's proposals for them."
        )
    if counts.as_image:
        out.append(
            f"{counts.as_image} parts are shown as pictures of the original. Screen "
            "readers cannot read them."
        )
    return out


def build(
    *,
    result: DocumentResult,
    edits: list[Any],
    who: dict[str, str],
    owner: str,
    filename: str,
    title: str,
    job_ref: str,
    converted_at: datetime | None,
    made_at: datetime,
    title_chosen: bool,
    contact_set: bool,
) -> Record:
    """The record from a finished job's parts. `edits` are its `ReviewEdit` rows (any
    order); `who` maps a user id to the name shown for them."""
    elements = {e.id: e for e in result.elements}
    labels = item_labels(result)
    fixes: dict[str, Fix] = {}
    for fix in result.fixes:
        fixes.setdefault(fix.element_id, fix)
    counts = job_page.breakdown(result.fixes)
    decisions = []
    for edit in sorted(edits, key=lambda e: (as_utc(e.created_at) or e.created_at, e.id)):
        fix = fixes.get(edit.element_id)
        kind = fix.kind.value if fix is not None else ""
        item = labels.get(edit.element_id) or (
            KIND_LABELS.get(Kind(kind), kind.title()) if kind else "Item"
        )
        before = (edit.before or {}).get("status", "")
        decisions.append(
            DecisionRow(
                when=as_utc(edit.created_at) or edit.created_at,
                item=item,
                page=_page_of(elements, edit.element_id),
                tool_had=TOOL_STATUS_WORDS.get(str(before), ""),
                decision=_decision_words(edit.action),
                by=who.get(edit.user_id, owner),
            )
        )
    images = [
        ImageRow(
            item=labels.get(f.element_id, KIND_LABELS.get(f.kind, f.kind.value.title())),
            page=_page_of(elements, f.element_id),
            why=explain_reason(f.reason).text if f.reason else "",
        )
        for f in result.fixes
        if job_page.shown_as_image(f)
    ]
    failed = [
        CheckRow(severity_label(c), explain_check(c).text, explain_check(c).code)
        for c in result.html_checks
        if not c.passed
    ]
    open_items = [
        labels.get(unit[0].element_id, unit[0].kind.value.title())
        for unit in triage.units(f for f in result.fixes if f.pending)
    ]
    return Record(
        title=title,
        filename=filename,
        job_ref=job_ref,
        pages=result.stats.pages,
        converted_at=converted_at,
        made_at=made_at,
        tool=f"a11y-stem {__version__}, pipeline {result.pipeline_version}",
        owner=owner,
        language=job_page.language_line(result),
        title_chosen=title_chosen,
        contact_set=contact_set,
        counts=counts,
        by_kind=dict(Counter(job_page.word_kind(f) for f in result.fixes)),
        decisions=decisions,
        kept_as_images=images,
        checks_run=len(result.html_checks),
        failed_checks=failed,
        open_items=open_items,
        not_checked=not_checked_list(result, counts),
    )


# --------------------------------------------------------------------------- the CSV

CSV_HEADER = (
    "section",
    "item",
    "page",
    "tool_had",
    "decision",
    "by",
    "when_utc",
    "detail",
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> str:
    """A cell a spreadsheet will not run as a formula: text that starts like one gets a
    quote in front."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def to_csv(record: Record) -> str:
    """The record as one CSV table (UTF-8; the route adds a byte order mark so Excel reads
    the letters right). Each row has a `section`: document, summary, decision, open,
    not_converted, check, not_checked."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)

    def row(section: str, item="", page="", tool_had="", decision="", by="", when="", detail=""):
        writer.writerow(
            [safe_cell(v) for v in (section, item, page, tool_had, decision, by, when, detail)]
        )

    row("document", "Title", detail=record.title)
    row("document", "File name", detail=record.filename)
    row("document", "Pages", detail=record.pages)
    row("document", "Converted on", when=iso_when(record.converted_at))
    row("document", "Converted by", by=record.owner)
    row("document", "Tool", detail=record.tool)
    row("document", "Language", detail=record.language)
    row("document", "Record made on", when=iso_when(record.made_at))
    row("document", "Reference", detail=record.job_ref)
    c = record.counts
    row("summary", "Parts found", detail=c.items)
    for kind, n in record.by_kind.items():
        row("summary", f"Found: {kind}", detail=n)
    row("summary", "Accepted by the tool, no decision from a person", detail=c.accepted)
    row("summary", "Decided by a person", detail=c.decided)
    row("summary", "Decisions still open", detail=c.decisions)
    row("summary", "Shown as pictures of the original", detail=c.as_image)
    for d in record.decisions:
        row("decision", d.item, d.page or "", d.tool_had, d.decision, d.by, iso_when(d.when))
    for item in record.open_items:
        row("open", item, decision="Waiting for a decision")
    for i in record.kept_as_images:
        row("not_converted", i.item, i.page or "", detail=i.why)
    row("check", "Automatic checks of the HTML file that ran", detail=record.checks_run)
    for ch in record.failed_checks:
        row("check", ch.code, tool_had=ch.severity, decision="Did not pass", detail=ch.text)
    for line in record.not_checked:
        row("not_checked", detail=line)
    return out.getvalue()
