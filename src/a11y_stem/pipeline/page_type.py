"""Page types: what is on a page, and can its text layer be trusted? Deterministic.

Why this exists: a PDF can carry text nobody sees. iPad notes apps write an invisible
layer of handwriting-recognition guesses over the ink (fill opacity 0), and OCR programs
write their guesses over a scan in render mode 3 or paint the page image over them. Read
as content, that layer became paragraphs and headings of garbage that looked "finished"
with nothing to decide (docs/layout.md "Hidden text and page types"). This module tells
such pages apart from typed ones, so the pipeline never uses hidden text and keeps
handwritten and scanned pages as page images, marked "Not converted", with a decision.

There is no handwriting or scan conversion (decided 2026-10-09): a page this module calls
handwritten or scanned is only ever kept as its page image.

Ported from the research classifier (detect v4, 996 labelled pages) with the changes the
design review asked for (C1): visible paint with no element is never lost (that rule
lives in `layout`, independent of this module), the hidden-ratio rule, opacity checks
before text is called covered, clip-mode text kept, "scan" only for a picture of a page,
and a time budget for the ink analysis. The accuracy that was measured, and what is not
detected, are in docs/layout.md; read them before trusting a kind.

What it reads (no model call, no second text extraction):

- the raw text dictionary layout already extracts (`get_text("rawdict")`): each span's
  `alpha` (0-255) and `char_flags` (FILLED 16, STROKED 32, CLIPPED 64);
- the drawings layout already extracts (`get_cdrawings()`), for the ink analysis;
- MuPDF's image blocks (`get_text("blocks")` with images; boxes clipped by the clip path
  in force, and no image is decoded), for how much of the page is a picture;
- paint order (`get_bboxlog`, `get_texttrace`), to find visible text painted over by an
  image or a filled shape afterwards. Such text is "covered" only when removing it does
  not change the rendered page there (`_cover_test`, on a one-page copy): that is the
  opacity check, and it covers soft masks, colour-key and stencil masks, constant alpha
  and blend modes alike.

Page kinds (`PageTypeResult.kind`):

  born_digital        visible text, nothing hidden
  mixed_ink           visible text plus handwritten ink
  mixed_hidden_text   visible text plus some invisible or covered words
  scan_no_text        a picture of a page, no usable text
  scan_ocr            a picture of a page with a hidden OCR layer
  handwritten         vector ink, no text layer
  handwritten_ocr     vector ink with a hidden recognition layer
  vector_no_text      drawings, little or no text
  blank               nothing painted

`route` says where the page's content would have to come from (`ROUTE`); this app reads
only text layers, so `kept_reason` says which pages become page images instead.

Content-free: kinds, reason codes and counts, never text (`to_record`).
"""

from __future__ import annotations

import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pymupdf

from a11y_stem.core.types import PageType

# detect v4 plus the design review's C1 changes (v5); v5.1 after fresh set 4: a cover over
# part of a line is looked for, and a page of paper rules alone is blank.
CLASSIFIER_VERSION = "page-type-v5.1"

BBox = tuple[float, float, float, float]

# ---------------------------------------------------------------------------- thresholds

# Text visibility. In rawdict, render mode 3 is a span with neither the filled nor the
# stroked char flag; a fill under an ExtGState alpha of 0 (what iOS writes) is alpha 0.
# Text used as a clip (render mode 7: a gradient- or picture-filled title) also has alpha
# 0, but it carries the CLIPPED flag and is visible, so alpha alone never hides it.
INVISIBLE_OPACITY = 0.05  # alpha at or under this paints nothing a reader can see
INVISIBLE_ALPHA = round(INVISIBLE_OPACITY * 255)  # 13 on rawdict's 0-255 scale
CHAR_FILLED, CHAR_STROKED, CHAR_CLIPPED = 16, 32, 64  # mupdf.FZ_STEXT_FILLED, ...
IMAGE_FLAGS = pymupdf.TEXT_PRESERVE_IMAGES | pymupdf.TEXT_MEDIABOX_CLIP
# The flags of the one text extraction per page (`layout.TEXT_FLAGS` is this): defined
# here so the upload probe can read pages the same way without importing layout.
TEXT_FLAGS = (
    pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP | pymupdf.TEXT_ACCURATE_BBOXES
)

# Covered text: visible text painted, then covered by an image or a filled shape painted
# later (ABBYY-style "text under the page image" OCR, a black box over a line). A cover
# must hold this share of a glyph's box (its middle), so an underline or a strikeout is
# not one. Whether the text still shows (a transparent PNG, a 30 % wash, a multiply
# blend) is decided by rendering: removing the text must change at most this share of
# its pixels by more than COVER_PIXEL_DELTA levels. At most COVER_TEST_SPANS text runs
# are tested per page (the largest); the others follow the majority.
COVER_CORE = 0.6
COVER_ZOOM = 2.0
COVER_PIXEL_DELTA = 24
COVER_CHANGED_SHARE = 0.01
COVER_TEST_SPANS = 24
REDACT_INSET = 0.5  # pt: how far inside a run of glyphs the removal stays
THICK_STROKE = 3.0  # pt: a stroked line this wide can cover the middle of a glyph

# Raster coverage: the union of image boxes on a 64 x 64 grid. A scan is one or more
# images covering most of the page; margin crops, letter-on-A4 and strip tiling stay
# above 0.75 (measured 0.86-1.0). A page is only called a scan when it is a picture of a
# page: its images cover PICTURE_COVERAGE of it (the threshold layout drops a page
# background at), or it has no visible text at all (C1.5: a slide title over a photo is
# a typed page with a figure, not a scan).
SCAN_COVERAGE = 0.5
PICTURE_COVERAGE = 0.85
GRID = 64

# Text amounts, in non-space characters.
TEXT_MIN = 40  # visible chars for a page to "have a text layer" (about one line of type)
OCR_MIN = 20  # untrusted chars that alone (no image, no ink) mean an OCR layer
HIDDEN_FEW = 3  # untrusted chars that make a page "_ocr" or "mixed_hidden_text"

# Ink strokes: a handwriting stroke is a small, curved, wiggly, one-off path.
STROKE_MIN_SIDE = 1.5  # pt; smaller is a dot or noise
STROKE_MAX_SIDE = 90.0  # pt; larger is a plot line, a frame or a long rule
STROKE_MIN_POINTS = 8  # flattened points; circle markers and arrowheads have fewer
FILLED_MIN_POINTS = 12  # a filled pen outline has two sides: lec14's 10th percentile is 28;
#                         quiver arrows and other machine polygons have 4 to 8
OBLIQUE_MIN = 3  # segments off the axes; fewer and no curves means boxes, grids, ticks
CLOSED_TURNING = 2.6 * math.pi  # any convex outline turns exactly 2 pi; pen strokes wiggle
OPEN_TURNING = 1.25 * math.pi  # an open pen stroke bends more than a half circle
RIBBON_MAX_RATIO = 0.3  # filled outline: 2 x area / perimeter (its mean width) at most this
#                         share of its longest side. A pen ribbon is ~0.1; a disc is 0.5.
ZIGZAG_ANGLE = 0.35  # rad (20 degrees): a "sharp" turn, for the zigzag measure
ZIGZAG_MAX = 0.3  # share of turns that are sharp and reverse the last sharp turn. Pen
#                   strokes bend one way at a time (lec14 p90 0.16, synthetic p90 0.17);
#                   TikZ random steps 0.49, random-walk tracks 0.43.
SIG_GRID = 0.2  # pt / pt^2: perimeter and area rounded to this are compared for repeats
SIG_TURN = 0.25  # rad: total turning rounded to this; coarse, as PDF coordinates are
#                  written with few decimals and copies differ by rounding noise

