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

That 24 px layout is the floor. Afterwards each link grows toward 44 CSS px (`COMFORT_PX`,
`_grow`) as far as it can without touching another link: a link on a quiet part of the page
is a 44 px target, one between neighbours stays 24 px with space around it, which is what
2.5.8 asks for.

The layout covers every fix on the page, shown or not (accepted ones are only shown on
request), so whatever subset a view shows, no two of its links overlap.

Only links that can ever touch are compared. A link moves only along the axes where it still
covers the middle of its element (`_slack`), so each has a region it never leaves (`_reach`);
two marks whose regions are apart are never compared, and the rest are compared in the order
all pairs would have been, so the result is the same as comparing every pair, at a cost that
grows with the marks that are near each other, not with the square of all of them.

A mark's number sits just left of its frame, except for an equation in a line of text,
where it would cover the word before: its number goes in the page margin on the same
line (`Mark.tag_x`), when the margin has room. Numbers of neighbouring lines are 22 px high
and the lines 12 pt apart, so a number that would cover an earlier one moves outward along
its line, as far as the margin goes (`_space_tags`).
"""

from __future__ import annotations

from dataclasses import dataclass

BBox = tuple[float, float, float, float]

FRAME_PAD_PT = 3.0  # the frame is this much bigger than the element on every side (its border
# is drawn inside, so this is the room between the border and the item)
MIN_TARGET_PX = 24  # WCAG 2.5.8
MIN_PAGE_PX = 576  # the page frame's min-width (36rem at the default 16 px), app.css
COMFORT_PX = 44  # the size a link is grown to where the page has room (WCAG 2.5.5)
TARGET_MARGIN = 1.05  # a little over 24 px, so rounding never leaves 23.9
GAP_PT = 0.4  # space left between two links pushed apart
ROUNDS = 60
PHASES = 4
TRIES_PER_MARK = 150  # pairs tried for each mark before laying out gives up (`_separate_all`)
# Bump when `place` or the rules made from its marks (`routes.split._box_rules`) change what
# they write for the same boxes: the stylesheet's `ETag` carries it, so a browser holding
# the old stylesheet asks again.
PLACEMENT_VERSION = 2
NEAR_PT = 1e-6  # two boxes this close count as touching (rounding never hides a neighbour)
MARGIN_TAG_PT = 28.0  # room a number needs in the page margin
TAG_WIDTH_PX = 26  # a two-digit number (app.css `.box-tag`: 14 px digits, 5 px padding)
TAG_HEIGHT_PX = 22
TAG_HALF_PX = 11  # a number's `left` is its middle less this (`routes.split._box_rules`)
TAG_GAP_PX = 4  # between a number and the frame it sits left of
MAX_TAG_COLUMNS = 4  # a number moves out at most this many numbers' widths


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
    # `_overlap`, written out: this runs for every pair near each other, most of them apart.
    al, bl = a.link, b.link
    ox = min(al[2], bl[2]) - max(al[0], bl[0])
    if ox <= 0:
        return False
    oy = min(al[3], bl[3]) - max(al[1], bl[1])
    if oy <= 0:
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
    _separate_all(marks, side)
    _grow(marks, page_size, comfortable_pt(page_size[0]))
    _space_tags(marks, page_size, text)
    return {m.element_id: m for m in marks}


def tag_box(mark: Mark, page_width: float) -> BBox:
    """Where the mark's number is, in points, on a page drawn `MIN_PAGE_PX` wide (app.css
    `.box-tag`: beside the frame, or at `tag_x`, on the middle of the frame's height)."""
    px = page_width / MIN_PAGE_PX
    width, height = TAG_WIDTH_PX * px, TAG_HEIGHT_PX * px
    left = (
        mark.frame[0] - TAG_GAP_PX * px - width
        if mark.tag_x is None
        else mark.tag_x - TAG_HALF_PX * px
    )
    middle = (mark.frame[1] + mark.frame[3]) / 2
    return (left, middle - height / 2, left + width, middle + height / 2)


def overlapping_tags(placed: dict[str, Mark], page_width: float) -> list[tuple[str, str]]:
    """Pairs of numbers that still cover each other (for tests and the docs' claim)."""
    items = [(m.element_id, tag_box(m, page_width)) for m in placed.values()]
    return [
        (a, b)
        for i, (a, box_a) in enumerate(items)
        for b, box_b in items[i + 1 :]
        if _overlaps_box(box_a, box_b)
    ]


