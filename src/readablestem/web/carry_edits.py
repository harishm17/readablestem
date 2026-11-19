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
- **"Not an equation"** is made again on the equation that has the same picture and the
  same words under it, when exactly one waiting equation of the new file does.
- **Pages left out** are left out again only when the page looks exactly as it did.

Anything else is counted as not carried (`EditsCarried.dropped`) and the instructor is told
that some edits need to be made again. Nothing here reads a model, spends money or writes
text of the document anywhere but into the new result; the report holds ids and counts.
A try-again compares a file with itself, so the page looks the same by definition.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from readablestem.core import uid
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
ROLE = "role"
BLOCK_LEFT_OUT = "block_left_out"
PICTURE = "picture"
EQUATION = "equation"
PAGE_LEFT_OUT = "page_left_out"
NOT_EQUATION = "not_equation"
# A page operation that is never made again (a try-again or a revision starts from the
# pipeline's reading): an equation read again. It is listed, by name and page, as not re-applied.
READ_AGAIN = "read_again"
KINDS = (TEXT, HEADING, ROLE, BLOCK_LEFT_OUT, PICTURE, EQUATION, PAGE_LEFT_OUT, NOT_EQUATION)
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
    is `page_edits.page_key`), how many earlier edits were not carried, which ones (kind and
    1-based page of the earlier file; None when the page is not known), and the crops of
    the pictures and equations added back. `changes` holds, for the kinds a later revision reads
    back from rows (words, heading, role, "not an equation"), the structural change that made
    each one on the new result, by (kind, id in the new result): the worker stores it with the
    carried row, so the edit has the snapshots a person's own edit has (`edit_row`)."""

    items: list[tuple[str, str]] = field(default_factory=list)
    dropped: int = 0
    not_applied: list[tuple[str, int | None]] = field(default_factory=list)
    crops: dict[str, bytes] = field(default_factory=dict)
    changes: dict[tuple[str, str], page_edits.StructureChange] = field(default_factory=dict)

    def drop(self, kind: str, page: int | None) -> None:
        """An earlier edit that was not made again: `page` is 0-based, as an element's."""
        self.dropped += 1
        self.not_applied.append((kind, None if page is None else page + 1))

    def extend(self, other: EditsCarried) -> None:
        self.items.extend(other.items)
        self.dropped += other.dropped
        self.not_applied.extend(other.not_applied)
        self.crops.update(other.crops)
        self.changes.update(other.changes)

    @property
    def ids(self) -> list[str]:
        return [element_id for _, element_id in self.items]


# The kinds of carried edit that a later revision reads back from the new job's rows, and the
# structural action each one is read as. Pictures, equations, paragraphs and pages left out are
# carried from the result as it stands, so they need no row.
ROW_ACTIONS = {
    TEXT: "edit_text",
    HEADING: "make_heading",
    ROLE: "set_role",
    NOT_EQUATION: "not_equation",
}


