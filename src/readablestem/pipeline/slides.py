"""Slide decks (Beamer and other slide PDFs): frames, overlay steps and the furniture around them.

A deck is a PDF that is a sequence of slides, not of pages: a landscape slide-shaped page,
a frame title at the top of each, a headline and a footline (section names, an author, "3 / 20",
navigation symbols) repeated around the content, and, in Beamer, one PDF page for every
overlay step (`\\pause`, `\\item<2->`, `\\uncover`) that repeats the steps before it. Read
page by page that gives the same paragraph two or three times, frame titles dropped as running
headers (they repeat on the pages of one frame), and a header bar of section names as figures.

This module decides what a deck is and what in it is a frame (docs/layout.md, "Slide decks").
It knows nothing about PyMuPDF: `layout` hands it a `PageView` of every page it read and gets
back a `Deck`, and then applies it (`layout._analyze_document`). Deterministic, no model calls.

1. `detect`: a deck is a landscape file in a slide shape (4:3, 16:9 or 16:10; not a landscape
   letter or A4 sheet) that says it is Beamer (`/Creator`), or whose consecutive pages share a
   page label (an overlay's pages have one, `hyperref`'s). A file that is neither is not a
   deck and is read as before.
2. Frames: consecutive pages with the same page label, or without labels the same title text,
   are one frame. A page of a frame is dropped when every line on it, and every picture, is
   also on the frame's last page: it is an earlier step of the same slide. Nothing is lost;
   the last page has it all. A page with something the last page does not have (`\\only<1>{A}`
   replaced by `\\only<2>{B}`) stays, as a step: "step 1 of 2" is added to its heading.
3. Titles: the frame title is the largest text in the top third of the page (at least 1.15 x
   the body), and always a heading. What lies above it in the page's headline (section and
   subsection names, a drawn bar) is furniture and is dropped, and slide numbers alone in the
   top or bottom band are too. The headline's text is kept as `Deck.context` for the page, so
   a reader of the record can still see which section a frame was in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from readablestem.core.types import BBox, Element, Kind

# Slide shapes (width / height) with their tolerance: Beamer's 4:3 and 16:9, widescreen 16:10.
SLIDE_ASPECTS = (4 / 3, 16 / 9, 16 / 10)
ASPECT_TOLERANCE = 0.02
# Landscape sheets of paper that happen to be near 4:3 (US Letter 1.29, A4 1.41) are never slides.
PAPER_SIZES = ((792.0, 612.0), (842.0, 595.0), (1008.0, 612.0), (1190.0, 842.0))
PAPER_TOLERANCE = 6.0
MIN_TITLE_RATIO = 1.15  # the frame title's font size over the body's
TITLE_ZONE = 0.35  # the frame title is in this top share of the page
HEADLINE_ZONE = 0.2  # a headline bar is in this top share
BAR_WIDTH = 0.6  # a drawn bar is at least this share of the page wide ...
BAR_HEIGHT = 0.18  # ... and at most this share of its height
NUMBER_BAND = 0.12  # a slide number alone is in this top or bottom share
STEP_REASON = "slide_step"
TITLE_REASON = "slide_title"
_BEAMER = re.compile(r"\bbeamer\b", re.IGNORECASE)
_SLIDE_NUMBER = re.compile(r"^\s*\d{1,3}(?:\s*/\s*\d{1,3})?\s*$")


@dataclass(frozen=True)
class RowView:
    """One line of text on a slide, as `layout` read it."""

    text: str
    bbox: BBox
    size: float
    vertical: bool = False


@dataclass(frozen=True)
class Mark:
    """A picture or a drawing on a slide: what it is (`kind`: "image" or "drawing"), a digest of
    its content or shape and colour (`key`), and where it is."""

    kind: str
    key: str
    bbox: BBox


@dataclass(frozen=True)
class PageView:
    """What `detect` needs to know of one page."""

    number: int  # 0-based
    width: float
    height: float
    label: str | None  # the page label the PDF gives it, if any
    rows: Sequence[RowView]
    marks: Sequence[Mark] = ()  # every image, and the drawings in the body of the slide


@dataclass(frozen=True)
class Title:
    text: str
    bbox: BBox
    size: float


@dataclass
class Deck:
    """What was found: a deck's frames, titles and which pages are earlier steps."""

    beamer: bool
    # Dropped page -> the last page of its frame, which holds everything it had.
    dropped: dict[int, int] = field(default_factory=dict)
    # Each page's label, for the slide numbers that are furniture.
    labels: dict[int, str] = field(default_factory=dict)
    # Kept page -> (its place among the frame's kept pages, how many) when there are several.
    steps: dict[int, tuple[int, int]] = field(default_factory=dict)
    titles: dict[int, Title] = field(default_factory=dict)
    # Each kept page's headline text (section and subsection names), top to bottom.
    context: dict[int, tuple[str, ...]] = field(default_factory=dict)
    frames: list[tuple[int, ...]] = field(default_factory=list)  # pages of each frame, as read


