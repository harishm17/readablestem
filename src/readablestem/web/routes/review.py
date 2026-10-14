"""Review decisions (the POST route both review views share) and the list view of every
item ("All items as a list", `/jobs/{id}/review/list`). The main review screen, one
decision at a time beside the page it is on, is `routes/split.py`.

Every decision form carries a hidden `view` field: "list" from the list view, "split"
from the review screen (the default). After a decision the reviewer goes back to the view
the form was on: the list's next card (`#item-<id>`), or the review screen's next open
decision (`?item=<key>`). A refused edit re-shows the same view with the error.

The list view:

Layout (see `web.review` for the rules), in three sections:

1. "Needs your decision (N)": equations, then figures, then tables, then headings, each
   listing the fixes still waiting (page and reading order). Repeated headings that share a
   `Fix.group_key` are one card with one decision for all of them ("Apply to all 11",
   "Make all 11 bold text", or one level for all); their own cards are folded inside it.
   N counts decisions, so such a group counts once. There is no bulk accept: every
   decision asked for is meaning-level, so each is opened (`web.review`). The one
   exception is "Accept all N clean tables" (T3-11, `routes/edits.py`): tables where
   nothing at all was found wrong, two or more, named in the button.
2. "Optional checks (M)": fixes accepted automatically with a note (a cosmetic style
   difference, a heading with weak evidence, a long alt text), folded away in a
   <details>, each note in plain English. Nothing there blocks "done"; each can still be
   edited or rejected. A note of the second automated check (a possible mismatch between
   the alt text and the image) is listed there too, by policy, but the page says it is
   worth a look instead of calling it small.
3. "Accepted or decided": everything else, folded away.

A decision is one transaction: lock the job row, apply the decision to the stored result
(`web.review`), store a `ReviewEdit` per changed fix, re-render the output HTML once with
the pipeline's assembler, count the write (`jobs.result_version`), save. If any step
fails nothing changes. Every form carries the version its page was drawn at (`version`):
one drawn before the last write (a tab left open while the document was changed in
another) is refused with a 409 and the decision as it is now. Without JavaScript
each button is a normal form POST that redirects to the next decision still waiting
(`#item-<id>`, or `#group-card-<key>` for a heading group), where a notice links back to
the one just changed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from readablestem.core import triage
from readablestem.core.types import Check, DocumentResult, Element, Fix, Kind, Status
from readablestem.pipeline.layout import REASON_LARGER_FONT
from readablestem.pipeline.validate import ALT_WARN_CHARS
from readablestem.web import events, heading_groups, notation, notice_places, page_edits, retention
from readablestem.web.db import Blob, Job, ReviewEdit, session_scope
from readablestem.web.deps import (
    ELEMENT_ID,
    CurrentUser,
    PageError,
    UserDep,
    flash,
    get_ctx,
    pop_flash,
    render,
    require_csrf,
)
from readablestem.web.job_page import word_kind
from readablestem.web.logsafe import log_event
from readablestem.web.mathml import try_sanitize_mathml
from readablestem.web.messages import (
    NOTE_LABEL,
    Explained,
    confidence_label,
    explain_check,
    explain_reason,
    severity_label,
)
from readablestem.web.page_edits import StructureChange
from readablestem.web.review import (
    ADD_DATA_TABLE,
    DESCRIBE,
    GROUP_SLUGS,
    NOT_CONVERTED,
    STATUS_FOR_ACTION,
    TABLE_CHANGES,
    UNDO_ACTION,
    USE_LOCAL_NOTE,
    WRITE_KEY,
    FieldError,
    FixGroup,
    GroupOutcome,
    HeadingPlace,
    ReviewChange,
    ReviewInvalid,
    ReviewStale,
    apply_group,
    apply_page_group,
    apply_review,
    bump_result_version,
    can_accept,
    can_describe,
    clean_tables,
    group_anchor,
    group_fixes,
    group_members,
    heading_level_written,
    heading_places,
    is_group_key,
    is_kept_image,
    is_page_group_key,
    is_stale,
    item_labels,
    item_numbers,
    kept_images_apart,
    kind_for_slug,
    needs_attention,
    next_to_review,
    restorable,
    review_anchor,
    undo_last,
)
from readablestem.web.routes.jobs import NOT_FOUND, owned_job, require_done
from readablestem.web.table_card import TableCard, table_card
from readablestem.web.views import (
    KIND_LABELS,
    JobView,
    decisions_label,
    fix_status_label,
    job_view,
)

logger = logging.getLogger(__name__)
router = APIRouter()

DONE_MESSAGES = {
    "accept": "Accepted.",
    "edit": "Your edit passed the required checks and was saved.",
    "reject": "The original from the PDF is kept in the document.",
    ADD_DATA_TABLE: "The data table was added: students get it under the figure.",
    DESCRIBE: "Your description is now the alt text of the image: students using a screen "
    "reader hear it.",
}
DESCRIPTION_REMOVED = "The description was taken away."
# A table edit: the decision stays open while a required check fails (web.review
# `table_still_open`), and the grid read from the PDF is not checked against itself.
TABLE_OPEN = (
    "Saved, but a required check still fails, so the table still waits for your decision: "
    "put the rest right, Accept it as it is, or keep the image."
)
USE_LOCAL_DONE = (
    "Its words are the PDF's own, so the checks could not compare them with anything: "
    "check it against the image."
)
EDIT_ANYWAY = "edit_anyway"  # the action of "Use my edit anyway"
ANYWAY_DONE = "Your edit was saved as you wrote it, although it differs from the PDF."


@dataclass(frozen=True)
class RejectWords:
    """ "Reject" named for what it does to this kind of item (UX review MF14): the
    button, the line under it, the notice after it and the item's state after it."""

    button: str
    consequence: str
    done: str
    state: str


REJECT_WORDS = {
    Kind.EQUATION: RejectWords(
        "Keep as image",
        "Students see a picture of the original; screen readers hear the PDF text of the "
        "equation, marked as not checked.",
        "Kept as an image of the original. Screen readers will hear the PDF text of the "
        "equation, marked as not checked.",
        "Kept as an image by you",
    ),
    # A table's image is kept, marked "not converted" (when there was no proposal, there
    # is nothing else to keep).
    Kind.TABLE: RejectWords(
        "Keep as image",
        "Students see a picture of the table, marked “not converted”; screen readers get no "
        "table to read.",
        "The image of the table is kept in the document, marked “not converted”.",
        "Kept as an image by you",
    ),
    Kind.FIGURE: RejectWords(
        "Keep without a description",
        "The figure stays as it is in the PDF, marked “Needs a description”; screen readers "
        "get no description of it.",
        "Kept without a description, marked “Needs a description”.",
        "Left without a description by you",
    ),
}
BOLD_TEXT_WORDS = RejectWords(
    "Make bold text",
    "It stays in the text, in bold, but is no longer a heading in the outline.",
    "Made bold text, not a heading.",
    "Made bold text by you",
)
PLAIN_TEXT_WORDS = RejectWords(
    "Make plain text",
    "It stays in the text, but is no longer a heading in the outline.",
    "Made plain text, not a heading.",
    "Made plain text by you",
)
OTHER_WORDS = RejectWords(
    "Keep the original",
    "The original from the PDF stays in the document, marked for a later look.",
    DONE_MESSAGES["reject"],
    "Original kept by you",
)


# A page (or handwriting) kept as an image before any model work (pipeline.page_type).
KEPT_IMAGE_WORDS = RejectWords(
    "Keep as image",
    "Students see a picture of it, marked “Not converted”; screen readers hear only that.",
    "Kept as an image, marked “Not converted”.",
    "Kept by you",  # short: the review screen's status pill does not wrap at 320 px
)


