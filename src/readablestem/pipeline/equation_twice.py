# ruff: noqa: RUF001
# (the curly quotes are what a text layer holds around a formula)
"""One formula that layout cut into two boxes is written once.

Layout sometimes puts one formula into two boxes: an inline formula cut at a wide space, an
accent and its letter. Each box is read with a picture of its line, and the line prompt tells the
model to read on past a box that stops early, so a reading can come back as the whole formula
while the next box says its own part again, and the page said it twice. Layout is left alone (a
joined box is a new crop and a new request); the repeat is taken out here, once every reading is
in, where the results are put into the document (`run._convert`, next to `table_math.settle`).

The evidence is the file's own characters, in the order they stand, never a bag of letters (a
bag cannot tell a repeat from a second formula that has the same letters). There are two rules.

- **A run of boxes on one line.** The inline equations of one paragraph whose markers follow each
  other on one line (the boxes share a line, and nothing but text stands between two markers).
  Take a window of two or more of them. A member's reading K says the whole window when the
  letters, digits and Greek letters of K's reading, in document order, are the window's own
  characters one for one and in order (each member's box, and the paragraph's text between two
  members in its place), and every other member's reading is a run inside K's (it says nothing
  K does not say). The window is then written once: K's marker stands where the window began, and
  the other markers and the text between the first and the last go. Which member is K: an accepted
  one before an asked one, then one whose box has characters, then the first. A window whose
  characters are K's letters in another order (a fraction, a matrix, a root that the file stores
  in another order) is left as it is.
- **An accent cut from its letter.** Two inline equations whose boxes each lie half or more on the
  other, with the same reading, whose two boxes' characters together are exactly the reading's
  letters. The accepted one stays, else the first.

Not built: an inline box inside a display's box. Where layout puts a block box over an inline one,
the inline formula belongs to its sentence, and taking it out for the display leaves the sentence
without it (`docs/steps.md`, "One formula in two boxes"): that is a fault of layout this step does
not mend.

A formula taken out leaves its paragraph with the spacing a reader expects (`_spaced`). The
paragraph's links, colours and runs move with the text (`core.inline`). A decision stays asked
wherever the reading kept is not accepted; one asked about the dropped copy only is gone with it.
What the dropped copy cost is handed back by the caller, so the document's cost and call counts
stay what was spent. A change that would leave a paragraph empty is not made.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import defusedxml.ElementTree as DET

from readablestem.core import inline
from readablestem.core.types import (
    INLINE_MARKER_RE,
    Element,
    Fix,
    Kind,
    Status,
    inline_marker,
)
from readablestem.pipeline import text_layer_compare

# The share of each box that the other must cover for two boxes to be on the same ink.
SAME_INK = 0.5
# The share of the shorter box's height that two boxes of one line share.
SAME_LINE = 0.5

_STYLE = re.compile(r"\\(?:mathbf|boldsymbol|bm|mathrm|mathit|text|left|right|displaystyle)\b")
_SPACING = re.compile(r"\\[,;:! ]|[\s{}]")
_LEAVES = frozenset({"mi", "mn", "mo", "mtext", "ms"})
_INVISIBLE = "\u2061\u2062\u2063\u2064"
_OPEN, _CLOSE = inline_marker("x")[0], inline_marker("x")[-1]
_CLOSING = ".,;:!?)]}%’”"
_OPENING = "([{‘“"


@dataclass(frozen=True)
class _Reading:
    element: Element
    fix: Fix
    norm: str  # the LaTeX without style words, spacing and braces
    reading: str  # letters, digits and Greek letters of the reading, in order
    box: str  # the same of the PDF's text in the box, in order
    tokens: tuple[str, ...]  # every symbol and operator of the reading, in order


@dataclass(frozen=True)
class _Merge:
    kind: Literal["run", "accent"]
    keep: _Reading
    dropped: tuple[_Reading, ...]
    first: str | None = None  # a run: the marker of its first member and of its last
    last: str | None = None


def _reading(element: Element, fix: Fix | None) -> _Reading | None:
    """What a settled equation says, or None when its fix is not a reading to compare."""
    if (
        fix is None
        or fix.equation is None
        or fix.status not in (Status.ACCEPTED, Status.NEEDS_REVIEW)
    ):
        return None
    try:
        reading = text_layer_compare.mathml_seq(fix.equation.mathml)
    except Exception:  # (a MathML that does not parse is no reading to compare)
        return None
    norm = _SPACING.sub("", _STYLE.sub("", fix.equation.latex))
    box = text_layer_compare.symbol_seq(element.text or "")
    return _Reading(element, fix, norm, reading, box, _tokens(fix.equation.mathml))


def _tokens(mathml: str) -> tuple[str, ...]:
    """The symbols and operators of a MathML in document order, as a text layer would hold them
    (styled letters folded, invisible operators and spaces left out)."""
    root = DET.fromstring(mathml, forbid_dtd=True)
    out = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in _LEAVES and node.text:
            token = "".join(unicodedata.normalize("NFKC", node.text).casefold().split())
            if token.strip(_INVISIBLE):
                out.append(token.strip(_INVISIBLE))
    return tuple(out)


def _within(small: tuple[str, ...], big: tuple[str, ...]) -> bool:
    """Is `small` a run of `big`?"""
    n = len(small)
    return n > 0 and any(big[i : i + n] == small for i in range(len(big) - n + 1))


def _rank(r: _Reading, position: int) -> tuple[bool, bool, int]:
    """Which reading stays: an accepted one before an asked one, then one whose box has
    characters, then the first."""
    return (r.fix.status is not Status.ACCEPTED, not r.box, position)


def _same_line(a: Element, b: Element) -> bool:
    height = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1])
    shared = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
    return height > 0 and shared / height >= SAME_LINE


def _overlap(a: Element, b: Element) -> tuple[float, float]:
    """The area both boxes cover, as a share of each box's own area."""
    (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = a.bbox, b.bbox
    width = min(ax1, bx1) - max(ax0, bx0)
    height = min(ay1, by1) - max(ay0, by0)
    area_a, area_b = (ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0)
    if width <= 0 or height <= 0 or area_a <= 0 or area_b <= 0:
        return 0.0, 0.0
    return width * height / area_a, width * height / area_b


Run = list[tuple[int, int, _Reading]]  # (where its marker starts, where it ends, its reading)


def _runs(text: str, readings: dict[str, _Reading]) -> list[Run]:
    """The markers of a paragraph's text that follow each other on one line, as runs of two or
    more equations (a marker that is not a reading, or a line change, ends a run)."""
    runs: list[Run] = []
    run: Run = []
    for m in INLINE_MARKER_RE.finditer(text):
        r = readings.get(m.group(1))
        if r is None or (run and not _same_line(run[-1][2].element, r.element)):
            if len(run) > 1:
                runs.append(run)
            run = []
        if r is not None:
            run.append((m.start(), m.end(), r))
    if len(run) > 1:
        runs.append(run)
    return runs


def _whole(text: str, run: Run, i: int, j: int) -> str:
    """The characters of members i to j in order: each box's, and the text between two boxes."""
    out = run[i][2].box
    for k in range(i + 1, j + 1):
        out += text_layer_compare.symbol_seq(text[run[k - 1][1] : run[k][0]]) + run[k][2].box
    return out


def _windows(text: str, run: Run) -> list[_Merge]:
    """The windows of a run that one of its readings says whole, longest first, none sharing a
    member with another."""
    n = len(run)
    taken: set[int] = set()
    merges: list[_Merge] = []
    for i, j in sorted(itertools.combinations(range(n), 2), key=lambda w: (w[0] - w[1], w[0])):
        if taken.intersection(range(i, j + 1)):
            continue
        members = run[i : j + 1]
        whole = _whole(text, run, i, j)
        if not any(m[2].box for m in members):
            continue  # no characters of the file in the window: nothing to compare with
        says = [
            (_rank(m[2], at), m[2])
            for at, m in enumerate(members)
            if m[2].reading == whole
            and all(
                o is m or (o[2].reading in m[2].reading and _within(o[2].tokens, m[2].tokens))
                for o in members
            )
        ]
        if not says:
            continue
        keep = min(says, key=lambda s: s[0])[1]
        merges.append(
            _Merge(
                "run",
                keep,
                tuple(m[2] for m in members if m[2] is not keep),
                inline_marker(members[0][2].element.id),
                inline_marker(members[-1][2].element.id),
            )
        )
        taken.update(range(i, j + 1))
    return merges


def _accents(readings: list[_Reading], gone: set[str]) -> list[_Merge]:
    """Pairs of boxes on the same ink that say one formula between them."""
    merges: list[_Merge] = []
    pages: dict[int, list[_Reading]] = {}
    for r in readings:
        pages.setdefault(r.element.page, []).append(r)
    for page in pages.values():
        page.sort(key=lambda r: r.element.order)
        for i, a in enumerate(page):
            for b in page[i + 1 :]:
                if a.element.id in gone or b.element.id in gone or a.norm != b.norm:
                    continue
                if min(_overlap(a.element, b.element)) < SAME_INK or not a.reading:
                    continue
                if sorted(a.box + b.box) != sorted(a.reading):
                    continue
                a_asked = a.fix.status is not Status.ACCEPTED and b.fix.status is Status.ACCEPTED
                keep, lose = (b, a) if a_asked else (a, b)
                gone.add(lose.element.id)
                merges.append(_Merge("accent", keep, (lose,)))
    return merges


def _plan(elements: list[Element], fixes: dict[str, Fix]) -> list[_Merge]:
    """Every formula written twice that the file's characters show, as merges."""
    readings = {
        e.id: r
        for e in elements
        if e.kind is Kind.EQUATION
        and e.display == "inline"
        and (r := _reading(e, fixes.get(e.id))) is not None
    }
    merges: list[_Merge] = []
    for parent in elements:
        if parent.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM) and parent.text:
            for run in _runs(parent.text, readings):
                merges.extend(_windows(parent.text, run))
    gone = {r.element.id for m in merges for r in (m.keep, *m.dropped)}
    return merges + _accents([r for i, r in readings.items() if i not in gone], gone)