# Pen consistency (filled ribbons only): one pen gives one ribbon width. Share of filled
# strokes whose width is within PEN_BAND x the median of strokes of the same colour.
# lec14: 0.95-0.98; synthetic ribbons 0.97; a filled contour map 0.24; the outlined
# lettering of a seal 0.55; quiver arrows 0.79.
PEN_BAND = (0.75, 1.33)
PEN_SHARE_MIN = 0.8

# Writing rows: strokes grouped into bands along a baseline (level, or tilted up to 15
# degrees). A band of writing is at most this tall and holds at least this many strokes.
# Plots, contour fields and diagrams make one tall band instead.
ROW_MAX_HEIGHT = 60.0
ROW_MIN_STROKES = 3
ROW_STROKE_SHARE = 0.6  # pen strokes among the strokes and machine shapes in the band
ROW_ANGLES = (-15, -10, -5, 0, 5, 10, 15)  # degrees; baselines tried for tilted notes
# Regularity guard: machine layouts repeat box sizes to rounding precision (every panel
# of a small-multiples plot draws its curve to fill the same box); hands never do.
SIZE_TWIN_TOLERANCE = 0.001
SIZE_TWIN_ABS = 0.01  # pt
SIZE_TWIN_MAX = 0.5  # share of strokes with a twin above which the "ink" is a layout

# Page-level ink decisions.
INK_PAGE_STROKES = 40  # strokes for a page of handwriting (lec14's sparsest page has 220)
INK_PAGE_ROWS = 3  # rows of writing for a page of handwriting
INK_MIXED_STROKES = 12  # strokes for "some handwriting" on a typed page
INK_MIXED_ROWS = 1
# With no visible text and no page image, a few clean strokes in one row are already the
# page's content (a short note): nothing else on the page could explain them.
INK_ALONE_STROKES = 5
INK_MAX_REPEATS = 0.35  # typed glyph outlines and plot markers repeat exactly; ink never
# A page with little text is "vector_no_text" only if something is drawn: at least this
# many visible paths, or an image. Otherwise its little text is all there is.
VECTOR_MIN_PATHS = 3
# Paper rules (ruled, squared, margin or Cornell paper) are separate lines at least this
# share of the page across; shorter or joined horizontal and vertical lines are marks.
RULE_MIN_SHARE = 0.5

# Time budget (C1.6). The ink analysis is pure Python and dense pages are slow (up to
# 0.9 s a page measured; 30 dense pages took 34 s on one core). A document gets
# INK_BUDGET_S of it, and a page with more than INK_MAX_ITEMS path items is not analysed
# at all. A page not analysed says so (reason "ink_not_analysed"); with drawings and no
# visible text at all, it is kept as a page image with a decision (`kept_reason`),
# never answered "unreadable".
INK_BUDGET_S = 10.0
INK_MAX_ITEMS = 200_000

# Handwriting regions on a typed page (mixed_ink): strokes closer than this join one
# region; a region is padded by REGION_PAD. More than MAX_REGIONS regions on a page
# become one region round all of them.
REGION_GAP = 24.0
REGION_PAD = 4.0
MAX_REGIONS = 6

OCR_FONT = re.compile(r"GlyphLess|^\.SF|^\.Apple|^\.Helvetica", re.IGNORECASE)
SPACES = frozenset({0x20, 0xA0, 0x09, 0x0A, 0x0D, 0x3000})

ROUTE = {
    "born_digital": "text_layer",
    "mixed_ink": "text_layer_and_ink_regions",
    "mixed_hidden_text": "visible_text_layer",
    "scan_no_text": "vision_page",
    "scan_ocr": "vision_page",
    "handwritten": "vision_page",
    "handwritten_ocr": "vision_page",
    "vector_no_text": "vision_page",
    "blank": "nothing",
}
KINDS = tuple(ROUTE)

# Fix reasons (and notice codes) of the pages and regions kept as images.
SCANNED_PAGE = "scanned_page"
HANDWRITTEN_PAGE = "handwritten_page"
HANDWRITING_ON_PAGE = "handwriting_on_page"
UNREAD_PAGE = "unread_page"  # painted, but nothing could be read from it
# Element reason of a handwriting region layout cuts out of a typed page.
HANDWRITING_REGION = "handwriting"


# ---------------------------------------------------------------------------- visibility


def span_hidden(span: dict[str, Any]) -> bool:
    """A rawdict span nobody sees: render mode 3 (neither filled nor stroked), or an alpha
    of 5 % or less, unless it is text used as a clip (C1.4)."""
    flags = int(span.get("char_flags", CHAR_FILLED))
    if not flags & (CHAR_FILLED | CHAR_STROKED):
        return True
    return int(span.get("alpha", 255)) <= INVISIBLE_ALPHA and not flags & CHAR_CLIPPED


def origin_key(origin: Sequence[float]) -> tuple[int, int]:
    """A character's origin rounded to half a point: how layout's characters are matched
    to the covered ones found in the paint-order trace (both in unrotated page space)."""
    return round(origin[0] * 2), round(origin[1] * 2)


def _ink(ch: str) -> bool:
    return ord(ch[:1] or " ") not in SPACES


# ---------------------------------------------------------------------------- results


@dataclass
class TextStats:
    visible_chars: int = 0
    invisible_chars: int = 0  # render mode 3 or alpha ~0
    covered_chars: int = 0  # painted, then covered by an image or a fill painted later
    ocr_font_chars: int = 0  # GlyphLessFont (Tesseract), Apple ".SF..." (iOS)
    clipped_chars: int = 0  # text used as a clip: kept, recorded only
    # Origins (`origin_key`) of the characters layout must leave out although their span
    # looks visible: covered ones, and clip text nothing was painted through (counted as
    # invisible).
    covered: set[tuple[int, int]] = field(default_factory=set)

    @property
    def untrusted(self) -> int:
        return self.invisible_chars + self.covered_chars


@dataclass
class InkStats:
    analysed: bool = True  # False: over the time budget or the item cap
    paths: int = 0  # visible drawings
    marks: int = 0  # ... that are not only straight lines (a paper template's rules)
    items: int = 0  # their path items
    candidates: int = 0  # small curved complex subpaths, repeats included
    repeated: int = 0  # ... with a twin on the page (same perimeter, area and turning)
    strokes: int = 0  # handwriting-like one-off strokes
    filled_strokes: int = 0
    pen_share: float = 0.0  # share of filled strokes near their colour's median width
    annot_strokes: int = 0  # strokes in /Ink annotations
    rows: int = 0  # bands of writing
    size_twins: float = 0.0  # share of strokes with a same-size twin
    stroke_boxes: list[BBox] = field(default_factory=list)  # display space
    ink_drawings: set[int] = field(default_factory=set)  # indices into the drawings list

    @property
    def repeat_share(self) -> float:
        return self.repeated / self.candidates if self.candidates else 0.0


@dataclass
class PageTypeResult:
    kind: str
    trust: str  # which characters may be used: "all" | "visible_only" | "none" | "empty"
    route: str
    reasons: list[str]
    text: TextStats
    ink: InkStats
    image_coverage: float
    producer: str = "other"

    def to_record(self, page: int) -> PageType:
        """The content-free record stored on the result (`core.types.PageType`)."""
        return PageType(
            page=page,
            kind=self.kind,
            trust=self.trust,
            route=self.route,
            reasons=list(self.reasons),
            visible_chars=self.text.visible_chars,
            invisible_chars=self.text.invisible_chars,
            covered_chars=self.text.covered_chars,
            image_coverage=round(self.image_coverage, 3),
            ink_strokes=self.ink.strokes,
            ink_rows=self.ink.rows,
            ink_repeat_share=round(self.ink.repeat_share, 3),
            ink_pen_share=round(self.ink.pen_share, 3),
            ink_size_twins=round(self.ink.size_twins, 3),
            annot_strokes=self.ink.annot_strokes,
            producer=self.producer,
            classifier_version=CLASSIFIER_VERSION,
        )