def reject_words(kind: Kind, element: Element | None, fix: Fix | None = None) -> RejectWords:
    """What rejecting does here. A heading becomes a paragraph, bold when its line is
    bold in the PDF (as `assemble` writes it); a page kept as an image stays one."""
    if fix is not None and is_kept_image(fix):
        return KEPT_IMAGE_WORDS
    if kind is Kind.HEADING:
        return BOLD_TEXT_WORDS if element is not None and element.bold else PLAIN_TEXT_WORDS
    return REJECT_WORDS.get(kind, OTHER_WORDS)


def status_words(fix: Fix, element: Element | None) -> str:
    """The item's state in words, a reviewer's "Reject" named for what it did."""
    if fix.status is Status.REJECTED:
        return reject_words(fix.kind, element, fix).state
    return fix_status_label(fix)


def done_message(change: ReviewChange, element: Element | None) -> str:
    """What the notice after one decision says (content-free: it lives in the session)."""
    if change.action == "reject":
        return reject_words(change.fix.kind, element, change.fix).done
    if change.action == DESCRIBE and not change.fix.described:
        return DESCRIPTION_REMOVED
    if change.anyway:
        return ANYWAY_DONE
    message = DONE_MESSAGES.get(change.action, "Done.")
    if change.action == "edit" and change.fix.kind is Kind.TABLE:
        if change.fix.pending:
            message = TABLE_OPEN
        elif change.note == USE_LOCAL_NOTE:
            message = USE_LOCAL_DONE
    if change.note:  # what a table edit changed ("Row 2, column 3 changed.")
        message = f"{change.note} {message}"
    if change.warnings:
        n = len(change.warnings)
        message += f" {n} {'check gives a warning' if n == 1 else 'checks give warnings'}: "
        message += "see the notes on it."
    return message


# A flash whose target starts with this belongs to a group (a bulk action), not to one fix.
GROUP_FLASH = "group:"
# ... and this one to a heading group's card (a group decision).
HEADING_GROUP_FLASH = "heading-group:"
NOUNS = {
    Kind.EQUATION: ("equation", "equations"),
    Kind.FIGURE: ("figure", "figures"),
    Kind.TABLE: ("table", "tables"),
    Kind.HEADING: ("heading", "headings"),
}
# The list view's own section for pages and handwriting kept as images
# (`web.review.kept_images_apart`): figures in the data, not in words.
KEPT_IMAGE_NOUNS = {
    "page": ("page kept as an image", "pages kept as images"),
    "handwriting": ("piece of handwriting kept as an image", "handwriting kept as images"),
    "both": ("page or handwriting kept as an image", "pages and handwriting kept as images"),
}


def counted(n: int, kind: Kind) -> str:
    """'1 figure', '6 equations'."""
    one, many = NOUNS[kind]
    return f"{n} {one if n == 1 else many}"


@dataclass
class CheckView:
    text: str  # sentence for the reviewer (web.messages)
    code: str  # "name: code", shown small for debugging
    severity: str  # "Must be fixed", "Worth checking", or "Note" on an optional check


@dataclass
class Notice:
    message: str
    label: str | None = None  # the fix it is about, e.g. "Equation 3 on page 2"
    href: str | None = None  # link to that fix
    element_id: str = ""  # that fix's id (app.js forgets a draft of it kept in this tab)


STALE_TITLE = "Your choice was not saved"
STALE_MESSAGE = (
    "This document was changed in another tab or window after this page was opened. It is "
    "shown below as it is now: check it, then choose again."
)


@dataclass
class StaleNotice:
    """A refused write from an out-of-date page (`web.review.is_stale`)."""

    title: str
    message: str
    now: str  # what the item is now ("Equation 1 is now: accepted by you."), or ""
    href: str  # the page as it is now, by GET (reloading the POST would only re-send it)
    kept: bool = False  # what was typed is back in its field (which is then open)


STALE_KEPT = "What you typed is kept below."


def stale_notice(
    result: DocumentResult,
    numbers: dict[str, int],
    target: str,
    href: str,
    typed: Mapping[str, str] | None = None,
) -> StaleNotice:
    fix = (
        result.fix_for(target) if not (is_group_key(target) or is_page_group_key(target)) else None
    )
    now = ""
    if fix is not None and fix.kind in NOUNS:
        label = item_labels(result).get(
            fix.element_id, f"{KIND_LABELS[fix.kind]} {numbers.get(fix.element_id, 0)}"
        )
        element = next((e for e in result.elements if e.id == target), None)
        now = f"{label} is now: {status_words(fix, element).lower()}."
    kept = any((value or "").strip() for value in (typed or {}).values())
    message = f"{STALE_MESSAGE} {STALE_KEPT}" if kept else STALE_MESSAGE
    return StaleNotice(STALE_TITLE, message, now, href, kept)


def _stale_anchor(result: DocumentResult, target: str) -> str:
    if is_group_key(target) or is_page_group_key(target):
        return group_anchor(target)
    if kind_for_slug(target) is not None:
        return f"group-{target}"
    return review_anchor(result, target)


@dataclass
class ReviewItem:
    element_id: str
    kind: str
    label: str  # "Equation 3": number within its kind, in reading order
    page: int  # 1-based; 0 when the element is missing
    status: str
    status_label: str
    pending: bool
    confidence_label: str
    checks: list[CheckView]
    reason: Explained | None
    crop_url: str | None
    mathml: Markup | None
    speech: str | None
    alt: str | None
    decorative: bool
    caption: str | None
    heading_level: int | None
    heading_text: str | None
    # Headings: levels 1 to this one keep their place in the outline (web.review).
    heading_max_level: int | None
    # Headings the outline repair moves: the level the output writes instead, and
    # whether it is written as a heading now (else it would be, if accepted again).
    heading_shown: int | None
    heading_in_document: bool
    can_accept: bool
    values: dict[str, str]
    optional: bool = False  # accepted automatically with optional notes (`checks`)
    second_check: bool = False  # ... one of them the second automated check's
    errors: dict[str, str] = field(default_factory=dict)  # field prefix -> message
    notices: list[Notice] = field(default_factory=list)  # changes elsewhere, shown above
    changed: list[str] = field(default_factory=list)  # changes to this fix, shown inside
    # The form comes back with the reviewer's own change in a field (a refused Accept or
    # edit): "Save edit" is then the main button.
    edited: bool = False
    # Tables: the proposal as the student would get it, and what can be changed.
    table: TableCard | None = None
    # Figures with long descriptions on: the proposal's long description, and its chart
    # data table as HTML (`table_html`, captioned like the student file's) with the id
    # prefix its caption id starts with. None when the figure has none.
    long_description: str | None = None
    data_table: Markup | None = None
    data_table_id: str | None = None
    # The data table was read from the image and is not in the student file: an
    # optional extra the card offers ("Add the data table"), not the decision.
    data_table_offered: bool = False
    # An edit refused only because it differs from the PDF where that changes the
    # meaning: the form offers "Use my edit anyway".
    override: bool = False
    # "Reject" named for what it does here (`reject_words`).
    reject: RejectWords = OTHER_WORDS
    by_you: bool = False  # accepted, edited or rejected by the reviewer
    # What the item is, in a few words, for lists that name it (UX review MF15): the
    # heading's text or the start of the alt text. Equations show `mathml` instead.
    preview: str = ""
    # What a screen reader gets from the PDF for an equation: its text layer ("" when
    # the PDF has none). Shown only to the job's owner, on the review screens.
    text_layer: str = ""
    # A page (or handwriting) kept as an image (`web.review.is_kept_image`): its card says
    # what it is now and offers no figure form (its one decision is the page group's).
    kept_image: bool = False
    kept_state: str = ""  # what students get, once decided
    # "Describe it in words" (T3-16): offered on an item kept as an image; what it replaces.
    can_describe: bool = False
    describe_hint: str = ""

    @property
    def open(self) -> bool:
        """A folded fix is unfolded when the reviewer just changed it or it has an error
        to correct."""
        return bool(self.changed or self.errors)

    @property
    def alt_chars(self) -> int:
        return len(self.values.get("alt", ""))

    @property
    def notes(self) -> str:
        """The optional notes as one line of plain English (for a folded summary)."""
        return " ".join(c.text for c in self.checks)


