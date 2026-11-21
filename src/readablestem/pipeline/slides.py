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
   letter or A4 sheet) that (a) says it is Beamer (`/Creator`), or (b) has consecutive pages
   that share a page label (an overlay's pages have one, `hyperref`'s), or (c) has a title on
   most of its pages: the page's largest text in the top third (`_title`), or a title slide's
   title (`_title_slide`), on at least `TITLED_SHARE` of the pages. (c) needs neither Beamer
   nor labels: a PowerPoint or Keynote deck exported as PDF has neither. For (c) a letter or
   A4 sheet on its side counts as well: a deck printed to PDF is on one, and its titles say
   it is a deck. Pages kept whole as images (`PageView.kept_whole`: scanned slides) have no text
   and are left out of the share. A file that is none of these is not a deck and is read as before.
2. Decks: a file may hold several. A deck ends where the page size changes, and where a title
   slide comes (a page with a title-sized line, at least `TITLE_SLIDE_RATIO` x an ordinary
   slide title, and at most `MAX_SUBTITLE_ROWS` other lines in its body) when the file's first
   page is one too. A deck that starts on a title slide has that slide's title as its title
   (`Deck.starts`). When the file holds more than one deck, or a deck has a title, each heading
   on a deck's pages says which deck it is in (`DECK_REASON`), and `layout.heading_levels`
   judges heading sizes deck by deck: a deck's title is level 1 and every other heading of the
   deck is level 2 or lower, so the written outline is the document's title, then each deck's
   title, then its slide titles (`assemble.title_plan` places the one H1).
3. Frames: consecutive pages with the same page label, or without labels the same title text,
   are one frame; in a deck found by (c) alone, a frame is a run of pages each of which extends
   the page before it (every line and mark of the earlier page is on the later one, as
   `_repeats` decides). A page of a frame is dropped when every line on it, and every picture,
   is also on the frame's last page: it is an earlier step of the same slide. Nothing is lost;
   the last page has it all. A page with something the last page does not have (`\\only<1>{A}`
   replaced by `\\only<2>{B}`) stays, as a step: "step 1 of 2" is added to its heading.
4. Titles: the frame title is the largest text in the top third of the page (at least 1.15 x
   the body; a line that is only a formula is no title), and always a heading; its lines are
   one title when they follow each other in one size. What lies above it in the page's
   headline (section and subsection names, a drawn bar) is furniture and is dropped, and slide
   numbers alone in the top or bottom band are too. The
   headline's text is kept as `Deck.context` for the page, so a reader of the record can still
   see which section a frame was in.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Sequence
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
# Most of a deck's pages have a title. The titled files among the 22 landscape ones of the corpus
# (decks of 6 to 39 pages) have a title on 91 to 100 % of their pages; the files that are not
# decks (pictured and photographed slides, a template) on 0 to 33 %. Any share between the two
# separates them; 70 % lets a deck of mostly titled slides keep a third of its pages without
# one (a picture slide, a credits page).
TITLED_SHARE = 0.7
TITLE_SLIDE_RATIO = 1.25  # a title slide's title over an ordinary slide's title
MAX_SUBTITLE_ROWS = 3  # lines on a title slide besides its title (subtitle, author, date)
TITLE_LINE_GAP = 1.5  # a title's next line starts within this many times its size
STEP_REASON = "slide_step"
TITLE_REASON = "slide_title"
DECK_REASON = "slide_deck"  # "slide_deck 2": the heading is on a page of the second deck
DECK_TITLE_REASON = "slide_deck_title"  # the title of a deck that starts on a title slide
_BEAMER = re.compile(r"\bbeamer\b", re.IGNORECASE)
_SLIDE_NUMBER = re.compile(r"^\s*\d{1,3}(?:\s*/\s*\d{1,3})?\s*$")
# A name of a function in a formula ("sin", "log"): not a word of text.
_FUNCTION = re.compile(
    r"(?<![^\W\d_])(?:sin|cos|tan|cot|sec|csc|log|ln|exp|lim|det|max|min|sup|inf|arg|gcd|mod)h?"
    r"(?![^\W\d_])"
)
_WORD = re.compile(r"[^\W\d_]{3,}")


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


