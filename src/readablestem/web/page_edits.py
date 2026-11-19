"""Edits to the document's structure that no fix covers (T3-5, T3-16).

A review decision (`web.review`) changes a fix. These change something else: the text of a
paragraph, a line made a heading, a picture or an equation the tool did not find, a page
left out of review and of the student file. Each is applied to a `DocumentResult` in place
and returns a `StructureChange` the route stores as a `ReviewEdit`, like a decision.

Undo has to put these back too, and a fix snapshot (`web.review.snapshot`) does not hold an
element, a page's contents or the reading order. So each change here records the state of
what it touched before it ran (`capture`), under `STRUCTURE_KEY` in the edit's `before`,
and `restore` puts exactly that back. `web.review.undo_last` calls it for such an edit, and
the routes drop the crops of elements an undo removes.

Content: a snapshot holds the document's own text (it is deleted with the job, like every
`ReviewEdit`); nothing here is logged.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from readablestem.core import inline, triage
from readablestem.core.page_checks import make
from readablestem.core.types import (
    INLINE_MARKER_RE,
    PENDING_STATUSES,
    DocumentResult,
    Element,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    LeftOutBlock,
    Notice,
    OmittedPage,
    Role,
    Status,
    inline_marker,
)
from readablestem.web.review import (
    STRUCTURE_KEY,
    FieldError,
    ReviewInvalid,
    _recount,
    field_id,
    item_numbers,
)

PAGE_OMITTED = "page_omitted"
BLOCK_OMITTED = "block_omitted"


def page_key(page: int) -> str:
    """The id a page-level edit is recorded under in a `ReviewEdit` ("page-3", 1-based)."""
    return f"page-{page + 1}"


@dataclass
class StructureChange:
    """One structural edit, to be stored as a `ReviewEdit` (it has what
    `routes.review._review_edit` reads from a `web.review.ReviewChange`).

    `element_id`: what the edit is recorded under (an element, or a page: `page_key`).
    `message`: the notice after it. `crops`: PNGs of elements it created, for the route
    to store; `warnings`: failed checks to mention."""

    element_id: str
    action: str
    before: dict[str, Any]
    after: dict[str, Any]
    message: str = ""
    crops: dict[str, bytes] = field(default_factory=dict)
    warnings: list[Any] = field(default_factory=list)
    # The element a created thing is shown at afterwards (None: stay on the page).
    shows: str | None = None
    # Elements an undo removed: their crops go too.
    removed: list[str] = field(default_factory=list)
    # The 0-based page it happened on, to come back to (None: unknown).
    page: int | None = None


# --------------------------------------------------------------------------- snapshots


def capture(
    result: DocumentResult,
    *,
    elements: Iterable[str] = (),
    fixes: Iterable[str] = (),
    orders: bool = False,
    pages: bool = False,
    blocks: bool = False,
    page: int | None = None,
) -> dict[str, Any]:
    """The state of what a change is about to touch, as JSON (`restore` puts it back).

    `elements` and `fixes`: ids whose element or fix is saved as it is now (None when there
    is none, so undoing the creation of one removes it). `orders`: every element's place in
    the reading order. `pages`: the omitted pages and the notices (an omission moves both).
    `blocks`: the paragraphs left out and the notices (leaving one out moves both).
    """
    by_element = {e.id: e for e in result.elements}
    by_fix = {f.element_id: f for f in result.fixes}
    state: dict[str, Any] = {
        "elements": {
            i: by_element[i].model_dump(mode="json") if i in by_element else None
            for i in dict.fromkeys(elements)
        },
        "fixes": {
            i: by_fix[i].model_dump(mode="json") if i in by_fix else None
            for i in dict.fromkeys(fixes)
        },
    }
    if page is not None:
        state["page"] = page
    if orders:
        state["orders"] = {e.id: e.order for e in result.elements}
    if pages:
        state["omitted"] = [o.model_dump(mode="json") for o in result.omitted]
        state["notices"] = [n.model_dump(mode="json") for n in result.notices]
        state["image_only"] = list(result.image_only_pages)
    if blocks:
        state["left_out"] = [b.model_dump(mode="json") for b in result.left_out_blocks]
        state["notices"] = [n.model_dump(mode="json") for n in result.notices]
    return state


def restore(result: DocumentResult, state: dict[str, Any]) -> list[str]:
    """Put back what `capture` saved. Returns the ids of elements this removed (an undone
    creation), so the route can drop their crops. Raises ValueError for a state that does
    not validate (nothing is then changed)."""
    elements = {i: (Element.model_validate(d) if d else None) for i, d in state["elements"].items()}
    fixes = {i: (Fix.model_validate(d) if d else None) for i, d in state["fixes"].items()}
    omitted = (
        [OmittedPage.model_validate(d) for d in state["omitted"]] if "omitted" in state else None
    )
    notices = [Notice.model_validate(d) for d in state["notices"]] if "notices" in state else None
    left_out = (
        [LeftOutBlock.model_validate(d) for d in state["left_out"]] if "left_out" in state else None
    )
    removed: list[str] = []
    for element_id, element in elements.items():
        position = next((n for n, e in enumerate(result.elements) if e.id == element_id), None)
        if element is None and position is not None:
            del result.elements[position]
            removed.append(element_id)
        elif element is not None and position is None:
            result.elements.append(element)
        elif element is not None and position is not None:
            result.elements[position] = element
    for element_id, fix in fixes.items():
        position = next((n for n, f in enumerate(result.fixes) if f.element_id == element_id), None)
        if fix is None and position is not None:
            del result.fixes[position]
        elif fix is not None and position is None:
            result.fixes.append(fix)
        elif fix is not None and position is not None:
            result.fixes[position] = fix
    for element in result.elements:
        if "orders" in state and element.id in state["orders"]:
            element.order = int(state["orders"][element.id])
    if omitted is not None:
        result.omitted = omitted
    if notices is not None:
        result.notices = notices
    if left_out is not None:
        result.left_out_blocks = left_out
    if "image_only" in state:
        result.image_only_pages = [int(p) for p in state["image_only"]]
    _recount(result)
    return removed


def undo(result: DocumentResult, element_id: str, state: dict[str, Any]) -> StructureChange:
    """Put back the state one structural edit saved (`capture`). Raises ValueError,
    KeyError or TypeError for a state that does not fit (the route turns it into "nothing
    to undo")."""
    current = capture(
        result,
        elements=state["elements"],
        fixes=state["fixes"],
        orders="orders" in state,
        pages="omitted" in state,
        blocks="left_out" in state,
    )
    removed = restore(result, state)
    page = state.get("page")
    return StructureChange(
        element_id=element_id,
        action="undo",
        before={STRUCTURE_KEY: current},
        after={},
        removed=removed,
        page=page if isinstance(page, int) else None,
    )


# --------------------------------------------------------------------------- pages left out

LAST_PAGE = "At least one page has to stay in the file."
NO_SUCH_PAGE = "There is no such page."
ALREADY_OUT = "That page is already left out."
NOT_OUT = "That page is not left out."


def page_count(result: DocumentResult, job_pages: int = 0) -> int:
    """How many pages the document has (the job's count, or what the result shows)."""
    seen = [job_pages, result.stats.pages, *(e.page + 1 for e in result.elements)]
    seen += [o.page + 1 for o in result.omitted]
    return max(1, *seen)


def omit_page(result: DocumentResult, page: int, *, pages: int) -> StructureChange:
    """Leave page `page` (0-based) out of review and of the student file: its elements,
    fixes and notices move into `result.omitted`, so nothing downstream sees them, and a
    notice says so. Nothing is deleted; `restore_page` puts it back. Refused for a page
    that is already out and for the last page left. Raises ReviewInvalid."""
    fid = field_id("page", str(page + 1))
    if not 0 <= page < pages:
        raise ReviewInvalid([FieldError(fid, NO_SUCH_PAGE)])
    if page in result.omitted_pages:
        raise ReviewInvalid([FieldError(fid, ALREADY_OUT)])
    if pages - len(result.omitted) <= 1:
        raise ReviewInvalid([FieldError(fid, LAST_PAGE)])
    on_page = [e for e in result.elements if e.page == page]
    ids = {e.id for e in on_page}
    moved_fixes = [f for f in result.fixes if f.element_id in ids]
    before = capture(
        result,
        elements=[e.id for e in on_page],
        fixes=[f.element_id for f in moved_fixes],
        pages=True,
        page=page,
    )
    moved_notices = [n for n in result.notices if n.page == page]
    was_image = page in result.image_only_pages
    result.omitted.append(
        OmittedPage(
            page=page,
            elements=on_page,
            fixes=moved_fixes,
            notices=moved_notices,
            image_only=was_image,
        )
    )
    result.image_only_pages = [p for p in result.image_only_pages if p != page]
    result.elements = [e for e in result.elements if e.id not in ids]
    result.fixes = [f for f in result.fixes if f.element_id not in ids]
    result.notices = [n for n in result.notices if n.page != page]
    result.notices.append(make(PAGE_OMITTED, page=page))
    _recount(result)
    after = capture(result, pages=True)
    return StructureChange(
        element_id=page_key(page),
        action="omit_page",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: after},
        message=f"Page {page + 1} is left out of the student file and of the review.",
    )