def times_phrase(n: int) -> str:
    """ "once", "twice", "3 times": how often a heading repeats. A group can be left with
    one when the page of its others was left out (T3-16)."""
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


@dataclass
class HeadingGroupView:
    """One decision for repeated headings with the same label and style."""

    key: str
    anchor: str
    text: str  # the label, as the first heading has it
    level: int | None  # the level proposed for them (the same for all: heading_step)
    level_max: int  # levels 1 to this keep their place for the first one waiting
    style: str  # how they look in the PDF, in words
    size: int  # how many headings the pipeline found
    members: list[ReviewItem]  # the ones still waiting, reading order
    pages: list[int]
    keep: int  # how many "Apply to all" would keep at their level
    values: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    notices: list[str] = field(default_factory=list)
    members_open: bool = False  # one of them was just decided on its own
    # The advice (`web.heading_groups`, T1-18): the answer to suggest ("bold" or "keep"),
    # the fact that decides it, and up to three copies to look at.
    suggest: str = "keep"
    fact: str = ""
    crops: list[heading_groups.Crop] = field(default_factory=list)

    @property
    def times(self) -> str:
        return times_phrase(self.size)

    @property
    def bold_first(self) -> bool:
        """The suggested answer is the main button: bold text when that is the advice, or
        the only direct choice (keeping them would move their level)."""
        return self.suggest == "bold" or not self.keep

    @property
    def waiting(self) -> int:
        return len(self.members)

    @property
    def open(self) -> bool:
        """The folded cards of its headings are unfolded when the reviewer is working
        through them one by one, or one has an error to correct (its error summary link
        must reach the field)."""
        return self.members_open or any(m.errors for m in self.members)

    @property
    def pages_text(self) -> str:
        pages = sorted(set(self.pages))
        if len(pages) == 1:
            return f"page {pages[0]}"
        return "pages " + ", ".join(str(p) for p in pages[:-1]) + f" and {pages[-1]}"


@dataclass
class PageGroupView:
    """One decision for the pages (or the handwriting) kept as images for one reason
    (`web.review.page_group_members`): keep them, marked "Not converted". There is no
    handwriting or scan conversion; the card says what students get and how to do better.
    Content-free: page numbers and counts only."""

    key: str
    anchor: str
    reason: str
    title: str  # "Handwritten pages, not converted"
    lead: str  # "Pages 2, 3 and 5 are handwritten."
    explain: str  # what students get
    hint: str  # what to do to make them accessible
    hidden: str  # the hidden text that was not used, as a count; "" when none
    button: str  # "Keep the page images, marked “Not converted”"
    members: list[ReviewItem]  # the ones still waiting, reading order
    pages: list[int]  # 1-based, of every member
    size: int
    state: str = ""  # once decided: what they are now
    values: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    notices: list[str] = field(default_factory=list)
    members_open: bool = False
    # Every page or piece of handwriting of the group, to describe in words (T3-16).
    describe: list[ReviewItem] = field(default_factory=list)

    @property
    def waiting(self) -> int:
        return len(self.members)


# What the page group card says, by the reason the pages were kept (pipeline.page_type).
# "{pages}" is "Page 2" or "Pages 2 and 5". TODO(Harish): final wording (design 3.3).
PAGE_GROUP_WORDS: dict[str, dict[str, tuple[str, str]]] = {
    "handwritten_page": {
        "title": ("Handwritten page, not converted", "Handwritten pages, not converted"),
        "lead": ("{pages} is handwritten.", "{pages} are handwritten."),
        "explain": (
            "This tool does not convert handwriting. In the HTML the page is an image marked "
            "“Not converted”, so a student using a screen reader hears only that.",
            "This tool does not convert handwriting. In the HTML each page is an image marked "
            "“Not converted”, so a student using a screen reader hears only that.",
        ),
    },
    "scanned_page": {
        "title": ("Scanned page, not converted", "Scanned pages, not converted"),
        "lead": ("{pages} is a scanned image.", "{pages} are scanned images."),
        "explain": (
            "This tool does not convert scans. In the HTML the page is an image marked “Not "
            "converted”, so a student using a screen reader hears only that.",
            "This tool does not convert scans. In the HTML each page is an image marked “Not "
            "converted”, so a student using a screen reader hears only that.",
        ),
    },
    "unread_page": {
        "title": ("Page kept as an image", "Pages kept as images"),
        "lead": (
            "{pages} has drawings or writing that this tool could not read.",
            "{pages} have drawings or writing that this tool could not read.",
        ),
        "explain": (
            "In the HTML the page is an image marked “Not converted”, after any text read "
            "from it, so a student using a screen reader hears only that for the rest.",
            "In the HTML each page is an image marked “Not converted”, after any text read "
            "from it, so a student using a screen reader hears only that for the rest.",
        ),
    },
    "handwriting_on_page": {
        "title": ("Handwriting on a typed page", "Handwriting on typed pages"),
        "lead": ("{pages} has handwriting on it.", "{pages} have handwriting on them."),
        "explain": (
            "The typed text was converted. This tool does not convert handwriting, so it is "
            "an image after the typed text, marked “Not converted”: a student using a screen "
            "reader hears only that.",
            "The typed text was converted. This tool does not convert handwriting, so each "
            "piece of it is an image after the typed text, marked “Not converted”: a student "
            "using a screen reader hears only that.",
        ),
    },
}
# A decided page (or piece of handwriting) kept as an image, in the list of decisions.
KEPT_IMAGE_STATE = (
    "Kept as an image, marked “Not converted”: a student using a screen reader hears only "
    "that. There is nothing else to choose for it."
)
PAGE_GROUP_HINTS = {
    "page": (
        "To make it accessible, type it up (for example in LaTeX or Word) and upload the "
        "typed version.",
        "To make them accessible, type them up (for example in LaTeX or Word) and upload the "
        "typed version.",
    ),
    "handwriting": (
        "If it matters, type it into your source file and upload that again.",
        "If it matters, type it into your source file and upload that again.",
    ),
    # A page of drawings or pictures nothing could be read from: not a page to type up.
    "drawn": (
        "If students need what is on it, describe it in your source file (for example as a "
        "figure with a description) and upload that again.",
        "If students need what is on them, describe it in your source file (for example as "
        "figures with descriptions) and upload that again.",
    ),
}


def pages_phrase(pages: list[int]) -> str:
    """ "Page 2" or "Pages 2, 3 and 5" (1-based)."""
    pages = sorted(set(pages))
    if len(pages) == 1:
        return f"Page {pages[0]}"
    return "Pages " + ", ".join(str(p) for p in pages[:-1]) + f" and {pages[-1]}"


def page_group_view(
    result: DocumentResult, unit: list[Fix], items: dict[str, ReviewItem]
) -> PageGroupView:
    """The card of a group of kept images (`unit`: its fixes, the waiting ones first)."""
    from readablestem.web.review import group_anchor, page_group_members

    first = unit[0]
    key = first.group_key or first.element_id
    reason = first.reason or "unread_page"
    words = PAGE_GROUP_WORDS.get(reason, PAGE_GROUP_WORDS["unread_page"])
    members = page_group_members(result, key) if first.group_key else list(unit)
    page_of = {e.id: e.page + 1 for e in result.elements}
    pages = sorted({page_of[f.element_id] for f in members if f.element_id in page_of})
    many = 1 if len(pages) > 1 else 0
    region = reason == "handwriting_on_page"
    hint_kind = "handwriting" if region else "drawn" if reason == "unread_page" else "page"
    hint = PAGE_GROUP_HINTS[hint_kind][many]
    if region:
        button = "Keep the handwriting as images" if len(members) > 1 else "Keep it as an image"
        button += ", marked “Not converted”"
    else:
        button = f"Keep the page image{'s' if many else ''}, marked “Not converted”"
    kept = {p - 1 for p in pages}
    hidden_chars = sum(t.untrusted_chars for t in result.page_types if t.page in kept)
    hidden = ""
    if hidden_chars >= 3 and not region:  # page_type.HIDDEN_FEW
        these = "These pages have" if many else "This page has"
        hidden = (
            f"{these} hidden text ({hidden_chars:,} characters) that a tablet or a text "
            "recognition (OCR) program guessed from the page. It was not used. It is a count "
            "only: the hidden text is not shown here."
        )
    waiting = [items[f.element_id] for f in members if f.pending and f.element_id in items]
    state = ""
    if not waiting:
        state = (
            "Kept as images, marked “Not converted”."
            if many or len(members) > 1
            else "Kept as an image, marked “Not converted”."
        )
    return PageGroupView(
        key=key,
        anchor=group_anchor(key),
        reason=reason,
        title=words["title"][many],
        lead=words["lead"][many].format(pages=pages_phrase(pages) if pages else "This page"),
        explain=words["explain"][1 if len(members) > 1 else 0],
        hint=hint,
        hidden=hidden,
        button=button,
        members=waiting,
        pages=pages,
        size=len(members),
        state=state,
        describe=[items[f.element_id] for f in members if f.element_id in items],
    )


