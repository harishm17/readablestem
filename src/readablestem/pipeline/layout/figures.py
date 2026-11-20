"""Figures and page furniture: raster images and their budgets, clusters of vector drawings,
diagrams, bonds and plots, labels and their owners, running text that is never part of a
figure, and the bands (headers, footers, frames) around a page. This is the second half of the
old figures section; the drawings it reads are in `drawings`.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from itertools import combinations
from typing import Any, cast

import pymupdf

from readablestem.core import images as pixel_budget
from readablestem.core.captions import FIGURE_CAPTION as CAPTION
from readablestem.core.types import BBox
from readablestem.pipeline import equation_images
from readablestem.pipeline.layout import entries
from readablestem.pipeline.layout.context import LayoutError, _Document
from readablestem.pipeline.layout.drawings import JOIN_PT, MIN_IMAGE_PT
from readablestem.pipeline.layout.glyphs import _union
from readablestem.pipeline.layout.shared import (
    _MATH_WORDS,
    _area,
    _box_key,
    _CharInfo,
    _Context,
    _Display,
    _Figure,
    _lead_end,
    _limit_end,
    _Path,
    _Row,
    _RowIndex,
    _Run,
    _Straight,
    _tokens,
)

# Sizes are PDF points unless the name says otherwise. docs/layout.md explains each one.

HEADING_SIZE_RATIO = 1.15  # a short run at least this much larger than body text
TITLE_MIN_PT = 18.0  # a row of several words this large is a title, never a figure's label
MIN_IMAGE_PX = 24  # raster images with a side smaller than this (pixels) are decoration
FULL_PAGE_FRACTION = 0.85  # images covering this share of the page: background or scan
MIN_DRAWING_PT = 24.0  # a vector figure is at least this big on both sides
FIGURE_PART_REACH = 12.0  # a smaller drawing this close to a vector figure is part of it
DRAWING_CLUSTER_GAP = 6.0  # drawings closer than this belong to the same figure
CLUSTER_BAND_PT = 16.0  # `_cluster` looks up earlier boxes in horizontal bands this tall
CLUSTER_MAX_BANDS = 32  # a box spanning more bands than this is checked against every box
PAGE_SIZED_FRACTION = 0.85  # a drawing this wide and tall fills the page (or its frame)
FRAME_EDGE_BAND = 0.15  # a page-long rule within this share of an edge is a frame's side

# Leftover drawings this close to each other are one (the marks of a scatter of shapes,
# the glyphs of one formula drawn as outlines): MIN_DRAWING_PT.
LEFTOVER_REACH = 24.0
HAND_MEET = 0.75  # pt

# A row is a label of a figure (`_label_owners`) when it is at most LABEL_SHORT characters
# and within LABEL_REACH x its font size of the figure's nearest stroke or of a label the
# figure holds already: the one reach. A turned row (an axis title) is never a line of a
# paragraph, and stands further off (LABEL_TURNED times as far) and may be longer.
LABEL_SHORT = 25
LABEL_SHORT_TURNED = 40
LABEL_REACH = 1.0
LABEL_TURNED = 2.5

_PROSE_TWO_LETTER = frozenset(
    (
        *("is", "so", "if", "to", "of", "in", "at", "on", "by", "as", "or", "an", "be"),
        *("we", "it", "no", "do", "up", "us"),
    )
)


def _prose_spans(row: _Row, info: _CharInfo) -> list[tuple[int, int, str]]:
    """Words that read as English: 3+ plain letters (not sin/log/...) or "is", "if", ...
    as (first character, end, lower-case word)."""
    found: list[tuple[int, int, str]] = []
    i = 0
    chars = row.chars
    while i < len(chars):
        if info.classes[i] != "L" or info.script[i]:
            i += 1
            continue
        j = i
        letters = 0
        while True:
            while j < len(chars) and info.classes[j] == "L" and not info.script[j]:
                letters += 1
                j += 1
            after = _limit_end(chars, info.classes, info.script, j)
            if after == j:
                break
            j = after  # a limit set under the word ("argmin") is read inside it
        word = "".join(chars[t].c for t in range(i, j) if not info.script[t]).lower()
        variables = letters > 1 and info.words[i] == 1  # italic maths letters, see _word_lengths
        if not variables and (
            (len(word) >= 3 and word not in _MATH_WORDS) or word in _PROSE_TWO_LETTER
        ):
            found.append((i, j, word))
        i = j
    return found


def _prose_words(row: _Row, info: _CharInfo) -> int:
    return len(_prose_spans(row, info))


def _raster_figures(
    page: pymupdf.Page, page_rect: pymupdf.Rect, small: list[_Figure] | None = None
) -> list[_Figure]:
    """Raster images as figures. Decoration is decided by the size drawn, not the pixels
    (a 21-pixel QR code drawn an inch wide is content): an image drawn under MIN_IMAGE_PT
    on a side is a bullet, a spacer or a rule; one with fewer than MIN_IMAGE_PX pixels on
    a side is decoration only when it is plain (`_plain_image`: a transparent spacer, a
    one-colour block). A picture covering the page is the page's own (a background, a
    scan): never a figure; whether the page keeps its image is `_page_picture`'s. Those
    drawn under MIN_IMAGE_PT but over equation_images.MIN_SIDE_PT on both sides are added
    to `small` when it is given: an equation of a letter or a fraction pasted in a line is
    that small, and only `_equation_pictures` may keep one (inside a line of prose)."""
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
            if small is not None and min(rect.width, rect.height) >= equation_images.MIN_SIDE_PT:
                small.append(_Figure((rect.x0, rect.y0, rect.x1, rect.y1), images=1, xref=xrefs[k]))
            continue  # bullets, spacers, rules drawn as images
        if rect.width * rect.height >= FULL_PAGE_FRACTION * page_area:
            continue  # the page's background or a scan of it: `_page_picture`
        w_px, h_px = int(info.get("width") or 0), int(info.get("height") or 0)
        if min(w_px, h_px) < MIN_IMAGE_PX and _plain_image(page.parent, xrefs[k]):
            continue  # a spacer or a fill drawn as an image
        figures.append(_Figure((rect.x0, rect.y0, rect.x1, rect.y1), images=1, xref=xrefs[k]))
    return figures


def _images_in_budget(page: pymupdf.Page) -> bool:
    """Whether every image the page lists is within the pixel budget (`core.images`), by
    the sizes their PDF objects state: only then may anything hash or decode them."""
    doc, number = _where(page)
    try:
        return all(
            pixel_budget.within_budget(width, height)
            for _, _, width, height, *_ in doc.get_page_images(number, full=True)
        )
    except (RuntimeError, ValueError):
        return False


def _image_infos(page: pymupdf.Page, *, hashes: bool = False) -> list[dict[str, Any]]:
    """`page.get_image_info()`; with `hashes`, each image's `digest` too, but only on a page
    whose images are all within the pixel budget: hashing decodes them (twice a page's
    images, on every call, in PyMuPDF's own `xrefs=True`), so on a page with one over it the
    infos come without digests."""
    if not hashes or not _images_in_budget(page):
        return page.get_image_info()
    memo = _decode_memo(_where(page)[0])
    started = time.perf_counter()
    try:
        return page.get_image_info(hashes=True)
    finally:
        _charge(memo, started)


def _image_xrefs(page: pymupdf.Page, count: int) -> list[int]:
    """The xref of each of the page's images, in `get_image_info` order (0 for an inline
    image, or when the lists disagree). Images are told apart by a digest of their pixels
    (kept per image for the document, not made again for every page) when the page's images
    are all within the pixel budget; else by their stated size, an image whose size another
    listed image shares being 0."""
    doc, number = _where(page)
    infos = page.get_image_info()
    if len(infos) != count:
        return [0] * count
    listed = doc.get_page_images(number)
    if _images_in_budget(page):
        memo = _decode_memo(doc)
        started = time.perf_counter()
        try:
            hashed = page.get_image_info(hashes=True)
            by_digest: dict[bytes, int] = {}
            for item in listed:
                xref = item[0]
                if xref not in memo.digests:
                    memo.digests[xref] = pymupdf.Pixmap(doc, xref).digest
                by_digest[memo.digests[xref]] = xref
        finally:
            _charge(memo, started)
        xrefs = [by_digest.get(info.get("digest"), 0) for info in hashed]
    else:
        by_size: dict[tuple[int, int], list[int]] = {}
        for item in listed:
            by_size.setdefault((item[2], item[3]), []).append(item[0])
        xrefs = []
        for info in infos:
            same = by_size.get((info.get("width"), info.get("height")), [])
            xrefs.append(same[0] if len(same) == 1 else 0)
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


class _DecodeMemo:
    """What `_plain_image` has answered for one open document: the answer per image (a
    picture a template repeats on every page is decoded once) and the seconds all its
    decodes took, which `images.DECODE_BUDGET_S` bounds for the job."""

    def __init__(self) -> None:
        self.answers: dict[int, bool] = {}
        self.digests: dict[int, bytes] = {}  # a digest of each image's pixels, by xref
        self.spent_s = 0.0


def _charge(memo: _DecodeMemo, started: float) -> None:
    """Add the time since `started` to the document's decode time; past the job's budget
    the layout stops (`image_too_large`)."""
    memo.spent_s += time.perf_counter() - started
    if memo.spent_s > pixel_budget.DECODE_BUDGET_S:
        raise LayoutError("image_too_large", "the pictures took too long to read")


def _decode_memo(doc: pymupdf.Document) -> _DecodeMemo:
    memo = getattr(doc, "_rs_decode_memo", None)
    if memo is None:
        memo = doc._rs_decode_memo = _DecodeMemo()  # ty: ignore[unresolved-attribute]
    return memo


def _where(page: pymupdf.Page) -> tuple[pymupdf.Document, int]:
    """The document `page` was read from and its number there. PyMuPDF types
    `Page.parent` and `Page.number` as optional; a page of an open document has both."""
    return cast(pymupdf.Document, page.parent), cast(int, page.number)


def _image_size(doc: pymupdf.Document, xref: int) -> tuple[int, int]:
    """Width and height in pixels as the image's PDF object states them, read without
    decoding it ((0, 0) when it does not say)."""
    try:
        width, height = (doc.xref_get_key(xref, key)[1] for key in ("Width", "Height"))
        return int(width), int(height)
    except (RuntimeError, ValueError, IndexError, TypeError):
        return 0, 0


def _plain_image(doc: pymupdf.Document | None, xref: int) -> bool:
    """The image shows no picture: nothing in it is seen (a transparent spacer), or it is
    seen whole in one colour or a smooth gradient (a wash, a template's background). A
    picture with transparent parts (ink on a transparent overlay, a stencil mask's
    shapes) is not plain, and neither is an image that cannot be read (an inline one) or
    that is over the pixel budget (`core.images`): that one is not decoded at all, so a
    small file holding a huge canvas costs nothing here. The answer is kept per image for
    the document; the decodes of one job together may take `DECODE_BUDGET_S` seconds, then
    the layout stops with `image_too_large`."""
    if doc is None or xref <= 0:
        return False
    memo = _decode_memo(doc)
    if xref not in memo.answers:
        started = time.perf_counter()
        try:
            memo.answers[xref] = _decide_plain(doc, xref)
        finally:
            _charge(memo, started)
    return memo.answers[xref]


def _decide_plain(doc: pymupdf.Document, xref: int) -> bool:
    """`_plain_image` for an image not seen before."""
    mask_xref = 0
    try:
        key = doc.xref_get_key(xref, "SMask")
        if key[0] == "xref":
            mask_xref = int(key[1].split()[0])
    except (RuntimeError, ValueError, IndexError):
        return False
    for wanted in (xref, mask_xref):
        if wanted and not pixel_budget.within_budget(*_image_size(doc, wanted)):
            return False  # not decoded: too many pixels, or a size the object does not give
    try:
        pix = pymupdf.Pixmap(doc, xref)
        mask = pymupdf.Pixmap(doc, mask_xref) if mask_xref else None
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


def _cluster_scan(boxes: list[BBox], gap: float) -> list[list[int]]:
    """`_cluster` by comparing each box with every later one in reach on the x axis (the
    reference the banded sweep must match; used for boxes with a non-finite edge)."""
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


def _cluster(boxes: list[BBox], gap: float) -> list[list[int]]:
    """Groups of box indices whose boxes come within `gap` of each other.

    The groups are the connected parts of the graph that joins two boxes when they are
    within `gap` of each other in both directions, each in ascending order of index and
    the groups in order of their first index. A sweep from left to right looks, for each
    box, only at the earlier boxes still in reach on the x axis and in the horizontal
    bands its extent (grown by `gap`) touches, so a page of tens of thousands of small
    pen strokes costs a few comparisons per box, not a pass over every box in its column.
    """
    if not all(math.isfinite(v) for box in boxes for v in box):
        return _cluster_scan(boxes, gap)  # no bands for a box with no place
    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    band = max(gap, CLUSTER_BAND_PT)
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    bands: dict[int, list[int]] = {}  # band number -> earlier boxes touching it
    tall: list[int] = []  # earlier boxes spanning more than CLUSTER_MAX_BANDS bands
    for b in order:
        bx0, by0, _, by1 = boxes[b]
        low, high = min(by0, by1), max(by0, by1)
        first, last = math.floor((low - gap) / band), math.floor((high + gap) / band)
        if last - first + 1 > CLUSTER_MAX_BANDS:
            lists = [*bands.values(), tall]
        else:
            lists = [tall]
            lists += [bands[n] for n in range(first, last + 1) if n in bands]
        for held in lists:
            kept = 0
            for a in held:
                _, ay0, ax1, ay1 = boxes[a]
                if ax1 + gap < bx0:
                    continue  # out of reach now, and of every later box (they start further right)
                held[kept] = a
                kept += 1
                if by0 <= ay1 + gap and ay0 <= by1 + gap:
                    root = find(a)
                    if root != b:  # `b` is a root here: no box has joined it yet
                        parent[root] = b
            del held[kept:]
        own_first, own_last = math.floor(low / band), math.floor(high / band)
        if own_last - own_first + 1 > CLUSTER_MAX_BANDS:
            tall.append(b)
        else:
            for n in range(own_first, own_last + 1):
                bands.setdefault(n, []).append(b)
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
    document: _Document,
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
        # A ruler (a timeline's line and ticks) is thin by nature: long enough, it is a figure
        # with its labels, not a small drawing that a paragraph's block swallows (WS6.15). Its
        # ticks stand out of the line: a line alone (a table's header rule) is a rule.
        thin_ruler = ruler and max(width, height) >= 4 * MIN_DRAWING_PT and min(width, height) > 1.0
        if not thin_ruler and (
            width < MIN_DRAWING_PT or height < MIN_DRAWING_PT or width * height < 1200
        ):
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
        # A filled shape that holds a line of text is shading behind it, and a filled strip
        # along a whole side of the cluster is a side of its frame (a shaded contents box
        # drawn as a frame of four strips and one rectangle per entry): neither is a bar.
        bars = [
            p
            for p in members
            if p.filled
            and p.bbox[3] - p.bbox[1] >= 4
            and p.bbox[2] - p.bbox[0] <= 0.5 * width
            and not any(_centre_inside(r.bbox, p.bbox) for r in inside)
            and not _frame_side(p.bbox, box)
        ]
        if fills_page:
            if not page_sized:
                if left_out is not None:
                    left_out.append(box)
                continue
            # A plotted line or bars on axes (or on a ruler: an axis with ticks) with
            # evidence of a plot is a plot, not a decorated border, with one curve and
            # with running text inside (which stays in the reading flow: `_label_owners`).
            plot = (curved or len(bars) >= 2) and any(p.plot or p.ruler for p in members)
            running = ctx is not None and bool(_running_text(inside, ctx, document=document))
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
                    document=document,
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
            running = ctx is not None and bool(_running_text(inside, ctx, document=document))
            if signs != "drawing" and (signs is None or running):
                if not _round_text(members, inside) and left_out is not None:
                    left_out.append(box)
                continue
        if rule_only:  # a Fischer projection's bonds: the atom groups at their ends too
            box = _union([box, *(end for p in members for end in _bond_ends(p, index))])
        figures.append(_Figure(box, paths=len(members), members=list(members)))
    # Small drawings just outside a figure (axis titles and tick labels drawn as outlines,
    # a legend's markers a little apart, the force arrows round a free-body diagram's box)
    # are part of it, within LEFTOVER_REACH as `_leftover_drawings` groups them: left out,
    # its crop missed them.
    for box, members in small:
        for figure in figures:
            if _within(box, figure.bbox, LEFTOVER_REACH):
                figure.bbox = figure.drawing = _union([figure.drawing, box])
                figure.paths += len(members)
                figure.members += members
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
            near.bbox = near.drawing = _union([near.drawing, path.bbox])
            near.paths += 1
            near.members.append(path)
            grew = True
        marks = rest


MAX_DIAGRAM_BOXES = 200  # more box outlines in one cluster are a grid of cells, not a diagram
ORIGIN_REACH = 4.0  # pt: a plotted line that starts this close to its axes' corner starts there


def _answer_fields(paths: Sequence[_Path], rows: Sequence[_Row]) -> None:
    """Rulers whose ticks stand on one side of the line a letter cell apart (`_Path.comb`)
    with no short row by their ticks (a tick label) are an answer field's cells (a comb
    to write a candidate number or a name in): no ruler, so rules alone, not a drawing
    (2026-10-13: an exam cover's two comb fields became figures and decisions). A label
    beside the field's start ("Candidate number") is the field's own, not a tick's."""
    combs = [p for p in paths if p.comb]
    if not combs:
        return
    labels = [r for r in rows if not r.vertical and len(r.clean) <= LABEL_SHORT]
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
    short row (a tick label, an axis title; LABEL_SHORT) just outside one of its lines
    (LABEL_REACH x its size), on the side away from the other line. `rows` are the
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
    labels = [r for r in rows if len(r.clean) <= LABEL_SHORT and not CAPTION.match(r.clean)]
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
        reach = LABEL_REACH * row.size
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


def _frame_side(bbox: BBox, box: BBox) -> bool:
    """A strip along one whole side of `box`: as tall as it at its left or right edge, or
    as wide as it at its top or bottom edge."""
    x0, y0, x1, y1 = bbox
    tall = y1 - y0 >= 0.8 * (box[3] - box[1]) and (x0 <= box[0] + 1 or x1 >= box[2] - 1)
    wide = x1 - x0 >= 0.8 * (box[2] - box[0]) and (y0 <= box[1] + 1 or y1 >= box[3] - 1)
    return tall or wide


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
        parts = [p for f in members for p in f.members]
        merged.append(_Figure(box, images=images, paths=paths, members=parts))
    return merged


def _mostly_in(box: BBox, outer: BBox) -> bool:
    """At least 60 % of `box` lies inside `outer`."""
    area = max(1e-6, _area(box))
    inter = max(0.0, min(box[2], outer[2]) - max(box[0], outer[0])) * max(
        0.0, min(box[3], outer[3]) - max(box[1], outer[1])
    )
    return inter >= 0.6 * area


def _backdrop(
    figure: _Figure,
    rows: Sequence[_Row],
    ctx: _Context,
    doc: pymupdf.Document | None = None,
    *,
    document: _Document,
) -> bool:
    """Is the "figure" no figure: a plain picture under running text, or a box round code?

    A picture with text on it (a slide's photo under its title, a caption box over a photo)
    stays a figure: it was dropped as a background, with nothing to say so (the silent-loss
    sweep, 2026-10-12). Only a plain one (`_plain_image`: a coloured panel or a wash drawn
    as a picture) under running text is a background: it is dropped, and its text keeps its
    place in the outline. A box drawn round program code, with no label by it, is the
    code's frame. A picture covering the page is the page's own (`_raster_figures`)."""
    inside = [
        r
        for r in rows
        if not r.absorbed and not CAPTION.match(r.clean) and _mostly_in(r.bbox, figure.drawing)
    ]
    running = _running_text(inside, ctx, document=document)
    if running and figure.images == 1 and not figure.paths and _plain_image(doc, figure.xref):
        return True
    if figure.images or not inside or not all(r.code and id(r) in running for r in inside):
        return False
    return not any(  # a short row by the box is a label: the drawing is a figure
        not r.absorbed
        and r not in inside
        and _short_label(r)
        and _stroke_gap(r.bbox, figure) <= LABEL_REACH * r.size
        for r in rows
    )


# The typical gap between the words of a row of tick or category labels, in font sizes:
# the words of running text are a quarter of a font size apart, half in a justified line.
LABEL_SPREAD = 0.8
LABEL_SPREAD_MIN = 0.4  # ... and no gap of such a row is narrower than this
LABEL_APART = 2.0  # font sizes: words this far apart are separate labels on one line


def _spread_row(row: _Row) -> bool:
    """A row of two or more words set far apart (median gap LABEL_SPREAD font sizes or
    more, none under LABEL_SPREAD_MIN): the tick or category labels along an axis
    ("2000 2005 2010 2015 2020"), never a line of prose. Two long category names may
    stand closer than the rest, so the typical gap decides."""
    gaps = []
    last = None
    space = False
    for c in row.chars:
        if c.blank:
            space = True
            continue
        if last is not None and (space or c.x0 - last.x1 > 0.3 * row.size):
            gaps.append(c.x0 - last.x1)  # between two words: a space, or a clear gap
        last = c
        space = False
    return (
        bool(gaps)
        and statistics.median(gaps) >= LABEL_SPREAD * row.size
        and min(gaps) >= LABEL_SPREAD_MIN * row.size
    )


def _label_groups(row: _Row) -> list[int]:
    """The characters of each group of a row cut where two words stand LABEL_APART font
    sizes apart or more (a justified line's spaces are half a font size at most)."""
    counts: list[int] = []
    last = None
    for c in row.chars:
        if c.blank:
            if counts:
                counts[-1] += 1  # the spaces inside a group count: it is a stretch of text
            continue
        if last is None or c.x0 - last.x1 >= LABEL_APART * row.size:
            counts.append(0)
        counts[-1] += 1
        last = c
    return counts


def _short_label(row: _Row) -> bool:
    """Short enough to be a label: LABEL_SHORT characters or fewer, the tick or category
    labels of an axis set in one row (`_spread_row`: each word of it is short), or several
    short labels far apart on one line (a timeline's events: `_label_groups`)."""
    if len(row.clean) <= (LABEL_SHORT_TURNED if row.vertical else LABEL_SHORT) or _spread_row(row):
        return True
    groups = _label_groups(row)
    return len(groups) >= 2 and max(groups) <= LABEL_SHORT


_ITEM_START = re.compile(r"^\(?[A-Za-z0-9]{1,2}[.)]\s+[A-Za-z]{3}")


def _starts_like_item(row: _Row) -> bool:
    """The row opens with a list or section marker and a word ("5. Blocks and springs",
    "(b) Find the speed"): the title or item of the text, not a label. A decimal ("3.7 ORCID
    iDs") has no space after its point; a part's own tag ("(a)", "R1 = 4 ohm") has no word."""
    return _ITEM_START.match(row.clean) is not None


def _edge_gap(edge: tuple[float, float, float, float], box: BBox) -> float:
    """How far a straight stroke lies from a box (0 when it crosses or touches it)."""
    x0, y0, x1, y1 = edge
    dx, dy = x1 - x0, y1 - y0
    enter, leave = 0.0, 1.0
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0), (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if p == 0:
            if q < 0:
                enter, leave = 1.0, 0.0  # parallel to a side and outside it
        elif p < 0:
            enter = max(enter, q / p)
        else:
            leave = min(leave, q / p)
    if enter <= leave:
        return 0.0
    length = dx * dx + dy * dy

    def to_stroke(x: float, y: float) -> float:
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length))
        return math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))

    def to_box(x: float, y: float) -> float:
        return math.hypot(max(box[0] - x, 0.0, x - box[2]), max(box[1] - y, 0.0, y - box[3]))

    return min(
        to_stroke(box[0], box[1]),
        to_stroke(box[2], box[1]),
        to_stroke(box[0], box[3]),
        to_stroke(box[2], box[3]),
        to_box(x0, y0),
        to_box(x1, y1),
    )


