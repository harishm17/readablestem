"""PDF -> document layout: elements in reading order. Deterministic; no model calls.

How a page is read (thresholds and known failure modes: docs/layout.md):

1. Characters come from PyMuPDF's raw text dictionary with accurate glyph boxes, so the
   numerator and denominator rows of a fraction do not overlap. All geometry is turned
   into display coordinates (the page's /Rotate applied), the space `crop` clips in.
2. Running headers/footers (text in the top/bottom 7 % of the page that repeats on at
   least half of the pages, digits normalised) and bare page numbers are dropped.
3. Columns: a vertical white gap in the x-coverage of narrow lines splits a page into
   two columns. Lines that cross it, and narrow lines with nothing beside them in the
   other column, are full-width and split the reading flow.
4. PyMuPDF lines on one baseline are merged into rows (it splits justified text and
   inline maths into several "lines"). Rows are put in reading order and grouped into
   runs: paragraphs, headings, display equations, captions. PyMuPDF's own blocks are not
   trusted: they glue captions onto the next paragraph and a fraction's denominator onto
   the text below it.
5. Equations: rows that score as maths (`parse.equation_score`) and contain no prose word
   are display equations; vertically adjacent maths rows and short rows tied together by
   a vector fraction bar or radical become one region. Inside paragraphs, runs of maths
   characters (maths fonts, math-italic letters, Greek, maths symbols) or an italic single
   letter next to an operator become inline equations, and the paragraph text gets
   `core.types.inline_marker(id)` in their place.
6. Figures: raster images and clusters of vector drawings, minus equation rules, text
   underlines, page borders and boxes or tables drawn around text. Short labels inside or
   right next to a figure belong to it; a "Figure N" caption within reach is attached.

Thread safety: PyMuPDF is not thread-safe, so every PyMuPDF call here runs under one
module lock. `crop` keeps a small cache of opened documents keyed by the PDF bytes; call
`release(pdf_bytes)` when a document is finished so its content does not stay in memory.
"""

from __future__ import annotations

import hashlib
import re
import statistics
import threading
import unicodedata
from collections import Counter, OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from itertools import count, pairwise

import pymupdf

from a11y_stem.core.types import BBox, Element, Kind, inline_marker
from a11y_stem.pipeline.parse import (
    EQUATION_THRESHOLD,
    GREEK,
    MATH_FONT,
    RELATIONS,
    equation_score,
    is_math_alphanumeric,
)

# ----------------------------------------------------------------------------- thresholds
# Sizes are PDF points unless the name says otherwise. docs/layout.md explains each one.

HEADING_SIZE_RATIO = 1.15  # a short run at least this much larger than body text
HEADING_LEVEL_TOLERANCE = 0.05  # heading sizes within 5 % of each other share a level
MAX_HEADING_CHARS = 200
MAX_HEADING_ROWS = 3
MAX_BOLD_HEADING_CHARS = 120
MAX_BOLD_HEADING_ROWS = 2
MARGIN_BAND = 0.07  # top/bottom share of the page searched for running headers/footers
MARGIN_REFERENCE_PAGES = 24  # pages sampled to decide what repeats
MIN_GUTTER = 8.0  # narrowest white gap between two text columns
MIN_IMAGE_PX = 24  # raster images with a side smaller than this (pixels) are decoration
MIN_IMAGE_PT = 12.0  # ... and so are images drawn smaller than this on the page
FULL_PAGE_FRACTION = 0.85  # images covering this share of the page: background or scan
MIN_DRAWING_PT = 24.0  # a vector figure is at least this big on both sides
DRAWING_CLUSTER_GAP = 6.0  # drawings closer than this belong to the same figure
RULE_THICKNESS = 1.6  # a fraction bar or table rule is at most this thick
CAPTION_MAX_GAP = 36.0  # furthest a "Figure N" caption may sit from its figure
SCRIPT_RATIO = 0.85  # characters smaller than this x the row size are sub/superscripts
DOCUMENT_CACHE_SIZE = 4

TEXT_FLAGS = (
    pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP | pymupdf.TEXT_ACCURATE_BBOXES
)

# Evidence codes on heading elements (`Element.reasons`). Content-free; heading_step reads
# them to decide confidence and review status.
REASON_LARGER_FONT = "larger font"
REASON_BOLD_BODY = "bold body-size line"
REASON_NUMBERED = "numbered section"
REASON_NUMBERED_LINE = "standalone numbered line"
REASON_LIST_NUMBER = "list-style number"

# "2.3 Speed", "Chapter 2: Motion", "IV. Results", "A) Units". Letters and roman numerals
# need a separator or a prefix ("A ball rolls" is not numbered).
SECTION_NUMBER = re.compile(
    r"^(?P<prefix>(?:Chapter|CHAPTER|Section|SECTION|Unit|UNIT|Part|PART|Lecture|LECTURE"
    r"|Module|MODULE|Appendix|APPENDIX)\s+)?"
    r"(?P<num>\d{1,3}(?:\.\d{1,3}){0,4}|[IVX]{1,5}|[A-Z])(?P<sep>[.:)]?)(?:\s+|$)"
)
# "Figure 2.3 Velocity...", "Fig. 4: ...", but not "Figure 2 shows that ...".
CAPTION = re.compile(
    r"^(?:Figure|FIGURE|Fig\.|FIG\.)\s*\d+[A-Za-z]?(?:[.\-\u2013]\d+[A-Za-z]?)*"
    r"(?:\s*[.:|\u2013\u2014-]\s*|\s+(?=[A-Z(\"'\u201c])|\s*$)"
)
PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|of)\s*\d{1,4})?$", re.IGNORECASE
)
EQUATION_NUMBER = re.compile(r"^\(\s*[\dA-Za-z]{1,3}(?:[.\-]\d{1,3})*[a-z]?\s*\)$")
_BULLET = re.compile(
    r"^(?:[\u2022\u00b7\u25e6\u25aa\u25cf\u2023\u2043\u2219*]\s|\(?\d{1,2}[.)]\s|\([a-z]\)\s"
    r"|[a-z]\)\s|\([ivx]{1,4}\)\s)"
)
_LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$")
_JUNK_TITLE = re.compile(
    r"^(?:untitled|document\s*\d*$|microsoft\s+(?:word|powerpoint)|powerpoint presentation"
    r"|slide\s*\d+$|title$|no title$)|\.(?:pdf|docx?|pptx?|tex|odt|rtf|txt|html?|key)$",
    re.IGNORECASE,
)
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))

# Character classes for maths detection. Symbols that only occur in maths (signs such as
# the minus are in _SIGNS: in a text font they also print dashes and option flags):
_STRONG_MATH = frozenset(
    "\u2211\u220f\u222b\u222e\u221a\u2202\u2207\u221e"  # sum prod int oint sqrt partial nabla inf
    "\u2264\u2265\u2260\u2248\u2261\u221d"  # <= >= != approx equiv proportional
    "\u2192\u2190\u2194\u21d2\u21d4"  # arrows
    "\u22c5\u00d7\u00f7"  # cdot times div
    "\u2208\u2209\u2282\u2286\u2283\u2287\u222a\u2229\u2200\u2203"  # sets and quantifiers
    "\u2032\u2033\u2220\u22a5\u2225\u27e8\u27e9"  # primes angle perp parallel brackets
)
_OPERATORS = frozenset("+-*/=<>^_|~")
_SIGNS = frozenset("\u2212\u00b1\u2213")  # minus, plus-minus, minus-plus
_BRACKETS = frozenset("()[]{}")
_OPENING = frozenset("([{")
_SCRIPT_DIGITS = frozenset(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"  # superscript 0-9
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"  # subscript 0-9
)
# Upright words that still belong to maths (functions, units) and English two-letter words.
_MATH_WORDS = frozenset(
    (
        *("sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh"),
        *("arcsin", "arccos", "arctan", "log", "ln", "lg", "exp", "lim", "max", "min"),
        *("sup", "inf", "det", "dim", "ker", "arg", "deg", "gcd", "lcm", "mod", "var"),
        *("cov", "const", "sgn", "tr", "mol", "rad"),
    )
)
_PROSE_TWO_LETTER = frozenset(
    (
        *("is", "so", "if", "to", "of", "in", "at", "on", "by", "as", "or", "an", "be"),
        *("we", "it", "no", "do", "up", "us"),
    )
)
_BOLD_FONT = re.compile(
    r"bold|black|heavy|semibold|demi|cmbx|cmb\d|sfbx|-medi$|\bbd\b|,bd|-bd", re.IGNORECASE
)
_ITALIC_FONT = re.compile(r"italic|oblique|cmti|cmsl|sfti|-it$|,it|ital", re.IGNORECASE)
_MONO_FONT = re.compile(r"mono|courier|consolas|menlo|inconsolata|cmtt|sftt", re.IGNORECASE)
_MARKUP = re.compile(r"</?[A-Za-z][\w:.-]*\s*/?>|</")
_BOLD_FLAG = 16
_ITALIC_FLAG = 2
_SUPERSCRIPT_FLAG = 1
_MONO_FLAG = 8

_LOCK = threading.RLock()  # PyMuPDF is not thread-safe: one PyMuPDF call at a time


