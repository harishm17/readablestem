"""Table step: a detected data table -> a rebuilt grid, checked against the PDF's words.

`pipeline.run` calls it like the other steps, on a pool thread with the table's crop:
`rebuild(element, png, *, client, settings, context_text) -> Fix` with `kind=Kind.TABLE`,
the proposal on `Fix.table` and every model call on `Fix.calls`. The options in force
(`pipeline.options.current()`) say whether rebuilding is on (`ConversionOptions.tables`,
FEATURE_TABLES). When it is off the table is kept as an image of the table with a
required decision, FAILED "table_not_rebuilt", decided before any model work.

Routing (the equation step's shape): the cheap model (gpt-6-luna) reads the crop first,
through `models.router.call_with_fallback`, so an outage on one model falls back to the
other. When any check fails (except the spacing note and "no PDF text", which a second
reading cannot change) or the answer says the crop is not a data table, the main model
(gpt-6.1-sol) is asked once, with no fallback: its fallback is the cheap model, which has
answered this exact request already. The main model's answer is kept unless it is
unusable, or it has a meaning-level problem the cheap answer does not have. Every
attempt's `ModelCall` is on the fix. `BudgetExceeded` propagates: the orchestrator
decides what happens to the rest of the document.

The model sees the crop, the "Table N" caption and the neighbouring text
(`context_text`), which the client fences as untrusted data. It is not sent the PDF's own
words for the table (`Element.table`, the local grid): they are what its answer is
checked against, as the equation step keeps an equation's text layer from the model. A
model that copied them could pass the checks with a flattened exponent (10⁵ is "105" in
most text layers).

A row a page break split (`tables.join_split_row`) is read the way layout joined it:
each crop shows one page, so on the table whose last row goes on (`REASON_ROW_SPLIT`) a
last-row cell the model read as the start of the PDF's cell gets the PDF's whole text,
and on the table that holds the row's end (`REASON_ROW_CONTINUED`) the model's row of
it, right after the header rows, is left out (`with_split_rows`). The checks then run as
usual, so a model answer that differs anywhere else is still caught.

The answer becomes a grid (`grid_from_output`): each row's cells placed from the left,
skipping slots a cell above spans into (as HTML places them), slots no cell covers filled
with empty cells, whitespace collapsed, and LaTeX kept only for real maths (a cell such as
`5.2\\ \\text{m/s}` is plain text). Then the header policy (`apply_header_policy`):

- the column header rows are the top rows whose cells are all headers (in `<thead>`);
- row headers only for a column whose body cells the model marks as row headers and
  that are non-empty, not numbers, and all different (planets, quantities); otherwise
  they are data cells, so a numeric first column is never a row header. A label/value
  table therefore gets row headers and no header row. The leading such columns are the
  row headers (two levels when a row-spanning group label comes first); one after data
  cells is the second of two tables side by side, written with headers/id. When a
  column marked so after data cells is not labels, the row headers to its left would
  label its half of each row, so they are dropped too, with a note
  (`row_headers_dropped`);
- a header cell across the full width of the body is a section heading;
- the caption is the "Table N" caption layout found beside the table (`Element.caption`),
  never the model's: a caption is never invented.

Checks (content-free codes; docs/tables.md):

- `table.grid`: the grid itself (`core.types.grid_problems`): cells that overlap, leave a
  gap or reach outside it (`grid_invalid`);
- `table.headers`: header sanity: a header below the body, a data cell among the
  headers, a header spanning into the body, headers covering the table
  (`header_problem`), or no header cell at all (`no_headers`);
- `table.grid_agreement` (with a local grid): the model's rows x columns and merged
  cells agree with the grid layout built from the PDF (`grid_size_differs`,
  `spans_differ`): two independent readings that agree;
- `table.cell_text`: the cell-text bijection. The words and numbers of the model's
  non-maths cells, as a multiset, equal the PDF's words for the table, and with a local
  grid of the same size each cell's words are those of the local cell in its place
  (`text_mismatch`: a word or number dropped, duplicated, invented or changed;
  `row_mismatch`: one in the wrong row; `cell_mismatch`: one in the wrong column of its
  row, such as an empty cell the model left out or two values swapped). When only the
  spacing within each cell differs ("12 345" for "12345", a word broken at the end of a
  line), it is a note (`whitespace_differs`). With no PDF words to compare:
  `no_pdf_text`;
- `table.math` (error): in a grid read from the PDF, no cell holds maths its text gives
  flat (`flattened_maths`: an accent, a stacked fraction; `assemble_tables.flattened_cells`);
  every maths cell's LaTeX passes `validate.check_latex`,
  `safe_latex_to_mathml` and `check_mathml` (`invalid_latex`; that cell then shows its
  text);
- `table.math_symbols`, `table.math_signs`, `table.math_style`: each maths cell against
  the PDF's glyphs in that cell, with the equation step's own rules (letters, digits and
  Greek letters; signs and relations; a meaning-level letter style, or a cosmetic one
  as a note). Only when the grids agree, so the PDF cell is known.

Status rules, in order (docs/design/content-features.md "Review tiers"):
1. rebuilding off -> FAILED "table_not_rebuilt" (required; the image is kept). Layout
   only makes a table element of a grid its local check calls data (`tables.find`: a
   grid of terms becomes a list, prose grids stay paragraphs), so this is a data table;
2. the answer says it is not a data table, after escalation -> its cells' text in
   reading order (`text_grid`: the grid read from the PDF, `is_data` False), ACCEPTED
   with the note `written_as_text` (review "optional": the local check called it data,
   so a person may look, but no decision is asked: no words are lost); without a grid
   read from the PDF that can be written (none, no text, or maths its text gives flat)
   ABSTAINED "not_data_table" (required; the image is kept). A grid of numbers
   (`numeric_grid`) is not written as text: its values would lose their columns. The
   grid read from the PDF with first-row headers is proposed instead, NEEDS_REVIEW
   (check `table.kind`, code `numbers_not_a_table`);
3. no usable answer from any attempt (the API unavailable, or answers that cannot be a
   grid) -> the local grid with first-row headers, NEEDS_REVIEW (check `table.source`,
   code `local_grid`); without a local grid FAILED "model_<outcome>" or "invalid_table";
4. every check passed -> ACCEPTED, review "none";
5. only notes failed (`NOTE_CODES`: spacing, a cosmetic maths style, row headers
   dropped) -> ACCEPTED, review "optional";
6. otherwise (a failed bijection, grid agreement, maths or header check, after
   escalation) -> NEEDS_REVIEW with the proposal (review "required").
The model gives no confidence: the checks decide, so `Fix.confidence` is None.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

import defusedxml.ElementTree as DET
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from readablestem.config import Settings
from readablestem.core.types import (
    MAX_TABLE_CELLS,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    Check,
    Element,
    Fix,
    Kind,
    ModelCall,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    grid_problems,
)
from readablestem.models import router
from readablestem.models.base import T, VisionClient, VisionRequest
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import options
from readablestem.pipeline.assemble_tables import flattened_cells

# The equation step's comparison rules, imported rather than copied so a maths cell and
# an equation are judged alike (as web/compare.py does for the review screen).
from readablestem.pipeline.equation_step import (
    _DOTLESS,
    TEXT_LAYER_MISMATCH_BELOW,
    _mathml_symbols,
    _symbol_bag,
    clean_latex,
    text_layer_similarity,
    text_layer_style,
    text_layer_symbols_differ,
)
from readablestem.pipeline.signs import compare_signs
from readablestem.pipeline.tables import REASON_ROW_CONTINUED, REASON_ROW_SPLIT
from readablestem.pipeline.validate import (
    LatexConversionError,
    check_latex,
    check_mathml,
    has_errors,
    safe_latex_to_mathml,
)

PROMPT_NAME = "table"
# "<name>-<sha8 of the prompt text>": changes whenever the prompt file changes.
PROMPT_VERSION = load_prompt(PROMPT_NAME).version

# Output tokens: about 30 per cell plus reasoning (measured on 59 table crops: at most
# 4,294 for 126 cells on gpt-6-luna). Sized from the local grid, so a small table does
# not reserve a large table's worst case.
BASE_OUTPUT_TOKENS = 2048
OUTPUT_TOKENS_PER_SLOT = 40
MIN_OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS = 16384
# Failed checks that are only notes: the fix is still ACCEPTED (review "optional").
NOTE_CODES = frozenset(
    {"whitespace_differs", "style_cosmetic", "row_headers_dropped", "written_as_text"}
)
# Failed checks a second reading cannot change: they never escalate.
NO_ESCALATION_CODES = frozenset({"whitespace_differs", "no_pdf_text", "row_headers_dropped"})
# `grid_problems` codes about the grid itself; the others are about its headers.
STRUCTURE_PROBLEMS = frozenset({"no_cells", "cell_out_of_bounds", "cells_overlap", "grid_hole"})
# Placing cells marks each slot it covers; a well-formed grid marks each slot once, so
# far more marks than slots can only be a broken (or hostile) answer.
_MAX_SLOT_MARKS = 2 * MAX_TABLE_ROWS * MAX_TABLE_COLS


class CellOutput(BaseModel):
    """One printed cell, as the model reads it (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    text: str = Field(description="The cell's text exactly as printed; empty for an empty cell.")
    latex: str = Field(
        description="Math-mode LaTeX for the whole cell when it holds mathematics; else empty."
    )
    header: Literal["col", "row", "none"] = Field(
        description="col: labels the cells below; row: labels the cells to its right."
    )
    row_span: int = Field(ge=1, description="Rows the cell covers (1 for an ordinary cell).")
    col_span: int = Field(ge=1, description="Columns the cell covers (1 for an ordinary cell).")


class RowOutput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    cells: list[CellOutput] = Field(description="The row's cells, left to right.")


class TableOutput(BaseModel):
    """Structured output the model must return (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    is_data_table: bool = Field(
        description="True for a table of data read against row or column labels."
    )
    caption: str = Field(description="The caption printed in the image, else empty.")
    rows: list[RowOutput] = Field(description="Every row, top to bottom, header rows included.")


# ----------------------------------------------------------------- model calls


def _call(
    client: VisionClient, request: VisionRequest, schema: type[T], settings: Settings
) -> tuple[T | None, list[ModelCall]]:
    """One routed call (model, then its fallback). Looked up on the module at call time
    so tests can substitute the router."""
    return router.call_with_fallback(client, request, schema, settings=settings)


def _call_once(
    client: VisionClient, request: VisionRequest, schema: type[T]
) -> tuple[T | None, list[ModelCall]]:
    """One call to request.model with no fallback (the escalation). The client still
    reserves spend first and raises BudgetExceeded when refused."""
    parsed, call = client.run(request, schema)
    return parsed, [call]


def output_tokens(element: Element) -> int:
    """The output budget of one request, from the size of the local grid (if any)."""
    slots = element.table.rows * element.table.cols if element.table is not None else 0
    wanted = BASE_OUTPUT_TOKENS + OUTPUT_TOKENS_PER_SLOT * slots
    return max(MIN_OUTPUT_TOKENS, min(MAX_OUTPUT_TOKENS, wanted))


def caption_of(element: Element) -> str | None:
    """The "Table N" caption layout found beside the table, if any."""
    for caption in (element.caption, element.table.caption if element.table else None):
        if caption and caption.strip():
            return " ".join(caption.split())
    return None


def table_context(element: Element, context_text: str | None) -> str | None:
    """Caption plus nearby text, for `VisionRequest.context_text` (fenced by the client).
    Never the table's own words (see the module docstring)."""
    caption = caption_of(element)
    nearby = (context_text or "").strip()
    parts = []
    if caption:
        parts.append(f"Caption: {caption}")
    if nearby:
        parts.append(f"Nearby text: {nearby}")
    return "\n\n".join(parts) or None


# ------------------------------------------------------------------ words


# Characters folded before comparing: dashes and minus signs into "-" (U+2212 and an
# ASCII hyphen are one minus sign), TeX's capital Delta (U+2206 INCREMENT in pdfTeX text)
# into the Greek letter, the dot operators into one middle dot, the tilde operator into "~".
_DASHES = (
    "\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{FIGURE DASH}\N{EN DASH}\N{EM DASH}"
    "\N{HORIZONTAL BAR}\N{MINUS SIGN}\N{SMALL HYPHEN-MINUS}\N{FULLWIDTH HYPHEN-MINUS}"
)
_FOLD = str.maketrans(
    {
        **dict.fromkeys(_DASHES, "-"),
        "\N{INCREMENT}": "\N{GREEK CAPITAL LETTER DELTA}",
        "\N{DOT OPERATOR}": "\N{MIDDLE DOT}",
        "\N{BULLET OPERATOR}": "\N{MIDDLE DOT}",
        "\N{TILDE OPERATOR}": "~",
    }
)
# Symbols that carry meaning in a cell: m/s is not m s, 5 % is not 5, and 10^5 (an
# exponent: "^" or superscript characters, which the local grid writes for raised
# glyphs) is not 105.
_SYMBOLS = (
    "/*%+=<>~^\N{MULTIPLICATION SIGN}\N{MIDDLE DOT}\N{PER MILLE SIGN}\N{PLUS-MINUS SIGN}"
    "\N{MINUS-OR-PLUS SIGN}\N{NOT EQUAL TO}\N{LESS-THAN OR EQUAL TO}"
    "\N{GREATER-THAN OR EQUAL TO}\N{ALMOST EQUAL TO}\N{DEGREE SIGN}\N{SQUARE ROOT}"
    "\N{INFINITY}\N{PROPORTIONAL TO}"
)
# What the bijection compares: a number (a minus sign glued to it at the start, or when a
# space, an opening bracket, a colon, a semicolon, a comma or one of the symbols above
# comes just before, so "a-5" and "1-3" keep their hyphen apart while "10 times -5", the
# point "(3,-5)" and the exponent "10^-18" keep their sign; U+2212 is always a minus
# sign, so a flattened "10−18" keeps it too), a run of letters, or one of the symbols.
# Letters and digits split ("9.8m" is "9.8" and "m"); brackets, quotes, commas, full
# stops and hyphens are not compared.
_SIGN_AFTER = r"\s(\[{:;," + re.escape(_SYMBOLS)
_WORD = re.compile(rf"(?:(?<![^{_SIGN_AFTER}])-)?\d+(?:[.,]\d+)*|[^\W\d_]+|[{re.escape(_SYMBOLS)}]")
# A hyphen that breaks a word at the end of a line ("tempera- ture").
_LINE_BREAK_HYPHEN = re.compile(r"(?<=[^\W\d_])-\s+(?=[^\W\d_])")
# Superscript characters: NFKC would make 10⁵ "105", so a run of them becomes "^"
# and its plain form first ("10^5"), as a raised exponent typed with a caret reads.
_SUPERSCRIPT_RUN = re.compile("[\u00b2\u00b3\u00b9\u2070\u2071\u2074-\u207f]+")


def _folded(text: str, *, minus: str = "-") -> str:
    """Superscripts marked (above), NFKC (ligatures, full-width forms), invisible format
    characters dropped, U+2212 written as `minus`, then the folds above."""
    text = _SUPERSCRIPT_RUN.sub(lambda m: "^" + unicodedata.normalize("NFKC", m[0]), text)
    text = unicodedata.normalize("NFKC", text).replace("\N{MINUS SIGN}", minus)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").translate(_FOLD)


def words(text: str | None) -> list[str]:
    """The words, numbers and meaningful symbols of `text` in order, as the bijection
    compares them. Case is kept (mW is not MW); spacing never matters."""
    return _WORD.findall(_folded(text, minus=" -")) if text else []


def squeezed(text: str | None) -> str:
    """`text` with all spacing removed (and a word joined again across a line-break
    hyphen): texts that differ only in spacing are equal here, while "1/2" and "12"
    still differ."""
    if not text:
        return ""
    return "".join(_LINE_BREAK_HYPHEN.sub("", _folded(text)).split())


# ------------------------------------------------------------------ the grid


# LaTeX that is only text: \text{...}, spacing and an escaped percent sign.
_TEXT_ONLY = re.compile(r"\\text\{[^{}]*\}|\\[%,;:! ]")


def is_maths(latex: str) -> bool:
    """Does this LaTeX need maths markup (a command, a script) rather than plain text?"""
    return bool(re.search(r"[\\^_]", _TEXT_ONLY.sub("", latex)))


def to_mathml(latex: str) -> str | None:
    """Validated inline MathML for one cell's LaTeX, or None when it fails a check."""
    if has_errors(check_latex(latex)):
        return None
    try:
        mathml = safe_latex_to_mathml(latex, "inline")
    except LatexConversionError:
        return None
    return None if has_errors(check_mathml(mathml)) else mathml


def _cell(out: CellOutput, row: int, col: int, row_span: int, col_span: int) -> TableCell:
    latex = clean_latex(out.latex) if out.latex.strip() else ""
    maths = bool(latex) and is_maths(latex)
    return TableCell(
        row=row,
        col=col,
        row_span=row_span,
        col_span=col_span,
        text=" ".join(out.text.split()),
        latex=latex if maths else None,
        mathml=to_mathml(latex) if maths else None,
        header=None if out.header == "none" else out.header,
    )


def grid_from_output(output: TableOutput, *, caption: str | None) -> TableGrid | None:
    """The model's rows as a `TableGrid` (source "model", header policy applied), or
    None when they cannot be one: no rows, or past the size limits."""
    placed = _grid_with_policy(output, caption=caption)
    return placed[0] if placed is not None else None


def _grid_with_policy(output: TableOutput, *, caption: str | None) -> tuple[TableGrid, bool] | None:
    """`grid_from_output`, and whether the header policy dropped row headers."""
    rows = len(output.rows)
    if not 0 < rows <= MAX_TABLE_ROWS:
        return None
    taken: set[tuple[int, int]] = set()
    marks = 0
    cells: list[TableCell] = []
    for r, row in enumerate(output.rows):
        col = 0
        for out in row.cells:
            while (r, col) in taken:
                col += 1
            row_span = min(out.row_span, rows - r)  # HTML cuts a span at the last row
            if col + out.col_span > MAX_TABLE_COLS or len(cells) >= MAX_TABLE_CELLS:
                return None
            marks += row_span * out.col_span
            if marks > _MAX_SLOT_MARKS:
                return None
            taken.update(
                (rr, cc) for rr in range(r, r + row_span) for cc in range(col, col + out.col_span)
            )
            cells.append(_cell(out, r, col, row_span, out.col_span))
            col += out.col_span
    cols = max((c.col + c.col_span for c in cells), default=0)
    if cols == 0:
        return None
    # A slot no cell covers is an empty printed cell the model left out.
    holes = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in taken]
    if len(cells) + len(holes) > MAX_TABLE_CELLS:
        return None
    cells += [TableCell(row=r, col=c) for r, c in holes]
    cells.sort(key=lambda c: (c.row, c.col))
    try:
        grid = TableGrid(
            rows=rows,
            cols=cols,
            cells=cells,
            caption=caption,
            is_data=output.is_data_table,
            source="model",
        )
    except ValidationError:
        return None
    return _header_policy(grid)


