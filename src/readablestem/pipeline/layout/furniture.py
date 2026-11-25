"""Page margins: the filter that drops running heads, page numbers and margin notes that repeat
from page to page, and the figures and marks found in a margin band.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace

import pymupdf

from readablestem.core.types import BBox
from readablestem.pipeline import page_type
from readablestem.pipeline.layout.context import _Document, _PageGuard
from readablestem.pipeline.layout.drawings import _page_paths
from readablestem.pipeline.layout.figures import (
    FULL_PAGE_FRACTION,
    HEADING_SIZE_RATIO,
    _centre_inside,
    _image_xrefs,
    _merge_figures,
    _raster_figures,
    _vector_figures,
)
from readablestem.pipeline.layout.rows import _page_rows, take_form_fields
from readablestem.pipeline.layout.shared import _area, _box_key, _Display, _Path, _Row

MARGIN_BAND = 0.07  # top/bottom share of the page searched for running headers/footers
MARGIN_PLACE_BAND = 0.10  # ... and for a head found by its place (`_MarginFilter.places`)
MARGIN_PLACE_PT = 2.0  # rows of the same side's pages this near in height are in one place
MARGIN_SIZE_PT = 0.5  # ... and this near in font size are of one size
NOTE_MARK_RATIO = 0.85  # x row size: a first character this small is a note's mark
MARGIN_REFERENCE_PAGES = 24  # pages sampled to decide what repeats
MARGIN_SHAPE_BAND = 0.12  # top/bottom share of the page where a head or foot is found by shape
SHAPE_CLEAR = 1.0  # x line height: a head or foot stands this far clear of the text block
SHAPE_PAGES = 1  # heads with words are found by shape on files of at most this many pages
SHAPE_LINES = 4  # a head or foot found by shape is at most this many lines
NOTE_WORDS = 8  # a row of this many words that opens in lower case continues a sentence
WATERMARK_RATIO = 2.0  # diagonal text this many times the body size is a watermark

PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|of)\s*\d{1,4})?$", re.IGNORECASE
)


_PAGE_DIGITS = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?$", re.IGNORECASE)
# A number that says it is a page's: "Page 3", "Page 3 of 9", "3 of 9". "25" and "17/20" do not
# (points, a score), so lines with words are never taken on their strength (`by_shape`).
_PAGE_WORDS = re.compile(
    r"^(?:page\s+\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?|\d{1,4}\s+of\s+\d{1,4})$", re.IGNORECASE
)


def _norm_margin(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _box_zone(box: BBox, height: float, band: float = MARGIN_BAND) -> str | None:
    centre = (box[1] + box[3]) / 2
    if centre <= height * band:
        return "top"
    if centre >= height * (1 - band):
        return "bottom"
    return None


def _margin_zone(row: _Row, height: float) -> str | None:
    return _box_zone(row.bbox, height)


def _is_page_number(text: str) -> bool:
    return bool(PAGE_NUMBER.match(text.strip(" -\u2013\u2014|")))


def _is_page_digits(text: str) -> bool:
    """A page's number in digits, or "Page 3 of 9": not a roman numeral, which may be a word,
    and not a bare year, which is a date in a table or a foot."""
    text = text.strip(" -\u2013\u2014|")
    if text.isdigit() and 1900 <= int(text) <= 2100:
        return False
    return bool(_PAGE_DIGITS.match(text))


def _lines_of(rows: Sequence[_Row]) -> list[list[_Row]]:
    """The rows grouped by the line they stand on (a vertical overlap of half the smaller
    height), each line left to right."""
    lines: list[list[_Row]] = []
    for row in sorted(rows, key=lambda r: r.y0):
        for line in lines:
            first = line[0]
            if min(first.y1, row.y1) - max(first.y0, row.y0) >= 0.5 * min(first.height, row.height):
                line.append(row)
                break
        else:
            lines.append([row])
    return [sorted(line, key=lambda r: r.x0) for line in lines]


def _ends_in_number(line: Sequence[_Row], *, worded: bool = False) -> bool:
    """Does the line hold one page number, at its start or its end? `worded`: one that says
    it is a page's ("Page 3 of 9"), which a bare number does not."""
    numbers = [r for r in line if _is_page_digits(r.clean)]
    if len(numbers) != 1 or not (numbers[0] is line[0] or numbers[0] is line[-1]):
        return False
    return not worded or bool(_PAGE_WORDS.match(numbers[0].clean.strip(" -\u2013\u2014|")))


