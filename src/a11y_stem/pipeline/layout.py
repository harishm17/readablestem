"""PDF -> document layout: elements in reading order. Deterministic; no model calls.

How a page is read (thresholds and known failure modes: docs/layout.md):

1. Characters come from PyMuPDF's raw text dictionary with accurate glyph boxes, so the
   numerator and denominator rows of a fraction do not overlap. All geometry is turned
   into display coordinates (the page's /Rotate applied), the space `crop` clips in.
2. Decoration is dropped: running headers/footers (text in the top/bottom 7 % of the
   page that repeats on at least half of the pages, digits normalised, unless it is
   larger or bolder than body text and changes from page to page), bare page numbers,
   images and drawings repeated in those bands (logos), and watermarks (diagonal text in
   a large font, or rotated text repeated at the same place).
3. Columns: a vertical white gap in the x-coverage of narrow lines splits a page into
   two columns. Lines that cross it are full-width and split the reading flow, and so are
   narrow lines at a section break across both columns (nothing beside them, both
   columns resuming below them at the same height).
4. PyMuPDF lines on one baseline are merged into rows (it splits justified text and
   inline maths into several "lines"). Rows are put in reading order and grouped into
   runs: paragraphs, list items, headings, display equations, captions. PyMuPDF's own
   blocks are not trusted: they glue captions onto the next paragraph and a fraction's
   denominator onto the text below it. A row starting with a bullet, a drawn bullet or
   an ordered marker confirmed by a sibling starts a list item (`_list_markers`).
5. Equations: rows that score as maths (`parse.equation_score`) and contain no prose word
   are display-equation rows. Rows on one fraction bar or radical, or side by side on one
   baseline, form lines; stacked lines form one display equation when one continues the
   other (starts with "="), is not a whole equation, or has its relation aligned with it
   (`_equation_regions`). An equation number beside it ("(2.4)", "Eq. 2.4", or a bare
   "2.4" to its right) becomes `Element.equation_label`, outside the box and the text.
   Inside paragraphs and list items, runs of maths characters (maths fonts, math-italic
   letters, Greek, maths symbols) or an italic single letter next to an operator become
   inline equations, and the text gets `core.types.inline_marker(id)` in their place.
6. Figures: raster images and clusters of vector drawings, minus equation rules, text
   underlines, page borders and boxes or tables drawn around text (rounded or not).
   Short labels inside or right next to a figure belong to it; running text never does,
   and an image with running text on it is a background, not a figure. A "Figure N"
   caption within reach is attached.
7. Text is document content: the inline-marker brackets are replaced by look-alikes
   wherever they occur in it, so only real inline equations have markers.

Untrusted input: MuPDF is C code parsing bytes from the internet, and some damaged files
make it abort the whole process (a glibc double free while rendering). Before a page is
extracted or rendered in this process, `analyze` and `crop` have a child process do the
same once (`_probe_pages`); a child that dies or hangs makes the file
`LayoutError("unreadable")`.
MuPDF's own error and warning printing is switched off: it quotes content-stream tokens
and resource names from the PDF, which must not reach the logs.

Thread safety: PyMuPDF is not thread-safe, so every PyMuPDF call here runs under one
module lock. `crop` keeps a small cache of opened documents keyed by the PDF bytes, which
expires after `DOCUMENT_CACHE_TTL_S` of disuse; call `release(pdf_bytes)` when a document
is finished so its content does not stay in memory even that long.
"""

from __future__ import annotations

import bisect
import hashlib
import re
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter, OrderedDict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property, lru_cache
from itertools import count, pairwise

import pymupdf

from a11y_stem.core.mathchars import base_char, char_variant, styled_char
from a11y_stem.core.notation import canonical_from_parts
from a11y_stem.core.types import BBox, Element, Kind, Notice, TableGrid, inline_marker
from a11y_stem.pipeline import options as run_options
from a11y_stem.pipeline import tables
from a11y_stem.pipeline.notation_memory import (
    BOLD_LETTER_REASON,
    EVIDENCE_COURSE,
    EVIDENCE_DOCUMENT,
    EVIDENCE_MATHS_FONT,
    EVIDENCE_NEARBY,
    bold_letter_latex,
)
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
MAX_GUTTER_CROSSINGS = 24  # lines that may cross a gutter (centred titles, captions)
MIN_IMAGE_PX = 24  # raster images with a side smaller than this (pixels) are decoration
MIN_IMAGE_PT = 12.0  # ... and so are images drawn smaller than this on the page
FULL_PAGE_FRACTION = 0.85  # images covering this share of the page: background or scan
MIN_DRAWING_PT = 24.0  # a vector figure is at least this big on both sides
DRAWING_CLUSTER_GAP = 6.0  # drawings closer than this belong to the same figure
RULE_THICKNESS = 1.6  # a fraction bar or table rule is at most this thick
CAPTION_MAX_GAP = 36.0  # furthest a "Figure N" caption may sit from its figure
SCRIPT_RATIO = 0.85  # characters smaller than this x the row size are sub/superscripts
WATERMARK_RATIO = 2.0  # diagonal text this many times the body size is a watermark
DOCUMENT_CACHE_SIZE = 4
DOCUMENT_CACHE_TTL_S = 120.0  # a cached document unused for this long is closed
PROBE_TIMEOUT_S = 60.0  # longest a child process may take to open and render the pages
PROBE_ZOOM = 1.0  # trial render at 72 dpi: the crash is in building the page display list
# ... but never more than this many pixels on the long side (crop's own cap), so a huge
# page box (MuPDF accepts 18000 pt) does not need a gigabyte of pixels: the display list,
# where the crash is, is built whatever the zoom.
PROBE_MAX_SIDE_PX = 2048
# Address space the probe child may use (768 MB, like the upload probe in
# web/pdf_probe.py), plus room for the PDF itself, which it holds twice.
PROBE_MEMORY_BYTES = 768 * 1024 * 1024
CROP_PAD = 2.0  # crop's default padding around an element box (pt)

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
# "Figure 2.3 Velocity...", "Fig. 4: ...", but not "Figure 2 shows that ...". Beamer
# captions have no number: "Figure: Velocity ...".
CAPTION = re.compile(
    r"^(?:Figure|FIGURE|Fig\.|FIG\.)\s*\d+[A-Za-z]?(?:[.\-\u2013]\d+[A-Za-z]?)*"
    r"(?:\s*[.:|\u2013\u2014-]\s*|\s+(?=[A-Z(\"'\u201c])|\s*$)"
    r"|^(?:Figure|Fig\.):\s+(?=\S)"
)
PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|of)\s*\d{1,4})?$", re.IGNORECASE
)
# "(2.4)", "[3]", "(A.1)", "Eq. 2.4", "Eqn (5)": an equation number wherever it sits.
EQUATION_NUMBER = re.compile(
    r"^(?:(?:Eq|Eqn|Equation)\.?\s*)?"
    r"(?:\(\s*[\dA-Za-z]{1,3}(?:[.\-]\d{1,3})*[a-z]?\s*\)|\[\s*\d{1,3}(?:[.\-]\d{1,3})*[a-z]?\s*\])$"
    r"|^(?:Eq|Eqn|Equation)\.?\s*\d{1,3}(?:[.\-]\d{1,3})*[a-z]?$"
)
# "2.4" or "2.4a" without brackets (OpenStax, many Word templates): an equation number only
# when it sits on the same line as maths, well to its right.
BARE_EQUATION_NUMBER = re.compile(r"^\d{1,3}(?:\.\d{1,3}){1,2}[a-z]?$")
# Glyphs that start list items. Word draws bullets in the Symbol font (as U+2022 or the
# private-use U+F0B7), and Symbol is a maths font, so without this every bullet would be
# an inline equation. Other private-use Symbol code points are Greek letters (U+F061 is
# alpha) and stay maths; Wingdings bullets are not in a maths font in the first place.
# Beamer draws its triangles with the AMS symbol font (U+25B6), also a maths font.
_BULLET_GLYPHS = frozenset(
    "\u2022\u25e6\u25aa\u25ab\u25a0\u25a1\u25cf\u25cb\u2023\u2043\u27a2\u2713\u2714\u25ba\u25b8"
    "\u25b6\u25b9\u25bb\u27a4\u25c6\u25c7\u2666\u2756\uf0b7"
)
# Bullets only at the start of a line and followed by a space (LaTeX's second-level "\u2013",
# third-level "\u2217", Markdown's "-" and "*"); maths or punctuation anywhere else. They
# need a sibling (the same glyph starting another line at the same indent), because a
# wrapped sentence can start a line with a dash.
_LINE_START_BULLETS = frozenset("-\u2013\u2014*\u2217\u00b7\u2219\u22c5")
_DOT_BULLETS = frozenset("\u00b7\u2219\u22c5")  # bullets at the start of a line, maths elsewhere
# Ordered list markers: "1.", "2)", "(3)", "a.", "b)", "(c)", "iv.", "(ii)", "A.", "IV)".
_ORDERED_MARKER = re.compile(
    r"^(?:\((?P<inner>\d{1,3}|[A-Za-z]|[ivxlc]{2,6}|[IVXLC]{2,6})\)"
    r"|(?P<plain>\d{1,3}|[A-Za-z]|[ivxlc]{2,6}|[IVXLC]{2,6})(?P<sep>[.)]))$"
)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
MAX_MARKER_GAP = 3.0  # x font size: widest gap between a list marker and its text
MARKER_ALIGN = 3.0  # pt: sibling markers (or their item texts) start this close in x
DRAWN_BULLET_RATIO = 0.6  # a drawn bullet is at most this x the row's font size
# Relations that make a maths row an equation of its own (arrows also appear in limits).
_ROW_RELATIONS = frozenset("=<>\u2264\u2265\u2260\u2248\u2261\u221d")
# Big operators of an inline formula that TeX sets on a baseline of their own ($\int_0^1$).
_BIG_OPERATORS = frozenset("\u222b\u222c\u222d\u222e\u2211\u220f\u2210\u221a")
_LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$")
_JUNK_TITLE = re.compile(
    r"^(?:untitled|document\s*\d*$|microsoft\s+(?:word|powerpoint)|powerpoint presentation"
    r"|slide\s*\d+$|title$|no title$)|\.(?:pdf|docx?|pptx?|tex|odt|rtf|txt|html?|key)$",
    re.IGNORECASE,
)
# Applied to every piece of text taken from the document: invisible characters go; the
# inline-marker brackets (core.types.INLINE_MARKER) become look-alike white square
# brackets, so text in the PDF cannot fake a marker (layout is the only producer of
# markers); Word's private-use Symbol bullet becomes U+2022.
_TEXT_MAP: dict[int, str | None] = {
    **dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff")),
    ord("\u27e6"): "\u301a",
    ord("\u27e7"): "\u301b",
    ord("\uf0b7"): "\u2022",
}

# Character classes for maths detection. Symbols that only occur in maths (signs such as
# the minus are in _SIGNS: in a text font they also print dashes and option flags):
_STRONG_MATH = frozenset(
    "\u2211\u220f\u222b\u222e\u221a\u2202\u2207\u221e"  # sum prod int oint sqrt partial nabla inf
    "\u2206"  # increment: TeX's text-font capital Delta (OT1 cmr) extracts as this
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
        *("cov", "const", "sgn", "tr", "mol", "rad", "proj", "rank", "diag", "grad", "curl"),
        *("erf", "coth", "sech", "csch", "arsinh", "arcosh", "artanh"),
    )
)
_PROSE_TWO_LETTER = frozenset(
    (
        *("is", "so", "if", "to", "of", "in", "at", "on", "by", "as", "or", "an", "be"),
        *("we", "it", "no", "do", "up", "us"),
    )
)
# TeX's bold fonts: CMBX (bold extended), CMB, CMMIB (bold maths italic, \boldsymbol and
# \bm) and CMBSY (bold symbols); EC/cm-super's SFBX.
_BOLD_FONT = re.compile(
    r"bold|black|heavy|semibold|demi|cmbx|cmb\d|cmmib|cmbsy|sfbx|-medi$|\bbd\b|,bd|-bd",
    re.IGNORECASE,
)
_ITALIC_FONT = re.compile(r"italic|oblique|cmti|cmsl|sfti|-it$|,it|ital", re.IGNORECASE)
# Regular-weight text and maths fonts known to have a bold face that PDFs name as bold
# (CMBX, Times-Bold, DejaVuSerif-Bold, ...), or OpenType maths fonts that write bold as
# Unicode maths bold. A letter drawn in one of them, not bold, is known to be plain, so
# the equation step may treat a bold reading of it as a real difference. Fonts that
# cannot show a letter's style in the text layer (MSBM double-struck, EUSM and RSFS
# script, EUFM fraktur, CMSY calligraphic, Symbol) are deliberately not in the list.
_REGULAR_FACE = re.compile(
    r"^(?:cmr\d|cmmi\d|cmti\d|cmsl\d|cmss\d|cmssi\d|cmu|lmroman|lmmathitalic|lmsans|"
    r"latinmodern|sfrm|sfti|sfss|txr|txmi|pxr|pxmi|times|nimbus|liberation|dejavu|arial|"
    r"helvetica|calibri|cambria|carlito|caladea|georgia|palatino|bookantiqua|garamond|"
    r"charter|verdana|tahoma|segoe|noto(?:serif|sans)|sourceserif|sourcesans|opensans|"
    r"roboto|lato|stix|xits|texgyre|linlibertine|libertinus|minion|myriad|utopia)",
    re.IGNORECASE,
)
_MONO_FONT = re.compile(r"mono|courier|consolas|menlo|inconsolata|cmtt|sftt", re.IGNORECASE)
# TeX fonts whose letters are a style the text layer drops: MSBM's R is \mathbb{R}, the
# reals, but its text layer says "R". (pattern, style, capitals only): MSBM, bbm and
# dsrom draw double-struck capitals; CMSY's capitals are \mathcal and EUSM, EUSB and RSFS
# (\mathscr) draw script capitals; EUFM and EUFB are fraktur in both cases.
_LETTER_STYLE_FONTS = (
    (re.compile(r"^(?:msbm\d|bbm\d|bbmss|dsrom|dsss)", re.IGNORECASE), "double-struck", True),
    (re.compile(r"^(?:cmsy\d|cmbsy\d|eusm\d|eusb\d|rsfs)", re.IGNORECASE), "script", True),
    (re.compile(r"^(?:eufm\d|eufb\d)", re.IGNORECASE), "fraktur", False),
)
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
    # Layout facts the job page lists ("Not handled in this file", review "none"): a
    # block of program code, a footnote (`_layout_notices`). Codes and ids only.
    notices: list[Notice] = field(default_factory=list)


# ============================================================================ public API