def _space_tags(
    marks: list[Mark], page_size: tuple[float, float], text: tuple[float, float] | None
) -> None:
    """Keep two numbers from covering each other. A number is 22 CSS px high, so the numbers
    of items on neighbouring lines (12 pt apart) overlap when they sit one above the other.
    A number that covers another moves along its own line to a free place: in a margin one
    of the places one number wide that fit in it (nearest first), beside a frame further
    left; where there is none it stays. Every number counts, shown at rest or not: the layout
    never depends on a decision. Sizes are those of a page drawn `MIN_PAGE_PX` wide, the
    smallest, where the numbers are largest against the page."""
    px = page_size[0] / MIN_PAGE_PX  # points in a CSS px
    tag_w = TAG_WIDTH_PX * px
    base = [tag_box(m, page_size[0]) for m in marks]
    middles = [(b[1] + b[3]) / 2 for b in base]
    lefts = [b[0] for b in base]  # each number's left edge, which is what moves
    tag_h = base[0][3] - base[0][1] if base else 0.0

    def box(at: float, i: int) -> BBox:
        return (at, base[i][1], at + tag_w, base[i][3])

    order = sorted(range(len(marks)), key=lambda i: middles[i])
    crowded: set[int] = set()
    for at, i in enumerate(order):
        for j in order[at + 1 :]:
            if middles[j] - middles[i] >= tag_h:
                break
            if _overlaps_box(box(lefts[i], i), box(lefts[j], j)):
                crowded.update((i, j))
    taken = [box(lefts[i], i) for i in range(len(marks)) if i not in crowded]
    for i in sorted(crowded):
        margin = None  # the margin the number is in, if it is in one
        if text is not None and marks[i].tag_x is not None:
            margin = (
                (text[1], page_size[0]) if marks[i].tag_x > page_size[0] / 2 else (0.0, text[0])
            )
        elif text is not None and lefts[i] >= 0.0 and lefts[i] + tag_w <= text[0] + NEAR_PT:
            margin = (0.0, text[0])  # beside a frame, and in the left margin
        if margin is None:
            places = [lefts[i] - k * tag_w for k in range(1, MAX_TAG_COLUMNS + 1)]
            places = [at for at in places if at >= 0.0]
        else:
            count = int((margin[1] - margin[0]) / tag_w + 1e-6)
            places = sorted(
                (margin[0] + k * tag_w for k in range(count)), key=lambda at: abs(at - lefts[i])
            )
        for at in places[:MAX_TAG_COLUMNS]:
            if not any(_overlaps_box(box(at, i), other) for other in taken):
                marks[i].tag_x = at + TAG_HALF_PX * px
                lefts[i] = at
                break
        taken.append(box(lefts[i], i))


def _separate_all(marks: list[Mark], side: float) -> None:
    """Push the overlapping links apart. Each pair along its cheaper axis until nothing
    moves; then the pairs still overlapping (one squeezed between two others along that
    axis) along the other. Where marks cannot all be separated (elements that overlap each
    other) the pushing can go round for a long time, so the work is bounded: `TRIES_PER_MARK`
    pairs tried for each mark, and the loop ends there with the links as they are."""
    pairs = _near_pairs([_reach(m, side) for m in marks])
    # What a pair does depends only on where its two links are: a pair that did nothing is
    # not tried again until one of its links has moved (`moves` counts each link's moves).
    moves = [0] * len(marks)
    idle: dict[tuple[int, int], tuple[int, int]] = {}
    tries = TRIES_PER_MARK * len(marks)
    for attempt in range(PHASES):
        for _ in range(ROUNDS):
            moved = False
            for pair in pairs:
                i, j = pair
                if idle.get(pair) == (moves[i], moves[j]):
                    continue
                if tries <= 0:
                    return
                tries -= 1
                if _separate(marks[i], marks[j], side):
                    moves[i] += 1
                    moves[j] += 1
                    moved = True
                else:
                    idle[pair] = (moves[i], moves[j])
            if not moved:
                break
        stuck = [(i, j) for i, j in pairs if _overlaps(marks[i], marks[j])]
        if not stuck or attempt == PHASES - 1:
            return
        for i, j in stuck:
            if _separate(marks[i], marks[j], side, other_axis=True):
                moves[i] += 1
                moves[j] += 1