class InkBudget:
    """How long the ink analysis may still run for one document (C1.6)."""

    def __init__(self, seconds: float = INK_BUDGET_S) -> None:
        self.deadline = time.monotonic() + seconds

    def left(self) -> bool:
        return time.monotonic() < self.deadline


# ---------------------------------------------------------------------------- geometry


def _area(r: Sequence[float]) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _clip(r: Sequence[float], w: float, h: float) -> BBox:
    return (max(r[0], 0.0), max(r[1], 0.0), min(r[2], w), min(r[3], h))


def _inside(box: Sequence[float], outer: Sequence[float]) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _core_inside(box: Sequence[float], outer: Sequence[float]) -> bool:
    """The middle COVER_CORE of `box` lies inside `outer`."""
    mx = (1 - COVER_CORE) / 2 * (box[2] - box[0])
    my = (1 - COVER_CORE) / 2 * (box[3] - box[1])
    return (
        outer[0] <= box[0] + mx
        and box[2] - mx <= outer[2]
        and outer[1] <= box[1] + my
        and box[3] - my <= outer[3]
    )


class _Grid:
    """Coarse occupancy grid: the union area of many boxes, cheaply."""

    def __init__(self, w: float, h: float, n: int = GRID) -> None:
        self.w, self.h, self.n = w, h, n
        self.cells: set[int] = set()

    def add(self, r: Sequence[float]) -> None:
        n, w, h = self.n, self.w, self.h
        if w <= 0 or h <= 0:
            return
        i0 = max(0, math.ceil(r[0] / w * n - 0.5))  # cells whose centre is inside the box
        i1 = min(n - 1, math.floor(r[2] / w * n - 0.5))
        j0 = max(0, math.ceil(r[1] / h * n - 0.5))
        j1 = min(n - 1, math.floor(r[3] / h * n - 0.5))
        for j in range(j0, j1 + 1):
            self.cells.update(range(j * n + i0, j * n + i1 + 1))

    @property
    def fraction(self) -> float:
        return len(self.cells) / (self.n * self.n)


class _Display:
    """Unrotated page coordinates -> display coordinates (layout's frame)."""

    def __init__(self, page: pymupdf.Page) -> None:
        m = page.rotation_matrix
        self.m = (m.a, m.b, m.c, m.d, m.e, m.f)
        self.rotated = page.rotation % 360 != 0

    def box(self, r: Sequence[float]) -> BBox:
        if not self.rotated:
            return r[0], r[1], r[2], r[3]
        a, b, c, d, e, f = self.m
        x0, y0 = r[0] * a + r[1] * c + e, r[0] * b + r[1] * d + f
        x1, y1 = r[2] * a + r[3] * c + e, r[2] * b + r[3] * d + f
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


# ---------------------------------------------------------------------------- images


def image_boxes(page: pymupdf.Page) -> tuple[list[BBox], float]:
    """Visible boxes of raster images (unrotated page space) and their union coverage of
    the page. MuPDF's image blocks are clipped by the clip path in force, unlike
    get_image_info and get_bboxlog (Google Docs and Canva crop screenshots with clip
    paths); "blocks" output does not decode or copy any image. The page is its CropBox
    (what a reader sees), in whose frame MuPDF gives every box: a scan cropped to trim
    the scanner's margins covers all of it."""
    frame = page.cropbox  # the visible page: text, images and drawings are read in its frame
    grid = _Grid(frame.width, frame.height)
    boxes = []
    for block in page.get_text("blocks", flags=IMAGE_FLAGS):
        if block[6] != 1:
            continue
        box = _clip(block[:4], frame.width, frame.height)
        if _area(box) > 0:
            boxes.append(box)
            grid.add(box)
    return boxes, grid.fraction


# ---------------------------------------------------------------------------- text


def text_stats(
    page: pymupdf.Page,
    raw_blocks: Iterable[dict[str, Any]],
    images: Sequence[BBox],
    drawings: Sequence[dict[str, Any]] | None = None,
) -> TextStats:
    """Visible, invisible and covered characters, from the rawdict layout extracts.

    The paint order is read only when a visible span lies inside an image or an opaque
    filled shape (`_maybe_covered`), so a page with neither costs nothing more."""
    stats = TextStats()
    spans: list[BBox] = []
    clips: list[tuple[BBox, list[tuple[int, int]], int]] = []
    for block in raw_blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                n = sum(1 for ch in span.get("chars", ()) if _ink(ch.get("c", "")))
                if n == 0:
                    continue
                flags = int(span.get("char_flags", CHAR_FILLED))
                if OCR_FONT.search(span.get("font") or ""):
                    stats.ocr_font_chars += n
                if flags & CHAR_CLIPPED:
                    stats.clipped_chars += n
                if span_hidden(span):
                    stats.invisible_chars += n
                    continue
                stats.visible_chars += n
                b = span["bbox"]
                spans.append((b[0], b[1], b[2], b[3]))
                if (run := _clip_run(span)) is not None:
                    clips.append(run)
    if spans and _maybe_covered(spans, images, drawings):
        covered = _covered(page, _thick_strokes(drawings))
        if covered:
            stats.covered = covered
            stats.covered_chars = min(len(covered), stats.visible_chars)
            stats.visible_chars -= stats.covered_chars
    if clips:
        hidden = _cover_test(page, clips)
        for (_, origins, _), gone in zip(clips, hidden, strict=True):
            if gone:  # a clip that nothing was painted through: nobody sees it
                new = {o for o in origins if o not in stats.covered}
                stats.covered |= new
                n = min(len(new), stats.visible_chars)
                stats.invisible_chars += n
                stats.visible_chars -= n
    return stats


def _clip_run(span: dict[str, Any]) -> tuple[BBox, list[tuple[int, int]], int] | None:
    """Text used as a clip that paints nothing itself (render mode 7: MuPDF gives it the
    CLIPPED flag and alpha 0): a run for the render test, which tells a gradient- or
    picture-filled title (visible) from a clip nothing was painted through (C1.4)."""
    flags = int(span.get("char_flags", CHAR_FILLED))
    if not flags & CHAR_CLIPPED or int(span.get("alpha", 255)) > INVISIBLE_ALPHA:
        return None
    chars = [ch for ch in span.get("chars", ()) if _ink(ch.get("c", ""))]
    if not chars:
        return None
    box = (
        min(ch["bbox"][0] for ch in chars),
        min(ch["bbox"][1] for ch in chars),
        max(ch["bbox"][2] for ch in chars),
        max(ch["bbox"][3] for ch in chars),
    )
    return box, [origin_key(ch["origin"]) for ch in chars], len(chars)


def _maybe_covered(
    spans: Sequence[BBox], images: Sequence[BBox], drawings: Sequence[dict[str, Any]] | None
) -> bool:
    """Some visible span lies under an image, a shading, an opaque filled shape or a
    thick stroke (either order): the shape spans the middle of the line's height and
    overlaps it sideways, so a box over one word of a line counts (`_covered` then looks
    glyph by glyph). Annotations count: MuPDF lists their appearance with the drawings.
    MuPDF's image blocks include shadings."""
    shapes = list(images)
    if drawings is None:
        shapes.append((-1e9, -1e9, 1e9, 1e9))  # unknown: look at the paint order
    else:
        for d in drawings:
            fill = d.get("fill")
            opacity = d.get("fill_opacity")
            if fill is None or (isinstance(opacity, int | float) and opacity <= INVISIBLE_OPACITY):
                continue
            rect = d.get("rect")
            if rect is not None and _area(rect) >= 4.0:
                shapes.append((rect[0], rect[1], rect[2], rect[3]))
        shapes += _thick_strokes(drawings) or []
    return any(_across(s, b) for s in spans for b in shapes)


