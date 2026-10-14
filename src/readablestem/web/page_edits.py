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

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from readablestem.core.types import (
    INLINE_MARKER_RE,
    PENDING_STATUSES,
    DocumentResult,
    Element,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Notice,
    OmittedPage,
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
    page: int | None = None,
) -> dict[str, Any]:
    """The state of what a change is about to touch, as JSON (`restore` puts it back).

    `elements` and `fixes`: ids whose element or fix is saved as it is now (None when there
    is none, so undoing the creation of one removes it). `orders`: every element's place in
    the reading order. `pages`: the omitted pages and the notices (an omission moves both).
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
    result.notices.append(Notice(code=PAGE_OMITTED, page=page, review="none"))
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


def omitted_counts(result: DocumentResult) -> Counter[int]:
    """Elements held back per omitted page (a count for the page's note)."""
    return Counter({o.page: len(o.elements) for o in result.omitted})


# --------------------------------------------------------------------------- text (T3-5)

MAX_TEXT_CHARS = 6000
TEXT_KINDS = (Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.HEADING)
# Characters a PDF or a pasted text can carry that mean nothing to a reader (controls,
# NUL: Postgres refuses it in JSON); newline and tab stay for program code.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_TOKEN = re.compile(r"\[equation (\d+)\]")
NOTHING_CHANGED = "Nothing changed: this is the text it already has."
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
    numbers = item_numbers(result)
    to_token: dict[str, str] = {}
    from_token: dict[str, str] = {}
    for match in INLINE_MARKER_RE.finditer(element.text or ""):
        marker_id = match.group(1)
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


def remap_spans(old: str, new: str, element: Element) -> tuple[dict[str, Any], int]:
    """The element's links, colour runs, script runs and glossary term, moved to where their
    words are in the new text. A span whose words were changed is dropped (it would point
    at other words). Returns (fields to update, how many links were dropped)."""
    mapping = _char_map(old, new)
    update: dict[str, Any] = {}
    dropped = 0
    for name in ("links", "colour_runs", "script_runs"):
        kept = []
        for span in getattr(element, name):
            moved = _moved(mapping, span.start, span.end)
            if moved is None:
                dropped += 1 if name == "links" else 0
                continue
            kept.append(span.model_copy(update={"start": moved[0], "end": moved[1]}))
        update[name] = kept
    if element.term_end is not None:
        # The glossary term stays a term only while its own words are unchanged; then the
        # definition may change freely.
        end = element.term_end
        update["term_end"] = end if new[:end] == old[:end] else None
    return update, dropped


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
            many = "twice" if left.get(marker_id, 0) > 1 else "not at all"
            message = (
                f"“{token}” is there {many}. Keep it once, where the equation belongs: "
                "the equation itself is changed in its own decision."
            )
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
    note = ""
    if dropped:
        note = (
            f" {dropped} link{' was' if dropped == 1 else 's were'} taken off because "
            f"{'its' if dropped == 1 else 'their'} words changed."
        )
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
            result.elements[index] = current.model_copy(update={"kind": Kind.HEADING})
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