def _reach(mark: Mark, side: float) -> BBox:
    """Every place the link can be in while it is laid out: where it starts, and as far
    either way along each axis as `_slack` lets it go (a link moves only inside that)."""
    box = []
    for axis in (0, 1):
        back, ahead = _slack(mark, axis, side)
        box.append((mark.link[axis] - max(back, 0.0), mark.link[axis + 2] + max(ahead, 0.0)))
    return (box[0][0], box[1][0], box[0][1], box[1][1])


def _near_pairs(boxes: list[BBox]) -> list[tuple[int, int]]:
    """The index pairs (i < j, in order) of the boxes that touch or overlap: a sweep along x,
    so each box is looked at against the few that start inside its width."""
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    pairs = []
    for at, i in enumerate(order):
        _, y0, x1, y1 = boxes[i]
        for j in order[at + 1 :]:
            b = boxes[j]
            if b[0] > x1 + NEAR_PT:
                break
            if b[1] <= y1 + NEAR_PT and b[3] >= y0 - NEAR_PT:
                pairs.append((min(i, j), max(i, j)))
    return sorted(pairs)


def comfortable_pt(page_width: float) -> float:
    """44 CSS px in points, on a page drawn `MIN_PAGE_PX` wide: the size a target is grown
    to where the page has room (WCAG 2.5.5)."""
    return COMFORT_PX * TARGET_MARGIN * page_width / MIN_PAGE_PX


def _grow(marks: list[Mark], page_size: tuple[float, float], goal: float) -> None:
    """Grow each link toward `goal` on both axes, around its centre, as far as it can without
    overlapping another link. The 24 px layout above is the floor: a link with no room keeps
    its size (so 2.5.8 holds everywhere), one with room becomes a 44 px target. Frames do
    not change. Earlier marks claim their room first."""
    # The most a link can ever cover is its own grown box (it only gets bigger), so a link
    # is compared only with the ones whose largest box touches its largest box.
    largest = []
    for mark in marks:
        x0, y0, x1, y1 = mark.link
        gx, gy = max(0.0, goal - (x1 - x0)) / 2, max(0.0, goal - (y1 - y0)) / 2
        largest.append(_clamp((x0 - gx, y0 - gy, x1 + gx, y1 + gy), page_size))
    near: list[list[int]] = [[] for _ in marks]
    for i, j in _near_pairs(largest):
        near[i].append(j)
        near[j].append(i)
    for at, mark in enumerate(marks):
        x0, y0, x1, y1 = mark.link
        grow_x, grow_y = max(0.0, goal - (x1 - x0)) / 2, max(0.0, goal - (y1 - y0)) / 2
        if grow_x <= 0 and grow_y <= 0:
            continue
        others = [marks[j].link for j in near[at]]

        def grown(t: float, x0=x0, y0=y0, x1=x1, y1=y1, gx=grow_x, gy=grow_y) -> BBox:
            return _clamp((x0 - gx * t, y0 - gy * t, x1 + gx * t, y1 + gy * t), page_size)

        def free(box: BBox, others=others) -> bool:
            return all(
                not (_overlap(box, o)[0] > 1e-6 and _overlap(box, o)[1] > 1e-6) for o in others
            )

        if not free(grown(0.0)):
            continue  # already overlapping (elements that overlap each other): leave it
        if free(grown(1.0)):
            mark.link = grown(1.0)
            continue
        low, high = 0.0, 1.0
        for _ in range(14):
            middle = (low + high) / 2
            low, high = (middle, high) if free(grown(middle)) else (low, middle)
        mark.link = grown(low)


def _overlaps_box(a: BBox, b: BBox) -> bool:
    ox, oy = _overlap(a, b)
    return ox > 1e-6 and oy > 1e-6


def _overlaps(a: Mark, b: Mark) -> bool:
    return _overlaps_box(a.link, b.link)


def overlapping(marks: dict[str, Mark]) -> list[tuple[str, str]]:
    """Pairs of links that still overlap (for tests and the docs' claim)."""
    items = list(marks.values())
    return [
        (a.element_id, b.element_id)
        for i, a in enumerate(items)
        for b in items[i + 1 :]
        if _overlaps(a, b)
    ]