class LayoutError(ValueError):
    """The PDF cannot be processed. `code` is a category, safe to log."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        # "unreadable" | "encrypted" | "too_many_pages" | "no_pages" | "page_out_of_range"
        self.code = code


@dataclass
class Layout:
    elements: list[Element]
    page_count: int  # pages in the file
    pages: list[int]  # 0-based pages analysed
    page_sizes: list[tuple[float, float]] = field(default_factory=list)  # per analysed page
    title: str | None = None  # PDF metadata title, else the first level-1 heading
    lang: str = "en"  # PDF /Lang if present, else "en"


# ============================================================================ public API


def analyze(
    pdf_bytes: bytes, *, pages: list[int] | None = None, max_pages: int | None = None
) -> Layout:
    """Elements of the given 0-based pages (all by default), ids "p{page+1}-e{index}".

    `index` counts from 0 per page in reading order; an inline equation comes right after
    the paragraph that contains it. `Element.order` is the position in the whole document.
    Pages are analysed in ascending order (duplicates ignored). Raises `LayoutError`.
    """
    with _LOCK:
        doc = _document(pdf_bytes)
        selected = _select_pages(doc.page_count, pages, max_pages)
        try:
            return _analyze_document(doc, selected)
        except (RuntimeError, pymupdf.mupdf.FzErrorBase) as exc:  # damaged content streams
            raise LayoutError("unreadable", "the PDF could not be parsed") from exc


def crop(
    pdf_bytes: bytes,
    element: Element,
    *,
    dpi: int = 200,
    pad: float = 2.0,
    max_side_px: int = 2048,
) -> bytes:
    """PNG of the element's region (padded), long side capped at `max_side_px`.

    The region is `element.bbox` grown by `pad` points on every side and clipped to the
    page, rendered at `dpi` on a white background, then scaled down if its long side would
    exceed `max_side_px`. Opened documents are cached (see `release`), so cropping every
    element of one PDF parses it once. Safe to call from several threads, but calls are
    serialised by the module lock, so there is nothing to gain from a thread pool here.
    """
    if dpi <= 0 or pad < 0 or max_side_px < 16:
        raise ValueError("crop needs dpi > 0, pad >= 0 and max_side_px >= 16")
    with _LOCK:
        doc = _document(pdf_bytes)
        if not 0 <= element.page < doc.page_count:
            raise ValueError(f"element page {element.page} is not in the PDF")
        page = doc[element.page]
        x0, y0, x1, y1 = element.bbox
        clip = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & page.rect
        if clip.is_empty or clip.width < 0.5 or clip.height < 0.5:
            raise ValueError("element region is empty or outside the page")
        # MuPDF rounds the clip outwards to whole pixels, so leave one pixel of headroom.
        zoom = min(dpi / 72.0, (max_side_px - 1) / max(clip.width, clip.height))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        if max(pixmap.width, pixmap.height) > max_side_px:  # defensive; rounding only
            zoom *= (max_side_px - 1) / max(pixmap.width, pixmap.height)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        return pixmap.tobytes("png")


def release(pdf_bytes: bytes | None = None) -> None:
    """Drop a cached document (or all of them with no argument).

    The cache holds the PDF bytes and the parsed document. The pipeline should release a
    document when its job finishes so user content does not linger in process memory.
    """
    with _LOCK:
        if pdf_bytes is None:
            keys = list(_CACHE)
        else:
            keys = [k for k, v in _CACHE.items() if v.data is pdf_bytes]
            keys = keys or [k for k in [_digest(pdf_bytes)] if k in _CACHE]
        for key in keys:
            _CACHE.pop(key).doc.close()


def heading_levels(elements: Sequence[Element]) -> dict[str, int]:
    """Raw outline level (1-6) per heading element id, before outline repair.

    Sizes of size-based headings are clustered (within `HEADING_LEVEL_TOLERANCE`), the
    largest cluster is level 1. Bold body-size and plain numbered headings sit one level
    below the smallest size cluster; among them, deeper section numbers ("2.3.1" under
    "2.3") go one level deeper per extra number. Used by heading_step and for the title.
    """
    headings = [e for e in elements if e.kind == Kind.HEADING]
    sized = sorted(
        {round(e.font_size or 0.0, 1) for e in headings if REASON_LARGER_FONT in e.reasons},
        reverse=True,
    )
    level_of: dict[float, int] = {}
    level, top = 0, None
    for size in sized:
        if top is None or size < top * (1 - HEADING_LEVEL_TOLERANCE) - 0.05:
            level, top = level + 1, size
        level_of[size] = level
    below = level + 1
    depth = {e.id: _section_depth(e) for e in headings if REASON_LARGER_FONT not in e.reasons}
    shallowest = min((d for d in depth.values() if d), default=1)
    levels: dict[str, int] = {}
    for e in headings:
        if REASON_LARGER_FONT in e.reasons:
            raw = level_of[round(e.font_size or 0.0, 1)]
        else:
            raw = below + max(0, (depth[e.id] or shallowest) - shallowest)
        levels[e.id] = max(1, min(6, raw))
    return levels


def _section_depth(element: Element) -> int | None:
    """Depth of a real section number ("2.3.1" -> 3); list-style "1." does not count."""
    number = section_number(element.text or "")
    return number[0] if number and not number[1] else None


def section_number(text: str) -> tuple[int, bool] | None:
    """(depth, list_like) when `text` starts with a section number, else None.

    "2.3 Speed" -> (2, False); "Chapter 2" -> (1, False); "1. Measure" -> (1, True)
    because "1." also starts list items; "A) Units" -> (1, True).
    """
    match = SECTION_NUMBER.match(text.strip())
    if not match:
        return None
    num, sep, prefix = match["num"], match["sep"], match["prefix"]
    if num[0].isdigit():
        depth = num.count(".") + 1
        return depth, depth == 1 and sep in (".", ")") and not prefix
    if not prefix and not sep:
        return None
    return 1, len(num) == 1 and num not in "IVX" and not prefix


# ============================================================================ documents


@dataclass
class _Cached:
    data: bytes
    doc: pymupdf.Document


_CACHE: OrderedDict[str, _Cached] = OrderedDict()


def _digest(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def _document(pdf_bytes: bytes) -> pymupdf.Document:
    """Opened document for these bytes, from the cache when possible. Hold `_LOCK`.

    Identity is checked first (the pipeline passes the same bytes object to every crop,
    so no hashing is needed); equal bytes from another object are found by SHA-256.
    """
    for key, entry in _CACHE.items():
        if entry.data is pdf_bytes:
            _CACHE.move_to_end(key)
            return entry.doc
    key = _digest(pdf_bytes)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key].doc
    doc = _open(pdf_bytes)
    _CACHE[key] = _Cached(pdf_bytes, doc)
    while len(_CACHE) > DOCUMENT_CACHE_SIZE:
        _CACHE.popitem(last=False)[1].doc.close()
    return doc


def _open(pdf_bytes: bytes) -> pymupdf.Document:
    if not pdf_bytes:
        raise LayoutError("unreadable", "the file is empty")
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # MuPDF raises several types for damaged or non-PDF input
        raise LayoutError("unreadable", "the file could not be opened as a PDF") from exc
    if not doc.is_pdf:  # MuPDF sniffs content and happily opens a PNG as a 1-page document
        doc.close()
        raise LayoutError("unreadable", "the file is not a PDF")
    if doc.needs_pass:
        doc.close()
        raise LayoutError("encrypted", "the PDF needs a password to open")
    return doc


def _select_pages(page_count: int, pages: list[int] | None, max_pages: int | None) -> list[int]:
    if page_count == 0:
        raise LayoutError("no_pages", "the PDF has no pages")
    if pages is None:
        selected = list(range(page_count))
    else:
        selected = sorted(set(pages))
        if not selected:
            raise LayoutError("no_pages", "no pages were requested")
        if selected[0] < 0 or selected[-1] >= page_count:
            raise LayoutError(
                "page_out_of_range", f"page index out of range; the PDF has {page_count} pages"
            )
    if max_pages is not None and len(selected) > max_pages:
        raise LayoutError(
            "too_many_pages", f"{len(selected)} pages requested; the limit is {max_pages}"
        )
    return selected


def _document_lang(doc: pymupdf.Document) -> str:
    try:
        kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
    except Exception:  # malformed catalog: fall back like a missing key
        return "en"
    value = value.strip().lstrip("/")
    return value if kind in ("string", "name") and _LANG.match(value) else "en"


def _meaningful_title(raw: str | None) -> str | None:
    """Metadata title unless it is empty, a file name or a tool default ("Untitled")."""
    title = _clean(raw or "")
    if sum(c.isalpha() for c in title) < 3 or _JUNK_TITLE.search(title):
        return None
    return title[:MAX_HEADING_CHARS]


# ============================================================================ characters


@dataclass(frozen=True, slots=True)
class _Char:
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    font: str
    flags: int

    @property
    def blank(self) -> bool:
        return self.c.isspace()

    @property
    def bold(self) -> bool:
        return bool(self.flags & _BOLD_FLAG) or _font_traits(self.font)[0]

    @property
    def italic(self) -> bool:
        return bool(self.flags & _ITALIC_FLAG) or _font_traits(self.font)[1]

    @property
    def math(self) -> bool:
        """Maths whatever the context: maths font, math-italic letter, Greek, symbol.

        A minus or plus-minus sign in a text font is not enough on its own: groff prints
        command-line options as "\u2212d", and dashes come out as minus signs too.
        """
        c = self.c
        if self.blank:
            return False
        if _font_traits(self.font)[2]:
            return True
        return is_math_alphanumeric(c) or c in _STRONG_MATH or bool(GREEK.match(c))

    @property
    def mono(self) -> bool:
        return bool(self.flags & _MONO_FLAG) or _font_traits(self.font)[3]


@lru_cache(maxsize=1024)
def _font_traits(font: str) -> tuple[bool, bool, bool, bool]:
    """(bold, italic, maths, monospaced) from a font name like "ABCDEF+DejaVuSerif-Bold"."""
    name = font.split("+", 1)[-1]
    return (
        bool(_BOLD_FONT.search(name)),
        bool(_ITALIC_FONT.search(name)),
        bool(MATH_FONT.search(name)),
        bool(_MONO_FONT.search(name)),
    )


def _char_class(ch: _Char) -> str:
    """W space, C code (monospaced), M maths, D digit, O operator, B bracket, L letter,
    P other."""
    c = ch.c
    if ch.blank:
        return "W"
    if ch.math:
        return "M"
    if ch.mono:
        return "C"  # code listings are full of "x = 5"; never maths
    if c.isdigit() or c in _SCRIPT_DIGITS:
        return "D"
    if c in _OPERATORS or c in _SIGNS:
        return "O"
    if c in _BRACKETS:
        return "B"
    if c.isalpha():
        return "L"
    return "P"


def _union(boxes: Iterable[BBox]) -> BBox:
    boxes = list(boxes)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _rounded(box: BBox) -> BBox:
    return (round(box[0], 2), round(box[1], 2), round(box[2], 2), round(box[3], 2))


def _clean(text: str) -> str:
    """Whitespace collapsed, soft hyphens/zero-width/control characters removed."""
    text = text.translate(_INVISIBLE)
    text = "".join(c for c in text if c.isspace() or unicodedata.category(c) != "Cc")
    return " ".join(text.split())


# ============================================================================ rows


@dataclass(eq=False)
class _Row:
    """Characters on one visual line of one column, left to right."""

    chars: list[_Char]
    vertical: bool = False  # rotated text: never merged with other rows
    column: str = "L"  # "L", "R" or "S" (spans both columns / single-column page)
    kind: str = "text"  # "math" | "frag" | "text", see _classify_row
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    absorbed: bool = False  # belongs to a figure (labels, text inside a diagram)

    @cached_property
    def ink(self) -> list[_Char]:
        return [c for c in self.chars if not c.blank] or self.chars

    @cached_property
    def bbox(self) -> BBox:
        return (
            min(c.x0 for c in self.ink),
            min(c.y0 for c in self.ink),
            max(c.x1 for c in self.ink),
            max(c.y1 for c in self.ink),
        )

    @cached_property
    def size(self) -> float:
        """Font size covering most characters."""
        counts = Counter(round(c.size * 2) / 2 for c in self.ink)
        return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @cached_property
    def baseline(self) -> float:
        main = [c.baseline for c in self.ink if abs(c.size - self.size) <= 0.5]
        return statistics.median(main or [c.baseline for c in self.ink])

    @cached_property
    def text(self) -> str:
        return "".join(c.c for c in self.chars)

    @cached_property
    def clean(self) -> str:
        return _clean(self.text)

    @cached_property
    def bold(self) -> bool:
        letters = [c for c in self.ink if c.c.isalnum()]
        return bool(letters) and all(c.bold for c in letters)

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass(frozen=True)
class _Display:
    """Unrotated page coordinates (what PyMuPDF extracts) -> display coordinates.

    Plain arithmetic instead of pymupdf.Rect/Point: a dense plot has thousands of paths,
    and building PyMuPDF objects for each point costs seconds per page.
    """

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float
    rotated: bool

    @classmethod
    def of(cls, page: pymupdf.Page) -> _Display:
        m = page.rotation_matrix
        return cls(m.a, m.b, m.c, m.d, m.e, m.f, page.rotation % 360 != 0)

    def point(self, x: float, y: float) -> tuple[float, float]:
        if not self.rotated:
            return x, y
        return x * self.a + y * self.c + self.e, x * self.b + y * self.d + self.f

    def vector(self, dx: float, dy: float) -> tuple[float, float]:
        if not self.rotated:
            return dx, dy
        return dx * self.a + dy * self.c, dx * self.b + dy * self.d

    def box(self, rect: Sequence[float]) -> BBox:
        if not self.rotated:
            return rect[0], rect[1], rect[2], rect[3]
        (x0, y0), (x1, y1) = self.point(rect[0], rect[1]), self.point(rect[2], rect[3])
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)  # /Rotate is 90k degrees


def _page_rows(page: pymupdf.Page) -> list[_Row]:
    """One row per PyMuPDF line (not yet merged), in display coordinates."""
    display = _Display.of(page)
    rows: list[_Row] = []
    for block in page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            dx, dy = display.vector(*line["dir"])
            chars: list[_Char] = []
            for span in line["spans"]:
                size, font, flags = float(span["size"]), span["font"], int(span["flags"])
                for ch in span["chars"]:
                    box = display.box(ch["bbox"])
                    origin = display.point(*ch["origin"])
                    chars.append(
                        _Char(ch["c"], *box, baseline=origin[1], size=size, font=font, flags=flags)
                    )
            if any(not c.blank for c in chars):
                rows.append(_Row(chars, vertical=abs(dy) > 0.2 or dx < 0))
    return rows


def _join_rows(left: _Row, right: _Row) -> _Row:
    """`right` appended to `left` on the same baseline, with a space if there was a gap."""
    chars = list(left.chars)
    gap = right.x0 - left.x1
    if gap > 0.15 * left.size and not chars[-1].blank and not right.chars[0].blank:
        last = left.ink[-1]
        chars.append(
            _Char(" ", left.x1, last.y0, right.x0, last.y1, last.baseline, last.size, last.font, 0)
        )
    chars.extend(right.chars)
    return _Row(chars, column=left.column)


def _merge_baselines(frags: list[_Row], gutter: tuple[float, float] | None) -> list[_Row]:
    """Merge fragments that share a baseline into rows, never across the column gutter."""
    rows: list[_Row] = []
    horizontal = sorted((f for f in frags if not f.vertical), key=lambda f: f.baseline)
    bands: list[list[_Row]] = []
    for frag in horizontal:
        band = bands[-1] if bands else None
        if band and abs(frag.baseline - band[0].baseline) <= 0.3 * max(frag.size, band[0].size):
            band.append(frag)
        else:
            bands.append([frag])
    for band in bands:
        band.sort(key=lambda f: f.x0)
        current = band[0]
        for frag in band[1:]:
            gap = frag.x0 - current.x1
            crosses = (
                gutter is not None and current.x1 <= gutter[0] + 1 and frag.x0 >= gutter[1] - 1
            )
            if -0.5 * current.size <= gap <= 4 * max(current.size, frag.size) and not crosses:
                current = _join_rows(current, frag)
            else:
                rows.append(current)
                current = frag
        rows.append(current)
    rows.extend(f for f in frags if f.vertical)
    for row in rows:
        row.column = _column_of(row.bbox, gutter)
    return _attach_scripts(rows)


def _attach_scripts(rows: list[_Row]) -> list[_Row]:
    """Fold small sub/superscript fragments into the row they belong to."""
    rows = sorted(rows, key=lambda r: (r.y0, r.x0))
    merged: dict[int, _Row] = {}
    dropped: set[int] = set()
    for i, small in enumerate(rows):
        if small.vertical or len(small.clean) > 12 or i in merged:
            continue
        best = None
        for j, host in enumerate(rows):
            if j == i or j in dropped or host.vertical or small.size > SCRIPT_RATIO * host.size:
                continue
            host = merged.get(j, host)
            centre = (small.y0 + small.y1) / 2
            near_y = host.y0 - 0.5 * host.size <= centre <= host.y1 + 0.5 * host.size
            near_x = host.x0 - host.size <= small.x0 and small.x1 <= host.x1 + 2 * host.size
            if near_y and near_x and host.column == small.column:
                best = j
                break
        if best is not None:
            host = merged.get(best, rows[best])
            chars = sorted(host.chars + small.chars, key=lambda c: c.x0)
            merged[best] = _Row(chars, column=host.column)
            dropped.add(i)
    return [merged.get(i, row) for i, row in enumerate(rows) if i not in dropped]


# ============================================================================ margins


def _norm_margin(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _margin_zone(row: _Row, height: float) -> str | None:
    centre = (row.y0 + row.y1) / 2
    if centre <= height * MARGIN_BAND:
        return "top"
    if centre >= height * (1 - MARGIN_BAND):
        return "bottom"
    return None


def _is_page_number(text: str) -> bool:
    return bool(PAGE_NUMBER.match(text.strip(" -\u2013\u2014|")))


@dataclass
class _MarginFilter:
    """Running headers/footers: margin text that repeats across pages, plus page numbers."""

    repeated: set[tuple[str, str]]

    def drops(self, row: _Row, height: float) -> bool:
        zone = _margin_zone(row, height)
        if zone is None:
            return False
        text = row.clean
        return _is_page_number(text) or (zone, _norm_margin(text)) in self.repeated


def _reference_pages(page_count: int, selected: list[int]) -> list[int]:
    """The analysed pages plus their neighbours, up to MARGIN_REFERENCE_PAGES pages."""
    chosen = set(selected[:MARGIN_REFERENCE_PAGES])
    distance = 1
    while len(chosen) < min(page_count, MARGIN_REFERENCE_PAGES) and distance < page_count:
        for page in selected:
            for candidate in (page - distance, page + distance):
                if 0 <= candidate < page_count and len(chosen) < MARGIN_REFERENCE_PAGES:
                    chosen.add(candidate)
        distance += 1
    return sorted(chosen)


def _margin_filter(
    doc: pymupdf.Document, selected: list[int], frags: dict[int, list[_Row]]
) -> _MarginFilter:
    reference = _reference_pages(doc.page_count, selected)
    counts: Counter[tuple[str, str]] = Counter()
    for number in reference:
        if number not in frags:
            frags[number] = _page_rows(doc[number])
        height = doc[number].rect.height
        seen = set()
        for row in frags[number]:
            zone = _margin_zone(row, height)
            if zone is not None:
                seen.add((zone, _norm_margin(row.clean)))
        counts.update(seen)
    needed = max(2, -(-len(reference) // 2))  # at least half of the pages, and two
    return _MarginFilter({key for key, n in counts.items() if n >= needed})


# ============================================================================ columns


def _find_gutter(boxes: list[BBox]) -> tuple[float, float] | None:
    """The white vertical gap between two text columns, or None for one column.

    Looks for an x-interval near the middle that (almost) no narrow line touches, with at
    least three lines on each side whose left edges line up (a real column has a margin)
    and whose rows are long enough to be prose (two table columns are not text columns).
    A couple of lines may cross it: a centred title or caption does not undo the columns.
    """
    if len(boxes) < 6:
        return None
    left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
    width = right - left
    if width < 150:
        return None
    narrow = [b for b in boxes if b[2] - b[0] <= 0.55 * width]
    # Strictest first: with crossings allowed, the gap would also take in the ragged ends
    # of the longest left-column lines.
    for allowed in range(max(2, len(narrow) // 10) + 1):
        gutter = _gutter_with(narrow, allowed, left, width)
        if gutter is not None:
            return gutter
    return None


def _gutter_with(
    narrow: list[BBox], allowed: int, left: float, width: float
) -> tuple[float, float] | None:
    best: tuple[float, float] | None = None
    for gap0, gap1 in _sparse_intervals(narrow, allowed):
        middle = (gap0 + gap1) / 2
        if gap1 - gap0 < MIN_GUTTER or not left + 0.25 * width <= middle <= left + 0.75 * width:
            continue
        if best is None or gap1 - gap0 > best[1] - best[0]:
            best = (gap0, gap1)
    if best is None:
        return None
    lefts = [b for b in narrow if b[2] <= best[0] + 0.5]
    rights = [b for b in narrow if b[0] >= best[1] - 0.5]
    for side in (lefts, rights):
        if len(side) < 3:
            return None
        edge = min(b[0] for b in side)
        if sum(b[0] <= edge + 2.5 for b in side) < 3:
            return None
        if statistics.median(_line_widths(side)) < 0.3 * width:
            return None
    return best


def _sparse_intervals(boxes: list[BBox], allowed: int) -> list[tuple[float, float]]:
    """Maximal x-intervals covered by at most `allowed` of the boxes."""
    events = sorted([(b[0], 1) for b in boxes] + [(b[2], -1) for b in boxes])
    intervals: list[tuple[float, float]] = []
    depth, start = 0, None
    for x, step in events:
        depth += step
        if depth <= allowed and start is None:
            start = x
        elif depth > allowed and start is not None:
            if x > start:
                intervals.append((start, x))
            start = None
    return intervals


def _line_widths(boxes: list[BBox]) -> list[float]:
    """Widths of visual lines (boxes sharing a bottom edge are one line)."""
    lines: dict[int, list[float]] = {}
    for x0, _, x1, y1 in boxes:
        span = lines.setdefault(round(y1), [x0, x1])
        span[0], span[1] = min(span[0], x0), max(span[1], x1)
    return [x1 - x0 for x0, x1 in lines.values()]


def _column_of(box: BBox, gutter: tuple[float, float] | None) -> str:
    if gutter is None:
        return "L"
    split = (gutter[0] + gutter[1]) / 2
    if box[0] < split - 2 and box[2] > split + 2:
        return "S"
    return "L" if (box[0] + box[2]) / 2 < split else "R"


def _reading_order(
    boxes: Sequence[BBox],
    gutter: tuple[float, float] | None,
    bridge: float,
    context: Sequence[BBox] = (),
) -> list[int]:
    """Indices of `boxes` in reading order.

    Two columns: left column, then right column, segment by segment, where full-width
    items (crossing the gutter, or with nothing beside them in the other column but more
    of that column below) close a segment. `context` boxes (e.g. figures while ordering
    text) shape the column extents but are not returned.
    """
    if gutter is None:
        return _top_down(boxes, range(len(boxes)))
    everything = list(boxes) + list(context)
    columns = [_column_of(b, gutter) for b in everything]
    bands = {
        side: _bands([b for b, c in zip(everything, columns, strict=True) if c == side], bridge)
        for side in ("L", "R")
    }
    for i, (box, column) in enumerate(zip(everything, columns, strict=True)):
        if column == "S":
            continue
        other = bands["R" if column == "L" else "L"]
        beside = any(box[1] < y1 and box[3] > y0 for y0, y1 in other)
        below = any(y0 >= box[3] for y0, _ in other)
        if not beside and below:
            columns[i] = "S"
    spanning = sorted(
        (i for i in range(len(boxes)) if columns[i] == "S"),
        key=lambda i: (boxes[i][1], boxes[i][0]),
    )
    starts = [boxes[i][1] for i in spanning]
    order: list[int] = []
    for segment in range(len(spanning) + 1):
        for side in ("L", "R"):
            members = [
                i
                for i in range(len(boxes))
                if columns[i] == side and sum(s <= boxes[i][1] for s in starts) == segment
            ]
            order.extend(_top_down(boxes, members))
        if segment < len(spanning):
            order.append(spanning[segment])
    return order


def _bands(boxes: list[BBox], bridge: float) -> list[tuple[float, float]]:
    """Vertical extents of one column, closing gaps smaller than `bridge`."""
    bands: list[list[float]] = []
    for _, y0, _, y1 in sorted(boxes, key=lambda b: b[1]):
        if bands and y0 <= bands[-1][1] + bridge:
            bands[-1][1] = max(bands[-1][1], y1)
        else:
            bands.append([y0, y1])
    return [(a, b) for a, b in bands]


def _top_down(boxes: Sequence[BBox], members: Iterable[int]) -> list[int]:
    """Top to bottom; items overlapping vertically by half their height go left to right."""
    ordered = sorted(members, key=lambda i: (boxes[i][1], boxes[i][0]))
    lines: list[list[int]] = []
    for i in ordered:
        if lines:
            anchor = boxes[lines[-1][0]]
            box = boxes[i]
            overlap = min(anchor[3], box[3]) - max(anchor[1], box[1])
            smaller = min(anchor[3] - anchor[1], box[3] - box[1])
            if smaller > 0 and overlap >= 0.5 * smaller:
                lines[-1].append(i)
                continue
        lines.append([i])
    return [i for line in lines for i in sorted(line, key=lambda i: boxes[i][0])]


# ============================================================================ maths


def _word_lengths(chars: list[_Char], classes: list[str], script: list[bool]) -> list[int]:
    """For letters (class L), the length of the plain-letter word they belong to."""
    lengths = [0] * len(chars)
    i = 0
    while i < len(chars):
        if classes[i] != "L":
            i += 1
            continue
        j = i
        while j < len(chars) and classes[j] == "L" and script[j] == script[i]:
            j += 1
        for k in range(i, j):
            lengths[k] = j - i
        i = j
    return lengths


@dataclass
class _CharInfo:
    """Per-character maths evidence for one row."""

    classes: list[str]
    script: list[bool]
    words: list[int]

    @classmethod
    def of(cls, row: _Row) -> _CharInfo:
        classes = [_char_class(c) for c in row.chars]
        script = [
            not c.blank and (c.size < SCRIPT_RATIO * row.size or bool(c.flags & _SUPERSCRIPT_FLAG))
            for c in row.chars
        ]
        return cls(classes, script, _word_lengths(row.chars, classes, script))

    def mathy(self, i: int) -> bool:
        kind = self.classes[i]
        return kind in "MDOB" or (kind == "L" and (self.words[i] == 1 or self.script[i]))


def _prose_words(row: _Row, info: _CharInfo) -> int:
    """Words that read as English: 3+ plain letters (not sin/log/...) or "is", "if", ..."""
    count = 0
    i = 0
    chars = row.chars
    while i < len(chars):
        if info.classes[i] != "L" or info.script[i]:
            i += 1
            continue
        j = i
        while j < len(chars) and info.classes[j] == "L" and not info.script[j]:
            j += 1
        word = "".join(c.c for c in chars[i:j]).lower()
        if (len(word) >= 3 and word not in _MATH_WORDS) or word in _PROSE_TWO_LETTER:
            count += 1
        i = j
    return count


def _classify_row(row: _Row) -> None:
    """Set row.kind: "math" (a display-equation line), "frag" (short maths-only piece such
    as a lone denominator, decided later) or "text"."""
    info = _CharInfo.of(row)
    text = row.clean
    fonts = sorted({c.font for c in row.ink})
    superscript = any(info.script)
    row.score, row.reasons = equation_score(text, fonts, superscript)
    ink = [i for i, c in enumerate(row.chars) if not c.blank]
    ratio = sum(info.mathy(i) for i in ink) / max(1, len(ink))
    prose = _prose_words(row, info)
    if row.vertical or sum(k == "C" for k in info.classes) > 0.5 * len(ink):
        row.kind = "text"
    elif prose == 0 and row.score >= EQUATION_THRESHOLD and ratio >= 0.5:
        row.kind = "math"
    elif prose == 0 and ratio >= 0.6 and len(ink) <= 40:
        row.kind = "frag"
    else:
        row.kind = "text"


@dataclass
class _Inline:
    """An inline equation found inside a paragraph row."""

    bbox: BBox
    text: str
    score: float
    reasons: list[str]
    font_size: float


def _inline_runs(row: _Row) -> list[tuple[int, int, list[str]]]:
    """(start, end, reasons) of the maths runs inside a prose row, by character index.

    A run is a stretch of maths-capable characters (maths/Greek/symbols, digits,
    operators, brackets, single letters, sub/superscripts). It is kept only with real
    evidence: a maths character; an italic single letter next to an operator, script or
    Greek letter; or a relation with operands on both sides ("x = 5"). A lone italic word,
    a citation "[3]" or a year range is not maths.
    """
    info = _CharInfo.of(row)
    chars = row.chars
    runs: list[tuple[int, int, list[str]]] = []
    i, n = 0, len(chars)
    while i < n:
        if not info.mathy(i) or chars[i].c in ")]}":
            i += 1
            continue
        start, depth = i, 0
        while i < n:
            c = chars[i].c
            if c in _OPENING:
                depth += 1
            elif c in ")]}":
                depth = max(0, depth - 1)
            between_digits = (
                c in ".,"
                and 0 < i < n - 1
                and chars[i - 1].c.isdigit()
                and chars[i + 1].c.isdigit()
            )
            if info.mathy(i) or chars[i].blank or between_digits or (c == "," and depth > 0):
                i += 1
                continue
            break
        start, end = _trim_run(chars, info, start, i)
        if end > start:
            reasons = _inline_evidence(chars, info, start, end)
            if reasons:
                runs.append((start, end, reasons))
    return runs


def _trim_run(chars: list[_Char], info: _CharInfo, start: int, end: int) -> tuple[int, int]:
    """Drop surrounding spaces, trailing operators/punctuation and unmatched brackets."""
    changed = True
    while changed and end > start:
        changed = False
        while end > start and (chars[end - 1].blank or chars[end - 1].c in ",.;:"):
            end -= 1
            changed = True
        while end > start and chars[start].blank:
            start += 1
            changed = True
        if _article(chars, info, start, end, start + 1):  # "a x" (maths x): the "a" is English
            start += 1
            changed = True
        elif _article(chars, info, start, end, end - 2):
            end -= 1
            changed = True
        if end > start and chars[end - 1].c in "+-*/=<>^_|~" and not info.script[end - 1]:
            end -= 1
            changed = True
        text = "".join(c.c for c in chars[start:end])
        if end > start and text[-1] in ")]}" and _unbalanced(text, closing=True):
            end -= 1
            changed = True
        elif end > start and text[0] in "([{" and _unbalanced(text, closing=False):
            start += 1
            changed = True
    return start, end


def _article(chars: list[_Char], info: _CharInfo, start: int, end: int, blank: int) -> bool:
    """Is the run edge next to `blank` a plain "a", "A" or "I" word separated by a space?"""
    if end - start < 3 or not start <= blank < end or not chars[blank].blank:
        return False
    edge = start if blank == start + 1 else end - 1
    ch = chars[edge]
    return ch.c in "aAI" and info.classes[edge] == "L" and not ch.italic and info.words[edge] == 1


def _unbalanced(text: str, *, closing: bool) -> bool:
    opened = sum(text.count(c) for c in "([{")
    closed = sum(text.count(c) for c in ")]}")
    return closed > opened if closing else opened > closed


def _inline_evidence(chars: list[_Char], info: _CharInfo, start: int, end: int) -> list[str]:
    idx = range(start, end)
    if any(info.classes[i] == "M" for i in idx):
        return ["math font or symbol"]
    operator = any(info.classes[i] == "O" or info.script[i] for i in idx)
    italic_letter = any(
        info.classes[i] == "L" and info.words[i] == 1 and chars[i].italic and not info.script[i]
        for i in idx
    )
    if italic_letter and operator:
        return ["italic variable with operator"]
    if any(
        info.classes[i] == "L" and info.words[i] == 1 and not info.script[i] and info.script[i + 1]
        for i in range(start, end - 1)
    ):
        return ["letter with sub/superscript"]
    if any(chars[i].c in _SIGNS and chars[i + 1].c.isdigit() for i in range(start, end - 1)):
        return ["signed number"]
    text = "".join(chars[i].c for i in idx)
    if _MARKUP.search(text):
        return []  # "<a><b/></a>" in a paragraph about XML is not a comparison
    for relation in RELATIONS:
        if relation in text:
            left, _, right = text.partition(relation)
            if any(c.isalnum() for c in left) and any(c.isalnum() for c in right):
                return ["relation between operands"]
    return []


# ============================================================================ drawings


@dataclass(eq=False)
class _Path:
    bbox: BBox
    kind: str  # "hrule" | "vrule" | "radical" | "shape"
    curved: bool  # has curves or diagonal lines (charts, diagrams; not tables or boxes)
    filled: bool  # a visible filled area (bars in a bar chart, shapes)


def _visible(colour: object, opacity: object) -> bool:
    if colour is None or (isinstance(opacity, (int, float)) and opacity <= 0.01):
        return False
    return not (isinstance(colour, tuple | list) and all(v >= 0.97 for v in colour))


def _page_paths(page: pymupdf.Page) -> list[_Path]:
    """Visible vector paths, classified. `get_cdrawings` (plain tuples) is several times
    faster than `get_drawings` on plots with thousands of markers."""
    display = _Display.of(page)
    paths: list[_Path] = []
    for drawing in page.get_cdrawings():
        stroked = _visible(drawing.get("color"), drawing.get("stroke_opacity"))
        filled = _visible(drawing.get("fill"), drawing.get("fill_opacity"))
        if not (stroked or filled):
            continue  # white boxes and invisible paths
        box = display.box(drawing["rect"])
        items = drawing.get("items") or []
        segments = []
        curved = False
        for item in items:
            if item[0] == "l":
                (px, py), (qx, qy) = display.point(*item[1]), display.point(*item[2])
                segments.append((px, py, qx, qy))
                if abs(px - qx) > 0.5 and abs(py - qy) > 0.5:
                    curved = True
            elif item[0] == "c":
                curved = True
        width, height = box[2] - box[0], box[3] - box[1]
        if height <= RULE_THICKNESS and width >= 3:
            kind = "hrule"
        elif width <= RULE_THICKNESS and height >= 3:
            kind = "vrule"
        elif _radical_like(segments, box, items):
            kind = "radical"
        else:
            kind = "shape"
        paths.append(_Path(box, kind, curved, filled and kind == "shape"))
    return paths


def _radical_like(
    segments: list[tuple[float, float, float, float]], box: BBox, items: Sequence[object]
) -> bool:
    """A stroked square-root sign: lines only, a steep diagonal and a bar along the top."""
    if len(segments) < 2 or len(segments) != len(items) or box[3] - box[1] > 60:
        return False
    top = box[1]
    bar = any(
        abs(y0 - y1) <= 0.6 and abs(x1 - x0) >= 4 and abs(y0 - top) <= 1.0
        for x0, y0, x1, y1 in segments
    )
    steep = any(
        abs(y1 - y0) >= 4 and abs(y1 - y0) >= 2 * abs(x1 - x0) > 0.5 for x0, y0, x1, y1 in segments
    )
    return bar and steep


def _rule_rows(rule: _Path, rows: list[_Row]) -> list[_Row]:
    """Maths rows touching a rule: above/below a fraction bar or under a radical."""
    x0, y0, x1, y1 = rule.bbox
    near = []
    for row in rows:
        if row.kind not in ("math", "frag") or row.absorbed:
            continue
        # Half of the shorter one: a numerator sits on its bar, a bar sits over a radicand.
        overlap = min(row.x1, x1 + 1) - max(row.x0, x0 - 1)
        if overlap < 0.5 * max(1.0, min(row.x1 - row.x0, x1 - x0)) and not (
            rule.kind == "radical" and overlap > 0
        ):
            continue
        distance = max(row.y0 - y1, y0 - row.y1)
        if -row.size <= distance <= 0.8 * row.size:
            near.append(row)
    return near


def _resolve_fragments(rows: list[_Row], rules: list[_Path]) -> list[_Path]:
    """Decide which "frag" rows are maths; return the rules that belong to equations.

    A fragment is maths when it sits on a fraction bar/radical that also touches another
    maths row (or fragments on both sides of a bar), or when it is vertically adjacent to
    a maths row (exponent rows, limits, an equation number on the same line). Others
    become text again (a lone "12 m/s." line is prose).
    """
    mathish = [r for r in rows if r.kind in ("math", "frag") and not r.absorbed]
    attached = {id(r) for r in mathish if r.kind == "math"}
    used: list[_Path] = []
    for rule in rules:
        near = _rule_rows(rule, mathish)
        if not near:
            continue
        widest = max(r.x1 - r.x0 for r in near)
        if rule.bbox[2] - rule.bbox[0] > 1.5 * widest + 2 * max(r.size for r in near):
            continue  # a table rule or separator next to a short maths-looking cell
        middle = (rule.bbox[1] + rule.bbox[3]) / 2
        above = any(r.y1 <= middle + 0.5 for r in near)
        below = any(r.y0 >= middle - 0.5 for r in near)
        if any(r.kind == "math" for r in near) or (above and below) or rule.kind == "radical":
            used.append(rule)
            attached.update(id(r) for r in near)
    changed = True
    while changed:
        changed = False
        for row in mathish:
            if id(row) in attached:
                continue
            if any(_math_neighbours(row, other) for other in mathish if id(other) in attached):
                attached.add(id(row))
                changed = True
    for row in mathish:
        if row.kind == "frag":
            row.kind = "math" if id(row) in attached else "text"
    return used


def _math_neighbours(row: _Row, other: _Row) -> bool:
    size = max(row.size, other.size)
    gap_y = max(row.y0 - other.y1, other.y0 - row.y1)
    gap_x = max(row.x0 - other.x1, other.x0 - row.x1)
    if gap_y <= 0.6 * size and gap_x <= 1.0 * size and gap_y > -0.5 * min(row.height, other.height):
        return True
    same_line = min(row.y1, other.y1) - max(row.y0, other.y0) >= 0.5 * min(row.height, other.height)
    return same_line and bool(EQUATION_NUMBER.match(row.clean)) and row.column == other.column


# ============================================================================ figures


@dataclass(eq=False)
class _Figure:
    bbox: BBox
    images: int = 0  # raster images in it
    paths: int = 0  # vector paths in it
    labels: list[_Row] = field(default_factory=list)
    caption: str | None = None

    @property
    def reasons(self) -> list[str]:
        reasons = ["raster image"] if self.images == 1 else []
        if self.images > 1:
            reasons.append(f"{self.images} raster images")
        if self.paths:
            reasons.append(f"vector drawing ({self.paths} paths)")
        return reasons


def _raster_figures(page: pymupdf.Page, page_rect: pymupdf.Rect) -> list[_Figure]:
    display = _Display.of(page)
    figures = []
    page_area = page_rect.width * page_rect.height
    for info in page.get_image_info():
        rect = pymupdf.Rect(display.box(info["bbox"])) & page_rect
        if rect.is_empty:
            continue
        w_px, h_px = int(info.get("width") or 0), int(info.get("height") or 0)
        if min(w_px, h_px) < MIN_IMAGE_PX or min(rect.width, rect.height) < MIN_IMAGE_PT:
            continue  # bullets, spacers, rules drawn as images
        if rect.width * rect.height >= FULL_PAGE_FRACTION * page_area:
            continue  # page background or a scanned page (OCR is out of scope)
        figures.append(_Figure((rect.x0, rect.y0, rect.x1, rect.y1), images=1))
    return figures


def _cluster(boxes: list[BBox], gap: float) -> list[list[int]]:
    """Groups of box indices whose boxes come within `gap` of each other."""
    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    for a_pos, a in enumerate(order):
        _, ay0, ax1, ay1 = boxes[a]
        for b in order[a_pos + 1 :]:
            bx0, by0, _, by1 = boxes[b]
            if bx0 > ax1 + gap:
                break
            if by0 <= ay1 + gap and ay0 <= by1 + gap:
                parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _vector_figures(paths: list[_Path], rows: list[_Row], page_rect: pymupdf.Rect) -> list[_Figure]:
    """Clusters of drawings that look like a picture, not rules, borders or tables."""
    figures = []
    for group in _overlapping(_cluster([p.bbox for p in paths], DRAWING_CLUSTER_GAP), paths):
        members = [paths[i] for i in group]
        if all(p.kind in ("hrule", "vrule") for p in members):
            continue  # separators, underlines, table grids
        box = _union(p.bbox for p in members)
        width, height = box[2] - box[0], box[3] - box[1]
        if width < MIN_DRAWING_PT or height < MIN_DRAWING_PT or width * height < 1200:
            continue
        if width >= 0.85 * page_rect.width and height >= 0.85 * page_rect.height:
            continue  # page border
        inside = [r for r in rows if _centre_inside(r.bbox, box)]
        # Curves that matter: a plotted line, or many markers; not one tick icon in a table.
        curves = [p for p in members if p.curved]
        curved = len(curves) >= 3 or any(_area(p.bbox) >= 0.05 * width * height for p in curves)
        bars = [
            p
            for p in members
            if p.filled and p.bbox[3] - p.bbox[1] >= 4 and p.bbox[2] - p.bbox[0] <= 0.5 * width
        ]
        if not curved and len(bars) < 2 and len(inside) >= 2:
            continue  # a box, shaded panel or table drawn around text
        figures.append(_Figure(box, paths=len(members)))
    return figures


def _overlapping(groups: list[list[int]], paths: list[_Path]) -> list[list[int]]:
    """Join clusters whose boxes overlap, until none do.

    Markers of a sparse scatter plot sit further apart than DRAWING_CLUSTER_GAP; without
    this they form many small clusters, and the lonely ones are dropped as too small
    instead of belonging to the plot they are drawn in.
    """
    while True:
        boxes = [_union(paths[i].bbox for i in group) for group in groups]
        joined = _cluster(boxes, 0.0)
        if len(joined) == len(groups):
            return groups
        groups = [[i for g in group for i in groups[g]] for group in joined]


def _area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _centre_inside(box: BBox, outer: BBox) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _merge_figures(figures: list[_Figure]) -> list[_Figure]:
    """Overlapping figures (a photo with vector arrows on it) become one."""
    merged = []
    for group in _cluster([f.bbox for f in figures], 2.0):
        members = [figures[i] for i in group]
        box = _union(f.bbox for f in members)
        images, paths = sum(f.images for f in members), sum(f.paths for f in members)
        merged.append(_Figure(box, images=images, paths=paths))
    return merged


def _absorb_labels(figure: _Figure, rows: list[_Row]) -> None:
    """Text inside a figure, and short labels just outside it, belong to the figure."""
    x0, y0, x1, y1 = figure.bbox
    for row in rows:
        if row.absorbed or CAPTION.match(row.clean):
            continue
        rx0, ry0, rx1, ry1 = row.bbox
        area = max(1e-6, (rx1 - rx0) * (ry1 - ry0))
        inter = max(0.0, min(rx1, x1) - max(rx0, x0)) * max(0.0, min(ry1, y1) - max(ry0, y0))
        inside = inter >= 0.6 * area
        short = len(row.clean) <= 25 and row.kind != "math"
        reach = 0.6 * row.size
        beside_y = min(ry1, y1) - max(ry0, y0) >= 0.5 * (ry1 - ry0)
        beside_x = min(rx1, x1) - max(rx0, x0) >= 0.5 * (rx1 - rx0)
        near_x = beside_y and (x0 - reach <= rx1 <= x0 + 1 or x1 - 1 <= rx0 <= x1 + reach)
        near_y = beside_x and (y0 - reach <= ry1 <= y0 + 1 or y1 - 1 <= ry0 <= y1 + reach)
        if inside or (short and (near_x or near_y)):
            row.absorbed = True
            figure.labels.append(row)
    if figure.labels:
        figure.bbox = _union([figure.bbox, *(r.bbox for r in figure.labels)])


# ============================================================================ blocks


@dataclass
class _Context:
    body: float  # body font size (pt)
    pitch: float  # typical baseline-to-baseline distance of body text (pt)


@dataclass(eq=False)
class _Run:
    rows: list[_Row]
    kind: str  # "math" | "text"
    block_start: bool  # the run starts after a geometric break, not just a style change
    block_end: bool = True


@dataclass(eq=False)
class _Block:
    """A future Element: heading, paragraph, display equation, caption or figure."""

    kind: Kind | str  # Kind, or "caption" until captions are attached
    bbox: BBox
    rows: list[_Row] = field(default_factory=list)
    text: str | None = None
    pieces: list[str | _Inline] = field(default_factory=list)  # paragraphs: text + inline maths
    font_size: float | None = None
    bold: bool = False
    score: float | None = None
    reasons: list[str] = field(default_factory=list)
    caption: str | None = None


def _dominant_size(rows: Sequence[_Row]) -> float:
    counts: Counter[float] = Counter()
    for row in rows:
        for c in row.ink:
            counts[round(c.size * 2) / 2] += 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if counts else 0.0


def _heading_sized(row: _Row, ctx: _Context) -> bool:
    return row.size >= HEADING_SIZE_RATIO * ctx.body


def _continues(prev: _Row, row: _Row, rows: list[_Row], ctx: _Context) -> bool:
    """Is `row` the next line of the paragraph whose rows so far are `rows`?"""
    size = prev.size
    if abs(row.size - size) > max(1.0, 0.12 * size):
        return False
    pitch = row.baseline - prev.baseline
    limit = max(1.3 * ctx.pitch * size / ctx.body, 1.25 * size)
    if not 0.5 * size <= pitch <= limit:
        return False
    left = min(r.x0 for r in rows)
    right = max(r.x1 for r in rows)
    if row.x0 > right or row.x1 < left or row.x0 < left - 2 * size:
        return False
    margin = min(r.x0 for r in rows[1:]) if len(rows) > 1 else rows[0].x0
    if row.x0 - margin >= 0.8 * size and prev.x1 < right - 1.5 * size:
        return False  # first-line indent after a short line: a new paragraph
    return not (_BULLET.match(row.clean) or CAPTION.match(row.clean))


def _same_style(prev: _Row, row: _Row, ctx: _Context) -> bool:
    if _heading_sized(prev, ctx) != _heading_sized(row, ctx):
        return False
    if _heading_sized(row, ctx) and abs(prev.size - row.size) > 0.5:
        return False
    return prev.bold == row.bold


def _runs(rows: list[_Row], ctx: _Context) -> list[_Run]:
    runs: list[_Run] = []
    for row in rows:
        if runs:
            run = runs[-1]
            prev = run.rows[-1]
            if run.kind == "math" and row.kind == "math" and _joins_equation(run, row):
                run.rows.append(row)
                continue
            geometric = run.kind == "text" == row.kind and _continues(prev, row, run.rows, ctx)
            if geometric and _same_style(prev, row, ctx):
                run.rows.append(row)
                continue
            run.block_end = not geometric
            ends_sentence = prev.clean[-1:] in ".:?!"
            short = row.x1 < max(r.x1 for r in run.rows) - 3 * row.size
            starts = not geometric or (ends_sentence and short)
            runs.append(_Run([row], row.kind, block_start=starts))
        else:
            runs.append(_Run([row], row.kind, block_start=True))
    return runs


def _joins_equation(run: _Run, row: _Row) -> bool:
    box = _union(r.bbox for r in run.rows)
    size = max(row.size, run.rows[-1].size)
    gap_y = row.y0 - box[3]
    horizontal = row.x0 <= box[2] + 3 * size and row.x1 >= box[0] - 3 * size
    same_line = min(row.y1, box[3]) - max(row.y0, box[1]) >= 0.5 * row.height
    return (gap_y <= 0.8 * size and horizontal) or same_line


def _title_like(text: str) -> bool:
    first = next((c for c in text if c.isalnum()), "")
    return bool(first) and not first.islower() and text[-1:] not in ",;"


def _classify_run(run: _Run, ctx: _Context) -> _Block:
    rows = run.rows
    box = _union(r.bbox for r in rows)
    size = _dominant_size(rows)
    ink = [c for r in rows for c in r.ink]
    # A lone ">4" or "+8" (a table cell, a stray sign) is too little to call an equation.
    trivial = len(rows) == 1 and len(ink) <= 2 and not any(c.math for c in ink)
    if run.kind == "math" and not trivial:
        text = _clean(" ".join(r.text for r in rows))
        script = any(c.flags & _SUPERSCRIPT_FLAG or c.size < SCRIPT_RATIO * size for c in ink)
        score, reasons = equation_score(text, sorted({c.font for c in ink}), script)
        if len(rows) > 1:
            reasons.append(f"{len(rows)} lines merged")
        return _Block(
            Kind.EQUATION,
            box,
            rows,
            text=text,
            font_size=size,
            score=max([score, *(r.score for r in rows)]),
            reasons=reasons,
        )
    text = _join_text(rows)
    letters = sum(c.isalpha() for c in text)
    bold = all(r.bold for r in rows)
    number = section_number(text)
    evidence: list[str] = []
    if number:
        evidence.append(REASON_LIST_NUMBER if number[1] else REASON_NUMBERED)
    ratio = size / ctx.body
    heading = None
    if (
        ratio >= HEADING_SIZE_RATIO
        and len(rows) <= MAX_HEADING_ROWS
        and len(text) <= MAX_HEADING_CHARS
        and (letters >= 2 or number)
    ):
        heading = [REASON_LARGER_FONT, f"font {ratio:.2f}x body", *evidence]
    elif CAPTION.match(text):
        return _Block("caption", box, rows, text=text, font_size=size)
    elif (
        bold
        and ratio >= 0.95
        and run.block_start
        and len(rows) <= MAX_BOLD_HEADING_ROWS
        and len(text) <= MAX_BOLD_HEADING_CHARS
        and letters >= 2
        and _title_like(text)
    ):
        heading = [REASON_BOLD_BODY, *evidence]
    elif (
        number
        and not number[1]
        and len(rows) == 1
        and run.block_start
        and run.block_end
        and len(text) <= 80
        and len(text.split()) <= 12
        and _title_like(text)
        and text[-1:] != "."
    ):
        heading = [REASON_NUMBERED_LINE, *evidence]
    if heading is not None:
        if bold:
            heading.append("bold")
        return _Block(
            Kind.HEADING, box, rows, text=text, font_size=size, bold=bold, reasons=heading
        )
    return _Block(Kind.PARAGRAPH, box, rows, font_size=size, bold=bold)


def _join_text(rows: Sequence[_Row]) -> str:
    """Rows joined with spaces, end-of-line hyphenation repaired (see `_line_break`)."""
    out = ""
    for row in rows:
        text = " ".join(row.text.split())  # soft hyphens survive until the final _clean
        if out:
            out, separator = _line_break(out, text)
            out += separator
        out += text
    return _clean(out)


def _line_break(before: str, after: str) -> tuple[str, str]:
    """(`before` with any hyphen removed, separator) for joining two lines of a paragraph.

    "veloc-" + "ity" -> "velocity": a letter, a hyphen at the line end and a lowercase
    start make one word. "Newton-" + "Raphson" keeps its hyphen but gets no space. A soft
    hyphen always disappears. Anything else is joined with a space. ("well-" + "known"
    becomes "wellknown": without a dictionary that case cannot be told apart.)
    """
    if before.endswith("\u00ad"):
        return before[:-1], ""
    if len(before) > 1 and before[-1] in "-\u2010" and before[-2].isalpha():
        return (before[:-1], "") if after[:1].islower() else (before, "")
    return before, " "


def _merge_style_splits(blocks: list[_Block], runs: list[_Run]) -> list[_Block]:
    """Re-join paragraphs that were split only by a bold line that did not become a heading."""
    out: list[_Block] = []
    out_runs: list[_Run] = []
    for block, run in zip(blocks, runs, strict=True):
        if (
            out
            and block.kind == Kind.PARAGRAPH
            and out[-1].kind == Kind.PARAGRAPH
            and not run.block_start
        ):
            prev = out[-1]
            prev.rows.extend(block.rows)
            prev.bbox = _union([prev.bbox, block.bbox])
            prev.font_size = _dominant_size(prev.rows)
            prev.bold = prev.bold and block.bold
            out_runs[-1].block_end = run.block_end
            continue
        out.append(block)
        out_runs.append(run)
    return out


def _merge_chapter_numbers(blocks: list[_Block]) -> list[_Block]:
    """A bare "2" or "Chapter 2" heading directly above the title becomes one heading."""
    out: list[_Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == Kind.HEADING == block.kind
            and REASON_LARGER_FONT in prev.reasons
            and REASON_LARGER_FONT in block.reasons
            and re.fullmatch(
                r"(?:(?:Chapter|CHAPTER|Unit|UNIT|Part|PART)\s+)?\d{1,3}", prev.text or ""
            )
            and 0 <= block.bbox[1] - prev.bbox[3] <= 2.5 * (block.font_size or 10)
        ):
            prev.text = f"{prev.text} {block.text}"
            prev.rows.extend(block.rows)
            prev.bbox = _union([prev.bbox, block.bbox])
            continue
        out.append(block)
    return out


def _paragraph_pieces(block: _Block) -> list[str | _Inline]:
    """Paragraph text with inline equations cut out (hyphenation repaired between rows)."""
    pieces: list[str | _Inline] = []
    for row in block.rows:
        row_pieces: list[str | _Inline] = []
        cursor = 0
        for start, end, reasons in _inline_runs(row):
            row_pieces.append("".join(c.c for c in row.chars[cursor:start]))
            chars = row.chars[start:end]
            text = _clean("".join(c.c for c in chars))
            ink = [c for c in chars if not c.blank]
            fonts = sorted({c.font for c in ink})
            score, _ = equation_score(text, fonts, any(c.flags & _SUPERSCRIPT_FLAG for c in ink))
            box = _union((c.x0, c.y0, c.x1, c.y1) for c in ink)
            row_pieces.append(_Inline(box, text, score, reasons, _dominant_size([row])))
            cursor = end
        row_pieces.append("".join(c.c for c in row.chars[cursor:]))
        _append_row(pieces, row_pieces)
    return _tidy(pieces)


def _append_row(pieces: list[str | _Inline], row_pieces: list[str | _Inline]) -> None:
    """Join the next row of a paragraph, repairing end-of-line hyphenation."""
    if not pieces:
        pieces.extend(row_pieces)
        return
    last, first = pieces[-1], row_pieces[0] if row_pieces else ""
    if isinstance(last, str) and isinstance(first, str):
        tail, separator = _line_break(last.rstrip(), first.lstrip())
        pieces[-1] = tail + separator
        if not separator:
            row_pieces[0] = first.lstrip()
    else:
        pieces.append(" ")
    pieces.extend(row_pieces)


def _tidy(pieces: list[str | _Inline]) -> list[str | _Inline]:
    """Merge adjacent strings, clean them, keep single spaces around inline maths."""
    merged: list[str | _Inline] = []
    for piece in pieces:
        if isinstance(piece, str) and merged and isinstance(merged[-1], str):
            merged[-1] += piece
        else:
            merged.append(piece)
    out: list[str | _Inline] = []
    for i, piece in enumerate(merged):
        if isinstance(piece, _Inline):
            out.append(piece)
            continue
        text = piece.translate(_INVISIBLE)
        text = "".join(c for c in text if c.isspace() or unicodedata.category(c) != "Cc")
        if i > 0 and text.lstrip()[:1] in tuple(".,;:!?)"):
            text = text.lstrip()  # no space between a marker and "." or ","
        lead = " " if i > 0 and text[:1].isspace() else ""
        trail = " " if i < len(merged) - 1 and text[-1:].isspace() else ""
        body = " ".join(text.split())
        text = (lead + body + trail) if body else (" " if lead or trail else "")
        if text:
            out.append(text)
    return out


def _attach_captions(blocks: list[_Block], figures: list[_Figure]) -> None:
    """Nearest caption (below preferred) within CAPTION_MAX_GAP; one per figure."""
    pairs = []
    for c_index, block in enumerate(blocks):
        if block.kind != "caption":
            continue
        for f_index, figure in enumerate(figures):
            fx0, fy0, fx1, fy1 = figure.bbox
            cx0, cy0, cx1, cy1 = block.bbox
            overlap = min(fx1, cx1) - max(fx0, cx0)
            if overlap < 0.3 * min(fx1 - fx0, cx1 - cx0):
                continue
            below, above = cy0 - fy1, fy0 - cy1
            if -2 <= below <= CAPTION_MAX_GAP:
                pairs.append((below, c_index, f_index))
            elif -2 <= above <= CAPTION_MAX_GAP:
                pairs.append((above + 2.0, c_index, f_index))  # small bias towards below
    used_captions: set[int] = set()
    for _, c_index, f_index in sorted(pairs):
        if c_index in used_captions or figures[f_index].caption is not None:
            continue
        figures[f_index].caption = blocks[c_index].text
        used_captions.add(c_index)
    for index, block in enumerate(blocks):
        if block.kind == "caption":
            block.kind = Kind.PARAGRAPH if index not in used_captions else "attached"


# ============================================================================ pages


@dataclass
class _PageBlocks:
    number: int
    size: tuple[float, float]
    blocks: list[_Block]


def _prepare_rows(
    page: pymupdf.Page, frags: list[_Row], margins: _MarginFilter
) -> tuple[list[_Row], tuple[float, float] | None]:
    height = page.rect.height
    kept = [f for f in frags if not margins.drops(f, height)]
    # Margin lines (an unrepeated header, a centred footer) say nothing about columns.
    body = [f.bbox for f in kept if not f.vertical and _margin_zone(f, height) is None]
    gutter = _find_gutter(body)
    return _merge_baselines(kept, gutter), gutter


def _page_blocks(
    page: pymupdf.Page,
    number: int,
    rows: list[_Row],
    gutter: tuple[float, float] | None,
    ctx: _Context,
) -> _PageBlocks:
    page_rect = page.rect
    for row in rows:
        _classify_row(row)
    paths = _page_paths(page)
    rules = [p for p in paths if p.kind in ("hrule", "radical")]
    used_rules = _resolve_fragments(rows, rules)
    loose = [p for p in paths if not any(p is u for u in used_rules)]
    figures = _merge_figures(
        _raster_figures(page, page_rect) + _vector_figures(loose, rows, page_rect)
    )
    for figure in figures:
        _absorb_labels(figure, rows)
    text_rows = [r for r in rows if not r.absorbed]
    bridge = 2.0 * ctx.body
    order = _reading_order([r.bbox for r in text_rows], gutter, bridge, [f.bbox for f in figures])
    ordered = [text_rows[i] for i in order]
    runs = _runs(ordered, ctx)
    blocks = [_classify_run(run, ctx) for run in runs]
    blocks = _merge_chapter_numbers(_merge_style_splits(blocks, runs))
    for block in blocks:
        if block.kind == Kind.EQUATION:
            _add_rules(block, used_rules)
    _attach_captions(blocks, figures)
    blocks = [b for b in blocks if b.kind != "attached"]
    for figure in figures:
        labels = " ".join(r.clean for r in sorted(figure.labels, key=lambda r: (r.y0, r.x0)))
        blocks.append(
            _Block(
                Kind.FIGURE,
                figure.bbox,
                text=labels or None,
                caption=figure.caption,
                reasons=figure.reasons,
            )
        )
    order = _reading_order([b.bbox for b in blocks], gutter, bridge)
    final = _merge_column_continuations([blocks[i] for i in order])
    for block in final:
        if block.kind == Kind.PARAGRAPH:
            block.pieces = _paragraph_pieces(block)
    final = [b for b in final if b.kind != Kind.PARAGRAPH or _has_content(b.pieces)]
    return _PageBlocks(number, (round(page_rect.width, 2), round(page_rect.height, 2)), final)


def _merge_column_continuations(blocks: list[_Block]) -> list[_Block]:
    """A paragraph cut by a column break is one paragraph again.

    The next block in reading order must be a paragraph that starts higher on the page
    (the top of the next column) with a lowercase letter, and the first part must not end
    a sentence. The merged bbox covers both parts; inline equations keep their own boxes.
    """
    out: list[_Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == Kind.PARAGRAPH == block.kind
            and block.bbox[1] < prev.bbox[1]
            and prev.rows[-1].clean[-1:] not in tuple(".?!:")
            and block.rows[0].clean[:1].islower()
        ):
            prev.rows.extend(block.rows)
            prev.bbox = _union([prev.bbox, block.bbox])
            continue
        out.append(block)
    return out


def _add_rules(block: _Block, rules: list[_Path]) -> None:
    """Grow an equation's box over the fraction bars and radicals it uses."""
    size = block.font_size or 10.0
    x0, y0, x1, y1 = block.bbox
    grow = 0.5 * size
    hits = []
    for rule in rules:
        rx0, ry0, rx1, ry1 = rule.bbox
        if rx0 <= x1 + grow and rx1 >= x0 - grow and ry0 <= y1 + grow and ry1 >= y0 - grow:
            hits.append(rule)
    if hits:
        block.bbox = _union([block.bbox, *(r.bbox for r in hits)])
        roots = [c for r in block.rows for c in r.ink if c.c == "\u221a"]
        block.reasons = sorted({*block.reasons, *(_rule_reason(r, roots) for r in hits)})