def _plain(cell: TableCell) -> str:
    return " ".join((cell.text or cell.latex or "").split())


def is_numeric(cell: TableCell) -> bool:
    """A number or a measurement ("2019", "-3.5", "1.5 x 10^5", "2 kg"): its first letter
    or digit is a digit."""
    first = next((ch for ch in unicodedata.normalize("NFKC", _plain(cell)) if ch.isalnum()), "")
    return first.isdigit()


def apply_header_policy(grid: TableGrid) -> TableGrid:
    """Header flags as the output writes them (see the module docstring): header rows
    from the top, row headers only for columns of distinct labels, full-width header
    cells in the body as section headings, no flag on an empty cell.

    A row of one full-width header cell is a header row only above the column labels
    (a group label over every column); below them it starts a section of the body."""
    return _header_policy(grid)[0]


def _row_header_columns(cells: list[TableCell], is_body: Any) -> tuple[set[int], bool]:
    """The columns whose body cells the model marked as row headers and that are labels
    (non-empty, not numbers, all different), and whether a marked label column had to
    be dropped.

    Every column is looked at, not only the first: the leading ones are the row headers
    proper (two levels when a row-spanning group label comes first), and a column of
    labels after data cells is the second table of two side by side, which `table_html`
    writes with headers/id (its last row may be empty). A column marked so after data
    cells whose cells are not labels would leave the row headers to its left labelling
    its half of each row (`scope="row"` covers the whole row), so those are dropped too:
    `dropped` says so."""
    columns: defaultdict[int, list[TableCell]] = defaultdict(list)
    for cell in cells:
        if is_body(cell):
            columns[cell.col].append(cell)
    kept: set[int] = set()
    dropped = False
    after_data = False
    for col in sorted(columns):
        filled = [c for c in columns[col] if not c.is_empty]
        # Leading columns must be complete; after data cells, at most half empty.
        enough = len(filled) * (2 if after_data else 1) >= len(columns[col])
        marked = bool(filled) and enough and all(c.header == "row" for c in filled)
        labels = [_plain(c).casefold() for c in filled]
        if marked and not any(map(is_numeric, filled)) and len(set(labels)) == len(labels):
            kept.add(col)
            continue
        if marked and after_data and kept:
            dropped = True
            kept.clear()
        after_data = True
    return kept, dropped