class LazyMarks:
    """A page's marks, read the first time `detect` looks at them. A page's marks are compared
    only when its rows are all on the last page of its frame (`_repeats`), so a file with no
    deck, or a page that is the only one of its frame, never has them read: reading them
    decodes every picture of the page and keys every drawing on it."""

    def __init__(self, read: Callable[[], Sequence[Mark]]) -> None:
        self._read: Callable[[], Sequence[Mark]] | None = read
        self._marks: Sequence[Mark] = ()

    def __iter__(self) -> Iterator[Mark]:
        if self._read is not None:
            read, self._read = self._read, None
            self._marks = read()
        return iter(self._marks)


@dataclass(frozen=True)
class PageView:
    """What `detect` needs to know of one page."""

    number: int  # 0-based
    width: float
    height: float
    label: str | None  # the page label the PDF gives it, if any
    rows: Sequence[RowView]
    # every image, and the drawings in the body of the slide (`LazyMarks` reads them late)
    marks: Iterable[Mark] = ()
    # The page is kept whole as its image (a scanned or photographed slide): it has no rows
    # here, and `detect` leaves it out of the share of titled pages.
    kept_whole: bool = False


@dataclass(frozen=True)
class Title:
    """A title: its text, the box and size of its first line, and where its last line ends
    (0 when it has one line)."""

    text: str
    bbox: BBox
    size: float
    bottom: float = 0.0

    @property
    def height(self) -> float:
        """From the top of the first line to the bottom of the last."""
        return max(self.bottom, self.bbox[3]) - self.bbox[1]


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
    # Each page of a deck -> its number (1, 2, ...) among the file's decks, kept only when the
    # file holds several decks or a deck has a title (`DECK_REASON`).
    numbers: dict[int, int] = field(default_factory=dict)
    # The first page of a deck that starts on a title slide -> that slide's title.
    starts: dict[int, Title] = field(default_factory=dict)


def deck_number(reasons: Iterable[str]) -> int | None:
    """The number of the deck a heading is in, from its `DECK_REASON`; None when it has none."""
    for reason in reasons:
        head, _, number = reason.partition(" ")
        if head == DECK_REASON and number.isdigit():
            return int(number)
    return None


def slide_shaped(width: float, height: float) -> bool:
    """Landscape, 4:3 / 16:9 / 16:10, and not a sheet of paper turned on its side."""
    if width <= 0 or height <= 0 or width <= height or _sheet_on_its_side(width, height):
        return False
    aspect = width / height
    return any(abs(aspect / a - 1) <= ASPECT_TOLERANCE for a in SLIDE_ASPECTS)


def _sheet_on_its_side(width: float, height: float) -> bool:
    """A sheet of paper turned on its side (`PAPER_SIZES`): what a deck printed to PDF from
    PowerPoint, Keynote or a browser is. Only titles make such a file a deck (`detect`)."""
    return any(
        abs(width - w) <= PAPER_TOLERANCE and abs(height - h) <= PAPER_TOLERANCE
        for w, h in PAPER_SIZES
    )


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


def _is_formula(text: str) -> bool:
    """A line that is a formula or a bare number and nothing else: it has no word of three
    letters that is not a function name ("sin", "log"), and it has a math sign or an italic
    math letter ("=", an integral sign, "x"), or no letter at all (the "1" over the bar of a
    fraction). "Slopes and Rates", "Q & A" and "AI" are text; "x = -b / 2a", "pH = -log10 [H+]"
    and "1" are not."""
    plain = "".join(c for c in text if not 0x1D400 <= ord(c) <= 0x1D7FF)
    if _WORD.search(_FUNCTION.sub(" ", plain)):
        return False
    return (
        len(plain) < len(text)
        or not any(c.isalpha() for c in text)
        or any(unicodedata.category(c) == "Sm" for c in text)
    )


