"""Reconcile the page math pass with layout's rules (ADR 0006).

`PageMathHook` is what `layout.analyze(page_math=...)` calls for each page, just before
layout groups the page's maths rows into display equations:

- `on_page` gives the page's text rows their ids and quote text (`page_math.PageRow`),
  which is what the model is shown, and, when there is a verified plan, lays its spans
  over the rows:

  * a row is cut at a span's offsets, so exercise numbers, equation numbers and the words
    beside an equation become rows of their own;
  * a block span becomes one display region over its rows, however layout would have
    stacked the lines (a system, an aligned derivation), with the fraction bars and radicals
    inside its box; the rows the spans did not take go back through layout's own rules;
  * an equation number goes to the nearest placed region as its label (layout's
    `equation_label`), or is dropped from the maths when none is beside it;
  * an inline span is marked on its row (`_Row.page_inline`), and `layout._paragraph_pieces`
    turns it into an inline equation at those characters, a lone variable included;
  * what the pass called a look-alike of maths is marked (`_Row.page_nonmath`) and layout's
    inline runs inside it are dropped.

- `after_regions` marks the regions layout found by its own rules on a page the pass
  read as `layout` ("the pass did not confirm"), which later lowers their confidence.

Nothing here changes what text the document says: rows are cut where the plan says, and
the text of every row piece is still the PDF's. A page whose rows no longer read as they did
when the pass looked at them is left as layout has it.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from readablestem.pipeline import equation_step, layout
from readablestem.pipeline.page_math import MathPlan, PageRow, Segment, Span
from readablestem.pipeline.validate import LatexConversionError, safe_latex_to_mathml

log = logging.getLogger(__name__)

# Rules this far outside a placed region's rows still belong to it (points).
RULE_MARGIN = 2.0
# A span is laid over layout only when its LaTeX reading and the text it quotes have the same
# letters, digits and Greek letters, by Dice similarity of the two symbol bags: a reading that
# says more (or less) than the characters quoted means the quote cut the expression short or
# wide, and layout's own cut is kept.
CONSISTENT_FROM = 0.8


def quote_text(row: Any) -> tuple[str, list[int]]:
    """The text of a layout row as the page pass quotes it, and for each character of it the
    index of the layout character it came from. Control characters are dropped and runs of
    blanks become one space; the ends are trimmed."""
    out: list[str] = []
    index: list[int] = []
    for i, char in enumerate(row.chars):
        for ch in char.c.translate(layout._TEXT_MAP):
            if ch.isspace():
                if not out or out[-1] == " ":
                    continue
                out.append(" ")
                index.append(i)
            elif unicodedata.category(ch) == "Cc":
                continue
            else:
                out.append(ch)
                index.append(i)
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def page_rows(ordered: list[Any]) -> tuple[list[PageRow], dict[str, Any]]:
    """The rows the model is shown for a page, in reading order, and the layout row of each
    id. Rotated rows and rows with no text are not offered."""
    rows: list[PageRow] = []
    objects: dict[str, Any] = {}
    for row in ordered:
        if row.vertical or row.diagonal:
            continue
        text, index = quote_text(row)
        if not text:
            continue
        row_id = f"L{len(rows)}"
        rows.append(PageRow(row_id, text, tuple(index)))
        objects[row_id] = row
    return rows, objects


@dataclass
class _Cut:
    lo: int  # layout character index, inclusive
    hi: int  # exclusive
    kind: str  # "math" | "number" | "label"
    span: int  # index of the span in the page's list


@dataclass
class HookStats:
    """What the hook did, content-free."""

    pages_seen: int = 0
    pages_applied: int = 0
    pages_stale: int = 0
    blocks: int = 0
    inline: int = 0
    numbers: int = 0
    labels: int = 0
    nonmath: int = 0
    skipped: int = 0
    inconsistent: int = 0  # spans whose LaTeX and quoted text disagree: left to layout
    absorbed: int = 0  # bracket and rule rows with no symbols taken into a placed region
    refused: int = 0  # block spans that would split a layout region: layout's cut is kept

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class PageMathHook:
    """Collects each page's rows and/or applies a plan (`layout.analyze(page_math=hook)`)."""

    plan: MathPlan | None = None
    rows: dict[int, list[PageRow]] = field(default_factory=dict)
    stats: HookStats = field(default_factory=HookStats)
    _checked: set[int] = field(default_factory=set)

    # -------------------------------------------------------------- layout's two calls

    def on_page(self, number: int, text_rows: list[Any], ordered: list[Any], rules: list[Any]):
        rows, objects = page_rows(ordered)
        self.rows[number] = rows
        self.stats.pages_seen += 1
        page = self.plan.pages.get(number) if self.plan is not None else None
        if page is None or page.failed is not None:
            return text_rows
        if [(r.id, r.text) for r in page.rows] != [(r.id, r.text) for r in rows]:
            self.stats.pages_stale += 1
            log.warning("page math: page %d reads differently than when it was asked", number)
            return text_rows
        self._checked.add(number)
        for row in text_rows:
            row.page_checked = True
        return self._apply(page.spans, rows, objects, text_rows, rules)

    def after_regions(self, number: int, text_rows: list[Any]) -> None:
        if number not in self._checked:
            return
        for row in text_rows:
            region = row.region
            if region is not None and region.page_pass is None:
                region.page_pass = "layout"

    # ----------------------------------------------------------------------- applying

    def _apply(
        self,
        spans: list[Span],
        rows: list[PageRow],
        objects: dict[str, Any],
        text_rows: list[Any],
        rules: list[Any],
    ) -> list[Any]:
        by_id = {r.id: r for r in rows}
        kept: list[Span] = []
        for span in spans:
            if span.role == "math" and not _consistent(span, by_id):
                self.stats.inconsistent += 1
                continue
            kept.append(span)
        spans = kept
        cuts: dict[int, list[_Cut]] = {}  # id(layout row) -> cuts
        inline: list[tuple[str, int, int, str]] = []
        nonmath: list[tuple[str, int, int]] = []

        def chars(seg: Segment) -> tuple[int, int]:
            row = by_id[seg.row]
            return row.chars[seg.start], row.chars[seg.end - 1] + 1

        blocks = [i for i, s in enumerate(spans) if s.role == "math" and s.display == "block"]
        block_rows = {seg.row for i in blocks for seg in spans[i].segments}
        for i, span in enumerate(spans):
            if i in blocks:
                continue
            for seg in span.segments:
                lo, hi = chars(seg)
                row = objects[seg.row]
                if span.role in ("exercise_or_item_number", "equation_number"):
                    # Only where it numbers maths: a row layout reads as maths, or one that
                    # holds a block span. A list marker is layout's own business.
                    if row.marker is None and (row.kind == "math" or seg.row in block_rows):
                        kind = "number" if span.role == "exercise_or_item_number" else "label"
                        cuts.setdefault(id(row), []).append(_Cut(lo, hi, kind, i))
                    else:
                        self.stats.skipped += 1
                elif span.role == "math":  # inline
                    lo, hi = _with_scripts(row, lo, hi)
                    inline.append((seg.row, lo, hi, span.latex))
                else:
                    nonmath.append((seg.row, lo, hi))
        for i in self._screen_blocks(blocks, spans, by_id, objects, text_rows, rules, cuts):
            for seg in spans[i].segments:
                lo, hi = chars(seg)
                cuts.setdefault(id(objects[seg.row]), []).append(_Cut(lo, hi, "math", i))

        # Cut the rows.
        pieces_of: dict[
            int, list[tuple[int, Any, str, int]]
        ] = {}  # id(row) -> (lo, piece, kind, span)
        out: list[Any] = []
        for row in text_rows:
            row_cuts = sorted(cuts.get(id(row), []), key=lambda c: c.lo)
            if not row_cuts:
                out.append(row)
                pieces_of[id(row)] = [(0, row, "plain", -1)]
                continue
            made = _cut_row(row, row_cuts)
            pieces_of[id(row)] = made
            out.extend(piece for _, piece, _, _ in made)

        # Regions for the block spans.
        regions: dict[int, Any] = {}
        label_pieces: list[Any] = []
        for made in pieces_of.values():
            for _, piece, kind, span_index in made:
                if kind == "math":
                    region = regions.get(span_index)
                    if region is None:
                        region = layout._Region(
                            forced=True, page_pass="span", latex=spans[span_index].latex
                        )
                        regions[span_index] = region
                    region.rows.append(piece)
                    piece.region = region
                elif kind == "label":
                    label_pieces.append(piece)
                    self.stats.labels += 1
                elif kind == "number":
                    self.stats.numbers += 1
        for region in regions.values():
            region.rows.sort(key=lambda r: (r.y0, r.x0))
            boxes = [r.bbox for r in region.rows]
            region.box = layout._union(boxes)
            x0, y0, x1, y1 = region.box
            region.rules = [
                rule
                for rule in rules
                if x0 - RULE_MARGIN <= (rule.bbox[0] + rule.bbox[2]) / 2 <= x1 + RULE_MARGIN
                and y0 - RULE_MARGIN <= (rule.bbox[1] + rule.bbox[3]) / 2 <= y1 + RULE_MARGIN
                and rule.bbox[2] - rule.bbox[0] <= (x1 - x0) + 2 * RULE_MARGIN
            ]
            region.box = layout._union([region.box, *(r.bbox for r in region.rules)])
            self.stats.blocks += 1
        self._absorb_brackets(out, list(regions.values()))
        for label in label_pieces:
            host = _region_beside(label, list(regions.values()))
            if host is None:
                label.kind = "text"  # a number with no equation beside it: not maths
                continue
            host.labels.append(label)
            label.region = host

        # Inline spans and look-alikes: marked on the piece that holds them.
        def holder(row_id: str, lo: int, hi: int) -> tuple[Any, int] | None:
            for start, piece, kind, _ in pieces_of.get(id(objects[row_id]), []):
                if kind == "plain" and start <= lo and hi <= start + len(piece.chars):
                    return piece, start
            return None

        for row_id, lo, hi, latex in inline:
            found = holder(row_id, lo, hi)
            if found is None or found[0].kind == "math":
                self.stats.skipped += 1  # across a cut, or inside a display layout kept
                continue
            piece, start = found
            piece.page_inline.append((lo - start, hi - start, latex))
            self.stats.inline += 1
        for row_id, lo, hi in nonmath:
            found = holder(row_id, lo, hi)
            if found is None:
                self.stats.skipped += 1
                continue
            piece, start = found
            piece.page_nonmath.append((lo - start, hi - start))
            self.stats.nonmath += 1
        self.stats.pages_applied += 1
        return out

    def _screen_blocks(
        self,
        blocks: list[int],
        spans: list[Span],
        by_id: dict[str, PageRow],
        objects: dict[str, Any],
        text_rows: list[Any],
        rules: list[Any],
        carve: dict[int, list[_Cut]],
    ) -> list[int]:
        """The block spans that may replace layout's own cut, by the rule of ADR 0006:

        a span stands where layout found no display equation (prose swallowed it, or nothing
        was read), and where it covers every row of the regions it overlaps (a system layout
        split, a determinant it left a row out of) or leaves of them only rows with no letter
        or digit (a bracket). It does not stand where it would leave a row with symbols of a
        region it only partly covers (it would split an equation layout kept whole; on the
        development set that cost more than it gained), nor where it would leave, in a row it
        shares, a piece layout reads as maths."""
        # Layout's regions on the rows as they are: a trial, the marks are taken off again.
        layout._equation_regions(text_rows, rules)
        region_of = {id(r): r.region for r in text_rows if r.region is not None}
        for row in text_rows:
            row.region = None
        taken: dict[int, list[tuple[int, int]]] = {
            key: [(c.lo, c.hi) for c in cuts] for key, cuts in carve.items()
        }
        accepted: list[int] = []
        for i in blocks:
            touched = [
                (
                    objects[seg.row],
                    by_id[seg.row].chars[seg.start],
                    by_id[seg.row].chars[seg.end - 1] + 1,
                )
                for seg in spans[i].segments
            ]
            ids = {id(row) for row, _, _ in touched}
            regions = {
                id(region): region for row, _, _ in touched if (region := region_of.get(id(row)))
            }
            split = any(
                id(row) not in ids and any(c.c.isalnum() for c in row.ink)
                for region in regions.values()
                for row in region.rows
            )
            mathy = False
            for row, lo, hi in touched:
                for start, end in _gaps(len(row.chars), [*taken.get(id(row), []), (lo, hi)]):
                    piece = row.chars[start:end]
                    if not any(not c.blank and c.c not in ",.;:" for c in piece):
                        continue
                    trial = layout._Row(piece, column=row.column)
                    layout._classify_row(trial)
                    mathy = mathy or trial.kind == "math"
            if split or mathy:
                self.stats.refused += 1
                continue
            for row, lo, hi in touched:
                taken.setdefault(id(row), []).append((lo, hi))
            accepted.append(i)
        return accepted

    def _absorb_brackets(self, rows: list[Any], regions: list[Any]) -> None:
        """Rows of a matrix's or determinant's brackets (glyphs with no letter or digit in
        them, which the model's quotes leave out) that sit against a placed region belong to
        it: left alone they would each become an equation of their own."""
        for row in rows:
            if row.kind != "math" or row.region is not None or row.vertical:
                continue
            if any(c.c.isalnum() for c in row.ink):
                continue
            host = _region_touching(row, regions)
            if host is not None:
                host.rows.append(row)
                host.box = layout._union([host.box, row.bbox])
                row.region = host
                self.stats.absorbed += 1

    def summary(self) -> Counter[str]:
        return Counter(self.stats.as_dict())