def _rule_reason(rule: _Path, roots: list[_Char]) -> str:
    """A bar starting at the top-right corner of a square-root glyph is its vinculum."""
    if rule.kind == "radical":
        return "radical sign"
    x0, y0, _, _ = rule.bbox
    for root in roots:
        if abs(root.x1 - x0) <= 3 and abs(root.y0 - y0) <= 2:
            return "radical sign"
    return "fraction bar"


def _has_content(pieces: list[str | _Inline]) -> bool:
    return any(isinstance(p, _Inline) or p.strip() for p in pieces)


def _body_and_pitch(rows_by_page: dict[int, list[_Row]]) -> _Context:
    counts: Counter[float] = Counter()
    for rows in rows_by_page.values():
        for row in rows:
            for c in row.ink:
                counts[round(c.size * 2) / 2] += 1
    body = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0] if counts else 10.0
    pitches = []
    for rows in rows_by_page.values():
        by_column: dict[str, list[_Row]] = {}
        for row in rows:
            if abs(row.size - body) <= 0.5 and not row.vertical:
                by_column.setdefault(row.column, []).append(row)
        for column_rows in by_column.values():
            column_rows.sort(key=lambda r: r.baseline)
            for a, b in pairwise(column_rows):
                step = b.baseline - a.baseline
                if 0.8 * body <= step <= 2.2 * body:
                    pitches.append(step)
    pitch = statistics.median(pitches) if pitches else 1.2 * body
    return _Context(body=max(body, 1.0), pitch=pitch)


