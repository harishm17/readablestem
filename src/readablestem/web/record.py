"""The review record of a document, for the accessibility office (T3-6).

A page to print and a CSV, made from what the app has stored about one finished job: the
stored result, the review decisions (`ReviewEdit`: who, when, what the tool had and what the
decision made of it) and the settings the instructor made. It says what the tool did, what a
person decided and when, which parts were not converted, which structure checks ran, and,
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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from readablestem import __version__
from readablestem.core import page_checks, triage
from readablestem.core.types import (
    DocumentResult,
    Element,
    Fix,
    Kind,
    Status,
)
from readablestem.pipeline import checked, checked_words
from readablestem.pipeline.checked import CheckedSummary
from readablestem.web import exam, job_page
from readablestem.web.database import as_utc
from readablestem.web.dates import format_when, machine_date
from readablestem.web.messages import (
    KIND_LABELS,
    explain_check,
    explain_notice,
    explain_reason,
    severity_label,
)
from readablestem.web.review import (
    BULK_KEY,
    CHOICE_KEY,
    GROUP_KINDS,
    INVENTORY_KEY,
    MODEL_READ_KEY,
    UNDO_ACTION,
    WITNESS_KEY,
    WRITE_KEY,
    item_labels,
)

STATEMENT = (
    "This record lists what the tool did to this document, what a person decided and when, "
    "which parts were not converted, and which structure checks ran. It does not say that "
    "the document meets any standard or law, and nobody at the tool has looked at the "
    "document. The accessibility office makes that judgment."
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
    "read_again": "Had the tool read the equation again",
    "not_equation": "Gave the equation back to the text",
    "carried": "Carried over from the earlier version of the document",
    "relabel": "Changed a symbol in several equations at once",
    UNDO_ACTION: "Undid the decision before",
}
# A decision with a mark in its `after` (web.review.BULK_KEY, MODEL_READ_KEY) says more.
CHOICE_WORDS = "Edited: chose the reading a second reader gave for one symbol"
WITNESS_WORDS = "Edited: chose the other reading of this equation"
BULK_WORDS = "Accepted, together with the other clean tables, in one action"
# The bulk accepts of equations and figures (web.bulk_accept) keep their kind under BULK_KEY.
BULK_KIND_WORDS = {
    "equations": "Accepted, together with the other equations whose checks passed, in one action",
    "figures": "Accepted, together with the other figures with generated alt text, in one action",
}
# One row for a symbol changed in many equations from the Symbols screen (web.inventory).
RELABEL_WORDS = (
    "Changed {was} to {now} in {n} equations at once, from the list of symbols. Each equation "
    "stayed waiting for a decision."
)
MAX_NAMED_EQUATIONS = 6
MODEL_READ_WORDS = "Table cell read by the AI service at the person's request, then saved"
# What a carried edit was (web.carry_edits.CARRIED_KEY).
CARRIED_EDIT_WORDS = {
    "text": "change to the words of the text",
    "heading": "paragraph made a heading",
    "picture": "picture added by hand",
    "equation": "equation marked by hand",
    "page_left_out": "page left out",
}
# The actions that change a page and settle no decision: counted as changes to pages, apart
# from the decisions a person made (`Record.count_line`).
PAGE_CHANGES = frozenset(
    {
        "edit_text",
        "make_heading",
        "add_figure",
        "add_equation",
        "omit_page",
        "restore_page",
        "read_again",
        "not_equation",
    }
)
# What the tool had, for an edit on something that is not a proposal of the tool.
STRUCTURE_TOOL_HAD = {
    "edit_text": "Text as read from the PDF",
    "make_heading": "A paragraph",
    "add_figure": "Not found by the tool",
    "add_equation": "Not found by the tool",
    "omit_page": "A page of the file",
    "restore_page": "A page left out",
}
# The file types a download can be (`web.events.FORMATS`), in words for the record.
FORMAT_WORDS = {
    "html": "HTML file",
    "canvas": "Canvas zip",
    "cartridge": "Common Cartridge",
    "docx": "Word",
    "epub": "EPUB",
}
TOOL_STATUS_WORDS = {
    "needs_review": "Asked for a decision",
    "accepted": "Accepted by the tool",
    "abstained": "No proposal made",
    "failed": "Not converted (error)",
    "edited": "Already edited",
    "rejected": "Already rejected",
}
# What the tool does not know, whatever the document. Plain facts about the method: the
# summary's (`pipeline.checked_words.NEVER_CHECKED`), then what only the record says.
ALWAYS_NOT_CHECKED = (
    *checked_words.NEVER_CHECKED,
    "How the file looks in your course site (for example Canvas). It has not been tried "
    "in a live course.",
    "The Word and EPUB files. They are made from the HTML file, and the structure checks "
    "above are of the HTML file. Nobody has checked how they open in Word or a reading app.",
)


def source_not_checked(origin: str) -> str:
    """The last fixed fact: the file this one was made from was not checked either."""
    noun = checked_words.source_noun(origin)
    return f"The {noun} itself. This record is about the HTML file made from it."


@dataclass(frozen=True)
class ItemRef:
    """An item as the record names it: its words, the review screen's address for it when the
    reader may open that screen and it is still there, and a note when it is not."""

    text: str  # "Equation 3"
    href: str = ""
    note: str = ""  # "no longer in the document"


@dataclass(frozen=True)
class DecisionRow:
    when: datetime
    item: str  # "Equation 3": the words, for the CSV
    page: int | None  # 1-based
    tool_had: str
    decision: str
    by: str  # as the page names the person to its reader ("you")
    by_file: str = ""  # as the CSV names them: it leaves the app, so there is no "you"
    refs: tuple[ItemRef, ...] = ()  # the items `item` names, each with its link
    more: int = 0  # items past the ones named


@dataclass(frozen=True)
class ImageRow:
    item: str
    page: int | None
    why: str
    ref: ItemRef | None = None


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
    csv_actors: bool  # the CSV names who decided only for the owner
    language: str
    title_chosen: bool
    contact_set: bool
    exam_mode: bool
    counts: job_page.Breakdown
    checked: CheckedSummary  # what was converted, checked and reviewed (`pipeline.checked`)
    by_kind: dict[str, int]
    decisions: list[DecisionRow] = field(default_factory=list)
    kept_as_images: list[ImageRow] = field(default_factory=list)
    checks_run: int = 0
    failed_checks: list[CheckRow] = field(default_factory=list)
    open_items: list[ItemRef] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)
    # What the instructor did by hand that the tool did not propose, as it stands now.
    by_hand: dict[str, int] = field(default_factory=dict)
    left_out_pages: list[int] = field(default_factory=list)  # 1-based
    # The file types downloaded so far, in words; None when the app is not recording
    # downloads (EVENTS_ENABLED off), so the record does not say "none" when it cannot know.
    downloaded: list[str] | None = None
    # The job's state (`web.states`) as every other page says it, and its gap notes (what
    # was not converted): (title, text).
    state: str = ""
    gaps: list[tuple[str, str]] = field(default_factory=list)
    changes: int = 0  # changes a person made to pages (`PAGE_CHANGES`)

    @property
    def count_line(self) -> str:
        """ "N decisions made by a person. M changes to pages." The page and the CSV say it
        with these two numbers."""
        decided = self.counts.decided
        return (
            f"{decided} {'decision' if decided == 1 else 'decisions'} made by a person. "
            f"{self.changes} {'change' if self.changes == 1 else 'changes'} to pages."
        )


long_when = format_when  # "October 9, 2026, 14:03 UTC" (`web.dates`)
iso_when = machine_date  # the CSV keeps ISO UTC: it is a file for machines


def _page_of(elements: dict[str, Element], element_id: str) -> int | None:
    element = elements.get(element_id)
    return element.page + 1 if element is not None else None


def _decision_words(edit: Any) -> str:
    """What the decision was, in words, from the stored action and the marks its `after` keeps."""
    action = str(edit.action)
    after = edit.after if isinstance(edit.after, dict) else {}
    if action == "accept" and after.get(BULK_KEY):
        kind = after[BULK_KEY]
        return BULK_KIND_WORDS.get(kind, BULK_WORDS) if isinstance(kind, str) else BULK_WORDS
    if action == "edit" and after.get(MODEL_READ_KEY):
        return MODEL_READ_WORDS
    if action == "edit" and after.get(CHOICE_KEY):
        return CHOICE_WORDS
    if action == "edit" and after.get(WITNESS_KEY):
        return WITNESS_WORDS
    if action == "carried" and after.get("carried_edit") in CARRIED_EDIT_WORDS:
        return f"Carried over from the earlier version: {CARRIED_EDIT_WORDS[after['carried_edit']]}"
    return ACTION_WORDS.get(action, "Changed")


def _relabel_mark(edit: Any) -> dict[str, Any] | None:
    """The `INVENTORY_KEY` mark of an edit made from the Symbols screen, or None."""
    after = edit.after if isinstance(edit.after, dict) else {}
    mark = after.get(INVENTORY_KEY)
    return mark if isinstance(mark, dict) else None


def _relabel_rows(
    edits: list[Any],
    labels: dict[str, str],
    who: dict[str, str],
    file_who: dict[str, str],
    owner: str,
    ref: Callable[[str, str], ItemRef],
) -> list[DecisionRow]:
    """One row for each write that changed a symbol in many equations: the writes' edits
    (one per equation, what Undo needs) are one decision of the person."""
    from readablestem.web import inventory

    writes: dict[Any, list[Any]] = {}
    for edit in edits:
        after = edit.after if isinstance(edit.after, dict) else {}
        writes.setdefault((edit.user_id, after.get(WRITE_KEY)), []).append(edit)
    rows = []
    for group in writes.values():
        first = group[0]
        mark = _relabel_mark(first) or {}
        named = [labels.get(e.element_id, "Equation") for e in group]
        shown = ", ".join(named[:MAX_NAMED_EQUATIONS])
        shown_edits = list(zip(named, group, strict=True))[:MAX_NAMED_EQUATIONS]
        more = max(0, len(named) - MAX_NAMED_EQUATIONS)
        if more:
            shown += f" and {more} more"
        rows.append(
            DecisionRow(
                when=as_utc(first.created_at) or first.created_at,
                item=shown,
                page=None,
                tool_had=TOOL_STATUS_WORDS["needs_review"],
                decision=RELABEL_WORDS.format(
                    was=inventory.name_of(str(mark.get("from", ""))),
                    now=inventory.name_of(str(mark.get("to", ""))),
                    n=len(group),
                ),
                by=who.get(first.user_id, owner),
                by_file=file_who.get(first.user_id, owner),
                refs=tuple(ref(label, e.element_id) for label, e in shown_edits),
                more=more,
            )
        )
    return rows


def _item_ref(result: DocumentResult, review_url: str | None) -> Callable[[str, str], ItemRef]:
    """Name an item (its words and its id) with the review screen's address for it, when the
    reader may open that screen and the item is in the document: a decision opens at its
    card, a page's own edit at that page's tools, a text at the tool that changes it. An item
    that is not in the document any more (a page tool or a re-read took it out) is named with
    a note and no link."""
    pages = {e.id: e.page + 1 for e in result.elements}
    decided = {f.element_id for f in result.fixes if f.kind in GROUP_KINDS}
    held = {e.id for o in result.omitted for e in o.elements}

    def ref(text: str, element_id: str) -> ItemRef:
        base = review_url or ""
        if (number := _page_number(element_id)) is not None:
            return ItemRef(text, f"{base}?page={number}&tools=1" if base else "")
        if element_id in decided:
            return ItemRef(text, f"{base}?item={quote(element_id)}" if base else "")
        if element_id in pages:
            where = f"?page={pages[element_id]}&tools=1&change={quote(element_id)}"
            return ItemRef(text, base + where if base else "")
        if element_id in held:
            return ItemRef(text, note="on a page left out of the document")
        return ItemRef(text, note="no longer in the document")

    return ref


def _page_number(element_id: str) -> int | None:
    """1-based page of a page-level edit recorded under "page-3"."""
    head, _, tail = element_id.partition("-")
    return int(tail) if head == "page" and tail.isdecimal() else None


def not_checked_list(result: DocumentResult, summary: CheckedSummary) -> list[str]:
    """What was not checked: the fixed facts plus the parts a person did not look at (the
    summary's open decisions, pictures, notes never opened and "Accept anyway")."""
    out = [*ALWAYS_NOT_CHECKED, source_not_checked(result.origin)]
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
    if summary.open_required:
        out.append(
            f"{summary.open_required} decisions were still open when this record was made. "
            "The file has the tool's proposals for them."
        )
    if summary.pictures:
        out.append(checked_words.as_image_sentence(summary))
    out.extend(checked_words.record_not_reviewed(summary))
    for line in by_hand_not_checked(result):
        out.append(line)
    out.extend(notice_not_checked(result))
    return out


def notice_not_checked(result: DocumentResult) -> list[str]:
    """The lines of the result that say something is not in the file or was not checked (the rows
    of `core.page_checks` with `not_checked`: words left out, pages whose drawings were not
    analysed, a source never compared with a compiled copy), one sentence per code: the job
    page lists them under "Not handled" or "Worth checking", and the record, which says "every
    part the tool found was converted", says them too. A page-level line names its pages, and a
    count in the sentence is the total."""
    out = []
    for code in page_checks.TABLE:
        if code not in page_checks.NOT_CHECKED_CODES:
            continue
        found = [n for n in result.notices if n.code == code]
        if not found:
            continue
        total = page_checks.make(code, count=sum(n.count for n in found))
        text = explain_notice(total).text.replace("{count}", f"{total.count:,}")
        pages = sorted({n.page + 1 for n in found if n.page is not None})
        if pages:
            label = "Page" if len(pages) == 1 else "Pages"
            text = f"{label} {', '.join(str(n) for n in pages)}: {text}"
        out.append(text)
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
    file_who: dict[str, str],
    owner: str,
    csv_actors: bool,
    review_url: str | None,
    filename: str,
    title: str,
    job_ref: str,
    converted_at: datetime | None,
    made_at: datetime,
    title_chosen: bool,
    contact_set: bool,
    exam_mode: bool,
    downloaded_formats: list[str] | None = None,
    state: Any = None,
) -> Record:
    """The record from a finished job's parts. `edits` are its `ReviewEdit` rows (any
    order); `who` maps a user id to the name shown for them; `state` is the job's
    `states.State`, whose label and gap notes the record prints. `who` and `file_who` map a
    user id to the words that name the person on the page and in the CSV (`shares.names_of`).
    `review_url` is the review screen's address for a reader who may open it, else None: the
    items the record names are links to it."""
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
    ref = _item_ref(result, review_url)
    counts = job_page.breakdown(result.fixes, result.notices)
    summary = checked.of_result(result)
    decisions = []
    ordered = sorted(edits, key=lambda e: (as_utc(e.created_at) or e.created_at, e.id))
    relabeled = [e for e in ordered if _relabel_mark(e) is not None]
    ordered = [e for e in ordered if _relabel_mark(e) is None]
    for edit in ordered:
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
                by_file=file_who.get(edit.user_id, owner),
                refs=(ref(item, edit.element_id),),
            )
        )
    decisions.extend(_relabel_rows(relabeled, labels, who, file_who, owner, ref))
    decisions.sort(key=lambda d: d.when)
    # The parts shown as pictures, as the summary counts them (an element with no fix too).
    images = [
        ImageRow(
            item=labels.get(p.element_id)
            or KIND_LABELS.get(elements[p.element_id].kind, p.kind.title()),
            page=p.page,
            why=explain_reason(fix.reason).text
            if (fix := fixes.get(p.element_id)) is not None and fix.reason
            else "",
            ref=ref(
                labels.get(p.element_id)
                or KIND_LABELS.get(elements[p.element_id].kind, p.kind.title()),
                p.element_id,
            ),
        )
        for p in summary.converted.pictures
    ]
    failed = [
        CheckRow(severity_label(c), explain_check(c).text, explain_check(c).code)
        for c in result.html_checks
        if not c.passed
    ]
    open_items = [
        ref(labels.get(unit[0].element_id, unit[0].kind.value.title()), unit[0].element_id)
        for unit in triage.units(f for f in result.fixes if f.pending)
    ]
    return Record(
        title=title,
        filename=filename,
        job_ref=job_ref,
        pages=result.stats.pages,
        converted_at=converted_at,
        made_at=made_at,
        tool=f"ReadableSTEM {__version__}, pipeline {result.pipeline_version}",
        owner=owner,
        csv_actors=csv_actors,
        language=job_page.language_line(result),
        title_chosen=title_chosen,
        contact_set=contact_set,
        exam_mode=exam_mode,
        counts=counts,
        checked=summary,
        by_kind=dict(Counter(job_page.word_kind(f) for f in result.fixes)),
        decisions=decisions,
        kept_as_images=images,
        checks_run=len(result.html_checks),
        failed_checks=failed,
        open_items=open_items,
        not_checked=not_checked_list(result, summary),
        by_hand=by_hand_counts(result),
        left_out_pages=sorted(o.page + 1 for o in result.omitted),
        downloaded=(
            None
            if downloaded_formats is None
            else [FORMAT_WORDS.get(f, f) for f in downloaded_formats]
        ),
        state=state.label if state is not None else "",
        gaps=[(g.title, g.text) for g in state.gaps] if state is not None else [],
        changes=sum(1 for e in ordered if e.action in PAGE_CHANGES),
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
    # Who decided is a column only in the owner's file: a person the document was shared with
    # gets the same record without it.
    columns = [c for c in CSV_HEADER if c != "by" or record.csv_actors]
    writer.writerow(columns)

    def row(section: str, item="", page="", tool_had="", decision="", by="", when="", detail=""):
        cells = dict(
            section=section,
            item=item,
            page=page,
            tool_had=tool_had,
            decision=decision,
            by=by,
            when_utc=when,
            detail=detail,
        )
        writer.writerow([safe_cell(cells[c]) for c in columns])

    row("document", "Title", detail=record.title)
    row("document", "File name", detail=record.filename)
    row("document", "Pages", detail=record.pages)
    row("document", "Converted on", when=iso_when(record.converted_at))
    if record.csv_actors:
        row("document", "Converted by", by=record.owner)
    row("document", "Tool", detail=record.tool)
    row("document", "Language", detail=record.language)
    row("document", "Exam or quiz", detail=exam.words(record.exam_mode))
    row("document", "Record made on", when=iso_when(record.made_at))
    row("document", "Reference", detail=record.job_ref)
    if record.state:
        row("document", "State", detail=record.state)
    if record.downloaded is not None:
        row("document", "File types downloaded", detail=", ".join(record.downloaded) or "None")
    c = record.counts
    row("summary", "Parts found", detail=c.items)
    for kind, n in record.by_kind.items():
        row("summary", f"Found: {kind}", detail=n)
    row("summary", "Accepted by the tool, no decision from a person", detail=c.accepted)
    row("summary", "Decisions made by a person", detail=c.decided)
    row("summary", "Changes to pages made by a person", detail=record.changes)
    s = record.checked
    # What people did, per decision (`core.triage.review_load`), the open ones first.
    for action, label in checked_words.ACTION_ROWS.items():
        row("summary", label, detail=s.load.actions[action])
    row("summary", "Shown as pictures of the original", detail=s.pictures)
    row("summary", checked_words.READY_ALL, detail="Yes" if s.all_found_converted else "No")
    for name, n in record.by_hand.items():
        row("summary", name, detail=n)
    if s.cut_off:
        row(
            "summary",
            "Empty pages at the end of the PDF (it looks cut off)",
            detail=" ".join(str(n) for n in s.converted.cut_off_pages),
        )
    if record.left_out_pages:
        pages = " ".join(str(n) for n in record.left_out_pages)
        row("summary", "Pages left out of the student file", detail=pages)
    for title, text in record.gaps:
        row("summary", title, detail=text)
    for d in record.decisions:
        row("decision", d.item, d.page or "", d.tool_had, d.decision, d.by_file, iso_when(d.when))
    for item in record.open_items:
        row("open", item.text, decision="Waiting for a decision")
    for i in record.kept_as_images:
        row("not_converted", i.item, i.page or "", detail=i.why)
    row("check", "Structure checks of the HTML file that ran", detail=record.checks_run)
    for ch in record.failed_checks:
        row("check", ch.code, tool_had=ch.severity, decision="Did not pass", detail=ch.text)
    for line in record.not_checked:
        row("not_checked", detail=line)
    return out.getvalue()