def _is_note_text(row: _Row) -> bool:
    """A row of a sentence's length that opens in lower case: the rest of a sentence from the
    page before (a footnote's lines), never the words of a head or foot."""
    text = row.clean.lstrip()
    return text[:1].islower() and len(text.split()) >= NOTE_WORDS


def _line_box(line: Sequence[_Row]) -> BBox:
    return (
        min(r.x0 for r in line),
        min(r.y0 for r in line),
        max(r.x1 for r in line),
        max(r.y1 for r in line),
    )


def _row_italic(row: _Row) -> bool:
    letters = [c for c in row.ink if c.c.isalnum()]
    return bool(letters) and all(c.italic for c in letters)


def _rotated_key(row: _Row) -> tuple[str, int, int]:
    """Same text near the same place: a watermark or a stamp repeated on every page."""
    return _norm_margin(row.clean), round((row.x0 + row.x1) / 40), round((row.y0 + row.y1) / 40)


# The line arXiv prints up the left margin of a paper's first page: "arXiv:2403.06391v1
# [quant-ph] 10 Mar 2024", or with an identifier of the old form and no subject,
# "arXiv:hep-th/9711200v3 22 Jan 1998".
_ARXIV_STAMP = re.compile(r"^arXiv:\S+(?:\s+\[[\w.-]+\])?\s+\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}$")


def _is_side_stamp(row: _Row) -> bool:
    """Is this turned row the stamp of the repository a paper was taken from? It stands on
    one page, so nothing repeats to say that it is no content (`_MarginFilter.rotated`), and
    its place in the height of the page put it between two paragraphs of the text. It is
    known by its words alone, and only when turned: a turned line at the edge of a page may
    also be the title of a chart's axis, and no place on the page tells the two apart."""
    return row.vertical and not row.diagonal and bool(_ARXIV_STAMP.match(row.clean.strip()))