def _header_policy(grid: TableGrid) -> tuple[TableGrid, bool]:
    """`apply_header_policy`, and whether a column the model marked as row-header labels
    was made data (`row_headers_dropped`, a note)."""
    cells = [c.model_copy(update={"header": None}) if c.is_empty else c for c in grid.cells]
    anchored: defaultdict[int, list[TableCell]] = defaultdict(list)
    for cell in cells:
        anchored[cell.row].append(cell)

    def full_width(cell: TableCell) -> bool:
        return cell.col == 0 and cell.col_span >= grid.cols > 1

    header_rows = 0
    labelled = False  # a header row with column labels has been seen
    for row in range(grid.rows):
        filled = [c for c in anchored[row] if not c.is_empty]
        headers = bool(filled) and all(c.header for c in filled)
        if not (headers and any(c.header == "col" for c in filled)):
            break
        section = len(filled) == 1 and full_width(filled[0])
        if section and labelled:
            break
        labelled = labelled or not section
        header_rows += 1

    row_columns, dropped = _row_header_columns(
        cells, lambda c: c.row >= header_rows and not full_width(c)
    )
    header_cols = 0
    while header_cols in row_columns:
        header_cols += 1
    out = []
    for cell in cells:
        header = cell.header
        if cell.row < header_rows:
            header = "col" if header else None  # a "row" corner cell is a column header
        elif full_width(cell) and header:
            header = "row"  # a section heading
        elif header == "row" and cell.col not in row_columns:
            header = None
        out.append(cell if header == cell.header else cell.model_copy(update={"header": header}))
    policy = grid.model_copy(
        update={"cells": out, "header_rows": header_rows, "header_cols": header_cols}
    )
    return policy, dropped