@dataclass
class UnitView:
    """One decision in the "Needs your decision" list: a fix, a heading group, or the
    pages kept as images for one reason."""

    item: ReviewItem | None = None
    group: HeadingGroupView | None = None
    pages: PageGroupView | None = None


@dataclass
class GroupView:
    slug: str
    title: str  # "Equations"
    one: str  # "equation"
    many: str  # "equations"
    units: list[UnitView]  # decisions waiting
    pending: list[ReviewItem]  # every fix waiting, also those inside a heading group
    optional: list[ReviewItem]
    done: list[ReviewItem]
    needs_review: int
    not_converted: int
    notices: list[str] = field(default_factory=list)
    error: str | None = None
    clean: int = 0  # tables waiting with nothing wrong found in them (T3-11)

    @property
    def total(self) -> int:
        return len(self.pending) + len(self.optional) + len(self.done)


# --------------------------------------------------------------------------- view model


def _item(
    fix: Fix,
    element: Element | None,
    number: int,
    job_id: str,
    crop_ids: set[str],
    place: HeadingPlace | None = None,
    label: str | None = None,
) -> ReviewItem:
    """`label`: the item's name (`web.review.item_labels`), else kind and `number`."""
    place = place if fix.kind is Kind.HEADING else None
    moved = place is not None and place.moved
    optional = fix.optional_note
    details = _figure_details(fix, element)
    values: dict[str, str] = {}
    if fix.equation is not None:
        values["latex"] = fix.equation.latex
    if fix.figure is not None:
        values["alt"] = fix.figure.alt
        values["decorative"] = "on" if fix.figure.decorative else ""
        if details.get("long_description") is not None:
            values["long_description"] = details["long_description"]
    describable = can_describe(fix)
    if describable:
        values["description"] = fix.described or ""
    if fix.heading is not None:
        # The select offers the level the document shows, not one it would move.
        suggested = place.suggested if place is not None else None
        values["level"] = str(suggested or fix.heading.level)
    table = table_card(fix, element) if fix.kind is Kind.TABLE else None
    # A table the reviewer gave a grid (the one read from the PDF) is converted now: the
    # pipeline's reason stays stored, but no longer says why it is not.
    reason = fix.reason if table is None or not table.in_document else None
    return ReviewItem(
        element_id=fix.element_id,
        kind=fix.kind.value,
        label=label or f"{KIND_LABELS.get(fix.kind, fix.kind.value.title())} {number}",
        page=(element.page + 1) if element else 0,
        status=fix.status.value,
        status_label=status_words(fix, element),
        pending=needs_attention(fix.status),
        confidence_label=confidence_label(fix.confidence),
        checks=[_check_view(c, note=optional) for c in fix.failed_checks],
        reason=explain_reason(reason) if reason else None,
        crop_url=f"/jobs/{job_id}/crops/{fix.element_id}.png"
        if fix.element_id in crop_ids
        else None,
        mathml=try_sanitize_mathml(fix.equation.mathml) if fix.equation else None,
        speech=fix.equation.speech if fix.equation else None,
        alt=fix.figure.alt if fix.figure else None,
        decorative=bool(fix.figure and fix.figure.decorative),
        caption=element.caption if element and fix.kind is Kind.FIGURE else None,
        heading_level=fix.heading.level if fix.heading else None,
        heading_text=fix.heading.text if fix.heading else (element.text if element else None),
        heading_max_level=place.highest if place is not None else None,
        heading_shown=place.written if place is not None and moved else None,
        heading_in_document=place is not None and place.in_document,
        # Accepting a level the output moves is refused (web.review), so not offered.
        can_accept=can_accept(fix) and fix.status is not Status.ACCEPTED and not moved,
        values=values,
        optional=optional,
        second_check=triage.second_check_note(fix),
        table=table,
        **details,
        reject=reject_words(fix.kind, element, fix),
        by_you=fix.decided_by_reviewer,
        preview=_preview(fix, element),
        text_layer=(element.text or "").strip()
        if element is not None and fix.kind is Kind.EQUATION
        else "",
        kept_image=is_kept_image(fix),
        kept_state=KEPT_IMAGE_STATE if is_kept_image(fix) and not fix.pending else "",
        can_describe=describable,
        describe_hint=DESCRIBE_HINTS.get(fix.kind.value, "") if describable else "",
    )


# What a description in words replaces, by the kind of item kept as an image (T3-16).
DESCRIBE_HINTS = {
    "equation": "Students using a screen reader hear this instead of the PDF's own text of "
    "the equation. Say the equation in words, for example “v equals d over t”.",
    "table": "Students using a screen reader hear this instead of “table not converted”. "
    "Say what the table holds and what it shows.",
    "figure": "Students using a screen reader hear this instead of the note that it is not "
    "converted. Say what is on it.",
}


def _figure_details(fix: Fix, element: Element | None) -> dict[str, Any]:
    """A figure's long description and data table for its card (figures work). The long
    description field is offered when the figure has one or a data table in the
    document, or when it is a chart or diagram described with long descriptions on but
    left without one (the notice `long_description_missing`), so the reviewer can write
    it; "" when empty. A data table read from the image is shown as an optional extra
    (`data_table_offered`) with "Add the data table"."""
    from readablestem.pipeline import assemble_tables, figure_details, figure_step, table_html

    figure = fix.figure
    if figure is None or figure.decorative:
        return {}
    long_description = figure.long_description
    if long_description is None and (
        figure_details.data_table_shown(figure) or figure_step.long_description_missing(fix)
    ):
        long_description = ""
    shown: dict[str, Any] = {"long_description": long_description}
    # A table read from the image is offered while the figure is in the document (not
    # kept as the original image).
    offered = figure_details.data_table_offered(figure)
    if offered and fix.status is Status.REJECTED:
        return shown
    shown["data_table_offered"] = offered
    if figure.data_table is not None:
        table_id = "review-" + assemble_tables.id_prefix(fix.element_id) + "-data"
        caption = element.caption if element is not None else None
        grid = figure.data_table.model_copy(
            update={"caption": figure_details.table_caption(figure, caption)}
        )
        # Escaped and rebuilt by the serialiser (cell text escaped, MathML allowlisted).
        shown["data_table"] = Markup(table_html.render_table(grid, id_prefix=table_id))
        shown["data_table_id"] = table_id
    return shown


PREVIEW_CHARS = 60  # of an alt text, in a list line


def _preview(fix: Fix, element: Element | None) -> str:
    """A few words that say which item this is: a heading's text, the start of the alt
    text. Shown only on the owner's review screens (document content)."""
    if fix.kind is Kind.HEADING:
        text = fix.heading.text if fix.heading is not None else (element.text if element else "")
        return " ".join((text or "").split())
    if fix.kind is Kind.FIGURE and fix.figure is not None:
        if fix.figure.decorative:
            return ""
        alt = " ".join(fix.figure.alt.split())
        if len(alt) <= PREVIEW_CHARS:
            return alt
        return alt[:PREVIEW_CHARS].rsplit(" ", 1)[0] + "…"
    return ""


