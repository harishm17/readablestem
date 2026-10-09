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

The answer becomes a grid (`grid_from_output`): each row's cells placed from the left,
skipping slots a cell above spans into (as HTML places them), slots no cell covers filled
with empty cells, whitespace collapsed, and LaTeX kept only for real maths (a cell such as
`5.2\\ \\text{m/s}` is plain text). Then the header policy (`apply_header_policy`):

- the column header rows are the top rows whose cells are all headers (in `<thead>`);
- row headers only when the model marks the first column's body cells as row headers
  and those cells are non-empty, not numbers, and all different (planets, quantities);
  otherwise they are data cells, so a numeric first column is never a row header. A
  label/value table therefore gets row headers and no header row;
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
  non-maths cells, as a multiset, equal the PDF's words for the table, and each row's
  words come from the matching row of the local grid (`text_mismatch`: a word or number
  dropped, duplicated, invented or changed; `row_mismatch`: one in the wrong row). When
  only the spacing differs ("12 345" for "12345", a word broken at the end of a line),
  it is a note (`whitespace_differs`). With no PDF words to compare: `no_pdf_text`;
- `table.math`: every maths cell's LaTeX passes `validate.check_latex`,
  `safe_latex_to_mathml` and `check_mathml` (`invalid_latex`; that cell then shows its
  text);
- `table.math_symbols`, `table.math_signs`, `table.math_style`: each maths cell against
  the PDF's glyphs in that cell, with the equation step's own rules (letters, digits and
  Greek letters; signs and relations; a meaning-level letter style, or a cosmetic one
  as a note). Only when the grids agree, so the PDF cell is known.

Status rules, in order (docs/design/content-features.md "Review tiers"):
1. rebuilding off -> FAILED "table_not_rebuilt" (required; the image is kept);
2. the answer says it is not a data table, after escalation -> ABSTAINED
   "not_data_table" (required; the image is kept);
3. no usable answer from any attempt (the API unavailable, or answers that cannot be a
   grid) -> the local grid with first-row headers, NEEDS_REVIEW (check `table.source`,
   code `local_grid`); without a local grid FAILED "model_<outcome>" or "invalid_table";