def restore_page(result: DocumentResult, page: int) -> StructureChange:
    """Put a page left out back (`omit_page`): its elements, fixes and notices return, and
    the notice that it was left out goes. Raises ReviewInvalid."""
    fid = field_id("page", str(page + 1))
    kept = next((o for o in result.omitted if o.page == page), None)
    if kept is None:
        raise ReviewInvalid([FieldError(fid, NOT_OUT)])
    before = capture(
        result,
        elements=[e.id for e in kept.elements],
        fixes=[f.element_id for f in kept.fixes],
        pages=True,
        page=page,
    )
    result.omitted = [o for o in result.omitted if o.page != page]
    if kept.image_only:
        result.image_only_pages = sorted({*result.image_only_pages, page})
    result.elements.extend(kept.elements)
    result.fixes.extend(kept.fixes)
    result.notices = [n for n in result.notices if not (n.code == PAGE_OMITTED and n.page == page)]
    result.notices.extend(kept.notices)
    _recount(result)
    return StructureChange(
        element_id=page_key(page),
        action="restore_page",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, pages=True)},
        message=f"Page {page + 1} is back in the student file and in the review.",
    )


# --------------------------------------------------------------------------- text (T3-5)

MAX_TEXT_CHARS = 6000
TEXT_KINDS = (Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.HEADING)
# Characters a PDF or a pasted text can carry that mean nothing to a reader (controls,
# NUL: Postgres refuses it in JSON); newline and tab stay for program code.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_TOKEN = re.compile(r"\[equation (\d+)\]")
NOTHING_CHANGED = "Nothing changed: this is the text it already has."
MARK_LEFT_OUT = "Keep the mark {token} in the text, or remove the equation as well."
MARK_REPEATED = (
    "“{token}” is there twice. Keep it once, where the equation belongs: "
    "the equation itself is changed in its own decision."
)
NOT_TEXT = "That is not a paragraph, a list item or a heading."
CODE_NOTE = "program code"