def _check_view(check: Check, *, note: bool = False) -> CheckView:
    explained = explain_check(check)
    return CheckView(explained.text, explained.code, NOTE_LABEL if note else severity_label(check))


def _style_words(element: Element | None) -> str:
    """How a group's headings look in the PDF, without any of their text."""
    if element is None:
        return "with the same style"
    if REASON_LARGER_FONT in element.reasons:
        return "in a larger font"
    if element.bold:
        return "as a bold line in the body text size"
    return "as a line on its own"


def _heading_group(
    result: DocumentResult,
    unit: list[Fix],
    items: dict[str, ReviewItem],
    elements: dict[str, Element],
    places: dict[str, HeadingPlace],
) -> HeadingGroupView:
    first = unit[0]
    key = first.group_key or ""
    level = first.heading.level if first.heading is not None else None
    place = places.get(first.element_id)
    keep = sum(
        1
        for f in unit
        if can_accept(f)
        and f.heading is not None
        and heading_level_written(result, f.element_id, f.heading.level) == f.heading.level
    )
    members = [items[f.element_id] for f in unit]
    return HeadingGroupView(
        key=key,
        anchor=group_anchor(key),
        text=(first.heading.text if first.heading is not None else "") or "(no text)",
        level=level,
        level_max=place.highest if place is not None else 6,
        style=_style_words(elements.get(first.element_id)),
        size=len(group_members(result, key)) or first.group_size or len(unit),
        members=members,
        pages=[m.page for m in members if m.page],
        keep=keep,
        values={"level": str(level or 1)},
        suggest=(advised := heading_groups.advice(result, unit)).suggest,
        fact=advised.fact,
        crops=heading_groups.crops(unit, elements),
    )


def _group(
    result: DocumentResult,
    group: FixGroup,
    items: dict[str, ReviewItem],
    elements: dict[str, Element],
    places: dict[str, HeadingPlace],
) -> GroupView:
    one, many = NOUNS[group.kind]
    if group.name:  # pages and handwriting kept as images
        words = {word_kind(f) for f in (*group.pending, *group.done, *group.optional)}
        one, many = KEPT_IMAGE_NOUNS[words.pop() if len(words) == 1 else "both"]
    units = [
        UnitView(group=_heading_group(result, unit, items, elements, places))
        if unit[0].group_key and unit[0].kind is Kind.HEADING
        else UnitView(pages=page_group_view(result, unit, items))
        if unit[0].group_key and is_kept_image(unit[0])
        else UnitView(item=items[unit[0].element_id])
        for unit in group.units
    ]
    return GroupView(
        slug=group.slug,
        title=many.capitalize(),
        one=one,
        many=many,
        units=units,
        pending=[items[f.element_id] for f in group.pending],
        optional=[items[f.element_id] for f in group.optional],
        done=[items[f.element_id] for f in group.done],
        needs_review=sum(f.status is Status.NEEDS_REVIEW for f in group.pending),
        not_converted=sum(f.status in NOT_CONVERTED for f in group.pending),
        clean=len(clean_tables(result)) if group.kind is Kind.TABLE else 0,
    )


def _load(
    request: Request, job_id: str, user: CurrentUser
) -> tuple[JobView, DocumentResult, set[str]]:
    view, result, crop_ids, _ = _load_versioned(request, job_id, user)
    return view, result, crop_ids


def _load_versioned(
    request: Request, job_id: str, user: CurrentUser
) -> tuple[JobView, DocumentResult, set[str], int]:
    """`_load`, and the job's review version: what the page's forms carry (stale guard)."""
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        view = job_view(session, job, with_summary=True)
        result = DocumentResult.model_validate(job.result_json or {})
        crop_ids = set(
            session.scalars(
                select(Blob.element_id).where(Blob.job_id == job.id, Blob.kind == "crop")
            ).all()
        )
        version = job.result_version or 0
    return view, result, {c for c in crop_ids if c}, version


def _place_flashes(
    flashes: list[dict[str, Any]],
    result: DocumentResult,
    items: dict[str, ReviewItem],
    groups: dict[str, GroupView],
    cards: dict[str, HeadingGroupView | PageGroupView],
) -> list[Notice]:
    """Put each one-time notice where the reviewer lands: a single decision's notice at
    the next decision still waiting (where the redirect points), linked back to the fix
    that changed; a bulk action's notice in its group, a heading group's on its card (or
    at the top when the whole group is decided). Every notice is also returned for the
    progress summary at the top, which says what just happened and what is left."""
    top: list[Notice] = []
    for entry in flashes:
        message = str(entry.get("message", ""))
        target = entry.get("element_id")
        target = target if isinstance(target, str) else ""
        if target.startswith(GROUP_FLASH):
            group = groups.get(target.removeprefix(GROUP_FLASH))
            if group is not None:
                group.notices.append(message)
            top.append(Notice(message))
            continue
        if target.startswith(HEADING_GROUP_FLASH):
            card = cards.get(target.removeprefix(HEADING_GROUP_FLASH))
            if card is not None:
                card.notices.append(message)
            top.append(Notice(message))
            continue
        changed = items.get(target)
        if changed is None:
            top.append(Notice(message))
            continue
        changed.changed.append(message)
        where = f" on page {changed.page}" if changed.page else ""  # 0: element missing
        notice = Notice(message, changed.label + where, f"#item-{target}", target)
        landing = next_to_review(result, changed.element_id)
        landing_fix = result.fix_for(landing) if landing else None
        if landing_fix is not None and landing_fix.group_key in cards:
            card = cards[landing_fix.group_key]
            card.notices.append(f"{notice.label}: {message}" if notice.label else message)
            changed_fix = result.fix_for(target)
            if changed_fix is not None and changed_fix.group_key == card.key:
                card.members_open = True  # deciding them one by one: keep them in view
        elif landing in items:
            items[landing].notices.append(notice)
        top.append(notice)
    return top


def _apply_invalid(
    invalid: tuple[str, ReviewInvalid],
    items: dict[str, ReviewItem],
    groups: dict[str, GroupView],
    cards: dict[str, HeadingGroupView | PageGroupView],
) -> None:
    target, exc = invalid
    if target in groups:
        groups[target].error = " ".join(e.message for e in exc.errors)
        return
    if target in cards:
        cards[target].error = " ".join(e.message for e in exc.errors)
        cards[target].values.update(exc.values)
        return
    item = items.get(target)
    if item is not None:
        item.values.update(exc.values)
        item.override = exc.override
        for error in exc.errors:
            item.errors[error.field_id.split("-", 1)[0]] = error.message