@dataclass
class _MarginFilter:
    """What repeats on the pages and is not content: running headers and footers, page
    numbers, logos in the header/footer band, watermarks; and the one line that is no
    content without repeating, a repository's stamp up the side of a page (`_is_side_stamp`).

    Margin text is dropped when its digit-normalised text repeats in the same band, unless
    it stands out (larger than body text, or bold at body size or more) and its digits change
    from page to page: "Problem 3 (15 points)" in 14 pt at the top of each page is a heading,
    "Notes - page 3" in 9 pt is a running header.

    A book's heads change by chapter, carry a page number, and differ between left and right
    pages, so the same words rarely repeat on half of all the pages. Text that is not body
    text (smaller than it, italic, or in another typeface) is also dropped when a row of the
    same size stands at the same height on half of the pages on its side of the book (odd or
    even) and the line is a head's or a foot's by more than its place: it carries the running
    page number, or its words stand there on another page (`places`). The place alone is not
    enough: the last line of a footnote, a short footnote, the source line of a slide all
    stand at one height in small type on page after page. Such a row is dropped for good,
    not given back as a figure's label.
    """

    repeated: set[tuple[str, str]]  # (zone, normalised text)
    fixed: set[tuple[str, str]]  # ... of those, the ones whose text never changes
    rotated: set[tuple[str, int, int]]  # rotated text repeated at the same place
    figures: set[tuple[str, tuple[int, int, int, int]]]  # (zone, box) of band figures
    body: float
    body_family: str = ""
    # Where rows repeat at one place on the pages of one side (`_repeated_places`).
    places: list[_Place] = field(default_factory=list)
    # (zone, box) of every drawing and image in the band that repeats: a logo however
    # small, a decorative line. Anything else in the band (a note in the margin) counts.
    marks: set[tuple[str, tuple[int, int, int, int]]] = field(default_factory=set)
    # Pictures covering the page that repeat (by xref): a template's background.
    backgrounds: set[int] = field(default_factory=set)
    # How many pages were looked at: on one page nothing repeats (`by_shape`).
    pages: int = 0

    def drops(self, row: _Row, height: float) -> bool:
        if row.vertical:
            if row.diagonal and row.size >= WATERMARK_RATIO * self.body:
                return True  # a large diagonal "DRAFT" across the page
            if _rotated_key(row) in self.rotated or _is_side_stamp(row):
                return True
        zone = _margin_zone(row, height)
        if zone is None:
            return False
        text = row.clean
        if _is_page_number(text) and row.size < HEADING_SIZE_RATIO * self.body:
            return True  # (a number set large at the top is a chapter number)
        key = (zone, _norm_margin(text))
        return key in self.repeated and (key in self.fixed or not self.prominent(row))

    def prominent(self, row: _Row) -> bool:
        """A heading's look: larger than body text, or bold and not smaller than it (a bold
        head in small type, as books set theirs, is no heading)."""
        return row.size >= HEADING_SIZE_RATIO * self.body or (
            row.bold and row.size > self.body - MARGIN_SIZE_PT
        )

    def apart(self, row: _Row) -> bool:
        """Not set as body text: smaller than it, italic, or in another typeface."""
        return (
            row.size <= self.body - MARGIN_SIZE_PT
            or _row_italic(row)
            or bool(self.body_family and row.family and row.family != self.body_family)
        )

    def at_place(self, row: _Row, height: float, number: int) -> bool:
        """Is this row of page `number` (0-based) a head or foot by its place (`places`): at
        a place where rows of that side's pages repeat, on a line that carries the page number
        or with words that stand there on another page? Rows that look like headings, body
        text or the start of a note never are."""
        if not self.places or not self.furniture_look(row):
            return False
        zone = _box_zone(row.bbox, height, MARGIN_PLACE_BAND)
        return zone is not None and any(
            place.holds(zone, number, row)
            and (place.numbered or _norm_margin(row.clean) in place.texts)
            for place in self.places
        )

    def by_shape(self, frags: Sequence[_Row], height: float, out: Collection[int]) -> set[int]:
        """The ids of the rows of a head or foot found by its shape, where no repeat from page
        to page can say so. At each edge of the page, the lines that stand together in the
        outer `MARGIN_SHAPE_BAND` (at most `SHAPE_LINES`, each less than a line from the one
        before) and then `SHAPE_CLEAR` lines clear of the rest of the page, with none set as a
        heading, and one of them ending in a page number (digits, or "Page 3 of 9"), are the
        head or foot of the page. A line of a table, a list or a note has a neighbor a line
        away, so it never stands clear; a page of nothing but the lines has no text block to
        be clear of. A page number alone on its line counts on every file (a number set low
        on a tall page is outside the band of `drops`); lines with words, on files of at most
        `SHAPE_PAGES` pages (on longer ones the words repeat and `drops` has them), and only
        with a number that says it is a page's ("Page 3", "3 of 9") or with every word set
        smaller than the body text, as a running head is: the name line and the points of a
        one-page quiz end in a bare number too, but are set as the body is, and nothing
        repeats to tell them from a running head."""
        # A row already taken (a page number the place rule has) is evidence, not a row to take.
        lines = _lines_of([f for f in frags if not f.vertical])
        taken: set[int] = set()
        for top in (True, False):
            edge = sorted(lines, key=lambda ln: _line_box(ln)[1], reverse=not top)
            stack = self._stack(edge, height, top)
            if stack and self._is_furniture_stack(stack):
                taken.update(id(r) for ln in stack for r in ln if id(r) not in out)
        return taken

    def _stack(self, edge: list[list[_Row]], height: float, top: bool) -> list[list[_Row]]:
        """The lines at one edge of the page, outermost first, that stand together and clear
        of the text block (`by_shape`); none when the text runs on into the page."""
        stack: list[list[_Row]] = []
        for line in edge:
            _, y0, _, y1 = _line_box(line)
            if stack:
                _, last0, _, last1 = _line_box(stack[-1])
                gap = y0 - last1 if top else last0 - y1
                if gap >= SHAPE_CLEAR * max(y1 - y0, last1 - last0):
                    return stack  # this line is the start of the text block
                if len(stack) == SHAPE_LINES:
                    return []
            if (
                (y1 > MARGIN_SHAPE_BAND * height)
                if top
                else (y0 < (1 - MARGIN_SHAPE_BAND) * height)
            ):
                return []  # the text goes on into the page
            stack.append(line)
        return []  # nothing follows: no text block to be clear of

    def _is_furniture_stack(self, stack: list[list[_Row]]) -> bool:
        """Does one line of the stack end in a page number, and is nothing in it a heading?
        Lines with words go only with a number that says it is a page's, or with words all set
        smaller than the body text: "Points ... 25" at the head of a quiz and "Total marks ...
        50" at its foot end in a number too, in the type of the body."""
        if not any(_ends_in_number(line) for line in stack):
            return False
        words = [r for line in stack for r in line if not _is_page_digits(r.clean)]
        if words and (
            self.pages > SHAPE_PAGES
            or not (
                any(_ends_in_number(line, worded=True) for line in stack)
                or (
                    all(r.size <= self.body - MARGIN_SIZE_PT for r in words)
                    and not any(_is_note_text(r) for r in words)
                )
            )
        ):
            return False
        return not any(self.prominent(r) for r in words) and all(
            r.size < HEADING_SIZE_RATIO * self.body for line in stack for r in line
        )

    def furniture_look(self, row: _Row) -> bool:
        """Could the row be a running head or foot: level, not set as a heading or as body
        text, and not opening with a raised mark (a footnote)?"""
        return (
            not row.vertical
            and not self.prominent(row)
            and self.apart(row)
            and not _opens_with_mark(row)
        )

    def numbering(self, row: _Row, height: float) -> bool:
        """The row's band text repeats on the reference pages and changes from page to
        page: a page-number series ("24", "25", ...), not a figure's label."""
        key = (_margin_zone(row, height), _norm_margin(row.clean))
        return key in self.repeated and key not in self.fixed

    def drops_figure(self, box: BBox, height: float) -> bool:
        zone = _box_zone(box, height)
        return zone is not None and (zone, _box_key(box)) in self.figures

    def drops_mark(self, box: BBox, height: float) -> bool:
        """A drawing or image in the header or footer band that repeats there (part of a
        logo or of the template), or that a repeated band figure holds."""
        zone = _box_zone(box, height)
        if zone is None:
            return False
        if (zone, _box_key(box)) in self.marks:
            return True
        return any(
            z == zone and _centre_inside(box, (k[0] - 1, k[1] - 1, k[2] + 1, k[3] + 1))
            for z, k in self.figures
        )