def _fid(prefix: str, element_id: str) -> str:
    return field_id(prefix, element_id)


def equation_tokens(
    result: DocumentResult, element: Element
) -> tuple[dict[str, str], dict[str, str]]:
    """The inline equations of an element's text, named as the review names them: marker
    id -> "[equation 3]" and back. A reviewer types the name; the marker (which no one
    could type) is put back when the text is saved."""
    to_token: dict[str, str] = {}
    from_token: dict[str, str] = {}
    numbers: dict[str, int] | None = None  # the whole document's numbering: only when needed
    for match in INLINE_MARKER_RE.finditer(element.text or ""):
        marker_id = match.group(1)
        if numbers is None:
            numbers = item_numbers(result)
        number = numbers.get(marker_id)
        token = f"[equation {number}]" if number else f"[equation {marker_id}]"
        to_token[marker_id] = token
        from_token[token] = marker_id
    return to_token, from_token


def friendly_text(result: DocumentResult, element: Element, fix: Fix | None = None) -> str:
    """The words to show in the box: an inline equation as "[equation 3]". A heading shows
    the text students get (the fix's), else the element's."""
    text = element.text or ""
    if element.kind is Kind.HEADING and fix is not None and fix.heading is not None:
        text = fix.heading.text or text
    to_token, _ = equation_tokens(result, element)
    return INLINE_MARKER_RE.sub(lambda m: to_token.get(m.group(1), m.group(0)), text)


def clean_text(typed: str, *, preformatted: bool) -> str:
    """Typed words as they are stored: controls and invisible format characters out. Whitespace
    collapsed to single spaces, as the PDF's text is; program code keeps its lines and
    indentation (trailing blanks off)."""
    from readablestem.pipeline import validate

    text = _CONTROL.sub("", typed.replace("\r\n", "\n").replace("\r", "\n"))
    if not preformatted:
        return validate.clean_alt(text)
    kept = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return "\n".join(line.rstrip() for line in kept.split("\n")).strip("\n")


def _char_map(old: str, new: str) -> list[int | None]:
    """For each character of `old`, where it is in `new` when the two texts agree there."""
    mapping: list[int | None] = [None] * len(old)
    for tag, i1, i2, j1, _ in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            for n in range(i2 - i1):
                mapping[i1 + n] = j1 + n
    return mapping


def _moved(mapping: list[int | None], start: int, end: int) -> tuple[int, int] | None:
    """A span [start, end) of the old text in the new one: only when every character of it
    is still there, side by side."""
    if not 0 <= start < end <= len(mapping):
        return None
    first, last = mapping[start], mapping[end - 1]
    if first is None or last is None or last - first != end - 1 - start:
        return None
    if any(mapping[i] != first + i - start for i in range(start, end)):
        return None
    return first, last + 1


def remap_spans(old: str, new: str, element: Element) -> tuple[dict[str, Any], Counter[str]]:
    """The element's spans (links, colour, raised and lowered runs, struck text:
    `core.inline`) and glossary term, moved to where their words are in the new text. A
    span whose words were changed is dropped (it would cover other words). Returns (fields
    to update, how many spans were dropped, by kind: "link", "deleted", ...)."""
    mapping = _char_map(old, new)
    update, dropped = inline.moved(element, lambda start, end: _moved(mapping, start, end))
    if element.term_end is not None:
        # The glossary term stays a term only while its own words are unchanged; then the
        # definition may change freely.
        end = element.term_end
        update["term_end"] = end if new[:end] == old[:end] else None
    return update, dropped


def _taken_off(dropped: Counter[str]) -> str:
    """The sentence that says what an edit took off (" 1 link was taken off because its
    words changed."): links and struck-text marks, which a reader would miss. A colour or a
    raised run is only the look of the words that changed, and is not counted."""
    named = [
        (dropped["link"], "link", "links"),
        (dropped["deleted"], "struck-text mark", "struck-text marks"),
    ]
    parts = [f"{n} {one if n == 1 else many}" for n, one, many in named if n]
    if not parts:
        return ""
    total = sum(n for n, _, _ in named)
    were, their = ("was", "its") if total == 1 else ("were", "their")
    return f" {' and '.join(parts)} {were} taken off because {their} words changed."


def text_element(result: DocumentResult, element_id: str) -> Element:
    element = next((e for e in result.elements if e.id == element_id), None)
    if element is None:
        raise LookupError(element_id)
    return element


