"""One formula that layout cut into two boxes is written once.

Layout sometimes puts one formula into two boxes: an inline formula cut at a wide space, an
accent and its letter, a line of a display that was also taken as an inline box. Each box is
read with a picture of its line, and the line prompt tells the model to read on past a box that
stops early, so each reading comes back as the whole formula and the page said it twice. Layout
is left alone (a joined box is a new crop and a new request); the repeat is taken out here,
once every reading is in, where the results are put into the document (`run._convert`, next to
`table_math.settle`).

Two boxes are one formula when one of these holds (`_pairs`):

- **Next to each other.** Two inline equations of one paragraph on one line, with nothing between
  their markers but space, or words the reading covers too, and the readings repeat (below).
- **On the same ink.** Two inline equations whose boxes lie on each other (half of the smaller
  box or more) and whose readings repeat: an accent cut from its letter.
- **Inside a display.** An inline equation whose box is inside the box of a display equation
  whose reading holds all of its letters.

The readings *repeat* when they are the same formula and each box alone holds only part of it
(next to each other: a symbol printed twice, each box holding all of it, is two symbols), or when
one reading goes past its own box and holds all the characters of the other box (the letters,
digits and Greek letters of the PDF's text there, `text_layer_compare.symbol_bag`).

The reading kept is the one that holds the other's characters (the whole formula; the other is
only a part of it), else the accepted one over the one asked about, else the first. The other
element writes nothing: it is taken out of the elements with its fix and its crop, and its
marker out of the paragraph's text (the paragraph's links, colours and runs move with the text,
`core.inline`). A decision stays asked wherever the reading kept is not accepted; one asked
about the dropped copy only is gone with it. What the dropped copy cost is handed back, so the
document's cost and call counts stay what was spent. A formula in three boxes takes more
than one pass (`settle`). A pair that cannot be written once
(the paragraph would be left empty, a fix that is not a reading) is left as it is.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

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

# The share of the smaller box that another box must cover to be on the same ink, and of an inline
# box that a display's box must cover to hold it.
SAME_INK = 0.5
INSIDE = 0.8
# The share of the shorter box's height that two boxes of one line share.
SAME_LINE = 0.5

_STYLE = re.compile(r"\\(?:mathbf|boldsymbol|bm|mathrm|mathit|text|left|right|displaystyle)\b")
_SPACING = re.compile(r"\\[,;:! ]|[\s{}]")


@dataclass(frozen=True)
class _Reading:
    element: Element
    fix: Fix
    norm: str  # the LaTeX without style words, spacing and braces
    reading: Counter[str]  # letters, digits and Greek letters of the reading
    box: Counter[str]  # the same of the PDF's text in the box
    printed: bool  # the PDF has text in the box (a picture or a handwritten page has none)


def _reading(element: Element, fix: Fix | None) -> _Reading | None:
    """What a settled equation says, or None when its fix is not a reading to compare."""
    if (
        fix is None
        or fix.equation is None
        or fix.status not in (Status.ACCEPTED, Status.NEEDS_REVIEW)
    ):
        return None
    try:
        reading = text_layer_compare.mathml_bag(fix.equation.mathml)
    except Exception:  # (a MathML that does not parse is no reading to compare)
        return None
    norm = _SPACING.sub("", _STYLE.sub("", fix.equation.latex))
    box = text_layer_compare.symbol_bag(element.text or "")
    return _Reading(element, fix, norm, reading, box, bool((element.text or "").strip()))


def _past(a: _Reading) -> bool:
    """Does the reading hold letters its own box does not?"""
    return bool(a.reading - a.box)


def _covers(a: _Reading, b: _Reading) -> bool:
    """Does `a`'s reading go past its own box and hold every character of `b`'s box?"""
    return bool(b.box) and _past(a) and not (b.box - a.reading)


def _relation(a: _Reading, b: _Reading, *, each_partial: bool) -> str | None:
    """ "same" (one formula read twice), "a" or "b" (that reading is the whole, the other box
    only a part of it), or None (two formulas)."""
    covers_a, covers_b = _covers(a, b), _covers(b, a)
    if a.norm == b.norm:  # (not the same letters: f(n) = O(g(n)) and g(n) = O(f(n)) have them)
        if not (a.printed or b.printed):
            return None  # no characters in the PDF (a handwritten page): nothing to compare
        if each_partial and not (a.box and b.box and _past(a) and _past(b)):
            return None  # a symbol printed twice: each box holds all of it
        return "same"
    if covers_a and covers_b:
        return "same"
    return "a" if covers_a else "b" if covers_b else None


def _overlap(a: Element, b: Element) -> tuple[float, float]:
    """The area both boxes cover, as a share of each box's own area."""
    (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = a.bbox, b.bbox
    width = min(ax1, bx1) - max(ax0, bx0)
    height = min(ay1, by1) - max(ay0, by0)
    area_a, area_b = (ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0)
    if width <= 0 or height <= 0 or area_a <= 0 or area_b <= 0:
        return 0.0, 0.0
    return width * height / area_a, width * height / area_b


def _same_line(a: Element, b: Element) -> bool:
    height = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1])
    shared = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
    return height > 0 and shared / height >= SAME_LINE