def _title(view: PageView, body: float) -> Title | None:
    """The largest text in the top of the page, at least MIN_TITLE_RATIO x the body; the
    topmost when several are as large. A line that is a formula only is not a title: a slide
    with a large formula under its title is titled by the title."""
    top = [
        row
        for row in view.rows
        if not row.vertical
        and row.text.strip()
        and not _is_formula(row.text)
        and row.bbox[1] <= TITLE_ZONE * view.height
        and row.size >= MIN_TITLE_RATIO * body
    ]
    if not top:
        return None
    largest = max(row.size for row in top)
    best = min((row for row in top if row.size >= largest / 1.05), key=lambda r: r.bbox[1])
    return _with_lines(view.rows, best)


def _with_lines(rows: Sequence[RowView], first: RowView) -> Title:
    """The title that starts at `first`: the lines of its size that follow it, each starting
    within TITLE_LINE_GAP x the size of where the one before ended (a title that wraps)."""
    text, last = [first.text.strip()], first
    for row in sorted(rows, key=lambda r: (r.bbox[1], r.bbox[0])):
        if (
            not row.vertical
            and row.text.strip()
            and row.bbox[1] > last.bbox[1]
            and abs(row.size - first.size) <= 0.05 * first.size
            and row.bbox[1] - last.bbox[3] <= (TITLE_LINE_GAP - 1) * first.size
            and abs(row.bbox[0] - first.bbox[0]) <= first.size
        ):
            text.append(row.text.strip())
            last = row
    return Title(" ".join(text), first.bbox, first.size, last.bbox[3])


def _title_slide(view: PageView, ordinary: float, own: Title | None) -> Title | None:
    """The title of a title slide: a page whose largest line is at least TITLE_SLIDE_RATIO x
    `ordinary` (the size of an ordinary slide's title) and which has at most MAX_SUBTITLE_ROWS
    other lines in its body (the top and bottom NUMBER_BAND are the headline and footline).
    The title may be anywhere on the page: a title slide's is often in the middle. A page with
    a title of its own (`own`, found in the top of the page) that is smaller than its largest
    line is an ordinary slide with a big formula or picture caption, not a title slide."""
    rows = [r for r in view.rows if not r.vertical and r.text.strip() and not _is_formula(r.text)]
    if not rows or ordinary <= 0:
        return None
    largest = max(r.size for r in rows)
    if largest < TITLE_SLIDE_RATIO * ordinary or (own is not None and own.size < largest / 1.05):
        return None
    first = min((r for r in rows if r.size >= largest / 1.05), key=lambda r: (r.bbox[1], r.bbox[0]))
    title = _with_lines(rows, first)
    others = [
        r
        for r in rows
        if r.size < largest / 1.05
        and NUMBER_BAND * view.height
        <= (r.bbox[1] + r.bbox[3]) / 2
        <= (1 - NUMBER_BAND) * view.height
    ]
    return title if len(others) <= MAX_SUBTITLE_ROWS else None


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
    slide = [slide_shaped(v.width, v.height) for v in views]
    # A sheet of paper on its side counts for (c) only: Beamer and page labels are not what
    # says a printed deck is one, its titles are.
    shaped = [
        ok or _sheet_on_its_side(v.width, v.height) for v, ok in zip(views, slide, strict=True)
    ]
    if sum(shaped) < 0.8 * len(views):
        return None
    beamer = bool(creator and _BEAMER.search(creator))
    body = _body_size(views)
    titles = {v.number: t for v in views if (t := _title(v, body)) is not None}
    ordinary = statistics.median(t.size for t in titles.values()) if titles else 0.0
    title_slides = {
        v.number: t
        for v in views
        if (t := _title_slide(v, ordinary, titles.get(v.number))) is not None
    }
    frames = _frames(views, titles)
    overlaid = any(len(frame) > 1 for frame in frames)
    # Not Beamer: the pages must have at least two different labels, and the labels must group
    # some pages and not all (a label tree with a prefix and no numbers labels every page alike).
    labelled = len({v.label for v in views if v.label}) >= 2 and len(frames) >= 2
    by_label = (beamer or (overlaid and labelled)) and sum(slide) >= 0.8 * len(views)
    # A page kept whole has no text to be titled by: the pages that have text are judged, so a
    # deck with some scanned slides is still one (a file of no text pages is not).
    readable = [v for v in views if not v.kept_whole]
    titled = sum(v.number in titles or v.number in title_slides for v in readable)
    if not by_label and (not readable or titled < TITLED_SHARE * len(readable)):
        return None
    # A title slide recurring starts a deck when the file starts with one.
    runs = _decks(views, title_slides if views[0].number in title_slides else ())
    deck = Deck(beamer=beamer, labels={v.number: v.label for v in views if v.label})
    # A run that is mostly slide-shaped is a deck (the rest of a file, say the portrait pages
    # between two decks, is not numbered).
    slid = {v.number for v, ok in zip(views, shaped, strict=True) if ok}
    decks = [run for run in runs if 2 * sum(v.number in slid for v in run) > len(run)]
    for run in decks:
        if run[0].number in title_slides:
            deck.starts[run[0].number] = title_slides[run[0].number]
    if len(decks) > 1 or deck.starts:
        deck.numbers = {v.number: n for n, run in enumerate(decks, start=1) for v in run}
    for run in runs:
        for frame in _frames(run, titles) if by_label else _step_frames(run):
            deck.frames.append(tuple(v.number for v in frame))
            _read_frame(deck, frame, titles)
    return deck