def _spaced(text: str, start: int, end: int, insert: str) -> tuple[int, int, str]:
    """Where `text[start:end]` is replaced by `insert`, the same cut widened so the spacing is
    what a reader expects: one space between two words, or a word and a formula, where there was
    space on either side; none before closing punctuation or after an opening bracket; and a
    word never touches a formula that it did not touch before."""
    if insert:
        return start, end, insert  # (a formula for a formula: the spaces around it stay)
    left = text[start - 1] if start else ""
    right = text[end] if end < len(text) else ""
    if not left or not right:  # at an end of the text: no space is left against the edge
        while start and text[start - 1].isspace():
            start -= 1
        while end < len(text) and text[end].isspace():
            end += 1
    elif left.isspace() and right in _CLOSING:
        while start and text[start - 1].isspace():
            start -= 1
    elif right.isspace() and (left.isspace() or left in _OPENING):
        while end < len(text) and text[end].isspace():
            end += 1
    elif not left.isspace() and not right.isspace():
        if (left.isalnum() or left == _CLOSE) and (right.isalnum() or right == _OPEN):
            insert = " "
    return start, end, insert


def _edit(parent: Element, start: int, end: int, insert: str) -> Element | None:
    """`parent` with `text[start:end]` replaced by `insert` and its spans moved, or None when
    that cannot be done (not a paragraph, or nothing would be left)."""
    if parent.kind not in (Kind.PARAGRAPH, Kind.LIST_ITEM) or not parent.text:
        return None
    start, end, insert = _spaced(parent.text, start, end, insert)
    text = parent.text[:start] + insert + parent.text[end:]
    if not text.strip():
        return None
    shift = len(insert) - (end - start)

    def first(offset: int) -> int:
        return offset if offset <= start else offset + shift if offset >= end else start

    def last(offset: int) -> int:
        return (
            offset if offset <= start else offset + shift if offset >= end else start + len(insert)
        )

    def place(a: int, b: int) -> tuple[int, int] | None:
        return (first(a), last(b)) if last(b) > first(a) else None

    update, _ = inline.moved(parent, place)
    update["text"] = text
    if parent.term_end is not None:
        update["term_end"] = last(parent.term_end)
    return parent.model_copy(update=update)