def edit_row(
    element_id: str, action: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> EditRow | None:
    """A stored `ReviewEdit` as carry-over reads it, or None for a row it has no use for. An edit
    that was itself carried onto this version is read as the edit it was (the carried row keeps
    its snapshots), so it goes on to the next revision like one made by hand. A carried row of
    an older release has no snapshots: it is kept as `carried`, and `_carry_unreadable` says it
    could not be made again."""
    if action != carry.CARRIED_ACTION:
        return EditRow(element_id, action, before, after)
    kind = (after or {}).get(CARRIED_KEY)
    if kind not in ROW_ACTIONS:
        return None
    if isinstance((before or {}).get(STRUCTURE_KEY), dict):
        return EditRow(element_id, ROW_ACTIONS[kind], before, after)
    return EditRow(element_id, carry.CARRIED_ACTION, before, after)


def with_omitted_back(result: DocumentResult) -> DocumentResult:
    """A copy of `result` with the elements and fixes of the pages left out in its own lists,
    so decisions and edits made before a page was left out are found like any other. The
    copy knows which pages were out (`omitted_pages`) and which paragraphs (`left_out_blocks`;
    the paragraphs are in the lists too, so what was decided or edited on one is found)."""
    copy = result.model_copy(deep=True)
    for held in copy.omitted:
        copy.elements.extend(held.elements)
        copy.fixes.extend(held.fixes)
    for block in copy.left_out_blocks:
        copy.elements.append(block.element)
        copy.fixes.extend(block.fixes)
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


def _role_in(snapshot: dict[str, Any] | None, element_id: str) -> tuple[bool, str | None]:
    """(found, role) of an element as a structural snapshot held it."""
    structure = (snapshot or {}).get(STRUCTURE_KEY)
    held = ((structure or {}).get("elements") or {}).get(element_id) if structure else None
    if not isinstance(held, dict):
        return False, None
    role = held.get("role")
    return True, role if isinstance(role, str) else None


def role_edit(element: Element, rows: list[EditRow]) -> tuple[bool, str | None]:
    """Did a person set or clear this paragraph's role, and does the result as it stands show
    it? (True, the role, None for "neither") when the latest `set_role` row of the element
    left the role it has now; (False, None) otherwise (no such row, or it was undone). The rows
    are in the order they were made."""
    for row in reversed([r for r in rows if r.element_id == element.id and r.action == "set_role"]):
        had_before, before = _role_in(row.before, element.id)
        had_after, after = _role_in(row.after, element.id)
        if had_before and had_after and before != after and after == element.role:
            return True, after
    return False, None


def _said(element: Element, origin: Origin | None = None) -> str:
    """The content of a text item as stable ids see it (`core.uid.fingerprint`), or of what
    it said before the edits (`origin`). Inline equations count by place, not by id, so a
    paragraph whose equations were renumbered by an item added above it is still found."""
    if origin is not None:
        if origin.kind not in Kind:  # a snapshot this code cannot read: matches nothing
            return "?"
        element = element.model_copy(update={"kind": Kind(origin.kind), "text": origin.text})
    return uid.fingerprint(element)


def _same_look(a: Element, b: Element) -> bool:
    """Size and weight agree (a heading is told from a paragraph by them)."""
    return abs((a.font_size or 0.0) - (b.font_size or 0.0)) < 0.5 and a.bold == b.bold


def _markers(text: str) -> list[str]:
    return [m.group(1) for m in INLINE_MARKER_RE.finditer(text)]


def _typed_text(
    old: DocumentResult,
    old_crops: Mapping[str, carry.Crop],
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
    old_crops: Mapping[str, carry.Crop],
    new: DocumentResult,
    new_crops: dict[str, bytes],
    rows: list[EditRow],
) -> EditsCarried:
    out = EditsCarried()
    edited: dict[str, Origin] = {}
    roles: dict[str, str | None] = {}
    gone = {b.element.id for b in old.left_out_blocks}
    for element in old.elements:
        if element.kind not in page_edits.TEXT_KINDS:
            continue
        origin = origin_of(element, rows)
        has_role, role = role_edit(element, rows)
        if has_role:
            roles[element.id] = role
        if origin or has_role or element.id in gone:
            edited[element.id] = origin or Origin(element.kind.value, element.text or "", ())
    if not edited:
        return out
    # How often each content (kind and words) was said in the earlier file, counting an edited
    # item by what it said before: two that said the same are told apart by place only
    # when the new file has the same number of them.
    seen_old: defaultdict[str, int] = defaultdict(int)
    for element in old.elements:
        if element.kind in page_edits.TEXT_KINDS:
            seen_old[_said(element, edited.get(element.id))] += 1
    seen_new: defaultdict[str, list[Element]] = defaultdict(list)
    for element in new.elements:
        if element.kind in page_edits.TEXT_KINDS:
            seen_new[_said(element)].append(element)

    pairs: list[tuple[float, str, str]] = []
    old_by = {e.id: e for e in old.elements}
    for old_id, origin in edited.items():
        key = _said(old_by[old_id], origin)
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
        kinds = None
        if new_id is not None:
            kinds = _apply_words(
                old,
                old_crops,
                old_by[old_id],
                origin,
                old_fixes.get(old_id),
                new,
                new_crops,
                new_id,
                set_role=old_id in roles,
                role=roles.get(old_id),
                leave_out=old_id in gone,
            )
        if kinds is None or new_id is None:
            # The edits of one paragraph are made together or not at all: each is a change
            # the person made, so each is listed.
            lost = [
                kind
                for kind, was_made in (
                    (HEADING, "make_heading" in origin.ops),
                    (TEXT, old_by[old_id].text != origin.text),
                    (ROLE, old_id in roles),
                    (BLOCK_LEFT_OUT, old_id in gone),
                )
                if was_made
            ]
            for kind in lost:
                out.drop(kind, old_by[old_id].page)
        else:
            carried, changes = kinds
            out.items.extend((kind, new_id) for kind in carried)
            out.changes.update({(kind, new_id): change for kind, change in changes.items()})
    return out


def _apply_words(
    old: DocumentResult,
    old_crops: Mapping[str, carry.Crop],
    old_element: Element,
    origin: Origin,
    old_fix: Fix | None,
    new: DocumentResult,
    new_crops: dict[str, bytes],
    new_id: str,
    *,
    set_role: bool = False,
    role: str | None = None,
    leave_out: bool = False,
) -> tuple[list[str], dict[str, page_edits.StructureChange]] | None:
    """The edits of one item on its counterpart: the kind of each one made (HEADING, TEXT, ROLE,
    BLOCK_LEFT_OUT, one per change the person made) and the structural change of each that
    made one, None when they were not (the result is left as it was). A paragraph left out is
    left out again last, and only where `page_edits.leave_out_block` allows it: a new file that
    has a decision open on it, a link to it or an equation in it keeps it, and the change is
    listed as not made again."""
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
    before = page_edits.capture(
        new, elements=[new_id], fixes=[new_id], orders=True, blocks=leave_out
    )
    changes: dict[str, page_edits.StructureChange] = {}
    try:
        if level is not None:
            changes[HEADING] = page_edits.make_heading(new, new_id, str(level))
        if typed is not None:
            try:
                changes[TEXT] = page_edits.edit_text(new, new_id, typed)
            except ReviewInvalid as exc:
                # "Nothing changed": the new file already says it. Anything else is a refusal.
                if not all(e.message == page_edits.NOTHING_CHANGED for e in exc.errors):
                    raise
        if set_role:
            try:
                changes[ROLE] = page_edits.set_role(new, new_id, role)
            except ReviewInvalid as exc:
                # The new file already has that role (or none), or says nothing to set one on.
                if not all(e.message == page_edits.NOTHING_CHANGED for e in exc.errors):
                    raise
        if leave_out:
            page_edits.leave_out_block(new, new_id)
    except (ReviewInvalid, LookupError):
        page_edits.restore(new, before)
        return None
    kinds = [
        kind
        for kind, made in (
            (HEADING, level is not None),
            (TEXT, typed is not None),
            (ROLE, set_role),
            (BLOCK_LEFT_OUT, leave_out),
        )
        if made
    ]
    return kinds, changes


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


def _added_by_hand(old: DocumentResult) -> list[Element]:
    """The pictures and equations added by hand to the earlier version, in reading order."""
    added = [
        e
        for e in old.elements
        if ADDED_ID.fullmatch(e.id) and e.kind in (Kind.FIGURE, Kind.EQUATION)
    ]
    return sorted(added, key=lambda e: e.order)


def regions_compared(old: DocumentResult) -> list[tuple[int, Box | None]]:
    """The places `carry_structure` and `carry_omitted` ask `Unchanged` about, for the earlier
    version with its omitted pages back: the region of each added picture or equation, and
    each page left out. A snapshot of the earlier version keeps how these looked
    (`web.carry_snapshot`), so the question can be answered once that file is gone."""
    asked: list[tuple[int, Box | None]] = [
        (e.page, _clip(e.bbox)) for e in _added_by_hand(old)[:MAX_CHECKS]
    ]
    return asked + [(page, None) for page in old.omitted_pages]


def _carry_added(
    old: DocumentResult,
    old_crops: Mapping[str, carry.Crop],
    new: DocumentResult,
    unchanged: Unchanged,
    job_pages: int,
) -> EditsCarried:
    out = EditsCarried()
    pages = page_edits.page_count(new, job_pages)
    fixes = {f.element_id: f for f in old.fixes}
    for index, element in enumerate(_added_by_hand(old)):
        fix = fixes.get(element.id)
        png = old_crops.get(element.id)
        kind = PICTURE if element.kind is Kind.FIGURE else EQUATION
        if (
            fix is None
            or not isinstance(png, bytes)  # the picture itself, not only its digest
            or index >= MAX_CHECKS
            or element.page >= pages
            or element.page in new.omitted_pages
            or page_edits.added_count(new) >= page_edits.MAX_ADDED
            or _found_there_now(old, element, new)
            or not unchanged(element.page, _clip(element.bbox))
        ):
            out.drop(kind, element.page)
            continue
        new_id = page_edits.next_added_id(new, element.page)
        page_edits.insert_element(new, element.model_copy(update={"id": new_id, "order": 0}))
        new.fixes.append(fix.model_copy(deep=True, update={"element_id": new_id}))
        out.crops[new_id] = png
        out.items.append((kind, new_id))
    if out.items:
        _recount(new)
    return out


# --------------------------------------------------------------------------- not an equation


def _held_element(row: EditRow) -> Element | None:
    """The equation as it was when it was given back to the text (the row's `before`)."""
    structure = (row.before or {}).get(STRUCTURE_KEY)
    held = ((structure or {}).get("elements") or {}).get(row.element_id)
    if not isinstance(held, dict):
        return None
    try:
        element = Element.model_validate(held)
    except ValueError:
        return None
    return element if element.kind is Kind.EQUATION else None


def _carry_not_equation(
    old_crops: Mapping[str, carry.Crop],
    new: DocumentResult,
    new_crops: dict[str, bytes],
    rows: list[EditRow],
) -> EditsCarried:
    out = EditsCarried()
    for row in rows:
        if row.action != NOT_EQUATION:
            continue
        held = _held_element(row)
        png = old_crops.get(row.element_id)
        if held is None or not png:
            out.drop(NOT_EQUATION, held.page if held is not None else None)
            continue
        print_ = carry.fingerprint(held, {held.id: png})
        waiting = {f.element_id for f in new.fixes if f.kind is Kind.EQUATION and f.pending}
        found = [
            e
            for e in new.elements
            if e.id in waiting
            and e.kind is Kind.EQUATION
            and (e.text or "") == (held.text or "")
            and carry.fingerprint(e, new_crops) == print_
        ]
        if len(found) != 1:
            out.drop(NOT_EQUATION, held.page)
            continue
        try:
            change = page_edits.not_an_equation(new, found[0].id)
        except (ReviewInvalid, LookupError):
            out.drop(NOT_EQUATION, held.page)
            continue
        out.items.append((NOT_EQUATION, found[0].id))
        out.changes[(NOT_EQUATION, found[0].id)] = change
    return out


# --------------------------------------------------------------------------- read again


def _carry_read_again(old: DocumentResult, rows: list[EditRow]) -> EditsCarried:
    """An equation read again is not made again: the new file has the tool's own reading.
    It is listed as not re-applied, unless it was undone (a structural undo row on the same
    item takes back the latest edit before it)."""
    out = EditsCarried()
    made: defaultdict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.action == READ_AGAIN:
            made[row.element_id].append(row.action)
        elif row.action == "undo" and STRUCTURE_KEY in (row.before or {}) and made[row.element_id]:
            made[row.element_id].pop()
    pages = {e.id: e.page for e in old.elements}
    for element_id, actions in made.items():
        for _ in actions:
            out.drop(READ_AGAIN, pages.get(element_id))
    return out


# --------------------------------------------------------------------------- older carried rows


def _carry_unreadable(old: DocumentResult, rows: list[EditRow]) -> EditsCarried:
    """Edits an earlier release carried onto the earlier version and kept no snapshot of (a
    `carried` row with nothing to read the edit back from): they cannot be made again, and the
    instructor is told, by name and page, as for any edit that is not carried. An item the
    person has edited again since has its own rows and is carried from those."""
    out = EditsCarried()
    pages = {e.id: e.page for e in old.elements}
    again = {(r.element_id, r.action) for r in rows if r.action != carry.CARRIED_ACTION}
    for row in rows:
        if row.action != carry.CARRIED_ACTION:
            continue
        kind = str((row.after or {}).get(CARRIED_KEY) or "edit")
        if (row.element_id, ROW_ACTIONS.get(kind, "")) in again:
            continue
        out.drop(kind, pages.get(row.element_id))
    return out


# --------------------------------------------------------------------------- the whole


def carry_structure(
    old: DocumentResult,
    old_crops: Mapping[str, carry.Crop],
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
    out.extend(_carry_not_equation(old_crops, new, new_crops, rows))
    out.extend(_carry_read_again(old, rows))
    out.extend(_carry_unreadable(old, rows))
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
            out.drop(PAGE_LEFT_OUT, page)
            continue
        try:
            page_edits.omit_page(new, page, pages=pages)
        except ReviewInvalid:
            out.drop(PAGE_LEFT_OUT, page)
            continue
        out.items.append((PAGE_LEFT_OUT, page_edits.page_key(page)))
    return out