def _reference_pages(page_count: int, selected: list[int], skip: Collection[int] = ()) -> list[int]:
    """The analysed pages plus their neighbours, up to MARGIN_REFERENCE_PAGES pages. `skip`:
    pages that are not neighbours (a slide deck's earlier overlay steps repeat the frame
    title, which would make it a running header)."""
    chosen = set(selected[:MARGIN_REFERENCE_PAGES])
    distance = 1
    room = min(page_count - len(skip), MARGIN_REFERENCE_PAGES)
    while len(chosen) < room and distance < page_count:
        for page in selected:
            for candidate in (page - distance, page + distance):
                if (
                    0 <= candidate < page_count
                    and candidate not in skip
                    and len(chosen) < MARGIN_REFERENCE_PAGES
                ):
                    chosen.add(candidate)
        distance += 1
    return sorted(chosen)


def _band_figures(
    page: pymupdf.Page, paths: list[_Path], *, document: _Document
) -> set[tuple[str, tuple]]:
    """(zone, rounded box) of the figures a page has in its header/footer band."""
    rect, height = page.rect, page.rect.height
    band_paths = [p for p in paths if _box_zone(p.bbox, height) is not None]
    figures = _merge_figures(
        _raster_figures(page, rect) + _vector_figures(band_paths, [], rect, document=document)
    )
    keys = set()
    for figure in figures:
        zone = _box_zone(figure.bbox, height)
        if zone is not None:
            keys.add((zone, _box_key(figure.bbox)))
    return keys