def _decks(views: Sequence[PageView], starts: Iterable[int]) -> list[list[PageView]]:
    """The file's pages as decks: a new deck starts where the page size changes and at each
    page in `starts` (title slides)."""
    begin = set(starts)
    runs: list[list[PageView]] = []
    for view in views:
        if runs:
            last = runs[-1][-1]
            same_size = abs(view.width - last.width) <= 1 and abs(view.height - last.height) <= 1
            if same_size and view.number not in begin:
                runs[-1].append(view)
                continue
        runs.append([view])
    return runs


def _step_frames(views: Sequence[PageView]) -> list[list[PageView]]:
    """Runs of consecutive pages that are one slide by content: each page extends the page
    before it (`_repeats`: every line and mark of the earlier page is on the later one)."""
    frames: list[list[PageView]] = []
    for view in views:
        if frames and view.number == frames[-1][-1].number + 1 and _repeats(frames[-1][-1], view):
            frames[-1].append(view)
        else:
            frames.append([view])
    return frames


def _read_frame(deck: Deck, frame: Sequence[PageView], titles: dict[int, Title]) -> None:
    """Which pages of the frame are earlier steps, which stay as steps, and their titles."""
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
    return _inside(title.bbox, element.bbox) >= 0.5 and tall <= 3 * title.height


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
    # A deck that starts on a title slide: the element that is its title is a heading, the
    # deck's.
    deck_titles: set[str] = set()
    for page, start in deck.starts.items():
        found = next(
            (
                e
                for e in elements
                if e.page == page
                and e.id not in gone
                and e.kind in (Kind.PARAGRAPH, Kind.HEADING)
                and _is_title(e, start)
            ),
            None,
        )
        if found is not None:
            deck_titles.add(found.id)
            if found.kind == Kind.PARAGRAPH and found.id not in updates:
                updates[found.id] = {
                    "kind": Kind.HEADING,
                    "reasons": [*found.reasons, TITLE_REASON, *title_reasons],
                    "font_size": found.font_size or start.size,
                }
    out = []
    for e in elements:
        if e.id in gone:
            continue
        e = e.model_copy(update=updates[e.id]) if e.id in updates else e
        if e.kind == Kind.HEADING and (number := deck.numbers.get(e.page)) is not None:
            reasons = [*e.reasons, f"{DECK_REASON} {number}"]
            if e.id in deck_titles:
                reasons.append(DECK_TITLE_REASON)
            e = e.model_copy(update={"reasons": reasons})
        out.append(e)
    return [e if e.order == i else e.model_copy(update={"order": i}) for i, e in enumerate(out)]


def _mark_step(update: dict, element: Element, deck: Deck) -> None:
    """Say in the title's text which step of its frame the page is."""
    if step := deck.steps.get(element.page):
        text = update.get("text", element.text) or ""
        update["text"] = f"{text} (step {step[0]} of {step[1]})"
        update["reasons"] = [*update.get("reasons", element.reasons), STEP_REASON]
