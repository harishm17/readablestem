"""Table claims: the cells of a table found on a page (`pipeline/tables.py`) are taken out of the
page's rows and blocks, with the math and key terms inside them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence

import pymupdf

from readablestem.core.types import BBox, Kind, TableGrid
from readablestem.pipeline import tables
from readablestem.pipeline.layout.blocks import _contents_lines, _dominant_size
from readablestem.pipeline.layout.context import _PLAIN, _Document
from readablestem.pipeline.layout.glyphs import (
    _MATH_SYMBOL_FONT,
    _Char,
    _clean,
    _regular_letters,
    _union,
)
from readablestem.pipeline.layout.inline_runs import _inline_runs, _script_glyphs
from readablestem.pipeline.layout.rows import _merge_baselines
from readablestem.pipeline.layout.shared import (
    _SUPERSCRIPT_FLAG,
    MAX_TERM_WORDS,
    _Block,
    _Inline,
    _Path,
    _Row,
)
from readablestem.pipeline.parse import equation_score

LOWERED_CELL_REASON = "lowered text in cells"  # a table with a subscript, written flat

# Run the table detector only on pages that could hold a table (`tables.may_hold_table`).
# The result is the same either way; switched off, only to check that.
TABLE_PREFILTER = True


def _claim_tables(
    page: pymupdf.Page,
    number: int,
    frags: dict[int, list[_Row]],
    paths: dict[int, list[_Path]],
    previous: list[tables.PageTable] | None,
    *,
    document: _Document,
    accept: Callable[[list[tables.PageTable]], list[tables.PageTable]] | None = None,
) -> list[tables.PageTable]:
    """Find the page's data tables (`pipeline.tables`) and take their lines (cells and
    "Table N" caption) and rules out of the page, before columns, rows, headings and
    equations are worked out: a table's numbers are not an equation, its bold header
    cells not headings, its ruled cells not a figure, and its columns not text columns.
    Only pages the probe passed get here (`analyze` probes every selected page first).
    `previous` is the page before's tables, when it was analysed: a table that goes on
    from its last one is marked, and the end of a row the page break split is joined to
    that row (`tables.join_split_row`). `accept` (with the region tree on) is asked about what
    the detector proposes, before anything is taken out of the page: the tree is built from the
    whole page, and a proposal it refuses (WS15.2) is left where it is."""
    if TABLE_PREFILTER and not tables.may_hold_table(page, frags[number], paths[number]):
        found = []  # nothing the detector could find would be kept (`may_hold_table`)
    else:
        found = tables.find(
            page,
            frags[number],
            paths[number],
            clean=_clean,
            budget=document.detector,
            detector=document.detector_sources.get(number, "page"),
        )
        if accept is not None:
            found = accept(found)
    if previous and not (found and tables.continues(previous[-1], found[0], page.rect.width)):
        # The rest of an unruled table has too few lines to be found on its own page.
        rest = tables.find_continued(
            page,
            frags[number],
            paths[number],
            previous[-1],
            found,
            clean=_clean,
            budget=document.detector,
        )
        if rest is not None and accept is not None:
            rest = next(iter(accept([rest])), None)
        if rest is not None:
            found = [rest, *found]
    if found:
        # A contents list is tried before its grid is taken for a table (`_shape_entries`).
        covered = _contents_lines(frags[number], number)
        found = [t for t in found if len(t.lines & covered) * 2 <= len(t.lines)]
    if not found:
        return found
    lines = set().union(*(t.lines for t in found))
    drawn = set().union(*(t.paths for t in found))
    for table in found:
        # A cell keeps a raised mark as a superscript character (`tables._marked`) but not a
        # lowered one: it is written flat, and the notice `scripts_flattened` says so.
        cells = _merge_baselines([frags[number][i] for i in sorted(table.lines)], None)
        if any(
            "sub" in _script_glyphs(row, document=document)[0] for row in cells if not row.vertical
        ):
            table.reasons.append(LOWERED_CELL_REASON)
        table.cell_maths = _cell_maths(
            table.grid, [frags[number][i] for i in sorted(table.lines)], document=document
        )
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


def _table_regions(found: Sequence[tables.PageTable], claimed: Sequence[_Row]) -> list[BBox]:
    """Where each table stands on the page, caption included: its box and the boxes of the
    lines it claimed (`claimed`: the page's fragments before `_claim_tables`)."""
    out: list[BBox] = []
    for table in found:
        boxes = [table.bbox] + [claimed[i].bbox for i in table.lines if i < len(claimed)]
        out.append(
            (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
        )
    return out


CELL_MATHS_REASON = "table cell maths"
MAX_CELL_EQUATIONS = 60  # a table with more maths cells than this is left to the table step
_CELL_SCAN_LIMIT = 2_000_000  # cells x glyphs: a bigger table is not scanned


def _letters_of(text: str) -> str:
    """The letters and digits of `text` as a text layer and a cell spell them alike."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in folded if c.isalnum())


def _marks_of(text: str) -> str:
    """`text` without spaces, as a text layer and a cell spell it alike (`_letters_of` keeps no
    symbols)."""
    return "".join(unicodedata.normalize("NFKC", text).split())


_SYMBOL_CELL_GLYPHS = 4  # a cell of more glyphs than this is not one symbol
_SYMBOL_BULLETS = frozenset("\u2022\u2023\u25e6\u25aa\u25ab\u2219\u00b7")


def _symbol_glyphs_only(ink: Sequence[_Char]) -> bool:
    """Every glyph of a cell is set in a maths symbol font (`_MATH_SYMBOL_FONT`: CMSY, MSAM,
    TXSYA, LM Math Symbols), a few at most. A bullet of the plain `Symbol` face is a list marker
    that Word and its kin print, not maths, and the Dingbats fonts are not in the set at all."""
    if not 0 < len(ink) <= _SYMBOL_CELL_GLYPHS:
        return False
    for c in ink:
        face = c.font.split("+", 1)[-1].replace(" ", "")
        if not _MATH_SYMBOL_FONT.match(face) or c.c.isspace():
            return False
        if face.lower().startswith("symbol") and c.c in _SYMBOL_BULLETS:
            return False
    return True


def _cell_owners(grid: TableGrid, glyphs: Sequence[_Char]) -> list[int | None]:
    """For each glyph, the index of the one cell it belongs to, or None. Cell boxes of a detected
    grid overlap by a few points, so a glyph whose centre is in two goes to the cell it overlaps
    most, and never to both."""
    boxes = [(i, c.bbox) for i, c in enumerate(grid.cells) if c.bbox is not None]
    owners: list[int | None] = []
    for g in glyphs:
        cx, cy = (g.x0 + g.x1) / 2, (g.y0 + g.y1) / 2
        best, best_area = None, 0.0
        for i, (x0, y0, x1, y1) in boxes:
            if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                continue
            area = max(0.0, min(g.x1, x1) - max(g.x0, x0)) * max(0.0, min(g.y1, y1) - max(g.y0, y0))
            if best is None or area > best_area:
                best, best_area = i, area
        owners.append(best)
    return owners


def _cell_maths(
    grid: TableGrid, rows: Sequence[_Row], *, document: _Document = _PLAIN
) -> dict[tuple[int, int], _Inline]:
    """The cells of a table whose whole content is one inline formula, by the glyphs.

    A cell of a TeX paper holds maths when the glyphs say it as they do in a line of prose
    (`_inline_runs`: a maths font, an italic variable with an operator, a script, a relation
    with operands): the cell's glyphs are read as a row of their own, and the cell counts only
    when one run covers everything in it. A cell of words with a formula in it, a bare number,
    a cell of several lines (a stacked fraction is one: left to the table step's model) and a
    rotated table are not taken. Nothing here reads LaTeX: the cell's crop goes to the equation
    step like any inline equation, and what it reads is flagged by the same checks.
    """
    if not document.tex_made or grid.turned or not grid.cells:
        return {}
    glyphs = [c for row in rows if not row.vertical and not row.diagonal for c in row.chars]
    if not glyphs or len(grid.cells) * len(glyphs) > _CELL_SCAN_LIMIT:
        return {}
    owned = _cell_owners(grid, glyphs)
    found: dict[tuple[int, int], _Inline] = {}
    for index, cell in enumerate(grid.cells):
        if cell.bbox is None or not cell.text.strip():
            continue
        mine = sorted(
            (c for c, owner in zip(glyphs, owned, strict=True) if owner == index),
            key=lambda c: (c.x0, c.y0),
        )
        ink = [c for c in mine if not c.blank]
        if not ink or max(c.y1 for c in ink) - min(c.y0 for c in ink) > 1.6 * max(
            c.size for c in ink
        ):
            continue
        row = _Row(mine)
        runs = _inline_runs(row, document=document)
        first = next(i for i, c in enumerate(mine) if not c.blank)
        last = max(i for i, c in enumerate(mine) if not c.blank)
        if (
            not runs
            and len(ink) == 1
            and ink[0].c.isascii()
            and ink[0].c.isalpha()
            and ink[0].italic
        ):
            # One italic letter alone in its cell is a variable: nothing in prose stands beside it
            # to make it a word (`_italic_variables` needs upright text around it).
            runs = [(first, last + 1, ["italic letter alone in a table cell"])]
        symbols = not runs and _symbol_glyphs_only(ink)
        if symbols:
            # A cell of nothing but maths symbol glyphs (a lone `\checkmark`, a `\times`) has no
            # run: the run finder wants an operand beside a symbol. In a table cell the glyph is
            # the whole content, so it is the formula.
            runs = [(first, last + 1, ["maths symbol glyphs alone in a table cell"])]
        if len(runs) != 1 or runs[0][0] > first or runs[0][1] < last + 1:
            continue
        # A leading or trailing mark the run trims (a comma, a full stop) is punctuation of the
        # cell, not part of the formula: only a whole cell is taken.
        span = mine[runs[0][0] : runs[0][1]]
        text = _clean("".join(c.styled for c in span))
        if _letters_of(text) != _letters_of(cell.text):
            continue  # not what the cell says (a glyph of its neighbour, a mark the cell drops)
        if symbols and _marks_of(text) != _marks_of(cell.text):
            continue  # symbols have no letters to compare: the marks themselves must agree
        shown = [c for c in span if not c.blank]
        score, _ = equation_score(
            text, sorted({c.font for c in shown}), any(c.flags & _SUPERSCRIPT_FLAG for c in shown)
        )
        found[(cell.row, cell.col)] = _Inline(
            _union((c.x0, c.y0, c.x1, c.y1) for c in shown),
            text,
            score,
            [CELL_MATHS_REASON, *runs[0][2]],
            _dominant_size([row]),
            _regular_letters(shown),
        )
    return found if len(found) <= MAX_CELL_EQUATIONS else {}


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
                cell_inlines=dict(found.cell_maths),
                flat_scripts=LOWERED_CELL_REASON in found.reasons,
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