def edit_text(result: DocumentResult, element_id: str, typed: str) -> StructureChange:
    """Change the words of a paragraph, a list item or a heading. The text is cleaned like a
    figure's alt text, an inline equation stays where its "[equation N]" is (the equation
    is changed in its own decision), links and colours stay on the words that did not
    change, and the HTML is made again from the stored result. Raises ReviewInvalid with
    nothing changed, LookupError for an element that is not there."""
    element = text_element(result, element_id)
    fid = _fid("text", element_id)
    values = {"text": typed}
    if element.kind not in TEXT_KINDS:
        raise ReviewInvalid([FieldError(fid, NOT_TEXT)], values)
    fix = next((f for f in result.fixes if f.element_id == element_id), None)
    heading = element.kind is Kind.HEADING and fix is not None and fix.heading is not None
    old_shown = friendly_text(result, element, fix)
    _, from_token = equation_tokens(result, element)
    too_long = f"Keep the text under {MAX_TEXT_CHARS} characters."
    if len(typed) > MAX_TEXT_CHARS * 2:
        raise ReviewInvalid([FieldError(fid, too_long)], values)
    text = clean_text(typed, preformatted=element.preformatted)
    values = {"text": text}
    if len(text) > MAX_TEXT_CHARS:
        raise ReviewInvalid([FieldError(fid, too_long)], values)
    if not text:
        raise ReviewInvalid([FieldError(fid, "Enter the words of this text.")], values)
    if text == clean_text(old_shown, preformatted=element.preformatted):
        raise ReviewInvalid([FieldError(fid, NOTHING_CHANGED)], values)
    # Put the markers back, and check that every equation is still there exactly once.
    stored = _TOKEN.sub(lambda m: _marker_for(m, from_token), text)
    left = Counter(m.group(1) for m in INLINE_MARKER_RE.finditer(stored))
    if set(left) - set(from_token.values()):
        message = "The marks ⟦ ⟧ are the tool's own, for equations. Leave them out of the text."
        raise ReviewInvalid([FieldError(fid, message)], values)
    for marker_id in from_token.values():
        if left.get(marker_id, 0) != 1:
            token = equation_tokens(result, element)[0][marker_id]
            template = MARK_REPEATED if left.get(marker_id, 0) > 1 else MARK_LEFT_OUT
            message = template.format(token=token)
            raise ReviewInvalid([FieldError(fid, message)], values)
    old = element.text or ""
    before = capture(
        result, elements=[element_id], fixes=[element_id] if heading else (), page=element.page
    )
    update, dropped = remap_spans(old, stored, element)
    for index, current in enumerate(result.elements):
        if current.id == element_id:
            result.elements[index] = current.model_copy(update={"text": stored, **update})
    if heading and fix is not None and fix.heading is not None:
        fix.heading = HeadingFix(level=fix.heading.level, text=stored)
    _recount(result)
    note = _taken_off(dropped)
    return StructureChange(
        element_id=element_id,
        action="edit_text",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=[element_id], page=element.page)},
        message="The text was changed." + note,
    )


def _marker_for(match: re.Match[str], from_token: dict[str, str]) -> str:
    marker_id = from_token.get(match.group(0))
    return inline_marker(marker_id) if marker_id else match.group(0)


# --------------------------------------------------------------------------- a line made a heading

NOT_A_PARAGRAPH = "Only a paragraph can be made a heading."
HEADING_CODE = "Program code and glossary entries stay as they are; they are not headings."
HEADING_TOO_LONG = "A heading is a line, not a paragraph: it is too long to be one."
MAX_HEADING_CHARS = 300


def make_heading(result: DocumentResult, element_id: str, level_text: str) -> StructureChange:
    """Make a paragraph a heading at the chosen level. It becomes a heading element with a
    fix the reviewer made (so it is theirs to take back: "Make plain text"), and the later
    headings are repaired in the outline as they always are (`web.review.heading_places`).
    Refused for anything but a plain paragraph, for a level the outline would change (with
    the levels that fit) and for text too long to be a heading. Raises ReviewInvalid and
    LookupError; nothing is changed then."""
    from readablestem.web.review import (
        _LEVEL,
        _moved_by,
        _refuse_moved_level,
        heading_places,
    )

    element = text_element(result, element_id)
    fid = _fid("level", element_id)
    values = {"level": level_text}
    if element.kind is not Kind.PARAGRAPH:
        raise ReviewInvalid([FieldError(fid, NOT_A_PARAGRAPH)], values)
    if element.preformatted or element.term_end is not None:
        raise ReviewInvalid([FieldError(fid, HEADING_CODE)], values)
    if len(element.text or "") > MAX_HEADING_CHARS or "\n" in (element.text or ""):
        raise ReviewInvalid([FieldError(fid, HEADING_TOO_LONG)], values)
    if not _LEVEL.fullmatch(level_text):
        raise ReviewInvalid([FieldError(fid, "Choose a heading level from 1 to 6.")], values)
    level = int(level_text)
    _refuse_moved_level(result, element_id, level, values=values)
    places_before = heading_places(result)
    before = capture(result, elements=[element_id], fixes=[element_id], page=element.page)
    for index, current in enumerate(result.elements):
        if current.id == element_id:
            result.elements[index] = current.model_copy(update={"kind": Kind.HEADING, "role": None})
    result.fixes.append(
        Fix(
            element_id=element_id,
            kind=Kind.HEADING,
            status=Status.EDITED,
            heading=HeadingFix(level=level, text=element.text or ""),
            reviewed=True,
            review="none",
        )
    )
    _recount(result)
    moved = _moved_by(places_before, heading_places(result), element_id)
    message = f"It is now a heading, level {level}."
    if moved:
        many = len(moved) > 1
        message += (
            f" {len(moved)} later heading{'s' if many else ''} {'are' if many else 'is'} "
            "now shown at another level."
        )
    return StructureChange(
        element_id=element_id,
        action="make_heading",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=[element_id], fixes=[element_id])},
        message=message,
    )


# --------------------------------------------------------------------------- a paragraph's role

ROLE_WORDS = {"aside": "sidebar", "quote": "quotation"}
NOT_A_ROLE_PARAGRAPH = "Only a paragraph can be a sidebar or a quotation."
ROLE_CODE = "Code, glossary entries, footnotes and list items cannot be a sidebar or a quotation."
CHOOSE_ROLE = "Choose a sidebar or a quotation."


def role_blocked(result: DocumentResult, element: Element) -> str:
    """Why a block cannot carry a role, or "" when it can. The student page writes a role
    only around a plain paragraph (`pipeline.assemble.Writer.paragraph`): not around code, a
    glossary entry, a footnote or a list item, so none of those is offered one."""
    if element.kind is not Kind.PARAGRAPH:
        return NOT_A_ROLE_PARAGRAPH
    if (
        element.preformatted
        or element.term_end is not None
        or element.list_marker
        or any(n.code == "footnote" and n.element_id == element.id for n in result.notices)
    ):
        return ROLE_CODE
    return ""


