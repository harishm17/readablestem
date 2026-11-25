"""Rows: PyMuPDF lines merged into rows on one baseline, form fields, ligatures and right-to-left
text, stacked fractions, binomials, scripts, big operators and the arrow labels of reactions;
the public glyph and strike readers (`line_glyphs`, `page_glyphs`, `page_strikes`).
"""

from __future__ import annotations

import bisect
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Any, cast

import pymupdf

from readablestem.core.images import png_size
from readablestem.core.mathchars import base_char
from readablestem.core.types import BBox, Element, GlyphContext
from readablestem.pipeline import (
    chem,
    font_codes,
    glyph_names,
    glyph_reconcile,
    glyph_strokes,
    page_type,
    tables,
)
from readablestem.pipeline.layout.access import (
    _CACHE,
    _LOCK,
    CROP_PAD,
    TEXT_FLAGS,
    _ensure_probed,
    _line_clip,
    _quiet_mupdf,
    _zoom,
)
from readablestem.pipeline.layout.glyphs import (
    _BRACKETS,
    _TEXT_MAP,
    _Char,
    _char_class,
    _font_traits,
    face_traits,
    glyph_style,
    mark_greek_words,
)
from readablestem.pipeline.layout.shared import (
    _CHEM_DASHES,
    LIMIT_SIGNS,
    SCRIPT_MIN_RATIO,
    SCRIPT_RATIO,
    SCRIPT_ROW_FLOOR,
    _arrow_spans,
    _Display,
    _over_arrow,
    _Path,
    _Row,
    _RowIndex,
    mark_small_capitals,
)
from readablestem.pipeline.parse import is_math_alphanumeric

# Big operators of an inline formula that TeX sets on a baseline of their own ($\int_0^1$): the
# signs that lead a short fragment `_attach_big_operators` folds into its line. The root is one.
# The big union and intersection of `shared.LIMIT_SIGNS` are not: one of them is folded only as
# a sign with its limits (`_sign_of_limits`; arXiv 2308.00984 page 22).
_BIG_OPERATORS = frozenset("\u222b\u222c\u222d\u222e\u2211\u220f\u2210\u221a")
LINE_GLYPH_REACH = 24  # glyphs either side of the span `line_glyphs` keeps, nearest first
LINE_GLYPH_ROW = 0.5  # x the span's height: how far off the span's row a neighbour may sit
# `GlyphContext.how`: the raw character's "how" (`glyph_names`, `font_codes`) as one letter.
_HOW_CODES = {None: "-", "name": "n", "font": "f", "gap": "g"}
# Neighbours that are maths (`_char_class`: maths, digit, operator, bracket), not words.
_MATHS_CLASSES = "MDOB"
LIFT_RAISED = 0.2  # a script glyph this far (x the text size) above the box's baseline is raised
LIFT_LOWERED = 0.08  # ... and this far below it, lowered