ATTACHED_LETTERS = 4  # at most this many glued letters or digits after a span


def _with_scripts(row: Any, lo: int, hi: int) -> tuple[int, int]:
    """`lo:hi` grown over what is stuck to it: sub- and superscript glyphs on either side (a
    charge, an index the model's quote left outside), and up to `ATTACHED_LETTERS` letters
    or digits glued to its end with no space (the "th" of "(r+1)th" and "nth"; a model quotes
    what it reads as the formula and leaves the word-ending behind, but the text layer has
    one token there, and the answer key's formula holds it)."""
    chars = row.chars
    gap = 0.3 * row.size
    while (
        hi < len(chars)
        and not chars[hi].blank
        and row.is_script(chars[hi])
        and chars[hi].x0 - chars[hi - 1].x1 <= gap
    ):
        hi += 1
    glued = 0
    while (
        hi < len(chars)
        and glued < ATTACHED_LETTERS
        and chars[hi].c.isalnum()
        and chars[hi].x0 - chars[hi - 1].x1 <= gap
    ):
        hi += 1
        glued += 1
    if hi < len(chars) and glued and chars[hi].c.isalnum():
        hi -= glued  # a longer word: the quote was right to stop where it did
    while (
        lo > 0
        and not chars[lo - 1].blank
        and row.is_script(chars[lo - 1])
        and chars[lo].x0 - chars[lo - 1].x1 <= gap
    ):
        lo -= 1
    return lo, hi