def text_grid(element: Element) -> TableGrid | None:
    """The grid read from the PDF, to be written as its cells' text in reading order
    (`is_data` False: `assemble_tables` writes a list or paragraphs, not a table), when
    the model says the crop is not a table of data. None when there is no such grid, it
    has no text, or it holds maths its text gives flat (written out, that would read as
    something else: the image is kept)."""
    local = _local(element)
    if local is None or not any(c.text.strip() for c in local.cells):
        return None
    grid = local.model_copy(
        update={"is_data": False, "caption": caption_of(element), "source": "local"}
    )
    return None if flattened_cells(grid) else grid


NUMERIC_SHARE = 0.5  # cells below the first row holding a digit, for a grid of numbers


def numeric_grid(local: TableGrid) -> bool:
    """A grid of numbers: at least two filled cells below the first row, and at least
    NUMERIC_SHARE of them with a digit. Written as loose items, its values would lose
    their column (which time goes with which speed)."""
    body = [c.text for c in local.cells if c.row > 0 and c.text.strip()]
    if len(body) < 2:
        return False
    return sum(any(ch.isdigit() for ch in text) for text in body) >= NUMERIC_SHARE * len(body)


def first_row_headers(local: TableGrid, *, caption: str | None) -> TableGrid:
    """The local grid as a proposal when no model answer is usable: its first row as the
    column headers (what the source most often does), no row headers."""
    header_rows = 1 if local.rows > 1 else 0
    cells = [
        c.model_copy(update={"header": "col" if c.row < header_rows and not c.is_empty else None})
        for c in local.cells
    ]
    return local.model_copy(
        update={
            "cells": cells,
            "header_rows": header_rows,
            "header_cols": 0,
            "caption": caption,
            "is_data": True,
            "source": "local",
        }
    )