def settle(
    elements: list[Element], fixes: dict[str, Fix]
) -> tuple[list[Element], dict[str, Fix], list[tuple[int, Fix]]]:
    """The elements and fixes with each repeated formula written once, and the dropped fixes
    with their pages (their cost and calls were spent: the caller counts them)."""
    merges = _plan(elements, fixes)
    if not merges:
        return elements, fixes, []
    by_id = {e.id: e for e in elements}
    dropped: dict[str, Fix] = {}
    for merge in merges:
        parent = by_id.get(merge.keep.element.parent_id or "")
        if parent is None or not parent.text:
            continue
        if merge.kind == "run":
            assert merge.first is not None and merge.last is not None
            start = parent.text.find(merge.first)
            end = parent.text.find(merge.last) + len(merge.last)
            cut = (
                _edit(parent, start, end, inline_marker(merge.keep.element.id))
                if start >= 0 and end > start
                else None
            )
        else:
            lose = merge.dropped[0].element
            parent = by_id.get(lose.parent_id or "")
            if parent is None or not parent.text:
                continue
            marker = inline_marker(lose.id)
            at = parent.text.find(marker)
            cut = _edit(parent, at, at + len(marker), "") if at >= 0 else None
        if cut is None:
            continue
        by_id[cut.id] = cut
        for d in merge.dropped:
            dropped[d.element.id] = d.fix
    if not dropped:
        return elements, fixes, []
    page_of = {e.id: e.page for e in elements}
    kept = [by_id[e.id] for e in elements if e.id not in dropped]
    return (
        kept,
        {i: f for i, f in fixes.items() if i not in dropped},
        [(page_of[i], f) for i, f in dropped.items()],
    )
