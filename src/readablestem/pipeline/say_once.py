"""The say-once measure (plan A17): for each page of a converted document, which of its words
are said by no element and which by two or more.

No model, no second read of the PDF. Layout already holds what the page says (`PageSource`:
the words of the rows it set out to read, each with the box of its glyphs, made while the
rows are prepared) and the conversion holds what each element says (`Element.text`, `bbox`).
This module only counts. It is the function SW60 runs at the end of a conversion, in shadow,
and the one WS4.11 later turns into a check of the product.

**Who owns a word.** Two kinds of owner, and the kind is the element's:

- by place: an equation, a table or a figure replaces the page's glyphs with a reading, so
  its characters are not in its text. A word whose centre is inside the box of such an
  element is its (`PLACE_KINDS`). A child inside its parent (a formula in a table cell or in
  a caption: `Element.parent_id`) is the same owner, so it is not counted twice;
- by words: a paragraph, a heading and a list item say what their text says. The words of
  the page that no place holds are matched, one for one, with the words of those elements
  (a bag: order is not read, so a block read in the wrong order is not seen, and nothing
  here says it is). A figure's caption and an equation's printed number are words too.

**What is counted** (`CODES`, each in words and in glyphs):

| code | a word that is |
|---|---|
| `no_owner` | in no place and not said by any element |
| `two_places` | inside the boxes of two elements that are not parent and child |
| `place_and_text` | inside an element's box and also said again by a text element |
| `said_twice` | in an element's text one time more than the page has it |

`not_on_page` (words an element has that the page's text layer does not) is counted but is
not a fault of this measure: a reading may spell a thing another way, and a page read from a
picture has no text layer to compare with.

**What is not counted** (`Left out` by name, never a fault): the running heads and feet, page
numbers and watermarks the margin filter drops (`furniture`) and the text nobody can see
(`hidden`, `page_type`). They are made by layout's own rules and only counted here. A page
kept whole as its picture has its own decision and is not measured.

**Normalizing.** Both sides are folded the same way: Unicode compatibility form (a ligature
is its letters, a superscript digit is the digit), case folded, combining marks dropped, and
cut into runs of letters and digits. A word broken at the end of a line ("veloc-" / "ity") is
matched when the two halves, joined, are a word the elements have (`_unsaid`). A
paragraph that goes on over a page break is one element with the page of its first part
(`Element.last_page`), so its words are shared by the pages it covers.

Content-free: counts only (`PageMeasure`); the words never leave this module's arguments.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from readablestem.core.types import INLINE_MARKER_RE, BBox, Element, Kind

CODES = ("no_owner", "two_places", "place_and_text", "said_twice")
INFO_CODES = ("not_on_page",)
# Elements that stand in place of the glyphs under them.
PLACE_KINDS = frozenset({Kind.EQUATION, Kind.TABLE, Kind.FIGURE})
# Elements that say words.
TEXT_KINDS = frozenset({Kind.PARAGRAPH, Kind.HEADING, Kind.LIST_ITEM})
# pt: elements' boxes are rounded to whole points, so a glyph at an edge is inside.
SLACK = 1.0
# The line the shadow notice uses (SW60), measured in `docs/eval-say-once.md`: a page with at
# least MIN_WORDS words of which at least LINE are in no element.
LINE = 0.15
MIN_WORDS = 40
HYPHENS = frozenset(["-", chr(0x2010), chr(0xAD)])  # hyphen-minus, hyphen, soft hyphen
NOTICE_CODE = "page_missing"  # the shadow step's name; the code of WS15.9's writer later


@dataclass(frozen=True, slots=True)
class Word:
    """A run of letters and digits of one row, folded (`fold`)."""

    text: str
    box: BBox
    glyphs: int
    hyphen: bool = False  # the row ends with a hyphen right after it: the next row finishes it
    start: bool = False  # the first word of its row


@dataclass
class PageSource:
    """What layout read of one page that has to be said: its words, and the glyphs it left out
    by rule, by reason (`furniture`: running heads, feet, page numbers, watermarks; `hidden`:
    text nobody can see)."""

    words: list[Word] = field(default_factory=list)
    left_out: dict[str, int] = field(default_factory=dict)

    def add_row(self, chars: Iterable[Any]) -> None:
        """Set down the words of one row of layout's characters (`_Char`: `c` and its box)."""
        self.words.extend(row_words((c.c, (c.x0, c.y0, c.x1, c.y1)) for c in chars))

    def leave_out(self, reason: str, glyphs: int) -> None:
        if glyphs:
            self.left_out[reason] = self.left_out.get(reason, 0) + glyphs

    def leave_out_at(self, reason: str, boxes: Sequence[BBox]) -> None:
        """Take the words whose centre is on one of `boxes` out of what is to be said and count
        their glyphs as left out for `reason`: a rule of layout cut them later than the page's
        words were set down (the leaders and page numbers of a contents line)."""
        kept = []
        for word in self.words:
            if any(_inside(word.box, box) for box in boxes):
                self.leave_out(reason, word.glyphs)
            else:
                kept.append(word)
        self.words = kept