def slide_shaped(width: float, height: float) -> bool:
    """Landscape, 4:3 / 16:9 / 16:10, and not a sheet of paper turned on its side."""
    if width <= 0 or height <= 0 or width <= height:
        return False
    if any(
        abs(width - w) <= PAPER_TOLERANCE and abs(height - h) <= PAPER_TOLERANCE
        for w, h in PAPER_SIZES
    ):
        return False
    aspect = width / height
    return any(abs(aspect / a - 1) <= ASPECT_TOLERANCE for a in SLIDE_ASPECTS)


def _norm(text: str) -> str:
    return "".join(text.split())


ROW_SHIFT = 0.1  # share of the page a row may move between steps and still be the same row
MARK_SHIFT = 3.0  # pt a picture or drawing may move between steps


def _order(rows: Sequence[RowView]) -> list[RowView]:
    return sorted(rows, key=lambda r: (round(r.bbox[1] / 4), r.bbox[0]))


def _repeats(step: PageView, last: PageView) -> bool:
    """Everything on `step` is on `last`: each row of it is a whole row of `last` (the same
    text exactly, in the same order, about where it was), and each picture and drawing is on
    `last` (the same content, about where it was). A row that is only part of a longer one
    ("a" in "Example: b"), a number inside another ("5" in "x=15") or a reordering is not."""
    if not step.rows:
        return False
    rows = _order(last.rows)
    at = 0
    for row in _order(step.rows):
        text = _norm(row.text)
        while at < len(rows) and not (
            _norm(rows[at].text) == text
            and abs(rows[at].bbox[1] - row.bbox[1]) <= ROW_SHIFT * step.height
            and abs(rows[at].bbox[0] - row.bbox[0]) <= ROW_SHIFT * step.width
        ):
            at += 1
        if at == len(rows):
            return False
        at += 1
    free = list(last.marks)
    for mark in step.marks:
        for i, other in enumerate(free):
            if (
                other.kind == mark.kind
                and other.key == mark.key
                and all(
                    abs(a - b) <= MARK_SHIFT for a, b in zip(other.bbox, mark.bbox, strict=True)
                )
            ):
                del free[i]
                break
        else:
            return False
    return True


def _frames(views: Sequence[PageView], titles: dict[int, Title]) -> list[list[PageView]]:
    """Runs of consecutive pages that are one frame: the same page label, or (when a page has
    no label) the same title text."""
    frames: list[list[PageView]] = []
    for view in views:
        if frames:
            last = frames[-1][-1]
            consecutive = view.number == last.number + 1
            if view.label and last.label:
                same = view.label == last.label
            else:
                a, b = titles.get(last.number), titles.get(view.number)
                same = a is not None and b is not None and _norm(a.text) == _norm(b.text)
            if consecutive and same:
                frames[-1].append(view)
                continue
        frames.append([view])
    return frames


def _title(view: PageView, body: float) -> Title | None:
    """The largest text in the top of the page, at least MIN_TITLE_RATIO x the body; the
    topmost when several are as large."""
    top = [
        row
        for row in view.rows
        if not row.vertical
        and row.text.strip()
        and row.bbox[1] <= TITLE_ZONE * view.height
        and row.size >= MIN_TITLE_RATIO * body
    ]
    if not top:
        return None
    largest = max(row.size for row in top)
    best = min((row for row in top if row.size >= largest / 1.05), key=lambda r: r.bbox[1])
    return Title(best.text.strip(), best.bbox, best.size)


