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
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from readablestem import __version__
from readablestem.core import triage
from readablestem.core.types import (
    DocumentResult,
    Element,
    Fix,
    Kind,
    Status,
)
from readablestem.web import job_page
from readablestem.web.database import as_utc
from readablestem.web.messages import explain_check, explain_reason, severity_label
from readablestem.web.review import BULK_KEY, MODEL_READ_KEY, UNDO_ACTION, item_labels
from readablestem.web.views import KIND_LABELS

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
    # Edits no decision on a proposal covers (T3-5, T3-16).
    "describe": "Described the image in words",
    "edit_text": "Changed the words of the text",
    "make_heading": "Made a heading of a paragraph",
    "add_figure": "Added a picture the tool missed, with the person's own alt text",
    "add_equation": "Marked an equation the tool missed; the tool read it and a person checked",
    "omit_page": "Left the page out of the student file and of the review",
    "restore_page": "Put the page back into the student file and the review",
    "carried": "Carried over from the earlier version of the document",
    UNDO_ACTION: "Undid the decision before",
}
# A decision with a mark in its `after` (web.review.BULK_KEY, MODEL_READ_KEY) says more.
BULK_WORDS = "Accepted, together with the other clean tables, in one action"
MODEL_READ_WORDS = "Table cell read by the AI service at the person's request, then saved"
# What a carried edit was (web.carry_edits.CARRIED_KEY).
CARRIED_EDIT_WORDS = {
    "text": "change to the words of the text",
    "heading": "paragraph made a heading",
    "picture": "picture added by hand",
    "equation": "equation marked by hand",
    "page_left_out": "page left out",
}
# What the tool had, for an edit on something that is not a proposal of the tool.
STRUCTURE_TOOL_HAD = {
    "edit_text": "Text as read from the PDF",
    "make_heading": "A paragraph",
    "add_figure": "Not found by the tool",
    "add_equation": "Not found by the tool",
    "omit_page": "A page of the file",
    "restore_page": "A page left out",
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
    # What the instructor did by hand that the tool did not propose, as it stands now.
    by_hand: dict[str, int] = field(default_factory=dict)
    left_out_pages: list[int] = field(default_factory=list)  # 1-based


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


def _decision_words(edit: Any) -> str:
    """What the decision was, in words, from the stored action and the marks its `after` keeps."""
    action = str(edit.action)
    after = edit.after if isinstance(edit.after, dict) else {}
    if action == "accept" and after.get(BULK_KEY):
        return BULK_WORDS
    if action == "edit" and after.get(MODEL_READ_KEY):
        return MODEL_READ_WORDS
    if action == "carried" and after.get("carried_edit") in CARRIED_EDIT_WORDS:
        return f"Carried over from the earlier version: {CARRIED_EDIT_WORDS[after['carried_edit']]}"
    return ACTION_WORDS.get(action, "Changed")


def _page_number(element_id: str) -> int | None:
    """1-based page of a page-level edit recorded under "page-3"."""
    head, _, tail = element_id.partition("-")
    return int(tail) if head == "page" and tail.isdecimal() else None


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
    for line in by_hand_not_checked(result):
        out.append(line)
    return out


ADDED_ID = re.compile(r"p\d+-x\d+")


def by_hand_counts(result: DocumentResult) -> dict[str, int]:
    """What the instructor added or described by hand, from the stored result: pictures and
    equations added (their ids have an "x"), images described in words."""
    elements = [*result.elements, *(e for held in result.omitted for e in held.elements)]
    fixes = [*result.fixes, *(f for held in result.omitted for f in held.fixes)]
    added = [e for e in elements if ADDED_ID.fullmatch(e.id)]
    counts = {
        "Pictures added by hand": sum(1 for e in added if e.kind is Kind.FIGURE),
        "Equations marked by hand": sum(1 for e in added if e.kind is Kind.EQUATION),
        "Images described in words": sum(1 for f in fixes if f.described),
    }
    return {name: n for name, n in counts.items() if n}


def by_hand_not_checked(result: DocumentResult) -> list[str]:
    lines = []
    counts = by_hand_counts(result)
    if counts.get("Images described in words"):
        lines.append(
            f"{counts['Images described in words']} images are described in words by the "
            "person. The tool checked the description's form (its length, no web address, not "
            "a file name), not whether it says what the image shows."
        )
    added = counts.get("Pictures added by hand", 0)
    if added:
        lines.append(
            f"{added} pictures were added by hand, with the person's own alt text, which the "
            "tool did not check against the picture."
        )
    if result.omitted:
        pages = ", ".join(str(n) for n in sorted(o.page + 1 for o in result.omitted))
        lines.append(
            f"Page {pages} {'was' if len(result.omitted) == 1 else 'were'} left out by the "
            "person: not reviewed, and not in the student file."
        )
    return lines


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
    fixes: dict[str, Fix] = {}
    for fix in result.fixes:
        fixes.setdefault(fix.element_id, fix)
    # What the pages left out hold is still a part of the document: a decision made before
    # the page was left out names an item that is there.
    for held in result.omitted:
        for e in held.elements:
            elements.setdefault(e.id, e)
        for f in held.fixes:
            fixes.setdefault(f.element_id, f)
    labels = item_labels(result)
    counts = job_page.breakdown(result.fixes)
    decisions = []
    for edit in sorted(edits, key=lambda e: (as_utc(e.created_at) or e.created_at, e.id)):
        fix = fixes.get(edit.element_id)
        element = elements.get(edit.element_id)
        kind = fix.kind.value if fix is not None else (element.kind.value if element else "")
        page_edit = _page_number(edit.element_id)
        words = KIND_LABELS.get(Kind(kind), kind.replace("_", " ").title()) if kind else "Item"
        item = labels.get(edit.element_id) or (f"Page {page_edit}" if page_edit else words)
        before = (edit.before or {}).get("status", "") or STRUCTURE_TOOL_HAD.get(edit.action, "")
        decisions.append(
            DecisionRow(
                when=as_utc(edit.created_at) or edit.created_at,
                item=item,
                page=page_edit if page_edit is not None else _page_of(elements, edit.element_id),
                tool_had=TOOL_STATUS_WORDS.get(str(before), str(before)),
                decision=_decision_words(edit),
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
        by_hand=by_hand_counts(result),
        left_out_pages=sorted(o.page + 1 for o in result.omitted),
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
    for name, n in record.by_hand.items():
        row("summary", name, detail=n)
    if record.left_out_pages:
        pages = " ".join(str(n) for n in record.left_out_pages)
        row("summary", "Pages left out of the student file", detail=pages)
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
