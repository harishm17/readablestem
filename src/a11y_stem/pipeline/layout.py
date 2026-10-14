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
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property, lru_cache
from itertools import combinations, count, pairwise
from typing import Any

import pymupdf

from a11y_stem.core.childenv import child_env
from a11y_stem.core.mathchars import base_char, char_variant, styled_char
from a11y_stem.core.notation import canonical_from_parts
from a11y_stem.core.types import BBox, Element, Kind, Link, Notice, TableGrid, inline_marker
from a11y_stem.pipeline import hyphens, page_type, tables
from a11y_stem.pipeline import options as run_options
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
TITLE_MIN_PT = 18.0  # a row of several words this large is a title, never a figure's label
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
FIGURE_PART_REACH = 12.0  # a smaller drawing this close to a vector figure is part of it
DRAWING_CLUSTER_GAP = 6.0  # drawings closer than this belong to the same figure
PAGE_SIZED_FRACTION = 0.85  # a drawing this wide and tall fills the page (or its frame)
FRAME_EDGE_BAND = 0.15  # a page-long rule within this share of an edge is a frame's side
FRAME_LINE_BAND = 0.05  # every point of a frame lies this share of its smaller side (at
#                         least MIN_IMAGE_PT) from its box's edge: double lines, rounded
#                         corners, corner ticks and curls
# Page-type reasons for ink on a page: a page-filling drawing there is never a figure for
# the model (it may be a page of notes). When the ink was not confirmed (rejected or not
# analysed) the safety net keeps the page image instead; on a typed page with
# handwriting (mixed_ink) the handwriting is cut out already and the rest is left out.
INK_REASONS = frozenset({"ink_rejected", "ink_not_analysed", "ink_some", "ink_writing"})
UNREAD_PAD = 2.0  # pt: a stroke within this of an element's box belongs to the element
# Pen strokes of a typed page (clean or not) that no figure holds, cut out as handwriting
# (fewer than page_type.INK_MIXED_STROKES make no mixed_ink page): from one stroke up, as
# a tick or a one-stroke signature is content too (the silent-loss sweep, 2026-10-12).
LOOSE_INK_STROKES = 1
# A drawing too small to be a figure, outside every element of a typed page, is kept
# (`_leftover_drawings`) when it is at least this big on its longer side or has
# page_type.VECTOR_MIN_PATHS paths: a tick, an icon, maths drawn as outlines. Smaller
# single marks (a dot) are noise.
LEFTOVER_MIN_PT = 6.0
# Leftover drawings this close to each other are one (the marks of a scatter of shapes,
# the glyphs of one formula drawn as outlines): MIN_DRAWING_PT.
LEFTOVER_REACH = 24.0
DOT_PT = 4.0  # a filled mark this small on its longer side is a dot
# A pen mark over typed text (`_pen_mark_over_text`): at most this many strokes, reaching
# out of the line they lie on by at least this share of their height. A hand tick, cross
# or slash is drawn bigger than the text; a cancel stroke or an icon set in a line is not.
PEN_MARK_STROKES = 2
PEN_MARK_OUT = 0.25
PEN_MARK_COLOUR = 0.3  # a closed or filled mark differs from the text this much (sRGB)
PEN_FILL_SHARE = 0.5  # a filled mark is a stroke's ribbon when it covers at most this of its box
# A row the header/footer rules dropped is a figure's label (`_figure_labels`) when it is
# at most this long and within LABEL_REACH x its font size of the figure or its labels.
LABEL_MAX_CHARS = 40
LABEL_REACH = 8.0
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

TEXT_FLAGS = page_type.TEXT_FLAGS  # whitespace kept, clipped to the MediaBox, glyph boxes

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
    # block of program code, a footnote (`_layout_notices`), hidden text left out
    # (`_hidden_text_notices`). Codes, ids and counts only.
    notices: list[Notice] = field(default_factory=list)
    # Per analysed page, what it is (`pipeline.page_type`; `to_record` gives the stored,
    # content-free form).
    page_types: dict[int, page_type.PageTypeResult] = field(default_factory=dict)
    # Pages kept whole as their page image, with the Fix reason ("handwritten_page",
    # "scanned_page", "unread_page"): nothing on them is read, they have no elements, and
    # the pipeline keeps each one as an image marked "Not converted", with a decision.
    kept_pages: dict[int, str] = field(default_factory=dict)


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
            command,
            input=pdf_bytes,
            capture_output=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
            env=child_env(),  # no keys or secrets for the process that reads the PDF
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
    colour: int = 0  # the span's sRGB colour (0xRRGGBB)

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
def _font_family(font: str) -> str:
    """The typeface of a font name, without subset tag, weight, style or design size:
    "ABCDEF+LiberationSans-Bold" and "LiberationSans" are "liberationsans", "CMBX12" is
    "cmbx". Bold and regular faces of one family compare equal; Sans and Serif do not."""
    name = font.split("+", 1)[-1].lower()
    return re.split(r"[-,]", name, maxsplit=1)[0].rstrip("0123456789")


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
    # An exercise number ("18.", "4)") cut off the front of a maths row: text, never maths.
    exercise_number: bool = False

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

    @cached_property
    def code(self) -> bool:
        """A line of program code: monospaced throughout (`CODE_MONO_SHARE` of its glyphs),
        upright text. Whether a run of such lines is a code block is `_code_run`'s call."""
        ink = [c for c in self.ink if not c.blank]
        return (
            bool(ink)
            and not self.vertical
            and not self.diagonal
            and sum(c.mono for c in ink) >= CODE_MONO_SHARE * len(ink)
        )

    @cached_property
    def family(self) -> str:
        """The typeface most letters are set in (maths fonts and symbols left out)."""
        counts = Counter(_font_family(c.font) for c in self.ink if c.c.isalnum() and not c.math)
        return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if counts else ""

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


def _raw_blocks(page: pymupdf.Page) -> list[dict]:
    """The page's raw text dictionary: the one text extraction layout makes per page,
    shared by the rows and the page type (`pipeline.page_type`)."""
    return page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]