# ============================================================================ document


def _analyze_document(doc: pymupdf.Document, selected: list[int]) -> Layout:
    frags: dict[int, list[_Row]] = {}
    margins = _margin_filter(doc, selected, frags)
    prepared = {}
    for number in selected:
        if number not in frags:
            frags[number] = _page_rows(doc[number])
        prepared[number] = _prepare_rows(doc[number], frags[number], margins)
    ctx = _body_and_pitch({n: rows for n, (rows, _) in prepared.items()})
    pages = [_page_blocks(doc[n], n, prepared[n][0], prepared[n][1], ctx) for n in selected]
    elements = _to_elements(pages)
    title = _meaningful_title(doc.metadata.get("title") if doc.metadata else None)
    if title is None:
        levels = heading_levels(elements)
        title = next((e.text for e in elements if levels.get(e.id) == 1), None)
    return Layout(
        elements=elements,
        page_count=doc.page_count,
        pages=list(selected),
        page_sizes=[p.size for p in pages],
        title=title,
        lang=_document_lang(doc),
    )


def _to_elements(pages: list[_PageBlocks]) -> list[Element]:
    """Blocks -> Elements with ids "p{page+1}-e{index}" and a global reading order."""
    elements: list[Element] = []
    for page in pages:
        ids = (f"p{page.number + 1}-e{index}" for index in count())
        for block in page.blocks:
            element_id = next(ids)
            if block.kind == Kind.PARAGRAPH:
                children = []
                parts = []
                for piece in block.pieces:
                    if isinstance(piece, _Inline):
                        child_id = next(ids)
                        children.append((child_id, piece))
                        parts.append(inline_marker(child_id))
                    else:
                        parts.append(piece)
                elements.append(
                    Element(
                        id=element_id,
                        kind=Kind.PARAGRAPH,
                        page=page.number,
                        bbox=_rounded(block.bbox),
                        order=len(elements),
                        text="".join(parts).strip(),
                        font_size=block.font_size,
                        bold=block.bold,
                    )
                )
                for child_id, inline in children:
                    elements.append(
                        Element(
                            id=child_id,
                            kind=Kind.EQUATION,
                            page=page.number,
                            bbox=_rounded(inline.bbox),
                            order=len(elements),
                            text=inline.text,
                            font_size=inline.font_size,
                            score=inline.score,
                            reasons=inline.reasons,
                            display="inline",
                            parent_id=element_id,
                        )
                    )
                continue
            kind = Kind(block.kind)
            elements.append(
                Element(
                    id=element_id,
                    kind=kind,
                    page=page.number,
                    bbox=_rounded(block.bbox),
                    order=len(elements),
                    text=block.text,
                    font_size=block.font_size,
                    bold=block.bold,
                    score=block.score,
                    reasons=block.reasons,
                    display="block" if kind == Kind.EQUATION else None,
                    caption=block.caption,
                )
            )
    return elements