4. every check passed -> ACCEPTED, review "none";
5. only notes failed (`NOTE_CODES`: spacing, a cosmetic maths style) -> ACCEPTED,
   review "optional";
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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from a11y_stem.config import Settings
from a11y_stem.core.types import (
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
from a11y_stem.models import router
from a11y_stem.models.base import T, VisionClient, VisionRequest
from a11y_stem.models.prompts import load_prompt
from a11y_stem.pipeline import options

# The equation step's comparison rules, imported rather than copied so a maths cell and
# an equation are judged alike (as web/compare.py does for the review screen).
from a11y_stem.pipeline.equation_step import (
    TEXT_LAYER_MISMATCH_BELOW,
    _mathml_symbols,
    _symbol_bag,
    clean_latex,
    text_layer_similarity,
    text_layer_style,
    text_layer_symbols_differ,
)
from a11y_stem.pipeline.signs import compare_signs
from a11y_stem.pipeline.validate import (
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
NOTE_CODES = frozenset({"whitespace_differs", "style_cosmetic"})
# Failed checks a second reading cannot change: they never escalate.
NO_ESCALATION_CODES = frozenset({"whitespace_differs", "no_pdf_text"})
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
# Symbols that carry meaning in a cell: m/s is not m s, and 5 % is not 5.
_SYMBOLS = (
    "/*%+=<>~\N{MULTIPLICATION SIGN}\N{MIDDLE DOT}\N{PER MILLE SIGN}\N{PLUS-MINUS SIGN}"
    "\N{MINUS-OR-PLUS SIGN}\N{NOT EQUAL TO}\N{LESS-THAN OR EQUAL TO}"
    "\N{GREATER-THAN OR EQUAL TO}\N{ALMOST EQUAL TO}\N{DEGREE SIGN}\N{SQUARE ROOT}"
    "\N{INFINITY}\N{PROPORTIONAL TO}"
)
# What the bijection compares: a number (a minus sign glued to it at the start, or when a
# space, an opening bracket, a colon, a semicolon, a comma or one of the symbols above
# comes just before, so "a-5" and "1-3" keep their hyphen apart while "10 times -5" and
# the point "(3,-5)" keep their sign), a run of letters, or one of the symbols. Letters
# and digits split ("9.8m" is "9.8" and "m"); brackets, quotes, commas, full stops and
# hyphens are not compared.
_SIGN_AFTER = r"\s(\[{:;," + re.escape(_SYMBOLS)
_WORD = re.compile(rf"(?:(?<![^{_SIGN_AFTER}])-)?\d+(?:[.,]\d+)*|[^\W\d_]+|[{re.escape(_SYMBOLS)}]")
# A hyphen that breaks a word at the end of a line ("tempera- ture").
_LINE_BREAK_HYPHEN = re.compile(r"(?<=[^\W\d_])-\s+(?=[^\W\d_])")


def _folded(text: str) -> str:
    """NFKC (ligatures, full-width forms, superscript digits become plain), invisible
    format characters dropped, then the folds above."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").translate(_FOLD)


def words(text: str | None) -> list[str]:
    """The words, numbers and meaningful symbols of `text` in order, as the bijection
    compares them. Case is kept (mW is not MW); spacing never matters."""
    return _WORD.findall(_folded(text)) if text else []


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
    return apply_header_policy(grid)


def _plain(cell: TableCell) -> str:
    return " ".join((cell.text or cell.latex or "").split())


def is_numeric(cell: TableCell) -> bool:
    """A number or a measurement ("2019", "-3.5", "1.5 x 10^5", "2 kg"): its first letter
    or digit is a digit."""
    first = next((ch for ch in unicodedata.normalize("NFKC", _plain(cell)) if ch.isalnum()), "")
    return first.isdigit()


def apply_header_policy(grid: TableGrid) -> TableGrid:
    """Header flags as the output writes them (see the module docstring): header rows
    from the top, row headers only for a first column of distinct labels, full-width
    header cells in the body as section headings, no flag on an empty cell.

    A row of one full-width header cell is a header row only above the column labels
    (a group label over every column); below them it starts a section of the body."""
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

    first_column = [c for c in cells if c.row >= header_rows and c.col == 0 and not full_width(c)]
    labels = [_plain(c).casefold() for c in first_column]
    row_headers = (
        bool(first_column)
        and all(c.header == "row" and not c.is_empty for c in first_column)
        and not any(is_numeric(c) for c in first_column)
        and len(set(labels)) == len(labels)
    )
    out = []
    for cell in cells:
        header = cell.header
        if cell.row < header_rows:
            header = "col" if header else None  # a "row" corner cell is a column header
        elif full_width(cell) and header:
            header = "row"  # a section heading
        elif header == "row" and not (row_headers and cell.col == 0):
            header = None
        out.append(cell if header == cell.header else cell.model_copy(update={"header": header}))
    return grid.model_copy(
        update={"cells": out, "header_rows": header_rows, "header_cols": int(row_headers)}
    )


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
    if all(bag(m) == bag(p) for m, p in pairs):
        return None
    if all(_joined(m) == _joined(p) for m, p in pairs):
        return "whitespace_differs"
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
        verdict.symbols = verdict.symbols or differ
        similarity = text_layer_similarity(layer, cell.mathml)
        if similarity is None or similarity >= TEXT_LAYER_MISMATCH_BELOW:
            signs = compare_signs(layer, cell.mathml)
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


def table_checks(grid: TableGrid, element: Element) -> list[Check]:
    """Every check of a data table proposal against the PDF (see the module docstring)."""
    checks = structure_checks(grid)
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


def _evaluate(output: TableOutput | None, calls: list[ModelCall], element: Element) -> _Candidate:
    if output is None:
        return _Candidate(output, calls)
    candidate = _Candidate(output, calls, grid_from_output(output, caption=caption_of(element)))
    if candidate.grid is not None and output.is_data_table:
        candidate.checks = table_checks(candidate.grid, element)
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
        return make(Status.ABSTAINED, "not_data_table", checks=[])
    if candidate.grid is None or not candidate.usable:
        local = _local(element)
        if local is not None:
            # No model could read it: the PDF's own grid, for a person to check. Its words
            # are the PDF's, so only its structure is checked.
            grid = first_row_headers(local, caption=caption_of(element))
            source = Check(name="table.source", passed=False, severity="warning", code="local_grid")
            return make(Status.NEEDS_REVIEW, grid=grid, checks=[source, *structure_checks(grid)])
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