@dataclass(frozen=True)
class PageMeasure:
    """The counts of one page. Words and glyphs by code, and what was left out by name."""

    page: int  # 0-based
    words: int  # words of the page that had to be said
    glyphs: int
    by_code: Mapping[str, tuple[int, int]]  # code -> (words, glyphs)
    left_out: Mapping[str, int]  # reason -> glyphs

    def count(self, code: str) -> int:
        return self.by_code.get(code, (0, 0))[0]

    @property
    def missing_share(self) -> float:
        """The share of the page's words said by no element."""
        return self.count("no_owner") / self.words if self.words else 0.0

    @property
    def flagged(self) -> bool:
        """A page the shadow notice names (`LINE`, `MIN_WORDS`)."""
        return self.words >= MIN_WORDS and self.missing_share >= LINE

    def record(self) -> dict[str, object]:
        return {
            "page": self.page,
            "words": self.words,
            "glyphs": self.glyphs,
            "codes": {code: list(counts) for code, counts in sorted(self.by_code.items())},
            "left_out": dict(sorted(self.left_out.items())),
        }


# --------------------------------------------------------------------------- folding


def _fold_char(c: str) -> str:
    folded = unicodedata.normalize("NFKC", c).casefold()
    return "".join(ch for ch in folded if unicodedata.category(ch) not in ("Mn", "Cf"))


def _breaks(run: Sequence[str], ch: str) -> bool:
    """A digit next to a letter starts a new word: a note's mark is raised against the word it
    marks ("1who"), and a text that spaces it says "1 who"."""
    return bool(run) and run[-1].isdigit() != ch.isdigit()


def fold(text: str) -> list[str]:
    """The words of `text`, folded: runs of letters, and runs of digits."""
    words: list[str] = []
    run: list[str] = []
    for c in text:
        for ch in _fold_char(c):
            if not ch.isalnum():
                if run:
                    words.append("".join(run))
                    run = []
                continue
            if _breaks(run, ch):
                words.append("".join(run))
                run = []
            run.append(ch)
    if run:
        words.append("".join(run))
    return words


def row_words(chars: Iterable[tuple[str, BBox]]) -> list[Word]:
    """The words of one row, from its characters left to right and each one's box."""
    words: list[Word] = []
    run: list[str] = []
    boxes: list[BBox] = []
    glyphs = 0
    last: str = ""  # the last character that is not a space

    def flush() -> None:
        nonlocal run, boxes, glyphs
        if run:
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            words.append(Word("".join(run), (x0, y0, x1, y1), glyphs, start=not words))
        run, boxes, glyphs = [], [], 0

    for c, box in chars:
        if not c.isspace():
            last = c
        folded = _fold_char(c)
        if any(ch.isalnum() for ch in folded):
            if _breaks(run, folded[0]):
                flush()
            run.extend(ch for ch in folded if ch.isalnum())
            boxes.append(box)
            glyphs += 1
        elif unicodedata.category(c) not in ("Mn", "Cf"):
            flush()
    flush()
    if words and last in HYPHENS:
        # (a hyphen right after the last word; "x - " with a space is a minus, close enough)
        w = words[-1]
        words[-1] = Word(w.text, w.box, w.glyphs, hyphen=True, start=w.start)
    return words


# --------------------------------------------------------------------------- owners


def _inside(box: BBox, outer: BBox) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] - SLACK <= cx <= outer[2] + SLACK and outer[1] - SLACK <= cy <= outer[3] + SLACK


def _bag(element: Element) -> Counter[str]:
    """The words an element says by its text."""
    said: list[str] = []
    if element.kind in TEXT_KINDS:
        said += fold(INLINE_MARKER_RE.sub(" ", element.text or ""))
        said += fold(element.list_marker or "")
    elif element.kind is Kind.TABLE:
        said += fold(
            element.caption or ""
        )  # ("Table N: ...": its row is set down apart from the grid)
    elif element.kind is Kind.FIGURE:
        # (`caption_marked` has a marker where each formula was; the plain caption says the
        # formula's words too, and the equation inside the caption owns them by place)
        said += fold(INLINE_MARKER_RE.sub(" ", element.caption_marked or element.caption or ""))
    elif element.kind is Kind.EQUATION and element.display != "inline":
        said += fold(element.equation_label or "")
    return Counter(said)