def _box_gap(box: BBox, other: BBox) -> float:
    """The straight distance between two boxes (0 when they touch or overlap)."""
    return math.hypot(
        max(0.0, other[0] - box[2], box[0] - other[2]),
        max(0.0, other[1] - box[3], box[1] - other[3]),
    )


def _stroke_gap(box: BBox, figure: _Figure, labels: Sequence[BBox] = ()) -> float:
    """How far `box` lies from the nearest drawn stroke of the figure, or from one of its
    `labels` already found: not from the figure's box, which reaches over blank ground
    where a diagonal or an L leaves the corner empty. A path drawn by a few straight lines
    counts by those lines (`_Path.edges`), any other by its box. A picture has no stroke:
    the text beside it is its own (a caption, a paragraph), and only text on it is a label
    (infinite for a figure that is a picture alone)."""
    if not figure.members:
        return math.inf
    best = min((_box_gap(box, b) for b in labels), default=math.inf)
    for path in figure.members:
        gap = _box_gap(box, path.bbox)
        if gap >= best:
            continue
        if path.edges:
            gap = min(_edge_gap(edge, box) for edge in path.edges)
        best = min(best, gap)
        if best <= 0:
            break
    return best


def _label_owners(
    figures: Sequence[_Figure], rows: Sequence[_Row], ctx: _Context, *, document: _Document
) -> dict[int, _Figure]:
    """The one rule for what a figure's labels are (SW40): the figure each row labels, by
    the id of the row.

    A row is a label of a figure when it lies within LABEL_REACH x its font size of the
    figure's nearest drawn stroke (`_stroke_gap`), or when most of it is inside the
    drawing's box (an inside row of any length: the text in a flowchart's boxes), and it is
    no line of running text (`_running_text`: among the rows inside the drawing for an
    inside row, among the page's for another: a paragraph that passes by a figure, a
    title). Outside the drawing it is also short (`_short_label`), no list item or numbered
    title (`_starts_like_item`) and no code (a program listing in a box). Any kind of row
    counts, maths and rotated ones too: a part's name on a circuit, an axis title,
    "R1 = 4 ohm". A caption never does (`_attach_captions`).
    Found again and again from the figure and the labels it holds until nothing more is,
    since an axis title stands a line beyond the tick labels; a row two figures could
    claim goes to the nearer, by its distance to their strokes."""
    owner: dict[int, _Figure] = {}
    pending = [r for r in rows if not r.absorbed and not CAPTION.match(r.clean)]
    if not figures or not pending:
        return owner
    on_page: set[int] | None = None  # the page's running text, found when a row is near
    in_drawing: dict[int, set[int]] = {}  # and that of the rows inside each drawing
    held: dict[int, list[BBox]] = {id(f): [] for f in figures}  # the labels found so far
    while pending:
        found: list[tuple[_Row, _Figure]] = []
        left = []
        for row in pending:
            reach = LABEL_REACH * row.size * (LABEL_TURNED if row.vertical else 1.0)
            best: tuple[float, _Figure, bool] | None = None
            for figure in figures:
                inside = _mostly_in(row.bbox, figure.drawing)
                if not inside and not (
                    _within(row.bbox, figure.drawing, reach)
                    or any(_within(row.bbox, b, reach) for b in held[id(figure)])
                ):
                    continue
                gap = _stroke_gap(row.bbox, figure, held[id(figure)])
                if (inside or gap <= reach) and (best is None or gap < best[0]):
                    best = (gap, figure, inside)
            if best is None:
                left.append(row)
                continue
            _, figure, inside = best
            if inside:  # a line of a paragraph among the rows inside the drawing
                if id(figure) not in in_drawing:
                    in_drawing[id(figure)] = _running_text(
                        [r for r in rows if not r.absorbed and _mostly_in(r.bbox, figure.drawing)],
                        ctx,
                        document=document,
                    )
                running = in_drawing[id(figure)]
            else:  # ... or among the page's rows, where one runs on from a line above
                if on_page is None:
                    on_page = _running_text(
                        [r for r in rows if not r.absorbed], ctx, document=document
                    )
                running = on_page
            if id(row) in running or not (
                inside
                or (
                    _short_label(row)
                    and not _starts_like_item(row)
                    and not (row.code and len(row.ink) >= CODE_MIN_GLYPHS)
                )
            ):
                continue  # never a label, wherever it lies
            found.append((row, figure))
        for row, figure in found:
            owner[id(row)] = figure
            held[id(figure)].append(row.bbox)
        pending = left if found else []
    return owner