def set_role(result: DocumentResult, element_id: str, role: str | None) -> StructureChange:
    """Make a paragraph a sidebar (`aside`) or a quotation (`quote`), or give it neither again
    (`role` None or ""). Refused for what cannot carry a role (`role_blocked`), for a word that
    is neither role, and for a paragraph that already is as asked. Raises ReviewInvalid and
    LookupError; nothing is changed then. The student page, the Word file, the EPUB and Listen
    all read the stored role, so each shows it with no change of its own."""
    element = text_element(result, element_id)
    fid = _fid("role", element_id)
    values = {"role": role or ""}
    if role and role not in ROLE_WORDS:
        raise ReviewInvalid([FieldError(fid, CHOOSE_ROLE)], values)
    if blocked := role_blocked(result, element):
        raise ReviewInvalid([FieldError(fid, blocked)], values)
    new_role: Role | None = "aside" if role == "aside" else "quote" if role == "quote" else None
    if new_role == element.role:
        raise ReviewInvalid([FieldError(fid, NOTHING_CHANGED)], values)
    before = capture(result, elements=[element_id], page=element.page)
    for index, current in enumerate(result.elements):
        if current.id == element_id:
            result.elements[index] = current.model_copy(update={"role": new_role})
    message = (
        f"It is now a {ROLE_WORDS[new_role]}."
        if new_role
        else f"It is no longer a {ROLE_WORDS[element.role or 'aside']}."
    )
    return StructureChange(
        element_id=element_id,
        action="set_role",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=[element_id])},
        message=message,
    )


# --------------------------------------------------------------------------- one block left out

NOT_A_TEXT_BLOCK = (
    "Only a paragraph or a list item can be left out. An equation, a figure or a table is "
    "changed in its own decision, and a heading is made plain text first, in its own decision "
    "(the outline would change)."
)
BLOCK_DECISION = "A decision is still waiting on this text. Decide it first."
BLOCK_FOOTNOTE = "A mark in the text above links to this footnote, so it stays."
BLOCK_LINKED = "Another part of the page links to this text, so it stays."
BLOCK_EQUATION = "It holds an equation, which has its own decision. Decide that first."
BLOCK_LAST = "At least one paragraph has to stay in the file."
BLOCK_ALREADY_OUT = "That text is already left out."
BLOCK_NOT_OUT = "That text is not left out."


def left_out_blocked(result: DocumentResult, element: Element) -> str:
    """Why a paragraph or list item cannot be left out, or "" when it can. What something
    else points at by id would dangle (a footnote its mark links to, a cross-reference or
    link to it, an inline equation inside it) and a decision that is open would be buried:
    each of those is refused, so nothing a student might need goes without the reviewer
    seeing why. Equations, figures, tables and headings have their own decisions."""
    if element.kind not in (Kind.PARAGRAPH, Kind.LIST_ITEM):
        return NOT_A_TEXT_BLOCK
    if any(f.element_id == element.id and f.pending for f in result.fixes):
        return BLOCK_DECISION
    if any(n.code == "footnote" and n.element_id == element.id for n in result.notices):
        return BLOCK_FOOTNOTE
    if any(e.parent_id == element.id for e in result.elements) or INLINE_MARKER_RE.search(
        element.text or ""
    ):
        return BLOCK_EQUATION
    if any(
        link.target_id == element.id
        for e in result.elements
        if e.id != element.id
        for link in e.links
    ):
        return BLOCK_LINKED
    return ""


def _block_notice(result: DocumentResult, page: int, step: int) -> None:
    """One `block_omitted` notice per page, counting the paragraphs left out of it (`step`
    +1 or -1); it goes when the count reaches 0."""
    held = next((n for n in result.notices if n.code == BLOCK_OMITTED and n.page == page), None)
    count = (held.count if held else 0) + step
    others = [n for n in result.notices if n is not held]
    result.notices = [*others, make(BLOCK_OMITTED, page=page, count=count)] if count > 0 else others


def leave_out_block(result: DocumentResult, element_id: str) -> StructureChange:
    """Leave one paragraph or list item out of the student file (a running head or stamp that
    got into the body, a printer's mark, a line a scan repeated). The element, its fixes and its
    own notices move into `result.left_out_blocks`, so no writer sees them, and the file's note
    says how many are out and on which pages. The words are kept, listed on the page's card with
    "Put it back" and named in the record. Refused as `left_out_blocked` says, and for the last
    paragraph of the document. Raises ReviewInvalid and LookupError; nothing is changed then."""
    fid = _fid("leave_out", element_id)
    if any(b.element.id == element_id for b in result.left_out_blocks):
        raise ReviewInvalid([FieldError(fid, BLOCK_ALREADY_OUT)])
    element = next((e for e in result.elements if e.id == element_id), None)
    if element is None:
        raise LookupError(element_id)
    if blocked := left_out_blocked(result, element):
        raise ReviewInvalid([FieldError(fid, blocked)])
    if not any(
        e.id != element_id and e.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM) for e in result.elements
    ):
        raise ReviewInvalid([FieldError(fid, BLOCK_LAST)])
    before = capture(
        result, elements=[element_id], fixes=[element_id], blocks=True, page=element.page
    )
    own = [n for n in result.notices if n.element_id == element_id]
    result.left_out_blocks.append(
        LeftOutBlock(
            element=element,
            fixes=[f for f in result.fixes if f.element_id == element_id],
            notices=own,
        )
    )
    result.elements = [e for e in result.elements if e.id != element_id]
    result.fixes = [f for f in result.fixes if f.element_id != element_id]
    result.notices = [n for n in result.notices if n.element_id != element_id]
    _block_notice(result, element.page, +1)
    _recount(result)
    return StructureChange(
        element_id=element_id,
        action="leave_out_block",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, blocks=True)},
        message=(
            "That text is left out of the student file, and the file says so. It is still "
            "listed on this page, with “Put it back”."
        ),
        page=element.page,
    )


