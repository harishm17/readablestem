"""One page: from its rows to its blocks (`_page_blocks`), the figures, hand strokes, pen marks
and drawn rules on the page, glyph outlines, footnote marks and the steps that flag half-
fractions and rules.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from collections.abc import Collection, Sequence
from functools import cached_property
from typing import Any, cast

import pymupdf

from readablestem.core.types import BBox, Kind
from readablestem.pipeline import baseline_rules, page_type, say_once, tables
from readablestem.pipeline.layout import regions
from readablestem.pipeline.layout.blocks import (
    _TEXT_BLOCKS,
    EQUATION_IMAGE_REASON,
    SET_OFF_FLAG,
    _attach_captions,
    _classify_run,
    _contents_rows,
    _flag_text,
    _heading_sized,
    _keeps_lines,
    _label_abstracts,
    _leave_out_teacher_tags,
    _merge_chapter_numbers,
    _merge_style_splits,
    _paragraph_pieces,
    _runs,
    _split_options,
)
from readablestem.pipeline.layout.context import _Document, _PageGuard
from readablestem.pipeline.layout.display import (
    _contiguous_regions,
    _equation_regions,
    _mark_case_rows,
    _split_braced_systems,
    _split_exercise_numbers,
    _split_labels,
)
from readablestem.pipeline.layout.drawings import MIN_IMAGE_PT, _resolve_fragments
from readablestem.pipeline.layout.figures import (
    DRAWING_CLUSTER_GAP,
    FIGURE_PART_REACH,
    HAND_MEET,
    LEFTOVER_REACH,
    _backdrop,
    _bond_ends,
    _bonds,
    _bullet_mark,
    _centre_inside,
    _claim_labels,
    _cluster,
    _connected,
    _fragment_figures,
    _label_owners,
    _line_chain,
    _merge_figures,
    _pad,
    _page_furniture,
    _raster_figures,
    _RightEdges,
    _round_page,
    _short_label,
    _vector_figures,
    _within,
)
from readablestem.pipeline.layout.furniture import (
    _box_zone,
    _clip_box,
    _is_page_number,
    _margin_zone,
    _MarginFilter,
    _norm_margin,
    _rotated_key,
)
from readablestem.pipeline.layout.glyphs import _MATH_SYMBOL_FONT, _Char, _union
from readablestem.pipeline.layout.inline_runs import _classify_row, _equation_pictures
from readablestem.pipeline.layout.order import (
    Gutter,
    _find_gutter,
    _reading_order,
    _words_both_sides,
)
from readablestem.pipeline.layout.rows import _merge_baselines
from readablestem.pipeline.layout.shared import (
    _Block,
    _box_key,
    _Context,
    _Display,
    _Figure,
    _Inline,
    _PageBlocks,
    _Path,
    _Row,
    _RowIndex,
)
from readablestem.pipeline.layout.tables import _key_term_lines, _table_blocks, _term_items

MARGIN_ATTACHED = 0.6  # x row height: a row this near the text inward of it goes on from it
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

# Pen evidence of a short straight stroke, too few points for the page type's tests (a
# tick, a cross, a slash: `_hand_strokes`). Each line runs off both axes by more than
# HAND_OBLIQUE of its run (about 5 degrees, as the page type's oblique test); its lines
# are not all one length within HAND_SAME (a printed chevron, a zigzag of equal bonds);
# no end meets another stroke's within HAND_MEET, no line lies alongside another
# stroke's within HAND_PARALLEL (a skeletal formula's bonds and double bonds), and no
# line has the length of another stroke's line to rounding precision
# (page_type.SIZE_TWIN_TOLERANCE: machine drawings repeat sizes, hands never do).
HAND_OBLIQUE = 0.08
HAND_SAME = 0.02
HAND_PARALLEL = math.radians(1.0)
DOT_PT = 4.0  # a filled mark this small on its longer side is a dot

# A pen mark over typed text (`_pen_mark_over_text`): at most this many strokes, reaching
# out of the line they lie on by at least this share of their height. A hand tick, cross
# or slash is drawn bigger than the text; a cancel stroke or an icon set in a line is not.
PEN_MARK_STROKES = 2
PEN_MARK_OUT = 0.25
PEN_MARK_COLOUR = 0.3  # a closed or filled mark differs from the text this much (sRGB)
PEN_FILL_SHARE = 0.5  # a filled mark is a stroke's ribbon when it covers at most this of its box


def _prepare_rows(
    page: pymupdf.Page,
    frags: list[_Row],
    margins: _MarginFilter,
    rules: Sequence[_Path] = (),
    source: say_once.PageSource | None = None,
    *,
    tables: Sequence[BBox] = (),
    document: _Document,
) -> tuple[list[_Row], Gutter, list[_Row]]:
    """The page's rows without its running headers, footers, page numbers and watermarks,
    its column gutter, and the rows so dropped (a figure may claim some back as labels:
    `_figure_labels`). `source` gets what the say-once measure counts: the words of the rows
    kept, and the glyphs of the rows left out as `furniture`."""
    height = page.rect.height
    kept: list[_Row] = []
    dropped: list[_Row] = []
    number = cast(int, page.number)
    gone = {id(f) for f in frags if margins.drops(f, height)}
    placed = {id(f) for f in frags if id(f) not in gone and margins.at_place(f, height, number)}
    placed -= _attached(frags, placed, gone, height)
    out = gone | placed
    standing = [f for f in frags if id(f) not in out]
    for frag in frags:
        if id(frag) in gone and _is_number_row(frag) and _shares_line(frag, standing):
            gone.discard(id(frag))  # a cell of a table, not a page number: it has a line mate
    for frag in frags:
        if id(frag) in gone:
            dropped.append(frag)
        elif id(frag) not in placed:
            kept.append(frag)
    if document.region_tree:  # the page's regions, from the fragments (regions.py)
        _mark_blanks(kept, rules)
        rows, tree, rest = regions.prepare(
            page, kept, dropped, rules, tables=tables, document=document
        )
        if source is not None:
            for row in rows:
                source.add_row(row.chars)
            out = [f for f in frags if id(f) in gone or id(f) in placed]
            source.leave_out("furniture", sum(1 for f in out for c in f.chars if not c.blank))
        return rows, tree, rest
    # Margin lines (an unrepeated header, a centred footer) say nothing about columns.
    body = [f.bbox for f in kept if not f.vertical and _margin_zone(f, height) is None]
    gutter = _find_gutter(body)
    if gutter is None and document.slide_deck:
        # A slide's columns are short (`_find_gutter`, `slide`), so they must also hold words:
        # the brace and the cases of a piecewise function are not two columns of text.
        gutter = _find_gutter(body, slide=True)
        if gutter is not None and not _words_both_sides(kept, gutter, height):
            gutter = None
    # Before the lines are made: a blank is part of its gap's width.
    _mark_blanks(kept, rules, gutter)
    merged = _merge_baselines(kept, gutter, rules)
    if source is not None:
        for row in merged:
            source.add_row(row.chars)
        out = [f for f in frags if id(f) in gone or id(f) in placed]
        source.leave_out("furniture", sum(1 for f in out for c in f.chars if not c.blank))
    return merged, gutter, _merge_baselines(dropped, gutter)


BLANK_LOW_LINES = 5  # a blank to fill in is written as this many underscores
BLANK_CLEAR_EM = 0.6  # text this close (x its size) over or under a rule is across it
BLANK_UNDER_SHARE = 0.25  # of the rule's width, covered by that text, for the rule to be across it
EDGE_TOUCH = 2.5  # pt a rule may stop short of a line and still meet it (boxes omit stroke width)
_AFTER_BLANK = frozenset(".,;:!?)]}\u201d\u2019")  # no space between a blank and these
_ROW_CACHES = tuple(n for n, v in vars(_Row).items() if isinstance(v, cached_property))


def _mark_blanks(
    rows: list[_Row], paths: Sequence[_Path], gutter: tuple[float, float] | None = None
) -> None:
    """A rule drawn under a gap in a row, or after its last word, is a blank to fill in
    (SW36): the row gets `BLANK_LOW_LINES` underscores there, which the text pass marks as a
    `blank` run like a typed "____". Which rules are blanks is `baseline_rules.classify`'s
    call, the same one the text pass asks about struck text, so no rule is both. A rule with
    text over or under it (a fraction bar, a brace) or that meets another drawn line is no
    blank; nor is the bar of a radical or the straight part of a brace, which TeX draws as a
    rule beside the sign's own glyphs (`_sign_pieces`), nor a rule the row is parted from
    (`_parted`: by the gutter, or by a drawn line such as a plot's axis). Done on the lines'
    fragments, before they are merged, so the lines are made with the blank in them; the
    rows are changed in place."""
    rules = [
        p
        for p in paths
        if p.kind == "hrule"
        and p.bbox[2] - p.bbox[0] >= baseline_rules.BLANK_MIN_PT
        and not _meets_drawing(p, paths)
    ]
    if not rules:
        return
    index = _RowIndex([r for r in rows if not r.vertical and not r.diagonal])
    found: dict[int, tuple[_Row, list[BBox]]] = {}
    for rule in rules:
        _, y0, _, y1 = rule.bbox
        if _ink_across(rule.bbox, index):
            continue  # a fraction bar, a division bar, a brace's label: not a blank
        for row in index.near(y0 - baseline_rules.BELOW_BASELINE_PT - 2.0, y1 + 1.0):
            if _sign_pieces(row):
                continue
            ink = [
                baseline_rules.Ink((c.x0, c.y0, c.x1, c.y1), c.size, c.baseline)
                for c in row.chars
                if not c.blank
            ]
            if baseline_rules.classify(rule.bbox, ink).kind != "blank":
                continue
            if not _parted(rule.bbox, row, paths, gutter):
                found.setdefault(id(row), (row, []))[1].append(rule.bbox)
                break
    for row, boxes in found.values():
        for box in sorted(boxes, reverse=True):  # right to left: places before stay valid
            at = sum(1 for c in row.chars if (c.x0 + c.x1) / 2 < (box[0] + box[2]) / 2)
            row.chars[at:at] = _blank_chars(row.chars, at, box)
        for cached in _ROW_CACHES:
            row.__dict__.pop(cached, None)


_SIGN_PIECES = frozenset("\u221a\u221b\u221c\u231c\u23b7\u23d0\u23de\u23df\u23dc\u23dd")


def _sign_pieces(row: _Row) -> bool:
    """A row that is only the glyphs of a big maths sign: a radical sign, the ends and the
    middle of a brace over or under a formula. TeX sets those glyphs with their baseline at
    their top, level with the rule that finishes the sign, so the rule reads as one that
    follows the row's last glyph; a row of words is never only these."""
    ink = [c for c in row.chars if not c.blank]
    return bool(ink) and all(c.c in _SIGN_PIECES or _MATH_SYMBOL_FONT.match(c.font) for c in ink)


def _parted(
    rule: BBox, row: _Row, paths: Sequence[_Path], gutter: tuple[float, float] | None
) -> bool:
    """Whether something stands between the rule and each glyph of the row beside it: the
    page's gutter (a rule at the head of one column level with a line of the other) or a
    drawn upright line (a plot's axis between a tick label and the line sample of the
    legend; the side of a frame). A blank and the words it belongs to are in one column
    and one cell."""
    x0, y, x1, _ = rule
    ink = [c for c in row.chars if not c.blank]
    before = max((c.x1 for c in ink if (c.x0 + c.x1) / 2 < x0), default=None)
    after = min((c.x0 for c in ink if (c.x0 + c.x1) / 2 > x1), default=None)
    walls = [(gutter[0] + gutter[1]) / 2] if gutter is not None else []
    for p in paths:
        upright = p.kind == "vrule" or p.frame
        if upright and p.bbox[1] - EDGE_TOUCH <= y <= p.bbox[3] + EDGE_TOUCH:
            walls += [p.bbox[0], p.bbox[2]]
    reach = baseline_rules.NEIGHBOUR_EM * row.size  # further off is no neighbour of the rule
    gaps = []
    if before is not None and x0 - before <= reach:
        gaps.append((before, x0))
    if after is not None and after - x1 <= reach:
        gaps.append((x1, after))
    return bool(gaps) and all(any(a <= wall <= b for wall in walls) for a, b in gaps)


def _ink_across(rule: BBox, index: _RowIndex) -> bool:
    """Printed text over or under the rule across much of its width, in any row: the numerator
    and denominator of a fraction bar, the label of a brace, the dividend under a division
    bar. A blank has nothing printed above or below it but the page."""
    x0, y0, x1, y1 = rule
    reach = BLANK_CLEAR_EM * index.largest
    covered = 0.0
    for row in index.near(y0 - reach, y1 + reach):
        for c in row.chars:
            if not c.blank and y0 - BLANK_CLEAR_EM * c.size <= c.y1 and c.y0 <= y1 + reach:
                covered += max(0.0, min(c.x1, x1) - max(c.x0, x0))
    return covered >= BLANK_UNDER_SHARE * (x1 - x0)


def _meets_drawing(rule: _Path, paths: Sequence[_Path]) -> bool:
    """A rule that crosses or touches a vertical edge of another drawn path (the plate of a
    battery on its wire, a rule that closes a cell) is part of a drawing, not a blank."""
    x0, y0, x1, _ = rule.bbox
    return any(
        o is not rule
        and o.kind != "hrule"
        and o.bbox[1] - EDGE_TOUCH <= y0 <= o.bbox[3] + EDGE_TOUCH
        and any(x0 - EDGE_TOUCH <= x <= x1 + EDGE_TOUCH for x in (o.bbox[0], o.bbox[2]))
        for o in paths
    )


def _blank_chars(chars: list[_Char], at: int, box: BBox) -> list[_Char]:
    """The underscores of a blank over `box`, with a space on each side where the text has
    none (and no closing punctuation after it), built to sit before `chars[at]`."""
    before = chars[at - 1] if at > 0 else None
    after = chars[at] if at < len(chars) else None
    ink = next((c for c in (before, after) if c is not None and not c.blank), None)
    if ink is None:
        ink = next(c for c in chars if not c.blank)
    x0, x1 = box[0], box[2]
    step = (x1 - x0) / BLANK_LOW_LINES

    def char(c: str, left: float, right: float) -> _Char:
        return _Char(
            c, left, ink.y0, right, ink.y1, ink.baseline, ink.size, ink.font, 0, ink.colour
        )

    made = [char("_", x0 + k * step, x0 + (k + 1) * step) for k in range(BLANK_LOW_LINES)]
    if before is not None and not before.blank:
        made.insert(0, char(" ", x0, x0))
    if after is not None and not after.blank and after.c not in _AFTER_BLANK:
        made.append(char(" ", x1, x1))
    return made


def _page_blocks(
    page: pymupdf.Page,
    number: int,
    rows: list[_Row],
    gutter: Gutter,
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
    page_math: Any = None,
    source: say_once.PageSource | None = None,
    document: _Document,
) -> _PageBlocks:
    page_rect = page.rect
    rows = _split_exercise_numbers(
        _split_braced_systems(_split_labels(rows), document=document), document=document
    )
    _mark_notes(rows, number, page_rect.height, ctx)
    for row in rows:
        _classify_row(row, document=document)
    contents = _contents_rows(rows, number)
    if source is not None:
        source.leave_out_at("contents", _cut_boxes(rows, contents))
    rows = contents
    _mark_case_rows(rows)
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
        document=document,
    )
    small_pictures: list[_Figure] = []
    figures = _merge_figures(_raster_figures(page, page_rect, small_pictures) + vector)
    # A logo repeated in the header or footer band is decoration, like a running header.
    figures = [f for f in figures if not margins.drops_figure(f.bbox, page_rect.height)]
    # A chart's tick labels in the footer band, or its axis title repeated on every slide,
    # are its labels, not running text.
    restored = _figure_labels(
        dropped, figures, rows, page_rect.height, ctx, label_keys, margins, document=document
    )
    for row in restored:
        _classify_row(row, document=document)
    rows = [*rows, *restored]
    figures = [f for f in figures if not _backdrop(f, rows, ctx, page.parent, document=document)]
    _claim_labels(figures, rows, ctx, document=document)
    figures, rows = _equation_pictures(page, figures, rows, ctx, small_pictures, document=document)
    figures += _fragment_figures(
        rows,
        loose,
        [f.bbox for f in figures] + [t.bbox for t in page_tables],
        ctx,
        document=document,
    )
    text_rows = [r for r in rows if not r.absorbed]
    placed = [f.bbox for f in figures] + [t.bbox for t in page_tables]
    if page_math is not None:
        # The page math pass (ADR 0006): the rows as they stand, in reading order, are what
        # the model is shown, and its verified spans are laid over them before the rules
        # below group them into equations.
        first = _reading_order([r.bbox for r in text_rows], gutter, ctx.body, placed)
        text_rows = page_math.on_page(
            number, text_rows, [text_rows[i] for i in first], used_rules, document=document
        )
    _equation_regions(text_rows, used_rules, document=document)
    if page_math is not None:
        page_math.after_regions(number, text_rows)
    order = _reading_order([r.bbox for r in text_rows], gutter, ctx.body, placed)
    ordered = _contiguous_regions([text_rows[i] for i in order])
    runs = _split_options(_runs(ordered, ctx, document=document), document=document)
    blocks = [_classify_run(run, ctx, document=document) for run in runs]
    blocks = _merge_chapter_numbers(_merge_style_splits(blocks, runs))
    _flag_text(blocks)
    for block in blocks:
        region = block.rows[0].region if block.rows else None
        if block.kind == Kind.EQUATION and region is not None:
            _add_rules(block, region.rules)
    _attach_captions(blocks, figures, document=document)
    captions = [b.bbox for b in blocks if b.kind == "attached"]
    blocks = [b for b in blocks if b.kind != "attached"]
    for figure in figures:
        if figure.equation:  # a picture of an equation: the equation step reads it
            blocks.append(
                _Block(
                    Kind.EQUATION,
                    figure.bbox,
                    text=None,
                    reasons=[EQUATION_IMAGE_REASON, *figure.reasons],
                    label=figure.number,
                )
            )
            continue
        labels = " ".join(r.clean for r in sorted(figure.labels, key=lambda r: (r.y0, r.x0)))
        blocks.append(
            _Block(
                Kind.FIGURE,
                figure.bbox,
                text=labels or None,
                caption=figure.caption,
                caption_pieces=figure.caption_pieces,
                font_size=figure.caption_size,
                reasons=figure.reasons,
                flat_scripts=figure.flat_scripts,
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
    # A glyph drawn as a filled outline in a line of typed text (an italic variable, the
    # digits of a quantity) is no pen stroke: a small drawing for the figure step, not
    # "handwriting, not converted" (F-364).
    glyphs = _glyph_outlines(loose, paths, rows) if loose else []
    if glyphs:
        loose = [s for s in loose if s not in glyphs]
        for box in page_type.stroke_regions(glyphs):
            reasons = [f"vector drawing ({GLYPH_OUTLINE})"]
            blocks.append(_Block(Kind.FIGURE, box, reasons=reasons))
    if len(loose) >= LOOSE_INK_STROKES or marks:
        ink = [*ink, *page_type.stroke_regions([*loose, *marks])]
    blocks.extend(_ink_blocks(ink, page_rect))
    # A caption held by its figure still fills its column: without it the column looks empty
    # beside the next line of the other column, which then reads as a heading across both.
    order = _reading_order([b.bbox for b in blocks], gutter, ctx.body, captions, blocks=True)
    final = _merge_column_continuations([blocks[i] for i in order])
    edges = _RightEdges(text_rows)
    _label_abstracts(final, text_rows, edges)
    for block in final:
        if block.kind in _TEXT_BLOCKS:
            block.lines = _keeps_lines(block, edges, document=document)
            block.pieces = _paragraph_pieces(block, document=document)
            if source is not None:
                _leave_out_teacher_tags(source, block)
    final = [b for b in final if b.kind not in _TEXT_BLOCKS or _has_content(b.pieces)]
    _flag_half_fractions(final, rules, text_rows)
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


def _cut_boxes(before: Sequence[_Row], after: Sequence[_Row]) -> list[BBox]:
    """The boxes of the glyphs that `before` has and `after` has no more: what a rule of layout
    cut from the rows (`_contents_rows`: leaders and page numbers)."""
    kept = {(c.c, c.x0, c.y0) for r in after for c in r.chars}
    return [
        (c.x0, c.y0, c.x1, c.y1)
        for r in before
        for c in r.chars
        if not c.blank and (c.c, c.x0, c.y0) not in kept
    ]


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
    ctx: _Context,
    accepted: Collection[tuple] = frozenset(),
    margins: _MarginFilter | None = None,
    *,
    document: _Document,
) -> list[_Row]:
    """Rows the header and footer rules dropped (`_prepare_rows`) that label a figure: a
    chart's tick labels in the footer band (bare numbers, read as page numbers), its axis
    title repeated on every slide of a deck (a running footer), a rotated axis title
    repeated at the same place (a watermark). A dropped row is a label when the one rule
    makes it one (`_label_owners`: short, not diagonal, near the figure's strokes or its
    other labels). Bare numbers count only two or more on one baseline, or level with a
    figure when the document's page numbers are not there (`margins.numbering`: an axis
    title "v" at the top of a full-page plot's y-axis, read as a page number in roman
    numerals): a page number alone under a figure, or beside one in the band where the
    other pages have theirs, is still a page number. Other text counts only when its key is
    `accepted` (`_label_keys`: beside a figure wherever the document repeats it), so a
    running footer that also runs under pages of text stays dropped."""
    if not dropped or not figures:
        return []
    candidates = [
        r
        for r in _label_candidates(dropped, numbers=True)
        if _is_number_row(r) or _label_key(r, height) in accepted
    ]
    owners = _label_owners(figures, [*rows, *candidates], ctx, document=document)
    found = [r for r in candidates if id(r) in owners]
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


def _is_number_row(row: _Row) -> bool:
    return not row.vertical and _is_page_number(row.clean)


def _attached(frags: Sequence[_Row], placed: set[int], gone: set[int], height: float) -> set[int]:
    """The rows at a head's or foot's place (`_MarginFilter.at_place`) that go on from the
    page's text: the row next to them towards the middle of the page is less than
    `MARGIN_ATTACHED` of a line away and stays on the page. A running head or foot stands
    clear of the text block; the last line of a footnote, or of a table in small type, at the
    foot of a page with narrow margins sits at the same height on every page and does not.
    Worked from the middle of the page outwards, so every line of such a note is kept."""
    rows = [f for f in frags if not f.vertical and id(f) not in gone]
    kept: set[int] = set()
    middle = height / 2
    for row in sorted(
        (f for f in rows if id(f) in placed), key=lambda f: abs((f.y0 + f.y1) / 2 - middle)
    ):
        top = (row.y0 + row.y1) / 2 < middle
        for other in rows:
            if other is row or (id(other) in placed and id(other) not in kept):
                continue
            gap = other.y0 - row.y1 if top else row.y0 - other.y1
            x0, _, x1, _ = other.bbox
            if -0.5 * row.height <= gap <= MARGIN_ATTACHED * row.height and min(
                x1, row.bbox[2]
            ) > max(x0, row.bbox[0]):
                kept.add(id(row))
                break
    return kept


def _shares_line(row: _Row, others: Sequence[_Row]) -> bool:
    """Does another row stand on the same line as `row` (a vertical overlap of half the
    smaller height)?"""
    return any(
        not o.vertical
        and o is not row
        and min(o.y1, row.y1) - max(o.y0, row.y0) >= 0.5 * min(o.height, row.height)
        for o in others
    )


def _label_candidates(dropped: Sequence[_Row], *, numbers: bool) -> list[_Row]:
    """Dropped rows that could be a figure's labels: short, not diagonal; bare numbers
    only with `numbers`."""
    return [
        r
        for r in dropped
        if not r.diagonal and _short_label(r) and (numbers or not _is_number_row(r))
    ]


def _label_key(row: _Row, height: float) -> tuple:
    """What the header and footer rules recognise a dropped row by (its band and text, or
    its rotated text and place)."""
    if row.vertical:
        return ("rotated", *_rotated_key(row))
    return ("band", _margin_zone(row, height), _norm_margin(row.clean))


def _label_keys(
    doc: pymupdf.Document,
    numbers: Sequence[int],
    prepared: dict[int, tuple[list[_Row], Gutter, list[_Row]]],
    paths: dict[int, list[_Path]],
    ctx: _Context,
    margins: _MarginFilter,
    guard: _PageGuard,
    *,
    document: _Document,
) -> set[tuple]:
    """Keys (`_label_key`) of the dropped repeated text that is beside a figure on every
    analysed page it was dropped from: an axis title repeated under each chart of a deck,
    not a running footer that also runs under pages of text. Only pages with such a
    candidate pay for a first look at their figures (`_rough_figures`). A page that raises
    (`guard`) says nothing: the keys are those of the other pages."""
    near: set[tuple] = set()
    far: set[tuple] = set()
    for n in numbers:
        with guard(n):
            page = doc[n]
            candidates = [
                r
                for r in _label_candidates(prepared[n][2], numbers=False)
                if _label_key(r, page.rect.height) not in far
            ]
            if not candidates:
                continue  # nothing, or only what a page of text already ruled out
            rows = prepared[n][0]
            figures = _rough_figures(page, rows, paths[n], ctx, margins, document=document)
            owners = _label_owners(figures, [*rows, *candidates], ctx, document=document)
            here = [(_label_key(row, page.rect.height), id(row) in owners) for row in candidates]
            near.update(key for key, beside in here if beside)
            far.update(key for key, beside in here if not beside)
    return near - far


def _rough_figures(
    page: pymupdf.Page,
    rows: list[_Row],
    paths: list[_Path],
    ctx: _Context,
    margins: _MarginFilter,
    *,
    document: _Document,
) -> list[_Figure]:
    """The page's figures as `_page_blocks` finds them, roughly (equation rules are
    not taken out first): for `_label_keys` only."""
    rect = page.rect
    figures = _merge_figures(
        _raster_figures(page, rect) + _vector_figures(paths, rows, rect, ctx, document=document)
    )
    return [f for f in figures if not margins.drops_figure(f.bbox, rect.height)]


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
    (half their paths or more are the page type's pen strokes, or every path is a hand
    mark over typed text, `_pen_mark_over_text`) are returned as handwriting boxes (never
    sent to the figure model); any other drawing, open strokes with no pen evidence
    included (a skeletal structure's bonds, a tick drawn as three points), becomes a
    FIGURE block for the model (WS6.10, 2026-11-11). Since 2026-10-13 (the drawings
    sweep) some straight drawings count too: bonds between atom groups (`_bond_ends`: a
    structural formula's h/v bonds, which the row of its letters holds), boxes joined by
    lines across their sides (`_connected`: a one-line circuit of resistor boxes and
    wires), and dots round a letter that reach out of its line (`_dots_out_of_line`: a
    Lewis structure's electrons); the drawing's box takes in the atom groups its bonds
    join."""
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
        # Handwriting only with pen evidence, from the strokes themselves: half its paths or
        # more are the page type's pen strokes, a pen stroke at any length (`_Path.pen`: a
        # signature) or a short straight stroke that touches nothing else in the group
        # (`_hand_strokes`: a tick, a cross); or it is a hand mark over typed text. Being an
        # open stroke is no evidence: the single straight lines of a skeletal structure,
        # meeting at their ends or running parallel, are open strokes, and were cut out as
        # "handwriting, not converted" on typeset exams (F-400, accuracy round 2).
        pen = _hand_strokes(members) | {id(p) for p in members if p.source in ink_sources or p.pen}
        if curved and (2 * len(pen) >= len(members) or all(id(p) in hand for p in members)):
            marks.append(box)
        else:
            reasons = [f"vector drawing ({len(members)} paths)"]
            figures.append(_Block(Kind.FIGURE, box, reasons=reasons))
    return figures, marks


def _hand_strokes(members: Sequence[_Path]) -> set[int]:
    """Ids of the short straight strokes (`_Path.lines`) of a group drawn as a hand draws:
    every line off both axes (HAND_OBLIQUE), not all one length (HAND_SAME), and nothing
    else in the group touching or copying them: no end within HAND_MEET of another open
    stroke's end or inside a filled shape (an arrow's head), no line alongside another
    stroke's line (HAND_PARALLEL), none of the same length as another stroke's line
    (`_same_length`). A skeletal formula's bonds meet at their ends, run side by side and
    repeat one length; a hand's tick, cross or slash does none of that."""
    found: set[int] = set()
    for p in members:
        if not p.lines or any(
            min(abs(x1 - x0), abs(y1 - y0)) <= HAND_OBLIQUE * (abs(x1 - x0) + abs(y1 - y0))
            for x0, y0, x1, y1 in p.lines
        ):
            continue
        lengths = [math.dist(s[:2], s[2:]) for s in p.lines]
        if len(lengths) > 1 and max(lengths) - min(lengths) <= HAND_SAME * max(lengths):
            continue
        ends = (p.lines[0][:2], p.lines[-1][2:])
        touches = False
        for q in members:
            if q is p:
                continue
            near = (q.lines[0][:2], q.lines[-1][2:]) if q.lines else q.ends if q.open_stroke else ()
            touches = (
                any(math.dist(a, b) <= HAND_MEET for a in ends for b in near)
                or (q.filled and any(_centre_inside((x, y, x, y), _pad(q.bbox)) for x, y in ends))
                or any(_alongside(s, t) or _same_length(s, t) for s in p.lines for t in q.lines)
            )
            if touches:
                break
        if not touches:
            found.add(id(p))
    return found


def _same_length(
    s: tuple[float, float, float, float], t: tuple[float, float, float, float]
) -> bool:
    """Two lines of one length to rounding precision (page_type.SIZE_TWIN_TOLERANCE)."""
    a, b = math.dist(s[:2], s[2:]), math.dist(t[:2], t[2:])
    return abs(a - b) <= max(page_type.SIZE_TWIN_TOLERANCE * a, page_type.SIZE_TWIN_ABS)


def _alongside(s: tuple[float, float, float, float], t: tuple[float, float, float, float]) -> bool:
    """`t` lies beside `s`: parallel within HAND_PARALLEL, its middle over `s` and nearer
    its line than half its length (the second line of a double bond)."""
    ux, uy = s[2] - s[0], s[3] - s[1]
    vx, vy = t[2] - t[0], t[3] - t[1]
    ls, lt = math.hypot(ux, uy), math.hypot(vx, vy)
    if not ls or not lt or abs(ux * vy - uy * vx) > math.sin(HAND_PARALLEL) * ls * lt:
        return False
    mx, my = (t[0] + t[2]) / 2 - s[0], (t[1] + t[3]) / 2 - s[1]
    along, across = (mx * ux + my * uy) / ls, abs(mx * uy - my * ux) / ls
    return 0 <= along <= ls and across <= ls / 2


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


REASON_HALF_FRACTION = "half a fraction"


def _flag_half_fractions(
    blocks: Sequence[_Block], rules: Sequence[_Path], rows: Sequence[_Row]
) -> None:
    """Mark the equations that sit against a fraction bar from one side only.

    An equation whose box ends at a thin bar, with the bar's other side holding glyphs of
    something else, is half a fraction: the numerator without its denominator or the
    denominator without its numerator ("Q(x)" of P(x)/Q(x)). Its crop shows half of what
    the page shows, and whatever is read from it is a plausible but wrong answer, so the
    equation step asks for a decision (`equation_step.HALF_FRACTION_CODE`). Bars that
    layout used (the equation's own, inside its box or touching both sides of it) never
    mark anything.
    """
    bars = [
        p
        for p in rules
        if p.kind == "hrule" and p.bbox[3] - p.bbox[1] <= 1.5 and p.bbox[2] - p.bbox[0] >= 2.0
    ]
    if not bars:
        return
    items: list[tuple[BBox, list[str], float]] = []
    for block in blocks:
        if block.kind == Kind.EQUATION:
            items.append((block.bbox, block.reasons, block.font_size or 10.0))
        else:
            for piece in block.pieces:
                if isinstance(piece, _Inline):
                    items.append((piece.bbox, piece.reasons, piece.font_size))
    if not items:
        return
    chars = [c for r in rows if not r.vertical for c in r.ink]
    for bar in bars:
        bx0, by0, bx1, by1 = bar.bbox
        middle = (by0 + by1) / 2
        for box, reasons, size in items:
            width = min(bx1, box[2]) - max(bx0, box[0])
            if width < 0.5 * min(bx1 - bx0, box[2] - box[0]) or bx1 - bx0 > 10 * size:
                continue
            if abs(middle - box[1]) <= 0.3 * size:  # the bar is at the top: denominator
                other = (-1, middle)  # glyphs above the bar
            elif abs(middle - box[3]) <= 0.3 * size:  # the bar is at the bottom: numerator
                other = (1, middle)
            else:
                continue
            if REASON_HALF_FRACTION in reasons or "fraction bar" in reasons:
                continue
            side, edge = other
            touching = any(
                bx0 - 1 <= (c.x0 + c.x1) / 2 <= bx1 + 1
                and (
                    0 <= edge - c.y1 <= 0.35 * size if side < 0 else 0 <= c.y0 - edge <= 0.35 * size
                )
                for c in chars
            )
            if touching:
                reasons.append(REASON_HALF_FRACTION)


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


GLYPH_OUTLINE = "glyph outline in a line of text"  # the reason `_glyph_outlines` gives
GLYPH_LINE_SLACK = 0.1  # of the line's height: how far a glyph outline may reach out of it
GLYPH_FILL_SLACK = 1.0  # pt: the fill that paints a stroke holds its box within this


def _glyph_outlines(
    strokes: Sequence[BBox], paths: Sequence[_Path], rows: Sequence[_Row]
) -> list[BBox]:
    """The pen strokes (the page type's, `strokes`) that are glyphs drawn as filled
    outlines inside a line of typed text: within the line's height (GLYPH_LINE_SLACK of it
    over at most), with typed glyphs of the line on both sides, and painted by a fill with
    no stroke that lies within the line too. Outline maths (an italic variable, the digits
    of an inline quantity) passed the stroke tests and was cut out as "handwriting, not
    converted" (F-364, r16: 14 such regions 13 to 35 pt wide). A pen stroke is stroked, or
    reaches out of the line, or stands where no typed text is (an answer on a blank)."""
    index = _RowIndex([r for r in rows if not r.vertical])
    fills = [p for p in paths if p.fill_only]
    slack = GLYPH_FILL_SLACK

    def in_line(box: BBox, row: _Row) -> bool:
        reach = GLYPH_LINE_SLACK * (row.y1 - row.y0)
        return row.y0 - reach <= box[1] and box[3] <= row.y1 + reach

    found = []
    for box in strokes:
        x0, y0, x1, y1 = box
        for row in index.near(y0, y1):
            if not (in_line(box, row) and row.x0 < x0 and x1 < row.x1):
                continue
            if any(
                in_line(p.bbox, row)
                and p.bbox[0] - slack <= x0
                and p.bbox[1] - slack <= y0
                and x1 <= p.bbox[2] + slack
                and y1 <= p.bbox[3] + slack
                for p in fills
            ):
                found.append(box)
                break
    return found


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


# A footnote: a block in the bottom part of the page (below this share of its height), in
# a smaller font than the body, that starts with a mark (a digit, *, a dagger) printed
# raised in the text above it on the same page.
FOOTNOTE_ZONE = 0.6
FOOTNOTE_SIZE = 0.92
_FOOTNOTE_MARKS = frozenset("*\u2020\u2021\u00a7")


def _row_at(rows: Sequence[_Row], point: tuple[float, float]) -> _Row | None:
    """The line a destination points at: hyperref puts it a little above the top of its
    line, so the row whose top is nearest below it (or at it), at its x."""
    x, y = point
    near = [
        r
        for r in rows
        if not r.vertical and r.y0 - 0.7 * r.size <= y <= r.y1 and r.x0 - r.size <= x <= r.x1
    ]
    return min(near, key=lambda r: abs(r.y0 - y)) if near else None


def _covers(row: _Row, box: BBox) -> bool:
    """Does the link rectangle sit on the line of `row` (its middle between the row's top
    and bottom)?"""
    return row.y0 - 1 <= (box[1] + box[3]) / 2 <= row.y1 + 1


def _mark_chars(row: _Row, box: BBox) -> list[int]:
    """The glyphs of `row` a link's rectangle covers (by their centres): the raised ones
    when it covers some, so a letter the rectangle touches is not part of the mark."""
    hits = [
        i
        for i, c in enumerate(row.chars)
        if not c.blank
        and box[0] - 0.5 <= (c.x0 + c.x1) / 2 <= box[2] + 0.5
        and box[1] - 0.5 <= (c.y0 + c.y1) / 2 <= box[3] + 0.5
    ]
    return [i for i in hits if row.is_script(row.chars[i])] or hits


def _lead_mark(row: _Row, named: bool) -> int:
    """Where the raised mark that starts `row` ends (a char index), or 0 without one. A
    mark is digits and footnote signs (`_FOOTNOTE_MARKS`), any raised glyph in a footnote
    the file names."""
    end = 0
    for i, c in enumerate(row.chars):
        if c.blank:
            if end:
                break
            continue
        if not row.is_script(c) or not (named or c.c.isdigit() or c.c in _FOOTNOTE_MARKS):
            break
        end = i + 1
    return end


def _set_off(row: _Row, at: int) -> None:
    """A blank before `row.chars[at]`, between a note mark and the word it touches: the glyph
    where the mark ends is where the split is made (`row.text` and the rest are made again)."""
    if not 0 < at < len(row.chars) or row.chars[at].blank or row.chars[at - 1].blank:
        return
    before = row.chars[at - 1]
    blank = _Char(" ", before.x1, before.y0, before.x1, before.y1, before.baseline, before.size,
                  before.font, before.flags | SET_OFF_FLAG)  # fmt: skip
    row.chars.insert(at, blank)
    for cached in ("text", "clean", "styled_text"):
        row.__dict__.pop(cached, None)


def _mark_notes(rows: list[_Row], number: int, height: float, ctx: _Context) -> None:
    """Where a footnote starts on this page, and each note mark set off from the word it
    touches (a raised "2" kept as a character of "Feehly" read "2Feehly").

    A file that names its footnotes (`ctx.notes`): the line a destination points at starts
    one (`Row.note`), and the glyphs a link to it covers are its mark in the text. Without
    names, a line in the lower part of the page, in smaller type, that starts with a raised
    digit or footnote sign starts one, as `_footnote` decides. The mark is split from the word
    at the glyph, where it is known to be raised: never an exponent, which no destination
    and no footnote line points at. A raised digit that ends a larger heading and is the
    mark of a footnote on the page is set off from the heading's words in the same way."""
    notes = ctx.notes
    named = notes is not None
    if notes is not None:
        for dest in notes.destinations.get(number, ()):
            row = _row_at(rows, dest.point)
            if row is not None:
                row.note = (*row.note, dest.name)
    marks: set[str] = set()
    for row in rows:
        if row.vertical:
            continue
        low = row.y0 >= FOOTNOTE_ZONE * height and row.size < FOOTNOTE_SIZE * ctx.body
        end = _lead_mark(row, named=bool(row.note)) if row.note or (not named and low) else 0
        if end:
            marks.add("".join(c.c for c in row.chars[:end] if not c.blank))
            _set_off(row, end)
    heading_rows = [r for r in rows if not r.note and not r.vertical and _heading_sized(r, ctx)]
    for ref in notes.references.get(number, ()) if notes is not None else ():
        for row in rows:
            if row.note or not _covers(row, ref.box):
                continue
            hits = _mark_chars(row, ref.box)
            if not hits:
                continue
            if row in heading_rows and hits[-1] == max(
                i for i, c in enumerate(row.chars) if not c.blank
            ):
                _set_off(row, hits[0])  # the mark ends the heading: not one of its words
            elif (after := hits[-1] + 1) < len(row.chars) and row.chars[after].c.isalnum():
                _set_off(row, after)
            break
    if named or not marks:
        return
    for row in heading_rows:
        last = max((i for i, c in enumerate(row.chars) if not c.blank), default=-1)
        start = last
        while start > 0 and row.is_script(row.chars[start]):
            start -= 1
        start += 1
        if start <= last and "".join(c.c for c in row.chars[start : last + 1]) in marks:
            _set_off(row, start)