def _claim_labels(
    figures: Sequence[_Figure], rows: Sequence[_Row], ctx: _Context, *, document: _Document
) -> None:
    """Give each figure its labels (`_label_owners`) and grow its box, and so the picture
    cut for the figure step, to hold them. Before, a chart's tick labels and category
    names stayed in the body as stray paragraphs and its crop had none of them, so a model
    estimated its values (F-399, accuracy round 2, 2026-11-11), and a circuit's part names,
    an axis title or a part's name on a diagram became equations and one-letter paragraphs
    (F-549, F-248)."""
    owner = _label_owners(figures, rows, ctx, document=document)
    for row in rows:
        if figure := owner.get(id(row)):
            row.absorbed = True
            figure.labels.append(row)
    for figure in figures:
        if figure.labels:
            figure.bbox = _union([figure.drawing, *(r.bbox for r in figure.labels)])


PANEL_GAP = 3.5  # body sizes: furthest apart two panels of one figure stand
PANEL_MARK_REACH = 2.5  # body sizes: furthest from a figure's box a one- or two-letter mark may be
PANEL_MARK_CHARS = 2  # a panel mark ("A", "b", "3") is this many characters or fewer


def _join_panels(
    figures: list[_Figure],
    captions: dict[str, BBox],
    rows: Sequence[_Row],
    ctx: _Context,
    *,
    document: _Document,
) -> list[_Figure]:
    """The panels of one figure are one figure ("Panels of one figure" in docs/layout.md).

    A figure that holds a caption (`captions`: the box of each caption text a figure took)
    takes the figures without one that stand beside it, above it or below it, within PANEL_GAP
    body sizes of the panels it holds already, on its caption's side of the caption and under
    its width, until none does. Only the caption's nearest figure had it, so the other panels
    were figures with a picture and no words: a reader got one description for each of them
    and the caption on one. A line of text among the panels (a paragraph that runs between two
    pictures) ends the join. The rows among the panels are then claimed as labels
    (`_claim_labels`), and a row of one or two characters within PANEL_MARK_REACH body sizes
    of any figure's box ("A", "b", "3", "n") is a label of it too (panel letters and the names
    of a drawing's parts stand at a corner, beyond the reach of `_label_owners`), unless it is
    a word ("to", "of") or the start of a list item."""
    joined: set[int] = set()
    reach = PANEL_GAP * ctx.body
    for host in figures:
        box = captions.get(host.caption) if host.caption is not None else None
        if box is None or host.equation or id(host) in joined:
            continue
        below = box[1] >= host.bbox[3] - 2.0  # the caption stands under the figure
        group = [host]
        union = host.bbox
        grew = True
        while grew:
            grew = False
            for other in figures:
                if other is host or other in group or other.caption is not None or other.equation:
                    continue
                if id(other) in joined:
                    continue
                dx = max(0.0, other.bbox[0] - union[2], union[0] - other.bbox[2])
                dy = max(0.0, other.bbox[1] - union[3], union[1] - other.bbox[3])
                if dx > reach or dy > reach or (dx > 0 and dy > 0):
                    continue  # too far, or only at a corner: not a row or a column of panels
                middle = (other.bbox[0] + other.bbox[2]) / 2
                if not box[0] - reach <= middle <= box[2] + reach:
                    continue  # beside the caption's column, not under it
                if (other.bbox[3] > box[1] + 2.0) if below else (other.bbox[1] < box[3] - 2.0):
                    continue  # on the other side of the caption
                grown = _union([union, other.bbox])
                if any(
                    not r.absorbed
                    and not CAPTION.match(r.clean)
                    and not _short_label(r)
                    and _mostly_in(r.bbox, grown)
                    and not _mostly_in(r.bbox, union)
                    and not _mostly_in(r.bbox, other.bbox)
                    for r in rows
                ):
                    continue  # text between the panels
                group.append(other)
                union = grown
                grew = True
        for other in group[1:]:
            joined.add(id(other))
            host.images += other.images
            host.paths += other.paths
            host.members.extend(other.members)
            host.labels.extend(other.labels)
        if len(group) > 1:
            host.xref = 0
            host.drawing = _union([f.drawing for f in group])
            host.bbox = _union([f.bbox for f in group])
    kept = [f for f in figures if id(f) not in joined]
    if joined:
        _claim_labels(kept, rows, ctx, document=document)  # the rows among the panels
    for figure in kept:
        if figure.equation:
            continue
        near = _pad(figure.bbox, PANEL_MARK_REACH * ctx.body)
        for row in rows:
            if (
                not row.absorbed
                and not row.vertical
                and len(row.clean) <= PANEL_MARK_CHARS
                and row.clean.lower() not in _PROSE_TWO_LETTER
                and not _starts_like_item(row)
                and _inside(row.bbox, near)
            ):
                row.absorbed = True
                figure.labels.append(row)
        if figure.labels:
            figure.bbox = _union([figure.bbox, *(r.bbox for r in figure.labels)])
    return kept