def _page_rows(
    page: pymupdf.Page,
    raw: list[dict] | None = None,
    covered: set[tuple[int, int]] | frozenset[tuple[int, int]] = frozenset(),
) -> list[_Row]:
    """One row per PyMuPDF line (not yet merged), in display coordinates.

    Only text a reader can see: a span in render mode 3 or at 5 % opacity or less
    (`page_type.span_hidden`, what iPad notes apps and OCR programs write), and a
    character painted over afterwards by an image or a filled shape (`covered`, origins
    from `page_type.text_stats`), are never content."""
    display = _Display.of(page)
    rows: list[_Row] = []
    for block in raw if raw is not None else _raw_blocks(page):
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            dx, dy = display.vector(*line["dir"])
            chars: list[_Char] = []
            for span in line["spans"]:
                if page_type.span_hidden(span):
                    continue
                size, font, flags = float(span["size"]), span["font"], int(span["flags"])
                colour = int(span.get("color") or 0)
                for ch in span["chars"]:
                    if covered and page_type.origin_key(ch["origin"]) in covered:
                        continue
                    box = display.box(ch["bbox"])
                    origin = display.point(*ch["origin"])
                    chars.append(
                        _Char(
                            ch["c"],
                            *box,
                            baseline=origin[1],
                            size=size,
                            font=font,
                            flags=flags,
                            colour=colour,
                        )
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
    # (zone, box) of every drawing and image in the band that repeats: a logo however
    # small, a decorative line. Anything else in the band (a note in the margin) counts.
    marks: set[tuple[str, tuple[int, int, int, int]]] = field(default_factory=set)
    # Pictures covering the page that repeat (by xref): a template's background.
    backgrounds: set[int] = field(default_factory=set)

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
        seen.update(("mark", key) for key in _band_marks(page, paths[number]))
        seen.update(("picture", xref) for xref in _page_pictures(page) if xref)
        counts.update(seen)
    needed = max(2, -(-len(reference) // 2))  # at least half of the pages, and two
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
    return _MarginFilter(
        repeated=repeated["text"],
        fixed={key for key in repeated["text"] if len(texts[key]) == 1},
        rotated=repeated["rotated"],
        figures=repeated["figure"],
        body=max(body, 1.0),
        marks=repeated["mark"],
        backgrounds=repeated["picture"],
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
    elif row.exercise_number:
        row.kind = "text"  # "18.": kept as text before the equation it numbers
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


GLOSSARY_HEADING = re.compile(
    r"\b(?:key\s+terms?|glossary|vocabulary|terms\s+and\s+definitions)\s*:?\s*$", re.IGNORECASE
)
GLOSSARY_INDENT = 1.2  # x font size: a definition starts this much further right than its term
GLOSSARY_TERM_ROWS = 2  # lines a term may take
GLOSSARY_GAP = 1.6  # x font size: most space between a term line and its definition


def _term_words(text: str) -> bool:
    """Reads like a term: a few words, not ending like a sentence."""
    words = text.split()
    return 0 < len(words) <= MAX_TERM_WORDS and text.rstrip()[-1:] not in ".?!;,"


def _entry_rows(block: _Block) -> int | None:
    """In a paragraph that is a term and its definition (the term at the margin, the
    definition on the lines indented below it: how OpenStax sets "Key Terms"), the number
    of lines the term takes; None for any other paragraph."""
    rows = block.rows
    if block.kind != Kind.PARAGRAPH or block.code or block.marker is not None or len(rows) < 2:
        return None
    margin, size = rows[0].x0, rows[0].size
    taken = 0
    while taken < min(len(rows), GLOSSARY_TERM_ROWS + 1) and rows[taken].x0 <= margin + 0.5 * size:
        taken += 1
    if not 1 <= taken <= GLOSSARY_TERM_ROWS or taken >= len(rows):
        return None
    if any(r.x0 < margin + GLOSSARY_INDENT * size for r in rows[taken:]):
        return None
    if not _term_words(_clean(" ".join(r.text for r in rows[:taken]))):
        return None
    return taken


def _bold_lead(row: _Row) -> int:
    """Where a line's bold lead ends (an index into `row.chars`; 0 if it has none): the
    line starts in bold and goes on in regular type, "Limit The value that ...". A line
    that is bold throughout has no lead."""
    chars = row.chars
    first = next((i for i, c in enumerate(chars) if not c.blank), None)
    if first is None or not chars[first].bold:
        return 0
    end = first
    for i in range(first, len(chars)):
        if chars[i].blank:
            continue
        if not chars[i].bold:
            return end if end > first or chars[first].c.strip() else 0
        end = i + 1
    return 0


def _run_in_entries(block: _Block) -> list[_Block] | None:
    """A paragraph whose lines start entries of a glossary, each with a bold term that runs
    into its definition ("Derivative The limit of ..."), as one block per entry; None when
    the paragraph is not that. An entry starts at a line with a bold lead of at most
    MAX_TERM_WORDS words, after a line that ends a sentence; every entry needs a
    definition."""
    rows = block.rows
    if block.kind != Kind.PARAGRAPH or block.code or block.marker is not None:
        return None
    starts = []
    for i, row in enumerate(rows):
        lead = _bold_lead(row)
        if not lead or not _term_words(_clean("".join(c.c for c in row.chars[:lead]))):
            continue
        if i == 0 or rows[i - 1].clean[-1:] in ".?!)":
            starts.append(i)
    if not starts or starts[0] != 0:
        return None
    entries = []
    for number, first in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(rows)
        group = rows[first:end]
        lead = _bold_lead(group[0])
        term = _Block(Kind.PARAGRAPH, block.bbox, [_Row(group[0].chars[:lead])])
        rest = [_Row(group[0].chars[lead:], column=group[0].column), *group[1:]]
        body = _Block(Kind.PARAGRAPH, block.bbox, rest)
        definition = _paragraph_pieces(body)
        if not _has_content(definition):
            return None
        box = _union(r.bbox for r in group)
        entries.append(_entry(_paragraph_pieces(term), definition, box, group, group[0].size))
    return entries


def _entry(term: list[str | _Inline], definition: list[str | _Inline], box: BBox, rows, size):
    """One glossary entry as a paragraph block: the term's pieces, a space, the definition's;
    `term_pieces` says how many pieces are the term."""
    return _Block(
        Kind.PARAGRAPH,
        box,
        list(rows),
        font_size=size,
        pieces=[*term, " ", *definition],
        term_pieces=len(term),
    )


def _term_over(block: _Block, following: _Block | None) -> bool:
    """Is `block` a term on a line or two of its own, with the paragraph indented right
    below it as its definition (OpenStax sets some entries as two blocks)?"""
    if following is None or block.kind != Kind.PARAGRAPH or following.kind != Kind.PARAGRAPH:
        return False
    if block.code or following.code or block.marker is not None or following.marker is not None:
        return False
    rows = block.rows
    margin, size = rows[0].x0, rows[0].size
    return (
        len(rows) <= GLOSSARY_TERM_ROWS
        and all(abs(r.x0 - margin) <= 0.5 * size for r in rows)
        and _term_words(_clean(" ".join(r.text for r in rows)))
        and all(r.x0 >= margin + GLOSSARY_INDENT * size for r in following.rows)
        and 0 <= following.bbox[1] - block.bbox[3] <= GLOSSARY_GAP * size
        and _has_content(following.pieces)
    )


def _glossary_entries(pages: list[_PageBlocks]) -> None:
    """Under a heading that ends in "Key Terms", "Glossary" or "Vocabulary", a term and its
    definition become one paragraph block that the assembler writes as a `<dt>` and `<dd>`
    (in place; `Element.term_end`). Two shapes: the term on a line of its own at the margin
    with the definition indented below it, in one paragraph (OpenStax), and a bold term of
    a few words that layout took for a heading, directly over its definition paragraph.
    The second one makes the terms stop being headings: a heading for every term of a
    glossary would be an outline of hundreds of entries.

    The section lasts until a heading that is not a bold line (a larger font or a section
    number); a bold line that is not directly over a paragraph stays a heading."""
    active = False
    for page in pages:
        blocks, out, i = page.blocks, [], 0
        while i < len(blocks):
            block = blocks[i]
            if block.kind == Kind.HEADING:
                strong = REASON_LARGER_FONT in block.reasons or REASON_NUMBERED in block.reasons
                if GLOSSARY_HEADING.search(block.text or ""):
                    active = True
                elif strong:
                    active = False
            following = blocks[i + 1] if i + 1 < len(blocks) else None
            if active and (entries := _run_in_entries(block)):
                out.extend(entries)
            elif active and block.kind == Kind.PARAGRAPH and (taken := _entry_rows(block)):
                head = _Block(Kind.PARAGRAPH, block.bbox, block.rows[:taken])
                tail = _Block(Kind.PARAGRAPH, block.bbox, block.rows[taken:])
                out.append(
                    _entry(
                        _paragraph_pieces(head),
                        _paragraph_pieces(tail),
                        block.bbox,
                        block.rows,
                        block.font_size,
                    )
                )
            elif active and _term_over(block, following):
                assert following is not None
                out.append(
                    _entry(
                        _paragraph_pieces(block),
                        _paragraph_pieces(following),
                        _union([block.bbox, following.bbox]),
                        [*block.rows, *following.rows],
                        block.font_size,
                    )
                )
                i += 1
            elif (
                active
                and block.kind == Kind.HEADING
                and REASON_BOLD_BODY in block.reasons
                and REASON_LARGER_FONT not in block.reasons
                and _term_words(block.text or "")
                and following is not None
                and following.kind == Kind.PARAGRAPH
                and not following.code
                and not following.bold
                and following.marker is None
                and following.bbox[1] - block.bbox[3] <= GLOSSARY_GAP * (block.font_size or 10.0)
                and _has_content(following.pieces)
            ):
                out.append(
                    _entry(
                        [block.text or ""],
                        following.pieces,
                        _union([block.bbox, following.bbox]),
                        [*block.rows, *following.rows],
                        following.font_size,
                    )
                )
                i += 1
            else:
                out.append(block)
            i += 1
        page.blocks = out


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
    source: int = -1  # index in the page's get_cdrawings list (handwriting is left out)
    frame: bool = False  # an outline along the edges of its own box only (a frame's shape)
    rules: bool = False  # paper rules: long separate horizontal and vertical lines only
    open_stroke: bool = False  # a stroked line or curve that does not close: a pen mark's shape
    axis: bool = False  # holds one of two lines meeting at a corner: a plot's axes (`_mark_axes`)
    ruler: bool = False  # holds a line with tick marks across or on it, or one of the ticks
    # The L pairs (horizontal line, vertical line) the path holds a line of (`_mark_axes`).
    axes: tuple[tuple[_Straight, _Straight], ...] = ()
    plot: bool = False  # one of those Ls has evidence of a plot (`_mark_plots`)
    # A ruler whose ticks all stand on one side of its line, a letter cell apart: an
    # answer field's cells when no label is near (`_answer_fields`).
    comb: bool = False
    # Where a curved open stroke's subpaths start and end (display space): a plotted
    # line starts at its axes' corner (`_mark_plots`).
    ends: tuple[tuple[float, float], ...] = ()
    # Set only where a later test reads them (`_page_paths`): an open stroke 12 pt or more
    # that has a curve or lines in two directions; a curved closed or filled shape's sRGB
    # colour, and the share of its box a filled one covers.
    turns: bool = False
    colour: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fill_share: float = 0.0


def _page_paths(page: pymupdf.Page, drawings: list[dict] | None = None) -> list[_Path]:
    """Visible vector paths, classified. `get_cdrawings` (plain tuples) is several times
    faster than `get_drawings` on plots with thousands of markers; the same list is
    shared with the page type's ink analysis. Near-white paint counts only on something
    darker (`page_type.paint_visibility`: a dark slide's white diagram is seen, a white box
    on the white page is not)."""
    display = _Display.of(page)
    width_pt, height_pt = page.rect.width, page.rect.height
    frame = page.cropbox  # the drawings' own (unrotated) frame
    paths: list[_Path] = []
    lines: list[list[_Straight]] = []  # each path's horizontal and vertical lines
    drawings = drawings if drawings is not None else page.get_cdrawings()
    seen = page_type.paint_visibility(drawings, page_type.image_boxes(page)[0])
    for source, drawing in enumerate(drawings):
        filled, stroked = seen[source]
        if not (stroked or filled):
            continue  # white boxes and invisible paths
        items = drawing.get("items") or []
        box = display.box(_drawing_bounds(drawing["rect"], items))
        if box[2] < 0 or box[3] < 0 or box[0] > width_pt or box[1] > height_pt:
            continue  # outside the CropBox: not visible on the page
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
        border = (
            kind == "shape"
            and not filled
            and (_rectangle_outline(items, segments, box) or _border_like(items, display, box))
        )
        rules = kind == "shape" and page_type.paper_rules(drawing, frame.width, frame.height)
        open_stroke = stroked and not filled and not _encloses(items, drawing.get("closePath"))
        fill = filled and kind == "shape"
        path = _Path(box, kind, curved, fill, source, border, rules, open_stroke)
        # What later tests read, only for the paths they look at (a page of handwriting
        # has tens of thousands of strokes, and the ink budget runs while they are read).
        if curved and open_stroke:
            path.ends = _subpath_ends(items, display)
        if open_stroke and kind == "shape" and max(width, height) >= MIN_IMAGE_PT:
            path.turns = _turns(items, segments)  # an open polyline (`_diagram_signs`)
        if curved and kind == "shape" and not open_stroke:  # a closed or filled hand mark
            path.colour = _rgb(drawing.get("color") if stroked else drawing.get("fill"))
            if fill:  # a ribbon tick has tens of points; a longer fill is no such mark
                path.fill_share = _fill_share(items, display, box) if len(items) <= 200 else 1.0
        paths.append(path)
        lines.append(_straight_lines(path, segments))
    _mark_axes(paths, lines)
    return paths


def _fill_share(items: Sequence[Any], display: _Display, box: BBox) -> float:
    """The share of `box` a filled path covers: its subpaths' areas by the shoelace over
    their points (a curve by its ends), at most 1. A highlight covers most of its box; a
    tick drawn as a filled ribbon little of it."""
    size = _area(box)
    if size <= 0:
        return 0.0
    total = 0.0
    ring: list[tuple[float, float]] = []

    def close() -> float:
        if len(ring) < 3:
            return 0.0
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in pairwise([*ring, ring[0]]))) / 2

    for item in items:
        if item[0] == "re":
            x0, y0, x1, y1 = item[1][0], item[1][1], item[1][2], item[1][3]
            total += abs(x1 - x0) * abs(y1 - y0)
            continue
        if item[0] == "qu":
            total += close()
            ring = [display.point(*q) for q in item[1]]
            total += close()
            ring = []
            continue
        start, end = display.point(*item[1]), display.point(*item[-1])
        if ring and (abs(ring[-1][0] - start[0]) > 0.01 or abs(ring[-1][1] - start[1]) > 0.01):
            total += close()
            ring = []
        if not ring:
            ring.append(start)
        ring.append(end)
    total += close()
    return min(1.0, total / size)


def _rgb(paint: Sequence[float] | None) -> tuple[float, float, float]:
    """A drawing's colour as sRGB components 0..1 (grey and CMYK converted)."""
    if not paint:
        return (0.0, 0.0, 0.0)
    if len(paint) == 1:
        return (paint[0], paint[0], paint[0])
    if len(paint) == 4:
        c, m, y, k = paint
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return (paint[0], paint[1], paint[2])


def _turns(items: Sequence[Any], segments: list[tuple[float, float, float, float]]) -> bool:
    """The path has a curve, or lines in two directions at least 20 degrees apart: a
    polyline (a step, a zigzag, an arrowhead, a wire round a corner), not one straight
    line drawn in pieces."""
    if any(item[0] == "c" for item in items):
        return True
    first = None
    for px, py, qx, qy in segments:
        dx, dy = qx - px, qy - py
        length2 = dx * dx + dy * dy
        if length2 < 0.25:
            continue
        if first is None:
            first = (dx, dy, length2)
            continue
        cross = first[0] * dy - first[1] * dx
        if cross * cross > 0.117 * first[2] * length2:  # sin^2 of 20 degrees
            return True
    return False


SUBPATH_ENDS = 16  # at most this many subpath starts and ends are kept per path


def _subpath_ends(items: Sequence[Any], display: _Display) -> tuple[tuple[float, float], ...]:
    """The start and end of each subpath of a path of lines and curves (display space): a
    subpath starts where an item does not begin at the last one's end. At most
    SUBPATH_ENDS points."""
    found: list[tuple[float, float]] = []
    last = None
    for item in items:
        if item[0] not in ("l", "c"):
            continue
        first, end = item[1], item[-1]
        if last is None or abs(first[0] - last[0]) > 0.01 or abs(first[1] - last[1]) > 0.01:
            if last is not None:
                found.append(display.point(*last))
            found.append(display.point(*first))
            if len(found) >= SUBPATH_ENDS:
                return tuple(found)
        last = end
    if last is not None:
        found.append(display.point(*last))
    return tuple(found)


def _drawing_bounds(rect: Sequence[float], items: Sequence[Any]) -> tuple[float, ...]:
    """The box of every point of a drawing (unrotated frame). MuPDF's `rect` leaves out the
    start of a subpath that begins away from where the last one ended: the axes of a plot
    drawn as one path, "80 75 m 640 75 l 80 335 m 80 75 l S", read as the x-axis alone and
    the y-axis was lost (the second final verify, 2026-10-12). Curves count their control
    points, as `rect` does."""
    x0, y0, x1, y1 = rect[0], rect[1], rect[2], rect[3]
    for item in items:
        op = item[0]
        if op == "re":
            points = ((item[1][0], item[1][1]), (item[1][2], item[1][3]))
        elif op == "qu":
            points = item[1]
        else:  # "l" and "c": their points
            points = item[1:]
        for x, y in points:
            if x < x0:
                x0 = x
            elif x > x1:
                x1 = x
            if y < y0:
                y0 = y
            elif y > y1:
                y1 = y
    return x0, y0, x1, y1


AXIS_MIN_PT = 48.0  # pt: each of a plot's two axes is at least this long
AXIS_CLOSE_PT = 4.5  # pt: a long line's end this close to an axis's far end closes a box
TICK_MAX_PT = 16.0  # pt: a tick mark across or on a ruler's line is at most this long
RULER_TICKS = 3  # tick marks at different places make a line a ruler (a frame's corner marks are 2)
STRAIGHT_MIN_PT = 3.0  # pt: shorter horizontal and vertical lines are not looked at
AXES_MAX_LINES = 20_000  # a page with more straight lines (a grid, hatching) is not looked at
PATH_MAX_LINES = 200  # nor are the lines of a path with more (a grid drawn as one path)


@dataclass(frozen=True, slots=True)
class _Straight:
    """A horizontal or vertical line of a path: at `c` (y for a horizontal line, x for a
    vertical one) from `lo` to `hi` along it, display space."""

    horizontal: bool
    c: float
    lo: float
    hi: float

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        if self.horizontal:
            return (self.lo, self.c), (self.hi, self.c)
        return (self.c, self.lo), (self.c, self.hi)


def _straight_lines(
    path: _Path, segments: list[tuple[float, float, float, float]]
) -> list[_Straight]:
    """The horizontal and vertical lines of a path at least STRAIGHT_MIN_PT long; a rule
    drawn as a thin filled rectangle is its middle line. A line drawn there and back (as
    MuPDF reports a closed one-segment path) is one line."""
    if not segments and path.kind in ("hrule", "vrule"):
        x0, y0, x1, y1 = path.bbox
        if path.kind == "hrule":
            return [_Straight(True, (y0 + y1) / 2, x0, x1)]
        return [_Straight(False, (x0 + x1) / 2, y0, y1)]
    found: dict[tuple[bool, int, int, int], _Straight] = {}
    for px, py, qx, qy in segments:
        if abs(py - qy) <= 0.5 and abs(px - qx) >= STRAIGHT_MIN_PT:
            line = _Straight(True, (py + qy) / 2, min(px, qx), max(px, qx))
        elif abs(px - qx) <= 0.5 and abs(py - qy) >= STRAIGHT_MIN_PT:
            line = _Straight(False, (px + qx) / 2, min(py, qy), max(py, qy))
        else:
            continue
        found.setdefault((line.horizontal, round(line.c), round(line.lo), round(line.hi)), line)
        if len(found) > PATH_MAX_LINES:
            return []
    return list(found.values())


def _mark_axes(paths: list[_Path], lines: list[list[_Straight]]) -> None:
    """Flag the paths that hold a plot's axes (`_Path.axis`) or a ruler (`_Path.ruler`),
    whether each line is its own path or one path holds them all.

    Axes: a horizontal and a vertical line, each at least AXIS_MIN_PT long, meeting end to
    end at a corner (JOIN_PT), with nothing else at that corner or at their other ends: an
    L. A frame drawn line by line meets itself at every corner; a U or a staircase has
    more than two lines. A ruler: a line at least AXIS_MIN_PT long that RULER_TICKS or more
    short perpendicular lines (TICK_MAX_PT, at most a quarter of it) cross or end on, at
    different places along it, where a tick that ends on it has its other end free (a
    table's row has its column rules end on the rules above and below: not ticks). A
    y-axis near the page's edge and as tall as the page read as a side of a frame, and a
    timeline across the top of a page as a header rule (the second final verify,
    2026-10-12): an axis or a ruler is never page furniture (`_page_furniture`)."""
    unique: dict[tuple[bool, int, int, int], tuple[_Straight, list[int]]] = {}
    for owner, own in enumerate(lines):
        for line in own:
            key = (line.horizontal, round(line.c), round(line.lo), round(line.hi))
            unique.setdefault(key, (line, []))[1].append(owner)
    flat = list(unique.values())
    if not flat or len(flat) > AXES_MAX_LINES:
        return
    ends: dict[tuple[int, int], list[int]] = {}
    for k, (line, _) in enumerate(flat):
        for x, y in line.ends():
            ends.setdefault((int(x // JOIN_PT), int(y // JOIN_PT)), []).append(k)

    def meeting(k: int, point: tuple[float, float]) -> set[int]:
        """The other lines with an end within JOIN_PT of `point`."""
        x, y = point
        bx, by = int(x // JOIN_PT), int(y // JOIN_PT)
        found = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in ends.get((bx + dx, by + dy), ()):
                    if m != k and any(
                        abs(ex - x) <= JOIN_PT and abs(ey - y) <= JOIN_PT
                        for ex, ey in flat[m][0].ends()
                    ):
                        found.add(m)
        return found

    def flag(k: int, attribute: str) -> None:
        for owner in flat[k][1]:
            setattr(paths[owner], attribute, True)

    long = [k for k, (line, _) in enumerate(flat) if line.hi - line.lo >= AXIS_MIN_PT]

    def closed_by(point: tuple[float, float], horizontal: bool) -> bool:
        """A long line of that orientation has an end within AXIS_CLOSE_PT of `point`: the
        L is part of a box whose corners do not quite meet (a report's box of four lines,
        2026-10-13), not axes."""
        x, y = point
        return any(
            flat[j][0].horizontal == horizontal
            and any(
                abs(ex - x) <= AXIS_CLOSE_PT and abs(ey - y) <= AXIS_CLOSE_PT
                for ex, ey in flat[j][0].ends()
            )
            for j in long
        )

    for k in long:
        line = flat[k][0]
        if not line.horizontal:
            continue
        for corner, other in (line.ends(), line.ends()[::-1]):
            met = meeting(k, corner)
            if len(met) != 1:
                continue
            (m,) = met
            upright = flat[m][0]
            if upright.horizontal or upright.hi - upright.lo < AXIS_MIN_PT or meeting(k, other):
                continue
            near, far = upright.ends()
            if abs(near[0] - corner[0]) > JOIN_PT or abs(near[1] - corner[1]) > JOIN_PT:
                near, far = far, near
            if closed_by(other, False) or closed_by(far, True):
                continue
            if meeting(m, near) == {k} and not meeting(m, far):
                flag(k, "axis")
                flag(m, "axis")
                for owner in {*flat[k][1], *flat[m][1]}:
                    paths[owner].axes += ((line, upright),)
    # Rulers: short lines by orientation, in bands of TICK_MAX_PT of their middle.
    short: dict[tuple[bool, int], list[int]] = {}
    for k, (line, _) in enumerate(flat):
        if line.hi - line.lo <= TICK_MAX_PT:
            middle = (line.lo + line.hi) / 2
            short.setdefault((line.horizontal, int(middle // TICK_MAX_PT)), []).append(k)
    across: dict[tuple[bool, int], list[int]] = {}  # every line, by orientation and place
    for k, (line, _) in enumerate(flat):
        across.setdefault((line.horizontal, int(line.c // JOIN_PT)), []).append(k)

    def on_parallel(point: tuple[float, float], horizontal: bool, skip: int) -> bool:
        """`point` lies on a line of that orientation other than `skip`."""
        x, y = point
        c, t = (y, x) if horizontal else (x, y)
        for b in (int(c // JOIN_PT) - 1, int(c // JOIN_PT), int(c // JOIN_PT) + 1):
            for m in across.get((horizontal, b), ()):
                other = flat[m][0]
                if (
                    m != skip
                    and abs(other.c - c) <= JOIN_PT
                    and other.lo - JOIN_PT <= t <= other.hi + JOIN_PT
                ):
                    return True
        return False

    for k in long:
        line = flat[k][0]
        length = line.hi - line.lo
        band = int(line.c // TICK_MAX_PT)
        ticks: list[int] = []
        places: set[int] = set()
        sides: set[int] = set()  # which side of the line each tick stands on (0: across it)
        for b in (band - 1, band, band + 1):
            for t in short.get((not line.horizontal, b), ()):
                tick = flat[t][0]
                if tick.hi - tick.lo > length / 4:
                    continue
                if not line.lo - JOIN_PT <= tick.c <= line.hi + JOIN_PT:
                    continue  # beyond the line's ends
                if tick.lo > line.c + JOIN_PT or tick.hi < line.c - JOIN_PT:
                    continue  # does not reach it
                if not (tick.lo < line.c - 1 and tick.hi > line.c + 1):  # ends on it
                    free = tick.hi if abs(tick.lo - line.c) <= JOIN_PT else tick.lo
                    point = (tick.c, free) if line.horizontal else (free, tick.c)
                    if on_parallel(point, line.horizontal, k):
                        continue  # a column rule between two rules, not a tick
                    sides.add(1 if free > line.c else -1)
                else:
                    sides.add(0)
                ticks.append(t)
                places.add(round(tick.c))
        if len(places) >= RULER_TICKS:
            flag(k, "ruler")
            for t in ticks:
                flag(t, "ruler")
            if len(sides) == 1 and 0 not in sides and _cells(sorted(places), ticks, flat):
                flag(k, "comb")
                for t in ticks:
                    flag(t, "comb")


COMB_CELL_PT = (8.0, 30.0)  # pt: an answer field's letter cells are this wide
COMB_EVEN = 1.3  # its widest cell is at most this x its narrowest


def _cells(places: list[int], ticks: list[int], flat: list[tuple[_Straight, list[int]]]) -> bool:
    """Ticks at even places a letter cell apart (COMB_CELL_PT, COMB_EVEN), all as long:
    the cells of an answer field to write a name or a number in, one letter each."""
    gaps = [b - a for a, b in pairwise(places)]
    lengths = [flat[t][0].hi - flat[t][0].lo for t in ticks]
    return (
        COMB_CELL_PT[0] <= min(gaps)
        and max(gaps) <= COMB_CELL_PT[1]
        and max(gaps) <= COMB_EVEN * min(gaps)
        and max(lengths) <= COMB_EVEN * min(lengths)
    )


def _encloses(items: Sequence[Any], close_path: object) -> bool:
    """The path closes round an area (a box, a polygon, a ring): not a pen mark's open
    stroke. A line that goes back on itself (MuPDF reports a closed one-segment path as
    there and back) encloses nothing."""
    if any(item[0] in ("re", "qu") for item in items):
        return True
    points = [q for item in items if item[0] in ("l", "c") for q in item[1:]]
    if len(points) < 3:
        return False
    first, last = points[0], points[-1]
    if not close_path and (abs(first[0] - last[0]) >= 0.3 or abs(first[1] - last[1]) >= 0.3):
        return False
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in pairwise([*points, points[0]]))
    return abs(area2) > 1.0  # pt^2, twice the area


def _border_like(items: Sequence[Any], display: _Display, box: BBox) -> bool:
    """The path runs along the edges of its own box only, all four of them: the shape of a
    frame, single or double, with square, rounded, ticked or curled corners, drawn as one
    path. Each line or curve stays near one edge (all its points within the band of that
    edge; a rounded corner is near both of its edges), and each rectangle or quad lies
    along all four. A plot's grid, axes or curves drawn in the same path cross the inside;
    a plot's two axes drawn as one L-shaped path run along two edges only (2026-10-12: a
    full-page plot's axes were set aside as a frame)."""
    x0, y0, x1, y1 = box
    band = max(MIN_IMAGE_PT, FRAME_LINE_BAND * min(x1 - x0, y1 - y0))
    if min(x1 - x0, y1 - y0) <= 4 * band:
        return False  # too small or too thin to tell its edges from its inside
    edges = [
        lambda q: q[0] - x0,
        lambda q: x1 - q[0],
        lambda q: q[1] - y0,
        lambda q: y1 - q[1],
    ]
    along = [False] * 4
    for item in items:
        if item[0] in ("re", "qu"):
            corners = (
                [(item[1][0], item[1][1]), (item[1][2], item[1][3])]
                if item[0] == "re"
                else list(item[1])
            )
            points = [display.point(*c) for c in corners]
            xs, ys = [q[0] for q in points], [q[1] for q in points]
            if max(min(xs) - x0, x1 - max(xs), min(ys) - y0, y1 - max(ys)) > band:
                return False
            along = [True] * 4
            continue
        points = [display.point(*q) for q in item[1:]]
        near = [all(distance(q) <= band for q in points) for distance in edges]
        if not any(near):
            return False
        along = [a or n for a, n in zip(along, near, strict=True)]
    return all(along)


def _rectangle_outline(
    items: Sequence[Any], segments: list[tuple[float, float, float, float]], box: BBox
) -> bool:
    """One upright rectangle and nothing else: a single "re" item, or four or five
    horizontal and vertical lines along the edges of the path's box (a page frame drawn
    line by line, closed or not)."""
    if len(items) == 1 and items[0][0] == "re":
        return True
    if len(segments) != len(items) or not 4 <= len(segments) <= 5:
        return False
    x0, y0, x1, y1 = box
    for px, py, qx, qy in segments:
        on_x = abs(px - qx) <= 0.5 and min(abs(px - x0), abs(px - x1)) <= 0.5
        on_y = abs(py - qy) <= 0.5 and min(abs(py - y0), abs(py - y1)) <= 0.5
        if not (on_x or on_y):
            return False
    return True


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


_EXERCISE_NUMBER = re.compile(r"^\d{1,3}[.)]$")


def _split_exercise_numbers(rows: list[_Row]) -> list[_Row]:
    """Cut an exercise number ("18.", "4)") off the front of a maths row into a text row.

    A numbered exercise that is only an equation ("18. 0 = 5 t - 4.9 t") reads as one maths
    row because `_merge_baselines` joins the number and the equation when they sit on one
    baseline. The number would then be sent to the model as part of the equation, and
    would stand in its text layer. The first token is cut off when it is a one- to
    three-digit number with a full stop or bracket, drawn in a text font (no maths font,
    no maths character), with a gap before the rest, and the rest alone reads as a display
    equation. A row that already starts a list item (`row.marker`) keeps its item, and a
    row whose rest is prose or a lone fragment is not touched. The number stays in the
    document as a text row of its own, in front of the equation (`_Row.exercise_number`).
    """
    out: list[_Row] = []
    for row in rows:
        tokens = _tokens(row)
        if row.vertical or row.marker is not None or len(tokens) < 2:
            out.append(row)
            continue
        first, rest = tokens[0], tokens[1:]
        end = first[1]
        token = "".join(c.c for c in row.chars[first[0] : end])
        if token.isdigit() and "".join(c.c for c in row.chars[slice(*rest[0])]) in (".", ")"):
            end = rest[0][1]  # "18 ." (the number and its full stop in separate spans)
            rest = rest[1:]
            token += row.chars[end - 1].c
        number = row.chars[first[0] : end]
        if (
            not rest
            or not _EXERCISE_NUMBER.match(token)
            or any(c.math or c.mono for c in number)
            or row.chars[rest[0][0]].x0 - number[-1].x1 < 0.3 * row.size
        ):
            out.append(row)
            continue
        remainder = _Row(row.chars[rest[0][0] :], column=row.column)
        _classify_row(remainder)
        if remainder.kind != "math":
            out.append(row)
            continue
        out.append(_Row(number, column=row.column, exercise_number=True))
        out.append(_Row(row.chars[rest[0][0] :], column=row.column))
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
    xref: int = 0  # the image of a figure that is one raster image (0: inline, or several)

    @property
    def reasons(self) -> list[str]:
        reasons = ["raster image"] if self.images == 1 else []
        if self.images > 1:
            reasons.append(f"{self.images} raster images")
        if self.paths:
            reasons.append(f"vector drawing ({self.paths} paths)")
        return reasons


def _raster_figures(page: pymupdf.Page, page_rect: pymupdf.Rect) -> list[_Figure]:
    """Raster images as figures. Decoration is decided by the size drawn, not the pixels
    (a 21-pixel QR code drawn an inch wide is content): an image drawn under MIN_IMAGE_PT
    on a side is a bullet, a spacer or a rule; one with fewer than MIN_IMAGE_PX pixels on
    a side is decoration only when it is plain (`_plain_image`: a transparent spacer, a
    one-colour block). A picture covering the page is the page's own (a background, a
    scan): never a figure; whether the page keeps its image is `_page_picture`'s."""
    display = _Display.of(page)
    figures = []
    page_area = page_rect.width * page_rect.height
    infos = page.get_image_info()
    xrefs = _image_xrefs(page, len(infos)) if infos else []
    for k, info in enumerate(infos):
        rect = pymupdf.Rect(display.box(info["bbox"])) & page_rect
        if rect.is_empty:
            continue
        if min(rect.width, rect.height) < MIN_IMAGE_PT:
            continue  # bullets, spacers, rules drawn as images
        if rect.width * rect.height >= FULL_PAGE_FRACTION * page_area:
            continue  # the page's background or a scan of it: `_page_picture`
        w_px, h_px = int(info.get("width") or 0), int(info.get("height") or 0)
        if min(w_px, h_px) < MIN_IMAGE_PX and _plain_image(page.parent, xrefs[k]):
            continue  # a spacer or a fill drawn as an image
        figures.append(_Figure((rect.x0, rect.y0, rect.x1, rect.y1), images=1, xref=xrefs[k]))
    return figures


def _image_xrefs(page: pymupdf.Page, count: int) -> list[int]:
    """The xref of each of the page's images, in `get_image_info` order (0 for an inline
    image, or when the lists disagree)."""
    xrefs = [int(info.get("xref") or 0) for info in page.get_image_info(xrefs=True)]
    return xrefs if len(xrefs) == count else [0] * count


# A plain image: looked at shrunk to at most PLAIN_SIDE pixels on its longer side, at most
# PLAIN_EDGES pairs of neighbouring pixels differ by more than PLAIN_STEP levels (JPEG
# noise on a flat colour; a gradient changes a level or two a pixel), and what is seen
# (alpha above ALPHA_SEEN) covers all of it or nothing.
PLAIN_SIDE = 128
PLAIN_STEP = 16
PLAIN_EDGES = 2
ALPHA_SEEN = 13
SEEN_WHOLE = 0.98


def _plain_image(doc: pymupdf.Document | None, xref: int) -> bool:
    """The image shows no picture: nothing in it is seen (a transparent spacer), or it is
    seen whole in one colour or a smooth gradient (a wash, a template's background). A
    picture with transparent parts (ink on a transparent overlay, a stencil mask's
    shapes) is not plain, and neither is an image that cannot be read (an inline one)."""
    if doc is None or xref <= 0:
        return False
    try:
        pix = pymupdf.Pixmap(doc, xref)
        key = doc.xref_get_key(xref, "SMask")
        mask = pymupdf.Pixmap(doc, int(key[1].split()[0])) if key[0] == "xref" else None
        if pix.colorspace is None:  # a stencil mask: its alpha is what is painted
            mask, pix = pix, None
        if pix is not None and (pix.alpha or pix.colorspace.n not in (1, 3)):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix) if pix.colorspace.n not in (1, 3) else pix
            pix = pymupdf.Pixmap(pix, 0) if pix.alpha else pix
    except (RuntimeError, ValueError, IndexError, AttributeError):
        return False
    if mask is not None:
        _shrink(mask)
        alpha = mask.samples[mask.n - 1 :: mask.n]
        seen = sum(1 for a in alpha if a > ALPHA_SEEN)
        if seen == 0:
            return True
        if seen < SEEN_WHOLE * len(alpha):
            return False
    if pix is None:
        return True  # a stencil painted whole: one colour
    _shrink(pix)
    return _edges(pix) <= PLAIN_EDGES


def _shrink(pix: pymupdf.Pixmap) -> None:
    factor = 0
    while max(pix.width, pix.height) >> factor > PLAIN_SIDE:
        factor += 1
    if factor:
        pix.shrink(factor)


def _edges(pix: pymupdf.Pixmap) -> int:
    """Pairs of neighbouring pixels (across and down) that differ by more than PLAIN_STEP
    in some channel."""
    w, h, n, stride, data = pix.width, pix.height, pix.n, pix.stride, pix.samples
    edges = 0
    for y in range(h):
        row = y * stride
        below = row + stride if y + 1 < h else None
        for x in range(w):
            i = row + x * n
            for c in range(n):
                v = data[i + c]
                if x + 1 < w and abs(v - data[i + n + c]) > PLAIN_STEP:
                    edges += 1
                    break
                if below is not None and abs(v - data[below + x * n + c]) > PLAIN_STEP:
                    edges += 1
                    break
            if edges > PLAIN_EDGES:
                return edges
    return edges


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


def _vector_figures(
    paths: list[_Path],
    rows: list[_Row],
    page_rect: pymupdf.Rect,
    ctx: _Context | None = None,
    *,
    page_sized: bool = True,
    left_out: list[BBox] | None = None,
    depth: int = 0,
    small_out: list[tuple[BBox, list[_Path]]] | None = None,
    ink: Collection[int] = frozenset(),
    hand: Sequence[BBox] = (),
) -> list[_Figure]:
    """Clusters of drawings that look like a picture, not rules, borders or tables.

    Page furniture (a frame round the page, a page-wide rule along an edge that is no
    plot's axis or ruler, a background fill) is set aside first: a frame's box holds
    everything on the page, so clustered with the rest it made every figure on the page
    one "page border" (docs/layout.md "Figures"). A drawing that still fills the page (a
    full-page plot, a slide's chart) is a figure when it is a plot (a plotted line or
    bars on axes or a ruler with evidence of a plot: `_mark_plots`), whatever text is
    inside its axes (a title, a note: it stays in the reading flow), or when it has three
    curves or two bars and no running text inside. Else it may be a decorated border or
    a background drawn behind text, but a drawing must not be lost with it: its border
    (`_border_member`) is set aside and the rest clustered again, and when nothing is
    made of a rest that holds curves or bars, or there is no border to take off, a
    page-filling drawing that holds a plotted line, curves or bars goes to `left_out`,
    and the safety net keeps the page image. With `page_sized` False (the page has ink:
    it may be a page of notes) such a drawing is never a figure for the model: its box
    goes to `left_out` too (`INK_REASONS`).

    Rules alone (separators, underlines, a table's grid) and boxes or panels round text
    are not figures, unless they show signs of a drawing (`_diagram_signs`: axes, a
    ruler, markers, a polyline; with no running text inside, also lines joined end to end
    into a staircase, boxes joined by lines, a line leaving a box or running along
    several: `_connected`). Without such signs a cluster whose text rows are not all held
    by its boxes, panels or rules (`_round_text`) goes to `left_out`: something is drawn
    there that is no box round text. Pen strokes (`ink`) and what lies in the page's
    handwriting regions (`hand`) are no signs. Clusters too small to be figures that are
    no part of one go to `small_out`: the page decides what they are once its text is
    read (`_leftover_drawings`)."""
    figures = []
    small: list[tuple[BBox, list[_Path]]] = []  # drawings too small to be figures on their own
    index = _RowIndex([r for r in rows if not r.vertical])
    paths = [p for p in paths if not _page_furniture(p, page_rect)]
    for group in _overlapping(_cluster([p.bbox for p in paths], DRAWING_CLUSTER_GAP), paths):
        members = [paths[i] for i in group]
        rule_only = all(p.kind in ("hrule", "vrule") or p.rules for p in members)
        ruler = any(p.ruler for p in members)  # a timeline, an axis with ticks
        if (
            rule_only
            and not ruler
            and not _line_chain(members)
            and not _connected(members)
            and not any(_bond_ends(p, index) for p in members)
        ):
            continue  # separators, underlines, table grids, paper rules
        x0, y0, x1, y1 = _union(p.bbox for p in members)
        box = (max(x0, 0.0), max(y0, 0.0), min(x1, page_rect.width), min(y1, page_rect.height))
        width, height = box[2] - box[0], box[3] - box[1]  # the visible part only
        if width < MIN_DRAWING_PT or height < MIN_DRAWING_PT or width * height < 1200:
            small.append((box, members))
            continue
        fills_page = (
            width >= PAGE_SIZED_FRACTION * page_rect.width
            and height >= PAGE_SIZED_FRACTION * page_rect.height
        )
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
        if fills_page:
            if not page_sized:
                if left_out is not None:
                    left_out.append(box)
                continue
            # A plotted line or bars on axes (or on a ruler: an axis with ticks) with
            # evidence of a plot is a plot, not a decorated border, with one curve and
            # with running text inside (which stays in the reading flow: `_absorb_labels`).
            plot = (curved or len(bars) >= 2) and any(p.plot or p.ruler for p in members)
            running = ctx is not None and bool(_running_text(inside, ctx))
            figure = plot or not ((len(curves) < 3 and len(bars) < 2) or running)
            inner = [p for p in members if not _border_member(p, box, page_rect)]
            border = bool(inner) and len(inner) < len(members) and depth < 2
            if border and (plot or not figure):
                # A plot inside a border of dashes or ornaments is the plot without them;
                # anything else is clustered again once its border is off.
                before = (len(left_out or ()), len(small_out or ()))
                found = _vector_figures(
                    inner,
                    rows,
                    page_rect,
                    ctx,
                    page_sized=page_sized,
                    left_out=left_out,
                    depth=depth + 1,
                    small_out=small_out,
                    ink=ink,
                    hand=hand,
                )
                _take_border_marks(found, [p for p in members if p not in inner])
                figures += found
                after = (len(left_out or ()), len(small_out or ()))
                # Nothing was made of what is left once the border is off: if it held
                # curves or bars, the page image keeps them.
                if (
                    not found
                    and after == before
                    and left_out is not None
                    and any(p.curved or p in bars for p in inner)
                ):
                    left_out.append(box)
                continue
            if not figure:
                if (curved or len(bars) >= 2) and left_out is not None:
                    left_out.append(box)  # drawings no figure was made of: the page image
                continue
        elif not curved and len(bars) < 2 and (len(inside) >= 2 or rule_only):
            # A box, shaded panel or table drawn around text, unless it shows signs of a
            # drawing (axes, a connector, markers, a polyline: a diagram with labels in
            # it, whose running text stays in the reading flow). With neither signs nor
            # every row held by its boxes or rules, the page image keeps it.
            signs = _diagram_signs(members, index, ink, hand)
            running = ctx is not None and bool(_running_text(inside, ctx))
            if signs != "drawing" and (signs is None or running):
                if not _round_text(members, inside) and left_out is not None:
                    left_out.append(box)
                continue
        if rule_only:  # a Fischer projection's bonds: the atom groups at their ends too
            box = _union([box, *(end for p in members for end in _bond_ends(p, index))])
        figures.append(_Figure(box, paths=len(members)))
    # Small drawings just outside a figure (axis titles and tick labels drawn as outlines,
    # a legend's markers a little apart) are part of it: left out, its crop missed them.
    for box, members in small:
        for figure in figures:
            if _within(box, figure.bbox, FIGURE_PART_REACH):
                figure.bbox = _union([figure.bbox, box])
                figure.paths += len(members)
                break
        else:
            if small_out is not None:
                small_out.append((box, members))
    return figures


def _take_border_marks(figures: Sequence[_Figure], border: Sequence[_Path]) -> None:
    """The marks smaller than a figure among a page-filling cluster's `border` that lie
    within FIGURE_PART_REACH of a figure made of the rest are part of it, spreading along
    a row of them: a plot's axes drawn as dashes, set aside as a frame's pieces along two
    edges (2026-10-13: the curve was a figure and its dashed axes were lost). A frame of
    dashes round the page stays apart from the plot inside it."""
    marks = [
        p for p in border if max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) < MIN_DRAWING_PT
    ]
    grew = bool(figures)
    while marks and grew:
        grew = False
        rest = []
        for path in marks:
            near = next((f for f in figures if _within(path.bbox, f.bbox, FIGURE_PART_REACH)), None)
            if near is None:
                rest.append(path)
                continue
            near.bbox = _union([near.bbox, path.bbox])
            near.paths += 1
            grew = True
        marks = rest


JOIN_PT = 1.5  # pt: line ends this close meet (a corner of a staircase, a connector's end)
MAX_DIAGRAM_BOXES = 200  # more box outlines in one cluster are a grid of cells, not a diagram
ORIGIN_REACH = 4.0  # pt: a plotted line that starts this close to its axes' corner starts there
AXIS_LABEL_REACH = 3.0  # x font size: a tick label or axis title is this close outside an axis


def _answer_fields(paths: Sequence[_Path], rows: Sequence[_Row]) -> None:
    """Rulers whose ticks stand on one side of the line a letter cell apart (`_Path.comb`)
    with no short row by their ticks (a tick label) are an answer field's cells (a comb
    to write a candidate number or a name in): no ruler, so rules alone, not a drawing
    (2026-10-13: an exam cover's two comb fields became figures and decisions). A label
    beside the field's start ("Candidate number") is the field's own, not a tick's."""
    combs = [p for p in paths if p.comb]
    if not combs:
        return
    labels = [r for r in rows if not r.vertical and len(r.clean) <= LABEL_MAX_CHARS]
    for group in _cluster([p.bbox for p in combs], JOIN_PT):
        members = [combs[i] for i in group]
        x0, y0, x1, y1 = _union(p.bbox for p in members)
        horizontal = x1 - x0 >= y1 - y0
        labelled = False
        for row in labels:
            reach = 1.5 * max(row.size, (y1 - y0) if horizontal else (x1 - x0))
            mx, my = (row.x0 + row.x1) / 2, (row.y0 + row.y1) / 2
            if horizontal and x0 <= mx <= x1 and _gap(row.bbox, (x0, y0, x1, y1)) <= reach:
                labelled = True
            if not horizontal and y0 <= my <= y1 and _gap(row.bbox, (x0, y0, x1, y1)) <= reach:
                labelled = True
        if not labelled:
            for p in members:
                p.ruler = p.comb = False


def _mark_plots(paths: Sequence[_Path], rows: Iterable[_Row]) -> None:
    """Flag the paths that hold an L of axes (`_mark_axes`) with evidence of a plot
    (`_Path.plot`), not a frame's two sides or a template's accent: tick marks on it (a
    ruler), a curve starting at its corner (ORIGIN_REACH), two bars standing on it, or a
    short row (a tick label, an axis title; LABEL_MAX_CHARS) just outside one of its lines
    (AXIS_LABEL_REACH x its size), on the side away from the other line. `rows` are the
    page's rows, the ones the header and footer rules dropped too (a full-page plot's tick
    labels are in the footer band). A title slide's L-shaped accent and the wave across it
    have none of these (2026-10-13: the slide became one figure whose labels were its
    title and subtitle)."""
    owners: dict[tuple[_Straight, _Straight], list[_Path]] = {}
    for p in paths:
        for pair in p.axes:
            owners.setdefault(pair, []).append(p)
    if not owners:
        return
    labels = [r for r in rows if len(r.clean) <= LABEL_MAX_CHARS and not CAPTION.match(r.clean)]
    ends = [(x, y) for p in paths for x, y in p.ends]
    filled = [p for p in paths if p.filled]
    for (h, v), held in owners.items():
        if any(p.plot for p in held):
            continue
        if any(p.ruler for p in held) or _plot_evidence(h, v, ends, filled, labels):
            for p in held:
                p.plot = True


def _plot_evidence(
    h: _Straight,
    v: _Straight,
    ends: Sequence[tuple[float, float]],
    filled: Sequence[_Path],
    labels: Sequence[_Row],
) -> bool:
    """`_mark_plots`' tests for one L: a curve end at its corner, two bars standing on its
    horizontal line, or a label just outside one of its lines."""
    # The corner, and which way each line runs from it.
    cx = h.lo if abs(h.lo - v.c) <= abs(h.hi - v.c) else h.hi
    cy = v.lo if abs(v.lo - h.c) <= abs(v.hi - h.c) else v.hi
    right = h.hi - cx > cx - h.lo  # the horizontal line runs right from the corner
    up = cy - v.lo > v.hi - cy  # the vertical line runs up from the corner
    if any(abs(x - cx) <= ORIGIN_REACH and abs(y - cy) <= ORIGIN_REACH for x, y in ends):
        return True
    standing = [
        p
        for p in filled
        if h.lo <= (p.bbox[0] + p.bbox[2]) / 2 <= h.hi
        and abs((p.bbox[3] if up else p.bbox[1]) - h.c) <= JOIN_PT + 1
    ]
    if len(standing) >= 2:
        return True
    for row in labels:
        reach = AXIS_LABEL_REACH * row.size
        mx, my = (row.x0 + row.x1) / 2, (row.y0 + row.y1) / 2
        below = row.y0 - h.c if up else h.c - row.y1  # outside the horizontal line
        if -0.5 * row.size <= below <= reach and h.lo - reach <= mx <= h.hi + reach:
            return True
        beside = v.c - row.x1 if right else row.x0 - v.c  # outside the vertical line
        if -0.5 * row.size <= beside <= reach and v.lo - reach <= my <= v.hi + reach:
            return True
    return False


def _drawn_diagram(members: Sequence[_Path]) -> bool:
    """Lines or boxes drawn as a diagram, not as structure round text: horizontal and
    vertical lines joined end to end into an open chain (`_line_chain`: a staircase, a
    step plot, each piece its own line), or two or more separate boxes with a line that
    starts at one of them outside every box (`_joined_boxes`: an org chart, a flow of two
    boxes and one arrow). A box drawn line by line closes on itself; a table's rules meet
    in crossings and T-junctions, not end to end; underlines do not meet at all."""
    return _line_chain(members) or _joined_boxes(members)


def _rule_chains(rules: Sequence[_Path]) -> list[tuple[list[int], bool]]:
    """The horizontal and vertical `rules` joined corner to corner, end to end (JOIN_PT):
    each chain's rule indices, and whether it closes (every end meets another rule's)."""
    ends: list[tuple[float, float, int]] = []
    for i, rule in enumerate(rules):
        x0, y0, x1, y1 = rule.bbox
        if rule.kind == "hrule":
            y = (y0 + y1) / 2
            ends += [(x0, y, i), (x1, y, i)]
        else:
            x = (x0 + x1) / 2
            ends += [(x, y0, i), (x, y1, i)]
    parent = list(range(len(rules)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    buckets: dict[tuple[int, int], list[int]] = {}
    for k, (x, y, _) in enumerate(ends):
        buckets.setdefault((int(x // JOIN_PT), int(y // JOIN_PT)), []).append(k)
    met = [False] * len(ends)
    for k, (x, y, i) in enumerate(ends):
        bx, by = int(x // JOIN_PT), int(y // JOIN_PT)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in buckets.get((bx + dx, by + dy), ()):
                    ox, oy, j = ends[m]
                    if j == i or rules[i].kind == rules[j].kind:
                        continue  # a corner joins a horizontal and a vertical line
                    if abs(ox - x) <= JOIN_PT and abs(oy - y) <= JOIN_PT:
                        met[k] = True
                        parent[find(i)] = find(j)
    chains: dict[int, list[int]] = {}
    for i in range(len(rules)):
        chains.setdefault(find(i), []).append(i)
    closed = {find(i): True for i in range(len(rules))}
    for k, (_, _, i) in enumerate(ends):
        if not met[k]:
            closed[find(i)] = False
    return [(chain, closed[root]) for root, chain in chains.items()]


def _line_chain(members: Sequence[_Path]) -> bool:
    """Four or more horizontal and vertical rules (two of each at least) joined corner to
    corner, end to end, into a chain that does not close (some end meets nothing)."""
    rules = [p for p in members if p.kind in ("hrule", "vrule")]
    if len(rules) < 4:
        return False
    for chain, closed in _rule_chains(rules):
        kinds = Counter(rules[i].kind for i in chain)
        if len(chain) >= 4 and kinds["hrule"] >= 2 and kinds["vrule"] >= 2 and not closed:
            return True
    return False


def _rule_boxes(members: Sequence[_Path]) -> list[tuple[BBox, list[_Path]]]:
    """Boxes drawn line by line: two horizontal and two vertical rules that close on each
    other corner to corner (`_rule_chains`), with the rules that draw each."""
    rules = [p for p in members if p.kind in ("hrule", "vrule")]
    if len(rules) < 4:
        return []
    found = []
    for chain, closed in _rule_chains(rules):
        sides = [rules[i] for i in chain]
        if closed and len(sides) == 4 and sum(p.kind == "hrule" for p in sides) == 2:
            found.append((_union(p.bbox for p in sides), sides))
    return found


CONTAINER_MIN_PT = 8.0  # pt: a filled panel this big on both sides can hold a row of text
MARK_MAX_PT = 8.0  # pt: a plot's marker (a small square, a plus) is at most this big
MARKS_MIN = 3  # markers in different places: a scatter plot's points


def _containers(members: Sequence[_Path]) -> tuple[list[BBox], set[int]]:
    """The boxes of a cluster that can hold text: box outlines (one path, or four rules:
    `_rule_boxes`) and filled panels (CONTAINER_MIN_PT); and the ids of the rules that
    draw the boxes drawn line by line."""
    boxes = [
        p.bbox
        for p in members
        if p.kind == "shape"
        and (p.frame or p.filled)
        and min(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) >= CONTAINER_MIN_PT
    ]
    sides: set[int] = set()
    for box, rules in _rule_boxes(members):
        boxes.append(box)
        sides.update(id(r) for r in rules)
    return boxes, sides


def _connected(members: Sequence[_Path]) -> bool:
    """A line outside every box of the cluster (`_containers`) that leaves one of them
    across its side (a connector, a whisker, a wire into a resistor's box: its end on the
    box's side, between that side's corners), or that runs along two or more boxes apart
    from each other (the baseline of a bar chart of outlined bars). A table's rule along a
    header panel runs along one box; its cells touch each other."""
    boxes, sides = _containers(members)
    if not boxes or len(boxes) > MAX_DIAGRAM_BOXES:
        return False
    reach = JOIN_PT + 1
    for path in members:
        if path.kind not in ("hrule", "vrule") or id(path) in sides:
            continue
        if any(_inside(path.bbox, b) for b in boxes):
            continue
        x0, y0, x1, y1 = path.bbox
        touching = [b for b in boxes if _within(path.bbox, b, reach)]
        for b in touching:
            if path.kind == "hrule":
                y = (y0 + y1) / 2
                across = abs(x1 - b[0]) <= reach or abs(x0 - b[2]) <= reach
                if across and b[1] + 1 < y < b[3] - 1:
                    return True
            else:
                x = (x0 + x1) / 2
                across = abs(y1 - b[1]) <= reach or abs(y0 - b[3]) <= reach
                if across and b[0] + 1 < x < b[2] - 1:
                    return True
        if any(_gap(a, b) > 2 * JOIN_PT for a, b in combinations(touching, 2)):
            return True
    return False


BOND_GAP = 1.5  # x font size: a bond ends this close to the atom groups it joins
BOND_TOKEN = 6  # characters: an atom group (H, OH, CHO, CH2OH, COOH) is at most this long


def _bond_ends(rule: _Path, index: _RowIndex) -> list[BBox]:
    """The boxes of the two atom groups a horizontal or vertical rule joins, or []: a
    token of at most BOND_TOKEN characters beyond each end of the rule, within BOND_GAP x
    its size, the rule at least as long as the tokens are tall, through the middle half of
    their height (a horizontal rule) or through their width (a vertical one). A bond of
    a structural formula or a Fischer projection (2026-10-13: dropped as rules alone). A
    blank to fill in lies on the baseline, a minus sign is shorter than its text, an
    underline and a strikeout run under or through one row."""
    if rule.kind not in ("hrule", "vrule"):
        return []
    x0, y0, x1, y1 = rule.bbox
    horizontal = rule.kind == "hrule"
    reach = BOND_GAP * index.largest
    before: BBox | None = None
    after: BBox | None = None
    for row in index.near(y0 - reach, y1 + reach):
        for start, end in _tokens(row):
            chars = [c for c in row.chars[start:end] if not c.blank]
            if not chars:
                continue
            box = _union((c.x0, c.y0, c.x1, c.y1) for c in chars)
            size = max(c.size for c in chars)
            gap = BOND_GAP * size
            # Text over or under a horizontal rule (a fraction's numerator, a word over an
            # underline), or beside a vertical one (a table's cells), is no atom group.
            if horizontal:
                over = min(box[2], x1) - max(box[0], x0) > 0.5 * (box[2] - box[0])
                near = -gap <= y0 - box[3] <= gap or -gap <= box[1] - y1 <= gap
            else:
                over = min(box[3], y1) - max(box[1], y0) > 0.5 * (box[3] - box[1])
                near = -gap <= x0 - box[2] <= gap or -gap <= box[0] - x1 <= gap
            if over and near:
                return []
            if len(chars) > BOND_TOKEN or (x1 - x0 if horizontal else y1 - y0) < box[3] - box[1]:
                continue
            if horizontal:
                y, h = (y0 + y1) / 2, box[3] - box[1]
                if not box[1] + 0.25 * h <= y <= box[3] - 0.25 * h:
                    continue
                if -1 <= x0 - box[2] <= gap:
                    before = box
                if -1 <= box[0] - x1 <= gap:
                    after = box
            else:
                if not box[0] <= (x0 + x1) / 2 <= box[2]:
                    continue
                if -1 <= y0 - box[3] <= gap:
                    before = box
                if -1 <= box[1] - y1 <= gap:
                    after = box
    return [before, after] if before is not None and after is not None else []


def _bonds(members: Sequence[_Path], index: _RowIndex) -> list[BBox]:
    """The atom groups the bonds among `members` join (`_bond_ends`), when every member
    is such a bond; else []."""
    found: list[BBox] = []
    for path in members:
        ends = _bond_ends(path, index)
        if not ends:
            return []
        found += ends
    return found


def _diagram_signs(
    members: Sequence[_Path],
    index: _RowIndex,
    ink: Collection[int] = frozenset(),
    hand: Sequence[BBox] = (),
) -> str | None:
    """Signs that a cluster of straight lines, boxes and marks with text in it is a
    drawing, not structure round text (a box, a panel, a table). "drawing" whatever text
    is in it: an L of axes or a ruler (`_mark_axes`), MARKS_MIN small marks in different
    places that are no list bullets (a scatter plot's squares or crosses), or an open
    polyline (a step, a skyline, a signal, a zigzag, a wire round a corner, an arrow; at
    least MIN_IMAGE_PT long) inside none of the cluster's other boxes (a mark drawn in a
    box round text is no sign of a diagram). "lines" when it holds no running text: lines
    joined into an open chain or boxes joined by a line (`_drawn_diagram`), or a line
    leaving a box or running along several (`_connected`), or bonds between atom groups
    (`_bond_ends`: a Fischer projection): a box round paragraphs drawn in pieces, or a
    grid of cell borders in a worked example, has them too. None without either. The
    drawings sweep of 2026-10-13: scatter plots with a legend, outlined bars on a
    baseline, histograms and step plots drawn as one path, timing diagrams, a box plot,
    circuits and flowcharts with labels inside were dropped as boxes round text. Pen
    strokes (`ink`: the page type's) and what lies in the handwriting cut out of a typed
    page (`hand`) are no sign: a hand mark in a box round text is cut out as
    handwriting, the box stays structure."""
    drawn = [
        p
        for p in members
        if p.source not in ink and not any(_centre_inside(p.bbox, r) for r in hand)
    ]
    if any(p.axis or p.ruler for p in drawn):
        return "drawing"
    marks = {
        _box_key(p.bbox)
        for p in drawn
        if p.kind == "shape"
        and max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) <= MARK_MAX_PT
        and not _bullet_mark(p, index)
    }
    if len(marks) >= MARKS_MIN:
        return "drawing"
    boxes, _ = _containers(members)
    if any(
        p.open_stroke
        and p.turns
        and p.kind == "shape"
        and max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) >= MIN_IMAGE_PT
        and not any(b != p.bbox and _inside(p.bbox, b) for b in boxes)
        for p in drawn
    ):
        return "drawing"
    if _drawn_diagram(members) or _connected(members):
        return "lines"
    if any(_bond_ends(p, index) for p in members):
        return "lines"  # bonds between atom groups
    return None


def _round_text(members: Sequence[_Path], rows: Sequence[_Row]) -> bool:
    """Every row is held by the cluster's structure: inside one of its boxes or panels
    (`_containers`), or between two of its rules that span it (a table's rows between
    its rules). Rows outside every box (the value labels over a bar chart's outlined bars)
    are not."""
    boxes, _ = _containers(members)
    boxes += [p.bbox for p in members if p.filled]  # a band of thin strips is a panel too
    grown = [(b[0] - 1, b[1] - 1, b[2] + 1, b[3] + 1) for b in boxes]
    hrules = [p.bbox for p in members if p.kind == "hrule"]
    vrules = [p.bbox for p in members if p.kind == "vrule"]
    for row in rows:
        if any(_centre_inside(row.bbox, b) for b in grown):
            continue
        cx, cy = (row.x0 + row.x1) / 2, (row.y0 + row.y1) / 2
        spans = [r for r in hrules if r[0] <= row.x0 + 1 and r[2] >= row.x1 - 1]
        if any(r[3] <= cy for r in spans) and any(r[1] >= cy for r in spans):
            continue
        spans = [r for r in vrules if r[1] <= row.y0 + 1 and r[3] >= row.y1 - 1]
        if any(r[2] <= cx for r in spans) and any(r[0] >= cx for r in spans):
            continue
        return False
    return True


def _joined_boxes(members: Sequence[_Path]) -> bool:
    """Two or more separate box outlines (none inside another) and a line (not a box, not
    a filled shape) outside every box that starts at one of them: a connector."""
    outlines = [p.bbox for p in members if p.frame and p.kind == "shape"]
    if not 2 <= len(outlines) <= MAX_DIAGRAM_BOXES:
        return False  # one box round text, or a grid of cells (a table)
    boxes = [
        b for b in outlines if not any(_area(o) > _area(b) and _inside(b, o) for o in outlines)
    ]
    if len(boxes) < 2:
        return False
    grown = [(b[0] - 1, b[1] - 1, b[2] + 1, b[3] + 1) for b in boxes]
    for path in members:
        if path.frame or path.filled:
            continue
        if any(_centre_inside(path.bbox, b) for b in grown):
            continue
        if any(_within(path.bbox, b, JOIN_PT) for b in boxes):
            return True
    return False


def _inside(box: BBox, outer: BBox) -> bool:
    return (
        outer[0] - 1 <= box[0] and box[2] <= outer[2] + 1 and outer[1] - 1 <= box[1]
        and box[3] <= outer[3] + 1
    )  # fmt: skip


def _within(box: BBox, other: BBox, reach: float) -> bool:
    """`box` comes within `reach` of `other` (or overlaps it)."""
    return (
        box[0] <= other[2] + reach
        and other[0] <= box[2] + reach
        and box[1] <= other[3] + reach
        and other[1] <= box[3] + reach
    )


def _page_furniture(path: _Path, page_rect: pymupdf.Rect) -> bool:
    """A frame round the page (an outline along the edges of its own box, whose sides are
    each within FRAME_EDGE_BAND of the page's: any corners, single or double; or a
    page-long rule near an edge), a background fill (at least PAGE_SIZED_FRACTION of the
    page's area, or a straight-edged panel round the page with a margin) or a band of
    colour along an edge (a slide template's title bar): never part of a figure, and
    never content on its own. A page-long rule that is a plot's axis or a ruler's line
    (`_mark_axes`: a full-page plot's y-axis, a timeline across the top) is a drawing; an
    L round the page with no evidence of a plot (`_mark_plots`: a template's accent along
    two edges) is two sides of a frame."""
    x0, y0, x1, y1 = path.bbox
    w, h = page_rect.width, page_rect.height
    big = PAGE_SIZED_FRACTION
    if path.filled:
        return (
            (x1 - x0) * (y1 - y0) >= big * w * h
            or _edge_band(path, page_rect)
            or (not path.curved and _round_page(path.bbox, page_rect))
        )
    if path.frame:
        return _round_page(path.bbox, page_rect)
    if path.curved or path.ruler or path.plot:
        return False
    if path.axis and path.kind == "shape":  # an L drawn as one path
        return _round_page(path.bbox, page_rect)
    edge = FRAME_EDGE_BAND
    if path.kind == "hrule":
        return x1 - x0 >= big * w and (y1 <= edge * h or y0 >= (1 - edge) * h)
    if path.kind == "vrule":
        return y1 - y0 >= big * h and (x1 <= edge * w or x0 >= (1 - edge) * w)
    return False


def _border_member(path: _Path, box: BBox, page_rect: pymupdf.Rect) -> bool:
    """Part of the border of a page-filling cluster (`box`): frame-shaped, an L with no
    evidence of a plot (`_mark_plots`: a frame's two sides, a template's accent), straight
    and across the page (PAGE_SIZED_FRACTION of its width or height), curved and across
    the page along one of its edges (within FRAME_EDGE_BAND), or a mark smaller than a
    figure along an edge of the cluster: the pieces of a frame whose shape was not
    recognised, corner ornaments. A plot's axes and a ruler (`_mark_axes`) are not; nor
    is a curve across the middle of the page (a wave across a title slide, a plotted
    line: 2026-10-13, a full-page plot's curve was set aside and its axes alone made
    nothing)."""
    x0, y0, x1, y1 = path.bbox
    w, h = page_rect.width, page_rect.height
    if path.ruler or path.plot:
        return False
    if path.frame or path.axis:
        return True
    if x1 - x0 >= PAGE_SIZED_FRACTION * w or y1 - y0 >= PAGE_SIZED_FRACTION * h:
        return not path.curved or _in_edge_band(path.bbox, page_rect)
    if max(x1 - x0, y1 - y0) >= MIN_DRAWING_PT:
        return False
    bx, by = FRAME_EDGE_BAND * w, FRAME_EDGE_BAND * h
    return x1 <= box[0] + bx or x0 >= box[2] - bx or y1 <= box[1] + by or y0 >= box[3] - by


def _in_edge_band(box: BBox, page_rect: pymupdf.Rect) -> bool:
    """`box` lies within FRAME_EDGE_BAND of one edge of the page, all of it."""
    w, h = page_rect.width, page_rect.height
    band_x, band_y = FRAME_EDGE_BAND * w, FRAME_EDGE_BAND * h
    return box[3] <= band_y or box[1] >= h - band_y or box[2] <= band_x or box[0] >= w - band_x


def _round_page(box: BBox, page_rect: pymupdf.Rect) -> bool:
    """Each side of `box` lies within FRAME_EDGE_BAND of the same side of the page: a
    box round the page, with a margin of up to that share."""
    w, h = page_rect.width, page_rect.height
    edge = FRAME_EDGE_BAND
    return (
        box[0] <= edge * w
        and box[2] >= (1 - edge) * w
        and box[1] <= edge * h
        and box[3] >= (1 - edge) * h
    )


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
        if len(members) == 1:
            merged.append(members[0])
            continue
        box = _union(f.bbox for f in members)
        images, paths = sum(f.images for f in members), sum(f.paths for f in members)
        merged.append(_Figure(box, images=images, paths=paths))
    return merged


def _absorb_labels(
    figure: _Figure, rows: list[_Row], ctx: _Context, doc: pymupdf.Document | None = None
) -> bool:
    """Give a figure its labels; False when the "figure" is a plain background.

    Short text inside a figure, and short labels just outside it, belong to it. Running
    text never does: lines of a paragraph inside a figure's box (text set on a backdrop,
    in a panel, over a watermark logo) stay in the reading flow, and so does a title-sized
    line. A picture with such text on it (a slide's photo under its title, a caption box
    over a photo) stays a figure: it was dropped as a background, with nothing to say so
    (the silent-loss sweep, 2026-10-12). Only a plain one (`_plain_image`: a coloured
    panel or a wash drawn as a picture) is a background: it is dropped, nothing absorbed,
    and its text keeps its place in the outline. A picture covering the page is the
    page's own (`_raster_figures`)."""
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
    if running and figure.images == 1 and not figure.paths and _plain_image(doc, figure.xref):
        return False
    if (
        inside
        and not near
        and not figure.images
        and all(r.code and id(r) in running for r in inside)
    ):
        return False  # a box drawn round program code is its frame, not a figure
    for row in [*inside, *near]:
        if id(row) not in running:
            row.absorbed = True
            figure.labels.append(row)
    if figure.labels:
        figure.bbox = _union([figure.bbox, *(r.bbox for r in figure.labels)])
    return True


def _running_text(rows: list[_Row], ctx: _Context) -> set[int]:
    """Ids of rows that are not labels: lines of a paragraph (a prose line that the next
    or previous prose line continues) and title-sized lines of several words: at least
    HEADING_SIZE_RATIO x body size, or TITLE_MIN_PT whatever the body (a slide whose only
    text is its title sets the body size itself: its title was a figure's label). The
    short last line of such a paragraph ("the three.") is one of its lines too."""
    found: set[int] = set()
    prose = []
    texts = []
    for row in rows:
        if row.kind != "text" or row.vertical:
            continue
        texts.append(row)
        if row.code and len(row.ink) >= CODE_MIN_GLYPHS:
            found.add(id(row))  # a line of code in a box is code, not the box's label
            continue
        words = _prose_words(row, _CharInfo.of(row))
        if words >= 2 and (row.size >= HEADING_SIZE_RATIO * ctx.body or row.size >= TITLE_MIN_PT):
            found.add(id(row))
        if words >= 3:
            prose.append(row)
    prose.sort(key=lambda r: r.baseline)
    lines: set[int] = set()  # lines of a paragraph
    for i, above in enumerate(prose):
        for below in prose[i + 1 :]:
            if below.baseline - above.baseline > 2.0 * max(above.size, below.size):
                break
            if below.column == above.column and _continues(
                above, below, _Run([above], "text", True), ctx
            ):
                lines.update((id(above), id(below)))
    texts.sort(key=lambda r: r.baseline)
    for i, row in enumerate(texts):
        if id(row) in lines:
            continue
        for above in reversed(texts[:i]):
            if row.baseline - above.baseline > 2.0 * max(above.size, row.size):
                break
            if (
                id(above) in lines
                and above.column == row.column
                and _continues(above, row, _Run([above], "text", True), ctx)
            ):
                lines.add(id(row))
                break
    return found | lines


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
    code: bool = False  # rows of program code, grouped whatever their indentation
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
    code: bool = False  # paragraphs: program code, lines and indentation kept (`_code_text`)
    term_pieces: int | None = None  # glossary entries: how many of `pieces` are the term


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
    """Two lines of one block: both heading-sized or neither; for heading-sized lines the
    same size (to half a point); the same weight; and for bold lines, which are headings
    or run-in labels, the same size and typeface too ("Practice Makes Perfect" in a bold
    sans over the bold serif of the section it introduces is two headings; T2-8)."""
    if _heading_sized(prev, ctx) != _heading_sized(row, ctx):
        return False
    if _heading_sized(row, ctx) and abs(prev.size - row.size) > 0.5:
        return False
    if prev.bold != row.bold:
        return False
    return not (row.bold and (abs(prev.size - row.size) > 0.5 or prev.family != row.family))


def _code_groups(rows: Sequence[_Row]) -> dict[int, int]:
    """Rows of program code, by `id(row)`, with the number of the code block they are in.

    A code block is consecutive rows (in reading order) of upright text that is
    monospaced throughout (`_Row.code`), in one column, one line pitch apart (a blank line
    in the code is a gap of up to two) and of one size, with at least `CODE_MIN_GLYPHS`
    glyphs, or `CODE_MIN_SHORT` in two lines or more. Whatever their indentation: a
    Python loop's body is a line that starts further right, not a new paragraph. A single
    short monospaced line ("print") is not a block.
    """
    groups: list[list[_Row]] = []
    for row in rows:
        if row.kind != "text" or not row.code:
            groups.append([])  # ends the block
            continue
        last = groups[-1] if groups else []
        if last:
            prev = last[-1]
            pitch = row.baseline - prev.baseline
            if (
                row.column == prev.column
                and abs(row.size - prev.size) <= 0.5
                and 0.5 * prev.size <= pitch <= CODE_MAX_PITCH * prev.size
            ):
                last.append(row)
                continue
        groups.append([row])
    found: dict[int, int] = {}
    for group in groups:
        glyphs = sum(len(r.ink) for r in group)
        if glyphs >= CODE_MIN_GLYPHS or (len(group) >= 2 and glyphs >= CODE_MIN_SHORT):
            for row in group:
                found[id(row)] = id(group[0])
    return found


def _pitch(row: _Row) -> list[float]:
    """Distances between the starts of neighbouring characters of one word of a code line
    (no space between them), the advance of its monospaced font."""
    return [
        b.x0 - a.x0
        for a, b in pairwise(row.chars)
        if not a.blank and not b.blank and a.mono and b.mono and 0 < b.x0 - a.x0 < 2 * row.size
    ]


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _code_text(rows: Sequence[_Row]) -> str:
    """The text of a code block, line by line, with the indentation it was printed with.

    A line's spaces are the text layer's own; what it lacks is the indentation, which is
    the line's distance from the block's left edge over the font's advance (the usual
    distance between the starts of two neighbouring characters; a monospaced font moves
    the same distance for each). A gap between two neighbours with no space between them
    (a PDF that moves the pen instead of printing spaces) counts as spaces by the same
    measure. Blank lines are the gaps between lines that are bigger than the usual one."""
    advance = _median([d for r in rows for d in _pitch(r)])
    left = min(r.x0 for r in rows)
    pitches = [b.baseline - a.baseline for a, b in pairwise(rows) if b.baseline > a.baseline]
    unit = min(min(pitches), CODE_LINE * rows[0].size) if pitches else 0.0
    lines: list[str] = []
    for i, row in enumerate(rows):
        if i and unit > 0:
            lines.extend([""] * max(0, round((row.baseline - rows[i - 1].baseline) / unit) - 1))
        own = _median(_pitch(row)) or advance
        line = " " * (round((row.x0 - left) / advance) if advance > 0 else 0)
        before: _Char | None = None  # the character just before, with nothing between
        for c in row.chars:
            if c.blank:
                if (c.x1 - c.x0) < CODE_SPACE * own:
                    continue  # a space the text layer made up for a small gap, not printed
                if line.strip():
                    line += " "
                before = None
                continue
            if before is not None and own > 0 and (c.x0 - before.x0) >= CODE_GAP * own:
                line += " " * (round((c.x0 - before.x0) / own) - 1)
            line += c.c.translate(_TEXT_MAP)
            before = c
        lines.append(line.rstrip())
    return "\n".join(lines)


def _runs(rows: list[_Row], ctx: _Context) -> list[_Run]:
    runs: list[_Run] = []
    code = _code_groups(rows)
    for row in rows:
        if runs:
            run = runs[-1]
            prev = run.rows[-1]
            if id(row) in code or run.code:
                if id(row) in code and run.code and code[id(row)] == code[id(prev)]:
                    run.add(row)
                    continue
                run.block_end = True
                runs.append(_Run([row], row.kind, block_start=True, code=id(row) in code))
                continue
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
            # Two bold lines that differ in size or typeface are two headings (or labels),
            # not a heading and the paragraph it leads: the second one starts a block.
            restyled = geometric and prev.bold and row.bold
            starts = not geometric or restyled or (ends_sentence and short)
            runs.append(_Run([row], row.kind, block_start=starts))
        else:
            runs.append(_Run([row], row.kind, block_start=True, code=id(row) in code))
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


MATHS_HEADING_SHARE = 0.7  # a heading candidate with this share of maths characters is a formula
_LONGEST_SYMBOL_WORD = 3  # letters: a longer plain word (outside _MATH_WORDS) is prose


def _mostly_maths(rows: Sequence[_Row]) -> bool:
    """Is a heading candidate a formula? ("KE = m v", a large "F = m a" on a slide.)

    It needs a relation sign at full size, no plain word longer than three letters (a
    function name from `_MATH_WORDS` is fine), and at least MATHS_HEADING_SHARE of its
    characters (punctuation apart) are maths: digits, operators, brackets, maths symbols
    and letters that read as variables, or the letters of words up to three letters
    long, which beside a relation sign are products of variables ("mgh"). A heading that
    only contains a number or a sign has no relation sign and stays a heading.
    """
    counted = mathy = 0
    relation = False
    for row in rows:
        info = _CharInfo.of(row)
        chars = row.chars
        i = 0
        while i < len(chars):
            c = chars[i]
            if c.blank or c.c in ",.;:":
                i += 1
                continue
            if info.classes[i] == "L" and not info.script[i] and not info.maths_words[i]:
                j = i
                while j < len(chars) and info.classes[j] == "L" and not info.script[j]:
                    j += 1
                word = "".join(x.c for x in chars[i:j]).lower()
                if j - i > _LONGEST_SYMBOL_WORD and word not in _MATH_WORDS and info.words[i] > 1:
                    return False
                counted += j - i
                mathy += j - i
                i = j
                continue
            counted += 1
            mathy += info.mathy(i)
            relation = relation or (c.c in _ROW_RELATIONS and not row.is_script(c))
            i += 1
    return relation and counted > 0 and mathy / counted >= MATHS_HEADING_SHARE


def _equation_block(
    rows: list[_Row], box: BBox, size: float, ink: list[_Char], *, plain: bool = False
) -> _Block:
    """The equation block of maths rows (their region's equation number kept apart).

    `plain`: the text layer keeps no letter styles. For a line set bold as a whole (a
    heading's weight), where bold says nothing about vectors."""
    text = _clean(" ".join((r.text if plain else r.styled_text) for r in rows))
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


def _classify_run(run: _Run, ctx: _Context) -> _Block:
    rows = run.rows
    box = _union(r.bbox for r in rows)
    size = _dominant_size(rows)
    ink = [c for r in rows for c in r.ink]
    if run.code:
        return _Block(
            Kind.PARAGRAPH, box, rows, text=_code_text(rows), font_size=size, bold=False, code=True
        )
    # A lone ">4" or "+8" (a table cell, a stray sign) is too little to call an equation.
    trivial = len(rows) == 1 and len(ink) <= 2 and not any(c.math for c in ink)
    if run.kind == "math" and not trivial:
        return _equation_block(rows, box, size, ink)
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
    if heading is not None and _mostly_maths(rows):
        # A formula set large or bold on a slide is not a title: it goes to the equation
        # step like any display equation, and the page keeps its real headings.
        block = _equation_block(rows, box, size, ink, plain=bold)
        block.reasons.append("heading candidate that is mostly maths")
        return block
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
    start make one word. "Newton-" + "Raphson" keeps its hyphen but gets no space, and
    so does "well-" + "known": a compound is told from a broken word by the word list
    (`hyphens.keeps_hyphen`: kept when both pieces are words and the joined word is not,
    or when the piece before the hyphen is a single letter, as in "x-" + "ray"). A soft
    hyphen always disappears. Anything else is joined with a space.
    """
    if before.endswith("\u00ad"):
        return before[:-1], ""
    if len(before) > 1 and before[-1] in hyphens.HYPHENS and before[-2].isalpha():
        if after[:1].islower() and not hyphens.keeps_hyphen(before, after):
            return before[:-1], ""
        return before, ""
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
            and not block.code
            and not out[-1].code
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
    if block.code:
        return [block.text or ""]  # kept as printed: lines, indentation, no inline maths
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
    return _strip_teacher_tags(_tidy(pieces))


# OpenStax's teacher-support tags at the start of a paragraph: the level of student a
# prompt is for, "[BL][OL] Describe a scenario ...", "[AL] ...", "[All] ...". They are
# notes to a teacher, not words of the lesson, and a screen reader would read them out
# letter by letter. Only these four, in capitals as OpenStax prints them, and only before
# other text; "[1]", "[Visual]" and a "[BL]" in the middle of a sentence stay.
_TEACHER_TAGS = re.compile(r"^\s*(?:\[(?:BL|OL|AL|All)\]\s*)+")


def _strip_teacher_tags(pieces: list[str | _Inline]) -> list[str | _Inline]:
    """`pieces` without OpenStax level tags at the start (see `_TEACHER_TAGS`); unchanged
    when nothing but the tags would be left."""
    if not pieces or not isinstance(pieces[0], str):
        return pieces
    stripped = _TEACHER_TAGS.sub("", pieces[0], count=1)
    if stripped == pieces[0]:
        return pieces
    if not stripped.strip() and len(pieces) == 1:
        return pieces
    rest = pieces[1:]
    return [stripped, *rest] if stripped else rest


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
    from its last one is marked, and the end of a row the page break split is joined to
    that row (`tables.join_split_row`)."""
    found = tables.find(page, frags[number], paths[number], clean=_clean)
    if not found:
        return found
    lines = set().union(*(t.lines for t in found))
    drawn = set().union(*(t.paths for t in found))
    frags[number] = [f for i, f in enumerate(frags[number]) if i not in lines]
    paths[number] = [p for i, p in enumerate(paths[number]) if i not in drawn]
    if previous and tables.continues(previous[-1], found[0], page.rect.width):
        found[0].reasons.append(tables.REASON_CONTINUED)
        # A row the page break split: its end goes back to the table it belongs to; a
        # table left with only its repeated header rows is no table (its lines stay
        # claimed: they were the header again and the end of that row).
        if tables.join_split_row(previous[-1], found[0]):
            found = found[1:]
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


KEY_TERMS_HEADING = re.compile(r"\bkey\s+terms?\s*$", re.IGNORECASE)
TERM_GAP = 0.7  # x font size: a gap this wide on a line of terms separates two terms
MAX_TERM_WORDS = 6  # a term is a few words; more is a sentence, and stays a paragraph


def _key_term_lines(blocks: list[_Block]) -> list[_Block]:
    """A line of terms under a "Key Terms" heading becomes its terms as list items.

    OpenStax sets the terms of an unruled "Key Terms" box side by side on one line, each
    in a box of its own; the text layer reads "dependent variable independent variable
    tangent". Right under a heading that ends in "Key Terms", a paragraph of plain text
    whose lines break into pieces at gaps of at least TERM_GAP x the font size (a space
    between words is a quarter of that) with at most MAX_TERM_WORDS words in each piece
    is read as a list of terms: one item per piece, in reading order (`_term_items`
    does the same for a ruled box found as a table). A paragraph with no such gap, or
    with a long piece, is left as it is.
    """
    out: list[_Block] = []
    for block in blocks:
        previous = out[-1] if out else None
        if (
            block.kind == Kind.PARAGRAPH
            and previous is not None
            and previous.kind == Kind.HEADING
            and KEY_TERMS_HEADING.search(previous.text or "")
            and all(isinstance(piece, str) for piece in block.pieces)
        ):
            terms = _term_pieces(block.rows)
            if terms:
                out.extend(_Block(Kind.LIST_ITEM, box, text=t, pieces=[t]) for box, t in terms)
                continue
        out.append(block)
    return out


def _term_pieces(rows: Sequence[_Row]) -> list[tuple[BBox, str]]:
    """(box, text) of each piece of the rows' lines, or [] unless they are a line of terms
    (`_key_term_lines`)."""
    found: list[tuple[BBox, str]] = []
    spread = False
    for row in rows:
        if row.vertical:
            return []
        pieces: list[list[_Char]] = []
        previous: _Char | None = None
        for char in row.chars:
            if char.blank:
                continue
            if previous is None or char.x0 - previous.x1 >= TERM_GAP * row.size:
                pieces.append([])
            pieces[-1].append(char)
            previous = char
        spread = spread or len(pieces) > 1
        for chars in pieces:
            text = _clean("".join(c.c for c in row.chars if chars[0].x0 <= c.x0 <= chars[-1].x0))
            if not text or len(text.split()) > MAX_TERM_WORDS:
                return []
            found.append((_union((c.x0, c.y0, c.x1, c.y1) for c in chars), text))
    return found if spread else []


# ============================================================================ pages


@dataclass
class _PageBlocks:
    number: int
    size: tuple[float, float]
    blocks: list[_Block]
    # Drawings that fill the page and were not made a figure because the page's ink was
    # rejected or not analysed (`_vector_figures`): the safety net keeps the page image.
    left_out: list[BBox] = field(default_factory=list)
    # On a page with less than a line of text: something painted lies outside every block
    # (`_painted`), so the safety net keeps the page image.
    unread: bool = False


def _prepare_rows(
    page: pymupdf.Page, frags: list[_Row], margins: _MarginFilter
) -> tuple[list[_Row], tuple[float, float] | None, list[_Row]]:
    """The page's rows without its running headers, footers, page numbers and watermarks,
    its column gutter, and the rows so dropped (a figure may claim some back as labels:
    `_figure_labels`)."""
    height = page.rect.height
    kept: list[_Row] = []
    dropped: list[_Row] = []
    for frag in frags:
        (dropped if margins.drops(frag, height) else kept).append(frag)
    # Margin lines (an unrepeated header, a centred footer) say nothing about columns.
    body = [f.bbox for f in kept if not f.vertical and _margin_zone(f, height) is None]
    gutter = _find_gutter(body)
    return _merge_baselines(kept, gutter), gutter, _merge_baselines(dropped, gutter)


def _page_blocks(
    page: pymupdf.Page,
    number: int,
    rows: list[_Row],
    gutter: tuple[float, float] | None,
    ctx: _Context,
    paths: list[_Path],
    margins: _MarginFilter,
    page_tables: Sequence[tables.PageTable] = (),
    ink: Sequence[BBox] = (),
    *,
    page_sized: bool = True,
    little_text: bool = False,
    strokes: Sequence[BBox] = (),
    dropped: Sequence[_Row] = (),
    ink_sources: Collection[int] = frozenset(),
    mixed_ink: bool = False,
    label_keys: Collection[tuple] = frozenset(),
    covers: Sequence[tuple[float, float]] = (),
) -> _PageBlocks:
    page_rect = page.rect
    rows = _split_exercise_numbers(_split_labels(rows))
    for row in rows:
        _classify_row(row)
    rules = [p for p in paths if p.kind in ("hrule", "radical")]
    used_rules = _resolve_fragments(rows, rules)
    used = {id(rule) for rule in used_rules}
    loose = [p for p in paths if id(p) not in used]
    left_out: list[BBox] = []
    small: list[tuple[BBox, list[_Path]]] = []
    vector = _vector_figures(
        loose,
        rows,
        page_rect,
        ctx,
        page_sized=page_sized,
        left_out=left_out,
        small_out=small,
        ink=ink_sources,
        hand=ink,
    )
    figures = _merge_figures(_raster_figures(page, page_rect) + vector)
    # A logo repeated in the header or footer band is decoration, like a running header.
    figures = [f for f in figures if not margins.drops_figure(f.bbox, page_rect.height)]
    # A chart's tick labels in the footer band, or its axis title repeated on every slide,
    # are its labels, not running text.
    restored = _figure_labels(dropped, figures, rows, page_rect.height, label_keys, margins)
    for row in restored:
        _classify_row(row)
    rows = [*rows, *restored]
    figures = [f for f in figures if _absorb_labels(f, rows, ctx, page.parent)]
    _take_labels(restored, figures, rows)
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
    # A typed page's drawings too small to be figures that no element holds (an icon, a
    # benzene ring, maths drawn as outlines, a tick): a figure of their own, or
    # handwriting when they are pen marks. A page with less than a line of text keeps
    # its image instead (`_painted`, below).
    marks: list[BBox] = []
    leftover = False
    if not little_text:
        index = _RowIndex([r for r in rows if not r.vertical])
        frames = [q.bbox for q in paths if q.frame and _page_furniture(q, page_rect)]
        found, marks = _leftover_drawings(
            small, blocks, index, page_rect, margins, ink_sources, frames, covers
        )
        if page_sized:
            blocks.extend(found)
        elif mixed_ink:  # a page with handwriting: what is left over is cut out with it
            marks += [b.bbox for b in found]
        else:  # a page with ink the checks did not confirm: its image, not a figure
            leftover, marks = bool(found or marks), []
    # Handwriting too light for a mixed_ink page (a short note, a signature, a circle and
    # a tick) that no figure holds: cut out like the rest of handwriting on a typed page,
    # not lost. A mark over typed text (a word circled, a line underlined by hand) is not
    # part of the text, nor is a note written over an equation or in a table's cells (a
    # transcription or a rebuilt table would leave it out); a note in the top or bottom
    # margin counts too.
    held = [b.bbox for b in blocks if b.kind == Kind.FIGURE]
    loose = _loose_strokes(strokes, held, (page_rect.width, page_rect.height), margins=True)
    if len(loose) >= LOOSE_INK_STROKES or marks:
        ink = [*ink, *page_type.stroke_regions([*loose, *marks])]
    blocks.extend(_ink_blocks(ink, page_rect))
    order = _reading_order([b.bbox for b in blocks], gutter, ctx.body)
    final = _merge_column_continuations([blocks[i] for i in order])
    for block in final:
        if block.kind in _TEXT_BLOCKS:
            block.pieces = _paragraph_pieces(block)
    final = [b for b in final if b.kind not in _TEXT_BLOCKS or _has_content(b.pieces)]
    final = _key_term_lines(_term_items(final))
    origins = _column_origins(text_rows)
    for block in final:
        if block.marker is not None:
            block.origin = origins.get(block.rows[0].column, 0.0)
    size = (round(page_rect.width, 2), round(page_rect.height, 2))
    # A page with less than a line of text (a title, a few labels) is little but its
    # drawings: anything painted that went into no block (pen strokes too few to be called
    # handwriting under a title, a drawing too small to be a figure) is lost unless the
    # page image is kept. A drawn list bullet belongs to its item.
    unread = leftover or (
        little_text
        and _painted(
            page,
            paths,
            [b.bbox for b in final],
            _RowIndex([r for r in rows if not r.vertical]),
            margins,
        )
    )
    return _PageBlocks(number, size, final, left_out, unread)


def _covered_points(
    page: pymupdf.Page, kind: page_type.PageTypeResult
) -> list[tuple[float, float]]:
    """Origins (display space) of the visible characters something was painted over."""
    display = _Display.of(page)
    return [display.point(x / 2, y / 2) for x, y in kind.text.covered]


def _figure_labels(
    dropped: Sequence[_Row],
    figures: Sequence[_Figure],
    rows: Sequence[_Row],
    height: float,
    accepted: Collection[tuple] = frozenset(),
    margins: _MarginFilter | None = None,
) -> list[_Row]:
    """Rows the header and footer rules dropped (`_prepare_rows`) that label a figure: a
    chart's tick labels in the footer band (bare numbers, read as page numbers), its axis
    title repeated on every slide of a deck (a running footer), a rotated axis title
    repeated at the same place (a watermark). A dropped row is a label when it is short
    (LABEL_MAX_CHARS), not diagonal, and within LABEL_REACH x its size of a figure or of
    the short rows just beside it (its tick labels), level with it for a rotated row and
    under or over it otherwise (`_beside_figure`). Bare numbers count only two or more on
    one baseline, or level with a figure when the document's page numbers are not there
    (`margins.numbering`: an axis title "v" at the top of a full-page plot's y-axis, read
    as a page number in roman numerals): a page number alone under a figure, or beside
    one in the band where the other pages have theirs, is still a page number. Other text
    counts only when its key is `accepted` (`_label_keys`: beside a figure wherever the
    document repeats it), so a running footer that also runs under pages of text stays
    dropped."""
    if not dropped or not figures:
        return []
    extents = _figure_extents([f.bbox for f in figures], rows)
    found = []
    for row in _label_candidates(dropped, numbers=True):
        if not _beside_figure(row, extents):
            continue
        if _is_number_row(row) or _label_key(row, height) in accepted:
            found.append(row)
    numbers = [r for r in found if _is_number_row(r)]
    lone = {
        id(r)
        for r in numbers
        if not any(o is not r and abs(o.baseline - r.baseline) <= 0.3 * r.size for o in numbers)
        and not (
            any(f.bbox[1] <= (r.y0 + r.y1) / 2 <= f.bbox[3] for f in figures)
            and not (margins is not None and margins.numbering(r, height))
        )
    }
    return [r for r in found if id(r) not in lone]


def _gap(box: BBox, other: BBox) -> float:
    """How far apart two boxes are (0 when they touch or overlap), sideways plus up or down."""
    return max(0.0, other[0] - box[2], box[0] - other[2]) + max(
        0.0, other[1] - box[3], box[1] - other[3]
    )


def _take_labels(
    restored: Sequence[_Row], figures: Sequence[_Figure], rows: Sequence[_Row]
) -> None:
    """The rows `_figure_labels` took back from the header and footer bands are labels of
    the figure they are beside, not text of their own, even beyond the reach of
    `_absorb_labels` (a full-page plot's tick labels 6 pt under its x-axis in the footer
    band were read as five paragraphs of one digit each)."""
    for row in restored:
        if row.absorbed:
            continue
        beside = [
            (_gap(row.bbox, f.bbox), i)
            for i, f in enumerate(figures)
            if _beside_figure(row, _figure_extents([f.bbox], rows))
        ]
        if not beside:
            continue
        figure = figures[min(beside)[1]]
        row.absorbed = True
        figure.labels.append(row)
        figure.bbox = _union([figure.bbox, row.bbox])


def _is_number_row(row: _Row) -> bool:
    return not row.vertical and _is_page_number(row.clean)


def _label_candidates(dropped: Sequence[_Row], *, numbers: bool) -> list[_Row]:
    """Dropped rows that could be a figure's labels: short, not diagonal; bare numbers
    only with `numbers`."""
    return [
        r
        for r in dropped
        if not r.diagonal and len(r.clean) <= LABEL_MAX_CHARS and (numbers or not _is_number_row(r))
    ]


def _label_key(row: _Row, height: float) -> tuple:
    """What the header and footer rules recognise a dropped row by (its band and text, or
    its rotated text and place)."""
    if row.vertical:
        return ("rotated", *_rotated_key(row))
    return ("band", _margin_zone(row, height), _norm_margin(row.clean))


def _figure_extents(boxes: Sequence[BBox], rows: Sequence[_Row]) -> list[BBox]:
    """Each figure box grown by the short rows just beside it (its tick labels)."""
    extents = []
    for box in boxes:
        near = [
            r.bbox
            for r in rows
            if not r.vertical and len(r.clean) <= 25 and _within(r.bbox, box, 1.5 * r.size)
        ]
        extents.append(_union([box, *near]))
    return extents


def _beside_figure(row: _Row, extents: Sequence[BBox]) -> bool:
    reach = LABEL_REACH * row.size
    cx, cy = (row.x0 + row.x1) / 2, (row.y0 + row.y1) / 2
    for x0, y0, x1, y1 in extents:
        if row.vertical:
            if y0 <= cy <= y1 and max(x0 - row.x1, row.x0 - x1) <= reach:
                return True
        elif x0 - reach <= cx <= x1 + reach and max(y0 - row.y1, row.y0 - y1) <= reach:
            return True
    return False


def _label_keys(
    doc: pymupdf.Document,
    numbers: Sequence[int],
    prepared: dict[int, tuple[list[_Row], tuple[float, float] | None, list[_Row]]],
    paths: dict[int, list[_Path]],
    ctx: _Context,
    margins: _MarginFilter,
) -> set[tuple]:
    """Keys (`_label_key`) of the dropped repeated text that is beside a figure on every
    analysed page it was dropped from: an axis title repeated under each chart of a deck,
    not a running footer that also runs under pages of text. Only pages with such a
    candidate pay for a first look at their figures (`_figure_boxes`)."""
    near: set[tuple] = set()
    far: set[tuple] = set()
    for n in numbers:
        page = doc[n]
        candidates = [
            r
            for r in _label_candidates(prepared[n][2], numbers=False)
            if _label_key(r, page.rect.height) not in far
        ]
        if not candidates:
            continue  # nothing, or only what a page of text already ruled out
        rows = prepared[n][0]
        extents = _figure_extents(_figure_boxes(page, rows, paths[n], ctx, margins), rows)
        for row in candidates:
            key = _label_key(row, page.rect.height)
            (near if _beside_figure(row, extents) else far).add(key)
    return near - far


def _figure_boxes(
    page: pymupdf.Page,
    rows: list[_Row],
    paths: list[_Path],
    ctx: _Context,
    margins: _MarginFilter,
) -> list[BBox]:
    """The page's figure boxes as `_page_blocks` finds them, roughly (equation rules are
    not taken out first): for `_label_keys` only."""
    rect = page.rect
    figures = _merge_figures(_raster_figures(page, rect) + _vector_figures(paths, rows, rect, ctx))
    return [f.bbox for f in figures if not margins.drops_figure(f.bbox, rect.height)]


def _leftover_drawings(
    clusters: Sequence[tuple[BBox, list[_Path]]],
    blocks: Sequence[_Block],
    index: _RowIndex,
    page_rect: pymupdf.Rect,
    margins: _MarginFilter,
    ink_sources: Collection[int],
    frames: Sequence[BBox] = (),
    covers: Sequence[tuple[float, float]] = (),
) -> tuple[list[_Block], list[BBox]]:
    """A typed page's drawings too small to be figures (`_vector_figures`' `small_out`)
    that no block holds: before, dropped with nothing to say so (the silent-loss sweep,
    2026-10-12: an icon, a benzene ring, a number line, maths drawn as glyph outlines,
    ticks). Not counted: drawn list bullets, a repeated mark in the header or footer band,
    horizontal and vertical lines and boxes alone (an empty answer box, a black box over a
    line) unless they are a ruler (a timeline: `_mark_axes`), anything whose middle lies
    in a block (an underline, a box round a word), and
    the ornaments of a page frame: marks at the edges of a frame round the page
    (`frames`, corner curls), or a group that runs round the page itself (a frame of
    separate dots), dots alone (DOT_PT), and a shape painted over hidden text (`covers`,
    the covered characters' origins: a box or a black highlight hiding a line). The rest
    is grouped (LEFTOVER_REACH) and each group kept when at least LEFTOVER_MIN_PT on its
    longer side or of page_type.VECTOR_MIN_PATHS marks in different places: pen marks
    (most of their paths are the page type's pen strokes, or every path is an open
    stroked line or curve: a tick, a cross, a signature) are returned as handwriting
    boxes (never sent to the figure model); any other drawing becomes a FIGURE block for
    the model. Since 2026-10-13 (the drawings sweep) some straight drawings count too:
    bonds between atom groups (`_bond_ends`: a structural formula's h/v bonds, which the
    row of its letters holds), boxes joined by lines across their sides (`_connected`: a
    one-line circuit of resistor boxes and wires), and dots round a letter that reach out
    of its line (`_dots_out_of_line`: a Lewis structure's electrons); the drawing's box
    takes in the atom groups its bonds join."""
    pad = UNREAD_PAD
    grown = [(b.bbox[0] - pad, b.bbox[1] - pad, b.bbox[2] + pad, b.bbox[3] + pad) for b in blocks]
    keep: list[tuple[BBox, list[_Path]]] = []
    hand: set[int] = set()  # hand marks over typed text (`_pen_mark_over_text`)
    for _, cluster in clusters:
        members = [p for p in cluster if not _bullet_mark(p, index)]
        if not members:
            continue
        box = _clip_box(_union(p.bbox for p in members), page_rect)
        if margins.drops_mark(box, page_rect.height) or all(
            margins.drops_mark(p.bbox, page_rect.height) for p in members
        ):
            continue
        holders = [
            b
            for b, g in zip(blocks, grown, strict=True)
            if _centre_inside(box, g) and not _glyph_in(b, box)
        ]
        if holders and _pen_mark_over_text(members, box, holders):
            hand.update(id(p) for p in members)
        elif (
            holders
            and not _dots(members)  # decided once grouped (`_dots_out_of_line`)
            and not _bonds(members, index)
        ):
            continue  # an underline, a box round a word, maths drawn in a line
        if any(_at_edge(box, frame, FIGURE_PART_REACH) for frame in frames):
            continue
        keep.append((box, members))
    figures: list[_Block] = []
    marks: list[BBox] = []
    for group in _cluster([b for b, _ in keep], LEFTOVER_REACH):
        members = [p for i in group for p in keep[i][1]]
        box = _union(keep[i][0] for i in group)
        places = len({_box_key(p.bbox) for p in members})  # a path drawn again is one mark
        if (
            max(box[2] - box[0], box[3] - box[1]) < LEFTOVER_MIN_PT
            and places < page_type.VECTOR_MIN_PATHS
        ) or _round_page(box, page_rect):
            continue
        curved = any(p.curved for p in members)
        atoms = [end for p in members for end in _bond_ends(p, index)]
        if (
            not curved
            and not atoms
            and not any(p.ruler for p in members)
            and not _connected(members)
        ):
            continue  # rules, boxes and bars alone (an answer box, a box over a line)
        if _dots(members) and not _dots_out_of_line(members, box, index):
            continue  # dots alone: leaders, bullets the bullet test missed
        box = _union([box, *atoms])  # a formula's bonds and the atom groups they join
        if any(_centre_inside((x, y, x, y), box) for x, y in covers):
            continue  # a shape painted over hidden text: its cover, not a drawing
        pen = sum(p.source in ink_sources for p in members)
        if curved and (
            2 * pen >= len(members) or all(p.open_stroke or id(p) in hand for p in members)
        ):
            marks.append(box)  # a ruler's straight lines are machine-drawn: a figure
        else:
            reasons = [f"vector drawing ({len(members)} paths)"]
            figures.append(_Block(Kind.FIGURE, box, reasons=reasons))
    return figures, marks


def _glyph_in(block: _Block, box: BBox) -> bool:
    """`block` is one or two glyphs smaller than the drawing round them (`box`): the "!"
    of a warning triangle, the "i" of an information circle. It does not hold the
    drawing (2026-10-13: the icons were dropped as held by their glyph)."""
    glyphs = sum(1 for r in block.rows for c in r.chars if not c.blank)
    x0, y0, x1, y1 = block.bbox
    return 0 < glyphs <= 2 and x1 - x0 < box[2] - box[0] and y1 - y0 < box[3] - box[1]


def _dots(members: Sequence[_Path]) -> bool:
    """Every path is a filled mark no bigger than DOT_PT."""
    return all(
        p.filled and max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) <= DOT_PT for p in members
    )


def _dots_out_of_line(members: Sequence[_Path], box: BBox, index: _RowIndex) -> bool:
    """Dots in page_type.VECTOR_MIN_PATHS places or more over a line of text that reach
    out of it, above or below, by PEN_MARK_OUT of their height: the electrons of a Lewis
    structure round its letters (2026-10-13: dropped as dots alone, and held by the row of
    letters). Dot leaders and a missed bullet lie in their line."""
    if len({_box_key(p.bbox) for p in members}) < page_type.VECTOR_MIN_PATHS:
        return False
    height = box[3] - box[1]
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rows = [r for r in index.near(box[1], box[3]) if r.x0 <= cx <= r.x1]
    if height <= 0 or not rows:
        return False
    row = min(rows, key=lambda r: abs((r.y0 + r.y1) / 2 - cy))
    out = max(0.0, row.y0 - box[1]) + max(0.0, box[3] - row.y1)
    return out >= PEN_MARK_OUT * height


def _pen_mark_over_text(members: Sequence[_Path], box: BBox, holders: Sequence[_Block]) -> bool:
    """A hand mark drawn over typed text (`holders`: the blocks whose box holds the middle
    of `box`), not a mark set with the text: one or two open strokes that are diagonal
    lines or curves, or one curved outline, closed or filled (an answer circled, a tick
    drawn as a note app's filled ribbon) in another colour than the text under it
    (PEN_MARK_COLOUR), no radical sign; over a paragraph, list item, heading or display
    equation, reaching out of the line under its middle (the nearest one, or the nearest
    line it is beside) by PEN_MARK_OUT of its height or more, and over an equation at least
    as tall as its text. A hand tick, a cross or a slash over a line was taken as part of
    the line, like an underline, and lost (the second final verify, 2026-10-12); so were a
    red ellipse round an answer, a filled tick, and a tick over an equation, whose
    transcription leaves it out (the drawings sweep, 2026-10-13). A box round a word and
    an underline are closed or straight, a circle round a step number is in the text's
    colour, and a cancel stroke across a term stays within its line. On the research
    corpus's born-digital pages (586 real ones, the OpenStax renders, the fixtures) no
    drawing in a text element is an open diagonal or curved stroke at all."""
    if len(members) > PEN_MARK_STROKES or any(p.kind == "radical" or not p.curved for p in members):
        return False
    strokes = all(p.open_stroke and not p.filled for p in members)
    outline = len(members) == 1 and members[0].kind == "shape" and not members[0].open_stroke
    if not (strokes or outline):
        return False
    if any(b.kind not in (*_TEXT_BLOCKS, Kind.HEADING, Kind.EQUATION) for b in holders):
        return False
    height = box[3] - box[1]
    if height <= 0:
        return False
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rows = [r for b in holders for r in b.rows if not r.vertical]
    under = [r for r in rows if r.x0 <= cx <= r.x1] or rows  # or beside a line's end
    if not under:
        return False
    row = min(under, key=lambda r: abs((r.y0 + r.y1) / 2 - cy))
    out = max(0.0, row.y0 - box[1]) + max(0.0, box[3] - row.y1)
    if out < PEN_MARK_OUT * height:
        return False
    if any(b.kind == Kind.EQUATION for b in holders) and height < row.size:
        return False  # a mark in the maths (an arrow over a letter), not over it
    if strokes:
        return True
    if members[0].filled and members[0].fill_share > PEN_FILL_SHARE:
        return False  # a highlight behind the line, a filled icon: not a stroke's ribbon
    chars = [c for c in row.chars if not c.blank and box[0] <= (c.x0 + c.x1) / 2 <= box[2]]
    if not chars:
        return False
    text = Counter(c.colour for c in chars).most_common(1)[0][0]
    rgb = ((text >> 16) & 255) / 255, ((text >> 8) & 255) / 255, (text & 255) / 255
    return max(abs(a - b) for a, b in zip(rgb, members[0].colour, strict=True)) >= PEN_MARK_COLOUR


def _at_edge(box: BBox, frame: BBox, reach: float) -> bool:
    """`box` lies within `reach` of an edge of `frame` (outside it or inside it)."""
    if not _within(box, frame, reach):
        return False
    x0, y0, x1, y1 = frame
    return (
        box[0] <= x0 + reach or box[2] >= x1 - reach or box[1] <= y0 + reach or box[3] >= y1 - reach
    )


_TEXT_BLOCKS = (Kind.PARAGRAPH, Kind.LIST_ITEM)  # blocks whose text may hold inline maths


def _ink_blocks(regions: Sequence[BBox], page_rect: pymupdf.Rect) -> list[_Block]:
    """Handwriting on a typed page (`page_type.ink_regions`): one FIGURE per region,
    with no labels (the typed text under the ink stays in the text). The pipeline keeps
    each as an image of the region, marked "Not converted", with a decision; it is never
    sent to the figure model."""
    blocks = []
    for x0, y0, x1, y1 in regions:
        box = (max(x0, 0.0), max(y0, 0.0), min(x1, page_rect.width), min(y1, page_rect.height))
        if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
            blocks.append(_Block(Kind.FIGURE, box, reasons=[page_type.HANDWRITING_REGION]))
    return blocks


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
            and not prev.code
            and not block.code
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


def _read_page(
    page: pymupdf.Page, budget: page_type.InkBudget, producer: str
) -> tuple[list[_Row], list[_Path], page_type.PageTypeResult]:
    """The page's rows and paths, and its page type, from one text extraction and one
    drawings list (`pipeline.page_type` reads the same two)."""
    raw = _raw_blocks(page)
    drawings = page.get_cdrawings()
    kind = page_type.classify(page, raw, drawings, budget=budget, producer=producer)
    rows = _page_rows(page, raw, kind.text.covered)
    return rows, _page_paths(page, drawings), kind


def _analyze_document(doc: pymupdf.Document, selected: list[int]) -> Layout:
    frags: dict[int, list[_Row]] = {}
    paths: dict[int, list[_Path]] = {}
    types: dict[int, page_type.PageTypeResult] = {}
    budget = page_type.InkBudget()
    producer = page_type.producer_hint(doc)
    for number in selected:
        frags[number], paths[number], types[number] = _read_page(doc[number], budget, producer)
    margins = _margin_filter(doc, selected, frags, paths)
    # Handwritten and scanned pages are kept as their page image: nothing on them is read
    # (there is no handwriting or scan conversion). On a typed page with handwriting on
    # it, the ink stays out of the figures; each region of it is cut out instead.
    kept = {n: reason for n in selected if (reason := page_type.kept_reason(types[n]))}
    # A page kept as an image that has some visible typed text and nothing hidden (a title
    # slide on a photo, a page of notes not analysed in time under its typed title) keeps
    # that text; nothing drawn on it goes to the figure model.
    text_kept = {
        n
        for n, reason in kept.items()
        if reason == page_type.UNREAD_PAGE and types[n].text.visible_chars
    }
    prepared = {}
    found: dict[int, list[tables.PageTable]] = {}
    for number in selected:
        if number in kept and number not in text_kept:
            frags[number], paths[number], found[number] = [], [], []
            continue
        if number in text_kept:
            paths[number] = []
        if types[number].kind == "mixed_ink":
            ink = types[number].ink.ink_drawings
            paths[number] = [p for p in paths[number] if p.source not in ink]
        found[number] = _claim_tables(doc[number], number, frags, paths, found.get(number - 1))
        prepared[number] = _prepare_rows(doc[number], frags[number], margins)
        # Axes with tick labels, an axis title or a curve from their corner are a plot's:
        # the rows the header and footer rules dropped count (tick labels in the bands).
        # Rulers with no labels whose ticks are an answer field's cells are not rulers.
        page_rows = [*prepared[number][0], *prepared[number][2]]
        _answer_fields(paths[number], page_rows)
        _mark_plots(paths[number], page_rows)
    ctx = _body_and_pitch({n: rows for n, (rows, _, _) in prepared.items()})
    _list_markers({n: rows for n, (rows, _, _) in prepared.items()}, paths, ctx)
    label_keys = _label_keys(doc, list(prepared), prepared, paths, ctx, margins)
    pages = [
        _kept_page(doc[n], n)
        if n in kept and n not in text_kept
        else _page_blocks(
            doc[n],
            n,
            prepared[n][0],
            prepared[n][1],
            ctx,
            paths[n],
            margins,
            found[n],
            page_type.ink_regions(types[n]),
            page_sized=not INK_REASONS & set(types[n].reasons),
            little_text=types[n].text.visible_chars < page_type.TEXT_MIN,
            strokes=_light_ink(types[n]),
            dropped=prepared[n][2],
            ink_sources=types[n].ink.ink_drawings,
            mixed_ink=types[n].kind == "mixed_ink",
            label_keys=label_keys,
            covers=_covered_points(doc[n], types[n]),
        )
        for n in selected
    ]
    _glossary_entries(pages)
    _settle_bold_letters(pages)
    block_ids: dict[int, str] = {}
    elements = _to_elements(pages, ctx, block_ids)
    footnote_marks: dict[str, _FootnoteMark] = {}
    notices = _layout_notices(pages, ctx, block_ids, footnote_marks)
    elements = _footnote_links(elements, footnote_marks)
    # The safety net (independent of the page types; docs/layout.md "The safety net"): a
    # page keeps its page image, with a decision, when something painted on it was read
    # into nothing. The elements read from it stay.
    boxes: dict[int, list[BBox]] = {}
    for e in elements:
        boxes.setdefault(e.page, []).append(e.bbox)
    for page in pages:
        n = page.number
        if n in kept:
            continue
        if n not in boxes:
            unread = _painted(doc[n], paths[n], margins=margins)
        else:
            left_out = bool(page.left_out) and types[n].kind != "mixed_ink"
            unread = left_out or page.unread or _unread_ink(types[n], boxes[n], page.size)
        # A picture covering the page under its typed text (a scan with a stamp, a slide
        # on a full-bleed photo, ink drawn on a transparent overlay): never a figure, and
        # not to be dropped as a background unless it is one.
        if unread or _page_picture(doc[n], margins):
            kept[n] = page_type.UNREAD_PAGE
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
        notices=notices + _hidden_text_notices(types, kept),
        page_types=types,
        kept_pages=dict(sorted(kept.items())),
    )


def _kept_page(page: pymupdf.Page, number: int) -> _PageBlocks:
    """A page kept as its page image: no blocks."""
    return _PageBlocks(number, (round(page.rect.width, 2), round(page.rect.height, 2)), [])


def _unread_ink(
    kind: page_type.PageTypeResult, boxes: list[BBox], size: tuple[float, float]
) -> bool:
    """INK_MIXED_STROKES or more of the page's handwriting-like strokes (the page type's
    ink analysis, clean or rejected) lie outside every element read from the page and
    outside the header and footer bands: writing layout made nothing of (a figure, a
    table and a cut-out handwriting region each hold their own)."""
    if len(kind.ink.stroke_boxes) < page_type.INK_MIXED_STROKES:
        return False
    return len(_loose_strokes(kind.ink.stroke_boxes, boxes, size, margins=False)) >= (
        page_type.INK_MIXED_STROKES
    )


def _loose_strokes(
    strokes: Sequence[BBox], boxes: Sequence[BBox], size: tuple[float, float], *, margins: bool
) -> list[BBox]:
    """The strokes on the page (`size`: a stroke of a drawing that runs off the page is
    not seen) whose centre is in none of `boxes` (each grown by UNREAD_PAD); with
    `margins` False, outside the header and footer bands too."""
    if not strokes:
        return []
    pad = UNREAD_PAD
    width, height = size
    grown = [(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad) for b in boxes]
    return [
        stroke
        for stroke in strokes
        if _centre_inside(stroke, (0.0, 0.0, width, height))
        and (margins or _box_zone(stroke, height) is None)
        and not any(_centre_inside(stroke, box) for box in grown)
    ]


def _light_ink(kind: page_type.PageTypeResult) -> list[BBox]:
    """The pen strokes of a typed page (a line of visible text or more) too light to make
    it mixed_ink, clean or not (two pen widths in one note fail the clean test): the
    strokes `_page_blocks` cuts out when no figure holds them. None for other pages
    (mixed_ink cuts out all its strokes; a page with less text has the safety net,
    `_painted`)."""
    if kind.kind == "mixed_ink" or kind.text.visible_chars < page_type.TEXT_MIN:
        return []
    if INK_REASONS & set(kind.reasons):
        return []  # rejected or not analysed: the safety net's (`_unread_ink`, `left_out`)
    return kind.ink.stroke_boxes


def _page_picture(page: pymupdf.Page, margins: _MarginFilter) -> bool:
    """A picture covers the page (FULL_PAGE_FRACTION of it) and shows something: it is not
    plain (`_plain_image`: one colour, a smooth gradient, nothing seen) and not the
    template's background repeated on the document's pages (`_MarginFilter.backgrounds`).
    Such a picture is never a figure (`_raster_figures`), so the page keeps its image:
    a scan with 40 or more visible characters (a stamp, a header) and no hidden layer, a
    slide on a full-bleed photo, a typed page under a transparent picture of hand marks."""
    for xref in _page_pictures(page):
        if xref and (xref in margins.backgrounds or _plain_image(page.parent, xref)):
            continue
        return True
    return False


def _painted(
    page: pymupdf.Page,
    paths: list[_Path],
    boxes: Sequence[BBox] = (),
    bullets: _RowIndex | None = None,
    margins: _MarginFilter | None = None,
) -> bool:
    """Something is painted outside every block read from the page (`boxes`, grown by
    UNREAD_PAD): a raster image or a drawing at least MIN_IMAGE_PT on a side, or
    VECTOR_MIN_PATHS smaller marks (pen strokes are small). Rules (unless joined into a
    drawing: `_line_chain`), a frame round the page, a background fill and a band of
    colour along an edge (a slide template's) are not content, alone; nor is a drawn list
    bullet beside a row of `bullets`, nor what repeats in the header or footer band (a
    logo: `_MarginFilter.drops_mark`). A note written in the top or bottom margin counts."""
    height = page.rect.height
    pad = UNREAD_PAD
    grown = [(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad) for b in boxes]
    drawn = _drawn_rules(paths)
    marks = 0
    for path in paths:
        if path.kind in ("hrule", "vrule") and id(path) not in drawn:
            continue
        if margins is not None and margins.drops_mark(path.bbox, height):
            continue
        if path.rules or _page_furniture(path, page.rect):
            continue
        if any(_centre_inside(path.bbox, box) for box in grown):
            continue
        if bullets is not None and _bullet_mark(path, bullets):
            continue
        if max(path.bbox[2] - path.bbox[0], path.bbox[3] - path.bbox[1]) >= MIN_IMAGE_PT:
            return True
        marks += 1
        if marks >= page_type.VECTOR_MIN_PATHS:
            return True
    display = _Display.of(page)
    for info in page.get_image_info():
        box = display.box(info["bbox"])
        if min(box[2] - box[0], box[3] - box[1]) < MIN_IMAGE_PT:
            continue
        if margins is not None and margins.drops_mark(box, height):
            continue
        if not any(_centre_inside(box, inside) for inside in grown):
            return True
    return False


def _drawn_rules(paths: Sequence[_Path]) -> set[int]:
    """Ids of the horizontal and vertical rules that are part of a drawing: a cluster of
    rules joined end to end into an open chain (`_line_chain`: a step plot, a staircase),
    or a ruler's line and ticks (`_mark_axes`: a timeline, a number line)."""
    rules = [p for p in paths if p.kind in ("hrule", "vrule")]
    drawn = {id(r) for r in rules if r.ruler}
    if len(rules) < 4:
        return drawn
    for group in _cluster([r.bbox for r in rules], DRAWING_CLUSTER_GAP):
        members = [rules[i] for i in group]
        if _line_chain(members):
            drawn.update(id(r) for r in members)
    return drawn


def _edge_band(path: _Path, page_rect: pymupdf.Rect) -> bool:
    """A filled, straight-edged band along an edge of the page (a slide template's title
    bar or side strip): touching an edge, and at most FRAME_EDGE_BAND of the page deep."""
    if not path.filled or path.curved:
        return False
    x0, y0, x1, y1 = path.bbox
    w, h = page_rect.width, page_rect.height
    touches_x = x0 <= 1.0 or x1 >= w - 1.0
    touches_y = y0 <= 1.0 or y1 >= h - 1.0
    return (touches_y and y1 - y0 <= FRAME_EDGE_BAND * h) or (
        touches_x and x1 - x0 <= FRAME_EDGE_BAND * w
    )


def _hidden_text_notices(
    types: dict[int, page_type.PageTypeResult], kept: dict[int, str]
) -> list[Notice]:
    """Hidden text left out, per page, as counts (docs/layout.md "Hidden text and page
    types"): on a page kept as an image, the tablet's or OCR program's guesses that were
    not used (`ocr_text_layer_ignored`, review "none"); on a typed page, invisible or
    covered words that were left out (`hidden_text_ignored`, "optional": if students need
    them, the instructor adds them)."""
    notices = []
    for number, kind in sorted(types.items()):
        hidden = kind.text.untrusted
        if hidden < page_type.HIDDEN_FEW:
            continue
        if number in kept:
            notices.append(Notice(code="ocr_text_layer_ignored", page=number, count=hidden,
                                  review="none"))  # fmt: skip
        else:
            notices.append(Notice(code="hidden_text_ignored", page=number, count=hidden,
                                  review="optional"))  # fmt: skip
    return notices


# Consecutive lines set in a monospaced font (at least this share of each line's glyphs,
# and this many of them in all) are program code: a block of its own, with its line breaks
# and indentation (which decide a Python loop's body) kept (`_code_groups`).
CODE_MONO_SHARE = 0.8
CODE_MIN_GLYPHS = 12
CODE_MIN_SHORT = 6  # glyphs that are enough for a block of two lines or more
CODE_SPACE = 0.5  # x the advance: a narrower space in the text layer is not a printed one
CODE_LINE = 1.6  # x font size: a line pitch bigger than this has a blank line in it
CODE_GAP = 1.8  # x the advance: a gap this wide between two characters is a missing space
CODE_MAX_PITCH = 3.4  # x font size: line to line in code, one blank line included
# A footnote: a block in the bottom part of the page (below this share of its height), in
# a smaller font than the body, that starts with a mark (a digit, *, a dagger) printed
# raised in the text above it on the same page.
FOOTNOTE_ZONE = 0.6
FOOTNOTE_SIZE = 0.92
FOOTNOTE_CONTEXT = 10  # characters printed before a footnote's mark that find it again
_FOOTNOTE_MARKS = frozenset("*\u2020\u2021\u00a7")


@dataclass(frozen=True)
class _FootnoteMark:
    """A footnote's mark in the text above it: the block (by its element id) and what is
    printed just before the mark, to find it again in the element's text."""

    block_id: str
    before: str
    mark: str


def _layout_notices(
    pages: list[_PageBlocks],
    ctx: _Context,
    block_ids: dict[int, str],
    marks_out: dict[str, _FootnoteMark] | None = None,
) -> list[Notice]:
    """The code blocks and footnotes the job page lists under "Not handled in this file"
    (review "none": no decision; docs/design/content-features.md "Notices"). `marks_out`
    gets each footnote's element id with the place of its mark (`_FootnoteMark`)."""
    notices: list[Notice] = []
    for page in pages:
        raised: dict[str, _FootnoteMark] = {}  # marks printed raised above, on this page
        for block in page.blocks:
            element_id = block_ids.get(id(block))
            if block.kind not in _TEXT_BLOCKS or element_id is None:
                continue
            ink = [c for row in block.rows for c in row.chars if not c.blank]
            if block.code or (
                len(ink) >= CODE_MIN_GLYPHS
                and sum(c.mono for c in ink) >= CODE_MONO_SHARE * len(ink)
            ):
                notices.append(Notice(code="code_block", page=page.number, element_id=element_id,
                                      review="none"))  # fmt: skip
            elif ink and _footnote(block, ink[0].c, page, ctx, raised.keys()):
                notices.append(Notice(code="footnote", page=page.number, element_id=element_id,
                                      review="none"))  # fmt: skip
                if marks_out is not None:
                    marks_out[element_id] = raised[ink[0].c]
            for row in block.rows:
                for i, c in enumerate(row.chars):
                    if row.is_script(c) and c.c.strip():
                        before = _clean("".join(x.c for x in row.chars[:i]))[-FOOTNOTE_CONTEXT:]
                        raised[c.c] = _FootnoteMark(element_id, before, c.c)
    return notices


def _footnote_links(elements: list[Element], marks: dict[str, _FootnoteMark]) -> list[Element]:
    """The elements with an internal link from each footnote's mark in the text to the
    footnote. The mark is found again in the element's text by what is printed just before
    it; a mark that cannot be found once, exactly, gets no link."""
    by_id = {e.id: e for e in elements}
    added: dict[str, list[Link]] = {}
    for footnote, found in marks.items():
        owner = by_id.get(found.block_id)
        if owner is None or owner.text is None or not found.before.strip():
            continue
        wanted = [found.before + found.mark, found.before + " " + found.mark]
        spots = [
            m.end() - len(found.mark)
            for needle in wanted
            for m in re.finditer(re.escape(needle), owner.text)
        ]
        if len(spots) != 1:
            continue
        start = spots[0]
        added.setdefault(owner.id, []).append(
            Link(start=start, end=start + len(found.mark), kind="internal", target_id=footnote)
        )
    return [
        e.model_copy(update={"links": sorted([*e.links, *added[e.id]], key=lambda k: k.start)})
        if e.id in added
        else e
        for e in elements
    ]


def _footnote(
    block: _Block, first: str, page: _PageBlocks, ctx: _Context, raised: Collection[str]
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
                term_length = 0
                for number, piece in enumerate(block.pieces, start=1):
                    if isinstance(piece, _Inline):
                        child_id = next(ids)
                        children.append((child_id, piece))
                        parts.append(inline_marker(child_id))
                    else:
                        parts.append(piece)
                    if number == block.term_pieces:
                        term_length = len("".join(parts))
                joined = "".join(parts)
                text = joined.strip("\n") if block.code else joined.strip()
                term_end = None
                if block.term_pieces is not None:
                    term_end = max(0, term_length - (len(joined) - len(joined.lstrip())))
                marker = block.marker
                elements.append(
                    Element(
                        id=element_id,
                        kind=Kind(block.kind),
                        page=page.number,
                        bbox=_rounded(block.bbox),
                        order=len(elements),
                        text=text,
                        font_size=block.font_size,
                        bold=block.bold,
                        preformatted=block.code,
                        term_end=term_end,
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
    element text, and text nobody sees (`page_type.span_hidden`: render mode 3, 5 %
    opacity or less) is skipped, as in `_page_rows`. The caller holds `_LOCK`.
    """
    display = _Display.of(page)
    glyphs: list[Glyph] = []
    for block in page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if page_type.span_hidden(span):
                    continue
                colour = int(span.get("color") or 0) & 0xFFFFFF
                rgb = ((colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF)
                for ch in span["chars"]:
                    c = ch["c"].translate(_TEXT_MAP)
                    if c and not c.isspace():
                        glyphs.append(Glyph(c[0], display.box(ch["bbox"]), rgb))
    return glyphs