# ------------------------------------------------------------------ checks


def _owners(grid: TableGrid) -> dict[tuple[int, int], int]:
    """Slot -> index of the first cell (reading order) that covers it."""
    owner: dict[tuple[int, int], int] = {}
    order = sorted(range(len(grid.cells)), key=lambda i: (grid.cells[i].row, grid.cells[i].col))
    for index in order:
        cell = grid.cells[index]
        for r in range(cell.row, min(cell.row + cell.row_span, grid.rows)):
            for c in range(cell.col, min(cell.col + cell.col_span, grid.cols)):
                owner.setdefault((r, c), index)
    return owner


def _counterparts(cell: TableCell, owner: dict[tuple[int, int], int]) -> list[int]:
    """The local cells covering any slot of a model cell (the grids have the same size)."""
    found = {
        owner[(r, c)]
        for r in range(cell.row, cell.row + cell.row_span)
        for c in range(cell.col, cell.col + cell.col_span)
        if (r, c) in owner
    }
    return sorted(found)


def _local(element: Element) -> TableGrid | None:
    """The local grid, when it has cells to compare with."""
    grid = element.table
    return grid if grid is not None and grid.cells else None


def _aligned(grid: TableGrid, local: TableGrid | None) -> bool:
    return local is not None and (grid.rows, grid.cols) == (local.rows, local.cols)


def _spans(grid: TableGrid) -> set[tuple[int, int, int, int]]:
    return {
        (c.row, c.col, c.row_span, c.col_span)
        for c in grid.cells
        if c.row_span > 1 or c.col_span > 1
    }


def grid_agreement_code(grid: TableGrid, local: TableGrid) -> str | None:
    """Do the model's grid and the local one have the same rows x columns and merged
    cells? None when they agree."""
    if (grid.rows, grid.cols) != (local.rows, local.cols):
        return "grid_size_differs"
    if _spans(grid) != _spans(local):
        return "spans_differ"
    return None


def _joined(cells: Iterable[TableCell]) -> str:
    """The text of cells in reading order, run together with no spacing at all."""
    return "".join(squeezed(c.text) for c in sorted(cells, key=lambda c: (c.row, c.col)))


def _maths_symbols(cells: Iterable[TableCell]) -> Counter[str]:
    bag: Counter[str] = Counter()
    for cell in cells:
        bag += _mathml_symbols(cell.mathml) if cell.mathml else _symbol_bag(cell.text)
    return bag


def _rows_code(grid: TableGrid, local: TableGrid) -> str | None:
    """The bijection row by row, for grids of the same size: the PDF words of the local
    cells that stand where the model put maths are compared by the maths checks instead."""
    owner = _owners(local)
    maths = {i for c in grid.cells if c.latex for i in _counterparts(c, owner)}
    model_rows: list[list[TableCell]] = [[] for _ in range(grid.rows)]
    pdf_rows: list[list[TableCell]] = [[] for _ in range(grid.rows)]
    for cell in grid.cells:
        if not cell.latex and cell.row < grid.rows:
            model_rows[cell.row].append(cell)
    for index, cell in enumerate(local.cells):
        if index not in maths and cell.row < grid.rows:
            pdf_rows[cell.row].append(cell)

    def bag(cells: list[TableCell]) -> Counter[str]:
        return Counter(w for c in cells for w in words(c.text))

    pairs = list(zip(model_rows, pdf_rows, strict=True))
    if _spans(grid) == _spans(local):
        # Each text cell against the PDF cell in its place: a value in the wrong column
        # (a blank cell the model left out, two values swapped) keeps its row's words.
        cells = [
            ([cell], [local.cells[i] for i in _counterparts(cell, owner) if i not in maths])
            for cell in grid.cells
            if not cell.latex
        ]
    else:  # merged cells differ (spans_differ): rows are the finest pairing
        cells = pairs
    if all(bag(m) == bag(p) for m, p in cells):
        return None
    if all(_joined(m) == _joined(p) for m, p in cells):
        return "whitespace_differs"
    if all(bag(m) == bag(p) for m, p in pairs):
        return "cell_mismatch"
    if sum((bag(m) for m, _ in pairs), Counter()) == sum((bag(p) for _, p in pairs), Counter()):
        return "row_mismatch"
    return "text_mismatch"