def analyze(
    pdf_bytes: bytes, *, pages: list[int] | None = None, max_pages: int | None = None
) -> Layout:
    """Elements of the given 0-based pages (all by default), ids "p{page+1}-e{index}".

    `index` counts from 0 per page in reading order; an inline equation comes right after
    the paragraph that contains it. `Element.order` is the position in the whole document.
    Pages are analysed in ascending order (duplicates ignored). Raises `LayoutError`.

    The selected pages are first rendered once in a child process (see `_probe_pages`);
    the document is opened for this call only and closed again, so analysis leaves no
    user content in this module (only `crop` caches documents).
    """
    with _LOCK:
        _quiet_mupdf()
        try:
            doc = _open(pdf_bytes)
            try:
                selected = _select_pages(doc.page_count, pages, max_pages)
                _ensure_probed(pdf_bytes, _digest(pdf_bytes), selected)
                return _analyze_document(doc, selected)
            except (RuntimeError, pymupdf.mupdf.FzErrorBase) as exc:  # damaged content
                raise LayoutError("unreadable", "the PDF could not be parsed") from exc
            finally:
                doc.close()
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()  # the warning store quotes PDF content


def crop(
    pdf_bytes: bytes,
    element: Element,
    *,
    dpi: int = 200,
    pad: float = CROP_PAD,
    max_side_px: int = 2048,
) -> bytes:
    """PNG of the element's region (padded), long side capped at `max_side_px`.

    The region is `element.bbox` grown by `pad` points on every side and clipped to the
    page, rendered at `dpi` on a white background, then scaled down if its long side would
    exceed `max_side_px`. Opened documents are cached (see `release`), so cropping every
    element of one PDF parses it once. A page is only rendered here after it rendered in a
    child process (`analyze` already did that for the pages it analysed). Safe to call from
    several threads, but calls are serialised by the module lock, so there is nothing to
    gain from a thread pool here.
    """
    if dpi <= 0 or pad < 0 or max_side_px < 16:
        raise ValueError("crop needs dpi > 0, pad >= 0 and max_side_px >= 16")
    with _LOCK:
        _quiet_mupdf()
        try:
            key, doc = _CACHE.document(pdf_bytes)
            if not 0 <= element.page < doc.page_count:
                raise ValueError(f"element page {element.page} is not in the PDF")
            _ensure_probed(pdf_bytes, key, [element.page])
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
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def release(pdf_bytes: bytes | None = None) -> None:
    """Drop a cached document (or all of them with no argument).

    The cache holds the PDF bytes and the parsed document of recent `crop` calls. Entries
    also expire on their own after `DOCUMENT_CACHE_TTL_S` without use, but the pipeline
    should release a document as soon as its job finishes (in a `finally`), so user
    content does not linger in process memory.
    """
    with _LOCK:
        _CACHE.release(pdf_bytes)


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


# Words that make "12 Metres", "2.5 Kilograms" or "10 October 2026" a quantity or a date,
# not a numbered section title.
_QUANTITY_WORDS = frozenset(
    (
        *("metre", "meter", "kilometre", "kilometer", "centimetre", "centimeter"),
        *("millimetre", "millimeter", "gram", "kilogram", "second", "minute", "hour"),
        *("day", "week", "month", "year", "newton", "joule", "watt", "volt", "ampere", "amp"),
        *("kelvin", "degree", "litre", "liter", "percent", "mile", "foot", "feet", "inch"),
        *("inches", "pound", "ounce"),
        *("january", "february", "march", "april", "may", "june", "july", "august"),
        *("september", "october", "november", "december"),
        *("jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"),
    )
)


def section_heading_number(text: str) -> bool:
    """Does `text` start like a numbered section title, not a quantity, date or list item?

    Body-size headings need this before a number counts as strong evidence: "2.3 Units of
    Speed", "Chapter 2 Motion", "IV. Results" pass; "2 kg", "12 Metres", "0.5 s",
    "10 October 2026", "9.8 metres per second" and "1. Measure the ramp" do not. A bare
    integer needs a prefix word ("Chapter 3"); a decimal must not start with "0." and
    must be followed by a capitalised word that is not a unit or a month.
    """
    match = SECTION_NUMBER.match(text.strip())
    if not match:
        return False
    num, sep, prefix = match["num"], match["sep"], match["prefix"]
    if not prefix:
        if num[0].isdigit() and ("." not in num or num.startswith("0")):
            return False  # "2 kg", "12 Metres", "0.5 s"; "1." is a list item
        if not num[0].isdigit() and (not sep or sep == ")" or (len(num) == 1 and num not in "IVX")):
            return False  # "A) Units", "B. Smith"
    words = text.strip()[match.end() :].split()
    if not words:
        return bool(prefix)  # "Chapter 2" on its own line
    first = words[0].strip("\"'(“")
    letters = sum(c.isalpha() for c in " ".join(words))
    if not first[:1].isupper() or letters < 3:
        return False
    stem = "".join(c for c in first.lower() if c.isalpha())
    unit = stem in _QUANTITY_WORDS or stem.rstrip("s") in _QUANTITY_WORDS
    after = words[1] if len(words) > 1 else ""
    return not (unit and not after[:1].isupper())  # "Second Law" is a title, "Seconds for" not


# ============================================================================ documents


def _quiet_mupdf() -> None:
    """Stop MuPDF printing parser errors and warnings to stdout. Hold `_LOCK`.

    They quote content-stream tokens and resource names from the PDF ("unknown keyword:
    'Jane...'"), and stdout goes to the service logs (privacy rule 1). The switch is
    process-wide, so it is set again on every call in case other code turned it back on.
    """
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)


@dataclass
class _Cached:
    data: bytes
    doc: pymupdf.Document
    used: float  # time.monotonic() of the last use


class _DocumentCache:
    """Opened documents for `crop`, at most `DOCUMENT_CACHE_SIZE`, each closed after
    `DOCUMENT_CACHE_TTL_S` without use. Every method expects the caller to hold `_LOCK`.

    The expiry runs on a daemon timer, so a long-lived worker that goes idle after a job
    does not keep that job's PDF in memory until the next request comes along.
    """

    def __init__(self) -> None:
        self.entries: OrderedDict[str, _Cached] = OrderedDict()
        self.timer: threading.Timer | None = None
        self.due = 0.0  # time.monotonic() at which the pending timer fires

    def __len__(self) -> int:
        return len(self.entries)

    def document(self, pdf_bytes: bytes) -> tuple[str, pymupdf.Document]:
        """(SHA-256 key, opened document) for these bytes, opening them if needed.

        Identity is checked first (the pipeline passes the same bytes object to every
        crop, so no hashing is needed); equal bytes from another object are found by hash.
        """
        now = time.monotonic()
        self.expire(now)
        key = next((k for k, e in self.entries.items() if e.data is pdf_bytes), None)
        if key is None:
            key = _digest(pdf_bytes)
        entry = self.entries.get(key)
        if entry is None:
            entry = _Cached(pdf_bytes, _open(pdf_bytes), now)
            self.entries[key] = entry
            while len(self.entries) > DOCUMENT_CACHE_SIZE:
                self.entries.popitem(last=False)[1].doc.close()
        entry.used = now
        self.entries.move_to_end(key)
        self.schedule()
        return key, entry.doc

    def release(self, pdf_bytes: bytes | None) -> None:
        if pdf_bytes is None:
            keys = list(self.entries)
        else:
            keys = [k for k, e in self.entries.items() if e.data is pdf_bytes]
            keys = keys or [k for k in [_digest(pdf_bytes)] if k in self.entries]
        for key in keys:
            self.entries.pop(key).doc.close()

    def expire(self, now: float) -> None:
        for key in [k for k, e in self.entries.items() if now - e.used >= DOCUMENT_CACHE_TTL_S]:
            self.entries.pop(key).doc.close()

    def schedule(self) -> None:
        """Make sure a timer fires when the least recently used entry expires."""
        if not self.entries:
            return
        due = min(e.used for e in self.entries.values()) + DOCUMENT_CACHE_TTL_S + 0.05
        if self.timer is not None:
            if self.due <= due:
                return  # it fires in time; anything not yet expired is rescheduled then
            self.timer.cancel()
        self.due = due
        self.timer = threading.Timer(max(0.0, due - time.monotonic()), self._on_timer)
        self.timer.daemon = True
        self.timer.start()

    def _on_timer(self) -> None:
        with _LOCK:
            if self.timer is threading.current_thread():  # not a timer cancelled too late
                self.timer = None
            self.expire(time.monotonic())
            self.schedule()


_CACHE = _DocumentCache()