def _page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    *,
    invalid: tuple[str, ReviewInvalid] | None = None,
    stale: str | None = None,
    status_code: int = 200,
) -> Response:
    view, result, crop_ids, version = _load_versioned(request, job_id, user)
    elements = {e.id: e for e in result.elements}
    numbers = item_numbers(result)
    labels = item_labels(result)
    fix_groups = kept_images_apart(group_fixes(result))
    places = heading_places(result)
    items = {
        f.element_id: _item(
            f,
            elements.get(f.element_id),
            numbers[f.element_id],
            view.id,
            crop_ids,
            places.get(f.element_id),
            labels.get(f.element_id),
        )
        for g in fix_groups
        for f in (*g.pending, *g.optional, *g.done)
    }
    groups = {g.slug: _group(result, g, items, elements, places) for g in fix_groups}
    cards: dict[str, HeadingGroupView | PageGroupView] = {
        unit.group.key: unit.group
        for g in groups.values()
        for unit in g.units
        if unit.group is not None
    }
    cards.update(
        {unit.pages.key: unit.pages for g in groups.values() for unit in g.units if unit.pages}
    )
    top = _place_flashes(pop_flash(request), result, items, groups, cards)
    errors: list[FieldError] = []
    if invalid is not None:
        errors = invalid[1].errors
        _apply_invalid(invalid, items, groups, cards)
    optional = [item for g in groups.values() for item in g.optional]
    done = [item for g in groups.values() for item in g.done]
    first_fix: dict[str, Fix] = {}
    for fix in result.fixes:
        first_fix.setdefault(fix.element_id, fix)
    # T3-7: the notices about links, text and figures, each with where it is on the review
    # screen, listed with the optional checks.
    note_rows = notice_places.rows(
        view.id,
        [
            p
            for p in notice_places.places(result, labels)
            if notice_places.shown_in_review(p, first_fix)
        ],
        f"/jobs/{view.id}/review",
    )
    count = page_edits.page_count(result, view.page_count)
    context: dict[str, Any] = {
        "note_rows": note_rows,
        # T3-5, T3-16: the page tools are on the review screen; one link to each page's.
        "tool_pages": [(n, n - 1 in result.omitted_pages) for n in range(1, count + 1)],
        "job": view,
        "groups": list(groups.values()),
        "deciding": [g for g in groups.values() if g.units or g.error or g.notices],
        "optional": optional,
        "optional_open": any(item.open for item in optional),
        "second_checks": sum(item.second_check for item in optional),
        "done": done,
        "done_open": any(item.open for item in done),
        "errors": [{"field_id": e.field_id, "message": e.message} for e in errors],
        "flash": [],  # placed by _place_flashes instead of at the top of <main>
        "notices": top,
        "remaining": sum(len(g.units) for g in fix_groups),
        "remaining_by_kind": [decisions_label(g.units, g.kind) for g in fix_groups if g.pending],
        "alt_guideline": ALT_WARN_CHARS,
        "result_version": version,
        "undo": bool(top) and undo_offer(request, view.id, user, version),
        "stale": None
        if stale is None
        else stale_notice(result, numbers, stale, list_url(view.id, _stale_anchor(result, stale))),
        # No `active` nav item: this page is not "My documents" itself, so the link
        # must not be announced as the current page (aria-current="page").
    }
    return render(request, "review.html", context, status_code=status_code)


# --------------------------------------------------------------------------- routes

LIST_VIEW = "list"


def list_url(job_id: str, anchor: str) -> str:
    return f"/jobs/{job_id}/review/list#{anchor}"


@router.get("/jobs/{job_id}/review/list")
def review_list(request: Request, job_id: str, user: UserDep) -> Response:
    response = _page(request, job_id, user)
    ctx = get_ctx(request)  # usage event (web/events.py)
    events.record_review_opened(ctx.factory, ctx.settings, user.id, job_id, "list")
    return response


def _split_redirect(job_id: str, result: DocumentResult, after: str | None) -> Response:
    """To the review screen's next decision still open after `after` (an element id or a
    group key; None: the first one still open), or to its done state."""
    from readablestem.web import queue
    from readablestem.web.routes.split import review_url

    decisions = queue.decisions(result)
    current = queue.find(decisions, after) if after else None
    landing = queue.next_open(decisions, current)
    item = landing.key if landing is not None else None
    return RedirectResponse(review_url(job_id, item=item), status_code=303)


def _invalid_page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    view: str,
    target: str,
    exc: ReviewInvalid,
) -> Response:
    """The view the form was on again, with the error and the reviewer's input (422)."""
    if view == LIST_VIEW:
        return _page(request, job_id, user, invalid=(target, exc), status_code=422)
    from readablestem.web.routes.split import split_page

    return split_page(request, job_id, user, item=target, invalid=(target, exc), status_code=422)


def _stale_page(
    request: Request,
    job_id: str,
    user: CurrentUser,
    view: str,
    target: str,
    values: dict[str, str] | None = None,
) -> Response:
    """A write from an out-of-date page: nothing applied; the view the form was on, as it
    is now, says so (409). What the reviewer typed stays in the field, so choosing again
    is one click."""
    log_event(logger, "review_stale", job_id=job_id)
    kept = ReviewInvalid([], values)
    if view == LIST_VIEW:
        return _page(request, job_id, user, invalid=(target, kept), stale=target, status_code=409)
    from readablestem.web.routes.split import split_page

    return split_page(
        request, job_id, user, item=target, invalid=(target, kept), stale=True, status_code=409
    )


def _refuse_stale(job: Job, version: str) -> None:
    """Raise ReviewStale when the form was drawn before the job's last review write."""
    if is_stale(job.result_version, version):
        raise ReviewStale()


@router.post("/jobs/{job_id}/review/{element_id}", dependencies=[Depends(require_csrf)])
def review_action(
    request: Request,
    job_id: str,
    element_id: str,
    user: UserDep,
    action: Annotated[str, Form()] = "",
    latex: Annotated[str | None, Form()] = None,
    alt: Annotated[str | None, Form()] = None,
    decorative: Annotated[str | None, Form()] = None,
    level: Annotated[str | None, Form()] = None,
    view: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "",
    change: Annotated[str | None, Form()] = None,
    row: Annotated[str | None, Form()] = None,
    col: Annotated[str | None, Form()] = None,
    cell: Annotated[str | None, Form()] = None,
    header_row: Annotated[str | None, Form()] = None,
    header_col: Annotated[str | None, Form()] = None,
    # Figures with long descriptions on (web.review `_edited_details`).
    long_description: Annotated[str | None, Form()] = None,
    remove_data_table: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    version: Annotated[str, Form()] = "",
) -> Response:
    """One decision on one fix; when it is a heading group's key ("hg-p1-e7"), one
    decision for every heading of that group still waiting (with `scope=all`, for every
    heading of the group: a decided group decided again). A kind in place of the id
    ("equations"), the bulk accept removed on 2026-10-09, is not found.

    Element ids look like "p3-e12", so neither a kind nor a group key can be one.
    Sharing the route keeps a single CSRF-checked, ownership-checked way to change a
    review. `view` says which view the form was on (module docstring). The fields are
    passed on only when the form sent them: Accept refuses a changed one
    (`web.review.apply_review`), and a field that was not sent was not changed. A table
    edit says what it changes (`change`: "cell" with `row`, `col` and `cell`, "headers"
    with the `header_row` and `header_col` boxes, or "use_local"). `version` is the
    job's `result_version` the form was made from: a form made before another tab
    changed the review is refused with a 409 (`web.review.is_stale`).
    """
    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    view = LIST_VIEW if view == LIST_VIEW else "split"
    if kind_for_slug(element_id) is not None:
        # No bulk accept: every decision asked for is meaning-level, decided one by one.
        raise PageError(404, *NOT_FOUND)
    if is_group_key(element_id):
        every = scope == "all"
        return _group_action(
            request, job_id, element_id, user, action, level or "", view, every, version
        )
    if is_page_group_key(element_id):
        return _page_group_action(request, job_id, element_id, user, action, view, version)
    sent = {
        "latex": latex,
        "alt": alt,
        "decorative": decorative,
        "level": level,
        "change": change,
        "row": row,
        "col": col,
        "cell": cell,
        "header_row": header_row,
        "header_col": header_col,
        "long_description": long_description,
        "remove_data_table": remove_data_table,
        "description": description,
    }
    form = {name: value for name, value in sent.items() if value is not None}
    # "Use my edit anyway": an edit the reviewer chose to use although it differs from
    # the PDF's text where that changes the meaning (web.edit_check).
    anyway = action == EDIT_ANYWAY
    action = "edit" if anyway else action
    ctx = get_ctx(request)
    try:
        with (
            session_scope(ctx.factory) as session,
            notation.in_force(ctx.factory, ctx.settings, job_id),
        ):
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            _refuse_stale(job, version)
            result = DocumentResult.model_validate(job.result_json or {})
            change_made = apply_review(result, element_id, action, form, anyway=anyway)
            job.html_stale = True  # made again when somebody asks for it (`jobs.current_html`)
            job.result_json = result.model_dump(mode="json", exclude={"html"})
            written = bump_result_version(job)
            retention.restart_clock(job, get_ctx(request).settings)
            session.add(_review_edit(job.id, user, change_made, written))
            _decision_events(request, session, user, job, result, "single", 1)
    except LookupError:
        raise PageError(404, *NOT_FOUND) from None
    except ReviewStale:
        return _stale_page(request, job_id, user, view, element_id, _typed(form))
    except ReviewInvalid as exc:
        # `action` is free text from the form: only known values reach the log.
        known = action in STATUS_FOR_ACTION or action in {ADD_DATA_TABLE, DESCRIBE}
        log_event(logger, "review_invalid", job_id=job_id, action=action if known else "unknown")
        return _invalid_page(request, job_id, user, view, element_id, exc)
    log_event(
        logger,
        "review",
        job_id=job_id,
        element_id=element_id,
        action=action,
        status=change_made.fix.status.value,
        # A table edit's kind (a fixed code: anything else was refused).
        change=change if change in TABLE_CHANGES and action == "edit" else None,
        anyway=change_made.anyway,
    )
    table = change_made.fix.kind is Kind.TABLE
    element = next((e for e in result.elements if e.id == element_id), None)
    message = done_message(change_made, element)
    message += _moved_message(change_made.moved, item_numbers(result))
    flash(request, message, element_id=element_id)
    if view != LIST_VIEW:
        if (table and action == "edit") or action in {ADD_DATA_TABLE, DESCRIBE}:
            # A table is often changed more than once (a header, then a cell), and a
            # data table just added is worth a look: the reviewer stays on the item and
            # sees it as it is now.
            from readablestem.web.routes.split import review_url

            return RedirectResponse(review_url(job_id, item=element_id), status_code=303)
        return _split_redirect(job_id, result, element_id)
    return RedirectResponse(_landing(job_id, result, element_id), status_code=303)