def cell_text_code(grid: TableGrid, element: Element) -> str | None:
    """The cell-text bijection (see the module docstring). None when it holds.

    PDF words: the local grid's cells, else the element's text (no rows then). With a
    local grid of the same size the comparison is row by row; otherwise the whole table
    at once, where the PDF words no text cell accounts for must be the maths cells'
    letters and digits (a maths cell's text layer is flattened: 10^5 reads "105")."""
    local = _local(element)
    if local is not None and not any(words(c.text) for c in local.cells):
        local = None
    if local is None and not words(element.text):
        return "no_pdf_text"
    if local is not None and _aligned(grid, local):
        return _rows_code(grid, local)
    pdf = (
        Counter(w for c in local.cells for w in words(c.text))
        if local is not None
        else Counter(words(element.text))
    )
    maths = [c for c in grid.cells if c.latex]
    text_cells = [c for c in grid.cells if not c.latex]
    model = Counter(w for c in text_cells for w in words(c.text))
    leftover = pdf - model
    if not model - pdf:
        if not maths and not leftover:
            return None
        if maths and _symbol_bag(" ".join(leftover.elements())) == _maths_symbols(maths):
            return None
    if not maths:
        pdf_joined = _joined(local.cells) if local is not None else squeezed(element.text)
        if _joined(text_cells) == pdf_joined:
            return "whitespace_differs"
    return "text_mismatch"


@dataclass
class _MathsVerdict:
    compared: int = 0
    symbols: bool = False  # a letter, digit or Greek letter differs
    signs: bool = False  # a sign or relation differs
    style: str | None = None  # "meaning" or "cosmetic" (the worst seen)


# MathML layouts whose digits a text layer may run together or reorder (a fraction's
# numerator and denominator, a root's index, scripts above and below): numbers are not
# compared one by one there.
_STACKED = frozenset({"mfrac", "msqrt", "mroot", "msubsup", "munder", "mover", "munderover"})
_NUMBER = re.compile(r"\d+(?:\.\d+)*")
# An en dash between digits in a cell the model wrote as maths is a minus sign whose
# raise the text layer lost (the gold print's 10^-18 reads "10", an en dash, "18"), not a
# range.
_DIGIT_DASH = re.compile(r"(?<=\d)\u2013(?=\d)")


def _mathml_tags(mathml: str) -> tuple[set[str], str]:
    root = DET.fromstring(mathml, forbid_dtd=True)
    tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    return tags, " ".join(root.itertext())


def _cased(text: str) -> Counter[str]:
    """Letters with their case (math italic glyphs as ASCII; dotless i and j as i, j)."""
    folded = unicodedata.normalize("NFKC", text).translate(_DOTLESS)
    return Counter(ch for ch in folded if ch.isalpha() and unicodedata.category(ch) != "Lm")


def cell_text_layer(grid: TableGrid, cell: TableCell, element: Element | None) -> str | None:
    """The PDF's text where `cell` stands, for checking a reviewer's LaTeX in it
    (`web.edit_check.check_cell_edit`): the cell's own text in the grid read from the PDF
    (its text stays when the cell is written as LaTeX), else the text of the cells of
    that grid in its place, when the two grids line up. None when it is not known."""
    if grid.source == "local":
        return cell.text.strip() or None
    local = _local(element) if element is not None else None
    if local is None or not _aligned(grid, local):
        return None
    owner = _owners(local)
    text = " ".join(local.cells[i].text for i in _counterparts(cell, owner)).strip()
    return text or None


def maths_numbers_or_case_differ(layer: str, mathml: str) -> bool:
    """Stricter than the equation step's case-folded bag of letters and digits, for a
    maths cell (a value with its unit): the letters with their case (5 mW is not 5 MW),
    and, unless the MathML stacks digits (a fraction, a root, scripts above and below),
    the numbers as written (6.67 is not 66.7; 10^-31 is not 10^-13)."""
    try:
        tags, text = _mathml_tags(mathml)
    except DET.ParseError:
        return False
    if _cased(layer) != _cased(text):
        return True
    if tags & _STACKED:
        return False
    return not _numbers_agree(_NUMBER.findall(_folded(layer)), _NUMBER.findall(_folded(text)))


def _numbers_agree(layer: list[str], maths: list[str]) -> bool:
    """The same numbers, in any order; or, in order, each PDF number the MathML's next
    numbers run together (a text layer that flattened an exponent: "1024" for 10^{24})."""
    if Counter(layer) == Counter(maths):
        return True
    at = 0
    for number in layer:
        joined = ""
        while at < len(maths) and len(joined) < len(number):
            joined += maths[at]
            at += 1
        if joined != number:
            return False
    return at == len(maths)


def maths_verdict(grid: TableGrid, local: TableGrid) -> _MathsVerdict:
    """Each maths cell against the text of the local cells in its place (the equation
    step's rules: `text_layer_symbols_differ`, `compare_signs` when the letters mostly
    agree, `text_layer_style`). The grids have the same size.

    Unlike an equation, a maths cell is compared even where the PDF has no letters or
    digits: the rest of the table has a text layer, so maths read into an empty cell
    was invented."""
    owner = _owners(local)
    verdict = _MathsVerdict()
    for cell in grid.cells:
        if not cell.mathml:
            continue
        layer = " ".join(local.cells[i].text for i in _counterparts(cell, owner)).strip()
        differ = text_layer_symbols_differ(layer, cell.mathml)
        if differ is None:
            if _mathml_symbols(cell.mathml):
                verdict.compared += 1
                verdict.symbols = True
            continue
        verdict.compared += 1
        differ = differ or maths_numbers_or_case_differ(layer, cell.mathml)
        verdict.symbols = verdict.symbols or differ
        similarity = text_layer_similarity(layer, cell.mathml)
        if similarity is None or similarity >= TEXT_LAYER_MISMATCH_BELOW:
            signs = compare_signs(_DIGIT_DASH.sub("\N{MINUS SIGN}", layer), cell.mathml)
            verdict.signs = verdict.signs or (signs is not None and signs.mismatch)
        style = text_layer_style(layer, cell.mathml)
        if style == "meaning" or (style == "cosmetic" and verdict.style is None):
            verdict.style = style
    return verdict