def _owners(box: BBox, places: Sequence[Element]) -> int:
    """How many places hold a word: a child inside its parent is the parent's."""
    held = [p for p in places if _inside(box, p.bbox)]
    parents = {p.parent_id for p in held if p.parent_id}
    return sum(1 for p in held if p.id not in parents)


def _take(word: str, *bags: Counter[str]) -> bool:
    for bag in bags:
        if bag[word] > 0:
            bag[word] -= 1
            return True
    return False


def _unsaid(free: list[Word], bags: Sequence[Counter[str]]) -> list[Word]:
    """The words of `free` that no bag says, each said by at most one word. A word broken at
    a row end (the first half has the hyphen, the second begins a row) is looked for whole
    first: matched one half at a time, "in-" would be said by any "in" of the page and the
    other half would be left over."""
    joined: set[int] = set()
    starts = [w for w in free if w.start]
    for first in [w for w in free if w.hyphen]:
        near = sorted(
            (w for w in starts if w is not first and id(w) not in joined),
            key=lambda w: abs(w.box[1] - first.box[3]) + 0.1 * abs(w.box[0] - first.box[0]),
        )
        for second in near:
            if _take(first.text + second.text, *bags):
                joined |= {id(first), id(second)}
                break
    return [w for w in free if id(w) not in joined and not _take(w.text, *bags)]


def measure(
    sources: Mapping[int, PageSource],
    elements: Sequence[Element],
    *,
    skip: Collection[int] = (),
) -> dict[int, PageMeasure]:
    """The measure of each page of `sources` that is not in `skip` (pages kept whole as
    their picture), against `elements`, the document's elements at the end of the conversion."""
    on_page: dict[int, list[Element]] = {}
    for e in elements:
        on_page.setdefault(e.page, []).append(e)
    carried: list[tuple[int, Counter[str]]] = []  # paragraphs that go on over a page break
    made: dict[int, PageMeasure] = {}
    for number in sorted(set(sources) | set(on_page)):
        here = on_page.get(number, [])
        bag: Counter[str] = Counter()
        for e in here:
            if e.last_page is not None and e.last_page > number:
                carried.append((e.last_page, _bag(e)))
            else:
                bag += _bag(e)
        source = sources.get(number)
        if source is not None and number not in skip:
            places = [e for e in here if e.kind in PLACE_KINDS]
            counted = _page(number, source, places, [bag, *(pool for _, pool in carried)])
            # what the page's bag still has is the page's own excess; a carried bag's is
            # the excess of the page it ends on
            excess = bag
            for last, pool in carried:
                if last <= number:
                    excess = excess + pool
            made[number] = _with_excess(counted, source, excess)
        carried = [(last, pool) for last, pool in carried if last > number]
    return made


def _page(
    number: int, source: PageSource, places: Sequence[Element], bags: list[Counter[str]]
) -> PageMeasure:
    held: list[tuple[Word, int]] = [(w, _owners(w.box, places)) for w in source.words]
    free = [w for w, n in held if n == 0]
    unowned = _unsaid(free, bags)
    counts: dict[str, list[int]] = {code: [0, 0] for code in CODES}
    for w in unowned:
        counts["no_owner"][0] += 1
        counts["no_owner"][1] += w.glyphs
    for w, n in held:
        if n >= 2:
            counts["two_places"][0] += 1
            counts["two_places"][1] += w.glyphs
        if n >= 1 and _take(w.text, *bags):
            counts["place_and_text"][0] += 1
            counts["place_and_text"][1] += w.glyphs
    return PageMeasure(
        number,
        len(source.words),
        sum(w.glyphs for w in source.words),
        {code: (c[0], c[1]) for code, c in counts.items()},
        dict(source.left_out),
    )


def _with_excess(made: PageMeasure, source: PageSource, excess: Counter[str]) -> PageMeasure:
    """`made` with the words the elements say more often than the page has them: said twice
    where the page has the word, not on the page where it does not."""
    on_page = {w.text for w in source.words}
    by_code = dict(made.by_code)
    twice = [0, 0]
    absent = [0, 0]
    for word, n in excess.items():
        if n <= 0:
            continue
        target = twice if word in on_page else absent
        target[0] += n
        target[1] += n * len(word)
    by_code["said_twice"] = (twice[0], twice[1])
    by_code["not_on_page"] = (absent[0], absent[1])
    return PageMeasure(made.page, made.words, made.glyphs, by_code, made.left_out)


def over_line(made: Mapping[int, PageMeasure]) -> dict[int, tuple[float, bool]]:
    """What the shadow step keeps of each page: its share of words in no element and whether
    the page is over the line (`Shadow.over_line`). The words and the codes stay here."""
    return {number: (page.missing_share, page.flagged) for number, page in made.items()}