def _landing(job_id: str, result: DocumentResult, element_id: str) -> str:
    landing = next_to_review(result, element_id)
    anchor = review_anchor(result, landing) if landing else "review-progress"
    return list_url(job_id, anchor)


GROUP_DONE = {
    "accept": "Kept {n} as they are.",
    "edit": "Set {n} to level {level}.",
    "reject": "Made {n} bold text instead of headings.",
}


def _group_action(
    request: Request,
    job_id: str,
    group_key: str,
    user: CurrentUser,
    action: str,
    level: str,
    view: str,
    every: bool = False,
    version: str = "",
) -> Response:
    try:
        with session_scope(get_ctx(request).factory) as session:
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            _refuse_stale(job, version)
            result = DocumentResult.model_validate(job.result_json or {})
            outcome = apply_group(result, group_key, action, {"level": level}, every=every)
            if outcome.changes:  # nothing left to decide: no write, no re-render
                job.html_stale = True  # made again when somebody asks for it (`jobs.current_html`)
                job.result_json = result.model_dump(mode="json", exclude={"html"})
                written = bump_result_version(job)
                retention.restart_clock(job, get_ctx(request).settings)
                session.add_all(_review_edit(job.id, user, c, written) for c in outcome.changes)
                _decision_events(request, session, user, job, result, "group", len(outcome.changes))
    except LookupError:
        raise PageError(404, *NOT_FOUND) from None
    except ReviewStale:
        return _stale_page(request, job_id, user, view, group_key)
    except ReviewInvalid as exc:
        logged_action = action if action in STATUS_FOR_ACTION else "unknown"
        log_event(logger, "review_invalid", job_id=job_id, action=f"group_{logged_action}")
        return _invalid_page(request, job_id, user, view, group_key, exc)
    log_event(
        logger,
        "review_group",
        job_id=job_id,
        group=group_key,
        action=action,
        changed=len(outcome.changes),
        left=len(outcome.left),
    )
    message = _group_message(action, level, outcome, every=every)
    flash(request, message, element_id=HEADING_GROUP_FLASH + group_key)
    if view != LIST_VIEW:
        if outcome.left:
            # Headings left waiting (the output would move their level) keep the group
            # open: the reviewer stays on it to choose a level for them.
            from readablestem.web.routes.split import review_url

            return RedirectResponse(review_url(job_id, item=group_key), status_code=303)
        return _split_redirect(job_id, result, group_key)
    landing = None
    if not outcome.left and outcome.changes:
        landing = next_to_review(result, outcome.changes[-1].fix.element_id)
    if outcome.left or not outcome.changes:
        anchor = group_anchor(group_key) if outcome.left else "review-progress"
    else:
        anchor = review_anchor(result, landing) if landing else "review-progress"
    return RedirectResponse(list_url(job_id, anchor), status_code=303)


def _page_group_action(
    request: Request,
    job_id: str,
    group_key: str,
    user: CurrentUser,
    action: str,
    view: str,
    version: str = "",
) -> Response:
    """Keep every page (or piece of handwriting) of a group still waiting as an image,
    marked "Not converted" (`web.review.apply_page_group`). Its only action is "reject",
    as "Keep as image" is for one figure."""
    try:
        if action != "reject":
            message = "Choose “Keep”: these images have nothing else to accept."
            raise ReviewInvalid([FieldError(f"group-actions-{group_key}", message)])
        with session_scope(get_ctx(request).factory) as session:
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            _refuse_stale(job, version)
            result = DocumentResult.model_validate(job.result_json or {})
            outcome = apply_page_group(result, group_key)
            if outcome.changes:  # nothing left to decide: no write, no re-render
                job.html_stale = True  # made again when somebody asks for it (`jobs.current_html`)
                job.result_json = result.model_dump(mode="json", exclude={"html"})
                written = bump_result_version(job)
                retention.restart_clock(job, get_ctx(request).settings)
                session.add_all(_review_edit(job.id, user, c, written) for c in outcome.changes)
                _decision_events(request, session, user, job, result, "group", len(outcome.changes))
    except LookupError:
        raise PageError(404, *NOT_FOUND) from None
    except ReviewStale:
        return _stale_page(request, job_id, user, view, group_key)
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action="page_group_unknown")
        return _invalid_page(request, job_id, user, view, group_key, exc)
    log_event(
        logger, "review_page_group", job_id=job_id, group=group_key, changed=len(outcome.changes)
    )
    n = len(outcome.changes)
    if n:
        message = f"Kept {n} as {'an image' if n == 1 else 'images'}, marked “Not converted”."
    else:
        message = "Nothing changed: none of these were waiting for a decision."
    flash(request, message, element_id=HEADING_GROUP_FLASH + group_key)
    if view != LIST_VIEW:
        return _split_redirect(job_id, result, group_key)
    landing = next_to_review(result, outcome.changes[-1].fix.element_id) if n else None
    anchor = review_anchor(result, landing) if landing else "review-progress"
    return RedirectResponse(list_url(job_id, anchor), status_code=303)


# A decided group decided again ("Change all 11" on the review screen).
GROUP_REDONE = {
    "accept": "Changed {n} back to headings.",
    "edit": "Set {n} to level {level}.",
    "reject": "Made {n} bold text instead of headings.",
}


def _group_message(action: str, level: str, outcome: GroupOutcome, *, every: bool = False) -> str:
    n = len(outcome.changes)
    if not n:
        if every:
            return "Nothing changed: these headings were already like that."
        return "Nothing changed: none of these headings were waiting for a decision."
    if every:  # after "Repeated heading “Teacher Support”: " (the notice's label)
        message = GROUP_REDONE.get(action, "Done.").format(n=n, level=level)
    else:
        message = GROUP_DONE.get(action, "Done.").format(n=counted(n, Kind.HEADING), level=level)
    if outcome.left and every:
        left = counted(len(outcome.left), Kind.HEADING)
        message += (
            f" {left} {'was' if len(outcome.left) == 1 else 'were'} left as before: the "
            "document would show them at another level."
        )
    elif outcome.left:
        left = counted(len(outcome.left), Kind.HEADING)
        message += (
            f" {left} {'was' if len(outcome.left) == 1 else 'were'} left: the document "
            "would show them at another level, so choose a level for them."
        )
    return message