FRAGMENT_CHARS = 3  # a text row of this many characters or fewer is a fragment
MIN_FRAGMENT_ROWS = 12  # fragments in a region that is a drawing
FRAGMENT_REACH = 3.0  # body sizes between the fragments of one region
FRAGMENT_STROKES = 0.5  # short strokes per fragment row in a drawing (bonds, leads, arrows)
FRAGMENT_STROKE_MIN_EM = 0.5  # a dot (a contents leader) is no stroke: its longer side is longer
FRAGMENT_STROKE_EM = 6.0  # a stroke is short when its longer side is at most this many body sizes


def _fragment_figures(
    rows: Sequence[_Row],
    strokes: Sequence[_Path],
    held: Sequence[BBox],
    ctx: _Context,
    *,
    document: _Document,
) -> list[_Figure]:
    """Regions of the page that are a drawing made of text fragments, as figures.

    The labels of a structure, a circuit or a diagram that no drawing cluster took (its
    strokes are too short and plain to be a figure) were written as dozens of loose
    paragraphs and one-symbol equations, each its own decision (WS2.9a). A region is a group
    of at least `MIN_FRAGMENT_ROWS` rows of `FRAGMENT_CHARS` characters or fewer, within
    `FRAGMENT_REACH` body sizes of each other, outside the figures and tables already found
    (`held`), when the rows in its box (grown to the short strokes among them, at least
    `FRAGMENT_STROKES` per fragment: a derivation has a fraction bar or two, a structure a
    bond to each atom, and a contents page's leader dots are not strokes) have a median
    length under four characters and none of them holds three words. Its rows become the
    figure's labels and are written as text no more."""
    fragments = [
        r
        for r in rows
        if not r.absorbed
        and not r.vertical
        and not r.code
        and len(r.clean) <= FRAGMENT_CHARS
        and not any(_centre_inside(r.bbox, box) for box in held)
    ]
    short = [
        p
        for p in strokes
        if FRAGMENT_STROKE_MIN_EM * ctx.body
        <= max(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1])
        <= FRAGMENT_STROKE_EM * ctx.body
    ]
    found: list[_Figure] = []
    reach = FRAGMENT_REACH * ctx.body
    for group in _cluster([r.bbox for r in fragments], reach):
        if len(group) < MIN_FRAGMENT_ROWS:
            continue
        box = _union(fragments[i].bbox for i in group)
        near = [p for p in short if _centre_inside(p.bbox, _pad(box, reach))]
        if len(near) < FRAGMENT_STROKES * len(group):
            continue
        box = _union([box, *(p.bbox for p in near)])
        inside = [
            r
            for r in rows
            if not r.absorbed
            and not r.vertical
            and _centre_inside(r.bbox, box)
            and not any(_centre_inside(r.bbox, other) for other in held)
        ]
        if statistics.median(len(r.clean) for r in inside) >= 4 or any(
            _prose_words(r, _CharInfo.of(r, document=document)) >= 3 for r in inside
        ):
            continue
        for row in inside:
            row.absorbed = True
        found.append(_Figure(box, paths=len(near), labels=inside, fragments=True, members=near))
        held = [*held, box]
    return found


