"""Carry the reviewer's structural edits over to a new conversion of the same file (T3-1,
T3-2 with T3-5 and T3-16).

`web.carry` carries *decisions* on fixes. The review screen also lets a person change what
no fix covers: the words of a paragraph, a paragraph made a heading, a picture or an
equation the tool missed, a page left out. A new conversion (a revised PDF, or a document
tried again) starts from the pipeline's output and does not have them. They are put back
here, and only onto an item that is the same as the one they were made on:

- **Words and headings** are matched by what the item said *before* the edits (read back
  from the earlier version's `ReviewEdit` rows, checked against the result as it stands),
  its kind, size and weight, and by position when several say the same. A paragraph whose
  words changed in the new file is not touched. The equations inside a text must be the
  same pictures, in the same order, or the text is left alone.
- **Pictures and equations added by hand** are put back only where the page, drawn in the
  region, looks exactly as it did (the caller compares the two PDFs: `Unchanged`), the
  same page, and where the tool has found nothing in that place this time. A picture the
  tool now finds itself is its own item, and the hand-made one is not added twice.
- **Pages left out** are left out again only when the page looks exactly as it did.

Anything else is counted as not carried (`EditsCarried.dropped`) and the instructor is told
that some edits need to be made again. Nothing here reads a model, spends money or writes
text of the document anywhere but into the new result; the report holds ids and counts.
A try-again compares a file with itself, so the page looks the same by definition.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from readablestem.core.types import (
    INLINE_MARKER_RE,
    DocumentResult,
    Element,
    Fix,
    Kind,
)
from readablestem.web import carry, page_edits
from readablestem.web.review import STRUCTURE_KEY, ReviewInvalid, _recount

Box = tuple[float, float, float, float]
# Does page `page` (0-based), or its region `box`, look the same in the earlier and in the
# new file? Rendering failed or not knowing counts as no.
Unchanged = Callable[[int, "Box | None"], bool]

# What a carried edit is called in the report and on the record (`ReviewEdit.after`).
TEXT = "text"
HEADING = "heading"
PICTURE = "picture"
EQUATION = "equation"
PAGE_LEFT_OUT = "page_left_out"
KINDS = (TEXT, HEADING, PICTURE, EQUATION, PAGE_LEFT_OUT)
CARRIED_KEY = "carried_edit"

# `ReviewEdit.action` values of the edits that change the text or the kind of an element.
TEXT_ACTIONS = frozenset({"edit_text", "make_heading"})
ADDED_ID = re.compile(r"p\d+-x\d+")
# Added items a try-again or a revision may bring back in one go (page_edits.MAX_ADDED).
MAX_CHECKS = 60


@dataclass(frozen=True)
class EditRow:
    """The parts of an earlier `ReviewEdit` this module reads."""

    element_id: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass
class EditsCarried:
    """What was put on the new result: (kind, id in the new result) pairs (a page left out
    is `page_edits.page_key`), how many earlier edits were not carried, and the crops of
    the pictures and equations added back."""

    items: list[tuple[str, str]] = field(default_factory=list)
    dropped: int = 0
    crops: dict[str, bytes] = field(default_factory=dict)

    def extend(self, other: EditsCarried) -> None:
        self.items.extend(other.items)
        self.dropped += other.dropped
        self.crops.update(other.crops)

    @property
    def ids(self) -> list[str]:
        return [element_id for _, element_id in self.items]


def with_omitted_back(result: DocumentResult) -> DocumentResult:
    """A copy of `result` with the elements and fixes of the pages left out in its own lists,
    so decisions and edits made before a page was left out are found like any other. The
    copy knows which pages were out (`omitted_pages`)."""
    copy = result.model_copy(deep=True)
    for held in copy.omitted:
        copy.elements.extend(held.elements)
        copy.fixes.extend(held.fixes)
    return copy


# --------------------------------------------------------------------------- the words


def _state(snapshot: dict[str, Any] | None, element_id: str) -> tuple[str, str] | None:
    """(kind, text) of an element as a structural snapshot (`page_edits.capture`) held it."""
    structure = (snapshot or {}).get(STRUCTURE_KEY)
    if not isinstance(structure, dict):
        return None
    held = (structure.get("elements") or {}).get(element_id)
    if not isinstance(held, dict):
        return None
    return str(held.get("kind", "")), str(held.get("text") or "")


@dataclass(frozen=True)
class Origin:
    """What an edited element was before the edits: its kind and text, and which edits
    (oldest first) were made on it."""

    kind: str
    text: str
    ops: tuple[str, ...]


def origin_of(element: Element, rows: list[EditRow]) -> Origin | None:
    """Walk back from the element as it stands through the `edit_text` and `make_heading`
    rows whose result is that state, to what it was before the first. None when no row
    leads to the state (nothing was edited, or the rows no longer describe it). The rows are
    in the order they were made."""
    mine = [r for r in rows if r.element_id == element.id and r.action in TEXT_ACTIONS]
    current = (element.kind.value, element.text or "")
    used: set[int] = set()
    ops: list[str] = []
    while True:
        found = [
            n
            for n, r in enumerate(mine)
            if n not in used
            and _state(r.after, element.id) == current
            and _state(r.before, element.id) not in (None, current)
        ]
        if not found:
            break
        n = found[-1]  # the latest edit that left it like this
        used.add(n)
        ops.append(mine[n].action)
        previous = _state(mine[n].before, element.id)
        assert previous is not None
        current = previous
    if not ops:
        return None
    return Origin(current[0], current[1], tuple(reversed(ops)))


def _words(text: str | None) -> str:
    return carry._words(text)


def _same_look(a: Element, b: Element) -> bool:
    """Size and weight agree (a heading is told from a paragraph by them)."""
    return abs((a.font_size or 0.0) - (b.font_size or 0.0)) < 0.5 and a.bold == b.bold


def _markers(text: str) -> list[str]:
    return [m.group(1) for m in INLINE_MARKER_RE.finditer(text)]


def _typed_text(
    old: DocumentResult,
    old_crops: dict[str, bytes],
    old_element: Element,
    origin: Origin,
    new: DocumentResult,
    new_crops: dict[str, bytes],
    new_element: Element,
) -> str | None:
    """The edited text of `old_element`, as the box in the review shows it for
    `new_element` ("[equation N]" for each inline equation), or None when the equations of
    the two are not the same pictures in the same order."""
    old_ids = _markers(origin.text)
    new_ids = _markers(new_element.text or "")
    final = _markers(old_element.text or "")
    if len(old_ids) != len(new_ids) or sorted(final) != sorted(old_ids):
        return None
    old_by = {e.id: e for e in old.elements}
    new_by = {e.id: e for e in new.elements}
    swap: dict[str, str] = {}
    for before, after in zip(old_ids, new_ids, strict=True):
        a, b = old_by.get(before), new_by.get(after)
        if a is None or b is None:
            return None
        print_ = carry.fingerprint(a, old_crops)
        if print_ is None or print_ != carry.fingerprint(b, new_crops):
            return None
        swap[before] = after
    tokens, _ = page_edits.equation_tokens(new, new_element)
    text = old_element.text or ""
    return INLINE_MARKER_RE.sub(lambda m: tokens[swap[m.group(1)]], text)


def _carry_words(
    old: DocumentResult,
    old_crops: dict[str, bytes],
    new: DocumentResult,
    new_crops: dict[str, bytes],
    rows: list[EditRow],
) -> EditsCarried:
    out = EditsCarried()
    edited: dict[str, Origin] = {}
    for element in old.elements:
        if element.kind in page_edits.TEXT_KINDS and (origin := origin_of(element, rows)):
            edited[element.id] = origin
    if not edited:
        return out
    # How often each (kind, words) said the same in the earlier file, counting an edited
    # item by what it said before: two that said the same are told apart by place only
    # when the new file has the same number of them.
    seen_old: defaultdict[tuple[str, str], int] = defaultdict(int)
    for element in old.elements:
        if element.kind in page_edits.TEXT_KINDS:
            origin = edited.get(element.id)
            kind, text = (
                (origin.kind, origin.text) if origin else (element.kind.value, element.text)
            )
            seen_old[(kind, _words(text))] += 1
    seen_new: defaultdict[tuple[str, str], list[Element]] = defaultdict(list)
    for element in new.elements:
        if element.kind in page_edits.TEXT_KINDS:
            seen_new[(element.kind.value, _words(element.text))].append(element)

    pairs: list[tuple[float, str, str]] = []
    old_by = {e.id: e for e in old.elements}
    for old_id, origin in edited.items():
        key = (origin.kind, _words(origin.text))
        candidates = seen_new.get(key, [])
        if len(candidates) != seen_old[key]:
            continue  # said the same more or fewer times: which one is which is a guess
        for candidate in candidates:
            if _same_look(old_by[old_id], candidate):
                distance = carry._distance(old, old_by[old_id], new, candidate)
                pairs.append((distance, old_id, candidate.id))
    taken_new: set[str] = set()
    matched: dict[str, str] = {}
    for _, old_id, new_id in sorted(pairs):
        if old_id in matched or new_id in taken_new:
            continue
        matched[old_id] = new_id
        taken_new.add(new_id)

    old_fixes = {f.element_id: f for f in old.fixes}
    for old_id, origin in edited.items():
        new_id = matched.get(old_id)
        if new_id is None:
            out.dropped += 1
            continue
        kind = _apply_words(
            old, old_crops, old_by[old_id], origin, old_fixes.get(old_id), new, new_crops, new_id
        )
        if kind is None:
            out.dropped += 1
        else:
            out.items.append((kind, new_id))
    return out


def _apply_words(
    old: DocumentResult,
    old_crops: dict[str, bytes],
    old_element: Element,
    origin: Origin,
    old_fix: Fix | None,
    new: DocumentResult,
    new_crops: dict[str, bytes],
    new_id: str,
) -> str | None:
    """The edits of one item on its counterpart; HEADING or TEXT when they were made, None
    when not (the result is left as it was)."""
    new_element = next(e for e in new.elements if e.id == new_id)
    typed = None
    if old_element.text != origin.text:
        typed = _typed_text(old, old_crops, old_element, origin, new, new_crops, new_element)
        if typed is None:
            return None
    level = None
    if "make_heading" in origin.ops:
        if old_fix is None or old_fix.heading is None or old_element.kind is not Kind.HEADING:
            return None
        level = old_fix.heading.level
    before = page_edits.capture(new, elements=[new_id], fixes=[new_id], orders=True)
    try:
        if level is not None:
            page_edits.make_heading(new, new_id, str(level))
        if typed is not None:
            try:
                page_edits.edit_text(new, new_id, typed)
            except ReviewInvalid as exc:
                # "Nothing changed": the new file already says it. Anything else is a refusal.
                if not all(e.message == page_edits.NOTHING_CHANGED for e in exc.errors):
                    raise
    except (ReviewInvalid, LookupError):
        page_edits.restore(new, before)
        return None
    return HEADING if level is not None else TEXT


# --------------------------------------------------------------------------- added items


def _clip(box: Box) -> Box:
    pad = 4.0
    return (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)


def _found_there_now(old: DocumentResult, added: Element, new: DocumentResult) -> bool:
    """The tool found something in the added item's place this time that it had not found
    there before (a try-again that read a page it could not read, a better detection): that
    is its own item, and the one made by hand is not added beside it. Items that were there
    when the instructor drew the region (a caption it overlaps) do not count."""
    others = [e for e in old.elements if e.id != added.id]
    for found in new.elements:
        if carry._share(added, found) < carry.SAME_PLACE:
            continue
        if not any(
            e.kind is found.kind and carry._share(e, found) >= carry.SAME_PLACE for e in others
        ):
            return True
    return False


def _carry_added(
    old: DocumentResult,
    old_crops: dict[str, bytes],
    new: DocumentResult,
    unchanged: Unchanged,
    job_pages: int,
) -> EditsCarried:
    out = EditsCarried()
    pages = page_edits.page_count(new, job_pages)
    fixes = {f.element_id: f for f in old.fixes}
    added = [
        e
        for e in old.elements
        if ADDED_ID.fullmatch(e.id) and e.kind in (Kind.FIGURE, Kind.EQUATION)
    ]
    for index, element in enumerate(sorted(added, key=lambda e: e.order)):
        fix = fixes.get(element.id)
        png = old_crops.get(element.id)
        if (
            fix is None
            or png is None
            or index >= MAX_CHECKS
            or element.page >= pages
            or element.page in new.omitted_pages
            or page_edits.added_count(new) >= page_edits.MAX_ADDED
        ):
            out.dropped += 1
            continue
        if _found_there_now(old, element, new):
            out.dropped += 1
            continue
        if not unchanged(element.page, _clip(element.bbox)):
            out.dropped += 1
            continue
        new_id = page_edits.next_added_id(new, element.page)
        page_edits.insert_element(new, element.model_copy(update={"id": new_id, "order": 0}))
        new.fixes.append(fix.model_copy(deep=True, update={"element_id": new_id}))
        out.crops[new_id] = png
        out.items.append((PICTURE if element.kind is Kind.FIGURE else EQUATION, new_id))
    if out.items:
        _recount(new)
    return out


# --------------------------------------------------------------------------- the whole


def carry_structure(
    old: DocumentResult,
    old_crops: dict[str, bytes],
    new: DocumentResult,
    new_crops: dict[str, bytes],
    rows: list[EditRow],
    unchanged: Unchanged,
    *,
    job_pages: int = 0,
) -> EditsCarried:
    """Put the earlier edits to words, headings and added items on `new` (in place). Call it
    before `carry.carry_decisions`, so the decisions on a heading made by hand or an
    equation added by hand find their item. `old` has the pages left out put back
    (`with_omitted_back`); `rows` are the earlier version's structural `ReviewEdit`s in the
    order they were made."""
    out = _carry_words(old, old_crops, new, new_crops, rows)
    added = _carry_added(old, old_crops, new, unchanged, job_pages)
    out.extend(added)
    return out


def carry_omitted(
    old: DocumentResult, new: DocumentResult, unchanged: Unchanged, *, job_pages: int = 0
) -> EditsCarried:
    """Leave out again the pages that were left out, where the page looks exactly as it did.
    Call it last: the decisions on a page's items are carried first, so putting the page
    back later shows them."""
    out = EditsCarried()
    pages = page_edits.page_count(new, job_pages)
    for page in old.omitted_pages:
        if page >= pages or page in new.omitted_pages or not unchanged(page, None):
            out.dropped += 1
            continue
        try:
            page_edits.omit_page(new, page, pages=pages)
        except ReviewInvalid:
            out.dropped += 1
            continue
        out.items.append((PAGE_LEFT_OUT, page_edits.page_key(page)))
    return out