def _thick_strokes(drawings: Sequence[dict[str, Any]] | None) -> list[BBox] | None:
    """Boxes of the stroked drawings with a pen of at least THICK_STROKE, grown by half
    the pen (a marker scribble or a white bar drawn as a line): strokes that can cover
    the middle of a glyph. None when the drawings were not read (every stroke may)."""
    if drawings is None:
        return None
    boxes = []
    for d in drawings:
        width = float(d.get("width") or 0.0)
        opacity = d.get("stroke_opacity")
        if width < THICK_STROKE or d.get("color") is None:
            continue
        if isinstance(opacity, int | float) and opacity <= INVISIBLE_OPACITY:
            continue
        r, half = d["rect"], width / 2
        boxes.append((r[0] - half, r[1] - half, r[2] + half, r[3] + half))
    return boxes


def _across(span: Sequence[float], shape: Sequence[float]) -> bool:
    """`shape` covers the middle COVER_CORE of `span`'s height somewhere along it."""
    my = (1 - COVER_CORE) / 2 * (span[3] - span[1])
    return (
        shape[1] <= span[1] + my
        and span[3] - my <= shape[3]
        and shape[0] < span[2]
        and span[0] < shape[2]
    )


def _covered(page: pymupdf.Page, thick: Sequence[BBox] | None = None) -> set[tuple[int, int]]:
    """Origins of visible characters painted before an image, a shading, a filled shape
    or a thick stroke that covers them, and that do not show through it (`_cover_test`).
    Annotations are painted after the page, so a box drawn with an annotation tool is a
    cover like any other. A stroke is a cover only when it matches one of the `thick`
    strokes (`_thick_strokes`; None: any stroke may be), so the thin rules of a table drawn
    over its cells are not tested."""
    log = page.get_bboxlog()
    covers = [
        (i, box)
        for i, (kind, box) in enumerate(log)
        if _area(box) >= 4.0
        and (
            kind in ("fill-image", "fill-imgmask", "fill-path", "fill-shade")
            or (kind == "stroke-path" and (thick is None or any(_same_box(box, t) for t in thick)))
        )
    ]
    if not covers:
        return set()
    # One run per line of a trace span (a span can hold a whole paragraph): the box of its
    # covered glyphs only, so the render test removes nothing else.
    runs: list[tuple[BBox, list[tuple[int, int]], int]] = []  # (glyphs' box, origins, chars)
    for span in page.get_texttrace():
        if span.get("type") not in (0, 1) or float(span.get("opacity", 1.0)) <= INVISIBLE_OPACITY:
            continue
        seq = int(span.get("seqno", -1))
        sbox = span["bbox"]
        later = [b for i, b in covers if i > seq and _overlap(sbox, b)]
        if not later:
            continue
        lines: dict[int, list[tuple[Sequence[float], tuple[int, int]]]] = {}
        for ch in span.get("chars", ()):
            text = chr(ch[0]) if isinstance(ch[0], int) else str(ch[0])
            if _ink(text) and any(_core_inside(ch[3], b) for b in later):
                lines.setdefault(round(ch[2][1]), []).append((ch[3], origin_key(ch[2])))
        for glyphs in lines.values():
            box = (
                min(g[0][0] for g in glyphs),
                min(g[0][1] for g in glyphs),
                max(g[0][2] for g in glyphs),
                max(g[0][3] for g in glyphs),
            )
            runs.append((box, [g[1] for g in glyphs], len(glyphs)))
    if not runs:
        return set()
    hidden = _cover_test(page, runs)
    return {o for (_, origins, _), h in zip(runs, hidden, strict=True) if h for o in origins}


