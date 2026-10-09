"""Where the marks go on a page image of the review screen: one clickable area and one
visible frame per fixed element, in PDF points.

A mark has two boxes:

- the *frame*, the element's own box with a little room (`FRAME_PAD_PT`), is what the
  reviewer sees: it hugs the equation or heading, so it never crosses the lines above
  and below;
- the *link* (the `<a>`), invisible, is what is clicked: at least 24 x 24 CSS px
  (WCAG 2.5.8) at the smallest size the page is ever drawn (`MIN_PAGE_PX` wide; the
  stylesheet keeps the page frame at least that wide), centred on the element.

On a dense page (an inline equation on every line of a handout) links that size overlap,
and a link partly under another is a smaller target than it looks and sends some clicks
to the wrong item. So overlapping links are pushed apart, each by half the overlap along
the axis where the overlap is smaller, but never so far that a link stops covering the
middle of its own element (`_slack`). The frames stay where the elements are. Only what
cannot be separated that way (elements that overlap each other) is left overlapping.

The layout covers every fix on the page, shown or not (accepted ones are only shown on
request), so whatever subset a view shows, no two of its links overlap.

A mark's number sits just left of its frame, except for an equation in a line of text,
where it would cover the word before: its number goes in the page margin on the same
line (`Mark.tag_x`), when the margin has room.
"""

from __future__ import annotations

from dataclasses import dataclass

BBox = tuple[float, float, float, float]

FRAME_PAD_PT = 3.0  # the frame is this much bigger than the element on every side (its border
# is drawn inside, so this is the room between the border and the item)
MIN_TARGET_PX = 24  # WCAG 2.5.8
MIN_PAGE_PX = 576  # the page frame's min-width (36rem at the default 16 px), app.css
TARGET_MARGIN = 1.05  # a little over 24 px, so rounding never leaves 23.9
GAP_PT = 0.4  # space left between two links pushed apart
ROUNDS = 60
PHASES = 4
MARGIN_TAG_PT = 28.0  # room a number needs in the page margin


@dataclass
class Mark:
    element_id: str
    element: BBox  # the element's own box
    frame: BBox  # what is drawn
    link: BBox  # what is clicked
    # Where the mark's number goes, when not just left of the frame: for an equation in
    # a line of text, the middle of the page margin on its side (x, in points), so it
    # covers no word of the line.
    tag_x: float | None = None


def min_target_pt(page_width: float) -> float:
    """24 CSS px in points, on a page drawn `MIN_PAGE_PX` wide (the smallest it gets)."""
    return MIN_TARGET_PX * TARGET_MARGIN * page_width / MIN_PAGE_PX


def _clamp(box: BBox, size: tuple[float, float]) -> BBox:
    x0, y0, x1, y1 = box
    width, height = size
    return (max(0.0, x0), max(0.0, y0), min(width, x1), min(height, y1))


def _centred(element: BBox, frame: BBox, side: float, page_size: tuple[float, float]) -> BBox:
    """The frame, grown to at least `side` on each axis around the element's centre, and
    moved onto the page if that made it stick out (an element at the very edge)."""
    x0, y0, x1, y1 = frame
    cx, cy = (element[0] + element[2]) / 2, (element[1] + element[3]) / 2
    half_w, half_h = max(x1 - x0, side) / 2, max(y1 - y0, side) / 2
    box = [min(x0, cx - half_w), min(y0, cy - half_h), max(x1, cx + half_w), max(y1, cy + half_h)]
    for axis, limit in ((0, page_size[0]), (1, page_size[1])):
        if box[axis + 2] - box[axis] <= limit:
            shift = max(0.0, -box[axis]) - max(0.0, box[axis + 2] - limit)
            box[axis] += shift
            box[axis + 2] += shift
    return (box[0], box[1], box[2], box[3])


def _overlap(a: BBox, b: BBox) -> tuple[float, float]:
    return min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])


def _slack(mark: Mark, axis: int, side: float) -> tuple[float, float]:
    """How far the link may move along `axis` (0: x, 1: y), back and forward, and still
    cover the middle of its element: the element's centre with `keep` either side,
    `keep` being half the element (a small one) or a quarter of a target (a large one)."""
    lo, hi = mark.element[axis], mark.element[axis + 2]
    centre = (lo + hi) / 2
    keep = min((hi - lo) / 2, side / 4)
    start, end = mark.link[axis], mark.link[axis + 2]
    return start - (centre + keep - (end - start)), (centre - keep) - start