def put_back_block(result: DocumentResult, element_id: str) -> StructureChange:
    """Put a paragraph or list item left out back (`leave_out_block`): the element, its fixes
    and its notices return, in their reading order. Raises ReviewInvalid."""
    fid = _fid("leave_out", element_id)
    held = next((b for b in result.left_out_blocks if b.element.id == element_id), None)
    if held is None:
        raise ReviewInvalid([FieldError(fid, BLOCK_NOT_OUT)])
    before = capture(
        result, elements=[element_id], fixes=[element_id], blocks=True, page=held.element.page
    )
    result.left_out_blocks = [b for b in result.left_out_blocks if b is not held]
    result.elements.append(held.element)
    result.fixes.extend(held.fixes)
    result.notices.extend(held.notices)
    _block_notice(result, held.element.page, -1)
    _recount(result)
    return StructureChange(
        element_id=element_id,
        action="restore_block",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=[element_id], blocks=True)},
        message="That text is back in the student file.",
        page=held.element.page,
    )


# --------------------------------------------------------------------------- regions (T3-5)

MAX_ADDED = 30  # pictures and equations added by hand, per document
MIN_REGION_PT = 6.0
MAX_REGION_SHARE = 0.9  # of the page's area: a "region" that is the whole page is a mistake
ADDED_MARK = "x"  # ids of added elements: "p3-x1"


def added_count(result: DocumentResult) -> int:
    return sum(1 for e in result.elements if re.fullmatch(r"p\d+-x\d+", e.id))


def next_added_id(result: DocumentResult, page: int) -> str:
    prefix = f"p{page + 1}-{ADDED_MARK}"
    used = [
        int(e.id.removeprefix(prefix))
        for e in (*result.elements, *(x for o in result.omitted for x in o.elements))
        if e.id.startswith(prefix) and e.id.removeprefix(prefix).isdecimal()
    ]
    return f"{prefix}{max(used) + 1 if used else 1}"


def region_between(
    size: tuple[float, float],
    content: tuple[float, float],
    after: tuple[float, float, float, float] | None,
    before: tuple[float, float, float, float] | None,
) -> dict[str, str]:
    """The four edges, as the form's fields give them (percent of the page), of the part of
    a page that starts below the line `after` (None: at the top of the page) and ends above
    the line `before` (None: at the bottom), as wide as the page's content (`content`: its
    left and right edge). Each edge is rounded outward, so the region never takes in more of
    the lines around it than the number would. `region_box` then checks it like any other."""
    width, height = size
    edges = {
        "left": math.floor(10000 * content[0] / width) / 100,
        "top": math.ceil(10000 * (after[3] if after else 0.0) / height) / 100,
        "right": math.ceil(10000 * content[1] / width) / 100,
        "bottom": math.floor(10000 * (before[1] if before else height) / height) / 100,
    }
    return {name: f"{min(max(value, 0.0), 100.0):g}" for name, value in edges.items()}


def region_box(
    size: tuple[float, float], left: str, top: str, right: str, bottom: str, fid: str
) -> tuple[float, float, float, float]:
    """A region of a page from four edges, each a percentage of the page (left and right of
    its width, top and bottom of its height), as points on the page. Raises ReviewInvalid."""

    def percent(raw: str, name: str) -> float:
        try:
            value = float(raw.strip())
        except ValueError:
            value = float("nan")
        if not (value == value) or not 0 <= value <= 100:
            raise ReviewInvalid(
                [FieldError(fid, f"Give the {name} edge as a number from 0 to 100.")]
            )
        return value

    x0 = percent(left, "left") / 100 * size[0]
    x1 = percent(right, "right") / 100 * size[0]
    y0 = percent(top, "top") / 100 * size[1]
    y1 = percent(bottom, "bottom") / 100 * size[1]
    if x1 - x0 < MIN_REGION_PT or y1 - y0 < MIN_REGION_PT:
        message = (
            "The region is too small, or its right edge is not right of its left edge "
            "(or its bottom not below its top)."
        )
        raise ReviewInvalid([FieldError(fid, message)])
    if (x1 - x0) * (y1 - y0) > MAX_REGION_SHARE * size[0] * size[1]:
        raise ReviewInvalid(
            [FieldError(fid, "The region is nearly the whole page. Choose a smaller part of it.")]
        )
    return round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Share of the narrower box's width that the two boxes share."""
    shared = min(a[2], b[2]) - max(a[0], b[0])
    narrow = min(a[2] - a[0], b[2] - b[0])
    return shared / narrow if narrow > 0 and shared > 0 else 0.0


def reading_order_slot(
    result: DocumentResult, page: int, box: tuple[float, float, float, float]
) -> int:
    """The `order` a new element at `box` takes: right after the last element of its page
    that is above it in the same column (else the last one wholly above it, else the last
    element of an earlier page). Everything from there on moves down one."""
    on_page = sorted((e for e in result.elements if e.page == page), key=lambda e: e.order)
    same_column = [e for e in on_page if _overlap(e.bbox, box) > 0.3 and e.bbox[1] <= box[1]]
    above = [e for e in on_page if e.bbox[3] <= box[1] + 1.0]
    anchor = (same_column or above or [None])[-1]
    if anchor is not None:
        return anchor.order + 1
    earlier = [e.order for e in result.elements if e.page < page]
    return max(earlier) + 1 if earlier else 0