def _body_size(views: Sequence[PageView]) -> float:
    """The deck's body size: the font size most characters are set in."""
    weight: dict[float, int] = {}
    for view in views:
        for row in view.rows:
            if not row.vertical:
                key = round(row.size * 2) / 2
                weight[key] = weight.get(key, 0) + len(row.text.strip())
    return max(weight.items(), key=lambda kv: (kv[1], -kv[0]))[0] if weight else 10.0


def detect(creator: str | None, views: Sequence[PageView]) -> Deck | None:
    """The deck `views` (the analysed pages, ascending) make up, or None when they are not
    slides. Never raises."""
    if len(views) < 2:
        return None
    shaped = [slide_shaped(v.width, v.height) for v in views]
    if sum(shaped) < 0.8 * len(views):
        return None
    beamer = bool(creator and _BEAMER.search(creator))
    body = _body_size(views)
    titles = {v.number: t for v in views if (t := _title(v, body)) is not None}
    frames = _frames(views, titles)
    overlaid = any(len(frame) > 1 for frame in frames)
    # Not Beamer: the pages must have at least two different labels, and the labels must group
    # some pages and not all (a label tree with a prefix and no numbers labels every page alike).
    labelled = len({v.label for v in views if v.label}) >= 2 and len(frames) >= 2
    if not beamer and not (overlaid and labelled):
        return None
    deck = Deck(
        beamer=beamer,
        frames=[tuple(v.number for v in frame) for frame in frames],
        labels={v.number: v.label for v in views if v.label},
    )
    for frame in frames:
        last = frame[-1]
        kept = [last]
        for view in frame[:-1]:
            if _repeats(view, last):
                deck.dropped[view.number] = last.number
            else:
                kept.insert(len(kept) - 1, view)
        if len(kept) > 1:
            for k, view in enumerate(kept, start=1):
                deck.steps[view.number] = (k, len(kept))
        for view in kept:
            if (title := titles.get(view.number)) is not None:
                deck.titles[view.number] = title
                above = [
                    row.text.strip()
                    for row in sorted(view.rows, key=lambda r: (r.bbox[1], r.bbox[0]))
                    if row.bbox[3] <= title.bbox[1] + 0.2 * (title.bbox[3] - title.bbox[1])
                    and row.text.strip()
                ]
                if above:
                    deck.context[view.number] = tuple(above)
    return deck


def _inside(box: BBox, other: BBox) -> float:
    """The share of `box` that lies inside `other`."""
    w = min(box[2], other[2]) - max(box[0], other[0])
    h = min(box[3], other[3]) - max(box[1], other[1])
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
    return max(0.0, w) * max(0.0, h) / area


def _is_title(element: Element, title: Title) -> bool:
    """The element is the frame title's own text: it holds the title's box and is no taller
    than a few lines of it (a drawn bar that holds the title as a label is not)."""
    tall = element.bbox[3] - element.bbox[1]
    return _inside(title.bbox, element.bbox) >= 0.5 and tall <= 3 * (title.bbox[3] - title.bbox[1])


def _place(e: Element) -> tuple[int, int]:
    """Where a number stands, to 6 pt: its left edge and whether it is in the top or the bottom."""
    return round(e.bbox[0] / 6), round(e.bbox[1] / 6)


def _number_places(
    elements: list[Element], sizes: dict[int, tuple[float, float]]
) -> dict[tuple[int, int], int]:
    """How many pages have a bare number paragraph in each place near the top or bottom."""
    pages: dict[tuple[int, int], set[int]] = {}
    for e in elements:
        height = sizes.get(e.page, (0.0, 0.0))[1]
        if (
            e.kind == Kind.PARAGRAPH
            and height
            and _SLIDE_NUMBER.match((e.text or "").strip())
            and (e.bbox[3] <= NUMBER_BAND * height or e.bbox[1] >= (1 - NUMBER_BAND) * height)
        ):
            pages.setdefault(_place(e), set()).add(e.page)
    return {place: len(found) for place, found in pages.items()}