def _band_marks(page: pymupdf.Page, paths: list[_Path]) -> set[tuple[str, tuple]]:
    """(zone, rounded box) of every drawing and image in the header/footer band."""
    height = page.rect.height
    display = _Display.of(page)
    boxes = [p.bbox for p in paths] + [display.box(i["bbox"]) for i in page.get_image_info()]
    return {(zone, _box_key(b)) for b in boxes if (zone := _box_zone(b, height)) is not None}


def _page_pictures(page: pymupdf.Page) -> list[int]:
    """The xrefs of the pictures covering the page (FULL_PAGE_FRACTION of it; 0 for an
    inline image)."""
    display = _Display.of(page)
    rect = page.rect
    infos = page.get_image_info()
    big = [
        k
        for k, info in enumerate(infos)
        if _area(_clip_box(display.box(info["bbox"]), rect))
        >= FULL_PAGE_FRACTION * rect.width * rect.height
    ]
    if not big:
        return []
    xrefs = _image_xrefs(page, len(infos))
    return [xrefs[k] for k in big]


def _clip_box(box: BBox, rect: pymupdf.Rect) -> BBox:
    return (max(box[0], 0.0), max(box[1], 0.0), min(box[2], rect.width), min(box[3], rect.height))


def _margin_filter(
    doc: pymupdf.Document,
    selected: list[int],
    frags: dict[int, list[_Row]],
    paths: dict[int, list[_Path]],
    skip: Collection[int],
    guard: _PageGuard,
    *,
    document: _Document,
) -> _MarginFilter:
    """What repeats on the pages. A page that raises (`guard`) is left out of the count, as
    if it was not a reference page: the statistic is the others'."""
    reference = _reference_pages(doc.page_count, selected, skip)
    counts: Counter[tuple] = Counter()
    texts: dict[tuple[str, str], set[str]] = {}
    sizes: Counter[float] = Counter()
    families: Counter[str] = Counter()
    # Every row in a band, with its page, zone, height and size.
    band_rows: list[tuple[int, str, float, float, _Row]] = []
    on_side: Counter[int] = Counter()  # counted pages of each parity
    counted = 0
    for number in reference:
        with guard(number):
            page = doc[number]
            if number not in frags:
                page_type.turn_page(page)  # a reference page is read upright too
                take_form_fields(page)  # what a field draws is not a margin's text
                frags[number] = _page_rows(page)
            if number not in paths:
                paths[number] = _page_paths(page)
            height = page.rect.height
            seen: set[tuple] = set()
            for row in frags[number]:
                if row.vertical:
                    seen.add(("rotated", _rotated_key(row)))
                    continue
                place_zone = _box_zone(row.bbox, height, MARGIN_PLACE_BAND)
                if place_zone:
                    band_rows.append((number, place_zone, (row.y0 + row.y1) / 2, row.size, row))
                zone = _margin_zone(row, height)
                if zone is None:
                    for c in row.ink:
                        sizes[round(c.size * 2) / 2] += 1
                    families[row.family] += len(row.ink)
                    continue
                key = (zone, _norm_margin(row.clean))
                seen.add(("text", key))
                texts.setdefault(key, set()).add(row.clean)
            seen.update(
                ("figure", key) for key in _band_figures(page, paths[number], document=document)
            )
            seen.update(("mark", key) for key in _band_marks(page, paths[number]))
            seen.update(("picture", xref) for xref in _page_pictures(page) if xref)
            counts.update(seen)
            counted += 1
            on_side[number % 2] += 1
    needed = max(2, -(-counted // 2))  # at least half of the pages, and two
    repeated: dict[str, set] = {
        "text": set(),
        "rotated": set(),
        "figure": set(),
        "mark": set(),
        "picture": set(),
    }
    for (kind, key), n in counts.items():
        if n >= needed:
            repeated[kind].add(key)
    body = max(sizes.items(), key=lambda kv: (kv[1], -kv[0]))[0] if sizes else 10.0
    body_family = max(families.items(), key=lambda kv: (kv[1], kv[0]))[0] if families else ""
    result = _MarginFilter(
        repeated=repeated["text"],
        fixed={key for key in repeated["text"] if len(texts[key]) == 1},
        rotated=repeated["rotated"],
        figures=repeated["figure"],
        body=max(body, 1.0),
        marks=repeated["mark"],
        backgrounds=repeated["picture"],
        body_family=body_family,
        pages=counted,
    )
    result.places = _repeated_places(band_rows, result, on_side)
    return result


@dataclass(frozen=True)
class _Place:
    """A height in a band where rows of one size stand on the pages of one side."""

    zone: str
    parity: int
    y: float
    size: float
    numbered: bool = False  # its line carries the running page number
    texts: frozenset[str] = frozenset()  # words (`_norm_margin`) that stand there on 2+ pages

    def holds(self, zone: str, number: int, row: _Row) -> bool:
        return (
            zone == self.zone
            and number % 2 == self.parity
            and abs((row.y0 + row.y1) / 2 - self.y) <= MARGIN_PLACE_PT
            and abs(row.size - self.size) <= MARGIN_SIZE_PT
        )


_WHOLE_NUMBER = re.compile(r"(?<![\w.])\d+(?!\w|\.\d)")


def _opens_with_mark(row: _Row) -> bool:
    """The row opens with a character set smaller than the rest (a footnote's mark)."""
    ink = row.ink
    return len(ink) > 1 and ink[0].size <= NOTE_MARK_RATIO * row.size


def _repeated_places(
    band_rows: Sequence[tuple[int, str, float, float, _Row]],
    margins: _MarginFilter,
    on_side: Counter[int],
) -> list[_Place]:
    """The places in a band where rows that could be a head or foot stand on at least half
    of the reference pages of one side (odd or even) and at least two, and where the line is
    one by more than that: a number on it counts up with the pages (the page number), or
    the same words (with letters in them) stand there on two pages. The head of
    a left page and the head of a right page are different places."""
    candidates = [
        (number, zone, row) for number, zone, _, _, row in band_rows if margins.furniture_look(row)
    ]
    places: list[_Place] = []
    for number, zone, row in candidates:
        if any(place.holds(zone, number, row) for place in places):
            continue
        here = _Place(zone, number % 2, (row.y0 + row.y1) / 2, row.size)
        rows: dict[int, list[_Row]] = {}
        for n, z, other in candidates:
            if here.holds(z, n, other):
                rows.setdefault(n, []).append(other)
        if len(rows) < max(2, -(-on_side[here.parity] // 2)):
            continue
        # A page number goes up by the pages between: from one page of the place to the
        # next, on half of those steps (an excerpt put together from parts of a book jumps
        # at each join, so one offset for the whole file is too much to ask).
        numbers = {
            n: {int(v) for r in on_page for v in _WHOLE_NUMBER.findall(r.clean)}
            for n, on_page in sorted(rows.items())
        }
        steps = list(itertools.pairwise(numbers))
        counting = sum(any(v + m - n in numbers[m] for v in numbers[n]) for n, m in steps)
        numbered = counting >= max(1, -(-len(steps) // 2))
        seen = Counter(
            text
            for on_page in rows.values()
            for text in {_norm_margin(r.clean) for r in on_page}
            if sum(c.isalpha() for c in text) >= 3
        )
        texts = frozenset(text for text, n in seen.items() if n >= 2)
        if numbered or texts:
            places.append(replace(here, numbered=numbered, texts=texts))
    return places