def _shift(mark: Mark, axis: int, by: float) -> None:
    box = list(mark.link)
    box[axis] += by
    box[axis + 2] += by
    mark.link = (box[0], box[1], box[2], box[3])


def _separate(a: Mark, b: Mark, side: float, *, other_axis: bool = False) -> bool:
    """Push two overlapping links apart, along the axis where they overlap less (or the
    other one, `other_axis`), else along the other; True if either moved."""
    ox, oy = _overlap(a.link, b.link)
    if ox <= 0 or oy <= 0:
        return False
    axes = (0, 1) if (ox <= oy) != other_axis else (1, 0)
    for axis in axes:
        first, second = (a, b) if _mid(a, axis) <= _mid(b, axis) else (b, a)
        # The first one's far edge must end up before the second one's near edge (one
        # may contain the other: a heading's wide link round an equation's).
        need = first.link[axis + 2] - second.link[axis] + GAP_PT
        back = min(_slack(first, axis, side)[0], need)  # first moves back (left or up)
        ahead = min(_slack(second, axis, side)[1], need)  # second moves ahead
        back, ahead = max(back, 0.0), max(ahead, 0.0)
        if back + ahead < need - 1e-6:
            continue  # cannot be done along this axis: try the other one
        # Share the move, as evenly as each one's slack allows.
        half = need / 2
        move_back = min(back, max(half, need - ahead))
        move_ahead = need - move_back
        _shift(first, axis, -move_back)
        _shift(second, axis, move_ahead)
        return True
    return False


def _mid(mark: Mark, axis: int) -> float:
    return (mark.element[axis] + mark.element[axis + 2]) / 2


def _margin_x(element: BBox, page_width: float, text: tuple[float, float] | None) -> float | None:
    """The middle of the page margin beside an element in a line of text (left margin for
    the left half of the page, right for the right half), if there is room for a number
    there; `text` is how far the page's content reaches left and right."""
    if text is None:
        return None
    if (element[0] + element[2]) / 2 < page_width / 2:
        room = text[0]
        return room / 2 if room >= MARGIN_TAG_PT else None
    room = page_width - text[1]
    return page_width - room / 2 if room >= MARGIN_TAG_PT else None


def place(
    boxes: dict[str, BBox],
    page_size: tuple[float, float],
    *,
    inline: frozenset[str] = frozenset(),
    text: tuple[float, float] | None = None,
) -> dict[str, Mark]:
    """element id -> its mark, for the elements of one page (`page_size` in points).
    `inline` names the elements set in a line of text (their number goes in the margin),
    `text` is the page content's left and right edge."""
    side = min_target_pt(page_size[0])
    marks = []
    for element_id, element in boxes.items():
        pad = FRAME_PAD_PT
        frame = _clamp(
            (element[0] - pad, element[1] - pad, element[2] + pad, element[3] + pad), page_size
        )
        link = _centred(element, frame, side, page_size)
        tag_x = _margin_x(element, page_size[0], text) if element_id in inline else None
        marks.append(Mark(element_id, element, frame, link, tag_x))
    for attempt in range(PHASES):
        # Each pair along its cheaper axis until nothing moves; then the pairs still
        # overlapping (one squeezed between two others along that axis) along the other.
        for _ in range(ROUNDS):
            moved = False
            for i, a in enumerate(marks):
                for b in marks[i + 1 :]:
                    moved = _separate(a, b, side) or moved
            if not moved:
                break
        stuck = [(a, b) for i, a in enumerate(marks) for b in marks[i + 1 :] if _overlaps(a, b)]
        if not stuck or attempt == PHASES - 1:
            break
        for a, b in stuck:
            _separate(a, b, side, other_axis=True)
    return {m.element_id: m for m in marks}


def _overlaps(a: Mark, b: Mark) -> bool:
    ox, oy = _overlap(a.link, b.link)
    return ox > 1e-6 and oy > 1e-6


def overlapping(marks: dict[str, Mark]) -> list[tuple[str, str]]:
    """Pairs of links that still overlap (for tests and the docs' claim)."""
    items = list(marks.values())
    return [
        (a.element_id, b.element_id)
        for i, a in enumerate(items)
        for b in items[i + 1 :]
        if _overlaps(a, b)
    ]