def _is_slide_number(
    e: Element, deck: Deck, places: dict[tuple[int, int], int], total: int
) -> bool:
    """A bare number near an edge is furniture when it is the page's own number ("3" or "3 / 20"
    on the slide labelled 3) or when most pages carry a number in that very place. Any other
    number there ("2" over a figure's axis) is content."""
    label = deck.labels.get(e.page)
    first = re.match(r"\s*(\d+)", e.text or "")
    if label and first and label.lstrip("0") == first.group(1).lstrip("0"):
        return True
    count = places.get(_place(e), 0)
    return count >= 2 and 2 * count > total


def finish(
    elements: list[Element],
    deck: Deck,
    sizes: dict[int, tuple[float, float]],
    title_reasons: Sequence[str] = (),
) -> list[Element]:
    """The elements with the deck's furniture removed and each frame title a heading.

    Dropped, with the inline equations they own: a page's headline (a paragraph above the
    frame title that is smaller than it, and a drawn bar across the page), and a slide
    number alone in the top or bottom band. A title that layout read as a paragraph, or that
    a headline bar took as its label (a themed bar is a picture, and the title is printed on
    it), becomes a heading, with `title_reasons` (layout's own for a title by its size); a
    step's heading says which step it is. `order` is renumbered."""
    gone: set[str] = set()
    updates: dict[str, dict] = {}
    titled = {
        e.page
        for e in elements
        if e.kind in (Kind.PARAGRAPH, Kind.HEADING)
        and (t := deck.titles.get(e.page)) is not None
        and _is_title(e, t)
    }
    numbered = _number_places(elements, sizes)
    for e in elements:
        title = deck.titles.get(e.page)
        width, height = sizes.get(e.page, (0.0, 0.0))
        text = (e.text or "").strip()
        if e.kind == Kind.PARAGRAPH and height and _SLIDE_NUMBER.match(text):
            near = e.bbox[3] <= NUMBER_BAND * height or e.bbox[1] >= (1 - NUMBER_BAND) * height
            if near and _is_slide_number(e, deck, numbered, len(sizes)):
                gone.add(e.id)
                continue
        if title is None or e.kind not in (Kind.PARAGRAPH, Kind.FIGURE, Kind.HEADING):
            continue
        bar = (
            e.kind == Kind.FIGURE
            and (e.bbox[2] - e.bbox[0]) >= BAR_WIDTH * width
            and e.bbox[1] <= HEADLINE_ZONE * height
            and (e.bbox[3] - e.bbox[1]) <= BAR_HEIGHT * height
        )
        headline = (
            e.kind == Kind.PARAGRAPH
            and (e.bbox[1] + e.bbox[3]) / 2 < title.bbox[1]
            and e.bbox[1] <= HEADLINE_ZONE * height
            and (e.font_size or 0.0) <= 0.8 * title.size
        )
        if e.kind != Kind.FIGURE and _is_title(e, title):
            update: dict = {}
            if e.kind == Kind.PARAGRAPH:
                update = {
                    "kind": Kind.HEADING,
                    "reasons": [*e.reasons, TITLE_REASON, *title_reasons],
                    "font_size": e.font_size or title.size,
                }
            _mark_step(update, e, deck)
            if update:
                updates[e.id] = update
        elif bar and _inside(title.bbox, e.bbox) >= 0.5 and e.page not in titled:
            # The bar holds the title as a label: the bar becomes the title's heading.
            update = {
                "kind": Kind.HEADING,
                "text": title.text,
                "bbox": title.bbox,
                "font_size": title.size,
                "caption": None,
                "reasons": [TITLE_REASON, *title_reasons],
            }
            _mark_step(update, e, deck)
            updates[e.id] = update
            titled.add(e.page)
        elif bar or headline:
            gone.add(e.id)
    # An inline equation goes with its paragraph.
    gone |= {e.id for e in elements if e.parent_id in gone}
    out = [
        e.model_copy(update=updates[e.id]) if e.id in updates else e
        for e in elements
        if e.id not in gone
    ]
    return [e if e.order == i else e.model_copy(update={"order": i}) for i, e in enumerate(out)]


def _mark_step(update: dict, element: Element, deck: Deck) -> None:
    """Say in the title's text which step of its frame the page is."""
    if step := deck.steps.get(element.page):
        text = update.get("text", element.text) or ""
        update["text"] = f"{text} (step {step[0]} of {step[1]})"
        update["reasons"] = [*update.get("reasons", element.reasons), STEP_REASON]
