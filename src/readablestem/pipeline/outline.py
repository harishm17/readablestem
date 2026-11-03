"""The PDF's own outline (bookmarks) as the first signal for headings and their levels.

A PDF made from Word, LaTeX with hyperref, InDesign or a browser's print often carries an
outline: titles with levels and a page each (`Document.get_toc`). The author or the tool
wrote it from the document's real structure, so where it agrees with the page text it says
more than font sizes can (docs/layout.md, "The PDF's outline"):

- a heading that layout found, whose text is a bookmark's title on that bookmark's page,
  has the bookmark's level (`REASON_OUTLINE`, "outline heading h2"); the heading step treats
  it like a tagged heading (`layout.tagged_level`), and a real tag wins over it;
- a paragraph that is a bookmark's title becomes a heading only if it looks like one
  (`_promotion`): short, no sentence end, and either bold or set larger than the body
  text (`REASON_OUTLINE`, as above) or set off by space above it (`REASON_OUTLINE_WEAK`,
  "outline candidate h2": the bookmark's level, but weak evidence, so an optional note
  like any bold body-size line). A tool that bookmarks every short paragraph does not turn
  them all into headings: if more than `MAX_PROMOTED_SHARE` of the document's paragraphs
  (and more than `MIN_PROMOTED` of them) would be promoted, none is;
- the heading's text becomes the bookmark's title when the two differ only in spacing,
  case, punctuation or the letter styles of maths (a math-italic letter in the page text);
- no bookmark is used unless the outline is *consistent* with the pages: at least
  `MIN_MATCH_SHARE` of the bookmarks that point into the pages analysed name a line of
  that page. An outline of another edition of the document, or one made by a tool that
  numbers every page, is ignored whole;
- an outline with one level only is ignored when the headings it names are set in
  different sizes: the sizes are the better evidence of depth then.

Bookmarks never touch a line that is not a plain title: a paragraph with maths, a list
item, code, a glossary entry or a line over 200 characters stays what it is. Reading never
fails a conversion: an outline that cannot be read gives none.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass

import pymupdf

from readablestem.core.types import INLINE_MARKER_RE, Element, Kind

REASON_OUTLINE = "outline heading h"
REASON_OUTLINE_WEAK = "outline candidate h"
MAX_PROMOTED_SHARE = 0.3  # of the paragraphs a document has
MIN_PROMOTED = 2  # promotions that are always allowed, whatever the share
MAX_PROMOTED_WORDS = 12
MAX_PROMOTED_CHARS = 100
LARGER = 1.1  # x the body size: set larger
GAP_ABOVE = 0.8  # x the line's own height of space above, for a plain line
_NOT_A_TITLE_END = ".?!:;,"
MIN_MATCH_SHARE = 0.6  # of the bookmarks into the analysed pages that must name a line
MAX_ENTRIES = 5000  # a hostile outline is not walked
MAX_TITLE_CHARS = 200
_NUMBER_PREFIX = re.compile(r"^(?:chapter|section|part|unit|appendix|lecture|module)?[0-9]{1,3}$")


@dataclass(frozen=True)
class Bookmark:
    level: int
    title: str
    page: int  # 0-based
    key: str


def key_of(text: str) -> str:
    """Letters and digits of `text`, case-folded and in compatibility form (a math-italic
    letter is its plain letter)."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in folded if c.isalnum())


def read(doc: pymupdf.Document) -> list[tuple[int, str, int]]:
    """The document's outline as (level, title, 1-based page), or [] when there is none or
    it cannot be read."""
    try:
        raw = doc.get_toc(simple=True)
    except Exception:  # damaged outline: the conversion goes on without it
        return []
    out: list[tuple[int, str, int]] = []
    for item in raw[:MAX_ENTRIES]:
        try:
            level, title, page = item[0], item[1], item[2]
        except (TypeError, IndexError):
            continue
        if isinstance(level, int) and isinstance(title, str) and isinstance(page, int):
            out.append((level, title, page))
    return out


def _bookmarks(toc: Sequence[tuple[int, str, int]], pages: Collection[int]) -> list[Bookmark]:
    wanted = set(pages)
    out = []
    for level, title, page in toc:
        key = key_of(title)
        if key and page - 1 in wanted and len(title) <= MAX_TITLE_CHARS:
            out.append(Bookmark(max(1, min(6, level)), " ".join(title.split()), page - 1, key))
    return out


def _same_title(text_key: str, key: str) -> bool:
    """The line is the bookmark's title, with or without a section number the other lacks
    ("3 Forces" and "Forces")."""
    if text_key == key:
        return True
    longer, shorter = (text_key, key) if len(text_key) > len(key) else (key, text_key)
    if len(shorter) < 3 or not longer.endswith(shorter):
        return False
    return _NUMBER_PREFIX.match(longer[: len(longer) - len(shorter)]) is not None