def insert_element(result: DocumentResult, element: Element) -> None:
    """Put the element in the reading order at the slot its box gives it."""
    slot = reading_order_slot(result, element.page, element.bbox)
    for other in result.elements:
        if other.order >= slot:
            other.order += 1
    element.order = slot
    result.elements.append(element)


def _alt_for(typed: str, fid: str, values: dict[str, str]) -> tuple[str, list[Any]]:
    """The alt text to store and its warnings, checked like any alt text."""
    from readablestem.pipeline import validate
    from readablestem.web.review import MAX_ALT_CHARS, _blocking, plain_failed

    alt = validate.clean_alt(typed)
    values["alt"] = alt
    if not alt:
        raise ReviewInvalid([FieldError(fid, "Write the alt text for the picture.")], values)
    if len(alt) > MAX_ALT_CHARS:
        message = f"Keep the alt text under {MAX_ALT_CHARS} characters."
        raise ReviewInvalid([FieldError(fid, message)], values)
    checks = list(validate.check_alt(alt, decorative=False))
    if failed := _blocking(checks):
        message = "Your alt text was not saved. " + plain_failed(failed)
        raise ReviewInvalid([FieldError(fid, message)], values)
    return alt, checks


def refuse_left_out(result: DocumentResult, page: int, fid: str) -> None:
    if page in result.omitted_pages:
        message = "That page is left out of the file. Put it back first."
        raise ReviewInvalid([FieldError(fid, message)])


def check_alt_text(typed_alt: str, page: int) -> None:
    """Raise ReviewInvalid when the alt text of a picture to add cannot be used (so nothing
    is cut or spent before a form that was going to be refused)."""
    _alt_for(typed_alt, field_id("addalt", str(page + 1)), {})


def add_figure(
    result: DocumentResult,
    page: int,
    box: tuple[float, float, float, float],
    typed_alt: str,
    png: bytes,
) -> StructureChange:
    """Add a picture the tool did not find, with the reviewer's alt text. It is an element
    in the reading order with a fix the reviewer made (alt text checked like any other);
    `png` is its crop. Raises ReviewInvalid with nothing changed."""
    fid = field_id("addalt", str(page + 1))
    values: dict[str, str] = {}
    refuse_left_out(result, page, fid)
    if added_count(result) >= MAX_ADDED:
        raise ReviewInvalid(
            [FieldError(fid, f"{MAX_ADDED} pictures and equations were added already.")]
        )
    alt, checks = _alt_for(typed_alt, fid, values)
    element_id = next_added_id(result, page)
    before = capture(result, elements=[element_id], fixes=[element_id], orders=True, page=page)
    insert_element(result, Element(id=element_id, kind=Kind.FIGURE, page=page, bbox=box, order=0))
    result.fixes.append(
        Fix(
            element_id=element_id,
            kind=Kind.FIGURE,
            status=Status.EDITED,
            figure=FigureFix(figure_type="other", alt=alt),
            checks=checks,
            reviewed=True,
            review="none",
        )
    )
    _recount(result)
    return StructureChange(
        element_id=element_id,
        action="add_figure",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=[element_id], fixes=[element_id])},
        message="The picture was added, with your alt text.",
        crops={element_id: png},
        shows=element_id,
        warnings=[c for c in checks if not c.passed],
    )


def add_equation(
    result: DocumentResult,
    page: int,
    box: tuple[float, float, float, float],
    layer: str,
    png: bytes,
    read: Fix,
) -> StructureChange:
    """Add an equation the tool did not find. `read` is what the equation step made of the
    crop (`png`), under the new element's id; whatever the step made, the reviewer decides:
    an answer it would have accepted is a required decision here (this was marked by hand
    because the tool missed it, so its answer is checked), and a failed read waits as one
    too. Raises ReviewInvalid with nothing changed."""
    fid = field_id("add", str(page + 1))
    refuse_left_out(result, page, fid)
    if added_count(result) >= MAX_ADDED:
        raise ReviewInvalid(
            [FieldError(fid, f"{MAX_ADDED} pictures and equations were added already.")]
        )
    element_id = next_added_id(result, page)
    read = read.model_copy(update={"element_id": element_id})
    before = capture(result, elements=[element_id], fixes=[element_id], orders=True, page=page)
    insert_element(
        result,
        Element(
            id=element_id,
            kind=Kind.EQUATION,
            page=page,
            bbox=box,
            order=0,
            text=layer or None,
            display="block",
            reasons=["marked_by_reviewer"],
        ),
    )
    if read.status in PENDING_STATUSES:
        asked = read
    else:
        asked = read.model_copy(update={"status": Status.NEEDS_REVIEW})
    asked.review = "required"
    asked.reviewed = False
    triage.stamp([asked])
    result.fixes.append(asked)
    _recount(result)
    return StructureChange(
        element_id=element_id,
        action="add_equation",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=[element_id], fixes=[element_id])},
        message="The equation was added. Check what the tool read from it.",
        crops={element_id: png},
        shows=element_id,
    )


# --------------------------------------------------------------------------- an equation's card

NOT_AN_EQUATION_NO_TEXT = (
    "The PDF has no text for this, so there is nothing to give back to the text. Keep it as "
    "a picture with your description, or type the LaTeX."
)
NOT_AN_EQUATION_IN_TABLE = (
    "This equation is a cell of a table. Change the cell in the table, or keep the table "
    "as an image."
)
NOT_AN_EQUATION_WHERE = "The text this belongs to is not there any more."