def structure_checks(grid: TableGrid) -> list[Check]:
    """The grid itself and its headers (`grid_problems`, plus a data table without any
    header cell): nothing here looks at the PDF."""
    problems = set(grid_problems(grid))
    grid_code = "grid_invalid" if problems & STRUCTURE_PROBLEMS else None
    has_header = any(c.header and not c.is_empty for c in grid.cells)
    header_code = (
        "header_problem"
        if problems - STRUCTURE_PROBLEMS
        else (None if has_header else "no_headers")
    )
    return [
        Check(name="table.grid", passed=grid_code is None, severity="warning", code=grid_code),
        Check(
            name="table.headers", passed=header_code is None, severity="warning", code=header_code
        ),
    ]


def flattened_check(grid: TableGrid) -> list[Check]:
    """A grid read from the PDF whose cells hold maths its text gives flat (an accent, a
    stacked fraction: `assemble_tables.flattened_cells`): an error, so it cannot be
    accepted as it is and is not written into the document until those cells are LaTeX."""
    if not flattened_cells(grid):
        return []
    return [Check(name="table.math", passed=False, severity="error", code="flattened_maths")]


def table_checks(grid: TableGrid, element: Element) -> list[Check]:
    """Every check of a data table proposal against the PDF (see the module docstring)."""
    checks = structure_checks(grid) + flattened_check(grid)
    local = _local(element)
    if local is not None:
        agreement = grid_agreement_code(grid, local)
        checks.append(
            Check(
                name="table.grid_agreement",
                passed=agreement is None,
                severity="warning",
                code=agreement,
            )
        )
    text_code = cell_text_code(grid, element)
    checks.append(
        Check(name="table.cell_text", passed=text_code is None, severity="warning", code=text_code)
    )
    maths = [c for c in grid.cells if c.latex]
    if not maths:
        return checks
    invalid = any(c.mathml is None for c in maths)
    checks.append(
        Check(
            name="table.math",
            passed=not invalid,
            severity="error",
            code="invalid_latex" if invalid else None,
        )
    )
    if local is None or not _aligned(grid, local):
        return checks
    verdict = maths_verdict(grid, local)
    if not verdict.compared:
        return checks
    checks.append(
        Check(
            name="table.math_symbols",
            passed=not verdict.symbols,
            severity="warning",
            code="symbol_mismatch" if verdict.symbols else None,
        )
    )
    checks.append(
        Check(
            name="table.math_signs",
            passed=not verdict.signs,
            severity="warning",
            code="sign_mismatch" if verdict.signs else None,
        )
    )
    if verdict.style is not None:
        style_code = "style_mismatch" if verdict.style == "meaning" else "style_cosmetic"
        checks.append(
            Check(name="table.math_style", passed=False, severity="warning", code=style_code)
        )
    return checks


# ----------------------------------------------------------------- candidates


@dataclass
class _Candidate:
    """One model attempt (with its fallback) and what the checks made of it."""

    output: TableOutput | None
    calls: list[ModelCall]
    grid: TableGrid | None = None
    checks: list[Check] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """The answer is a grid with cells (whatever the checks say)."""
        return self.grid is not None and bool(self.grid.cells)

    @property
    def data(self) -> bool:
        return self.output is not None and self.output.is_data_table

    @property
    def problems(self) -> bool:
        """Anything that keeps it from being accepted: no usable data table, or a failed
        check other than a note."""
        if not (self.usable and self.data):
            return True
        return any(not c.passed and c.code not in NOTE_CODES for c in self.checks)


def _without_row(grid: TableGrid, row: int) -> TableGrid | None:
    """`grid` without `row` (1 x 1 cells only there, no span through it), else None."""
    if any(c.row == row and (c.row_span, c.col_span) != (1, 1) for c in grid.cells):
        return None
    if any(c.row < row < c.row + c.row_span for c in grid.cells):
        return None
    cells = [
        c.model_copy(update={"row": c.row - 1}) if c.row > row else c
        for c in grid.cells
        if c.row != row
    ]
    out = grid.model_copy(update={"rows": grid.rows - 1, "cells": cells})
    return out if out.rows and not grid_problems(out) else None


def with_split_rows(grid: TableGrid, element: Element) -> TableGrid:
    """The model's grid read the way layout joined a row a page break split (module
    docstring). Only where the element says so, and only where the answer fits the
    pattern; otherwise the grid is returned as it is, and the checks judge it."""
    local = _local(element)
    if local is None:
        return grid
    if REASON_ROW_CONTINUED in element.reasons and grid.rows == local.rows + 1:
        row = grid.header_rows
        first = next((c for c in grid.cells if c.row == row and c.col == 0), None)
        if first is not None and first.is_empty:
            grid = _without_row(grid, row) or grid
    if REASON_ROW_SPLIT in element.reasons and _aligned(grid, local):
        owner = _owners(local)
        last = grid.rows - 1
        cells = []
        for cell in grid.cells:
            whole = [local.cells[i] for i in _counterparts(cell, owner)]
            if cell.row == last and not cell.latex and cell.text and len(whole) == 1:
                started, pdf = squeezed(cell.text), squeezed(whole[0].text)
                if len(pdf) > len(started) and pdf.startswith(started):
                    cell = cell.model_copy(update={"text": whole[0].text})
            cells.append(cell)
        grid = grid.model_copy(update={"cells": cells})
    return grid