def _digest(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


# Pages that rendered in a child process, by document hash (hashes only, no content).
_PROBED: OrderedDict[str, set[int]] = OrderedDict()
PROBED_MEMORY = 64  # documents remembered

# Runs in a fresh interpreter: open the PDF from stdin, then extract and render each page
# the way analyze and crop will. Python errors are fine (the parent handles them); only a
# crash (MuPDF aborting the process) or a hang matters, so they are swallowed per page.
_PROBE_SCRIPT = """
import sys
try:  # cap this process's address space first (Linux, macOS; best effort elsewhere)
    import resource
    limit = int(sys.argv[5])
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    if soft == resource.RLIM_INFINITY or soft > limit:
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
except (ImportError, ValueError, OSError):
    pass
import pymupdf
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)
doc = pymupdf.open(stream=sys.stdin.buffer.read(), filetype="pdf")
zoom, max_side = float(sys.argv[2]), float(sys.argv[4])
for number in map(int, sys.argv[1].split(",")):
    try:
        page = doc[number]
        page.get_text("rawdict", flags=int(sys.argv[3]))
        page.get_cdrawings()
        page.get_image_info()
        side = max(page.rect.width, page.rect.height, 1.0)
        scale = min(zoom, max_side / side)
        page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    except Exception:
        pass
sys.stdout.write("ok")
"""


def _ensure_probed(pdf_bytes: bytes, key: str, pages: list[int]) -> None:
    """Probe the pages of this document that have not passed a probe yet. Hold `_LOCK`."""
    done = _PROBED.get(key, set())
    todo = [page for page in pages if page not in done]
    if todo:
        _probe_pages(pdf_bytes, todo)
        _PROBED[key] = done | set(todo)
    if key in _PROBED:
        _PROBED.move_to_end(key)
    while len(_PROBED) > PROBED_MEMORY:
        _PROBED.popitem(last=False)


def _probe_pages(pdf_bytes: bytes, pages: list[int]) -> None:
    """Extract and render `pages` in a child process; LayoutError("unreadable") if it dies.

    Some damaged PDFs make MuPDF abort the whole process (a glibc double free while
    building a page's display list) although they open and parse without an error. In the
    worker, that would take the web server down with it, and again on every retry of the
    job. A page that rendered in a child renders here too: MuPDF is deterministic. The
    child's output is discarded (its error messages may quote the PDF). The child caps
    its own memory and renders at most `PROBE_MAX_SIDE_PX` on a page's long side, so a
    huge page box costs megabytes, not a gigabyte.
    """
    command = [
        sys.executable,
        "-I",  # isolated: no PYTHON* variables, user site or current directory on the path
        "-c",
        _PROBE_SCRIPT,
        ",".join(map(str, pages)),
        str(PROBE_ZOOM),
        str(TEXT_FLAGS),
        str(PROBE_MAX_SIDE_PX),
        str(PROBE_MEMORY_BYTES + 2 * len(pdf_bytes)),
    ]
    try:
        result = subprocess.run(
            command, input=pdf_bytes, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired:
        raise LayoutError("unreadable", "the PDF took too long to render") from None
    if result.returncode != 0 or result.stdout != b"ok":
        raise LayoutError("unreadable", "the PDF could not be rendered")


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
        command-line options as "\u2212d", and dashes come out as minus signs too. List
        bullets are never maths, even in the Symbol font Word draws them in.
        """
        c = self.c
        if self.blank or c in _BULLET_GLYPHS:
            return False
        if _font_traits(self.font)[2]:
            return True
        return is_math_alphanumeric(c) or c in _STRONG_MATH or bool(GREEK.match(c))

    @property
    def mono(self) -> bool:
        return bool(self.flags & _MONO_FLAG) or _font_traits(self.font)[3]

    @property
    def styled(self) -> str:
        """The character with its style kept as a Unicode math letter (bold d -> U+1D41D;
        MSBM's R -> U+211D, the reals), as Chromium-printed PDFs already store it.
        Equation text layers use this so the equation step can check that a bold vector
        stayed bold and that the reals stayed the reals."""
        style = _letter_style(self.font) if self.c.isascii() and self.c.isalpha() else None
        if style is not None and (self.c.isupper() or not style[1]):
            return styled_char(self.c, style[0])
        return styled_char(self.c, "bold") if self.bold else self.c

    @property
    def known_regular(self) -> bool:
        """A letter known to be plain: not bold, in a font with a bold face, and not a
        styled maths code point (a maths bold or double-struck letter says its own style)."""
        if not self.c.isalpha() or self.bold or char_variant(self.c) not in (None, "italic"):
            return False
        return bool(_REGULAR_FACE.match(self.font.split("+", 1)[-1].replace(" ", "")))


@lru_cache(maxsize=1024)
def _letter_style(font: str) -> tuple[str, bool] | None:
    """(style, capitals only) when the font draws its letters in a style the text layer
    drops (`_LETTER_STYLE_FONTS`), else None."""
    name = font.split("+", 1)[-1]
    for pattern, style, capitals in _LETTER_STYLE_FONTS:
        if pattern.match(name):
            return style, capitals
    return None


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


def _regular_letters(chars: Iterable[_Char]) -> str | None:
    """`Element.regular_letters` of an equation: its letters known to be plain."""
    letters = sorted({base_char(c.c) for c in chars if c.known_regular})
    return "".join(letters) or None


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
    if c.isalpha() and c not in _ACCENTS:  # a TeX hat (U+02C6) is a "modifier letter"
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
    """Whitespace collapsed, soft hyphens/zero-width/control characters removed, marker
    brackets neutralised (`_TEXT_MAP`), TeX's negation slash joined to its relation."""
    text = text.translate(_TEXT_MAP)
    text = "".join(c for c in text if c.isspace() or unicodedata.category(c) != "Cc")
    if "\u0338" in text:
        text = _negate_relations(text)
    return " ".join(text.split())


# TeX writes \neq as a zero-width slash (U+0338, CMSY's "negationslash") drawn over "=".
_NEGATED = {
    "=": "\u2260",
    "<": "\u226e",
    ">": "\u226f",
    "\u2264": "\u2270",
    "\u2265": "\u2271",
    "\u2208": "\u2209",
    "\u2261": "\u2262",
    "\u223c": "\u2241",
    "\u2282": "\u2284",
    "\u2286": "\u2288",
}
_NEGATION = re.compile(
    "\\s*\u0338\\s*([" + "".join(_NEGATED) + "])|([" + "".join(_NEGATED) + "])\\s*\u0338"
)


def _negate_relations(text: str) -> str:
    """ "A\u0338 = 0" -> "A \u2260 0"; a slash with no relation next to it is dropped."""
    text = _NEGATION.sub(lambda m: " " + _NEGATED[m.group(1) or m.group(2)], text)
    return text.replace("\u0338", "")


# ============================================================================ rows


@dataclass(eq=False)
class _Row:
    """Characters on one visual line of one column, left to right."""

    chars: list[_Char]
    vertical: bool = False  # rotated text: never merged with other rows
    diagonal: bool = False  # ... at an angle that is not a multiple of 90 degrees
    column: str = "L"  # "L", "R" or "S" (spans both columns / single-column page)
    kind: str = "text"  # "math" | "frag" | "text", see _classify_row
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    absorbed: bool = False  # belongs to a figure (labels, text inside a diagram)
    marker: _Marker | None = None  # the row starts a list item (see _list_markers)
    region: _Region | None = None  # the display equation it belongs to (_equation_regions)

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
    def styled_text(self) -> str:
        """`text` with styled letters as Unicode math letters (equation text layers only)."""
        return "".join(c.styled for c in self.chars)

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

    def is_script(self, c: _Char) -> bool:
        """Is `c` a sub- or superscript in this row?

        Smaller than the row's text, or raised with MuPDF's superscript flag. The flag
        alone is not trusted: MuPDF sets it on any span that starts above the previous
        one, which in TeX output is most of a line after an inline fraction.
        """
        if c.blank:
            return False
        if c.size < SCRIPT_RATIO * self.size:
            return True
        return bool(c.flags & _SUPERSCRIPT_FLAG) and c.baseline < self.baseline - 0.2 * self.size


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
                vertical = abs(dy) > 0.2 or dx < 0
                rows.append(_Row(chars, vertical, diagonal=abs(dx) > 0.2 and abs(dy) > 0.2))
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
    return _attach_big_operators(_attach_scripts(rows))


def _attach_scripts(rows: list[_Row]) -> list[_Row]:
    """Fold small sub/superscript fragments into the row they belong to.

    A fragment is a script only if every glyph in it is small: a subscripted variable
    such as a fraction's denominator "v_avg" is mostly script-size letters, but its "v"
    is not, and folding it into the line above would interleave the two by x.
    Hosts are looked up in a vertical window around each fragment (rows are sorted by
    top edge), in the same order a full scan would visit them.
    """
    rows = sorted(rows, key=lambda r: (r.y0, r.x0))
    tops = [r.y0 for r in rows]
    tallest = max((r.height for r in rows if not r.vertical), default=0.0)  # hosts only
    largest = max((r.size for r in rows), default=0.0)
    merged: dict[int, _Row] = {}
    dropped: set[int] = set()
    for i, small in enumerate(rows):
        if small.vertical or len(small.clean) > 12 or i in merged:
            continue
        glyph = max(c.size for c in small.ink)  # the largest glyph, see the docstring
        best = None
        centre = (small.y0 + small.y1) / 2
        # A host's box grows when a script joins it, by at most about one font size.
        lo = bisect.bisect_left(tops, centre - 2 * tallest - 2 * largest)
        hi = bisect.bisect_right(tops, centre + 2 * largest)
        for j in range(lo, hi):
            host = rows[j]
            if j == i or j in dropped or host.vertical or glyph > SCRIPT_RATIO * host.size:
                continue
            host = merged.get(j, host)
            near_y = host.y0 - 0.5 * host.size <= centre <= host.y1 + 0.5 * host.size
            near_x = host.x0 - host.size <= small.x0 and small.x1 <= host.x1 + 2 * host.size
            if near_y and near_x and host.column == small.column:
                best = j
                break
        if best is not None:
            host = merged.get(best, rows[best])
            merged[best] = _Row(_insert_script(host.chars, small.chars), column=host.column)
            dropped.add(i)
    return [merged.get(i, row) for i, row in enumerate(rows) if i not in dropped]


def _attach_big_operators(rows: list[_Row]) -> list[_Row]:
    """Fold a short fragment led by a big operator into the line it sits in.

    TeX sets the integral sign of an inline $\\int_0^1 x^2\\,dx$ (and a text-style sum or
    root) on a baseline of its own, so it arrives as a separate row of an integral sign
    and its upper limit in the middle of a line of text. Left there, it becomes a display
    equation of its own and the inline equation's crop misses the sign. A fragment of at
    most four glyphs with a big operator in it joins the row whose vertical extent holds
    its centre and whose horizontal extent it lies within, before the first glyph to its
    right.
    """
    pieces = [
        i
        for i, r in enumerate(rows)
        if not r.vertical and len(r.ink) <= 4 and any(c.c in _BIG_OPERATORS for c in r.ink)
    ]
    if not pieces:
        return rows
    piece_ids = set(pieces)
    hosts = sorted((r.y0, i) for i, r in enumerate(rows) if i not in piece_ids and not r.vertical)
    tops = [y for y, _ in hosts]
    tallest = max((rows[i].height for _, i in hosts), default=0.0)
    merged: dict[int, _Row] = {}
    dropped: set[int] = set()
    for i in pieces:
        piece = rows[i]
        centre = (piece.y0 + piece.y1) / 2
        lo = bisect.bisect_left(tops, centre - tallest)
        best, overlap = None, 0.0
        for _, j in hosts[lo : bisect.bisect_right(tops, centre)]:
            host = merged.get(j, rows[j])
            reach = 0.5 * host.size
            inside = host.x0 - reach <= piece.x0 and piece.x1 <= host.x1 + reach
            shared = min(piece.y1, host.y1) - max(piece.y0, host.y0)
            on_line = host.y0 <= centre <= host.y1 and host.column == piece.column
            if on_line and inside and shared > overlap:
                best, overlap = j, shared
        if best is not None:
            host = merged.get(best, rows[best])
            at = next((k for k, c in enumerate(host.chars) if c.x0 >= piece.x0), len(host.chars))
            chars = [*host.chars[:at], *piece.chars, *host.chars[at:]]
            merged[best] = _Row(chars, column=host.column)
            dropped.add(i)
    return [merged.get(i, row) for i, row in enumerate(rows) if i not in dropped]


def _insert_script(host: list[_Char], script: list[_Char]) -> list[_Char]:
    """`script`'s characters as one block, after the host glyphs that start before its
    right edge. A sub/superscript lands right after its base; the denominator of a small
    fraction lands after its numerator instead of being interleaved with it by x."""
    end = max(c.x1 for c in script)
    at = 0
    for i, c in enumerate(host):
        if not c.blank and c.x0 < end - 0.01:
            at = i + 1
    return [*host[:at], *script, *host[at:]]


# ============================================================================ margins


def _norm_margin(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _box_zone(box: BBox, height: float) -> str | None:
    centre = (box[1] + box[3]) / 2
    if centre <= height * MARGIN_BAND:
        return "top"
    if centre >= height * (1 - MARGIN_BAND):
        return "bottom"
    return None


def _margin_zone(row: _Row, height: float) -> str | None:
    return _box_zone(row.bbox, height)


def _is_page_number(text: str) -> bool:
    return bool(PAGE_NUMBER.match(text.strip(" -\u2013\u2014|")))


def _box_key(box: BBox) -> tuple[int, int, int, int]:
    return round(box[0]), round(box[1]), round(box[2]), round(box[3])


def _rotated_key(row: _Row) -> tuple[str, int, int]:
    """Same text near the same place: a watermark or a stamp repeated on every page."""
    return _norm_margin(row.clean), round((row.x0 + row.x1) / 40), round((row.y0 + row.y1) / 40)


@dataclass
class _MarginFilter:
    """What repeats on the pages and is not content: running headers and footers, page
    numbers, logos in the header/footer band, watermarks.

    Margin text is dropped when its digit-normalised text repeats in the same band, unless
    it stands out (larger than body text, or bold) and its digits change from page to page:
    "Problem 3 (15 points)" in 14 pt at the top of each page is a heading, "Notes - page 3"
    in 9 pt is a running header.
    """

    repeated: set[tuple[str, str]]  # (zone, normalised text)
    fixed: set[tuple[str, str]]  # ... of those, the ones whose text never changes
    rotated: set[tuple[str, int, int]]  # rotated text repeated at the same place
    figures: set[tuple[str, tuple[int, int, int, int]]]  # (zone, box) of band figures
    body: float

    def drops(self, row: _Row, height: float) -> bool:
        if row.vertical:
            if row.diagonal and row.size >= WATERMARK_RATIO * self.body:
                return True  # a large diagonal "DRAFT" across the page
            if _rotated_key(row) in self.rotated:
                return True
        zone = _margin_zone(row, height)
        if zone is None:
            return False
        text = row.clean
        if _is_page_number(text):
            return True
        key = (zone, _norm_margin(text))
        if key not in self.repeated:
            return False
        prominent = row.size >= HEADING_SIZE_RATIO * self.body or row.bold
        return key in self.fixed or not prominent

    def drops_figure(self, box: BBox, height: float) -> bool:
        zone = _box_zone(box, height)
        return zone is not None and (zone, _box_key(box)) in self.figures


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


def _band_figures(page: pymupdf.Page, paths: list[_Path]) -> set[tuple[str, tuple]]:
    """(zone, rounded box) of the figures a page has in its header/footer band."""
    rect, height = page.rect, page.rect.height
    band_paths = [p for p in paths if _box_zone(p.bbox, height) is not None]
    figures = _merge_figures(_raster_figures(page, rect) + _vector_figures(band_paths, [], rect))
    keys = set()
    for figure in figures:
        zone = _box_zone(figure.bbox, height)
        if zone is not None:
            keys.add((zone, _box_key(figure.bbox)))
    return keys


def _margin_filter(
    doc: pymupdf.Document,
    selected: list[int],
    frags: dict[int, list[_Row]],
    paths: dict[int, list[_Path]],
) -> _MarginFilter:
    reference = _reference_pages(doc.page_count, selected)
    counts: Counter[tuple] = Counter()
    texts: dict[tuple[str, str], set[str]] = {}
    sizes: Counter[float] = Counter()
    for number in reference:
        page = doc[number]
        if number not in frags:
            frags[number] = _page_rows(page)
        if number not in paths:
            paths[number] = _page_paths(page)
        height = page.rect.height
        seen: set[tuple] = set()
        for row in frags[number]:
            if row.vertical:
                seen.add(("rotated", _rotated_key(row)))
                continue
            zone = _margin_zone(row, height)
            if zone is None:
                for c in row.ink:
                    sizes[round(c.size * 2) / 2] += 1
                continue
            key = (zone, _norm_margin(row.clean))
            seen.add(("text", key))
            texts.setdefault(key, set()).add(row.clean)
        seen.update(("figure", key) for key in _band_figures(page, paths[number]))
        counts.update(seen)
    needed = max(2, -(-len(reference) // 2))  # at least half of the pages, and two
    repeated: dict[str, set] = {"text": set(), "rotated": set(), "figure": set()}
    for (kind, key), n in counts.items():
        if n >= needed:
            repeated[kind].add(key)
    body = max(sizes.items(), key=lambda kv: (kv[1], -kv[0]))[0] if sizes else 10.0
    return _MarginFilter(
        repeated=repeated["text"],
        fixed={key for key in repeated["text"] if len(texts[key]) == 1},
        rotated=repeated["rotated"],
        figures=repeated["figure"],
        body=max(body, 1.0),
    )


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
    events = sorted([(b[0], 1) for b in narrow] + [(b[2], -1) for b in narrow])
    # Strictest first: with crossings allowed, the gap would also take in the ragged ends
    # of the longest left-column lines. A gutter crossed by more than MAX_GUTTER_CROSSINGS
    # lines is no gutter (and the cap keeps a page of thousands of rows linear).
    for allowed in range(min(max(2, len(narrow) // 10), MAX_GUTTER_CROSSINGS) + 1):
        gutter = _gutter_with(narrow, events, allowed, left, width)
        if gutter is not None:
            return gutter
    return None


def _gutter_with(
    narrow: list[BBox],
    events: list[tuple[float, int]],
    allowed: int,
    left: float,
    width: float,
) -> tuple[float, float] | None:
    best: tuple[float, float] | None = None
    for gap0, gap1 in _sparse_intervals(events, allowed):
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


def _sparse_intervals(events: list[tuple[float, int]], allowed: int) -> list[tuple[float, float]]:
    """Maximal x-intervals covered by at most `allowed` boxes, from sorted box edges
    ((x0, +1) and (x1, -1) per box)."""
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
    """Widths of visual lines: boxes that overlap vertically by half the smaller height
    are one line. TeX splits a line with inline maths into pieces whose bottom edges
    differ (subscripts, fractions); counted apart, they made a column of full lines look
    like a column of short ones."""
    lines: list[list[float]] = []  # [x0, y0, x1, y1] per line, top to bottom
    for x0, y0, x1, y1 in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        if lines:
            line = lines[-1]
            overlap = min(y1, line[3]) - max(y0, line[1])
            if overlap >= 0.5 * min(y1 - y0, line[3] - line[1]):
                line[0], line[2] = min(line[0], x0), max(line[2], x1)
                line[1], line[3] = min(line[1], y0), max(line[3], y1)
                continue
        lines.append([x0, y0, x1, y1])
    return [x1 - x0 for x0, _, x1, _ in lines]


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
    body: float,
    context: Sequence[BBox] = (),
) -> list[int]:
    """Indices of `boxes` in reading order.

    Two columns: left column, then right column, segment by segment, where full-width
    items close a segment: items crossing the gutter, and narrow items at a section break
    across both columns (see `_section_break`). `context` boxes (e.g. figures while
    ordering text) shape the column extents but are not returned.
    """
    if gutter is None:
        return _top_down(boxes, range(len(boxes)))
    everything = list(boxes) + list(context)
    columns = [_column_of(b, gutter) for b in everything]
    sides = {
        side: _ColumnExtent(
            [b for b, c in zip(everything, columns, strict=True) if c == side], body
        )
        for side in ("L", "R")
    }
    for i, (box, column) in enumerate(zip(everything, columns, strict=True)):
        if column != "S" and _section_break(
            box, sides[column], sides["R" if column == "L" else "L"], body
        ):
            columns[i] = "S"
    spanning = sorted(
        (i for i in range(len(boxes)) if columns[i] == "S"),
        key=lambda i: (boxes[i][1], boxes[i][0]),
    )
    starts = [boxes[i][1] for i in spanning]
    segments: dict[tuple[int, str], list[int]] = {}
    for i in range(len(boxes)):
        if columns[i] != "S":
            segment = bisect.bisect_right(starts, boxes[i][1])
            segments.setdefault((segment, columns[i]), []).append(i)
    order: list[int] = []
    for segment in range(len(spanning) + 1):
        for side in ("L", "R"):
            order.extend(_top_down(boxes, segments.get((segment, side), [])))
        if segment < len(spanning):
            order.append(spanning[segment])
    return order


class _ColumnExtent:
    """Where one column has content: bridged bands, plus item tops and bottoms."""

    def __init__(self, boxes: list[BBox], body: float) -> None:
        self.bands = _bands(boxes, 2.0 * body)
        self.band_tops = [y0 for y0, _ in self.bands]
        self.tops = sorted(b[1] for b in boxes)
        self.bottoms = sorted(b[3] for b in boxes)

    def overlaps(self, y0: float, y1: float) -> bool:
        k = bisect.bisect_left(self.band_tops, y1) - 1  # last band starting above y1
        return k >= 0 and self.bands[k][1] > y0

    def first_top_from(self, y: float) -> float | None:
        k = bisect.bisect_left(self.tops, y)
        return self.tops[k] if k < len(self.tops) else None

    def last_bottom_to(self, y: float) -> float | None:
        k = bisect.bisect_right(self.bottoms, y)
        return self.bottoms[k - 1] if k else None


def _section_break(box: BBox, own: _ColumnExtent, other: _ColumnExtent, body: float) -> bool:
    """Is this narrow item really full-width: a heading at a break across both columns?

    The other column must have nothing beside it but more content below, the item must
    stand apart from its own column's content above (a block gap, not the next line of a
    paragraph), and both columns must resume below it at about the same height. A
    heading inside one column, beside a large gap in the other (space around an equation
    or before a Word "Heading 1"), fails the last test and stays in its column.
    """
    if other.overlaps(box[1], box[3]):
        return False
    other_below = other.first_top_from(box[3])
    if other_below is None:
        return False
    above = own.last_bottom_to(box[1] + 0.25 * body)
    if above is not None and box[1] - above < 0.8 * body:
        return False  # the next line of a paragraph, not a block on its own
    own_below = own.first_top_from(box[3] - 0.25 * body)
    return own_below is not None and other_below >= own_below - 0.5 * body


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
    """Top to bottom; items overlapping vertically by half their height go left to right.

    Only items of comparable height share a line: a tall box (a panel or figure with text
    inside or beside it) would otherwise pull everything it spans into one "line" and
    order it by x instead of top to bottom.
    """
    ordered = sorted(members, key=lambda i: (boxes[i][1], boxes[i][0]))
    lines: list[list[int]] = []
    for i in ordered:
        if lines:
            anchor = boxes[lines[-1][0]]
            box = boxes[i]
            overlap = min(anchor[3], box[3]) - max(anchor[1], box[1])
            smaller = min(anchor[3] - anchor[1], box[3] - box[1])
            larger = max(anchor[3] - anchor[1], box[3] - box[1])
            if smaller > 0 and overlap >= 0.5 * smaller and larger <= 3 * smaller:
                lines[-1].append(i)
                continue
        lines.append([i])
    return [i for line in lines for i in sorted(line, key=lambda i: boxes[i][0])]


# ============================================================================ maths


def _word_lengths(
    chars: list[_Char], classes: list[str], script: list[bool]
) -> tuple[list[int], list[bool]]:
    """For letters (class L), the length of the plain-letter word they belong to, and
    whether that word is a maths word (`sin`, `det`, `lim`, units such as `mol`).

    Italic letters run together are maths variables set in a text font (beamer's
    sans-serif maths: "at" in v = v0 + at), not a word, when the run is short (three
    letters at most) and the row reads as maths around them: it also has upright letters
    (prose with a formula in it), or it has a relation sign and no longer italic run (a
    formula on its own; an italic sentence has longer words). Each letter then counts
    on its own.
    """
    lengths = [0] * len(chars)
    maths_word = [False] * len(chars)
    upright = any(k == "L" and not c.italic for k, c in zip(classes, chars, strict=True))
    if not upright and any(c.c in _ROW_RELATIONS for c in chars):
        upright = _longest_italic_run(chars, classes, script) <= 3
    i = 0
    while i < len(chars):
        if classes[i] != "L":
            i += 1
            continue
        j = i
        while j < len(chars) and classes[j] == "L" and script[j] == script[i]:
            j += 1
        italic_variables = upright and j - i <= 3 and all(c.italic for c in chars[i:j])
        word = "".join(c.c for c in chars[i:j]).lower()
        for k in range(i, j):
            lengths[k] = 1 if italic_variables else j - i
            maths_word[k] = word in _MATH_WORDS
        i = j
    return lengths, maths_word


def _longest_italic_run(chars: list[_Char], classes: list[str], script: list[bool]) -> int:
    longest = run = 0
    for c, k, small in zip(chars, classes, script, strict=True):
        run = run + 1 if k == "L" and c.italic and not small else 0
        longest = max(longest, run)
    return longest


@dataclass
class _CharInfo:
    """Per-character maths evidence for one row."""

    classes: list[str]
    script: list[bool]
    words: list[int]
    maths_words: list[bool]

    @classmethod
    def of(cls, row: _Row) -> _CharInfo:
        classes = [_char_class(c) for c in row.chars]
        first = next((i for i, c in enumerate(row.chars) if not c.blank), None)
        if (
            first is not None
            and row.chars[first].c in _DOT_BULLETS
            and (first + 1 == len(row.chars) or row.chars[first + 1].blank)
        ):
            classes[first] = "P"  # "\u00b7 Measure the ramp": a bullet, not a product
        script = [row.is_script(c) for c in row.chars]
        words, maths_words = _word_lengths(row.chars, classes, script)
        return cls(classes, script, words, maths_words)

    def mathy(self, i: int) -> bool:
        kind = self.classes[i]
        if kind in "MDOB":
            return True
        return kind == "L" and (self.words[i] == 1 or self.script[i] or self.maths_words[i])


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
        variables = j - i > 1 and info.words[i] == 1  # italic maths letters, see _word_lengths
        if not variables and (
            (len(word) >= 3 and word not in _MATH_WORDS) or word in _PROSE_TWO_LETTER
        ):
            count += 1
        i = j
    return count


def _is_equation_number(text: str) -> bool:
    """ "(2.4)", "[3]", "Eq. 2.4" or a bare "2.4": what an equation number looks like."""
    return bool(EQUATION_NUMBER.match(text) or BARE_EQUATION_NUMBER.match(text))


def _classify_row(row: _Row) -> None:
    """Set row.kind: "math" (a display-equation line), "frag" (short maths-only piece such
    as a lone denominator, decided later) or "text"."""
    info = _CharInfo.of(row)
    text = row.clean
    fonts = sorted({c.font for c in row.ink})
    superscript = any(info.script)
    row.score, row.reasons = equation_score(text, fonts, superscript)
    ink = [i for i, c in enumerate(row.chars) if not c.blank]
    # Punctuation after maths ("v ." at the end of a displayed fraction) counts neither way.
    counted = [i for i in ink if row.chars[i].c not in ",.;:"]
    ratio = sum(info.mathy(i) for i in counted) / max(1, len(counted))
    prose = _prose_words(row, info)
    if row.vertical or sum(k == "C" for k in info.classes) > 0.5 * len(ink):
        row.kind = "text"
    elif row.marker is not None:
        row.kind = "text"  # a list item ("b. - 40 m/s"): maths in it is inline
    elif not counted:
        row.kind = "frag"  # a lone "," or "." belongs to the maths it ends, if any
    elif _is_equation_number(text):
        row.kind = "frag"  # "(2.4)", "Eq. 2.4", "2.4": maths only beside an equation
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
    regular: str | None = None  # `Element.regular_letters`


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
            inside = between_digits or (c == "," and depth > 0)
            accent = c in _ACCENTS and i + 1 < n and info.mathy(i + 1)  # the hat of x-hat
            if info.mathy(i) or chars[i].blank or inside or accent:
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
        if end > start and chars[end - 1].c in _TRAILING_OPERATORS and not info.script[end - 1]:
            end -= 1
            changed = True
        if end > start and chars[start].c in _INFIX_ONLY:  # "/h)] = 40": a broken piece
            start += 1
            changed = True
        text = "".join(c.c for c in chars[start:end])
        if end > start and text[-1] in ")]}" and _unbalanced(text, closing=True):
            end -= 1
            changed = True
        elif end > start and text[0] in "([{" and _unbalanced(text, closing=False):
            start += 1
            changed = True
        elif end > start and text.count("|") % 2 and (text[0] == "|" or text[-1] == "|"):
            # An unpaired bar at an edge ("|x" before a line break); "|x| < 1" keeps both.
            if text[0] == "|":
                start += 1
            else:
                end -= 1
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


# Spacing accents TeX sets as separate glyphs above a letter (hat, tilde, bar, dot,
# check, breve): inside an inline formula they do not end it.
_ACCENTS = frozenset("\u02c6\u02dc\u00af\u02d9\u00a8\u02c7\u02d8")
# Operators that cannot end an expression: a run ending in one was cut by prose
# ("b - proj b" broken at a word the run does not take in). Not "|": it closes |x|.
_TRAILING_OPERATORS = frozenset("+-*/=<>^_~\u2212\u00b1\u2213\u00d7\u00f7\u22c5")
# Operators that cannot start an expression: a run starting with one is the tail of an
# expression cut by a line break or by prose ("(50 km)/(60 km/h)] = 40" -> "/h)] = 40").
# Not "|" (it opens |x|, see `_trim_run`) and not "~" ("~5 m" is about five metres).
_INFIX_ONLY = frozenset("/*^_=<>\u2264\u2265\u2260\u2248\u2261\u00d7\u00f7\u22c5")


def _broken_brackets(text: str) -> bool:
    """A closing bracket before any opening one, or an opening one never closed."""
    depth = 0
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return True
    return depth != 0


def _inline_evidence(chars: list[_Char], info: _CharInfo, start: int, end: int) -> list[str]:
    """Why a run inside a paragraph row is maths, or [] when it is not.

    Without a maths character, a run must also be a whole expression: brackets that open
    and close in order (not "h)] = 40", a piece of a plain-text formula cut by a line
    break), and an operator that is not just a leading sign ("-49 m" is a quantity with
    an italic unit, not a formula).
    """
    idx = range(start, end)
    if any(info.classes[i] == "M" for i in idx):
        return ["math font or symbol"]
    if _broken_brackets("".join(chars[i].c for i in idx)):
        return []
    first = next(i for i in idx if not chars[i].blank)
    operator = any(
        (info.classes[i] == "O" and not (i == first and chars[i].c in "+-\u2212\u00b1"))
        or info.script[i]
        for i in idx
    )
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


# ============================================================================ bold letters
# With FEATURE_NOTATION_MEMORY on, a single bold letter in running text ("the projection
# of a vector **b**") becomes an inline equation that the equation step fixes without a
# model call (`pipeline.notation_memory.bold_letter_fix`). docs/layout.md "Bold letters
# in prose" has the rules and their measured precision and recall.

# What may touch a lone letter on either side: a space, the row's edge or punctuation. A
# letter, digit, hyphen or slash next to it makes it part of a word ("x-axis", "a2").
_LETTER_EDGES = frozenset(",.;:!?()[]'\"\u2019\u201c\u201d")
_LABEL_AFTER = frozenset(").:")  # "a)", "A." or "b:" starting a paragraph: a label
_SENTENCE_END = frozenset(".!?:")


@dataclass
class _BoldLetters:
    """Bold-letter state while one paragraph's rows are cut into pieces."""

    course: frozenset[str]  # the course's notation keys (`NotationEntry.canonical`)
    first: bool = True  # the next row is the paragraph's first
    before: str = ""  # the last non-blank character of the rows so far

    @classmethod
    def start(cls) -> _BoldLetters | None:
        """None unless the conversion has the feature on (`pipeline.options`)."""
        current = run_options.current()
        if not current.notation_memory:
            return None
        return cls(frozenset(e.canonical for e in current.notation))


def _bold_key(text: str) -> str | None:
    """The notation key of one bold letter (bold italic folds to bold), else None."""
    if bold_letter_latex(text) is None:
        return None
    return canonical_from_parts(base_char(text), char_variant(text))


def _bold_word(chars: list[_Char], start: int, end: int) -> bool:
    """Two bold letters in a row between `start` and `end`: a bold word."""
    run = 0
    for c in chars[start:end]:
        run = run + 1 if c.bold and c.c.isalpha() else 0
        if run >= 2:
            return True
    return False


def _bold_letter_runs(
    row: _Row, runs: list[tuple[int, int, list[str]]], state: _BoldLetters | None
) -> list[tuple[int, int, list[str]]]:
    """`runs` (`_inline_runs`) plus the row's lone bold letters, by character index.

    A maths run that is one bold letter (a bold omega in CMMIB, a maths bold code point)
    is marked `BOLD_LETTER_REASON` with `EVIDENCE_MATHS_FONT`. Any other bold Latin
    letter that stands alone (a space, the row's edge or punctuation on both sides) is
    a candidate, except: a bold word on either side (a bold phrase: "Part A", a bold
    sentence); a label ("(b)" anywhere, "a)" or "A." starting the paragraph); a capital
    starting a sentence ("A" is also the article), unless the course lists it. A
    candidate the course lists keeps `EVIDENCE_COURSE`; the others wait for evidence
    from the whole document (`_settle_bold_letters`). Unchanged when the feature is off.
    """
    if state is None:
        return runs
    chars = row.chars
    info = _CharInfo.of(row)
    covered: set[int] = set()
    out: list[tuple[int, int, list[str]]] = []
    for start, end, reasons in runs:
        covered.update(range(start, end))
        ink = [c for c in chars[start:end] if not c.blank]
        if len(ink) == 1 and _bold_key(_clean(ink[0].styled)) is not None:
            reasons = [BOLD_LETTER_REASON, EVIDENCE_MATHS_FONT]
        out.append((start, end, reasons))
    ink = [i for i, c in enumerate(chars) if not c.blank]
    tokens = _tokens(row)  # (start, end) of each run of non-blank characters
    starts = [start for start, _ in tokens]
    for pos, i in enumerate(ink):
        c = chars[i]
        if i in covered or not c.bold or info.classes[i] != "L" or info.script[i]:
            continue
        # Its token (split at blanks and gaps) is the letter and punctuation only, and
        # the tokens either side hold no bold word.
        token = bisect.bisect_right(starts, i) - 1
        start, end = tokens[token]
        if any(chars[k].c not in _LETTER_EDGES for k in range(start, end) if k != i):
            continue  # part of a word ("x-axis", "a2", "vectors")
        sides = [tokens[k] for k in (token - 1, token + 1) if 0 <= k < len(tokens)]
        if any(_bold_word(chars, start, end) for start, end in sides):
            continue
        before = chars[ink[pos - 1]].c if pos else state.before
        after = chars[ink[pos + 1]].c if pos + 1 < len(ink) else ""
        then = chars[ink[pos + 2]].c if pos + 2 < len(ink) else ""
        opening = state.first and pos == 0
        if (before in "([" and after in ")]") or (opening and after in _LABEL_AFTER):
            continue  # a label
        if after in ".)" and (not then or then.isupper()) and (not before or before in ".!?:"):
            continue  # a run-in label after a sentence: "... question. a. Find the sum."
        key = styled_char(c.c, "bold")
        in_course = key in state.course
        if c.c.isupper() and (opening or not before or before in _SENTENCE_END) and not in_course:
            continue  # a capital starting a sentence
        out.append((i, i + 1, [BOLD_LETTER_REASON, *([EVIDENCE_COURSE] if in_course else [])]))
    state.first = False
    if ink:
        state.before = chars[ink[-1]].c
    return sorted(out)


def _settle_bold_letters(pages: list[_PageBlocks]) -> None:
    """Keep a waiting bold letter (`_bold_letter_runs`) as maths only with evidence:
    the same bold letter in the document's maths (a display or inline equation,
    `EVIDENCE_DOCUMENT`), or, for a small letter, another bold letter that is maths in
    the same paragraph ("x or s is used instead of d", `EVIDENCE_NEARBY`). Any other
    goes back into the text as the plain letter it was."""

    def waiting(piece: str | _Inline) -> bool:
        return isinstance(piece, _Inline) and piece.reasons == [BOLD_LETTER_REASON]

    def bold_keys(text: str) -> set[str]:
        return {key for ch in text if (key := _bold_key(ch)) is not None}

    blocks = [b for page in pages for b in page.blocks]
    if not any(waiting(p) for b in blocks if b.kind in _TEXT_BLOCKS for p in b.pieces):
        return
    in_maths: set[str] = set()
    for block in blocks:
        if block.kind == Kind.EQUATION:
            in_maths |= bold_keys(block.text or "")
        elif block.kind in _TEXT_BLOCKS:
            for piece in block.pieces:
                if isinstance(piece, _Inline) and not waiting(piece):
                    in_maths |= bold_keys(piece.text)
    for block in blocks:
        if block.kind not in _TEXT_BLOCKS or not any(waiting(p) for p in block.pieces):
            continue
        for piece in block.pieces:
            if isinstance(piece, _Inline) and waiting(piece) and _bold_key(piece.text) in in_maths:
                piece.reasons = [BOLD_LETTER_REASON, EVIDENCE_DOCUMENT]
        nearby = any(
            isinstance(p, _Inline) and not waiting(p) and bold_keys(p.text) for p in block.pieces
        )
        pieces: list[str | _Inline] = []
        for piece in block.pieces:
            if isinstance(piece, _Inline) and waiting(piece):
                letter = base_char(piece.text)
                if nearby and letter.islower():
                    piece.reasons = [BOLD_LETTER_REASON, EVIDENCE_NEARBY]
                else:
                    pieces.append(letter)
                    continue
            pieces.append(piece)
        block.pieces = _tidy(pieces)


# ============================================================================ lists


@dataclass(frozen=True)
class _Marker:
    """The list marker that starts a row: what is printed and where the item text starts."""

    text: str  # as printed: "a.", "(iii)", "3)", "\u2022"
    ordered: bool
    end: int  # index in row.chars where the item text starts (after the marker)
    x: float  # left edge of the item text: the same for every item of one list level


@dataclass(frozen=True, eq=False)
class _MarkerCandidate:
    """A row that starts like a list item, before siblings confirm it."""

    row: _Row
    marker: _Marker
    family: tuple[str, str]  # ("decimal" | "letter", "{}." | "{})" | "({})") or ("bullet", glyph)
    readings: tuple[tuple[str, int], ...]  # (style, ordinal) readings of an ordered marker
    x0: float  # left edge of the marker itself
    sure: bool  # needs no sibling: a bullet glyph, a drawn bullet, or "3." before a capital


_ROMAN_STEPS = (
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"),
    (1, "i"),
)  # fmt: skip


def roman_value(text: str) -> int | None:
    """ "iv" -> 4, "XII" -> 12; None unless `text` is a canonical roman numeral below 400."""
    lower = text.lower()
    if not lower or any(c not in _ROMAN for c in lower):
        return None
    total = 0
    for i, c in enumerate(lower):
        value = _ROMAN[c]
        total += -value if i + 1 < len(lower) and _ROMAN[lower[i + 1]] > value else value
    canonical, rest = "", total
    for number, letters in _ROMAN_STEPS:
        while rest >= number:
            canonical, rest = canonical + letters, rest - number
    return total if 0 < total < 400 and canonical == lower else None


def marker_readings(body: str) -> tuple[tuple[str, int], ...]:
    """Possible (style, ordinal) readings of an ordered marker's body.

    "3" -> decimal 3; "b" -> lower-alpha 2; "iv" -> lower-roman 4; "i" is both lower-alpha
    9 and lower-roman 1 (its siblings decide); "C" is upper-alpha 3 and upper-roman 100.
    """
    if body.isdigit():
        return (("decimal", int(body)),)
    readings: list[tuple[str, int]] = []
    case = "lower" if body.islower() else "upper"
    if len(body) == 1 and body.isascii() and body.isalpha():
        readings.append((f"{case}-alpha", ord(body.lower()) - ord("a") + 1))
    roman = roman_value(body)
    if roman is not None and (body.islower() or body.isupper()):
        readings.append((f"{case}-roman", roman))
    return tuple(readings)


def _ordered_marker(text: str) -> tuple[str, str] | None:
    """(form, body) of an ordered marker token: "(iv)" -> ("({})", "iv"), "3." -> ("{}.", "3")."""
    match = _ORDERED_MARKER.match(text)
    if not match:
        return None
    if match["inner"] is not None:
        return "({})", match["inner"]
    return "{}" + match["sep"], match["plain"]


def _tokens(row: _Row) -> list[tuple[int, int]]:
    """(start, end) character ranges of the ink tokens of a row: split at blanks and at
    gaps of at least 0.3 x font size (a marker and its text in separate spans)."""
    tokens: list[tuple[int, int]] = []
    start = None
    chars = row.chars
    for i, c in enumerate(chars):
        if c.blank:
            if start is not None:
                tokens.append((start, i))
                start = None
            continue
        if start is not None and c.x0 - chars[i - 1].x1 >= 0.3 * row.size:
            tokens.append((start, i))
            start = None
        if start is None:
            start = i
    if start is not None:
        tokens.append((start, len(chars)))
    return tokens


def _marker_candidate(row: _Row, bullets: _DrawnBullets) -> _MarkerCandidate | None:
    """Does `row` start like a list item? A bullet glyph, an ordered marker ("a.", "(3)",
    "iv)", "3 ." as OpenStax prints it) or a drawn bullet just left of it, followed by
    the item's text within MAX_MARKER_GAP x font size."""
    if row.vertical:
        return None
    drawn = bullets.left_of(row)
    tokens = _tokens(row)
    if drawn is not None:
        if not tokens:
            return None
        start = tokens[0][0]
        marker = _Marker(drawn[1], False, start, row.chars[start].x0)
        return _MarkerCandidate(row, marker, ("bullet", drawn[1]), (), drawn[0], True)
    if len(tokens) < 2:
        return None
    chars = row.chars
    (m0, m1), rest = tokens[0], tokens[1:]
    first = chars[m0].c
    if m1 - m0 == 1 and first in _DASHES and _signed_value(chars[m0], chars[rest[0][0] :]):
        return None  # "- 40 m/s", "\u2013 5 \u00b0C": a negative value, not a dash bullet
    if m1 - m0 == 1 and (first in _BULLET_GLYPHS or first in _LINE_START_BULLETS):
        glyph = _clean(first)
        family: tuple[str, str] = ("bullet", glyph)
        readings: tuple[tuple[str, int], ...] = ()
        sure = first in _BULLET_GLYPHS
        text = glyph
    else:
        text = "".join(c.c for c in chars[m0:m1])
        if text.isdigit() and "".join(c.c for c in chars[slice(*rest[0])]) in (".", ")"):
            m1 = rest[0][1]  # "3 ." (OpenStax prints the number and its full stop apart)
            rest = rest[1:]
            text += chars[m1 - 1].c
        parsed = _ordered_marker(text)
        if parsed is None or not rest:
            return None
        form, body = parsed
        readings = marker_readings(body)
        if not readings:
            return None
        family = ("decimal" if body.isdigit() else "letter", form)
        following = chars[rest[0][0]].c
        sure = body.isdigit() and (following.isupper() or following in "(\"'\u201c")
    start = rest[0][0]
    if chars[start].x0 - chars[m1 - 1].x1 > MAX_MARKER_GAP * row.size:
        return None
    if not any(c.c.isalnum() or c.math for c in chars[start:]):
        return None
    marker = _Marker(text, family[0] != "bullet", start, chars[start].x0)
    return _MarkerCandidate(row, marker, family, readings, chars[m0].x0, sure)


class _ColumnRows:
    """One page's rows per column, top to bottom: the lines above and below a row."""

    def __init__(self, rows: Sequence[_Row]) -> None:
        self.columns: dict[str, list[_Row]] = {}
        for row in rows:
            if not row.vertical:
                self.columns.setdefault(row.column, []).append(row)
        for column in self.columns.values():
            column.sort(key=lambda r: r.baseline)
        self.baselines = {k: [r.baseline for r in v] for k, v in self.columns.items()}

    def neighbour(self, row: _Row, step: int) -> _Row | None:
        """The nearest row above (step -1) or below (+1) in `row`'s column that overlaps
        it in x."""
        rows, baselines = self.columns.get(row.column, []), self.baselines.get(row.column, [])
        if step < 0:
            k = bisect.bisect_left(baselines, row.baseline - 0.3 * row.size) - 1
            span = range(k, -1, -1)
        else:
            k = bisect.bisect_right(baselines, row.baseline + 0.3 * row.size)
            span = range(k, len(rows))
        return next((rows[i] for i in span if rows[i].x0 < row.x1 and rows[i].x1 > row.x0), None)

    def wraps_into(self, candidate: _MarkerCandidate, ctx: _Context) -> bool:
        """Is the candidate's row the next line of the paragraph above, which happens to
        start "2. The"? The line above is at the usual pitch and size, starts as far left
        as the marker or further right, and does not end a sentence or clause; and the
        line below does not hang at the item text's indent."""
        row = candidate.row
        above = self.neighbour(row, -1)
        if above is None:
            return False
        size = row.size
        pitch = row.baseline - above.baseline
        limit = max(1.3 * ctx.pitch * size / ctx.body, 1.25 * size)
        if abs(above.size - size) > max(1.0, 0.12 * size) or not 0.5 * size <= pitch <= limit:
            return False
        if candidate.x0 > above.x0 + 0.5 * size:
            return False  # the marker is indented past the text above
        if above.clean[-1:] in tuple(".:;!?"):
            return False
        below = self.neighbour(row, 1)
        hanging = (
            below is not None
            and below.baseline - row.baseline <= limit
            and abs(below.x0 - candidate.marker.x) <= 1.5
            and candidate.marker.x - candidate.x0 > 1.5
        )
        return not hanging


_DASHES = frozenset("-\u2013\u2014")  # line-start bullets that are also how minus is typed


def _signed_value(dash: _Char, after: list[_Char]) -> bool:
    """Is a line-start dash the sign of the value after it? It is when the value starts
    with a digit or a decimal point ("- 40 m/s"; Word turns a spaced hyphen into "\u2013"),
    or when the dash is set in a maths font (a minus sign)."""
    text = "".join(c.c for c in after).lstrip()
    return dash.math or text[:1].isdigit() or (text[:1] in ".," and text[1:2].isdigit())


class _DrawnBullets:
    """Small drawn shapes that can be list bullets (Chromium draws CSS disc markers as
    paths), indexed by vertical centre."""

    def __init__(self, paths: Sequence[_Path], body: float) -> None:
        marks = []
        for path in paths:
            x0, y0, x1, y1 = path.bbox
            if path.kind == "shape" and 0.5 <= max(x1 - x0, y1 - y0) <= 1.2 * body:
                glyph = ("\u2022" if path.curved else "\u25aa") if path.filled else "\u25e6"
                marks.append(((y0 + y1) / 2, path.bbox, glyph))
        marks.sort(key=lambda m: m[0])
        self.marks = marks
        self.centres = [m[0] for m in marks]

    def left_of(self, row: _Row) -> tuple[float, str] | None:
        """(left edge, glyph) of a bullet drawn just left of `row` at its height."""
        lo = bisect.bisect_left(self.centres, row.y0)
        hi = bisect.bisect_right(self.centres, row.y1)
        for _, (x0, y0, x1, y1), glyph in self.marks[lo:hi]:
            side = max(x1 - x0, y1 - y0)
            if side <= DRAWN_BULLET_RATIO * row.size and 0 <= row.x0 - x1 <= 2.5 * row.size:
                return x0, glyph
        return None


def _list_markers(
    rows_by_page: dict[int, list[_Row]], paths: dict[int, list[_Path]], ctx: _Context
) -> None:
    """Set `row.marker` on every row that starts a list item, in all analysed pages.

    A bullet glyph or a drawn bullet is enough, and so is a number before a capital
    letter ("3. Measure"), unless that row is the next line of a wrapped paragraph
    (`_ColumnRows.wraps_into`). Letters, roman numerals, bare numbers and line-start
    dashes need a sibling: another row with the same kind of marker, the next or
    previous value ("b." for "a."; the same glyph for a dash) and the marker or the item
    text starting within MARKER_ALIGN in x, anywhere in the document (lists cross
    pages). "A ball rolls", "2.3 Speed", "2.5 metres", a lone "B. Smith", "... is
    exactly" / "2. The heavier ball ...", a spaced minus ("- 40 m/s") or a sentence
    wrapped onto a new line after a dash do not start lists.
    """
    candidates: list[_MarkerCandidate] = []
    for number, rows in rows_by_page.items():
        bullets = _DrawnBullets(paths.get(number, []), ctx.body)
        found = [c for row in rows if (c := _marker_candidate(row, bullets)) is not None]
        if any(c.sure and c.readings for c in found):
            columns = _ColumnRows(rows)
            found = [
                replace(c, sure=False)
                if c.sure and c.readings and columns.wraps_into(c, ctx)
                else c
                for c in found
            ]
        candidates.extend(found)
    index: dict[tuple, tuple[list[float], list[float]]] = {}
    for candidate in candidates:
        for reading in candidate.readings or (("bullet", 0),):
            lists = index.setdefault((candidate.family, reading), ([], []))
            lists[0].append(candidate.x0)
            lists[1].append(candidate.marker.x)
    for lists in index.values():
        lists[0].sort()
        lists[1].sort()

    def aligned(key: tuple, candidate: _MarkerCandidate, needed: int) -> bool:
        lists = index.get(key)
        if lists is None:
            return False
        for values, x in ((lists[0], candidate.x0), (lists[1], candidate.marker.x)):
            near = bisect.bisect_right(values, x + MARKER_ALIGN)
            if near - bisect.bisect_left(values, x - MARKER_ALIGN) >= needed:
                return True
        return False

    for candidate in candidates:
        if candidate.readings:
            confirmed = any(
                aligned((candidate.family, (style, value + step)), candidate, 1)
                for style, value in candidate.readings
                for step in (-1, 1)
            )
        else:  # the same bullet glyph on another row (this one counts itself)
            confirmed = aligned((candidate.family, ("bullet", 0)), candidate, 2)
        if candidate.sure or confirmed:
            candidate.row.marker = candidate.marker


def _list_levels(blocks: Iterable[_Block], tolerance: float) -> dict[int, int]:
    """Nesting level (1 = top) per list-item block id, from the indent of the item text.

    Consecutive items in reading order form one list (display equations inside an item
    do not end it; anything else does). A deeper indent opens a level, a shallower one
    closes levels until one matches. Indents are measured from the left edge of the
    item's column (`_Block.origin`), so a list that goes on in the next column or on a
    page with another margin keeps its levels.
    """
    levels: dict[int, int] = {}
    stack: list[float] = []
    for block in blocks:
        if block.marker is None:
            if block.kind != Kind.EQUATION:
                stack = []
            continue
        x = block.marker.x - block.origin
        while stack and x < stack[-1] - tolerance:
            stack.pop()
        if not stack or x > stack[-1] + tolerance:
            stack.append(x)
        levels[id(block)] = len(stack)
    return levels


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
    width_pt, height_pt = page.rect.width, page.rect.height
    paths: list[_Path] = []
    for drawing in page.get_cdrawings():
        stroked = _visible(drawing.get("color"), drawing.get("stroke_opacity"))
        filled = _visible(drawing.get("fill"), drawing.get("fill_opacity"))
        if not (stroked or filled):
            continue  # white boxes and invisible paths
        box = display.box(drawing["rect"])
        if box[2] < 0 or box[3] < 0 or box[0] > width_pt or box[1] > height_pt:
            continue  # outside the CropBox: not visible on the page
        items = drawing.get("items") or []
        segments = []
        curved = False
        straight = 0.0  # length of horizontal and vertical lines
        for item in items:
            if item[0] == "l":
                (px, py), (qx, qy) = display.point(*item[1]), display.point(*item[2])
                segments.append((px, py, qx, qy))
                if abs(px - qx) > 0.5 and abs(py - qy) > 0.5:
                    curved = True
                else:
                    straight += abs(px - qx) + abs(py - qy)
            elif item[0] == "c":
                curved = True
        width, height = box[2] - box[0], box[3] - box[1]
        if curved and straight >= 0.6 * 2 * (width + height):
            curved = False  # a rounded rectangle (a callout box): its corners are not a plot
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


class _RowIndex:
    """Rows sorted by top edge, for finding rows within a vertical window quickly."""

    def __init__(self, rows: list[_Row]) -> None:
        self.rows = sorted(rows, key=lambda r: r.y0)
        self.tops = [r.y0 for r in self.rows]
        self.tallest = max((r.height for r in self.rows), default=0.0)
        self.largest = max((r.size for r in self.rows), default=0.0)  # font size

    def near(self, y0: float, y1: float) -> list[_Row]:
        """Rows whose vertical extent meets [y0, y1]."""
        lo = bisect.bisect_left(self.tops, y0 - self.tallest)
        hi = bisect.bisect_right(self.tops, y1)
        return [r for r in self.rows[lo:hi] if r.y1 >= y0]


def _rule_rows(rule: _Path, index: _RowIndex) -> list[_Row]:
    """Maths rows touching a rule: above/below a fraction bar or under a radical."""
    x0, y0, x1, y1 = rule.bbox
    near = []
    reach = 0.8 * index.largest  # the furthest a touching row can be (see below)
    for row in index.near(y0 - reach, y1 + reach):
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

    Attachment spreads breadth-first from the maths rows through a vertical index, so a
    page full of short numeric rows costs about n log n, not a pass per row.
    """
    mathish = [r for r in rows if r.kind in ("math", "frag") and not r.absorbed]
    index = _RowIndex(mathish)
    attached = {id(r) for r in mathish if r.kind == "math"}
    used: list[_Path] = []
    for rule in rules:
        near = _rule_rows(rule, index)
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
    queue = deque(r for r in mathish if id(r) in attached)
    reach = 0.6 * index.largest  # _math_neighbours' widest vertical gap
    while queue:
        source = queue.popleft()
        for row in index.near(source.y0 - reach, source.y1 + reach):
            if id(row) not in attached and _math_neighbours(row, source):
                attached.add(id(row))
                queue.append(row)
    for row in mathish:
        if row.kind == "frag":
            row.kind = "math" if id(row) in attached else "text"
    return used


def _math_neighbours(row: _Row, other: _Row) -> bool:
    """Does fragment `row` belong with maths row `other`? Vertically adjacent and
    overlapping in x, or an equation number beside it (`_number_beside`)."""
    size = max(row.size, other.size)
    gap_y = max(row.y0 - other.y1, other.y0 - row.y1)
    gap_x = max(row.x0 - other.x1, other.x0 - row.x1)
    if gap_y <= 0.6 * size and gap_x <= 1.0 * size and gap_y > -0.5 * min(row.height, other.height):
        return True
    return row.column == other.column and _number_beside(row, other.bbox, size)


def _number_beside(row: _Row, box: BBox, size: float) -> bool:
    """Is `row` an equation number for the maths in `box`?

    Its vertical centre must be within half a font size of the maths: numbers sit on the
    baseline of a one-line equation, or halfway down a fraction (between numerator and
    denominator, on neither line). "(2.4)" or "Eq. 2.4" may sit on either side; a bare
    "2.4" counts only to the right of the maths, with a gap.
    """
    text = row.clean
    bare = bool(BARE_EQUATION_NUMBER.match(text))
    if not bare and not EQUATION_NUMBER.match(text):
        return False
    centre = (row.y0 + row.y1) / 2
    if not box[1] - 0.5 * size <= centre <= box[3] + 0.5 * size:
        return False
    return not bare or row.x0 - box[2] >= 1.0 * size


# ============================================================================ display equations

# Signs that continue the line above when a maths line starts with them ("= 250 m/s" under
# "v = ...", "+ c" under a long sum).
_CONTINUATION_SIGNS = _ROW_RELATIONS | frozenset("+-\u2212\u00b1\u2213\u00d7\u22c5")
MAX_LINE_GAP = 0.8  # x font size: widest vertical gap between lines of one display equation
MAX_COLUMN_GAP = 6.0  # x font size: widest gap between the columns of an aligned row


def _split_labels(rows: list[_Row]) -> list[_Row]:
    """Cut an equation number off the end of a maths row into a row of its own.

    `_merge_baselines` joins pieces of a line up to 4 x font size apart, so an equation
    number set close to its equation ("= 2000 m - 525 m ,   2.8") arrives as part of the
    row. It is cut off when the last token reads as an equation number (or the last two
    do: "Eq. 2.4", "Equation (2.4)"), stands at least one font size clear of the rest,
    and the rest has maths in it (or is only the comma that ends the maths) and does not
    end in a relation or operator ("x =   2.4" keeps its right-hand side). Whether it is
    an equation number is decided later, by where it sits (`_equation_regions`).
    """
    out: list[_Row] = []
    for row in rows:
        tokens = _tokens(row)
        if row.vertical or len(tokens) < 2:
            out.append(row)
            continue
        start = tokens[-1][0]
        label = _clean("".join(c.c for c in row.chars[start:]))
        if len(tokens) >= 3:
            prefixed = _clean("".join(c.c for c in row.chars[tokens[-2][0] :]))
            if EQUATION_NUMBER.match(prefixed):  # "Eq. 2.4": the prefix goes with it
                start, label = tokens[-2][0], prefixed
        before = [c for c in row.chars[:start] if not c.blank]
        if (
            _is_equation_number(label)
            and before
            and row.chars[start].x0 - before[-1].x1 >= 1.0 * row.size
            and before[-1].c not in _CONTINUATION_SIGNS
            and (
                any(c.math or c.c in _ROW_RELATIONS or c.c in _SIGNS for c in before)
                or all(c.c in ",.;:" for c in before)  # the comma that ends the maths
            )
        ):
            out.append(_Row(row.chars[:start], column=row.column, marker=row.marker))
            out.append(_Row(row.chars[start:], column=row.column))
        else:
            out.append(row)
    return out


@dataclass(eq=False)
class _Region:
    """One display equation: the maths rows that belong together, the rules it uses
    (fraction bars, radicals) and the rows of its equation number, kept apart."""

    rows: list[_Row] = field(default_factory=list)
    rules: list[_Path] = field(default_factory=list)
    labels: list[_Row] = field(default_factory=list)
    box: BBox = (0.0, 0.0, 0.0, 0.0)  # rows and rules, while lines are being stacked


@dataclass(eq=False)
class _Line:
    """Maths rows of one line of a display equation: a main row, the numerators and
    denominators of its fractions, and the columns of an aligned row."""

    rows: list[_Row]
    rules: list[_Path]
    labels: list[_Row] = field(default_factory=list)

    @cached_property
    def box(self) -> BBox:
        return _union([*(r.bbox for r in self.rows), *(p.bbox for p in self.rules)])

    @cached_property
    def size(self) -> float:
        return max(r.size for r in self.rows)

    @cached_property
    def ink(self) -> list[_Char]:
        return sorted((c for r in self.rows for c in r.ink), key=lambda c: c.x0)

    @cached_property
    def relations(self) -> list[_Char]:
        """Relation signs at full size (not in a sub/superscript), left to right."""
        return [
            c
            for r in self.rows
            for c in r.ink
            if c.c in _ROW_RELATIONS and not r.is_script(c) and c.size >= 0.75 * self.size
        ]

    @cached_property
    def complete(self) -> bool:
        """An equation on its own: a relation with something on both sides."""
        ink = self.ink
        return any(
            ink[0].x1 <= rel.x0 + 0.5 and ink[-1].x0 >= rel.x1 - 0.5 for rel in self.relations
        )

    @cached_property
    def continues(self) -> bool:
        """Starts with a relation or operator: the next line of the equation above."""
        return self.ink[0].c in _CONTINUATION_SIGNS

    @cached_property
    def relation_x(self) -> float | None:
        return min((c.x0 for c in self.relations), default=None)


def _same_line(a: _Row, b: _Row) -> bool:
    """Two maths rows side by side on one line, in the same column: on one baseline at
    most MAX_COLUMN_GAP font sizes apart (aligned columns, an operator in its own span),
    or a lone punctuation mark next to a row it overlaps vertically (Chromium sets the
    full stop after a fraction on a baseline of its own)."""
    if a.column != b.column:
        return False
    size = max(a.size, b.size)
    gap = max(a.x0 - b.x1, b.x0 - a.x1)
    if abs(a.baseline - b.baseline) <= 0.4 * size and gap <= MAX_COLUMN_GAP * size:
        return True
    lone = a if _punctuation_only(a) else b if _punctuation_only(b) else None
    if lone is None or gap > 1.5 * size:
        return False
    other = b if lone is a else a
    return min(lone.y1, other.y1) - max(lone.y0, other.y0) >= 0.5 * lone.height


def _punctuation_only(row: _Row) -> bool:
    return all(c.c in ",.;:" for c in row.ink)


def _equation_regions(rows: list[_Row], rules: list[_Path]) -> list[_Region]:
    """Group the page's maths rows into display equations; sets `row.region`.

    1. Lines: rows touching one fraction bar or radical (numerator, denominator,
       radicand) and the main row at the bar's height, plus rows side by side on one
       baseline, are one line. Equation numbers ("2.4", "(3)") are not in any line:
       each goes to the line it sits beside (`_number_beside`), nearest centre first.
    2. Equations: a line joins the equation above it when the gap is at most
       MAX_LINE_GAP x font size, they overlap horizontally, at most one of them has an
       equation number, and the line either continues the one above (starts with "="
       or an operator), or one of the two is not a whole equation (no relation with
       something on both sides), or both are whole equations with their first relations
       aligned (the rows of an aligned derivation). Two whole equations stacked close
       together but centred on their own ("time = d / v" over "d = v t") stay apart.
    """
    maths = [r for r in rows if r.kind == "math" and not r.absorbed and not r.vertical]
    if not maths:
        return []
    parent = list(range(len(maths)))
    position = {id(r): i for i, r in enumerate(maths)}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index = _RowIndex(maths)
    on_rule: dict[int, list[_Path]] = {}
    for rule in rules:
        near = _rule_rows(rule, index)
        if not near:
            continue
        middle = (rule.bbox[1] + rule.bbox[3]) / 2
        level = [
            r
            for r in index.near(middle, middle)
            if r.y0 <= middle <= r.y1
            and r.column == near[0].column
            and max(r.x0 - rule.bbox[2], rule.bbox[0] - r.x1) <= 4 * r.size
            and not (_is_equation_number(r.clean) and r.x0 - rule.bbox[2] >= r.size)
        ]
        members = [*near, *(r for r in level if r not in near)]
        for row in members:
            on_rule.setdefault(id(row), []).append(rule)
            parent[find(position[id(row)])] = find(position[id(members[0])])
    labels = [r for r in maths if id(r) not in on_rule and _is_equation_number(r.clean)]
    label_ids = {id(r) for r in labels}
    body = sorted((r for r in maths if id(r) not in label_ids), key=lambda r: r.baseline)
    window = index.largest  # rows of one line: a shared baseline, or a lone full stop
    for i, row in enumerate(body):
        for other in body[i + 1 :]:
            if other.baseline - row.baseline > window:
                break
            if _same_line(row, other):
                parent[find(position[id(other)])] = find(position[id(row)])
    groups: dict[int, list[_Row]] = {}
    for row in body:
        groups.setdefault(find(position[id(row)]), []).append(row)
    lines = [
        _Line(members, list({id(p): p for r in members for p in on_rule.get(id(r), [])}.values()))
        for members in groups.values()
    ]
    for label in labels:
        centre = (label.y0 + label.y1) / 2
        beside = [
            line
            for line in lines
            if line.rows[0].column == label.column
            and _number_beside(label, line.box, line.size)
            and line.ink[-1].c not in _CONTINUATION_SIGNS  # "x =   2.4": its right-hand side
        ]
        if beside:
            nearest = min(beside, key=lambda ln: abs((ln.box[1] + ln.box[3]) / 2 - centre))
            nearest.labels.append(label)
        else:
            lines.append(_Line([label], []))  # a number inside the maths, not beside it
    return _stack_lines(lines)


def _joins_line(above: _Line, box: BBox, line: _Line) -> bool:
    """Does `line` continue the display equation whose last line is `above` (box `box`)?

    A line that starts with "=" or an operator may sit a little further down (half as
    far again): nothing else starts a line that way. A line whose middle is above the
    bottom of the equation sits beside it, not under it: "f(t) =" level with the middle
    row of a cases brace, whose rows are whole equations ("1 if t > 0") of their own.
    """
    size = max(above.size, line.size)
    reach = MAX_LINE_GAP * size * (1.5 if line.continues else 1.0)
    if line.box[1] - box[3] > reach or line.rows[0].column != above.rows[0].column:
        return False
    if line.box[0] > box[2] + size or line.box[2] < box[0] - size:
        return False
    if above.labels and line.labels:
        return False  # two numbered equations
    if line.continues or not (above.complete and line.complete):
        return True
    if (line.box[1] + line.box[3]) / 2 < box[3]:
        return True  # beside the equation: rows of a brace, a matrix or an array
    if min(above.size, line.size) <= SCRIPT_RATIO * size:
        return True  # limits under a big operator ("i = 1" under a sum) or over it
    x0, x1 = above.relation_x, line.relation_x
    return x0 is not None and x1 is not None and abs(x0 - x1) <= 0.3 * size


def _stack_lines(lines: list[_Line]) -> list[_Region]:
    """Join lines into display equations top to bottom (see `_equation_regions`)."""
    regions: list[_Region] = []
    last: list[_Line] = []  # the lowest line of each region so far
    active: list[int] = []  # regions whose bottom is still within reach
    for line in sorted(lines, key=lambda ln: (ln.box[1], ln.box[0])):
        reach = line.box[1] - 2 * MAX_LINE_GAP * line.size  # beyond any _joins_line reach
        active = [k for k in active if regions[k].box[3] >= reach]
        host = next(
            (k for k in reversed(active) if _joins_line(last[k], regions[k].box, line)), None
        )
        if host is None:
            regions.append(_Region(box=line.box))
            last.append(line)
            active.append(len(regions) - 1)
            host = len(regions) - 1
        region = regions[host]
        region.rows.extend(line.rows)
        region.rules.extend(p for p in line.rules if p not in region.rules)
        region.labels.extend(line.labels)
        region.box = _union([region.box, line.box])
        if any(c.c.isalnum() for c in line.ink) or not last[host].ink:
            last[host] = line  # a stray mark does not stand for the line above
    for region in regions:
        for row in [*region.rows, *region.labels]:
            row.region = region
    return regions


def _contiguous_regions(ordered: list[_Row]) -> list[_Row]:
    """Reading order with each display equation's rows together, at the place of its
    first row, and equation numbers taken out of the flow."""
    members: dict[int, list[_Row]] = {}
    for row in ordered:
        if row.region is not None and row not in row.region.labels:
            members.setdefault(id(row.region), []).append(row)
    out: list[_Row] = []
    for row in ordered:
        region = row.region
        if region is None:
            out.append(row)
        elif id(region) in members:
            out.extend(members.pop(id(region)))
    return out


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
    index = _RowIndex([r for r in rows if not r.vertical])
    for group in _overlapping(_cluster([p.bbox for p in paths], DRAWING_CLUSTER_GAP), paths):
        members = [paths[i] for i in group]
        if all(p.kind in ("hrule", "vrule") for p in members):
            continue  # separators, underlines, table grids
        x0, y0, x1, y1 = _union(p.bbox for p in members)
        box = (max(x0, 0.0), max(y0, 0.0), min(x1, page_rect.width), min(y1, page_rect.height))
        width, height = box[2] - box[0], box[3] - box[1]  # the visible part only
        if width < MIN_DRAWING_PT or height < MIN_DRAWING_PT or width * height < 1200:
            continue
        if width >= 0.85 * page_rect.width and height >= 0.85 * page_rect.height:
            continue  # page border
        inside = [r for r in rows if _centre_inside(r.bbox, box)]
        # Curves that matter: a plotted line, or many markers; not one tick icon in a table,
        # nor the round bullets of a list in a panel.
        curves = [p for p in members if p.curved and not _bullet_mark(p, index)]
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


def _bullet_mark(path: _Path, index: _RowIndex) -> bool:
    """A small mark just left of a text row, at its height: a drawn list bullet."""
    x0, y0, x1, y1 = path.bbox
    centre = (y0 + y1) / 2
    for row in index.near(centre, centre):
        if (
            max(x1 - x0, y1 - y0) <= 0.8 * row.size
            and row.y0 <= centre <= row.y1
            and 0 <= row.x0 - x1 <= 2.5 * row.size
        ):
            return True
    return False


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


def _absorb_labels(figure: _Figure, rows: list[_Row], ctx: _Context) -> bool:
    """Give a figure its labels; False when the "figure" is really a background.

    Short text inside a figure, and short labels just outside it, belong to it. Running
    text never does: lines of a paragraph inside a figure's box (text set on a backdrop,
    in a panel, over a watermark logo) stay in the reading flow, and so does a title-sized
    line. A raster image with such text on top of it is a background or a watermark, not
    a figure: it is dropped (nothing absorbed) so its text keeps its place in the outline.
    """
    x0, y0, x1, y1 = figure.bbox
    inside: list[_Row] = []
    near: list[_Row] = []
    for row in rows:
        if row.absorbed or CAPTION.match(row.clean):
            continue
        rx0, ry0, rx1, ry1 = row.bbox
        area = max(1e-6, (rx1 - rx0) * (ry1 - ry0))
        inter = max(0.0, min(rx1, x1) - max(rx0, x0)) * max(0.0, min(ry1, y1) - max(ry0, y0))
        # Display maths is not a label, but a lone variable is ("x" at the end of an axis).
        short = len(row.clean) <= 25 and (row.kind != "math" or len(row.ink) <= 3)
        reach = 0.6 * row.size
        beside_y = min(ry1, y1) - max(ry0, y0) >= 0.5 * (ry1 - ry0)
        beside_x = min(rx1, x1) - max(rx0, x0) >= 0.5 * (rx1 - rx0)
        # Within reach of an edge, or across it (a tick label half inside the box).
        near_x = beside_y and max(rx0 - x1, x0 - rx1) <= reach
        near_y = beside_x and max(ry0 - y1, y0 - ry1) <= reach
        if inter >= 0.6 * area:
            inside.append(row)
        elif short and (near_x or near_y):
            near.append(row)
    running = _running_text(inside, ctx)
    if figure.images and not figure.paths and running:
        return False
    for row in [*inside, *near]:
        if id(row) not in running:
            row.absorbed = True
            figure.labels.append(row)
    if figure.labels:
        figure.bbox = _union([figure.bbox, *(r.bbox for r in figure.labels)])
    return True


def _running_text(rows: list[_Row], ctx: _Context) -> set[int]:
    """Ids of rows that are not labels: lines of a paragraph (a prose line that the next
    or previous prose line continues) and title-sized lines of several words."""
    found: set[int] = set()
    prose = []
    for row in rows:
        if row.kind != "text" or row.vertical:
            continue
        words = _prose_words(row, _CharInfo.of(row))
        if words >= 2 and row.size >= HEADING_SIZE_RATIO * ctx.body:
            found.add(id(row))
        if words >= 3:
            prose.append(row)
    prose.sort(key=lambda r: r.baseline)
    for i, above in enumerate(prose):
        for below in prose[i + 1 :]:
            if below.baseline - above.baseline > 2.0 * max(above.size, below.size):
                break
            if below.column == above.column and _continues(
                above, below, _Run([above], "text", True), ctx
            ):
                found.update((id(above), id(below)))
    return found


# ============================================================================ blocks


@dataclass
class _Context:
    body: float  # body font size (pt)
    pitch: float  # typical baseline-to-baseline distance of body text (pt)


@dataclass(eq=False)
class _Run:
    """Consecutive rows (in reading order) of one paragraph, heading or display equation.

    The extents are kept up to date as rows are added, so growing a run is O(1) per row
    (a crafted page with thousands of rows in one run must not be quadratic).
    """

    rows: list[_Row]
    kind: str  # "math" | "text"
    block_start: bool  # the run starts after a geometric break, not just a style change
    block_end: bool = True
    box: BBox = (0.0, 0.0, 0.0, 0.0)  # union of the rows' boxes
    margin: float = 0.0  # left edge of the rows after the first (the first's when alone)

    def __post_init__(self) -> None:
        rows, self.rows = self.rows, []
        for row in rows:
            self.add(row)

    def add(self, row: _Row) -> None:
        if not self.rows:
            self.box, self.margin = row.bbox, row.x0
        else:
            self.box = _union([self.box, row.bbox])
            self.margin = row.x0 if len(self.rows) == 1 else min(self.margin, row.x0)
        self.rows.append(row)


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
    marker: _Marker | None = None  # list items: the marker of the first row
    origin: float = 0.0  # list items: left edge of their column's text (`_column_origins`)
    label: str | None = None  # display equations: the equation number beside them
    regular: str | None = None  # display equations: `Element.regular_letters`
    table: TableGrid | None = None  # tables: the grid built from the PDF (`_table_blocks`)


def _dominant_size(rows: Sequence[_Row]) -> float:
    counts: Counter[float] = Counter()
    for row in rows:
        for c in row.ink:
            counts[round(c.size * 2) / 2] += 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if counts else 0.0


def _heading_sized(row: _Row, ctx: _Context) -> bool:
    return row.size >= HEADING_SIZE_RATIO * ctx.body


def _continues(prev: _Row, row: _Row, run: _Run, ctx: _Context) -> bool:
    """Is `row` the next line of the paragraph `run`, whose last row is `prev`?"""
    size = prev.size
    if abs(row.size - size) > max(1.0, 0.12 * size):
        return False
    pitch = row.baseline - prev.baseline
    limit = max(1.3 * ctx.pitch * size / ctx.body, 1.25 * size)
    if not 0.5 * size <= pitch <= limit:
        return False
    left, right = run.box[0], run.box[2]
    if row.x0 > right or row.x1 < left or row.x0 < left - 2 * size:
        return False
    if run.rows[0].marker is not None and row.x0 < left - 0.5 * size:
        return False  # a list item goes on at its marker or its text, never further left
    if row.x0 - run.margin >= 0.8 * size and prev.x1 < right - 1.5 * size:
        return False  # first-line indent after a short line: a new paragraph
    return row.marker is None and not CAPTION.match(row.clean)


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
            if run.kind == "math" and row.kind == "math" and row.region is prev.region:
                run.add(row)  # rows of one display equation (`_contiguous_regions`)
                continue
            if run.kind == "text" and row.kind == "math" and _wrapped_maths(prev, row, run, ctx):
                row.kind, row.region = "text", None  # its maths becomes inline equations
                run.add(row)
                continue
            geometric = run.kind == "text" == row.kind and _continues(prev, row, run, ctx)
            if geometric and _same_style(prev, row, ctx):
                run.add(row)
                continue
            run.block_end = not geometric
            ends_sentence = prev.clean[-1:] in ".:?!"
            short = row.x1 < run.box[2] - 3 * row.size
            starts = not geometric or (ends_sentence and short)
            runs.append(_Run([row], row.kind, block_start=starts))
        else:
            runs.append(_Run([row], row.kind, block_start=True))
    return runs


def _wrapped_maths(prev: _Row, row: _Row, run: _Run, ctx: _Context) -> bool:
    """Is maths row `row` the last line of the sentence above, not a display equation?

    "... the system has exactly one solution," / "x = A^-1 b." A display equation is
    centred or indented and set off by extra space; a line of the paragraph starts at its
    left margin at the usual line pitch. A numbered or multi-line equation is never one.
    """
    region = row.region
    if region is None or region.labels or len(region.rows) != 1:
        return False
    return abs(row.x0 - run.box[0]) <= 0.5 and _continues(prev, row, run, ctx)


def _title_like(text: str) -> bool:
    """Starts like a title: the first word that starts with a letter, after any number
    ("2 kg", "2.3 Units", "2nd Law"), is not lowercase, and it does not end like a clause."""
    for word in text.split():
        first = next((c for c in word if c.isalnum()), "")
        if first.isalpha():
            return not first.islower() and text[-1:] not in ",;"
    return False


def _classify_run(run: _Run, ctx: _Context) -> _Block:
    rows = run.rows
    box = _union(r.bbox for r in rows)
    size = _dominant_size(rows)
    ink = [c for r in rows for c in r.ink]
    # A lone ">4" or "+8" (a table cell, a stray sign) is too little to call an equation.
    trivial = len(rows) == 1 and len(ink) <= 2 and not any(c.math for c in ink)
    if run.kind == "math" and not trivial:
        text = _clean(" ".join(r.styled_text for r in rows))
        script = any(c.flags & _SUPERSCRIPT_FLAG or c.size < SCRIPT_RATIO * size for c in ink)
        score, reasons = equation_score(text, sorted({c.font for c in ink}), script)
        if len(rows) > 1:
            reasons.append(f"{len(rows)} lines merged")
        region = rows[0].region
        labels = sorted(region.labels, key=lambda r: (r.y0, r.x0)) if region else []
        if labels:
            reasons.append("equation number")
        return _Block(
            Kind.EQUATION,
            box,
            rows,
            text=text,
            font_size=size,
            score=max([score, *(r.score for r in rows)]),
            reasons=reasons,
            label=" ".join(r.clean for r in labels) or None,
            regular=_regular_letters(ink),
        )
    text = _join_text(rows)
    letters = sum(c.isalpha() for c in text)
    bold = all(r.bold for r in rows)
    ratio = size / ctx.body
    larger = ratio >= HEADING_SIZE_RATIO
    # A section number is strong evidence in a large font ("3 Forces" is a chapter title);
    # at body size only a real section shape counts, not "2 kg", "12 Metres" or a date.
    number = section_number(text)
    titled = section_heading_number(text)
    evidence: list[str] = []
    if number and number[1]:
        evidence.append(REASON_LIST_NUMBER)
    elif number and (larger or titled):
        evidence.append(REASON_NUMBERED)
    # Rotated and diagonal text (a watermark, a stamp in the margin) is never a heading.
    upright = not any(r.vertical for r in rows)
    heading = None
    if (
        upright
        and larger
        and len(rows) <= MAX_HEADING_ROWS
        and len(text) <= MAX_HEADING_CHARS
        and (letters >= 2 or number)
    ):
        heading = [REASON_LARGER_FONT, f"font {ratio:.2f}x body", *evidence]
    elif CAPTION.match(text):
        return _Block("caption", box, rows, text=text, font_size=size)
    elif (
        upright
        and bold
        and ratio >= 0.95
        and run.block_start
        and len(rows) <= MAX_BOLD_HEADING_ROWS
        and len(text) <= MAX_BOLD_HEADING_CHARS
        and letters >= 2
        and _title_like(text)
    ):
        heading = [REASON_BOLD_BODY, *evidence]
    elif (
        upright
        and titled
        and len(rows) == 1
        and run.block_start
        and run.block_end
        and len(text) <= 80
        and len(text.split()) <= 12
        and text[-1:] != "."
    ):
        heading = [REASON_NUMBERED_LINE, *evidence]
    if heading is not None:
        if bold:
            heading.append("bold")
        return _Block(
            Kind.HEADING, box, rows, text=text, font_size=size, bold=bold, reasons=heading
        )
    if rows[0].marker is not None:
        return _Block(Kind.LIST_ITEM, box, rows, font_size=size, bold=bold, marker=rows[0].marker)
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
    """Paragraph (or list item) text with inline equations cut out, hyphenation repaired
    between rows. A list item's text starts after its marker."""
    pieces: list[str | _Inline] = []
    rows = list(block.rows)
    if block.marker is not None and block.marker.end:
        first = rows[0]
        rows[0] = _Row(first.chars[block.marker.end :], column=first.column)
    letters = _BoldLetters.start()
    for row in rows:
        row_pieces: list[str | _Inline] = []
        cursor = 0
        for start, end, reasons in _bold_letter_runs(row, _inline_runs(row), letters):
            row_pieces.append("".join(c.c for c in row.chars[cursor:start]))
            chars = row.chars[start:end]
            text = _clean("".join(c.styled for c in chars))
            ink = [c for c in chars if not c.blank]
            fonts = sorted({c.font for c in ink})
            score, _ = equation_score(text, fonts, any(c.flags & _SUPERSCRIPT_FLAG for c in ink))
            box = _clear_of_punctuation(_union((c.x0, c.y0, c.x1, c.y1) for c in ink), row, end)
            row_pieces.append(
                _Inline(box, text, score, reasons, _dominant_size([row]), _regular_letters(ink))
            )
            cursor = end
        row_pieces.append("".join(c.c for c in row.chars[cursor:]))
        _append_row(pieces, row_pieces)
    return _tidy(pieces)


def _clear_of_punctuation(box: BBox, row: _Row, end: int) -> BBox:
    """Pull an inline equation's right edge in so its padded crop stops short of the
    punctuation that follows it ("d_0," in "positions d_0, d_f and ...", "in 1.5 s?").

    The comma stays in the paragraph text after the equation's marker; if the crop shows
    it too, the model transcribes it and the output reads "d0, ,". The edge moves left by
    at most CROP_PAD, so a crop with the default padding still holds every glyph of the
    equation.
    """
    after = next((c for c in row.chars[end:] if not c.blank), None)
    if after is None or after.c not in ",.;:?":
        return box
    x1 = max(min(box[2], after.x0 - CROP_PAD), box[2] - CROP_PAD, box[0] + 0.5)
    return (box[0], box[1], x1, box[3])


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
        text = piece.translate(_TEXT_MAP)
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


# ============================================================================ tables


def _claim_tables(
    page: pymupdf.Page,
    number: int,
    frags: dict[int, list[_Row]],
    paths: dict[int, list[_Path]],
    previous: list[tables.PageTable] | None,
) -> list[tables.PageTable]:
    """Find the page's data tables (`pipeline.tables`) and take their lines (cells and
    "Table N" caption) and rules out of the page, before columns, rows, headings and
    equations are worked out: a table's numbers are not an equation, its bold header
    cells not headings, its ruled cells not a figure, and its columns not text columns.
    Only pages the probe passed get here (`analyze` probes every selected page first).
    `previous` is the page before's tables, when it was analysed: a table that goes on
    from its last one is marked."""
    found = tables.find(page, frags[number], paths[number], clean=_clean)
    if not found:
        return found
    lines = set().union(*(t.lines for t in found))
    drawn = set().union(*(t.paths for t in found))
    frags[number] = [f for i, f in enumerate(frags[number]) if i not in lines]
    paths[number] = [p for i, p in enumerate(paths[number]) if i not in drawn]
    if previous and tables.continues(previous[-1], found[0], page.rect.width):
        found[0].reasons.append(tables.REASON_CONTINUED)
    return found


def _table_blocks(page_tables: Sequence[tables.PageTable]) -> list[_Block]:
    """TABLE blocks, placed in reading order like figures. The grid carries the caption;
    the element's text is the cells' text, row by row (what a crop-less fallback shows)."""
    blocks = []
    for found in page_tables:
        grid = found.grid.model_copy(update={"caption": found.caption})
        cells = sorted(grid.cells, key=lambda c: (c.row, c.col))
        text = _clean(" ".join(c.text for c in cells if c.text))
        blocks.append(
            _Block(
                Kind.TABLE,
                found.bbox,
                text=text or None,
                caption=found.caption,
                reasons=list(found.reasons),
                table=grid,
            )
        )
    return blocks


def _term_items(blocks: list[_Block]) -> list[_Block]:
    """A grid of terms (`tables.is_term_grid`: an OpenStax "Key Terms" box) becomes its
    terms as list items, in reading order, in the grid's place: a screen reader hears a
    list of terms, with no table, image or decision. Left in the text flow its lines ran
    together ("position reference frame scalar vector"); claimed as a data table it was
    an image. Every other block is kept as it is."""
    out: list[_Block] = []
    for block in blocks:
        grid = block.table
        if block.kind != Kind.TABLE or grid is None or grid.is_data:
            out.append(block)
            continue
        for cell in tables.term_list(grid):
            text = _clean(cell.text)
            if text:
                bbox = cell.bbox or block.bbox
                out.append(_Block(Kind.LIST_ITEM, bbox, text=text, pieces=[text]))
    return out


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
    paths: list[_Path],
    margins: _MarginFilter,
    page_tables: Sequence[tables.PageTable] = (),
) -> _PageBlocks:
    page_rect = page.rect
    rows = _split_labels(rows)
    for row in rows:
        _classify_row(row)
    rules = [p for p in paths if p.kind in ("hrule", "radical")]
    used_rules = _resolve_fragments(rows, rules)
    used = {id(rule) for rule in used_rules}
    loose = [p for p in paths if id(p) not in used]
    figures = _merge_figures(
        _raster_figures(page, page_rect) + _vector_figures(loose, rows, page_rect)
    )
    # A logo repeated in the header or footer band is decoration, like a running header.
    figures = [f for f in figures if not margins.drops_figure(f.bbox, page_rect.height)]
    figures = [f for f in figures if _absorb_labels(f, rows, ctx)]
    text_rows = [r for r in rows if not r.absorbed]
    _equation_regions(text_rows, used_rules)
    placed = [f.bbox for f in figures] + [t.bbox for t in page_tables]
    order = _reading_order([r.bbox for r in text_rows], gutter, ctx.body, placed)
    ordered = _contiguous_regions([text_rows[i] for i in order])
    runs = _runs(ordered, ctx)
    blocks = [_classify_run(run, ctx) for run in runs]
    blocks = _merge_chapter_numbers(_merge_style_splits(blocks, runs))
    for block in blocks:
        region = block.rows[0].region if block.rows else None
        if block.kind == Kind.EQUATION and region is not None:
            _add_rules(block, region.rules)
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
    blocks.extend(_table_blocks(page_tables))
    order = _reading_order([b.bbox for b in blocks], gutter, ctx.body)
    final = _merge_column_continuations([blocks[i] for i in order])
    for block in final:
        if block.kind in _TEXT_BLOCKS:
            block.pieces = _paragraph_pieces(block)
    final = [b for b in final if b.kind not in _TEXT_BLOCKS or _has_content(b.pieces)]
    final = _term_items(final)
    origins = _column_origins(text_rows)
    for block in final:
        if block.marker is not None:
            block.origin = origins.get(block.rows[0].column, 0.0)
    return _PageBlocks(number, (round(page_rect.width, 2), round(page_rect.height, 2)), final)


_TEXT_BLOCKS = (Kind.PARAGRAPH, Kind.LIST_ITEM)  # blocks whose text may hold inline maths


def _column_origins(rows: Sequence[_Row]) -> dict[str, float]:
    """Left edge of the text of each column of a page: the leftmost x where at least
    three rows start (within 2.5 pt), so one outdented line does not move it; the
    leftmost row when no three rows line up."""
    starts: dict[str, list[float]] = {}
    for row in rows:
        if not row.vertical:
            starts.setdefault(row.column, []).append(row.x0)
    origins: dict[str, float] = {}
    for column, xs in starts.items():
        xs.sort()
        origins[column] = next(
            (x for i, x in enumerate(xs) if bisect.bisect_right(xs, x + 2.5) - i >= 3), xs[0]
        )
    return origins


def _merge_column_continuations(blocks: list[_Block]) -> list[_Block]:
    """A paragraph (or list item) cut by a column break is one block again.

    The next block in reading order must be a paragraph that starts higher on the page
    (the top of the next column) with a lowercase letter, and the first part must not end
    a sentence. The merged bbox covers both parts; inline equations keep their own boxes.
    """
    out: list[_Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind in _TEXT_BLOCKS
            and block.kind == Kind.PARAGRAPH
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
    paths: dict[int, list[_Path]] = {}
    margins = _margin_filter(doc, selected, frags, paths)
    prepared = {}
    found: dict[int, list[tables.PageTable]] = {}
    for number in selected:
        if number not in frags:
            frags[number] = _page_rows(doc[number])
        if number not in paths:
            paths[number] = _page_paths(doc[number])
        found[number] = _claim_tables(doc[number], number, frags, paths, found.get(number - 1))
        prepared[number] = _prepare_rows(doc[number], frags[number], margins)
    ctx = _body_and_pitch({n: rows for n, (rows, _) in prepared.items()})
    _list_markers({n: rows for n, (rows, _) in prepared.items()}, paths, ctx)
    pages = [
        _page_blocks(doc[n], n, prepared[n][0], prepared[n][1], ctx, paths[n], margins, found[n])
        for n in selected
    ]
    _settle_bold_letters(pages)
    block_ids: dict[int, str] = {}
    elements = _to_elements(pages, ctx, block_ids)
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
        notices=_layout_notices(pages, ctx, block_ids),
    )


# A paragraph or list item set in a monospaced font (at least this share of its glyphs,
# and this many of them) is program code: its text is kept, but its line breaks and
# indentation (which decide a Python loop's body) are not.
CODE_MONO_SHARE = 0.8
CODE_MIN_GLYPHS = 12
# A footnote: a block in the bottom part of the page (below this share of its height), in
# a smaller font than the body, that starts with a mark (a digit, *, a dagger) printed
# raised in the text above it on the same page.
FOOTNOTE_ZONE = 0.6
FOOTNOTE_SIZE = 0.92
_FOOTNOTE_MARKS = frozenset("*\u2020\u2021\u00a7")


def _layout_notices(
    pages: list[_PageBlocks], ctx: _Context, block_ids: dict[int, str]
) -> list[Notice]:
    """The code blocks and footnotes the job page lists under "Not handled in this file"
    (review "none": no decision; docs/design/content-features.md "Notices")."""
    notices: list[Notice] = []
    for page in pages:
        raised: set[str] = set()  # marks printed raised above, on this page
        for block in page.blocks:
            element_id = block_ids.get(id(block))
            if block.kind not in _TEXT_BLOCKS or element_id is None:
                continue
            ink = [c for row in block.rows for c in row.chars if not c.blank]
            if len(ink) >= CODE_MIN_GLYPHS and sum(c.mono for c in ink) >= CODE_MONO_SHARE * len(
                ink
            ):
                notices.append(Notice(code="code_block", page=page.number, element_id=element_id,
                                      review="none"))  # fmt: skip
            elif ink and _footnote(block, ink[0].c, page, ctx, raised):
                notices.append(Notice(code="footnote", page=page.number, element_id=element_id,
                                      review="none"))  # fmt: skip
            for row in block.rows:
                raised |= {c.c for c in row.chars if row.is_script(c) and c.c.strip()}
    return notices


def _footnote(
    block: _Block, first: str, page: _PageBlocks, ctx: _Context, raised: set[str]
) -> bool:
    small = block.font_size is not None and block.font_size < FOOTNOTE_SIZE * ctx.body
    low = block.bbox[1] >= FOOTNOTE_ZONE * page.size[1]
    mark = first.isdigit() or first in _FOOTNOTE_MARKS
    return small and low and mark and first in raised


def _to_elements(
    pages: list[_PageBlocks], ctx: _Context, block_ids: dict[int, str] | None = None
) -> list[Element]:
    """Blocks -> Elements with ids "p{page+1}-e{index}" and a global reading order
    (`block_ids` gets each block's element id)."""
    elements: list[Element] = []
    levels = _list_levels((b for page in pages for b in page.blocks), 0.6 * ctx.body)
    for page in pages:
        ids = (f"p{page.number + 1}-e{index}" for index in count())
        for block in page.blocks:
            element_id = next(ids)
            if block_ids is not None:
                block_ids[id(block)] = element_id
            if block.kind in _TEXT_BLOCKS:
                children = []
                parts = []
                for piece in block.pieces:
                    if isinstance(piece, _Inline):
                        child_id = next(ids)
                        children.append((child_id, piece))
                        parts.append(inline_marker(child_id))
                    else:
                        parts.append(piece)
                marker = block.marker
                elements.append(
                    Element(
                        id=element_id,
                        kind=Kind(block.kind),
                        page=page.number,
                        bbox=_rounded(block.bbox),
                        order=len(elements),
                        text="".join(parts).strip(),
                        font_size=block.font_size,
                        bold=block.bold,
                        list_marker=marker.text if marker else None,
                        list_ordered=marker.ordered if marker else None,
                        list_level=levels.get(id(block)) if marker else None,
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
                            regular_letters=inline.regular,
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
                    equation_label=block.label if kind == Kind.EQUATION else None,
                    caption=block.caption,
                    regular_letters=block.regular if kind == Kind.EQUATION else None,
                    table=block.table if kind == Kind.TABLE else None,
                )
            )
    return elements


# ============================================================================ glyphs


@dataclass(frozen=True, slots=True)
class Glyph:
    """One printed character with the colour of its span, for the text pass
    (`pipeline.text_pass`), which finds links and coloured text by where glyphs are and
    maps them onto `Element.text`."""

    c: str
    bbox: BBox  # display coordinates, the frame of `Element.bbox`
    rgb: tuple[int, int, int]  # the span's fill colour, sRGB


def page_glyphs(page: pymupdf.Page) -> list[Glyph]:
    """Every visible character of `page` with its span colour, in PyMuPDF's order.

    The same extraction as `_page_rows` (flags, display coordinates), kept apart so
    reading a page for layout does not change: only the text pass needs colours. Spaces
    are left out (element text has its own), characters go through `_TEXT_MAP` like
    element text, and fully transparent text is skipped. The caller holds `_LOCK`.
    """
    display = _Display.of(page)
    glyphs: list[Glyph] = []
    for block in page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if int(span.get("alpha", 255)) == 0:
                    continue
                colour = int(span.get("color") or 0) & 0xFFFFFF
                rgb = ((colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF)
                for ch in span["chars"]:
                    c = ch["c"].translate(_TEXT_MAP)
                    if c and not c.isspace():
                        glyphs.append(Glyph(c[0], display.box(ch["bbox"]), rgb))
    return glyphs