def _running_text(rows: list[_Row], ctx: _Context, *, document: _Document) -> set[int]:
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
        words = _prose_words(row, _CharInfo.of(row, document=document))
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
                (id(above) in lines or CAPTION.match(above.clean))  # a caption's tail is its own
                and above.column == row.column
                and _continues(above, row, _Run([above], "text", True), ctx)
            ):
                lines.add(id(row))
                break
    return found | lines


class _RightEdges:
    """Where the lines of a page's text columns end, for telling a line that runs full from
    one that stops short: per column and type size, the right edge that a quarter of the
    lines reach or pass (the last line of a paragraph, a heading or a label falls short of
    it; a line in the middle of a paragraph does not)."""

    def __init__(self, rows: Sequence[_Row]) -> None:
        self._rows = [r for r in rows if r.kind == "text" and not r.vertical]
        self._seen: dict[tuple[str, int], tuple[float, float]] = {}

    def _ends(self, row: _Row) -> tuple[float, float]:
        """The right edge for `row`, and where the longest lines end (19 in 20 end by there)."""
        key = (row.column, round(row.size))
        if key not in self._seen:
            near = max(1.0, 0.12 * row.size)
            ends = sorted(
                r.x1
                for r in self._rows
                if r.column == row.column and abs(r.size - row.size) <= near
            ) or [row.x1]
            last = len(ends) - 1
            self._seen[key] = (
                ends[min(last, 3 * len(ends) // 4)],
                ends[min(last, 19 * len(ends) // 20)],
            )
        return self._seen[key]

    def of(self, row: _Row) -> float:
        return self._ends(row)[0]

    def firm(self, row: _Row) -> bool:
        """Is that edge a margin the lines are set to: the longest lines end within an em of
        it? Where a page holds little but short lines of uneven length (an algorithm and its
        title), the edge is only where the longer ones happen to end."""
        edge, longest = self._ends(row)
        return longest - edge <= row.size


INDENT_REACH = 4  # em: how far left of an indented first line the paragraph's next line may start
_REFERENCE_NUMBER = re.compile(r"\[\d{1,3}\]")
# The line that opens an abstract's keywords ("Keywords:", "Key words -", "Index Terms -").
_KEYWORDS_LINE = re.compile(r"(?:key\s?words?|index\s+terms)\s*[:–—-]", re.IGNORECASE)  # noqa: RUF001
# A theorem's label: "Definition 2.", "Theorem 1.1 (Name).", "Example 5:", "Proof.", or a
# numbered one with no stop ("Remark 5.1 It ..."), which `_starts_block` takes in bold or italic.
_THEOREM_LEAD = re.compile(
    r"(?:Definition|Theorem|Lemma|Proposition|Corollary|Claim|Conjecture|Remark|Example|Proof)"
    r"(?P<number>\s+\d+(?:\.\d+)*)?(?:\s*\([^)]{0,60}\))?(?P<stop>[.:](?!\d))?"
)
_LINE_NUMBER = re.compile(r"(\d{1,3})[:.]?\s+\S")
# The signs of a note: * and the asterisk operator, dagger, double dagger, section, pilcrow.
_NOTE_SIGNS = frozenset("*\u2217\u2020\u2021\u00a7\u00b6")
NOTE_BODY_RATIO = 0.92  # a note, an affiliation: set in smaller type than the body text
_MID_SENTENCE = frozenset(",;(-\u2013\u2014")


def _starts_entry(prev: _Row, row: _Row, run: _Run, edges: _RightEdges) -> bool:
    """Does `row` start an entry of a glossary or a list of terms: a lead term (`_lead_end`)
    at the left edge of the paragraph `run`, after a line that does not end in that style. (An
    italic title that starts a line inside an entry of a bibliography is indented with it; an
    italic "et al." after a line that runs full and stops mid-sentence is part of it.)"""
    found = _lead_end(row)
    if found is None or row.marker is not None or row.x0 > run.box[0] + 0.5 * row.size:
        return False
    if prev.clean[-1:] not in '.:;?!)”"' and prev.x1 >= edges.of(prev) - 1.5 * prev.size:
        return False
    last = next((c for c in reversed(prev.chars) if not c.blank), None)
    return last is not None and not found[1](last)


def _runs_on(prev: _Row, edges: _RightEdges | None) -> bool:
    """Does the sentence of `prev` go on in the next line: it ends in a lower-case letter, a
    comma or a dash, and, where the page's line ends are known, runs to the right edge."""
    end = prev.clean[-1:]
    if not (end.islower() or end in _MID_SENTENCE):
        return False
    return edges is None or prev.x1 >= edges.of(prev) - 3 * prev.size


# The words that start a line of an algorithm in lower case.
_STEP_WORDS = frozenset(
    {"if", "else", "elif", "for", "foreach", "while", "do", "repeat", "until", "end", "endif"}
    | {"endfor", "endwhile", "return", "then", "break", "continue", "function", "procedure"}
)


def _margin_number(prev: _Row, row: _Row, number: re.Match[str]) -> bool:
    """Is the number that starts `row` a line number of the margin, not a step: set apart
    from its line (in smaller type, or more than 1.2 em from the text), and the sentence of
    `prev` goes on in the words after it (they start in lower case, and are no step word)?"""
    chars = [c for c in row.chars if not c.blank]
    count = 0
    while count < len(chars) and (chars[count].c.isdigit() or chars[count].c in ":."):
        count += 1
    if not 0 < count < len(chars):
        return False
    apart = row.is_script(chars[0]) or chars[count].x0 - chars[count - 1].x1 > 1.2 * row.size
    word = re.match(r"[a-z]+", row.clean[number.end() - 1 :])
    end = prev.clean[-1:]
    last = re.search(r"[A-Za-z]+$", prev.clean)
    return (
        apart
        and word is not None
        and word.group(0) not in _STEP_WORDS
        and (end.islower() or end in _MID_SENTENCE - {";", "("})
        and (last is None or last.group(0) not in _STEP_WORDS)
    )


def _styled_lead(prev: _Row, row: _Row) -> bool:
    """The line starts in bold or italic and the line above does not end in that style."""
    first = next((c for c in row.chars if not c.blank), None)
    last = next((c for c in reversed(prev.chars) if not c.blank), None)
    if first is None or last is None:
        return False
    return (first.bold and not last.bold) or (
        first.italic and not first.math and not last.italic and not last.bold
    )


def _starts_block(
    prev: _Row, row: _Row, run: _Run, ctx: _Context, edges: _RightEdges | None = None
) -> bool:
    """Does `row` start a new block rather than go on with `run`, whose last row is `prev`,
    though it sits at a line's distance in the same size and the same column?

    The signals, each of a kind of start that the geometry of a paragraph's lines does not
    show (SW34): an entry of a glossary or a list of terms (a lead term), the "Keywords:"
    line, and with the region tree an entry of an index or an outline (`entries`); a note
    or an affiliation (a raised digit or sign at the line start, in smaller type than the
    body, after a line that does not go on); a theorem, definition, proof or example (its
    label, at the end of a sentence or in another style than the line above; a numbered one
    with no stop only in bold or italic); the next number of a column of numbers after a line
    that ends short of the right edge (the steps of an algorithm, the lines of a listing; not
    the line numbers of the margin of a passage, `_margin_number`). The signals that need the
    page's line ends (`edges`) are not applied without them."""
    text = row.clean
    if ctx.region_tree and entries.starts_entry(prev, row, run):
        return True
    if _KEYWORDS_LINE.match(text) is not None:
        return True
    if edges is not None and _starts_entry(prev, row, run, edges):
        return True
    first = next((c for c in row.chars if not c.blank), None)
    if (
        first is not None
        and (first.c.isdigit() or first.c in _NOTE_SIGNS)
        and row.is_script(first)
        and row.size < NOTE_BODY_RATIO * ctx.body
        and not _runs_on(prev, edges)
    ):
        return True
    label = _THEOREM_LEAD.match(text)
    if label is not None and (
        (label["stop"] and (not _runs_on(prev, edges) or _styled_lead(prev, row)))
        or (label["number"] and _styled_lead(prev, row) and not _runs_on(prev, edges))
    ):
        return True
    if edges is None:
        return False
    number, before = _LINE_NUMBER.match(text), _LINE_NUMBER.match(prev.clean)
    return (
        number is not None
        and before is not None
        and int(number.group(1)) == int(before.group(1)) + 1
        and prev.x1 < edges.of(prev) - 3 * prev.size
        and not _margin_number(prev, row, number)
    )


def _continues(
    prev: _Row, row: _Row, run: _Run, ctx: _Context, edges: _RightEdges | None = None
) -> bool:
    """Is `row` the next line of the paragraph `run`, whose last row is `prev`?

    With `edges` (the text lines of the page), a run of one line that is indented, runs to
    the right edge and is no list item, caption or end of a sentence goes on with a line
    that starts up to `INDENT_REACH` em to its left: the second line of a paragraph whose
    first line is indented like a typewriter's tab (not two centred lines, not the next
    numbered reference). Without `edges` it is rejected."""
    size = prev.size
    if abs(row.size - size) > max(1.0, 0.12 * size):
        return False
    pitch = row.baseline - prev.baseline
    limit = max(1.3 * ctx.pitch * size / ctx.body, 1.25 * size)
    if not 0.5 * size <= pitch <= limit:
        return False
    left, right = run.box[0], run.box[2]
    if row.x0 > right or row.x1 < left:
        return False
    if (
        row.x0 < left - 2 * size
        and not (
            edges is not None
            and len(run.rows) == 1
            and row.x0 >= left - INDENT_REACH * size
            and prev.x1 >= edges.of(prev) - 1.5 * size
            and row.x1 >= prev.x1 - 3 * size
            # two lines centred on one another (author, affiliation) are not an indent
            and abs(prev.x0 + prev.x1 - row.x0 - row.x1) > 0.5 * size
            and row.marker is None
            and not _REFERENCE_NUMBER.match(row.clean)  # "[20] Smith": the next reference
            and prev.marker is None
            and not CAPTION.match(prev.clean)
            and prev.clean[-1:] not in ".:?!"
            # a line that starts with a raised mark starts its own entry (an affiliation, a note)
            and not any(row.is_script(c) for c in row.chars[:1])
        )
    ):
        return False
    if run.rows[0].marker is not None and row.x0 < left - 0.5 * size:
        return False  # a list item goes on at its marker or its text, never further left
    if edges is not None and len(run.rows) == 1 and _lead_end(run.rows[0]) is not None:
        right = max(right, edges.of(prev))  # an entry of one line: its line ends short of the edge
    if row.x0 - run.margin >= 0.8 * size and prev.x1 < right - 1.5 * size:
        return False  # first-line indent after a short line: a new paragraph
    return (
        row.marker is None
        and not CAPTION.match(row.clean)
        and not _starts_block(prev, row, run, ctx, edges)
    )


def _gap(box: BBox, other: BBox) -> float:
    """How far apart two boxes are (0 when they touch or overlap), sideways plus up or down."""
    return max(0.0, other[0] - box[2], box[0] - other[2]) + max(
        0.0, other[1] - box[3], box[1] - other[3]
    )


def _pad(box: BBox, by: float = HAND_MEET) -> BBox:
    return box[0] - by, box[1] - by, box[2] + by, box[3] + by


def _edge_band(path: _Path, page_rect: pymupdf.Rect) -> bool:
    """A filled, straight-edged band along an edge of the page (a slide template's title
    bar, footer bar or side strip): touching an edge, and at most FRAME_EDGE_BAND of the
    page deep; or running the length of the page (PAGE_SIZED_FRACTION of it) within
    FRAME_EDGE_BAND of an edge, a slide's footer bar set in from the edge by a margin: its
    footer text lies on it, and a line of the slide that stands close above it was taken
    as a label of the "figure" it made (the 6.0001 deck, 35 pages)."""
    if not path.filled or path.curved:
        return False
    x0, y0, x1, y1 = path.bbox
    w, h = page_rect.width, page_rect.height
    touches_x = x0 <= 1.0 or x1 >= w - 1.0
    touches_y = y0 <= 1.0 or y1 >= h - 1.0
    long_band = _in_edge_band(path.bbox, page_rect) and (
        x1 - x0 >= PAGE_SIZED_FRACTION * w or y1 - y0 >= PAGE_SIZED_FRACTION * h
    )
    return (
        (touches_y and y1 - y0 <= FRAME_EDGE_BAND * h)
        or (touches_x and x1 - x0 <= FRAME_EDGE_BAND * w)
        or long_band
    )


CODE_MIN_GLYPHS = 12