def _lifts(chars: Sequence[_Char]) -> str:
    """`GlyphContext.lift` of the glyphs of a box, one letter each: "^" for a glyph smaller than
    the box's text (`SCRIPT_RATIO`) that sits above its baseline, "v" for one below, "-" for
    any other. The box's text is its largest glyphs (those from 0.85 of the largest size) and
    its baseline the median of theirs. A glyph as large as the text is never marked: a
    fraction's numerator is on its own line, which the picture shows and this does not say."""
    inked = [c for c in chars if not c.blank]
    if not inked:
        return "-" * len(chars)
    size = max(c.size for c in inked)
    baselines = sorted(c.baseline for c in inked if c.size >= SCRIPT_RATIO * size)
    base = baselines[len(baselines) // 2]
    marks = []
    for c in chars:
        rise = base - c.baseline
        if c.blank or c.size >= SCRIPT_RATIO * size:
            marks.append("-")
        else:
            marks.append(
                "^" if rise > LIFT_RAISED * size else "v" if rise < -LIFT_LOWERED * size else "-"
            )
    return "".join(marks)


def line_glyphs(
    pdf_bytes: bytes,
    element: Element,
    across: tuple[float, float] | None = None,
    *,
    reach: bool = True,
    facts: bool = False,
) -> GlyphContext | None:
    """The glyphs of an equation's span and, for an inline equation, of the line it sits in
    (the region of `crop_line`, `_line_clip`): those on the span's own row, left and right of
    it, nearest first (`LINE_GLYPH_REACH` each; `reach` False: none). A display equation has its
    own glyphs only. `facts` (`EQUATION_FILE_FACTS`): also how each glyph of the span was
    recovered, whether it sits raised or lowered, and the maths glyphs beside it
    (`GlyphContext.how`, `lift`, `math_before`, `math_after`); without it they stay empty.
    None when the page is turned, or no glyph lies under the span (a picture)."""
    with _LOCK:
        _quiet_mupdf()
        try:
            key, doc = _CACHE.document(pdf_bytes)
            if not 0 <= element.page < doc.page_count:
                raise ValueError(f"element page {element.page} is not in the PDF")
            _ensure_probed(pdf_bytes, key, [element.page])
            page = _CACHE.page(key, element.page)
            if page.rotation % 360:
                return None
            x0, y0, x1, y1 = element.bbox
            inline = element.display == "inline" and reach
            clip = _line_clip(page, element, across) if inline else pymupdf.Rect(element.bbox)
            clip += (-1, -1, 1, 1)
            height = max(y1 - y0, 4.0)
            row = (y0 - LINE_GLYPH_ROW * height, y1 + LINE_GLYPH_ROW * height)
            span: list[tuple[str, str | None]] = []
            span_chars: list[_Char] = []
            how: list[str] = []
            math_left: list[tuple[float, tuple[str, str | None]]] = []
            math_right: list[tuple[float, tuple[str, str | None]]] = []
            items: list[glyph_reconcile.Item] = []
            fonts_exact = page_type.producer_hint(doc) == "tex"
            scripts: set[str] = set()
            left: list[tuple[float, tuple[str, str | None]]] = []
            right: list[tuple[float, tuple[str, str | None]]] = []
            for block in _text_blocks(page, clip=clip):
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    for piece in line["spans"]:
                        if page_type.span_hidden(piece):
                            continue
                        size, font, flags = float(piece["size"]), piece["font"], int(piece["flags"])
                        for ch in piece["chars"]:
                            text = ch["c"].translate(_TEXT_MAP)
                            gx0, gy0, gx1, gy1 = ch["bbox"]
                            cx, cy = (gx0 + gx1) / 2, (gy0 + gy1) / 2
                            if not text or not (
                                clip.x0 <= cx <= clip.x1 and clip.y0 <= cy <= clip.y1
                            ):
                                continue
                            glyph = _Char(
                                text, gx0, gy0, gx1, gy1, ch["origin"][1], size, font, flags
                            )
                            entry = (" " if glyph.blank else glyph.styled, glyph_style(glyph))
                            if x0 - 0.5 <= cx <= x1 + 0.5 and y0 - 0.5 <= cy <= y1 + 0.5:
                                span.append(entry)
                                if facts and entry[0]:  # one of each for each pair of `span`
                                    span_chars.append(glyph)
                                    how.append(
                                        _HOW_CODES.get(ch.get("how"), "?" if "how" in ch else "-")
                                    )
                                items.append(glyph_reconcile.Item(text, gx0, gx1, cy, size))
                                if not text.isascii() and not glyph_reconcile.tex_font(font):
                                    fonts_exact = False
                                if text.isalpha() and glyph_reconcile.script_font(font):
                                    scripts.add(base_char(text))
                            elif inline and row[0] <= cy <= row[1]:
                                nearest = (abs(cx - (x0 if cx < x0 else x1)), entry)
                                (left if cx < x0 else right).append(nearest)
                                if facts and _char_class(glyph) in _MATHS_CLASSES:
                                    (math_left if cx < x0 else math_right).append(nearest)
            if not any(c.strip() for c, _ in span):
                return None
            left.sort(key=lambda pair: pair[0])
            right.sort(key=lambda pair: pair[0])
            math_left.sort(key=lambda pair: pair[0])
            math_right.sort(key=lambda pair: pair[0])
            return GlyphContext(
                span=GlyphContext.encode(span),
                before=GlyphContext.encode(g for _, g in left[:LINE_GLYPH_REACH]),
                after=GlyphContext.encode(g for _, g in right[:LINE_GLYPH_REACH]),
                tex=fonts_exact,
                labels=" ".join(glyph_reconcile.edge_labels(items)),
                scr="".join(sorted(c for c in scripts if len(c) == 1 and c.isupper())),
                how="".join(how),
                lift=_lifts(span_chars) if facts else "",
                math_before=GlyphContext.encode(g for _, g in math_left[:LINE_GLYPH_REACH]),
                math_after=GlyphContext.encode(g for _, g in math_right[:LINE_GLYPH_REACH]),
            )
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


@dataclass(frozen=True, slots=True)
class _Placed:
    """One glyph of a table's box in the frame of its crop (`table_words`): what
    `tables.printed_words` reads, in pixels, with the table turned upright."""

    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    blank: bool
    baseline: float  # the ink bottom stands for it in turned text


def table_words(pdf_bytes: bytes, element: Element, png: bytes) -> list[tables.PrintedWord]:
    """The words the PDF prints inside a table's box, with their boxes in pixels of `png`,
    the crop `crop` made of that element (so a table set sideways is upright here, as the
    model sees it), and each raised digit marked as raised (`tables.printed_words`).

    What the file states about the region, for the table step to hand the reader and to
    check its answer against. Not the grid: nothing here knows which cell a word is in."""
    with _LOCK:
        _quiet_mupdf()
        try:
            key, doc = _CACHE.document(pdf_bytes)
            if not 0 <= element.page < doc.page_count:
                raise ValueError(f"element page {element.page} is not in the PDF")
            _ensure_probed(pdf_bytes, key, [element.page])
            page = _CACHE.page(key, element.page)
            x0, y0, x1, y1 = element.bbox
            clip = pymupdf.Rect(x0 - CROP_PAD, y0 - CROP_PAD, x1 + CROP_PAD, y1 + CROP_PAD)
            clip &= page.rect
            if clip.is_empty or clip.width < 0.5 or clip.height < 0.5:
                raise ValueError("element region is empty or outside the page")
            turn = element.table.turned if element.table is not None else 0
            rows = _page_rows(page)
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()
    move = _zoom(1.0, turn)
    frame = clip * move
    scale = png_size(png)[0] / frame.width

    def place(box: Sequence[float]) -> BBox:
        (ax, ay), (bx, by) = (
            pymupdf.Point(box[0], box[1]) * move,
            pymupdf.Point(box[2], box[3]) * move,
        )
        return (
            (min(ax, bx) - frame.x0) * scale,
            (min(ay, by) - frame.y0) * scale,
            (max(ax, bx) - frame.x0) * scale,
            (max(ay, by) - frame.y0) * scale,
        )

    pieces: list[list[_Placed]] = []
    for row in rows:
        placed = []
        for c in row.chars:
            if not (x0 - 0.5 <= (c.x0 + c.x1) / 2 <= x1 + 0.5):
                continue
            if not (y0 - 0.5 <= (c.y0 + c.y1) / 2 <= y1 + 0.5):
                continue
            box = place((c.x0, c.y0, c.x1, c.y1))
            baseline = box[3] if turn else (c.baseline - frame.y0) * scale
            placed.append(_Placed(c.c, *box, c.size * scale, c.blank, baseline))
        ink = [c for c in placed if not c.blank]
        # Only text that reads along the crop: a line of rotated labels does not.
        if ink and abs(ink[-1].x0 - ink[0].x0) >= abs(ink[-1].y0 - ink[0].y0):
            pieces.append(placed)
    words = tables.printed_words(cast("list[list[Any]]", pieces))
    return [
        tables.PrintedWord(w.text, round(w.x0), round(w.y0), round(w.x1), round(w.y1))
        for w in words
        if w.text.strip()
    ]


def _raw_blocks(page: pymupdf.Page) -> list[dict]:
    """The page's raw text dictionary: the one text extraction layout makes per page,
    shared by the rows and the page type (`pipeline.page_type`)."""
    return _text_blocks(page)


def _text_blocks(page: pymupdf.Page, **kwargs: Any) -> list[dict]:
    """`page.get_text("rawdict", flags=TEXT_FLAGS, **kwargs)["blocks"]` with the codes of
    symbol and TeX fonts read as what they draw (`font_codes`) and each ligature's
    characters given their own share of the glyph's width (`_spread_ligatures`), and the
    glyphs of drawing fonts taken out of the text (`glyph_strokes`), and each span's size
    the size its glyphs are drawn at (`page_type.drawn_size`). Every place layout reads
    characters from the PDF goes through here (rows, line glyphs, the text pass's glyphs), so
    no path sees the zero-width boxes ligatures come out with, or a line set as glyphs."""
    flags = TEXT_FLAGS | page_type.actualtext_flag(page)  # the page's one choice (page_type)
    blocks = page.get_text("rawdict", flags=flags, **kwargs)["blocks"]
    for block in blocks:  # before anything measures by a span's size
        for line in block.get("lines", ()):
            for span in line["spans"]:
                span["size"] = page_type.drawn_size(span)
    glyph_strokes.take(blocks)  # the glyphs of drawing fonts are strokes, not text
    glyph_names.recover(page, blocks)
    font_codes.remap(blocks)
    glyph_names.mark_gaps(blocks)
    for block in blocks:
        if block.get("type") == 0:
            for line in block["lines"]:
                for span in line["spans"]:
                    _swap_lam_alef(span["chars"])
                    _spread_ligatures(span["chars"], line["dir"])
    return blocks


ALEFS = frozenset("\u0622\u0623\u0625\u0627\u0671")  # the alef family, the letters a lam joins


def _swap_lam_alef(chars: list[dict]) -> None:
    """A lam-alef ligature glyph is one glyph for the two letters, written lam then alef;
    MuPDF reverses the characters of right-to-left text and the pair with them, so the alef
    comes out before its lam ("إىل" for "إلى"; PDFium and Poppler do the same, and no second
    reading can catch it). Where an alef-family letter and a lam follow one another and share
    one glyph box (one has no width, at the other's edge, as `_spread_ligatures` finds the
    characters of a ligature), they are put back in order. The boxes stay where they are."""
    for first, second in pairwise(chars):
        if first["c"] in ALEFS and second["c"] == "\u0644":
            (a0, _, a1, _), (b0, _, b1, _) = first["bbox"], second["bbox"]
            if (a1 - a0 < 1e-3 or b1 - b0 < 1e-3) and min(abs(a0 - b1), abs(a1 - b0)) < 0.5:
                first["c"], second["c"] = second["c"], first["c"]


def _spread_ligatures(chars: list[dict], direction: Sequence[float]) -> None:
    """A ligature glyph ("ff", "ffi", "fl" in Latin Modern, Computer Modern, Times) is one
    glyph for several characters, and MuPDF gives the first the whole glyph box and the rest
    a box of no width at its end. Sorted by position, as rows, table cells and words are,
    the zero-width characters swap with the next letter ("Stiffness" came out "Stifnf ess").
    Each of the characters now gets an equal share of the glyph's box along the line, so
    position order is reading order. Only letters share a box: the other characters of no
    width (the arrow of a vector, a combining accent, a negation slash) are drawn over the
    letter before them and stay at its end. Text running left to right or top to bottom only;
    other directions are left as they are."""
    dx, dy = direction[0], direction[1]
    if dx >= 0.7:
        lo = 0
    elif dy >= 0.7:
        lo = 1
    else:
        return
    hi = lo + 2
    i = 1
    while i < len(chars):
        run = i
        while (
            run < len(chars)
            and chars[run]["bbox"][hi] - chars[run]["bbox"][lo] < 1e-3
            and chars[run]["c"].isalpha()
            and abs(chars[run]["bbox"][lo] - chars[i - 1]["bbox"][hi]) < 0.5
        ):
            run += 1
        head = chars[i - 1]
        width = head["bbox"][hi] - head["bbox"][lo]
        if run == i or width < 1e-3:
            i += 1
            continue
        parts = run - i + 1
        start, step = head["bbox"][lo], width / parts
        for k, ch in enumerate([head, *chars[i:run]]):
            box = list(ch["bbox"])
            box[lo], box[hi] = start + k * step, start + (k + 1) * step
            ch["bbox"] = tuple(box)
        i = run + 1


@dataclass(frozen=True, slots=True)
class FormField:
    """One fillable field of the page (an AcroForm widget), in display coordinates."""

    kind: str  # "text" | "choice" | "checkbox" | "signature"
    box: BBox
    value: str = ""  # a text or choice field's value, as one line
    checked: bool = False


_FIELD_KINDS = {
    "Text": "text",
    "ComboBox": "choice",
    "ListBox": "choice",
    "CheckBox": "checkbox",
    "RadioButton": "checkbox",
    "Signature": "signature",
}


_FIELD_UNSET = ("", "Off", "False", "None")
FIELD_BLANK = "_____"  # an empty field, written as a drawn blank is (`_mark_blanks`)
FIELD_BOXES = {True: "☑", False: "☐"}  # a checkbox with its state
FIELD_SIGNATURE = f"{FIELD_BLANK} (signature field)"


def take_form_fields(page: pymupdf.Page) -> list[FormField]:
    """The page's text, choice, checkbox, radio and signature fields (SW39), in reading
    order, taken off the open page: what a field draws (its value, a tick, a frame, a
    picture of a signature) is then in no later read of the page's text, drawings or images,
    so it can be neither a stray paragraph nor a figure; `_field_rows` puts the field back
    as a token. Push buttons stay as they are. The document is the caller's copy, so
    nothing is written to the file. The caller holds `_LOCK`."""
    display = _Display.of(page)
    taken: list[tuple[pymupdf.Widget, FormField]] = []
    try:
        for widget in list(page.widgets() or []):
            kind = _FIELD_KINDS.get(widget.field_type_string)
            if kind is None:
                continue
            raw = widget.field_value
            value = " ".join(
                " ".join(raw if isinstance(raw, (list, tuple)) else [str(raw or "")]).split()
            )
            if kind in ("text", "choice"):
                value = "" if value in _FIELD_UNSET else value
            checked = kind == "checkbox" and value not in _FIELD_UNSET
            taken.append((widget, FormField(kind, display.box(widget.rect), value, checked)))
        for widget, _ in taken:
            page.delete_widget(widget)
    except (RuntimeError, ValueError):
        pass  # a form this library cannot read is read as the page draws it
    return sorted((f for _, f in taken), key=lambda f: (round(f.box[1]), f.box[0]))


def _field_rows(fields: Sequence[FormField], rows: Sequence[_Row]) -> list[_Row]:
    """A row for each field, on the baseline and in the size of the line it stands in, so
    it merges with its label: an empty text field is a blank, a filled one its value, a
    checkbox its box with its state, a signature field a blank that is named. The text pass
    marks the blank and the box as runs (`text_pass.token_runs`)."""
    upright = [r for r in rows if not r.vertical]
    sizes = [r.size for r in upright]
    out: list[_Row] = []
    for f in fields:
        x0, y0, x1, y1 = f.box
        mid = (y0 + y1) / 2
        beside = [r for r in upright if r.bbox[1] <= mid <= r.bbox[3]]
        near = min(beside, key=lambda r: abs(r.x1 - x0), default=None)
        size = near.size if near else statistics.median(sizes) if sizes else 11.0
        baseline = near.baseline if near else mid + 0.35 * size
        if f.kind == "checkbox":
            shown = FIELD_BOXES[f.checked]
        elif f.kind == "signature":
            shown = FIELD_SIGNATURE
        else:
            shown = f.value or FIELD_BLANK
        step = min(0.5 * size, (x1 - x0) / len(shown)) if f.value else 0.5 * size
        chars = [
            _Char(
                c,
                x0 + i * step,
                baseline - 0.8 * size,
                x0 + (i + 1) * step,
                baseline + 0.2 * size,
                baseline=baseline,
                size=size,
                font="",
                flags=0,
            )
            for i, c in enumerate(shown)
        ]
        out.append(_Row(chars))
    return out


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
                diagonal = abs(dx) > 0.2 and abs(dy) > 0.2
                chars = mark_greek_words(chars)
                rows.append(
                    _Row(
                        chars if vertical or diagonal else mark_small_capitals(chars),
                        vertical,
                        diagonal=diagonal,
                    )
                )
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


# An inline fraction's bar is at most this many font sizes wide, and its numerator and
# denominator at most this far (x font size) above and below it.
MAX_INLINE_BAR = 8.0
INLINE_FRACTION_REACH = 1.1
PROSE_LETTERS_ON_LINE = 3  # plain letters on a bar's line that say it stands in running text


def _lift_stacked_fractions(frags: list[_Row], rules: Sequence[_Path]) -> list[_Row]:
    """Take the glyphs of inline stacked fractions out of the lines they were read into.

    A fraction set in a line of text ("of 1/5 of a pool", "P(x)/Q(x)") has a numerator and
    a denominator in a smaller size on either side of a thin bar. The text extractor cuts
    them into lines by position: the numerator ends up in the line above, the denominator
    in the line below or the next one, each beside the words of its line, and a fraction
    wider than its neighbour overlaps it and is never merged back. The inline maths then
    came out as two pieces ("P(x)" and "Q(x)"), or, for plain digits, not at all.

    For each short bar: the glyphs over it (numerator) and under it (denominator), in a
    smaller size than the text around the bar and not wider than the bar, are one
    stacked fragment (numerator, a space, denominator, each glyph marked `stacked`) on
    the baseline of the text beside the bar, so it merges into that line. A bar with
    glyphs on one side only (an underline, a table rule), with big glyphs on both (a
    fraction set at the size of its text: a display equation, handled by its rows), or
    wider than a fraction of this size, is left alone.
    """
    bars = [
        p
        for p in rules
        if p.kind == "hrule" and p.bbox[3] - p.bbox[1] <= 1.5 and p.bbox[2] - p.bbox[0] >= 2.0
    ]
    if not bars or not frags:
        return frags
    index = _RowIndex([f for f in frags if not f.vertical])
    # A bar with another bar among its numerator or denominator is a complex fraction
    # ("(1/2 + 1/3) / (1/4 - 1/5)"): it and the bars it holds are left as they were.
    found_at = {id(b): _fraction_at(b, index, set()) for b in bars}
    complex_bars: set[int] = set()
    for bar in bars:
        found = found_at[id(bar)]
        if found is None:
            continue
        glyphs = [c for _, _, c in (*found[0], *found[1])]
        top, bottom = min(c.y0 for c in glyphs), max(c.y1 for c in glyphs)
        for other in bars:
            overlap = min(bar.bbox[2], other.bbox[2]) - max(bar.bbox[0], other.bbox[0])
            if other is not bar and overlap > 0 and top <= other.bbox[1] <= bottom:
                complex_bars.update((id(bar), id(other)))
    claimed: set[int] = set()
    lifted: list[_Row] = []
    removed: dict[int, set[int]] = {}
    for bar in bars:
        found = None if id(bar) in complex_bars else _fraction_at(bar, index, claimed)
        if found is None:
            continue
        numerator, denominator, baseline = found
        for row, i, _ in (*numerator, *denominator):
            removed.setdefault(id(row), set()).add(i)
        claimed.update(id(c) for _, _, c in (*numerator, *denominator))
        top = sorted((c for _, _, c in numerator), key=lambda c: c.x0)
        bottom = sorted((c for _, _, c in denominator), key=lambda c: c.x0)
        last = top[-1]
        chars = [replace(c, stacked=True) for c in top]
        chars.append(
            _Char(
                " ", last.x1, last.y0, bottom[0].x0, last.y1, last.baseline, last.size, last.font, 0
            )
        )
        chars.extend(replace(c, stacked=True) for c in bottom)
        piece = _Row(chars)
        piece.__dict__["baseline"] = baseline  # the line of text it stands in
        lifted.append(piece)
    if not lifted:
        return frags
    return [*_without(frags, removed), *lifted]


def _fraction_at(
    bar: _Path, index: _RowIndex, claimed: set[int]
) -> tuple[list[tuple[_Row, int, _Char]], list[tuple[_Row, int, _Char]], float] | None:
    """(numerator glyphs, denominator glyphs, baseline of the text beside) of the inline
    fraction on `bar`, each glyph with its row and place in it; None when the bar is not
    one (see `_lift_stacked_fractions`)."""
    x0, y0, x1, y1 = bar.bbox
    middle = (y0 + y1) / 2
    reach = 1.6 * index.largest
    candidates: list[tuple[_Row, int, _Char]] = []
    beside: list[_Char] = []  # glyphs level with the bar, near it
    line: list[_Char] = []  # ... or anywhere on its line
    for row in index.near(middle - reach, middle + reach):
        for i, c in enumerate(row.chars):
            if c.blank or id(c) in claimed:
                continue
            centre_x = (c.x0 + c.x1) / 2
            if x0 - 1.0 <= centre_x <= x1 + 1.0:
                candidates.append((row, i, c))
            elif c.y0 <= middle <= c.y1:
                line.append(c)
                if abs(centre_x - (x0 + x1) / 2) <= 6 * c.size + (x1 - x0):
                    beside.append(c)
    # Only inside running text: a fraction in a display equation is read with its rows
    # (`_equation_regions`), and lifting it would cut those rows. Three plain letters on
    # the line of the bar are words.
    words = sum(c.c.isalpha() and not c.math for c in line)
    if words < PROSE_LETTERS_ON_LINE or not beside:
        # Words on the line but none near the bar: nothing to compare its glyphs with.
        return None
    # The text around the bar must be set bigger than the glyphs on it: that is what makes
    # it an inline fraction (a display fraction's own line has glyphs of the same size).
    text = max(c.size for c in beside)
    over: list[tuple[_Row, int, _Char]] = []
    under: list[tuple[_Row, int, _Char]] = []
    for row, i, c in candidates:
        if c.size > SCRIPT_RATIO * text:
            continue
        centre_y = (c.y0 + c.y1) / 2
        if 0 < middle - centre_y <= INLINE_FRACTION_REACH * c.size:
            over.append((row, i, c))
        elif 0 < centre_y - middle <= INLINE_FRACTION_REACH * c.size:
            under.append((row, i, c))
    if not over or not under:
        return None
    size = max(c.size for _, _, c in (*over, *under))
    glyphs = [c for _, _, c in (*over, *under)]
    width = x1 - x0
    covered = max(c.x1 for c in glyphs) - min(c.x0 for c in glyphs)
    if width > MAX_INLINE_BAR * size or covered < 0.6 * width or covered > width + 2.0:
        return None
    nearest = min(
        (
            c for c in beside if c.size >= size / SCRIPT_RATIO and c.c.isalnum()
        ),  # not a tall bracket
        key=lambda c: abs((c.x0 + c.x1) / 2 - (x0 + x1) / 2),
        default=None,
    )
    if nearest is None:
        return None
    return over, under, nearest.baseline


def _lift_stacked_pairs(frags: list[_Row]) -> list[_Row]:
    """Take the glyphs of inline binomials out of the lines they were read into.

    "( 5 over 3 )", a binomial coefficient or a column of two entries, has no bar: a
    bracket, two short pieces of text one above the other, a bracket. They arrive as
    three lines (the bracket and the numerator in the line of text, the denominator and
    the closing bracket in the next), and the maths came out cut in pieces, without its
    brackets and with half of its entries. The glyphs between a bracket and its closing
    one are the pair when nothing stands on the line of the brackets between them, one
    short token stands above it and one below, and the two overlap in x; they become one
    stacked fragment, like the glyphs of a fraction (`_lift_stacked_fractions`).
    """
    rows = [f for f in frags if not f.vertical]
    index = _RowIndex(rows)
    claimed: set[int] = set()
    lifted: list[_Row] = []
    removed: dict[int, set[int]] = {}
    for row in rows:
        for i, bracket in enumerate(row.chars):
            if bracket.c not in "([" or id(bracket) in claimed:
                continue
            found = _binomial_at(row, i, index, claimed)
            if found is None:
                continue
            members, baseline = found
            for owner, place, glyph in members:
                removed.setdefault(id(owner), set()).add(place)
                claimed.add(id(glyph))
            glyphs = [g for _, _, g in members]
            gap = next(k for k, g in enumerate(glyphs) if g.y0 > bracket.y0 + 0.25 * bracket.size)
            chars = [replace(g, stacked=True) for g in glyphs]
            before = chars[gap - 1]
            chars.insert(  # a space between the top and the bottom
                gap,
                _Char(
                    " ",
                    before.x1,
                    before.y0,
                    before.x1,
                    before.y1,
                    before.baseline,
                    before.size,
                    before.font,
                    0,
                ),
            )
            piece = _Row(chars)
            piece.__dict__["baseline"] = baseline
            lifted.append(piece)
    if not lifted:
        return frags
    return [*_without(frags, removed), *lifted]


def _without(frags: list[_Row], removed: dict[int, set[int]]) -> list[_Row]:
    """`frags` without the characters at the given places (a row left with no ink goes)."""
    out: list[_Row] = []
    for frag in frags:
        gone = removed.get(id(frag))
        if not gone:
            out.append(frag)
            continue
        kept = [c for i, c in enumerate(frag.chars) if i not in gone]
        if any(not c.blank for c in kept):
            out.append(
                _Row(kept, vertical=frag.vertical, diagonal=frag.diagonal, column=frag.column)
            )
    return out


def _binomial_at(
    row: _Row, place: int, index: _RowIndex, claimed: set[int]
) -> tuple[list[tuple[_Row, int, _Char]], float] | None:
    """The glyphs (bracket, top, bottom, closing bracket, each with its row and place) of
    the inline binomial that the opening bracket at `place` of `row` starts, with the
    baseline of the text beside it; None when it starts none."""
    opening = row.chars[place]
    closing = ")" if opening.c == "(" else "]"
    size = opening.size
    middle = (opening.y0 + opening.y1) / 2
    near: list[tuple[_Row, int, _Char]] = []
    for other in index.near(opening.y0 - 1.3 * size, opening.y1 + 1.3 * size):
        for i, c in enumerate(other.chars):
            if c.blank or c is opening or id(c) in claimed:
                continue
            centre_x = (c.x0 + c.x1) / 2
            if (
                opening.x1 - 0.3 * size <= centre_x <= opening.x1 + 8 * size
                and c.y1 >= opening.y0 - 0.5 * size  # within reach of the bracket's height
                and c.y0 <= opening.y1 + 0.5 * size
            ):
                near.append((other, i, c))
    ends = [
        m for m in near if m[2].c == closing and abs((m[2].y0 + m[2].y1) / 2 - middle) <= 0.6 * size
    ]
    if not ends:
        return None
    end = min(ends, key=lambda m: m[2].x0)
    between = [m for m in near if m[2].x1 <= end[2].x0 + 0.3 * size and m is not end]
    above = [m for m in between if (m[2].y0 + m[2].y1) / 2 < middle - 0.25 * size]
    below = [m for m in between if (m[2].y0 + m[2].y1) / 2 > middle + 0.25 * size]
    if not above or not below or len(above) + len(below) != len(between):
        return None  # something stands on the line of the brackets, or one side is empty
    if len(above) > 6 or len(below) > 6 or not (_one_token(above) and _one_token(below)):
        return None
    shared = min(max(m[2].x1 for m in above), max(m[2].x1 for m in below)) - max(
        min(m[2].x0 for m in above), min(m[2].x0 for m in below)
    )
    narrow = min(
        max(m[2].x1 for m in above) - min(m[2].x0 for m in above),
        max(m[2].x1 for m in below) - min(m[2].x0 for m in below),
    )
    if shared < 0.5 * narrow:
        return None
    left = min(m[2].x0 for m in between)
    right = max(m[2].x1 for m in between)
    if left - opening.x1 > size or end[2].x0 - right > size:
        return None
    text = [
        c
        for r in index.near(middle, middle)
        for c in r.chars
        if c.c.isalnum()
        and c.y0 <= middle <= c.y1
        and c.size >= 0.9 * size
        and c not in [m[2] for m in between]
    ]
    nearest = min(text, key=lambda c: abs((c.x0 + c.x1) / 2 - opening.x0), default=None)
    baseline = nearest.baseline if nearest is not None else opening.baseline
    key = lambda m: m[2].x0  # noqa: E731
    members = [
        (row, place, opening),
        *sorted(above, key=key),
        *sorted(below, key=key),
        end,
    ]
    return members, baseline


def _one_token(glyphs: list[tuple[_Row, int, _Char]]) -> bool:
    """Do the glyphs form one token: no gap between neighbours of 0.3 x their size?"""
    chars = sorted((m[2] for m in glyphs), key=lambda c: c.x0)
    return all(b.x0 - a.x1 < 0.3 * a.size for a, b in pairwise(chars))


def _merge_baselines(
    frags: list[_Row], gutter: tuple[float, float] | None, rules: Sequence[_Path] = ()
) -> list[_Row]:
    """Merge fragments that share a baseline into rows, never across the column gutter."""
    frags = _lift_stacked_pairs(_lift_stacked_fractions(frags, rules))
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
            lead, rest = (
                _leading_script(frag, current.size) if gap < 0 and not crosses else (None, None)
            )
            if lead is not None and _stacked_tail(current, lead):
                # The lowered half of a stacked pair (the 4 of SO4 2-) under the raised half
                # that ends the row: it goes in beside it, not after the row's text.
                after = rest.chars if rest is not None else ()
                current = _Row(
                    _insert_script(current.chars, lead.chars, after), column=current.column
                )
                if rest is None:
                    continue
                frag = rest
                gap = frag.x0 - current.x1
            if (
                (-0.5 * current.size <= gap <= 4 * max(current.size, frag.size) and not crosses)
                or (gap < 0 and not crosses and _label_over_arrow(current, frag))
                or (not crosses and gap >= 0 and _half_reaction_label(current, frag))
            ):
                current = _join_rows(current, frag)
            else:
                rows.append(current)
                current = frag
        rows.append(current)
    rows.extend(f for f in frags if f.vertical)
    for row in rows:
        row.column = _column_of(row.bbox, gutter)
    return _attach_big_operators(_attach_scripts(rows))


def _label_over_arrow(current: _Row, frag: _Row) -> bool:
    """Does `frag` start with a reaction arrow that has a label over it, the label being the
    small glyphs `current` ends with? TeX sets `\\xrightarrow{\\Delta}` with the label raised
    over the arrow: the text layer has one line end at the label and the next start at the
    arrow, and the two overlap by the width of the label. Without this the reaction is cut in
    three (docs/chemistry.md, "Accuracy"). Only a label no larger than a script, ending the
    row, with the arrow reaching under it: a long arrow (`-----→`) as well as `−→`."""
    ink = frag.ink
    head = next((i for i, c in enumerate(ink) if c.c in chem.ARROW_HEADS), None)
    if head is None or any(c.c not in _CHEM_DASHES for c in ink[:head]):
        return False
    label: list[_Char] = []
    for c in reversed(current.ink):
        if c.size > SCRIPT_RATIO * current.size:
            break
        label.append(c)
    if not label:
        return False
    x0, x1 = min(c.x0 for c in label), max(c.x1 for c in label)
    return frag.x0 <= x1 + 0.2 * current.size and ink[head].x1 >= x0 - 0.5 * current.size


HALF_REACTION_GAP = 12  # x glyph size: the widest gap between a half-reaction's label and it

_HALF_REACTION_LABEL = re.compile(
    r"^(?=[^:]*\b(?:anode|cathode|overall|oxidation|reduction|net|reaction|equation)\b)"
    r"[A-Za-z][A-Za-z()\- ]*:$",
    re.IGNORECASE,
)


def _half_reaction_label(current: _Row, frag: _Row) -> bool:
    """Is `current` the label of the half-reaction or reaction that `frag` holds ("anode
    (oxidation):", "Overall:"), set apart from it by more than a word space? A short phrase
    ending in a colon that names a half-reaction, with a reaction arrow in the piece that
    follows it on the same baseline: one line of the half-reaction block, not a caption and
    an equation."""
    if frag.x0 - current.x1 > HALF_REACTION_GAP * max(current.size, frag.size):
        return False
    text = current.clean
    return (
        len(text.split()) <= 4
        and _HALF_REACTION_LABEL.match(text) is not None
        and any(c.c in chem.ARROW_HEADS for c in frag.chars)
    )


MAX_ARROW_LABEL = 40  # characters: the longest label set over an arrow that is kept with it


def _arrow_under(host: _Row, label: _Row) -> int | None:
    """Index in `host.chars` of the head of the arrow that the small row `label` is set over
    or under (a reaction condition: `\\xrightarrow{high temperature}`), or None."""
    chars = host.chars
    for span in _arrow_spans(chars):
        if all(_over_arrow(chars, span, c) for c in label.ink):
            return span[1]
    return None


def _insert_label(host: list[_Char], label: list[_Char], head: int) -> list[_Char]:
    """`label`'s glyphs after the arrow whose head is at `head`, and after the labels that
    already stand there (the one over the arrow, then the one under it): not inside the
    shaft where an x-position would put them."""
    at = head + 1
    span = next(s for s in _arrow_spans(host) if s[1] == head)
    k = at
    while k < len(host) and (host[k].blank or _over_arrow(host, span, host[k])):
        k += 1
        if not host[k - 1].blank:
            at = k
    # A space on each side: the label is words of its own, not the end of the arrow's word.
    gap = replace(label[-1], c=" ", x0=label[-1].x1, y0=label[-1].y0, x1=label[-1].x1)
    before = [] if at == 0 or host[at - 1].blank else [replace(gap, x0=label[0].x0, x1=label[0].x0)]
    after = [] if at == len(host) or host[at].blank else [gap]
    return [*host[:at], *before, *label, *after, *host[at:]]


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
        long = len(small.clean) > 12
        if small.vertical or (long and len(small.clean) > MAX_ARROW_LABEL) or i in merged:
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
            if glyph < SCRIPT_ROW_FLOOR * host.size:
                continue  # words set small over a far larger one (a slide's word art)
            host = merged.get(j, host)
            near_y = host.y0 - 0.5 * host.size <= centre <= host.y1 + 0.5 * host.size
            near_x = host.x0 - host.size <= small.x0 and small.x1 <= host.x1 + 2 * host.size
            if near_y and near_x and host.column == small.column:
                if long and _arrow_under(host, small) is None:
                    continue  # only an arrow's label is a script that long
                best = j
                break
        if best is not None:
            host = merged.get(best, rows[best])
            head = _arrow_under(host, small)
            if head is not None:
                chars = _insert_label(host.chars, small.chars, head)
            else:
                chars = host.chars
                # Degree signs set apart (E°cell = E°cathode - E°anode) are each a script of
                # of their own, not one run.
                degrees = len(small.ink) > 1 and all(c.c in _DEGREE_SIGNS for c in small.ink)
                for part in [[c] for c in small.ink] if degrees else [small.chars]:
                    chars = _insert_script(chars, part)
            merged[best] = _Row(chars, column=host.column)
            dropped.add(i)
    return [merged.get(i, row) for i, row in enumerate(rows) if i not in dropped]


def _tall_bracket(row: _Row) -> bool:
    """Is the row a single bracket glyph? A bracket that is a row of its own is one the
    line's baseline did not take in: TeX sets a tall bracket (a CMEX glyph) lowered, on a
    baseline of its own, round a fraction or a stack, as in d/dx (7x^3 - 15x^2)."""
    return len(row.ink) == 1 and row.ink[0].c in _BRACKETS


LIMIT_GLYPHS = 8  # the glyphs of one limit of a sign, at most ("Ns/2−1", "i,j=1")


def _sign_of_limits(row: _Row) -> _Char | None:
    """The sum, product or integral sign of a row that is that sign and its limits alone: one
    sign, and every other glyph a script-size one set over it or under it (its centre is above
    the sign's top or below its bottom), at most `LIMIT_GLYPHS` on each side. The tall
    brackets round a sum, "(∑ )−1", are no limits: they are the sign's size."""
    ink = row.ink
    signs = [c for c in ink if c.c in LIMIT_SIGNS]
    if len(signs) != 1 or len(ink) < 2:
        return None
    sign = signs[0]
    rest = [c for c in ink if c is not sign]
    if any(c.size > SCRIPT_RATIO * sign.size for c in rest):
        return None
    centres = [(c.y0 + c.y1) / 2 for c in rest]
    over = sum(centre <= sign.y0 for centre in centres)
    under = sum(centre >= sign.y1 for centre in centres)
    if over + under < len(centres) or over > LIMIT_GLYPHS or under > LIMIT_GLYPHS:
        return None
    return sign


def _attach_big_operators(rows: list[_Row]) -> list[_Row]:
    """Fold a short fragment led by a big operator, or a tall bracket, into the line it
    sits in.

    TeX sets the integral sign of an inline $\\int_0^1 x^2\\,dx$ (and a text-style sum or
    root) on a baseline of its own, so it arrives as a separate row of an integral sign
    and its upper limit in the middle of a line of text. Left there, it becomes a display
    equation of its own and the inline equation's crop misses the sign. A fragment of at
    most four glyphs with a big operator in it joins the row whose vertical extent holds
    its centre and whose horizontal extent it lies within, before the first glyph to its
    right. So does a lone bracket (`_tall_bracket`): the opening bracket of
    "d/dx (7x^3 - 15x^2)" stood in a row of its own, and the inline equation lost it and
    kept the closing one, which was then cut off as unmatched.

    A limit set over or under the sign and folded onto it (`_attach_scripts`) makes the
    fragment longer and moves its centre off the line. So a sign with its limits alone
    (`_sign_of_limits`) is a fragment whatever its length, placed where the sign stands, and
    a fragment that holds a sign that takes limits (`LIMIT_SIGNS`) and a glyph smaller than
    that sign, which is how a limit in it shows, looks at the centre of the sign when it finds
    no row at its own centre. (A root takes no limits and has a smaller glyph under it: it is
    not looked at twice, or the denominator "√tn" of a fraction was read between the terms of
    its numerator. One that reaches into a row at its own centre without lying within it stays
    where it is: the sum of a fraction's numerator, whose row of terms begins under its raised
    script, is no part of the line under it. And a sign with nothing but a bracket before it,
    "(∑", has no limit that moved its centre: it stays too.)
    One that finds no line at all may still stand in a row of a
    sign and its limits: the product and the sum before an integral whose row took their
    limits. The line keeps the size it had before the limits came into it: the sign carries
    it (`_Char.line_size`).
    """
    signs = {i: _sign_of_limits(r) for i, r in enumerate(rows) if not r.vertical}
    pieces = [
        i
        for i, r in enumerate(rows)
        if not r.vertical
        and (
            (len(r.ink) <= 4 and any(c.c in _BIG_OPERATORS for c in r.ink))
            or _tall_bracket(r)
            or signs[i] is not None
        )
    ]
    if not pieces:
        return rows
    piece_ids = set(pieces)
    lines = sorted((r.y0, i) for i, r in enumerate(rows) if i not in piece_ids and not r.vertical)
    sign_rows = sorted((rows[i].y0, i) for i in pieces if signs[i] is not None)
    merged: dict[int, _Row] = {}
    dropped: set[int] = set()
    for i in pieces:
        piece = merged.get(i, rows[i])
        best, overlap = None, 0.0
        sign = signs[i]
        centres = [(piece.y0 + piece.y1) / 2]
        big = next((c for c in piece.ink if c.c in LIMIT_SIGNS), None)
        if big is not None and any(c.size < big.size - 0.5 for c in piece.ink):
            centres.append((big.y0 + big.y1) / 2)
        for hosts in (lines, [(y, j) for y, j in sign_rows if j != i and j not in dropped]):
            tops = [y for y, _ in hosts]
            tallest = max((merged.get(j, rows[j]).height for _, j in hosts), default=0.0)
            for centre in centres:
                lo = bisect.bisect_left(tops, centre - tallest)
                beside = False
                for _, j in hosts[lo : bisect.bisect_right(tops, centre)]:
                    host = merged.get(j, rows[j])
                    reach = 0.5 * host.size
                    start = min(host.x0, piece.x0) if _starts_at(host, piece) else host.x0
                    inside = start - reach <= piece.x0 and piece.x1 <= host.x1 + reach
                    shared = min(piece.y1, host.y1) - max(piece.y0, host.y0)
                    on_line = host.y0 <= centre <= host.y1 and host.column == piece.column
                    beside = beside or (on_line and piece.x0 < host.x1 and host.x0 < piece.x1)
                    if on_line and inside and shared > overlap:
                        best, overlap = j, shared
                if best is not None or beside:
                    break
            if best is not None:
                break
        if best is not None:
            host = merged.get(best, rows[best])
            left = piece.x0 if sign is None or len(piece.ink) <= 4 or i in merged else sign.x0
            at = next((k for k, c in enumerate(host.chars) if c.x0 >= left), len(host.chars))
            folded = piece.chars
            if sign is not None and best not in piece_ids:
                kept = replace(sign, line_size=host.size)
                folded = [kept if c is sign else c for c in folded]
            merged[best] = _Row([*host.chars[:at], *folded, *host.chars[at:]], column=host.column)
            dropped.add(i)
    return [merged.get(i, row) for i, row in enumerate(rows) if i not in dropped]


def _starts_at(host: _Row, piece: _Row) -> bool:
    """Does `host` begin where a sum, product or integral sign of `piece` ends, no further than
    half an em beyond it? A sign that leads a line comes as a piece of its own, and the row of
    what goes on starts with the lower limit TeX lowers to the baseline of the line, or with the
    integrand, so that row seems to start a sign's width after the sign (arXiv 2411.02531 page 1,
    2212.05955 page 13)."""
    first = next((c for c in host.chars if not c.blank), None)
    return first is not None and any(
        c.c in LIMIT_SIGNS and c.x0 <= first.x0 <= c.x1 + 0.5 * host.size for c in piece.ink
    )


def _insert_script(
    host: list[_Char], script: list[_Char], after: Sequence[_Char] = ()
) -> list[_Char]:
    """`script`'s characters as one block, after the host glyphs that start before its
    right edge. A sub/superscript lands right after its base; the denominator of a small
    fraction lands after its numerator instead of being interleaved with it by x.

    A lowered script under a raised one (the 4 under the 2- of SO4 2-) is read first: it
    lands before the raised script it sits under, when that follows a base. Before a
    nuclide's symbol the raised one comes first (the mass number over the atomic number)."""
    end = max(c.x1 for c in script)
    at = 0
    for i, c in enumerate(host):
        if not c.blank and c.x0 < end - 0.01:
            at = i + 1
    at = _stack_position(host, at, script, after)
    return [*host[:at], *script, *host[at:]]


def _stack_position(
    host: list[_Char], at: int, script: list[_Char], after: Sequence[_Char] = ()
) -> int:
    """Where `script` goes among the glyphs of `host`, `at` being just after the glyphs
    that start before its right edge. When it lies over or under small glyphs the host
    already holds (a stacked pair), it goes before them or after them so that the pair
    reads lowered first after a base (SO4 2-) and raised first before a symbol (the mass
    number before the atomic number of a nuclide). Otherwise `at`."""
    main = max((c.size for c in host), default=0.0)
    first = at
    while first > 0 and not host[first - 1].blank and host[first - 1].size <= SCRIPT_RATIO * main:
        first -= 1
    last = at
    while last < len(host) and not host[last].blank and host[last].size <= SCRIPT_RATIO * main:
        last += 1  # the rest of the small glyphs the script lies over
    cluster = host[first:last]
    left, right = min(c.x0 for c in script), max(c.x1 for c in script)
    if not cluster or not any(c.x1 > left and c.x0 < right for c in cluster):
        return at
    level = statistics.mean(c.baseline for c in cluster)
    mine = statistics.mean(c.baseline for c in script)
    if abs(mine - level) < 0.05 * main:
        return at
    # After a base of upright text (SO4 2-) lowered first; before the capital letter of a
    # symbol (a nuclide) raised first. Any other stack (a variable's v_0^2 in a maths font, a
    # small fraction) keeps the order of the text layer, as before, unless that order would put
    # the script inside a name after a base.
    base = host[first - 1] if first else None
    after_base = base is not None and (_text_char(base) or base.c in ")]}")
    following = next((c for c in [*host[last:], *after] if not c.blank), None)
    before_symbol = following is not None and following.c.isupper() and _text_char(following)
    if after_base and before_symbol:  # either: the closer one
        assert base is not None and following is not None
        end = max(right, *(c.x1 for c in cluster))
        prefix = following.x0 - end < min(c.x0 for c in cluster) - base.x1
    elif after_base or before_symbol:
        prefix = before_symbol and not after_base
    elif _degree_over_label(script, first, at, last):
        return last  # E°cell: the degree sign does not cut "cell" in two
    elif base is not None and first < at < last and all(c.mono for c in cluster):
        # A typewriter name of several small glyphs (the KRR of a filter's superscript) with a
        # lowered script under it: the script is a script of the whole name, not a letter of it,
        # and the lowered one is read first (the order of SO4 2-). Names in other fonts (the ref
        # of R_n^ref) keep the order of the text layer.
        return first if mine > level else last
    else:
        return at
    script_lower = mine > level
    return first if script_lower != prefix else last


def _degree_over_label(script: Sequence[_Char], first: int, at: int, last: int) -> bool:
    """Is `script` a lone degree sign that the x-order would put inside the word that is the
    lowered label of a symbol (the `cell` of E°cell, `cathode`, `anode`)? TeX sets it over the
    start of the label, so the text layer read `E c°ell`; the label stays whole, the degree
    sign after it (the order of `E_{cell}^{°}`)."""
    return (
        len(script) == 1
        and script[0].c in _DEGREE_SIGNS
        and last - first >= 2
        and first < at < last
    )


_DEGREE_SIGNS = frozenset("°∘")


def _leading_script(frag: _Row, size: float) -> tuple[_Row | None, _Row | None]:
    """(the glyphs that start `frag` and are small beside text of `size`, the rest of it or
    None). MuPDF starts a line with the lowered half of a stacked pair when the line goes on
    at a slightly different baseline. (None, None) when `frag` does not start with a few
    small glyphs."""
    if frag.vertical:
        return None, None
    limit = SCRIPT_RATIO * size
    cut = 0
    while cut < len(frag.chars):
        c = frag.chars[cut]
        if c.blank or c.small_cap or c.size > limit:
            break
        cut += 1
    if cut == 0 or cut > 6:
        return None, None
    lead = _Row(frag.chars[:cut], column=frag.column)
    rest = frag.chars[cut:]
    return lead, (_Row(rest, column=frag.column) if any(not c.blank for c in rest) else None)


def _stacked_tail(row: _Row, frag: _Row) -> bool:
    """Is `frag` a small fragment lying under the small glyphs that end `row`: the lowered
    half of a stacked pair whose raised half the line already holds?"""
    if row.vertical or frag.vertical or len(frag.clean) > 12:
        return False
    glyph = max(c.size for c in frag.ink)
    if not SCRIPT_MIN_RATIO * row.size <= glyph <= SCRIPT_RATIO * row.size:
        return False  # not script-sized: the numbers of a fraction are half the size
    tail = [c for c in row.ink[-4:] if c.size <= SCRIPT_RATIO * row.size and not c.small_cap]
    return any(c.x1 > frag.x0 + 0.5 and c.x0 < frag.x1 for c in tail) and (
        frag.x0 >= row.x1 - 4 * frag.size
    )


def _column_of(box: BBox, gutter: tuple[float, float] | None) -> str:
    if gutter is None:
        return "L"
    split = (gutter[0] + gutter[1]) / 2
    if box[0] < split - 2 and box[2] > split + 2:
        return "S"
    return "L" if (box[0] + box[2]) / 2 < split else "R"


def _text_char(c: _Char) -> bool:
    """A letter or digit of upright text: not italic, not in a maths font, not a maths
    alphanumeric. The base of H2O, Na+, cm3, 226Ra; a variable (an italic x, a letter in
    CMMI) is not one, and keeps its scripts in an equation."""
    if c.blank or c.italic or is_math_alphanumeric(c.c) or _font_traits(c.font)[2]:
        return False
    return c.c.isalnum()


@dataclass(frozen=True, slots=True)
class Glyph:
    """One printed character with the colour of its span, for the text pass
    (`pipeline.text_pass`), which finds links and coloured text by where glyphs are and
    maps them onto `Element.text`."""

    c: str
    bbox: BBox  # display coordinates, the frame of `Element.bbox`
    rgb: tuple[int, int, int]  # the span's fill colour, sRGB
    size: float = 0.0  # the span's font size in points (0: not known)
    baseline: float = 0.0  # y of the glyph's origin (`baseline_rules.classify`)
    # The face's weight, slant and pitch (`face_traits`), for the runs of emphasis.
    bold: bool = False
    italic: bool = False
    mono: bool = False


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
    for block in _text_blocks(page):
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if page_type.span_hidden(span):
                    continue
                colour = int(span.get("color") or 0) & 0xFFFFFF
                rgb = ((colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF)
                size = float(span.get("size") or 0.0)
                traits = face_traits(str(span.get("font") or ""), int(span.get("flags") or 0))
                for ch in span["chars"]:
                    c = ch["c"].translate(_TEXT_MAP)
                    if c and not c.isspace():
                        origin = display.point(*ch["origin"])
                        box = display.box(ch["bbox"])
                        glyphs.append(Glyph(c[0], box, rgb, size, origin[1], *traits))
    return glyphs


# A mark that may strike text through (`page_strikes`): a horizontal line or a filled bar at
# most this thick. The text pass decides, glyph by glyph, whether it crosses text where a
# strikeout does (`text_pass.struck_glyphs`).
STRIKE_MAX_PT = 3.0


@dataclass(frozen=True, slots=True)
class Strike:
    """A thin horizontal mark on a page (`annotation` False: drawn; its box is the line with
    its thickness), or the area of a StrikeOut annotation (`annotation` True: its box holds
    the struck glyphs). Display coordinates, like `Glyph.bbox`."""

    bbox: BBox
    annotation: bool = False


# A filled rectangle that may be a highlight behind text (`page_paint`): at most this tall and
# wide, so a shaded box around a block never is one. The text pass decides, from the glyphs it
# holds, whether it hugs one line of text (`text_pass.highlighted_glyphs`).
HIGHLIGHT_MAX_PT = (36.0, 600.0)


@dataclass(frozen=True, slots=True)
class Highlight:
    """The area of a Highlight annotation (`rgb` None: its box holds the marked glyphs), or a
    filled rectangle that may sit behind text, with its fill colour. Display coordinates."""

    bbox: BBox
    rgb: tuple[int, int, int] | None = None


def _quads(annot: pymupdf.Annot, display: _Display) -> list[BBox]:
    """The boxes of an annotation's quads, in display coordinates."""
    points = list(annot.vertices or ())
    boxes: list[BBox] = []
    for i in range(0, len(points) - 3, 4):  # four points per quad, unrotated page space
        xs = [float(x) for x, _ in points[i : i + 4]]
        ys = [float(y) for _, y in points[i : i + 4]]
        boxes.append(display.box((min(xs), min(ys), max(xs), max(ys))))
    return boxes


def page_paint(
    page: pymupdf.Page, *, figure_art: bool = False
) -> tuple[list[Strike], list[Highlight]]:
    """The marks on `page` that may strike text through and the ones that may highlight it,
    for the text pass, from one read of the page's drawings and annotations.

    Strikes: every visible horizontal line (its box is the stroke's width around it) or filled
    bar no thicker than `STRIKE_MAX_PT`, and the quads of every StrikeOut annotation.
    Highlights: the quads of every Highlight annotation, and every visible filled rectangle
    within `HIGHLIGHT_MAX_PT`, with its colour. A page too complex to read its drawings
    (`page_type.too_complex`) gives its annotations only. With `figure_art` (`LAYOUT_FIGURE_ART`)
    the drawings are read as layout reads them (`page_type.page_drawings`): a line or a fill that
    a clip lets nowhere through is no strike and no highlight. The caller holds `_LOCK`."""
    display = _Display.of(page)
    marks: list[Strike] = []
    lit: list[Highlight] = []
    for annot in page.annots(types=[pymupdf.mupdf.PDF_ANNOT_STRIKE_OUT]) or ():
        marks.extend(Strike(box, True) for box in _quads(annot, display))
    for annot in page.annots(types=[pymupdf.mupdf.PDF_ANNOT_HIGHLIGHT]) or ():
        lit.extend(Highlight(box) for box in _quads(annot, display))
    drawings = page_type.page_drawings(page) if figure_art else page.get_cdrawings()
    if page_type.too_complex(drawings):
        return marks, lit
    seen = page_type.paint_visibility(drawings)
    for drawing, (filled, stroked) in zip(drawings, seen, strict=True):
        items = drawing.get("items") or []
        if filled and drawing.get("fill") and _filled_rectangle(drawing):
            x0, y0, x1, y1 = (float(v) for v in drawing["rect"])
            if y1 - y0 <= HIGHLIGHT_MAX_PT[0] and x1 - x0 <= HIGHLIGHT_MAX_PT[1]:
                r, g, b = (round(float(v) * 255) for v in drawing["fill"][:3])
                lit.append(Highlight(display.box((x0, y0, x1, y1)), (r, g, b)))
        if any(item[0] == "c" for item in items):
            continue  # a curve: not a rule
        x0, y0, x1, y1 = (float(v) for v in drawing["rect"])
        if filled and y1 - y0 <= STRIKE_MAX_PT and x1 - x0 >= 2 * max(y1 - y0, 0.5):
            marks.append(Strike(display.box((x0, y0, x1, y1))))
            continue
        if not stroked:
            continue
        half = max(float(drawing.get("width") or 1.0), 0.1) / 2
        if 2 * half > STRIKE_MAX_PT:
            continue
        for item in items:
            if item[0] != "l":
                continue
            (px, py), (qx, qy) = item[1], item[2]
            if abs(py - qy) <= 0.25 and abs(px - qx) >= 2.0:
                y = (py + qy) / 2
                box = (min(px, qx), y - half, max(px, qx), y + half)
                marks.append(Strike(display.box(box)))
    return marks, lit


def _filled_rectangle(drawing: dict[str, Any]) -> bool:
    """The drawing is one axis-aligned rectangle (a `re` item, or four straight lines)."""
    items = drawing.get("items") or []
    if len(items) == 1:
        return items[0][0] == "re"
    return len(items) == 4 and all(item[0] == "l" for item in items)