def _gap_ok(parent: Element, a: _Reading, b: _Reading) -> bool:
    """Is what the paragraph has between the two markers only space, or words the readings
    say too? The markers are in the paragraph's text in this order or the other."""
    text = parent.text or ""
    first, second = sorted(
        (text.find(inline_marker(a.element.id)), text.find(inline_marker(b.element.id)))
    )
    if first < 0:
        return False
    gap = text[first + len(inline_marker(a.element.id)) : second]
    if INLINE_MARKER_RE.search(gap):
        return False  # a third formula is between them
    left = text_layer_compare.symbol_bag(gap)
    return not (left - (a.reading | b.reading))


def _pairs(elements: list[Element], fixes: dict[str, Fix]) -> list[tuple[_Reading, _Reading, str]]:
    """The pairs of equations that are one formula, each as (the first in reading order, the
    second, which is the whole: "same", "a" or "b")."""
    by_id = {e.id: e for e in elements}
    pages: dict[int, list[_Reading]] = {}
    for element in elements:
        if element.kind is Kind.EQUATION and (r := _reading(element, fixes.get(element.id))):
            pages.setdefault(element.page, []).append(r)
    found: list[tuple[_Reading, _Reading, str]] = []
    for page in pages.values():
        page.sort(key=lambda r: r.element.order)
        for i, a in enumerate(page):
            for b in page[i + 1 :]:
                ea, eb = a.element, b.element
                if ea.display == "inline" and eb.display == "inline":
                    parent = by_id.get(ea.parent_id or "")
                    if max(_overlap(ea, eb)) >= SAME_INK:
                        rel = _relation(a, b, each_partial=False)
                    elif (
                        parent is not None
                        and ea.parent_id == eb.parent_id
                        and _same_line(ea, eb)
                        and _gap_ok(parent, a, b)
                    ):
                        rel = _relation(a, b, each_partial=True)
                    else:
                        continue
                    if rel:
                        found.append((a, b, rel))
                elif ea.display != eb.display:  # an inline box inside a display's box
                    inner, outer = (a, b) if ea.display == "inline" else (b, a)
                    share = _overlap(inner.element, outer.element)[0]
                    if (
                        share >= INSIDE
                        and inner.printed
                        and inner.reading
                        and not (inner.reading - outer.reading)
                    ):
                        found.append((a, b, "a" if outer is a else "b"))
    return found


def _removed(text: str, marker: str) -> tuple[str, int, int] | None:
    """`text` without `marker` and the space before it (after it at the start of the text), and
    the cut as (start, end), or None when nothing would be left or the marker is not there."""
    at = text.find(marker)
    if at < 0:
        return None
    start, end = at, at + len(marker)
    if start and text[start - 1].isspace():
        while start and text[start - 1].isspace():
            start -= 1
    else:
        while end < len(text) and text[end].isspace():
            end += 1
    rest = text[:start] + text[end:]
    return (rest, start, end) if rest.strip() else None


def _without_marker(parent: Element, marker: str) -> Element | None:
    """`parent` with its text and spans cut by `marker`, or None if it cannot be."""
    if parent.kind not in (Kind.PARAGRAPH, Kind.LIST_ITEM) or not parent.text:
        return None
    cut = _removed(parent.text, marker)
    if cut is None:
        return None
    rest, start, end = cut

    def move(offset: int) -> int:
        return offset if offset <= start else start if offset < end else offset - (end - start)

    def place(first: int, last: int) -> tuple[int, int] | None:
        new_first, new_last = move(first), move(last)
        return (new_first, new_last) if new_last > new_first else None

    update, _ = inline.moved(parent, place)
    update["text"] = rest
    if parent.term_end is not None:
        update["term_end"] = move(parent.term_end)
    return parent.model_copy(update=update)


PASSES = 4  # a formula cut into more than two boxes takes one pass for each repeat


def settle(
    elements: list[Element], fixes: dict[str, Fix]
) -> tuple[list[Element], dict[str, Fix], list[tuple[int, Fix]]]:
    """The elements and fixes with each repeated formula written once, and the dropped fixes
    with their pages (their cost and calls were spent: the caller counts them).

    A formula in three boxes is read three times: the first pass takes out one repeat, and the
    two boxes left are next to each other for the second."""
    spent: list[tuple[int, Fix]] = []
    for _ in range(PASSES):
        elements, fixes, dropped = _once(elements, fixes)
        if not dropped:
            break
        spent.extend(dropped)
    return elements, fixes, spent


def _once(
    elements: list[Element], fixes: dict[str, Fix]
) -> tuple[list[Element], dict[str, Fix], list[tuple[int, Fix]]]:
    pairs = _pairs(elements, fixes)
    if not pairs:
        return elements, fixes, []
    by_id = {e.id: e for e in elements}
    dropped: dict[str, Fix] = {}
    for a, b, whole in pairs:
        if a.element.id in dropped or b.element.id in dropped:
            continue
        if whole == "same":  # the accepted one stays, else the first
            a_asked = a.fix.status is not Status.ACCEPTED and b.fix.status is Status.ACCEPTED
            lose = a if a_asked else b
        else:
            lose = b if whole == "a" else a
        parent = by_id.get(lose.element.parent_id or "")
        if lose.element.display != "inline" or parent is None:
            continue  # (a display is never the one dropped)
        cut = _without_marker(parent, inline_marker(lose.element.id))
        if cut is None:
            continue
        by_id[parent.id] = cut
        dropped[lose.element.id] = lose.fix
    if not dropped:
        return elements, fixes, []
    page_of = {e.id: e.page for e in elements}
    kept = [by_id[e.id] for e in elements if e.id not in dropped]
    return (
        kept,
        {i: f for i, f in fixes.items() if i not in dropped},
        [(page_of[i], f) for i, f in dropped.items()],
    )