def _plain_line(element: Element) -> bool:
    """A heading, or a paragraph that is only a short plain title: no list marker, no code,
    no glossary term, no inline maths."""
    if element.kind == Kind.HEADING:
        return bool(element.text)
    return (
        element.kind == Kind.PARAGRAPH
        and bool(element.text)
        and len(element.text or "") <= MAX_TITLE_CHARS
        and element.list_marker is None
        and not element.preformatted
        and element.term_end is None
        and element.parent_id is None
        and not INLINE_MARKER_RE.search(element.text or "")
    )


def _match(elements: Sequence[Element], marks: Sequence[Bookmark]) -> list[Element | None]:
    by_page: dict[int, list[Element]] = {}
    for element in sorted(elements, key=lambda e: (e.kind != Kind.HEADING, e.order)):
        if _plain_line(element):
            by_page.setdefault(element.page, []).append(element)
    taken: set[str] = set()
    found: list[Element | None] = []
    for mark in marks:
        element = next(
            (
                e
                for e in by_page.get(mark.page, [])
                if e.id not in taken and _same_title(key_of(e.text or ""), mark.key)
            ),
            None,
        )
        if element is not None:
            taken.add(element.id)
        found.append(element)
    return found


def apply(
    elements: list[Element], toc: Sequence[tuple[int, str, int]], pages: Collection[int]
) -> list[Element]:
    """The elements with the outline put on them (see the module docstring); the same list
    when the outline is missing, does not match the pages or is flat where the type is not."""
    marks = _bookmarks(toc, pages)
    if not marks:
        return elements
    found = _match(elements, marks)
    pairs = [(m, e) for m, e in zip(marks, found, strict=True) if e is not None]
    if len(pairs) < max(1, math.ceil(MIN_MATCH_SHARE * len(marks))):
        return elements
    if len({m.level for m, _ in pairs}) == 1:
        sizes = {round((e.font_size or 0.0) * 2) for _, e in pairs if e.kind == Kind.HEADING}
        if len(sizes) > 1:
            return elements
    plan: list[tuple[Bookmark, Element, str]] = []
    context = _Context(elements)
    for mark, element in pairs:
        if element.kind == Kind.HEADING:
            plan.append((mark, element, REASON_OUTLINE))
        elif (reason := _promotion(element, context)) is not None:
            plan.append((mark, element, reason))
    promoted = sum(e.kind == Kind.PARAGRAPH for _, e, _ in plan)
    if promoted > max(MIN_PROMOTED, MAX_PROMOTED_SHARE * context.paragraphs):
        plan = [(m, e, r) for m, e, r in plan if e.kind == Kind.HEADING]
    updates: dict[str, dict] = {}
    for mark, element, reason in plan:
        update: dict = {"reasons": [*element.reasons, f"{reason}{mark.level}"]}
        if element.kind == Kind.PARAGRAPH:
            update["kind"] = Kind.HEADING
        if (
            element.text != mark.title
            and not element.script_runs
            and key_of(element.text or "") == mark.key
        ):
            update["text"] = mark.title
        updates[element.id] = update
    return [e.model_copy(update=updates[e.id]) if e.id in updates else e for e in elements]


def weak_level(element: Element) -> int | None:
    """The level of a weak outline promotion (`REASON_OUTLINE_WEAK`), else None."""
    for reason in element.reasons:
        if reason.startswith(REASON_OUTLINE_WEAK) and reason[len(REASON_OUTLINE_WEAK) :].isdigit():
            return int(reason[len(REASON_OUTLINE_WEAK) :])
    return None


class _Context:
    """What a paragraph is set against: the body size, the line above it, the paragraph count."""

    def __init__(self, elements: Sequence[Element]) -> None:
        weights: Counter[float] = Counter()
        self.paragraphs = 0
        self.above: dict[str, Element | None] = {}
        last: dict[int, Element] = {}
        for element in sorted(elements, key=lambda e: e.order):
            if element.parent_id is not None:
                continue
            self.above[element.id] = last.get(element.page)
            last[element.page] = element
            if element.kind == Kind.PARAGRAPH and element.text:
                self.paragraphs += 1
                if element.font_size:
                    weights[round(element.font_size * 2) / 2] += len(element.text)
        self.body = max(weights.items(), key=lambda kv: (kv[1], kv[0]))[0] if weights else 0.0


def _promotion(element: Element, context: _Context) -> str | None:
    """How a paragraph that is a bookmark's title may become a heading: `REASON_OUTLINE`
    (short and bold or larger than the body text), `REASON_OUTLINE_WEAK` (short and set off by
    space above), or None (it stays a paragraph)."""
    text = (element.text or "").strip()
    if (
        not text
        or len(text) > MAX_PROMOTED_CHARS
        or len(text.split()) > MAX_PROMOTED_WORDS
        or text[-1] in _NOT_A_TITLE_END
    ):
        return None
    size = element.font_size or 0.0
    if element.bold or (context.body and size >= LARGER * context.body):
        return REASON_OUTLINE
    above = context.above.get(element.id)
    height = element.bbox[3] - element.bbox[1]
    if above is None or above.page != element.page:
        return REASON_OUTLINE_WEAK
    if element.bbox[1] - above.bbox[3] >= GAP_ABOVE * height:
        return REASON_OUTLINE_WEAK
    return None