def _evaluate(output: TableOutput | None, calls: list[ModelCall], element: Element) -> _Candidate:
    if output is None:
        return _Candidate(output, calls)
    placed = _grid_with_policy(output, caption=caption_of(element))
    if placed is not None and output.is_data_table:
        placed = (with_split_rows(placed[0], element), placed[1])
    candidate = _Candidate(output, calls, placed[0] if placed is not None else None)
    if candidate.grid is not None and output.is_data_table:
        candidate.checks = table_checks(candidate.grid, element)
        if placed is not None and placed[1]:
            candidate.checks.append(
                Check(
                    name="table.row_headers",
                    passed=False,
                    severity="warning",
                    code="row_headers_dropped",
                )
            )
    return candidate


def _needs_escalation(candidate: _Candidate, settings: Settings) -> bool:
    """Ask the main model again when the cheap answer is unusable, says it is not a data
    table, or fails any check a second reading could change."""
    if candidate.output is None:
        return False  # the router already tried the other model as the fallback
    if candidate.calls and candidate.calls[-1].model == settings.main_model:
        return False  # the fallback was the main model: nothing better to ask
    if not (candidate.usable and candidate.data):
        return True
    return any(not c.passed and c.code not in NO_ESCALATION_CODES for c in candidate.checks)


def _prefer(main: _Candidate, cheap: _Candidate) -> _Candidate:
    """The main model's answer wins unless it produced nothing, only the cheap one is
    usable, or only the cheap one is free of problems."""
    if main.output is None or (not main.usable and cheap.usable):
        return cheap
    if main.problems and not cheap.problems:
        return cheap
    return main


# ----------------------------------------------------------------------- status


def _fix(element: Element, candidate: _Candidate, calls: list[ModelCall]) -> Fix:
    def make(
        status: Status,
        reason: str | None = None,
        grid: TableGrid | None = None,
        checks: list[Check] | None = None,
        review: Any = None,
    ) -> Fix:
        return Fix(
            element_id=element.id,
            kind=Kind.TABLE,
            status=status,
            table=TableFix(grid=grid) if grid is not None else None,
            checks=candidate.checks if checks is None else checks,
            calls=calls,
            reason=reason,
            review=review,  # None: from the status (waiting -> required)
        )

    if candidate.output is not None and not candidate.output.is_data_table:
        local = _local(element)
        if local is not None and numeric_grid(local):
            # Numbers written as loose items lose which value is in which column, and the
            # local check called the grid data: the verdict is doubted. The PDF's own grid
            # is proposed as a table, for a person to check against the image.
            grid = first_row_headers(local, caption=caption_of(element))
            doubt = Check(
                name="table.kind", passed=False, severity="warning", code="numbers_not_a_table"
            )
            checks = [doubt, *structure_checks(grid), *flattened_check(grid)]
            return make(Status.NEEDS_REVIEW, grid=grid, checks=checks)
        text = text_grid(element)
        if text is None:
            return make(Status.ABSTAINED, "not_data_table", checks=[])
        note = Check(name="table.kind", passed=False, severity="warning", code="written_as_text")
        return make(Status.ACCEPTED, grid=text, checks=[note], review="optional")
    if candidate.grid is None or not candidate.usable:
        local = _local(element)
        if local is not None:
            # No model could read it: the PDF's own grid, for a person to check. Its words
            # are the PDF's, so only its structure is checked.
            grid = first_row_headers(local, caption=caption_of(element))
            source = Check(name="table.source", passed=False, severity="warning", code="local_grid")
            checks = [source, *structure_checks(grid), *flattened_check(grid)]
            return make(Status.NEEDS_REVIEW, grid=grid, checks=checks)
        if candidate.output is None:
            outcome = calls[-1].outcome if calls else "skipped"
            return make(Status.FAILED, f"model_{outcome}", checks=[])
        return make(Status.FAILED, "invalid_table", checks=[])
    failed = [c for c in candidate.checks if not c.passed]
    if all(c.code in NOTE_CODES for c in failed):
        review = "optional" if failed else "none"
        return make(Status.ACCEPTED, grid=candidate.grid, review=review)
    return make(Status.NEEDS_REVIEW, grid=candidate.grid)


def rebuild(
    element: Element,
    png: bytes,
    *,
    client: VisionClient,
    settings: Settings,
    context_text: str | None = None,
) -> Fix:
    """Rebuild one table from its crop. Never raises for model or validation problems
    (they become NEEDS_REVIEW, ABSTAINED or FAILED fixes); raises BudgetExceeded when
    spend is refused."""
    if not options.current().tables:
        return Fix(
            element_id=element.id,
            kind=Kind.TABLE,
            status=Status.FAILED,
            reason="table_not_rebuilt",
        )
    prompt = load_prompt(PROMPT_NAME)
    context = table_context(element, context_text)

    def attempt(model: str, *, escalation: bool = False) -> _Candidate:
        request = VisionRequest(
            step="table",
            model=model,
            prompt_version=prompt.version,
            instructions=prompt.text,
            images=[png],
            context_text=context,
            max_output_tokens=output_tokens(element),
            image_detail="high",
        )
        if escalation:
            output, calls = _call_once(client, request, TableOutput)
        else:
            output, calls = _call(client, request, TableOutput, settings)
        return _evaluate(output, calls, element)

    candidate = attempt(settings.cheap_model)
    calls = list(candidate.calls)
    if _needs_escalation(candidate, settings):
        escalated = attempt(settings.main_model, escalation=True)
        calls += escalated.calls
        candidate = _prefer(escalated, candidate)
    return _fix(element, candidate, calls)