def _gaps(length: int, taken: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The stretches of `0:length` that no `(lo, hi)` of `taken` covers."""
    out: list[tuple[int, int]] = []
    cursor = 0
    for lo, hi in sorted(taken):
        if lo > cursor:
            out.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < length:
        out.append((cursor, length))
    return out


def _cut_row(row: Any, cuts: list[_Cut]) -> list[tuple[int, Any, str, int]]:
    """The pieces of `row` cut at `cuts` (sorted, not overlapping): `(start, piece, kind,
    span)` with kind "plain" for what lies between the cuts. A piece with no ink is dropped.
    Plain pieces and numbers are classified as layout classifies a row; a cut-out maths piece
    is maths whatever its rules would say."""
    made: list[tuple[int, Any, str, int]] = []
    cursor = 0
    bounds = [(c.lo, c.hi, c.kind, c.span) for c in cuts]
    for lo, hi, kind, span in [*bounds, (len(row.chars), len(row.chars), "plain", -1)]:
        if lo > cursor:
            made.append((cursor, _piece(row, cursor, lo), "plain", -1))
        if hi > lo:
            made.append((lo, _piece(row, lo, hi), kind, span))
        cursor = max(cursor, hi)
    result = []
    for start, piece, kind, span in made:
        if all(c.blank for c in piece.chars):
            continue
        if kind == "number":
            piece.exercise_number = True
        layout._classify_row(piece)
        if kind in ("math", "label"):
            piece.kind = "math"
        result.append((start, piece, kind, span))
    return result


def _piece(row: Any, lo: int, hi: int) -> Any:
    return layout._Row(
        row.chars[lo:hi],
        vertical=row.vertical,
        diagonal=row.diagonal,
        column=row.column,
        marker=row.marker if lo == 0 else None,
        case_row=row.case_row,
        page_checked=True,
    )


def _consistent(span: Span, rows: dict[str, PageRow]) -> bool:
    """Does the span's LaTeX say what the text it quotes says (module constant)?"""
    text = " ".join(rows[seg.row].text[seg.start : seg.end] for seg in span.segments)
    try:
        mathml = safe_latex_to_mathml(span.latex, "block" if span.display == "block" else "inline")
        similarity = equation_step.symbol_dice(text, mathml)
    except (LatexConversionError, ValueError, RecursionError):
        return False  # a reading that does not convert grounds nothing
    return similarity >= CONSISTENT_FROM


def _region_touching(row: Any, regions: list[Any]) -> Any | None:
    """The region whose box `row` overlaps or touches (within half a font size), nearest first."""
    reach = 0.5 * row.size
    best, distance = None, float("inf")
    for region in regions:
        x0, y0, x1, y1 = region.box
        bx0, by0, bx1, by1 = row.bbox
        if bx1 < x0 - reach or bx0 > x1 + reach or by1 < y0 - reach or by0 > y1 + reach:
            continue
        if region.rows[0].column != row.column:
            continue
        gap = abs((x0 + x1) / 2 - (bx0 + bx1) / 2) + abs((y0 + y1) / 2 - (by0 + by1) / 2)
        if gap < distance:
            best, distance = region, gap
    return best


def _region_beside(label: Any, regions: list[Any]) -> Any | None:
    """The placed region an equation number belongs to: same column, the number's vertical
    centre within half a font size of the region's rows."""
    centre = (label.y0 + label.y1) / 2
    best, distance = None, float("inf")
    for region in regions:
        if region.rows[0].column != label.column or region.labels:
            continue
        _, y0, _, y1 = region.box
        if not y0 - 0.5 * label.size <= centre <= y1 + 0.5 * label.size:
            continue
        gap = abs((y0 + y1) / 2 - centre)
        if gap < distance:
            best, distance = region, gap
    return best