READ_AGAIN = "The tool read it again. Check what it read."
# The same picture, the same question: the record answered and the model was not asked.
READ_BEFORE = (
    "The tool has already read this picture a second time, and this is the reading it gave. "
    "It was not asked again and nothing was counted. If the reading is wrong, edit the LaTeX "
    "or keep the equation as an image."
)


def read_again(
    result: DocumentResult, element: Element, read: Fix, *, fresh: bool = True
) -> StructureChange:
    """Replace the proposal of an equation that waits with `read`, a new reading of the same
    picture (`web.reread`, made with no lock held), on the same item. The item keeps its id,
    its place and its crop; whatever the step made, the reviewer decides it (as for an
    equation added by hand: the tool read it a second time because a person asked, so
    its answer is checked), and Undo puts the earlier reading back, as a whole.
    `element` is the element as the read left it (a number printed beside the formula, read
    out of its LaTeX, is kept). `fresh` False: the reading is the one the model gave the last
    time this picture was asked about (`web.reread`), and the notice says so instead of saying
    the picture was read again. Raises LookupError for an item that is not a waiting equation."""
    fix = result.fix_for(element.id)
    if fix is None or fix.kind is not Kind.EQUATION or not fix.pending:
        raise LookupError(element.id)
    before = capture(result, elements=[element.id], fixes=[element.id], page=element.page)
    asked = read.model_copy(update={"element_id": element.id})
    if asked.status not in PENDING_STATUSES:
        asked.status = Status.NEEDS_REVIEW
    asked.review = "required"
    asked.reviewed = False
    asked.group_key = fix.group_key
    asked.band = fix.band  # the same place in the review order
    triage.stamp([asked])
    result.fixes[next(n for n, f in enumerate(result.fixes) if f is fix)] = asked
    for index, current in enumerate(result.elements):
        if current.id == element.id and element.equation_label != current.equation_label:
            result.elements[index] = current.model_copy(
                update={"equation_label": element.equation_label}
            )
    _recount(result)
    return StructureChange(
        element_id=element.id,
        action="read_again",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=[element.id], fixes=[element.id])},
        message=READ_AGAIN if fresh else READ_BEFORE,
        shows=element.id,
        page=element.page,
    )


# What an element keeps when it stops being an equation: where it is, how it is drawn and its
# words. Everything the equation step and the equation checks put on it goes.
_EQUATION_ONLY = {
    "score": None,
    "reasons": [],
    "display": None,
    "parent_id": None,
    "equation_label": None,
    "regular_letters": None,
    "glyphs": None,
    "page_latex": None,
    "page_status": None,
    "witness_latex": None,
    "witness_status": None,
    "alternatives": None,
}


def not_an_equation(result: DocumentResult, element_id: str) -> StructureChange:
    """ "Not an equation": the glyphs the PDF has under the box go back to the text as words. A
    display equation becomes a paragraph where it stands (its printed number, if it has one, is
    written after it); one inside a line is put back into the paragraph it was cut from, in
    the place of its mark. The equation's fix goes (nobody has to decide it), the item keeps its
    id and its crop, and Undo puts back the element, the paragraph and the fix as they were.
    Refused, with nothing changed, for an item that does not wait, one with no text from the
    PDF, a table's cell or text that is not there any more. Raises ReviewInvalid and
    LookupError."""
    element = text_element(result, element_id)
    fix = result.fix_for(element_id)
    fid = field_id("actions", element_id)
    if fix is None or fix.kind is not Kind.EQUATION or not fix.pending:
        raise LookupError(element_id)
    glyphs = clean_text(element.text or "", preformatted=False)
    if not glyphs:
        raise ReviewInvalid([FieldError(fid, NOT_AN_EQUATION_NO_TEXT)])
    from readablestem.pipeline import table_math

    if table_math.tie_of(result.elements, element_id) is not None:
        raise ReviewInvalid([FieldError(fid, NOT_AN_EQUATION_IN_TABLE)])
    parent = (
        next((e for e in result.elements if e.id == element.parent_id), None)
        if element.display == "inline"
        else None
    )
    inline = element.display == "inline"
    marker = inline_marker(element_id)
    if inline and (
        parent is None
        or parent.kind not in {Kind.PARAGRAPH, Kind.LIST_ITEM}
        or (parent.text or "").count(marker) != 1
    ):
        raise ReviewInvalid([FieldError(fid, NOT_AN_EQUATION_WHERE)])
    touched = [element_id, parent.id] if parent is not None else [element_id]
    before = capture(
        result,
        elements=touched,
        fixes=[element_id],
        orders=inline,
        pages=True,
        page=element.page,
    )
    if inline and parent is not None:
        old = parent.text or ""
        new = old.replace(marker, glyphs)
        update, _ = remap_spans(old, new, parent)
        for index, current in enumerate(result.elements):
            if current.id == parent.id:
                result.elements[index] = current.model_copy(update={"text": new, **update})
        result.elements = [e for e in result.elements if e.id != element_id]
    else:
        words = f"{glyphs} {clean_text(element.equation_label or '', preformatted=False)}".strip()
        for index, current in enumerate(result.elements):
            if current.id == element_id:
                result.elements[index] = current.model_copy(
                    update={"kind": Kind.PARAGRAPH, "text": words, **_EQUATION_ONLY}
                )
    result.fixes = [f for f in result.fixes if f.element_id != element_id]
    result.notices = [n for n in result.notices if n.element_id != element_id]
    _recount(result)
    return StructureChange(
        element_id=element_id,
        action="not_equation",
        before={STRUCTURE_KEY: before},
        after={STRUCTURE_KEY: capture(result, elements=touched, fixes=[element_id])},
        message="It is now text, as the PDF has it.",
        page=element.page,
    )