def _overlap(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _same_box(a: Sequence[float], b: Sequence[float]) -> bool:
    """Two boxes of one drawing (the paint log's and the drawings list's): they share at
    least half of the smaller one."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return False
    return w * h >= 0.5 * min(_area(a), _area(b))


def _cover_test(
    page: pymupdf.Page, runs: list[tuple[BBox, list[tuple[int, int]], int]]
) -> list[bool]:
    """For each candidate run of covered text: is it really hidden? Render the page (a
    one-page copy, unrotated, with its annotations: a box drawn with an annotation tool
    hides like one in the page), remove the runs with redactions that remove text only,
    render again: text that still showed changes pixels in its box. The largest
    COVER_TEST_SPANS runs are tested; the rest follow the majority. Any failure means
    "not hidden", so the text is kept as it was before this check existed."""
    order = sorted(range(len(runs)), key=lambda i: -runs[i][2])
    tested = order[:COVER_TEST_SPANS]
    verdict: dict[int, bool] = {}
    copy = pymupdf.open()
    try:
        copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number, annots=True)
        # Annotations become page content first: applying a redaction deletes the ones it
        # touches (a FreeText box), which would make covered text look visible.
        copy.bake(annots=True, widgets=True)
        one = copy[0]
        one.set_rotation(0)
        clip = pymupdf.Rect(runs[tested[0]][0])
        for i in tested[1:]:
            clip |= pymupdf.Rect(runs[i][0])
        clip &= one.rect
        if clip.is_empty:
            return [False] * len(runs)
        matrix = pymupdf.Matrix(COVER_ZOOM, COVER_ZOOM)
        before = one.get_pixmap(matrix=matrix, clip=clip, colorspace=pymupdf.csGRAY, alpha=False)
        for i in tested:  # a little inside the glyphs, so their neighbours stay
            x0, y0, x1, y1 = runs[i][0]
            inset = min(REDACT_INSET, (x1 - x0) / 4)
            one.add_redact_annot(pymupdf.Rect(x0 + inset, y0, x1 - inset, y1), fill=())
        one.apply_redactions(
            images=pymupdf.mupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.mupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.mupdf.PDF_REDACT_TEXT_REMOVE,
        )
        after = one.get_pixmap(matrix=matrix, clip=clip, colorspace=pymupdf.csGRAY, alpha=False)
        if (before.width, before.height) != (after.width, after.height):
            return [False] * len(runs)
        for i in tested:
            verdict[i] = _unchanged(before, after, clip, runs[i][0])
    except Exception:  # a page the redaction cannot rewrite: keep its text
        return [False] * len(runs)
    finally:
        copy.close()
    hidden_votes = sum(verdict.values())
    majority = hidden_votes * 2 > len(verdict)
    return [verdict.get(i, majority) for i in range(len(runs))]


def _unchanged(
    before: pymupdf.Pixmap, after: pymupdf.Pixmap, clip: pymupdf.Rect, box: BBox
) -> bool:
    """Removing the text in `box` changed almost no pixels of it."""
    x0 = max(0, int((box[0] - clip.x0) * COVER_ZOOM))
    y0 = max(0, int((box[1] - clip.y0) * COVER_ZOOM))
    x1 = min(before.width, math.ceil((box[2] - clip.x0) * COVER_ZOOM))
    y1 = min(before.height, math.ceil((box[3] - clip.y0) * COVER_ZOOM))
    if x1 <= x0 or y1 <= y0:
        return False
    a, b, stride = before.samples, after.samples, before.stride
    changed = 0
    for y in range(y0, y1):
        row = y * stride
        ra, rb = a[row + x0 : row + x1], b[row + x0 : row + x1]
        if ra != rb:
            changed += sum(1 for p, q in zip(ra, rb, strict=True) if abs(p - q) > COVER_PIXEL_DELTA)
    total = (x1 - x0) * (y1 - y0)
    return changed <= max(2, COVER_CHANGED_SHARE * total)


# ---------------------------------------------------------------------------- ink


def _visible_paint(colour: object, opacity: object) -> bool:
    if colour is None or (isinstance(opacity, int | float) and opacity <= 0.01):
        return False
    return not (isinstance(colour, tuple | list) and all(v >= 0.97 for v in colour))


def _rules_only(drawing: dict[str, Any], width: float, height: float) -> bool:
    """The rules of ruled, squared or margin paper (a page with only these is blank
    paper): stroked, not filled, horizontal and vertical lines only, each a line of its
    own (not continuing the one before it) and long, at least RULE_MIN_SHARE of the
    page across. A square wave, a step plot or a line-drawn block diagram joins its
    lines into one path, or has short ones: it is a mark."""
    if drawing.get("fill") is not None:
        return False
    last = None
    for item in drawing.get("items") or ():
        if item[0] != "l":
            return False
        (x0, y0), (x1, y1) = item[1], item[2]
        if abs(x0 - x1) > 0.5 and abs(y0 - y1) > 0.5:
            return False
        if last is not None and _near(item[1], last[1]):
            if _near(item[2], last[0]):
                continue  # the same line back: a closed one-line subpath
            return False  # continues the line before it: a polyline
        long_x = abs(x1 - x0) >= RULE_MIN_SHARE * width
        long_y = abs(y1 - y0) >= RULE_MIN_SHARE * height
        if not (long_x or long_y):
            return False
        last = (item[1], item[2])
    return True


def _near(p: Sequence[float], q: Sequence[float]) -> bool:
    return abs(p[0] - q[0]) <= 0.5 and abs(p[1] - q[1]) <= 0.5


def _subpaths(items: Sequence[Any]) -> list[list[Any]]:
    """Split a drawing's items where the pen jumps (a new moveto). Rectangles and quads
    are subpaths of their own."""
    out: list[list[Any]] = []
    cur: list[Any] = []
    last = None
    for it in items:
        if it[0] in ("re", "qu"):
            if cur:
                out.append(cur)
            cur, last = [], None
            out.append([it])
            continue
        start = it[1]
        if last is not None and (abs(start[0] - last[0]) > 0.05 or abs(start[1] - last[1]) > 0.05):
            out.append(cur)
            cur = []
        cur.append(it)
        last = it[-1]
    if cur:
        out.append(cur)
    return out


def _flatten(sub: Sequence[Any]) -> tuple[list[tuple[float, float]], int, int]:
    """Polyline of a subpath (a Bezier contributes its midpoint), its curves and lines."""
    pts: list[tuple[float, float]] = []
    curves = lines = 0
    for it in sub:
        op = it[0]
        if op == "l":
            lines += 1
            seq = (it[1], it[2])
        elif op == "c":
            curves += 1
            p0, p1, p2, p3 = it[1], it[2], it[3], it[4]
            mid = (
                (p0[0] + 3 * p1[0] + 3 * p2[0] + p3[0]) / 8,
                (p0[1] + 3 * p1[1] + 3 * p2[1] + p3[1]) / 8,
            )
            seq = (p0, mid, p3)
        else:
            return [], 0, 0
        for p in seq:
            if not pts or abs(p[0] - pts[-1][0]) > 1e-3 or abs(p[1] - pts[-1][1]) > 1e-3:
                pts.append((p[0], p[1]))
    return pts, curves, lines


def _box(pts: Sequence[tuple[float, float]]) -> BBox:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class _Candidate:
    box: BBox  # display space
    sig: tuple  # rotation-invariant: (closed, curves, lines, perimeter, area or chord, turning)
    stroke: bool  # passes the complexity, turning and ribbon tests
    filled: bool
    ribbon: float  # filled: mean width 2A/P; stroked: line width
    sources: tuple[int, ...]  # the drawings it was made from
    colour: tuple = ()
    machine: bool = False  # rejected on evidence of machine drawing (blob, jagged)
    complex: bool = True  # enough points to compare shapes; simple shapes collide by chance


def _candidate(
    pts: list[tuple[float, float]],
    curves: int,
    lines: int,
    closed: bool,
    filled: bool,
    width: float,
    display: _Display,
    sources: tuple[int, ...],
    colour: tuple = (),
) -> _Candidate | None:
    """None for anything that cannot be a pen stroke (too small, too big, rectilinear)."""
    if len(pts) < 2:
        return None
    x0, y0, x1, y1 = _box(pts)
    side = max(x1 - x0, y1 - y0)
    if side < STROKE_MIN_SIDE or side > STROKE_MAX_SIDE:
        return None
    oblique = 0
    perimeter = 0.0
    area2 = 0.0
    turning = 0.0
    zigzags = 0  # sharp turns against the direction of the previous sharp turn
    turns = 0
    last_sign = 0
    prev = None
    n = len(pts)
    for i in range(n - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        dx, dy = bx - ax, by - ay
        adx, ady = abs(dx), abs(dy)
        s = adx + ady
        if s == 0:
            continue
        if adx > 0.08 * s and ady > 0.08 * s:  # more than ~5 degrees off both axes
            oblique += 1
        perimeter += math.hypot(dx, dy)
        area2 += ax * by - bx * ay
        if prev is not None:
            angle = math.atan2(prev[0] * dy - prev[1] * dx, prev[0] * dx + prev[1] * dy)
            turning += abs(angle)
            turns += 1
            if abs(angle) > ZIGZAG_ANGLE:
                sign = 1 if angle > 0 else -1
                if last_sign and sign != last_sign:
                    zigzags += 1
                last_sign = sign
        prev = (dx, dy)
    if curves == 0 and oblique < OBLIQUE_MIN:
        return None  # rectangles, rules, ticks, table grids, step lines
    box = display.box((x0, y0, x1, y1))
    if closed:
        ax, ay = pts[-1]
        bx, by = pts[0]
        perimeter += math.hypot(bx - ax, by - ay)
        area2 += ax * by - bx * ay
        if prev is not None and (ax, ay) != (bx, by):
            dx, dy = bx - ax, by - ay
            turning += abs(math.atan2(prev[0] * dy - prev[1] * dx, prev[0] * dx + prev[1] * dy))
        third = round(abs(area2) / 2 / SIG_GRID)
    else:
        third = round(math.dist(pts[0], pts[-1]) / SIG_GRID)
    sig = (closed, curves, lines, round(perimeter / SIG_GRID), third, round(turning / SIG_TURN))
    ribbon = (abs(area2) / perimeter if perimeter else 0.0) if (filled and closed) else width
    zig = zigzags / turns if turns else 0.0
    if n < (FILLED_MIN_POINTS if filled else STROKE_MIN_POINTS):
        return _Candidate(box, sig, False, filled, ribbon, sources, colour, False, False)
    ok = turning >= (CLOSED_TURNING if closed else OPEN_TURNING)
    machine = False
    if ok and filled and closed and ribbon > RIBBON_MAX_RATIO * side:
        ok, machine = False, True  # a blob (disc, region, island), not a pen ribbon
    if ok and zig > ZIGZAG_MAX:
        ok, machine = False, True  # jagged: random-step decorations, random walks
    return _Candidate(box, sig, ok, filled and closed, ribbon, sources, colour, machine)


def _size_twin_share(boxes: Sequence[BBox]) -> float:
    """Share of strokes with another stroke of the same box size (width and height within
    SIZE_TWIN_TOLERANCE). Machine layouts repeat sizes; hands do not."""
    if len(boxes) < 2:
        return 0.0
    step = math.log1p(SIZE_TWIN_TOLERANCE)
    cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
    sizes = []
    for b in boxes:
        w, h = max(b[2] - b[0], 0.01), max(b[3] - b[1], 0.01)
        key = (int(math.log(w) / step), int(math.log(h) / step))
        cells.setdefault(key, []).append((w, h))
        sizes.append((w, h, key))
    twins = 0
    for w, h, (i, j) in sizes:
        tw = max(SIZE_TWIN_TOLERANCE * w, SIZE_TWIN_ABS)
        th = max(SIZE_TWIN_TOLERANCE * h, SIZE_TWIN_ABS)
        found = 0
        reach_i = 1 + int(tw / w / step)  # cells to look at either side
        reach_j = 1 + int(th / h / step)
        for di in range(-reach_i, reach_i + 1):
            for dj in range(-reach_j, reach_j + 1):
                for w2, h2 in cells.get((i + di, j + dj), ()):
                    if abs(w2 - w) <= tw and abs(h2 - h) <= th:
                        found += 1
        twins += found > 1  # itself plus at least one other
    return twins / len(boxes)


def _rows(shapes: list[tuple[BBox, int]]) -> int:
    """Rows of writing: the best count over a few baseline angles."""
    if not shapes:
        return 0
    return max(_rows_at(shapes, math.radians(a)) for a in ROW_ANGLES)


def _rows_at(shapes: list[tuple[BBox, int]], theta: float) -> int:
    """Bands of writing along baselines turned by theta. Each shape votes +1 (pen
    stroke), -1 (machine: an exact repeat, a blob, a jagged path) or 0 (too simple to
    say). A band is a row of writing when it is at most ROW_MAX_HEIGHT tall, holds at
    least ROW_MIN_STROKES pen strokes, and pen strokes are at least ROW_STROKE_SHARE of
    the strokes and machine shapes in it."""
    s, c = math.sin(theta), math.cos(theta)
    cores = []
    for b, vote in shapes:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        ext = abs(b[3] - b[1]) * c + abs(b[2] - b[0]) * abs(s)  # extent across the baseline
        y = cy * c - cx * s
        cores.append((y - 0.25 * ext, y + 0.25 * ext, y - 0.5 * ext, y + 0.5 * ext, vote))
    cores.sort()
    bands: list[list[float]] = []  # [core top, core bottom, top, bottom, strokes, machine]
    for c0, c1, top, bottom, vote in cores:
        if not (bands and c0 <= bands[-1][1]):
            bands.append([c0, c1, top, bottom, 0, 0])
        band = bands[-1]
        band[1] = max(band[1], c1)
        band[2] = min(band[2], top)
        band[3] = max(band[3], bottom)
        band[4] += vote > 0
        band[5] += vote < 0
    return sum(
        1
        for b in bands
        if b[4] >= ROW_MIN_STROKES
        and b[4] >= ROW_STROKE_SHARE * (b[4] + b[5])
        and b[3] - b[2] <= ROW_MAX_HEIGHT
    )


def ink_stats(
    page: pymupdf.Page, drawings: Sequence[dict[str, Any]], budget: InkBudget | None = None
) -> InkStats:
    """Handwriting evidence from the drawings layout extracted. Counts the visible paths
    always; the stroke analysis only within the budget and the item cap."""
    stats = InkStats()
    frame = page.cropbox  # the visible page: text, images and drawings are read in its frame
    for d in drawings:
        if not (
            _visible_paint(d.get("fill"), d.get("fill_opacity"))
            or _visible_paint(d.get("color"), d.get("stroke_opacity"))
        ):
            continue
        rect = d["rect"]
        if rect[2] < 0 or rect[3] < 0 or rect[0] > frame.width or rect[1] > frame.height:
            continue
        stats.paths += 1
        stats.items += len(d.get("items") or ())
        if not _rules_only(d, frame.width, frame.height):
            stats.marks += 1
    for annot in page.annots(types=[pymupdf.mupdf.PDF_ANNOT_INK]) or []:
        stats.annot_strokes += len(annot.vertices or [])
    if stats.items > INK_MAX_ITEMS or (budget is not None and stats.paths and not budget.left()):
        stats.analysed = False
        return stats
    if not stats.paths:
        return stats
    cands = _ink_candidates(page, drawings)
    return _summarise(page, stats, cands)


def _ink_candidates(page: pymupdf.Page, drawings: Sequence[dict[str, Any]]) -> list[_Candidate]:
    """Every subpath that could be a pen stroke. /Ink annotation appearances are in the
    drawings already (MuPDF traces them), so they are not added again."""
    frame = page.cropbox  # the visible page: text, images and drawings are read in its frame
    display = _Display(page)
    cands: list[_Candidate] = []
    # Stroked ink: Xournal++ writes one segment per path object, so open subpaths that
    # continue where the previous one ended, in the same colour, are chained into one.
    chain: list[tuple[float, float]] = []
    counts = [0, 0]
    chain_colour: tuple = ()
    chain_width = 0.0
    chain_sources: list[int] = []

    def flush() -> None:
        nonlocal chain, chain_sources
        if chain:
            c = _candidate(chain, counts[0], counts[1], False, False, chain_width, display,
                           tuple(dict.fromkeys(chain_sources)))  # fmt: skip
            if c:
                cands.append(c)
        chain, chain_sources = [], []
        counts[0] = counts[1] = 0

    for index, d in enumerate(drawings):
        filled = _visible_paint(d.get("fill"), d.get("fill_opacity"))
        stroked = _visible_paint(d.get("color"), d.get("stroke_opacity"))
        if not (filled or stroked):
            continue
        rect = d["rect"]
        if rect[2] < 0 or rect[3] < 0 or rect[0] > frame.width or rect[1] > frame.height:
            continue
        if rect[2] - rect[0] < STROKE_MIN_SIDE and rect[3] - rect[1] < STROKE_MIN_SIDE and filled:
            continue  # a dot; a stroked tiny segment may still be part of a chain
        width = float(d.get("width") or 0.0)
        for sub in _subpaths(d.get("items") or []):
            pts, curves, lines = _flatten(sub)
            if not pts:
                if not filled:
                    flush()
                continue
            closed = (
                filled
                or bool(d.get("closePath"))
                or (
                    len(pts) > 3
                    and abs(pts[0][0] - pts[-1][0]) < 0.3
                    and abs(pts[0][1] - pts[-1][1]) < 0.3
                )
            )
            if not closed:
                colour = tuple(d.get("color") or ())
                joined = (
                    chain
                    and colour == chain_colour
                    and abs(pts[0][0] - chain[-1][0]) < 0.3
                    and abs(pts[0][1] - chain[-1][1]) < 0.3
                )
                if joined:
                    chain.extend(pts[1:])
                else:
                    flush()
                    chain = list(pts)
                    chain_colour, chain_width = colour, width
                chain_sources.append(index)
                counts[0] += curves
                counts[1] += lines
                continue
            flush()
            box = _box(pts)
            if max(box[2] - box[0], box[3] - box[1]) > STROKE_MAX_SIDE:
                continue
            paint = d.get("fill") if filled else d.get("color")
            colour = tuple(round(v, 2) for v in paint or ())
            c = _candidate(pts, curves, lines, True, filled, width, display, (index,), colour)
            if c:
                cands.append(c)
    flush()
    return cands


def _summarise(page: pymupdf.Page, stats: InkStats, cands: list[_Candidate]) -> InkStats:
    # Repeats are counted among complex shapes only: two short simple strokes of a small
    # hand can share perimeter, area and turning by chance; glyph outlines and plot
    # markers are complex and repeat exactly.
    counts = Counter(c.sig for c in cands if c.complex)
    stats.candidates = sum(1 for c in cands if c.complex)
    stats.repeated = sum(1 for c in cands if c.complex and counts[c.sig] > 1)
    strokes = [c for c in cands if c.stroke and counts[c.sig] == 1]
    stats.strokes = len(strokes)
    if not strokes:
        return stats
    filled = [c for c in strokes if c.filled]
    stats.filled_strokes = len(filled)
    if filled:
        by_colour: dict[tuple, list[float]] = {}
        for c in filled:
            by_colour.setdefault(c.colour, []).append(c.ribbon)
        near = 0
        for widths in by_colour.values():
            med = statistics.median(widths)
            near += sum(1 for w in widths if PEN_BAND[0] * med <= w <= PEN_BAND[1] * med)
        stats.pen_share = near / len(filled)
    boxes = [c.box for c in strokes]
    stats.size_twins = _size_twin_share(boxes)
    shapes = [
        (c.box, -1 if ((c.complex and counts[c.sig] > 1) or c.machine) else (1 if c.stroke else 0))
        for c in cands
    ]
    stats.rows = _rows(shapes)
    stats.stroke_boxes = boxes
    # A drawing is ink when most of the shapes taken from it are pen strokes (an iOS path
    # object holds about 120 strokes; a ruled template's lines are never candidates).
    votes: Counter[int] = Counter()
    seen: Counter[int] = Counter()
    for c in cands:
        stroke = c.stroke and counts[c.sig] == 1
        for s in c.sources:
            seen[s] += 1
            votes[s] += stroke
    stats.ink_drawings = {s for s, n in seen.items() if votes[s] * 2 >= n}
    del page
    return stats


def producer_hint(doc: pymupdf.Document) -> str:
    """A coarse family of the producing software. Recorded as a hint, never decides."""
    meta = doc.metadata or {}
    s = f"{meta.get('producer') or ''} {meta.get('creator') or ''}".lower()
    for word, hint in (
        ("ios version", "apple_mobile"),
        ("ipados", "apple_mobile"),
        ("goodnotes", "note_app"),
        ("notability", "note_app"),
        ("xournal", "note_app"),
        ("onenote", "note_app"),
        ("remarkable", "note_app"),
        ("ocrmypdf", "ocr_tool"),
        ("tesseract", "ocr_tool"),
        ("abbyy", "ocr_tool"),
        ("finereader", "ocr_tool"),
        ("paper capture", "ocr_tool"),
        ("scan", "scanner"),
        ("tex", "tex"),
        ("skia", "chromium"),
        ("microsoft", "office"),
        ("word", "office"),
    ):
        if word in s:
            return hint
    return "other"


# ---------------------------------------------------------------------------- decision


def _pen_like(ink: InkStats) -> bool:
    """Ink that is mostly filled ribbons must share one pen width (typed glyph outlines
    and vector art do not)."""
    if ink.filled_strokes * 2 < ink.strokes:
        return True
    return ink.pen_share >= PEN_SHARE_MIN


def decide(
    text: TextStats, ink: InkStats, image_coverage: float, producer: str = "other"
) -> PageTypeResult:
    untrusted = text.untrusted
    has_text = text.visible_chars >= TEXT_MIN
    clean = (
        ink.repeat_share <= INK_MAX_REPEATS and _pen_like(ink) and ink.size_twins <= SIZE_TWIN_MAX
    )
    writing = clean and ink.strokes >= INK_PAGE_STROKES and ink.rows >= INK_PAGE_ROWS
    some_ink = clean and ink.strokes >= INK_MIXED_STROKES and ink.rows >= INK_MIXED_ROWS
    # A few clean strokes in one row are a short note only on a page with no typed text:
    # under a typed title they are as likely a row of machine-drawn curves (five waves,
    # five inductor coils), so such a page needs some ink or a second row.
    ink_alone = clean and ink.strokes >= INK_ALONE_STROKES and ink.rows >= 1
    if text.visible_chars:
        ink_alone = some_ink or (ink_alone and ink.rows >= 2)
    # Rows of writing made of pen strokes, though the clean checks rejected the ink
    # (exact repeats among tiny strokes): what dense handwriting looks like; typed glyph
    # outlines vote against their own rows (they repeat), so they make none.
    written_rows = ink.strokes >= INK_MIXED_STROKES and ink.rows >= INK_PAGE_ROWS
    reasons: list[str] = []
    if text.invisible_chars:
        reasons.append("invisible_text")
    if text.covered_chars:
        reasons.append("text_covered")
    if text.ocr_font_chars:
        reasons.append("ocr_font")
    if not ink.analysed:
        reasons.append("ink_not_analysed")
    elif writing:
        reasons.append("ink_writing")
    elif some_ink:
        reasons.append("ink_some")
    elif ink.strokes >= INK_MIXED_STROKES:
        reasons.append("ink_rejected")  # strokes, but repeats, no single pen, or a layout
    if image_coverage >= SCAN_COVERAGE:
        reasons.append("page_image")

    if text.visible_chars + untrusted == 0:
        trust = "empty"
    elif untrusted == 0:
        trust = "all"
    elif text.visible_chars == 0:
        trust = "none"
    else:
        trust = "visible_only"

    def result(kind: str, extra: list[str] | None = None) -> PageTypeResult:
        return PageTypeResult(
            kind, trust, ROUTE[kind], reasons + (extra or []), text, ink, image_coverage, producer
        )

    ocr = untrusted >= HIDDEN_FEW  # any hidden words: the layer must not be read
    # Hidden-ratio rule (C1.2): more hidden than visible text, or a page image with a
    # hidden layer, is a picture or ink page whatever its visible text (a scan with a
    # visible "Downloaded from" stamp; notes under a typed template header).
    hidden_page = untrusted >= max(OCR_MIN, text.visible_chars) or (
        image_coverage >= SCAN_COVERAGE and untrusted >= OCR_MIN
    )
    if hidden_page:
        reasons.append("hidden_ratio")
    if has_text and not hidden_page:
        # A page with a real visible text layer is never a picture of a page as a whole.
        # At most it is mixed_ink, which keeps the text layer.
        if some_ink:
            return result("mixed_ink")
        if ocr:
            return result("mixed_hidden_text")
        return result("born_digital", [] if reasons else ["visible_text"])
    if writing or (ink_alone and image_coverage < SCAN_COVERAGE):
        if "ink_writing" not in reasons and "ink_some" not in reasons:
            reasons.append("ink_few")
        return result("handwritten_ocr" if ocr else "handwritten")
    picture = image_coverage >= PICTURE_COVERAGE or text.visible_chars == 0
    if image_coverage >= SCAN_COVERAGE and picture:
        return result("scan_ocr" if ocr else "scan_no_text")
    if untrusted >= OCR_MIN:
        # A hidden layer and no picture of a page: over vector ink (clean, or rows of
        # writing the checks rejected, or ink not analysed) it is a handwriting
        # recognition layer. Over machine-drawn vector content (text turned into
        # outlines, a diagram: what an OCR program writes over a vector page) the page
        # is drawings: the hidden layer is dropped and the drawings are read as figures.
        if image_coverage < SCAN_COVERAGE:
            if some_ink or written_rows or not ink.analysed:
                return result("handwritten_ocr", ["invisible_without_image"])
            if ink.marks >= VECTOR_MIN_PATHS or ink.candidates >= VECTOR_MIN_PATHS:
                return result("vector_no_text", ["invisible_without_image"])
        return result("scan_ocr", ["invisible_without_image"])
    if ink.marks >= VECTOR_MIN_PATHS or ink.candidates >= VECTOR_MIN_PATHS or image_coverage > 0:
        return result("vector_no_text", ["little_text"])
    if text.visible_chars:
        return result("born_digital", ["little_text"])
    if ink.marks:
        return result("vector_no_text", ["little_text"])
    return result("blank", ["paper_only"] if ink.paths else ["empty"])


def classify(
    page: pymupdf.Page,
    raw_blocks: Iterable[dict[str, Any]],
    drawings: Sequence[dict[str, Any]],
    *,
    budget: InkBudget | None = None,
    producer: str = "other",
) -> PageTypeResult:
    """The page's type, from the rawdict blocks and drawings layout already extracted."""
    images, coverage = image_boxes(page)
    text = text_stats(page, raw_blocks, images, drawings)
    ink = ink_stats(page, drawings, budget)
    return decide(text, ink, coverage, producer)


# ---------------------------------------------------------------------------- what to do


HANDWRITTEN_KINDS = frozenset({"handwritten", "handwritten_ocr"})
SCAN_KINDS = frozenset({"scan_no_text", "scan_ocr"})


def kept_reason(page: PageTypeResult | PageType) -> str | None:
    """The Fix reason when this app keeps the whole page as its image (nothing on it is
    converted), else None. There is no handwriting or scan conversion: handwritten and
    scanned pages are kept as images, and so is a page with drawings and less than a line
    of visible text whose ink could not be analysed in time (C1.6: a page of notes with a
    typed title, after the budget ran out, may be handwriting; its title is kept in the
    page image). Works on a classifier result and on the stored record alike."""
    if page.kind in HANDWRITTEN_KINDS:
        return HANDWRITTEN_PAGE
    if page.kind in SCAN_KINDS:
        return SCANNED_PAGE
    if (
        page.kind == "vector_no_text"
        and "ink_not_analysed" in page.reasons
        and _visible_chars(page) < TEXT_MIN
    ):
        return UNREAD_PAGE
    return None


def _visible_chars(page: PageTypeResult | PageType) -> int:
    return page.text.visible_chars if isinstance(page, PageTypeResult) else page.visible_chars


def ink_regions(result: PageTypeResult) -> list[BBox]:
    """Boxes (display space) of the handwriting on a typed page (mixed_ink): strokes
    within REGION_GAP of each other form one region, padded by REGION_PAD; more than
    MAX_REGIONS become one box round all of them."""
    if result.kind != "mixed_ink" or not result.ink.stroke_boxes:
        return []
    boxes = sorted(result.ink.stroke_boxes)
    regions: list[list[float]] = []
    for b in boxes:
        for r in regions:
            if (
                b[0] <= r[2] + REGION_GAP
                and r[0] <= b[2] + REGION_GAP
                and b[1] <= r[3] + REGION_GAP
                and r[1] <= b[3] + REGION_GAP
            ):
                r[0], r[1] = min(r[0], b[0]), min(r[1], b[1])
                r[2], r[3] = max(r[2], b[2]), max(r[3], b[3])
                break
        else:
            regions.append(list(b))
    merged = True
    while merged and len(regions) > 1:  # regions that grew into each other
        merged = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, c = regions[i], regions[j]
                if a[0] <= c[2] and c[0] <= a[2] and a[1] <= c[3] and c[1] <= a[3]:
                    a[:] = [min(a[0], c[0]), min(a[1], c[1]), max(a[2], c[2]), max(a[3], c[3])]
                    del regions[j]
                    merged = True
                    break
            if merged:
                break
    if len(regions) > MAX_REGIONS:
        regions = [
            [
                min(r[0] for r in regions),
                min(r[1] for r in regions),
                max(r[2] for r in regions),
                max(r[3] for r in regions),
            ]
        ]
    pad = REGION_PAD
    return [
        (r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad)
        for r in sorted(regions, key=lambda r: (r[1], r[0]))
    ]


# ---------------------------------------------------------------------------- the upload


REFUSAL_CODES = ("handwritten", "handwritten_no_text", "scanned", "scanned_ocr", "no_content")


def _certain(page: PageType) -> str | None:
    """`kept_reason` for a page that is certainly handwritten or a scan. A full-page
    picture with a few visible characters and no hidden layer may be a slide with a
    background photo and a title, and a few strokes under a typed title ("ink_few") may
    be a machine-drawn diagram: such pages are kept as images with a decision when the
    PDF is converted, but they never refuse the upload."""
    reason = kept_reason(page)
    sure = page.visible_chars == 0 or page.untrusted_chars >= HIDDEN_FEW
    if reason == HANDWRITTEN_PAGE and (sure or "ink_few" not in page.reasons):
        return reason
    if reason == SCANNED_PAGE and sure:
        return reason
    return None


def refusal_code(pages: Sequence[PageType]) -> str | None:
    """Why a whole document cannot be converted, or None if some page can (the upload
    probe, and the worker for a document the probe did not see whole).

    Every page certainly handwritten or scanned (`_certain`), or blank, and at least one
    not blank: "handwritten" (with a hidden recognition layer) or "handwritten_no_text"
    when handwritten pages are at least as many as scanned ones, else "scanned_ocr" (a
    hidden OCR layer) or "scanned". Every page blank: "no_content". A page whose ink was
    not analysed in time is never refused: the conversion keeps it as an image with a
    decision."""
    if not pages:
        return None
    certain = [(p, reason) for p in pages if (reason := _certain(p))]
    if len(certain) + sum(p.kind == "blank" for p in pages) < len(pages):
        return None
    if not certain:
        return "no_content"
    hand = sum(reason == HANDWRITTEN_PAGE for _, reason in certain)
    hidden = any(p.untrusted_chars >= HIDDEN_FEW for p, _ in certain)
    if hand * 2 >= len(certain):
        return "handwritten" if hidden else "handwritten_no_text"
    return "scanned_ocr" if hidden else "scanned"


def clearly_convertible(text: TextStats, coverage: float) -> bool:
    """A page that is a typed page whatever its ink: enough visible text, and not the
    hidden-ratio rule. The upload probe stops at the first one."""
    untrusted = text.untrusted
    hidden_page = untrusted >= max(OCR_MIN, text.visible_chars) or (
        coverage >= SCAN_COVERAGE and untrusted >= OCR_MIN
    )
    return text.visible_chars >= TEXT_MIN and not hidden_page


def document_refusal(doc: pymupdf.Document, *, budget_s: float = INK_BUDGET_S) -> str | None:
    """`refusal_code` for a whole open document (the upload probe). Reads the text of
    each page first and stops at the first clearly typed page, so only documents with
    no such page pay for the drawings and the ink analysis (within `budget_s`)."""
    texts: list[tuple[int, pymupdf.Page, TextStats, float]] = []
    for number in range(doc.page_count):
        page = doc[number]
        raw = page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]
        images, coverage = image_boxes(page)
        stats = text_stats(page, raw, images, None)
        if clearly_convertible(stats, coverage):
            return None
        texts.append((number, page, stats, coverage))
    budget = InkBudget(budget_s)
    hint = producer_hint(doc)
    records = []
    for number, page, stats, coverage in texts:
        drawings = page.get_cdrawings() if budget.left() else None
        if drawings is None:
            ink = InkStats(analysed=False, paths=1, marks=1)  # unknown: never refused
        else:
            ink = ink_stats(page, drawings, budget)
        records.append(decide(stats, ink, coverage, hint).to_record(number))
    return refusal_code(records)