def _crops(session: Session, job_id: str) -> dict[str, bytes]:
    blobs = session.scalars(select(Blob).where(Blob.job_id == job_id, Blob.kind == "crop"))
    return {b.element_id: b.content for b in blobs if b.element_id}


def _decision_events(
    request: Request,
    session: Session,
    user: CurrentUser,
    job: Job,
    result: DocumentResult,
    kind: str,
    changed: int,
) -> None:
    """Usage events of a review write (web/events.py), in its transaction:
    `decision_made`, and `review_completed` when no decision is left open."""
    events.add_decision(
        session,
        get_ctx(request).settings,
        user_id=user.id,
        job=job,
        kind=kind,
        changed=changed,
        open_after=triage.open_decisions(result.fixes),
    )


MOVED_NAMED = 5  # name up to this many moved headings; count any more


def _moved_message(moved: list[str], numbers: dict[str, int]) -> str:
    """' Heading 3 is now shown at a different level.': the other headings a heading
    decision moved in the output, so the change to them is not silent."""
    if not moved:
        return ""
    if len(moved) > MOVED_NAMED:
        return f" {len(moved)} later headings are now shown at a different level."
    names = [str(numbers[eid]) for eid in moved]
    if len(names) == 1:
        return f" Heading {names[0]} is now shown at a different level."
    listed = ", ".join(names[:-1]) + " and " + names[-1]
    return f" Headings {listed} are now shown at a different level."


def drop_crops(session: Session, job_id: str, element_ids: list[str]) -> None:
    """Delete the stored crops of these elements (an undone creation)."""
    if element_ids:
        session.execute(
            delete(Blob).where(
                Blob.job_id == job_id, Blob.kind == "crop", Blob.element_id.in_(element_ids)
            )
        )


def _review_edit(
    job_id: str, user: CurrentUser, change: ReviewChange | StructureChange, version: int
) -> ReviewEdit:
    """One fix's change. Its `after` snapshot also records the review version the write
    made (`web.review.WRITE_KEY`): the edits of the last write are the ones Undo puts
    back."""
    return ReviewEdit(
        job_id=job_id,
        user_id=user.id,
        element_id=change.element_id,
        action=change.action,
        before=change.before,
        after=change.after | {WRITE_KEY: version},
    )


# Fields typed into a form, kept when it is refused as stale (a table's cell reopens with
# them: `split._apply_invalid`).
TYPED_FIELDS = frozenset(
    {"latex", "alt", "decorative", "level", "row", "col", "cell", "long_description", "description"}
)


def _typed(form: dict[str, str]) -> dict[str, str]:
    """The fields of a refused form, to show again as they were typed."""
    return {k: v for k, v in form.items() if k in TYPED_FIELDS}


# --------------------------------------------------------------------------- undo


def last_write(session: Session, job_id: str, version: int) -> list[ReviewEdit]:
    """The ReviewEdits of the job's last review write (the one that made `version`), in
    the order they were made. Empty when that write left none that says so (it was made
    before writes were numbered)."""
    edits = session.scalars(
        select(ReviewEdit)
        .where(ReviewEdit.job_id == job_id)
        .order_by(ReviewEdit.created_at, ReviewEdit.id)
    ).all()
    return [e for e in edits if isinstance(e.after, dict) and e.after.get(WRITE_KEY) == version]


def can_undo(edits: list[ReviewEdit]) -> bool:
    """Undo is offered for the last write when it was a decision (not an Undo itself)
    and every fix it changed can be put back."""
    return bool(edits) and all(e.action != UNDO_ACTION and restorable(e.before) for e in edits)


def undo_offer(request: Request, job_id: str, user: CurrentUser, version: int) -> bool:
    """Whether the notice after a decision offers Undo (one small query)."""
    with session_scope(get_ctx(request).factory) as session:
        owned_job(session, job_id, user)
        return can_undo(last_write(session, job_id, version))


UNDO_PENDING = "Undone. {it} waiting for your decision again."
UNDO_BACK = "Undone. {it} back as {was}."


@router.post("/jobs/{job_id}/undo", dependencies=[Depends(require_csrf)])
def undo(
    request: Request,
    job_id: str,
    user: UserDep,
    version: Annotated[str, Form()] = "",
    view: Annotated[str, Form()] = "",
) -> Response:
    """Undo the job's last review write: every fix it changed goes back to what its
    ReviewEdit's `before` recorded, the HTML is rendered again, and the undo is a write
    of its own (counted, and recorded as "undo" ReviewEdits). Only from a page drawn
    after that write (`version` must be the job's): Undo means "what I just did", never
    something another tab did since."""
    view = LIST_VIEW if view == LIST_VIEW else "split"
    try:
        with session_scope(get_ctx(request).factory) as session:
            job = owned_job(session, job_id, user, lock=True)
            require_done(job)
            if not version.strip():
                raise ReviewStale()
            _refuse_stale(job, version)
            edits = last_write(session, job.id, job.result_version or 0)
            if not can_undo(edits):
                raise LookupError("nothing to undo")
            result = DocumentResult.model_validate(job.result_json or {})
            changes = undo_last(result, [(e.element_id, e.before or {}) for e in edits])
            for change in changes:
                if isinstance(change, StructureChange) and change.removed:
                    drop_crops(session, job.id, change.removed)
            job.html_stale = True  # made again when somebody asks for it (`jobs.current_html`)
            job.result_json = result.model_dump(mode="json", exclude={"html"})
            written = bump_result_version(job)
            retention.restart_clock(job, get_ctx(request).settings)
            session.add_all(_review_edit(job.id, user, c, written) for c in changes)
    except ReviewStale:
        log_event(logger, "review_stale", job_id=job_id)
        flash(request, "Nothing was undone: this document changed after this page was opened.")
        return _back(job_id, view)
    except LookupError:
        flash(request, "There is nothing to undo here.")
        return _back(job_id, view)
    log_event(logger, "review_undo", job_id=job_id, changed=len(changes))
    from readablestem.web.routes.split import review_url

    structural = [c for c in changes if isinstance(c, StructureChange)]
    if structural:
        # An edit to the text, a page or something added: back on the page it was on.
        flash(request, "Undone. The document is as it was before.", element_id=None)
        page = structural[0].page
        where = review_url(
            job_id, page=page + 1 if page is not None else None, anchor="page-tools", tools=True
        )
        if view == LIST_VIEW:
            where = list_url(job_id, "review-progress")
        return RedirectResponse(where, status_code=303)
    fixes = [c.fix for c in changes if isinstance(c, ReviewChange)]
    many = len(fixes) > 1
    words = {"it": "They are" if many else "It is", "was": "they were" if many else "it was"}
    message = (UNDO_PENDING if any(f.pending for f in fixes) else UNDO_BACK).format(**words)
    keys = {f.group_key for f in fixes}
    if len(fixes) == 1:
        target, flashed = fixes[0].element_id, fixes[0].element_id
    elif len(keys) == 1 and None not in keys:
        target = str(next(iter(keys)))
        flashed = HEADING_GROUP_FLASH + target
    else:  # a bulk accept
        slug = GROUP_SLUGS.get(fixes[0].kind, "")
        target, flashed = slug, GROUP_FLASH + slug
    flash(request, message, element_id=flashed)
    if view == LIST_VIEW:
        return RedirectResponse(list_url(job_id, _stale_anchor(result, target)), status_code=303)
    item = target if target and kind_for_slug(target) is None else None
    return RedirectResponse(review_url(job_id, item=item), status_code=303)


def _back(job_id: str, view: str) -> Response:
    if view == LIST_VIEW:
        return RedirectResponse(list_url(job_id, "review-progress"), status_code=303)
    from readablestem.web.routes.split import review_url

    return RedirectResponse(review_url(job_id), status_code=303)
