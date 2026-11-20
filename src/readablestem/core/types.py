"""Shared data model for the conversion pipeline.

A PDF becomes a list of `Element`s (what is on the page, in reading order). Each element
that needs work gets a `Fix` (what the pipeline proposes, how sure it is, which checks it
passed, and what the model calls cost). The assembler turns elements + fixes into
accessible HTML. `DocumentResult` is the whole thing, serialisable to JSON.

Privacy rule for this module: `Check.code`, `Fix.reason`, `ModelCall.outcome`,
`Fix.group_key`, `Verification`, `Notice`, `SourceFacts` and `ProgressEvent` are category
codes, ids or numbers (e.g. "unknown_command", "hg-p3-e7"), never document text, so they
can be logged and aggregated after the document itself is deleted.

Every field added after the first release has a default, so results stored before it
existed still load. The content features of pipeline 0.4.0 (tables, long descriptions,
links, language, colour, notation memory) are described in docs/web.md and docs/tables.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from readablestem.config import Settings

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points

# Inline equations sit inside a paragraph. The paragraph's `text` holds this marker where
# the equation goes, and the equation element's `parent_id` is the paragraph id.
INLINE_MARKER = "\u27e6{id}\u27e7"  # rendered e.g. "⟦p1-e7⟧"
_MARKER_OPEN, _MARKER_CLOSE = INLINE_MARKER.split("{id}")
# One inline-equation marker; group 1 is the element id. Ids never contain spaces, so
# maths written with these brackets ("⟦a, b⟧") is not one. `assemble` replaces markers with
# maths, and `run` with "[equation]" in the context text it sends to models.
INLINE_MARKER_RE = re.compile(
    re.escape(_MARKER_OPEN)
    + r"([^\s"
    + re.escape(_MARKER_OPEN + _MARKER_CLOSE)
    + r"]{1,64})"
    + re.escape(_MARKER_CLOSE)
)


def inline_marker(element_id: str) -> str:
    return INLINE_MARKER.format(id=element_id)


class Kind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    EQUATION = "equation"
    FIGURE = "figure"
    # One item of a bulleted or numbered list (deterministic, no Fix). Consecutive items
    # with the same `list_level` and `list_ordered` form one <ul>/<ol> in the output.
    LIST_ITEM = "list_item"
    # A data table, claimed from the text flow by layout. `Element.table` holds the grid
    # built from the PDF's own words (source "local"); its fix proposes the rebuilt table
    # (`Fix.table`) or keeps the image of the table, flagged ("table_not_rebuilt").
    TABLE = "table"


class Status(StrEnum):
    """Lifecycle of a fix. The first four are set by the pipeline, the last two by review.

    A reviewer's "Accept" also sets ACCEPTED (the proposal is used as it is, exactly as
    when the pipeline accepts it); `Fix.reviewed` tells the two apart.
    """

    # Used as proposed: nothing needs a person's decision. It may still carry optional
    # notes (failed warning checks that do not change the meaning; `Fix.review`).
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"  # usable proposal that needs a decision (`review`)
    ABSTAINED = "abstained"  # no trustworthy proposal; the original stays, flagged
    FAILED = "failed"  # model/budget/validation error; the original stays, flagged
    EDITED = "edited"  # a reviewer changed the proposal
    REJECTED = "rejected"  # a reviewer rejected it; the original stays


# Statuses that wait for a person's decision. The pipeline gives them `review="required"`.
PENDING_STATUSES = frozenset({Status.NEEDS_REVIEW, Status.FAILED, Status.ABSTAINED})
# Statuses whose proposal is not used in the output: the original stays, flagged.
KEPT_ORIGINAL_STATUSES = frozenset({Status.ABSTAINED, Status.FAILED, Status.REJECTED})

# Review tier of a fix, set by the pipeline ("meaning-level only" policy, 2026-10-08):
#   required  a person must decide (NEEDS_REVIEW, FAILED or ABSTAINED): a wrong answer
#             would change what a student reads, or nothing usable was produced;
#   optional  accepted automatically, with a note a reviewer may look at (a cosmetic
#             style difference, a heading with weak evidence, a warning-only check);
#   none      accepted automatically, nothing to note.
# A reviewer's decision changes `status`, never `review`: the tier records what the
# pipeline asked for (ops aggregates count it).
Review = Literal["required", "optional", "none"]


def default_review(status: Status | str | None) -> Review:
    """The tier of a fix stored before tiers existed: waiting -> required, else none."""
    try:
        return "required" if Status(status) in PENDING_STATUSES else "none"
    except ValueError:
        return "none"


# `Element.reasons` of what was read from an image of a scanned or handwritten page (ADR 0007,
# `pipeline.image_page`): every element of such a page, the figure that stands for the whole
# page's picture (the output's "show the original" toggle: a figure element with no fix, which
# the steps leave alone, since there is nothing to decide), an equation whose box could not be
# used, and an inline equation (it has no region of its own, so no crop is made for it).
IMAGE_PAGE_REASON = "image page"
IMAGE_PAGE_ORIGINAL_REASON = "image page original"
IMAGE_PAGE_NO_BOX_REASON = "image page box unusable"
IMAGE_PAGE_NO_CROP_REASON = "image page inline"
# A figure element standing for a stretch of the page's ink that no block of the reading
# accounts for (`pipeline.image_page_omission`): its region is cropped, and it is a required
# decision that says the reading left something out.
IMAGE_PAGE_OMISSION_REASON = "image page omission"

# `Fix.reason` of a heading fix that is not a heading: its text is the same as the heading
# directly above it (a box title repeated under the section's own heading), so it is
# written as bold text (`heading_step`).
HEADING_REPEATS_ABOVE = "repeats_heading_above"


class Check(BaseModel):
    """Result of one deterministic validator. Content-free by design."""

    name: str  # e.g. "latex.allowed_commands", "mathml.well_formed", "alt.length"
    passed: bool
    severity: Literal["error", "warning"] = "error"
    code: str | None = None  # category when it fails, e.g. "unknown_command"
    # The day the failed rule was added to the checks, for a rule that has one: a release's
    # new rule can reopen a document that was ready, and the job page can say since when
    # the rule exists. None for a check stored before rules carried a date, and for rules
    # without one. A date, never content.
    added_on: date | None = None


Step = Literal[
    "equation",
    "figure",
    "judge",
    "table",
    "check",
    "page_math",
    "page_witness",
    "image_page",
    "page_audit",
]

Outcome = Literal[
    "ok", "refused", "invalid_output", "timeout", "api_error", "budget_exceeded", "skipped"
]


class ModelCall(BaseModel):
    """One request to a model provider (one attempt). Content-free."""

    step: Step
    model: str
    prompt_version: str
    input_tokens: int = 0  # the whole prompt, cached and cache-written tokens included
    cached_input_tokens: int = 0  # read from the provider's prompt cache
    # Written to the prompt cache (Anthropic bills these above the input rate). Left out of
    # the JSON when 0, so a record that never has any (OpenAI) is unchanged.
    cache_creation_input_tokens: int = Field(default=0, exclude_if=lambda v: v == 0)
    output_tokens: int = 0  # includes reasoning tokens, which are billed as output
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempt: int = 1
    is_fallback: bool = False
    outcome: Outcome = "ok"
    # The part of `cost_usd` charged at the reservation, not at what it cost, because a send
    # may have reached the provider and no usage came back (a connection that failed or
    # timed out after it was sent). 0: every dollar is a measured one. Left out of the JSON
    # when 0, so a record of a call with none is unchanged.
    billed_unseen_usd: float = Field(default=0.0, exclude_if=lambda v: v == 0)


class Verification(BaseModel):
    """One answer of the second automated check (`models.verifier`). Content-free.

    Kept apart from `Fix.calls`: those are the attempts that produced the proposal, and
    routing, escalation and the eval's route counts read them. This records a probability
    question asked about the proposal afterwards (OpenAI Decisions API).

    - `check`: what was asked, "figure_alt" (does the alt text match the image) or
      "equation_match" (does the LaTeX match the equation image);
    - `stage`: "final" (the proposal on the fix) or "cheap" (an equation's cheap-model
      answer, before it was escalated);
    - `mode`: "note" (may add an optional note to an accepted fix) or "shadow" (recorded
      only; changes nothing);
    - `outcome`: "ok" when every question was answered, else why not (`Outcome` codes:
      refused, timeout, api_error, invalid_output, budget_exceeded). Anything but "ok"
      leaves the fix exactly as it would be without the check (fail-open);
    - `probabilities`: answer code -> probability ("ok", "factual_error"; "match",
      "cosmetic", "meaning_changing"). Not calibrated: a ranking signal, compared with
      thresholds from settings, never shown to a reader as a confidence;
    - `flagged`: the threshold said "possible mismatch" (None when not judged).

    Codes are plain strings, not literals, so a result written by a newer release with a
    check this one does not know still loads.
    """

    check: str
    stage: str = "final"
    mode: str = "note"
    outcome: str = "ok"
    probabilities: dict[str, float] = Field(default_factory=dict)
    flagged: bool | None = None
    model: str = ""
    version: str = ""  # "<check>-<sha8>" of the question wording, like prompt versions
    input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class EquationFix(BaseModel):
    latex: str
    mathml: str  # presentation MathML, <math> root, display attribute set
    speech: str  # plain-English reading, for the review screen and the report
    # The version of the speech rules that wrote `speech` (`pipeline.speech.SPEECH_VERSION`);
    # 0 is a reading made before rules had one, left out of the JSON. A reading older than the
    # rules in force is made again from `mathml` where it is shown (`speech.reading_for`),
    # unless a person wrote it (a negative version), which is never replaced.
    speech_version: int = Field(default=0, exclude_if=lambda v: v == 0)
    display: Literal["block", "inline"] = "block"
    # The other reading of this formula when the crop's and the page witness's readings
    # differ (`pipeline.witness_reconcile`): `latex` is the one the glyphs favour (or the
    # crop's, on a tie) and this is the one that was not chosen. None: no disagreement.
    other_latex: str | None = Field(default=None, exclude_if=lambda v: v is None)
    other_source: Literal["crop", "page_witness"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    # `EQUATION_GLYPH_RECONCILE`: the model's reading before the equation step corrected it from
    # the PDF's own glyphs (`pipeline.glyph_reconcile`; the fix's `equation.pdf_correction` note
    # says which rule). `latex` is the corrected reading. None: nothing was corrected.
    model_latex: str | None = Field(default=None, exclude_if=lambda v: v is None)


# --------------------------------------------------------------------------- tables

# Size limits for one grid. A page holds far fewer; the limits keep a hostile or broken
# model answer from making the checks and the serialiser do unbounded work.
MAX_TABLE_ROWS = 300
MAX_TABLE_COLS = 60
MAX_TABLE_CELLS = 5000


class TableCell(BaseModel):
    """One cell of a `TableGrid`, anchored at (row, col), 0-based, and covering
    `row_span` x `col_span` slots.

    `text` is the cell's text (untrusted: from the PDF or a model). When the cell holds
    maths, `latex` is the whole cell as LaTeX (words inside `\\text{}`) and `mathml` the
    validated presentation MathML made from it; the serialiser still re-sanitises it and
    falls back to `text` when it cannot be shown. `header` says which way a header cell
    points: "col" (it labels the cells below it) or "row" (the cells to its right); None
    is a data cell. `bbox` is in PDF points in the page frame (`Element.bbox`'s frame).
    """

    row: int = Field(ge=0)
    col: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    text: str = ""
    latex: str | None = None
    mathml: str | None = None
    header: Literal["col", "row"] | None = None
    bbox: BBox | None = None
    # Layout found maths in this cell from its glyphs (`pipeline.layout._cell_maths`) and made it
    # an inline equation element of the table: its id. The equation step reads the cell's crop
    # like any inline equation, and the page writes what it read into the cell
    # (`pipeline.table_math`).
    equation_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    # Words of `text` the source sets in bold, italic or a fixed-pitch face (a Word or LaTeX
    # table; `strong`, `emphasis`, `code`, `highlight` runs, offsets into `text`). A PDF's
    # cells have none, and a cell with maths has none.
    runs: list[Run] = Field(default_factory=list, exclude_if=lambda v: not v)

    @property
    def is_empty(self) -> bool:
        return not (self.text.strip() or (self.latex or "").strip())


# Where a table's header meaning came from (`TableGrid.header_source`).
HeaderSource = Literal["model", "layout", "source", "reviewer", "none"]


class TableGrid(BaseModel):
    """A table as a grid of `rows` x `cols` slots and the cells that cover them.

    Every slot is covered by exactly one cell. An empty cell in the printed table is a
    `TableCell` with `text=""`; a slot that no cell covers is a hole, which a valid grid
    does not have (`grid_problems` reports it, and the serialiser fills it with an empty
    data cell). The first `header_rows` rows are column headers (written in `<thead>`)
    and the first `header_cols` columns are row headers; each header cell also says so
    itself (`TableCell.header`). `caption` is the "Table N ..." text found beside the
    table, None when there was none (never invented). `is_data` is False for a layout
    table (text arranged in boxes), which is not written as a data table. `source`:
    "local" when built from the PDF's own words and drawn lines, "model" when the table
    step's model answer was used.

    `header_source` says where the header rows and columns came from: "model" (the table
    step's model marked the header cells), "layout" (the header rows the reader of the PDF
    found itself: a ruled line under the row, a gap, a header the table reader marked),
    "source" (the Word or LaTeX file marks them), "reviewer" (a person chose them on the
    table card) or "none" (no header cell and no evidence for one: a person decides). None in
    a result stored before the field existed, which is read as it always was.
    """

    rows: int = Field(ge=1, le=MAX_TABLE_ROWS)
    cols: int = Field(ge=1, le=MAX_TABLE_COLS)
    cells: list[TableCell] = Field(default_factory=list, max_length=MAX_TABLE_CELLS)
    header_rows: int = Field(default=0, ge=0)
    header_cols: int = Field(default=0, ge=0)
    caption: str | None = None
    is_data: bool = True
    source: Literal["model", "local"] = "local"
    # Degrees clockwise to turn the picture of the table to read it: 90 or 270 for a table set
    # sideways on an upright page (`pipeline.tables._page_turn`), 0 otherwise. Boxes stay in
    # the page frame.
    turned: Literal[0, 90, 270] = 0
    header_source: HeaderSource | None = Field(default=None, exclude_if=lambda v: v is None)


# Codes `grid_problems` can return, in the order it returns them.
GRID_PROBLEMS = (
    "no_cells",  # the grid has no cells at all
    "cell_out_of_bounds",  # a cell (with its spans) reaches past `rows` or `cols`
    "cells_overlap",  # two cells cover the same slot
    "grid_hole",  # a slot no cell covers (an empty cell must be a cell with text "")
    "headers_cover_table",  # `header_rows` or `header_cols` leave no body row or column
    "header_span_into_body",  # a cell in the header rows spans down into the body
    "header_below_body",  # a column header ("col") below the header rows
    "data_in_header_rows",  # a non-empty data cell inside the header rows
    "data_in_header_cols",  # a non-empty data cell inside the leading header columns
)


def _cell_slots(cell: TableCell, rows: int, cols: int) -> Iterator[tuple[int, int]]:
    """The in-bounds slots a cell covers (row-major)."""
    for r in range(cell.row, min(cell.row + cell.row_span, rows)):
        for c in range(cell.col, min(cell.col + cell.col_span, cols)):
            yield r, c


def grid_problems(grid: TableGrid) -> list[str]:
    """What is structurally wrong with a grid, as content-free codes (`GRID_PROBLEMS`
    order, each at most once). Empty for a well-formed grid. Pure and deterministic.

    Used by the table step to judge a model's grid ("header sanity" included) and by
    the serialiser, which renders a grid with problems best-effort instead of failing.
    Irregular but valid tables (a row header in the middle of the body, a section row,
    multi-level column headers) are not problems: `pipeline.table_html` writes their
    header relationships with `headers`/`id`.
    """
    found: set[str] = set()
    if not grid.cells:
        found.add("no_cells")
    covered: set[tuple[int, int]] = set()
    # Visits past one per slot can only be overlaps; past twice that, stop counting (a
    # grid that broken is reported, not measured, and its holes are not looked for).
    budget = 2 * grid.rows * grid.cols
    measured = True
    for cell in grid.cells:
        if cell.row + cell.row_span > grid.rows or cell.col + cell.col_span > grid.cols:
            found.add("cell_out_of_bounds")
        for slot in _cell_slots(cell, grid.rows, grid.cols):
            if budget == 0:
                found.add("cells_overlap")
                measured = False
                break
            budget -= 1
            if slot in covered:
                found.add("cells_overlap")
            covered.add(slot)
    if measured and len(covered) < grid.rows * grid.cols:
        found.add("grid_hole")
    if grid.header_rows >= grid.rows or grid.header_cols >= grid.cols:
        found.add("headers_cover_table")
    for cell in grid.cells:
        in_header_rows = cell.row < grid.header_rows
        if in_header_rows and cell.row + cell.row_span > grid.header_rows:
            found.add("header_span_into_body")
        if cell.header == "col" and not in_header_rows:
            found.add("header_below_body")
        if cell.header is None and not cell.is_empty:
            if in_header_rows:
                found.add("data_in_header_rows")
            elif cell.col < grid.header_cols:
                found.add("data_in_header_cols")
    return [code for code in GRID_PROBLEMS if code in found]


class TableFix(BaseModel):
    """The table step's proposal: the rebuilt grid (`source` "model" or "local")."""

    grid: TableGrid


# --------------------------------------------------------------------------- figures

FigureType = Literal["chart", "diagram", "photo", "illustration", "table_image", "other"]
# Why a figure is decoration, beyond the model's word (`pipeline.decoration`, WS6.4): its
# picture repeats on the pages, it is a rule by its shape, or a person said so.
Decoration = Literal["repeated", "shape", "person"]


class FigureFix(BaseModel):
    figure_type: FigureType
    alt: str  # short alt text; "" only when decorative
    decorative: bool = False
    # The evidence that a decorative figure is decoration; only then is it written with
    # empty alt text. Left out of the stored result when there is none.
    decoration: Decoration | None = Field(default=None, exclude_if=lambda v: v is None)
    # Complex figures (charts, diagrams): a longer description in plain text, written
    # after the figure. None when the figure needs none or the model gave none.
    long_description: str | None = None
    # Charts: the plotted values as a data table (`table_html.render_table` writes it).
    data_table: TableGrid | None = None
    # True when every number in `data_table` appears in the PDF text inside the figure
    # region (the table is in the student file); False when some were read from the
    # image (approximate: an optional extra, kept here but not written until a reviewer
    # adds it); None when there is no data table.
    data_values_from_text: bool | None = None
    # A reviewer chose "Add the data table" for a table whose values were read from the
    # image: only then is such a table written (`figure_details.data_table_shown`).
    data_table_added: bool = False
    # The figure tells things apart by colour alone (series, regions): a notice.
    colour_only: bool = False


# --------------------------------------------------------------------------- inline spans

# Schemes a link in the output may use. Anything else (javascript:, file:, data:, PDF
# launch actions) is never written as a link (`Link`, notice "link_scheme_dropped").
SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
MAX_HREF_CHARS = 2048
_HREF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f-\x9f]")  # controls and whitespace


def is_safe_href(href: str) -> bool:
    """An absolute http, https or mailto URL with no whitespace or control characters
    (http and https with a host, mailto with an address)."""
    if not href or len(href) > MAX_HREF_CHARS or _HREF_FORBIDDEN.search(href):
        return False
    try:
        parts = urlsplit(href)
    except ValueError:  # e.g. an unbalanced IPv6 bracket
        return False
    scheme = parts.scheme.lower()
    if scheme not in SAFE_LINK_SCHEMES:
        return False
    if scheme == "mailto":
        return bool(parts.path)
    return bool(parts.hostname)


class _Span(BaseModel):
    """A range of an element's `text` as stored, inline markers included: `start` is
    the first character, `end` one past the last. A span never starts or ends inside an
    inline marker (the assembler drops one that does, and any span outside the text)."""

    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> _Span:
        if self.end <= self.start:
            raise ValueError("a span must end after it starts")
        return self


class Link(_Span):
    """A link on part of an element's text.

    - "uri": a PDF link to the web or an email address; `href` is http, https or mailto
      (enforced: anything else is a validation error, so it can never reach the output);
    - "internal": a PDF link to a place in this document; `target_id` is the element there;
    - "crossref": text such as "Figure 2.3", "Equation (3)", "Table 1", "Section 3.2" or
      "Theorem 2.1"; `target_id` is the element it names;
    - "typed": a web or email address typed in the text with no link in the file; `href` is
      as for "uri" (it is not one of the file's links, so the card's link count leaves it out).
    """

    kind: Literal["uri", "internal", "crossref", "typed"]
    href: str | None = None
    target_id: str | None = None

    @model_validator(mode="after")
    def _target(self) -> Link:
        if self.kind in ("uri", "typed"):
            if self.href is None or not is_safe_href(self.href):
                raise ValueError(f"a {self.kind} link needs an http, https or mailto href")
            if self.target_id is not None:
                raise ValueError(f"a {self.kind} link has no target_id")
        else:
            if not self.target_id:
                raise ValueError(f"an {self.kind} link needs a target_id")
            if self.href is not None:
                raise ValueError(f"an {self.kind} link has no href")
        return self


Channel = Annotated[int, Field(ge=0, le=255)]
# BCP 47-ish: "en", "en-US", "zh-Hant-TW" (the assembler's own check, `_clean_lang`).
LANG_TAG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")


class ColourRun(_Span):
    """Coloured text in an element's `text`: the colour as printed in the PDF (sRGB).
    The assembler adjusts it to reach 4.5:1 contrast on the output background."""

    rgb: tuple[Channel, Channel, Channel]


class ScriptRun(_Span):
    """Raised or lowered text in an element's `text` (Na+, H2O, cm3, a nuclide's mass number
    in 226Ra): the characters are in `text` as they were read, and the assembler writes
    them as `<sup>` or `<sub>`. Only upright text (not a variable set in a maths font,
    which stays an inline equation)."""

    kind: Literal["sup", "sub"]


# What a run of an element's text is marked as (`Run`). "deleted": text the page shows struck
# through (a cancelled date), written as `<del>` with the words "deleted:" and "end deleted"
# around it (`pipeline.inline_text`). "blank": a gap to fill in, written in text as underscores
# (typed, or drawn under a gap: `layout._mark_blanks`), and "checkbox": a printed box
# (U+2610 and its checked forms); both are written with words that say what they are.
# "strong", "emphasis" and "code": words the page sets in bold, in italic or in a fixed-pitch
# face inside a paragraph or list item (`<strong>`, `<em>`, `<code>`; `text_pass.style_runs`).
# "highlight": words the page marks with a highlight annotation or a filled rectangle behind
# them in a colour of their own (`<mark>`; `text_pass.highlight_runs`).
# "break": a line end the page keeps (a poem, an address), one run over the space that stands
# there in `text`, written as `<br>` in its place (`pipeline.inline_text`). It moves with its
# space when a reviewer edits the words and is dropped when the space goes, like every span.
# Later kinds are new values here, not new fields.
RunKind = Literal[
    "deleted", "blank", "checkbox", "strong", "emphasis", "code", "highlight", "break"
]


# What a block of text is apart from its kind (`Element.role`). "aside": text set apart from
# the body, a sidebar, a callout box, a margin note or a pull quote (`<aside>`, which a screen
# reader announces as a landmark). "quote": a block quotation (`<blockquote>`). A footnote is
# not a role: it is a notice of its own (`Notice` "footnote"), and program code is
# `Element.preformatted`, each read where it already was.
Role = Literal["aside", "quote"]


class Run(_Span):
    """A run of an element's `text` with a meaning of its own (`RunKind`). The characters
    stay in `text` as they were read: a struck date is kept and marked, never removed."""

    kind: RunKind


class HeadingFix(BaseModel):
    level: int = Field(ge=1, le=6)
    text: str


# What else a doubtful symbol of a reading may be (`pipeline.reading_alternatives`): how many
# symbols a formula lists and how many options each has. More would be a menu, not a choice.
MAX_DOUBTS_PER_READING = 3
MAX_OPTIONS_PER_DOUBT = 3
DoubtKind = Literal["case", "greek_latin", "digit_letter", "operator", "script", "other"]


class SymbolAlternative(BaseModel):
    """One doubtful symbol of a reading: the stretch `latex[start:end]` of the reading (`symbol`,
    as a check) and the other LaTeX it may be, likeliest first. Choosing an option replaces that
    stretch (`ReadingAlternatives.apply`). `kind` says why it is doubtful, for the wording of the
    choice: a letter's case, Greek against Latin, a digit against a letter, an operator, a
    script lost or added, or something else."""

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    symbol: str
    kind: DoubtKind = "other"
    options: list[str] = Field(min_length=1, max_length=MAX_OPTIONS_PER_DOUBT)

    @model_validator(mode="after")
    def _ordered(self) -> SymbolAlternative:
        if self.end <= self.start:
            raise ValueError("a symbol must end after it starts")
        return self


class ReadingAlternatives(BaseModel):
    """The doubts of one expression the page reader gave, for the review to offer as one-click
    choices (data from the reader's own answer; nothing here is a reading the page shows).

    `latex` is the reading the offsets belong to: the page reader's LaTeX as stored (after
    `\\ce` is written out and the wrappers are removed). It is the element's `Fix.equation.latex`
    for an inline equation and `Element.page_latex` for a display equation, whose final reading
    may come from the crop and then differ: a consumer offers the choices only for the string
    they belong to, never for another one. `symbols` are in reading order and do not overlap, and
    each `symbol` is `latex[start:end]` (enforced), so a stored value is always consistent."""

    latex: str
    symbols: list[SymbolAlternative] = Field(min_length=1, max_length=MAX_DOUBTS_PER_READING)

    @model_validator(mode="after")
    def _consistent(self) -> ReadingAlternatives:
        previous_end = 0
        for symbol in self.symbols:
            stretch = self.latex[symbol.start : symbol.end]
            if symbol.end > len(self.latex) or stretch != symbol.symbol:
                raise ValueError("a symbol must be the stretch of the reading it points at")
            if symbol.start < previous_end:
                raise ValueError("symbols must be in reading order and not overlap")
            previous_end = symbol.end
        return self

    def apply(self, symbol: int, option: int) -> str:
        """The reading with the `symbol`-th doubt's `option`-th choice in place (indexes into
        `symbols` and its `options`; IndexError when out of range)."""
        chosen = self.symbols[symbol]
        return self.latex[: chosen.start] + chosen.options[option] + self.latex[chosen.end :]

    @property
    def choices(self) -> int:
        """How many one-click choices these doubts give (every option of every symbol)."""
        return sum(len(s.options) for s in self.symbols)


# One character per letter style in `GlyphContext`: "-" is a glyph with no style to tell.
GLYPH_STYLE_CODES = {
    "italic": "i",
    "upright": "u",
    "bold": "b",
    "bold-italic": "B",
    "script": "s",
    "double-struck": "d",
    "fraktur": "f",
}
_GLYPH_STYLES = {code: style for style, code in GLYPH_STYLE_CODES.items()}


class GlyphContext(BaseModel):
    """What the PDF's glyphs say about an equation, kept with it so every check of a reading
    (the equation step's, and the review screen's of an edit) compares with the same evidence.

    Three runs of glyphs, each a string of character pairs: the glyph as `Element.text` writes it
    (a bold letter as the Unicode bold letter) and the code of its letter style
    (`GLYPH_STYLE_CODES`). `span`: the glyphs under the element's box. `before` and `after`: an
    inline equation's neighbours on its row, left and right of the box, nearest first, the
    glyphs a reading that reaches past the box may cover (`text_layer_compare.extend_text_layer`).
    Written by `layout.line_glyphs`; None on an element nobody read the glyphs of."""

    span: str = ""
    before: str = ""
    after: str = ""
    # For `EQUATION_GLYPH_RECONCILE` (`pipeline.glyph_reconcile`): `tex`, the PDF is TeX-made and
    # every non-ASCII glyph of the span is in a TeX font, so the text layer names its symbols
    # exactly; `labels`, the equation numbers "(N)" printed at an edge of the box apart from the
    # formula, space-separated; `scr`, the capitals drawn in an RSFS or EUSM script face
    # (`\mathscr`, not CMSY's `\mathcal`). Empty or False when layout found none.
    tex: bool = Field(default=False, exclude_if=lambda v: not v)
    labels: str = Field(default="", exclude_if=lambda v: not v)
    scr: str = Field(default="", exclude_if=lambda v: not v)

    @staticmethod
    def encode(glyphs: Iterable[tuple[str, str | None]]) -> str:
        return "".join(c[:1] + GLYPH_STYLE_CODES.get(style or "", "-") for c, style in glyphs if c)

    @staticmethod
    def decode(pairs: str) -> tuple[tuple[str, str | None], ...]:
        return tuple(
            (pairs[i], _GLYPH_STYLES.get(pairs[i + 1])) for i in range(0, len(pairs) - 1, 2)
        )

    @property
    def span_glyphs(self) -> tuple[tuple[str, str | None], ...]:
        return self.decode(self.span)

    @property
    def before_glyphs(self) -> tuple[tuple[str, str | None], ...]:
        return self.decode(self.before)

    @property
    def after_glyphs(self) -> tuple[tuple[str, str | None], ...]:
        return self.decode(self.after)


class Element(BaseModel):
    """Something on a page, in document reading order."""

    # "p{page+1}-e{index}": its place on its page, fixed within one result but not between
    # two conversions (an element added above renames it). `core.uid` gives the stable one.
    id: str
    kind: Kind
    page: int  # 0-based
    bbox: BBox
    order: int  # reading-order position across the whole document
    text: str | None = None  # source text (untrusted input; never put in instructions)
    font_size: float | None = None
    bold: bool = False
    # Equation detection evidence (cheap, explainable; see pipeline/parse.py).
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    display: Literal["block", "inline"] | None = None  # equations only
    parent_id: str | None = None  # inline equations: id of the containing paragraph
    # Display equations: the equation number printed beside it ("2.4", "(3)"), kept out
    # of the equation's bbox and text so it is not transcribed as part of the maths.
    equation_label: str | None = None
    # List items: the marker as printed ("a.", "3)", "•"), whether the list is ordered,
    # and the nesting level (1 = top level). `text` excludes the marker.
    list_marker: str | None = None
    list_ordered: bool | None = None
    list_level: int | None = None
    caption: str | None = None  # figures: nearby "Figure N ..." text, if found
    # Figures whose caption has inline maths: the caption with an inline marker where each
    # formula was (`parent_id` of those equations is the figure). `caption` stays plain text.
    caption_marked: str | None = Field(default=None, exclude_if=lambda v: v is None)
    # Equations: letters of `text` the PDF draws, not bold, in a font known to have a bold
    # face (Computer Modern, Times, ...), sorted, no repeats. Such a letter is known to be
    # plain, so a bold reading of it (a scalar read as a vector) is a real difference.
    # None when layout did not record it (older results, equations built outside layout).
    regular_letters: str | None = None
    # Equations: the glyphs under the box and beside it, with each letter's style, for the
    # checks of a reading against the PDF (`GlyphContext`). None when nobody read them.
    glyphs: GlyphContext | None = Field(default=None, exclude_if=lambda v: v is None)
    # Equations, only with the page math pass (ADR 0006): its LaTeX reading of this span
    # (a second reading, never output text), and whether the pass placed the span ("span")
    # or only layout found it on a page the pass read ("layout"). None: no pass.
    page_latex: str | None = Field(default=None, exclude_if=lambda v: v is None)
    page_status: Literal["span", "layout"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    # Equations, only with the page witness (`pipeline.witness_reconcile`): its LaTeX reading
    # of this formula (a second witness, never output text) and how its extent stands
    # against the element's: "match" (every edge within one glyph), "extent" (overlaps but
    # reaches further or less), "added" (the witness found it and layout did not).
    witness_latex: str | None = Field(default=None, exclude_if=lambda v: v is None)
    witness_status: Literal["match", "extent", "added"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    # A paragraph that goes on over page breaks is one element with the page and box of its
    # first part (`page`, `bbox`); this is the 0-based page of its last part. None: it ends
    # on its own page. A writer that marks page breaks writes the markers of the pages in
    # between before or after the element, never inside it.
    last_page: int | None = Field(default=None, exclude_if=lambda v: v is None)
    # Elements read from an image of a scanned or handwritten page (ADR 0007): how sure the
    # page reader said it was of this block. None for every element that came from a text
    # layer. The review tier of such an element's fix follows it (`pipeline.image_page`).
    image_confidence: Literal["high", "medium", "low"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    # Equations: the symbols another reading or a reader named as
    # doubtful, with what else each may be (`ReadingAlternatives`, which says which string its
    # offsets belong to). None when the reader listed none, and for everything else.
    alternatives: ReadingAlternatives | None = Field(default=None, exclude_if=lambda v: v is None)
    # Tables: the grid layout built from the PDF's own words and lines (source "local").
    table: TableGrid | None = None
    # Paragraphs: program code. `text` keeps its line breaks and the indentation it was
    # printed with, and is written as a code block (`<pre><code>`).
    preformatted: bool = Field(default=False, exclude_if=lambda v: not v)
    # Paragraphs: a glossary entry, a term and its definition in one paragraph. The term
    # is `text[:term_end]`, the definition the rest; consecutive entries are written as one
    # definition list. None for every other element.
    term_end: int | None = Field(default=None, exclude_if=lambda v: v is None)
    # Paragraphs: set apart from the body (`Role`). None for every other element and left out
    # of the JSON, so an element with no role is stored and written as before roles existed.
    role: Role | None = Field(default=None, exclude_if=lambda v: v is None)
    # Figures: the alt text the PDF's own tags give this picture (a tagged Word or
    # PowerPoint export), cleaned. A proposal only: the figure step shows it to the model
    # as context, and offers it to a reviewer when the model has no usable description.
    source_alt: str | None = Field(default=None, exclude_if=lambda v: v is None)
    # Links and coloured runs in `text` (offsets into `text` as stored, markers included).
    links: list[Link] = Field(default_factory=list)
    colour_runs: list[ColourRun] = Field(default_factory=list)
    # Raised and lowered runs of the text (`ScriptRun`), written as <sup> and <sub>.
    script_runs: list[ScriptRun] = Field(default_factory=list)
    # Marked runs of the text (`Run`: struck text). Left out of the JSON when empty, so an
    # element without runs is stored, cached and rendered exactly as before runs existed.
    # Every place that moves, keeps or writes the spans of an element goes through
    # `core.inline`, so no span is dropped by one place that forgot its field.
    runs: list[Run] = Field(default_factory=list, exclude_if=lambda v: not v)
    # BCP 47 language of this passage, only when it differs from the document's
    # (`DocumentResult.lang`); written as a `lang` attribute on its block.
    lang: str | None = None

    @field_validator("lang")
    @classmethod
    def _lang_tag(cls, value: str | None) -> str | None:
        if value is not None and not LANG_TAG.fullmatch(value):
            raise ValueError("lang must be a BCP 47 tag such as 'fr' or 'pt-BR'")
        return value


class Fix(BaseModel):
    element_id: str
    kind: Kind
    status: Status
    confidence: float | None = None  # in [0, 1]; None when nothing was proposed
    equation: EquationFix | None = None
    figure: FigureFix | None = None
    heading: HeadingFix | None = None
    table: TableFix | None = None
    checks: list[Check] = Field(default_factory=list)
    calls: list[ModelCall] = Field(default_factory=list)
    reason: str | None = None  # category code for ABSTAINED / FAILED
    # True once a reviewer accepted, edited or rejected this fix on the review screen.
    # Defaults to False so results stored before the field existed still load.
    reviewed: bool = False
    # Review tier (see `Review`). Results stored before tiers existed get it from their
    # status (`default_review`): needs_review, failed, abstained -> required, else none.
    review: Review = "none"
    # Repeated headings decided together: every heading with the same label and style that
    # needed a decision shares this id ("hg-" + the first one's element id) and the group's
    # size, and the review screen asks once for the whole group. None when on its own.
    group_key: str | None = None
    group_size: int | None = None
    # The second automated check's answers about this fix (see `Verification`). Empty
    # when it did not run, and in results stored before it existed.
    verifications: list[Verification] = Field(default_factory=list)
    # Equations that started on the cheap model: what asking the main model again did.
    # "same" or "changed" (its LaTeX against the cheap answer's, whitespace aside),
    # "failed" (it gave no usable answer; the cheap one stands) or "skipped" (the cheap
    # answer's only reason to be asked again was a confidence just below the accept bar,
    # `equation_step.SKIP_ESCALATION_FROM`). None when no second reading was due. Left out
    # of the JSON when None, so a record without it is unchanged.
    escalation: str | None = Field(default=None, exclude_if=lambda v: v is None)
    # The reviewer's description in words of an item kept as an image (an equation, a
    # table, a page): written as the image's alt text instead of the PDF's own text or
    # the fixed words (T3-16). Cleaned and checked like any alt text (`web.review`); None
    # when there is none.
    described: str | None = Field(default=None, exclude_if=lambda v: v is None)
    # The sources that raised this decision, the one that opened it first (`core.triage.SOURCES`).
    # Empty is the pipeline's own flag: every result stored before sources existed. A name
    # that is not in the table is refused, so no source outside it can ask for a person.
    sources: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)
    # The risk band of a required decision (`core.triage.risk_rank`), fixed when it was asked:
    # a review changes the checks and the status the band is read from, and the review order
    # must not move while a person works. None: not set (read from the fix as it is).
    band: int | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("sources")
    @classmethod
    def _known_sources(cls, value: list[str]) -> list[str]:
        from readablestem.core.triage import SOURCES

        unknown = [name for name in value if name not in SOURCES]
        if unknown:
            raise ValueError("not a source of decisions: " + ", ".join(unknown))
        return value

    @model_validator(mode="before")
    @classmethod
    def _review_from_status(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("review") is None:
            return {**data, "review": default_review(data.get("status"))}
        return data

    @property
    def cost_usd(self) -> float:
        """Everything this fix cost: its model calls and its second-check requests."""
        return sum(c.cost_usd for c in self.calls) + self.verifier_cost_usd

    @property
    def verifier_cost_usd(self) -> float:
        return sum(v.cost_usd for v in self.verifications)

    @property
    def pending(self) -> bool:
        """Waiting for a person's decision (counts toward "not done yet")."""
        return self.status in PENDING_STATUSES

    @property
    def optional_note(self) -> bool:
        """Accepted automatically with an optional note no reviewer has acted on yet."""
        return self.review == "optional" and self.status is Status.ACCEPTED and not self.reviewed

    @property
    def accepted_by_reviewer(self) -> bool:
        return self.status is Status.ACCEPTED and self.reviewed

    @property
    def decided_by_reviewer(self) -> bool:
        """Accepted, edited or rejected by a reviewer (EDITED and REJECTED only come from
        review, also in results stored before `reviewed` existed)."""
        return self.reviewed or self.status in {Status.EDITED, Status.REJECTED}

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


class PageStats(BaseModel):
    page: int  # 0-based
    elements: int
    fixes: int
    cost_usd: float
    latency_ms: int


class DocumentStats(BaseModel):
    pages: int
    cost_usd: float
    latency_ms: int
    model_calls: int
    by_kind: dict[str, int] = Field(default_factory=dict)  # fixes per Kind
    by_status: dict[str, int] = Field(default_factory=dict)  # fixes per Status
    # Fixes per review tier ("required", "optional", "none") as the pipeline left them,
    # and the decisions the required ones make (a heading group counts once). Empty and
    # None in results stored before tiers existed (`core.triage` recomputes them).
    by_review: dict[str, int] = Field(default_factory=dict)
    required_decisions: int | None = None
    per_page: list[PageStats] = Field(default_factory=list)


# Notice codes known to this release (each has
# a sentence in web/messages.py NOTICE_MESSAGES). `Notice.code` stays a plain string, so a
# result written by a newer release with a code this one does not know still loads.
NOTICE_CODES = frozenset(
    {
        "table_not_rebuilt",  # a data table kept as an image of the table
        "scanned_page",  # a page that is only a picture of a page
        "code_block",  # program code, kept as plain text
        "footnote",  # a footnote, left where it falls in the text
        "colour_reference",  # the text refers to colour ("shown in red")
        "colour_used",  # text printed in colour that the converted file does not keep
        "colour_only",  # a figure tells things apart by colour alone
        "link_scheme_dropped",  # a PDF link that is not http, https or mailto: text only
        "link_text_unclear",  # link text that says nothing on its own ("click here")
        "lang_detected",  # the document language was detected from the text
        "lang_assumed",  # no language chosen or found in the text, and the file's own does not fit
        "lang_mixed",  # no one language holds most of the text: the largest is the document's
        "long_description_missing",  # a complex figure without a long description
        "scripts_flattened",  # raised or lowered text (an exponent, a charge) written flat
        "struck_text",  # text the page shows struck through, kept and marked as deleted
        "struck_not_kept",  # struck text in a table or a display equation, which carry no runs
        "form_fields",  # a fillable PDF form: its fields are read as text, not as live inputs
        "annotations_read",  # comments in the PDF's annotations, included as paragraphs
        "attachments_not_converted",  # files attached to the PDF, not converted
        "option_labels",  # answer options (A, B, C) that run together in one paragraph or item
        "rtl_text",  # right-to-left words, in the order the PDF gives them
        # Hidden text and page types (pipeline.page_type, docs/layout.md "Hidden text
        # and page types"). The first four have a decision: the page's (or region's)
        # FAILED figure fix with the same reason, grouped by reason.
        "handwritten_page",  # a handwritten page, kept as an image (not converted)
        "handwriting_on_page",  # handwriting on a typed page, kept as an image of it
        "unread_page",  # drawings or images, nothing read from them: page kept as image
        "page_read_failed",  # reading the page raised: kept as an image, the rest is read
        "ocr_text_layer_ignored",  # a hidden tablet or OCR layer that was not used
        "hidden_text_ignored",  # invisible or covered words on a typed page, left out
        # ... and a paragraph or more of them (`page_type.HIDDEN_BLOCK`): the instructor looks
        "hidden_text_check",
        # The page's drawings were not checked for handwriting: over a count of work, or the
        # clock guard fired (`page_type.InkBudget`, plan WS14.9). Its typed text was read.
        "drawings_not_analysed",
        # The table search's CPU time for the document ran out (tables.DetectorBudget): a table
        # on this page may be read as text.
        "tables_not_searched",
        # A PDF that had to be repaired when it was opened (`source_facts.repair_notices`):
        # "file_cut_off" when its last pages are empty (`page` the first of them, `count` how
        # many; required: the owner continues with the pages before them or uploads a fresh
        # copy), "file_repaired" when it has content on every page (optional).
        "file_cut_off",
        "file_repaired",
        # Review edits (T3-16): a page the reviewer left out of the student file.
        "page_omitted",
        # Review edits: paragraphs or list items of a page the reviewer left out (`count`).
        "block_omitted",
        # A scanned or handwritten page converted from an image of it (ADR 0007).
        "image_page_converted",
        # A second reader of handwritten equations (pipeline.image_page_reconcile) that gave
        # no reading for some equations: `count` of them, one notice for the document.
        "second_reader_unavailable",
        "second_reader_budget",
        # LaTeX source: text LaTeXML could not read (an unknown command), kept as written.
        "source_error",
        # LaTeX source: LaTeX errors the reader could not tie to a place in the result, and
        # pictures the source names that were not found or not read (one decision each).
        "source_unplaced",
        "source_figure_missing",
        # A source job (LaTeX or Word): no compile witness ran, so its text was not compared
        # with a compiled copy (`core.page_checks`, `source.convert`).
        "source_not_compared",
        # A page saved sideways (no /Rotate) that was turned to be read (`layout.document`).
        "page_turned",
        # LaTeX source: words or pictures no rule of the reader took (`source.report`
        # `SourceReport.unread_words`, `unread_pictures`): `count` of them.
        "source_unread",
        # Word or PowerPoint upload (`web.office_census`): what the PDF route left out or kept
        # open, counted from the file. Tracked changes still open are required; the rest are
        # optional. `count` is the number of changes, notes, slides or comments.
        "office_tracked_changes",
        "office_speaker_notes",
        "office_hidden_slides",
        "office_comments",
        # Word file read from the file itself (`source.docx_reader`): what the reader took
        # out of the reading or could not carry. `count`: words, boxes, parts or changes.
        "word_hidden_text",  # hidden text, left out as Word hides it
        "word_text_boxes",  # text in boxes the reader did not read (words lost, counted)
        "word_headers_footers",  # running headers and footers, not part of the text
        "word_objects",  # SmartArt, charts or embedded objects: not read
        "word_tracked_changes",  # tracked changes: the reader took the accepted text
        "word_table_flattened",  # a table too large for a grid, written as a list (`count`)
        "word_rtl_text",  # right-to-left paragraphs: words in reading order, no direction kept
    }
)


class Notice(BaseModel):
    """Something the job page lists for the instructor, beyond the review decisions.

    Content-free like `Check`: a category `code` (see `NOTICE_CODES`), where it is
    (0-based `page`, `element_id`, either may be None for the whole document) and how
    many times it occurs there (`count`). Never document text, so notices can be shown,
    logged and counted after the document is deleted; the one exception is `names` below.

    `review` places it on the job page: "required" and "none" are listed under "Not
    handled in this file", "optional" under "Worth checking". A notice never adds a
    decision of its own: when something needs one (`review="required"`, e.g. a table
    kept as an image), the decision is the `Fix` with the same `element_id`, and only
    fixes count towards `DocumentStats.required_decisions`. A required notice no fix
    stands behind (open tracked changes in a Word file, right-to-left text, answer options run
    together) keeps the job at "Ready", never "Ready to share": `web.states` lists those codes.
    """

    code: str
    page: int | None = Field(default=None, ge=0)
    element_id: str | None = None
    count: int = Field(default=1, ge=1)
    review: Review = "optional"
    # The names of the files attached to the PDF (`attachments_not_converted`, the only code
    # that has any): the owner's own words, shown on the owner's pages and kept with the
    # result, never logged or counted.
    names: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)


class SourceFacts(BaseModel):
    """Numbers about the input PDF for the job page's before/after card. Content-free.

    - `tagged`: the PDF says it is tagged (/MarkInfo /Marked); None when unknown;
    - `has_lang`, `has_title`: the catalog's /Lang and the metadata title are set;
    - `bookmarks`: outline entries; `images`: images on the analysed pages;
    - `images_with_alt`: of those, how many carry alt text (tagged PDFs only, else None);
    - `equations`: equations layout found; `equations_flattened`: of those, maths whose
      text layer loses its 2-D structure (fractions, scripts, roots), so a screen reader
      would read a flat string; `bold_maths_letters`: bold letters in maths that the
      text layer gives as plain characters (a vector read as a scalar);
    - `links`: link annotations; `tables`: data tables detected.
    """

    tagged: bool | None = None
    has_lang: bool = False
    has_title: bool = False
    bookmarks: int = Field(default=0, ge=0)
    images: int = Field(default=0, ge=0)
    images_with_alt: int | None = Field(default=None, ge=0)
    equations: int = Field(default=0, ge=0)
    equations_flattened: int = Field(default=0, ge=0)
    bold_maths_letters: int = Field(default=0, ge=0)
    links: int = Field(default=0, ge=0)
    tables: int = Field(default=0, ge=0)


class PageType(BaseModel):
    """What one page is, as `pipeline.page_type` measured it. Content-free: a kind, reason
    codes and counts, never text, so it can be aggregated after the document is deleted.

    - `kind`: born_digital, mixed_ink, mixed_hidden_text, scan_no_text, scan_ocr,
      handwritten, handwritten_ocr, vector_no_text or blank (a plain string, so a kind
      from a newer release still loads);
    - `trust`: which characters may be used ("all", "visible_only", "none", "empty");
    - `route`: where the page's content would have to come from ("text_layer",
      "text_layer_and_ink_regions", "visible_text_layer", "vision_page", "nothing").
      This release reads text layers only: a "vision_page" that is handwritten or
      scanned is kept as its page image, marked "Not converted";
    - `reasons`: content-free codes ("invisible_text", "ink_writing", ...);
    - counts: visible, invisible (render mode 3 or alpha 5 % or less) and covered
      (painted, then hidden under an image or a filled shape) characters, the share of
      the page under raster images, and the ink measures.
    """

    page: int = Field(ge=0)  # 0-based
    kind: str
    trust: str = "all"
    route: str = "text_layer"
    reasons: list[str] = Field(default_factory=list)
    visible_chars: int = Field(default=0, ge=0)
    invisible_chars: int = Field(default=0, ge=0)
    covered_chars: int = Field(default=0, ge=0)
    # Painted where nobody can see it (under 2 pt, or flat on the page): left out like the two
    # above, but no sign of a text layer from a scan. Not written when 0, and 0 in a record
    # stored before it was kept.
    unseen_chars: int = Field(default=0, ge=0, exclude_if=lambda v: not v)
    image_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    ink_strokes: int = Field(default=0, ge=0)
    ink_rows: int = Field(default=0, ge=0)
    ink_repeat_share: float = Field(default=0.0, ge=0.0, le=1.0)
    ink_pen_share: float = Field(default=0.0, ge=0.0, le=1.0)
    ink_size_twins: float = Field(default=0.0, ge=0.0, le=1.0)
    annot_strokes: int = Field(default=0, ge=0)
    # Why the ink was not analysed, beside the reason "ink_not_analysed": "work" (a count of
    # work ran out, the same on every machine) or "time" (the clock guard fired). None when
    # it was analysed, and in a record stored before this was kept.
    ink_cause: str | None = Field(default=None, exclude_if=lambda v: v is None)
    producer: str = "other"  # a coarse family of the producing software, a hint only
    classifier_version: str = "page-type-v5.1"

    @property
    def untrusted_chars(self) -> int:
        return self.invisible_chars + self.covered_chars

    @property
    def left_out_chars(self) -> int:
        """Every character of the page's text layer that was left out: what the notice counts."""
        return self.untrusted_chars + self.unseen_chars


class OmittedPage(BaseModel):
    """A page the reviewer left out of review and of the student file (T3-16): a blank
    page, a cover. Its elements, fixes and notices are moved here, out of the result's
    own lists, so nothing downstream (the output, the decisions, the counts) sees them;
    putting the page back moves them home again. Nothing is deleted."""

    page: int = Field(ge=0)  # 0-based
    elements: list[Element] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)
    # The page was listed in `DocumentResult.image_only_pages` (kept whole as an image): it
    # is taken off that list while it is out, and put back on it with the page.
    image_only: bool = Field(default=False, exclude_if=lambda v: not v)


class LeftOutBlock(BaseModel):
    """One paragraph or list item the reviewer left out of the student file: running-head
    text, a printer's mark or a scanned line that got into the body. It is the size of one
    element of what `OmittedPage` is for a page: the element, its fixes and its own notices
    are moved here, out of the result's lists, so no writer, count or decision sees them;
    putting it back moves them home again. Nothing is deleted, and the words stay here, so
    the page's card can list it and the record can say what was taken out."""

    element: Element
    fixes: list[Fix] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)


# Where `DocumentResult.lang` came from: the PDF's /Lang, detection from the text, the
# default ("en") when neither gave one, or the instructor's own choice on the job page.
LangSource = Literal["pdf", "detected", "default", "user"]


class PageMathSummary(BaseModel):
    """What the page math pass did for one document (ADR 0006). Content-free: counts and
    cost only. `cost_usd` is also in `DocumentStats.cost_usd`."""

    pages: int = 0  # pages the model was asked about
    failed: int = 0  # pages with no usable answer
    calls: int = 0
    cost_usd: float = 0.0
    proposed: int = 0  # expressions the model proposed
    verified: int = 0  # ... whose quotes held
    relocated: int = 0  # ... found elsewhere in their row than stated
    dropped: int = 0  # ... dropped, a quote that held nowhere
    overlapped: int = 0  # ... dropped, overlapping another
    applied_pages: int = 0  # pages where the plan was laid over layout
    stale_pages: int = 0  # pages whose rows changed between the two layout runs
    blocks: int = 0  # display spans placed
    inline: int = 0  # inline spans placed
    numbers: int = 0  # exercise or item numbers cut off
    labels: int = 0  # equation numbers cut off
    refused: int = 0  # display spans that would split a layout region: layout's cut is kept
    inconsistent: int = 0  # spans whose LaTeX and quoted text disagree
    absorbed: int = 0  # bracket rows taken into a placed region
    stopped_by: str | None = None  # a spend cap that ended the pass early


class PageWitnessSummary(BaseModel):
    """What the page witness did for one document. Content-free: counts and cost only.
    `cost_usd` is also in `DocumentStats.cost_usd`."""

    pages: int = 0  # maths pages the model was asked about
    failed: int = 0  # pages with no usable answer (refused, invalid, budget)
    refused: int = 0  # ... of them refused
    calls: int = 0
    split_pages: int = 0  # dense pages asked in more than one window
    unoffered_rows: int = 0  # rows left out of every window of a very dense page
    cost_usd: float = 0.0
    proposed: int = 0  # formulas the model proposed
    verified: int = 0  # ... whose quotes held
    dropped: int = 0  # ... dropped, a quote that held nowhere or an overlap
    matched: int = 0  # formulas whose extent is within one glyph of an element's
    extent_differs: int = 0  # overlapping an element, extent differs by more
    added: int = 0  # witness-only formulas made new elements (glyph evidence)
    no_glyph_evidence: int = 0  # witness-only formulas without maths glyphs: left out
    inside_text: int = 0  # witness-only formulas inside another element: left to it
    stopped_by: str | None = None  # a spend cap that ended the pass early


class ImagePageRecord(BaseModel):
    """What the image page pass did for one page (ADR 0007). Content-free: a code, counts,
    cost. `outcome` is "converted" or why the page stayed an image: a `ModelCall.outcome`
    code ("refused", "invalid_output", "timeout", "api_error", "budget_exceeded"), "empty"
    (no usable block), "no_image" (the page could not be drawn) or "error"."""

    page: int = Field(ge=0)  # 0-based
    outcome: str
    model: str = ""
    calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    blocks: int = 0
    # The blocks by kind ("paragraph": 4) as the model gave them, and what became of them.
    by_kind: dict[str, int] = Field(default_factory=dict)
    illegible: int = 0  # "[illegible]" markers in the page's text
    low_confidence: int = 0  # blocks the model called medium or low
    kept_as_image: int = 0  # blocks whose LaTeX, grid or box could not be used
    omitted: int = 0  # regions of ink the blocks leave out: each a required decision
    recovered: int = 0  # blocks read from a picture of a left-out region (not in `omitted`)
    required: int = 0  # required decisions the page's blocks start with
    optional: int = 0  # optional notes the page's blocks start with


class SecondReaderSummary(BaseModel):
    """What the second reader of handwritten equations did in one job (`pipeline.image_page_
    reconcile`). Counts and seconds only, so the parent process can log it for an operator."""

    reader: str
    considered: int = 0  # handwritten display equations it could have read
    read: int = 0  # reads that gave a reading
    agreed: int = 0
    differed: int = 0
    with_choices: int = 0  # differed, and the difference became choices on the card
    unavailable: int = 0  # no reading: an error, a timeout, an unusable answer, or no time left
    over_budget: int = 0  # not asked: the readers' own dollar share was spent
    seconds: float = 0.0
    cost_usd: float = 0.0


class ImagePagesSummary(BaseModel):
    """The image page pass for a document (ADR 0007). Content-free; `cost_usd` is also in
    `DocumentStats.cost_usd`."""

    pages: int = 0  # pages the model was asked about
    converted: int = 0
    fell_back: int = 0  # pages kept as images after the model was asked (or could not be)
    calls: int = 0
    cost_usd: float = 0.0
    blocks: int = 0
    stopped_by: str | None = None  # a spend cap that ended the pass early
    per_page: list[ImagePageRecord] = Field(default_factory=list)
    second_reader: SecondReaderSummary | None = Field(default=None, exclude_if=lambda v: v is None)


class SourceProblem(BaseModel):
    """One line LaTeXML wrote about a source (`source.report`): an error, a fatal error, or a
    file, package or class it could not find.

    Unlike `Notice` and `Check`, this holds the instructor's own names (a command, a file
    name, a package): it is kept with the document, removed with it, and never logged; logs
    get the counts per category only. `name` and `file` are trimmed to printable characters
    and 120 of them; `file` is relative to the project ("" when LaTeXML named no file of the
    project). `what` says what a missing name is: a class or package with no binding
    ("class", "package"), a package whose binding needs the package's own file
    ("package_source"), or a file."""

    level: Literal["error", "fatal", "missing"]
    category: str
    name: str = ""
    file: str = ""
    line: int | None = None
    step: Literal["latexml", "post"] = "latexml"
    what: Literal["class", "package", "package_source", "file"] | None = None


class SourceMark(BaseModel):
    """A place in the converted document where LaTeXML left an error node (`ltx_ERROR`): the
    element that holds it (None when the reader did not place it) and whether it sits in a
    formula. `name` is the node's text (a command, as written), trimmed like `SourceProblem`.
    `decided`: a formula's error whose element holds a required decision about it (its own
    equation, or the table, heading or figure that shows the formula as written)."""

    name: str = ""
    element_id: str | None = None
    in_formula: bool = False
    decided: bool = Field(default=False, exclude_if=lambda v: not v)


class SourceFigure(BaseModel):
    """A figure whose picture could not be read: the file name the source asked for."""

    element_id: str
    name: str


class SourceReport(BaseModel):
    """What LaTeXML said about a source, and where its error nodes ended up (`source.report`).

    Three totals that should agree: `log_errors` (errors LaTeXML counted), `error_nodes`
    (error nodes in its XHTML) and the marks the reader placed; `unplaced_errors` is the
    number of error nodes the reader could not tie to an element. `counts` holds every error
    by category, content-free; `problems` keeps at most 20 named lines per category and
    `omitted` the number left out. Harmless warnings (a helper package LaTeXML's own
    bindings ask for) are counted in `warnings` and never named.

    `unread_words` and `unread_pictures` count what LaTeXML wrote that the reader neither
    read nor left out by a named rule (`source.ltx_reader.SKIP`); `unread_start` is the
    first of those words, as written."""

    problems: list[SourceProblem] = Field(default_factory=list)
    missing: list[SourceProblem] = Field(default_factory=list)
    marks: list[SourceMark] = Field(default_factory=list)
    figures: list[SourceFigure] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    omitted: dict[str, int] = Field(default_factory=dict)
    warnings: int = 0
    log_errors: int = 0
    error_nodes: int = 0
    unplaced_errors: int = 0
    unread_words: int = Field(default=0, ge=0, exclude_if=lambda v: not v)
    unread_pictures: int = Field(default=0, ge=0, exclude_if=lambda v: not v)
    unread_start: str = Field(default="", exclude_if=lambda v: not v)

    @property
    def empty(self) -> bool:
        """Nothing to tell the instructor: no error, no missing file, no error node, nothing
        left unread."""
        return not (self.problems or self.missing or self.marks or self.figures) and not (
            self.log_errors or self.error_nodes or self.unread_words or self.unread_pictures
        )


class DocumentResult(BaseModel):
    """Everything the pipeline produced for one PDF. Crop images are kept separately."""

    pipeline_version: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)  # step -> version
    title: str | None = None
    lang: str = "en"
    elements: list[Element] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)
    stats: DocumentStats
    html: str = ""
    # Run-level outcome, content-free like the codes above. `stopped_by` is the reason a
    # spend cap stopped model work early ("budget_daily", "budget_job_usd",
    # "budget_job_calls"): every equation and figure not done by then is FAILED with that
    # reason and keeps its original image. None when the run was not cut short.
    stopped_by: str | None = None
    # 0-based pages kept whole as a page image, flagged as not converted, because
    # nothing on them is converted: scanned and handwritten pages, and pages with
    # drawings or images from which nothing could be read (before pipeline 0.4.0's page
    # types: scanned pages only, one image covering the page and no text).
    image_only_pages: list[int] = Field(default_factory=list)
    # Static checks of `html` (pipeline.html_checks.static_checks) when it was rendered.
    html_checks: list[Check] = Field(default_factory=list)
    # 0-based page -> (width, height) in points, the frame of the element boxes (the page
    # as layout measured it, after rotation): the review screen places its marks on the
    # page images with them. Empty in results stored before it existed (the review screen
    # then asks the PDF itself).
    page_sizes: dict[int, tuple[float, float]] = Field(default_factory=dict)
    # What the job page lists beyond the review decisions (see `Notice`).
    notices: list[Notice] = Field(default_factory=list)
    # Facts about the input PDF for the before/after card. None when not measured.
    source_facts: SourceFacts | None = None
    lang_source: LangSource = "default"
    # One record per analysed page (`PageType`), in page order. Empty in results stored
    # before page types existed.
    page_types: list[PageType] = Field(default_factory=list)
    # Pages the reviewer left out (`OmittedPage`); empty for every result before the
    # option existed, and left out of the JSON then.
    omitted: list[OmittedPage] = Field(default_factory=list, exclude_if=lambda v: not v)
    # Paragraphs and list items the reviewer left out (`LeftOutBlock`); empty for every
    # result before the option existed, and left out of the JSON then.
    left_out_blocks: list[LeftOutBlock] = Field(default_factory=list, exclude_if=lambda v: not v)
    # 0-based page -> the page's printed label ("iv", "12"): the PDF's own page label, else
    # the number printed in the margin (`pipeline.page_labels`). Only pages that have one;
    # the output's page markers say the PDF position for the rest. Left out of the JSON when
    # empty, so a result stored before it existed reads the same.
    page_labels: dict[int, str] = Field(default_factory=dict, exclude_if=lambda v: not v)
    # A part of a longer file: the 0-based page of that file each of this result's pages is
    # (index = this result's page), so the page markers can say "PDF page 31" for a page
    # without a printed label. Empty for a document that is not a part.
    source_pages: list[int] = Field(default_factory=list, exclude_if=lambda v: not v)
    # The page math pass (ADR 0006); None when the flag was off, and left out of the JSON.
    page_math: PageMathSummary | None = Field(default=None, exclude_if=lambda v: v is None)
    # The page witness (`pipeline.page_witness`); None when the flag was off.
    page_witness: PageWitnessSummary | None = Field(default=None, exclude_if=lambda v: v is None)
    # The image page pass (ADR 0007); None when the flag was off, and left out of the JSON.
    image_pages: ImagePagesSummary | None = Field(default=None, exclude_if=lambda v: v is None)
    # Where the document came from: "pdf" (every result before this field), "latex" (a LaTeX
    # source converted by `readablestem.source`, which has no pages to show), "beamer" or "docx"
    # (a Word file read from the file itself, `source.docx_reader`). Left
    # out of the JSON for a PDF, so a stored result reads the same.
    origin: Literal["pdf", "latex", "beamer", "docx"] = Field(
        default="pdf", exclude_if=lambda v: v == "pdf"
    )
    # The symbols of the job's course the conversion was handed (`web.worker`, which hands them
    # over), for the job page's "Read with 6 symbols from ..." line. None: no record (a
    # result from before it, a document with no course, a source file), left out of the JSON;
    # an empty list: the course had no symbols then.
    notation_applied: list[AppliedSymbol] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    # A source only: what LaTeXML reported and where its error nodes are (`SourceReport`).
    # It holds names from the source, so it lives and dies with the document. None for a PDF
    # and for a source converted before it existed, and left out of the JSON then.
    source_report: SourceReport | None = Field(default=None, exclude_if=lambda v: v is None)

    @property
    def omitted_pages(self) -> list[int]:
        """0-based pages left out, in order."""
        return sorted(o.page for o in self.omitted)

    @property
    def left_out_block_pages(self) -> list[int]:
        """0-based page of each paragraph left out, in reading order."""
        held = sorted(self.left_out_blocks, key=lambda b: (b.element.page, b.element.order))
        return [b.element.page for b in held]

    def fix_for(self, element_id: str) -> Fix | None:
        return next((f for f in self.fixes if f.element_id == element_id), None)


class CallFact(BaseModel):
    """One request a conversion sent to a model, with the page it was about. Content-free:
    codes, counts, seconds and dollars (`pipeline.step_facts`)."""

    # A `ModelCall.step`, or "second_check" (a `Verification`) or "second_reader".
    step: str
    page: int | None = None  # 0-based; None for a request about the whole document
    model: str = ""
    # Whether it is in the job's call count. A second check or second reading is not: only
    # its dollars count toward the job's limits.
    counted: bool = True
    input_tokens: int = 0  # the whole prompt, cached and cache-written tokens included
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    attempt: int = 1
    is_fallback: bool = False
    outcome: str = "ok"
    # The part of `cost_usd` charged at the reservation, no usage having come back
    # (`ModelCall.billed_unseen_usd`): totaled apart from settled spend.
    billed_unseen_usd: float = 0.0


class StageFact(BaseModel):
    """The wall time of one stage of a job. The stages of a job do not overlap."""

    stage: str
    seconds: float


# Why a step stopped before it had done all of its work (`NotFinished.reason`):
# - "work_budget": it had done the count of work it is allowed (the same on every machine);
# - "time_limit": a clock stopped it, so another machine may have got further;
# - "memory_limit": it would have held more memory than it may;
# - "cap_reached": the job's own limit on dollars or on model calls was reached.
NotFinishedReason = Literal["work_budget", "time_limit", "memory_limit", "cap_reached"]


class NotFinished(BaseModel):
    """A step that stopped before it had done all of its work. Content-free: the step's name,
    a page number, a code and two counts.

    A limit may stop a step; it may not quietly change what a document contains. So every
    step that stops early says so here, and a difference between two conversions of one file
    is allowed only where the slower one holds such a fact (tests/test_determinism.py)."""

    step: str
    page: int | None = None  # 0-based; None for a step over the whole document
    reason: NotFinishedReason
    done: int = 0  # units of the step's work that were done (elements, pages, reads)
    of: int = 0  # units there were to do


class ShadowFact(BaseModel):
    """What a step run in shadow said next to the answer the conversion kept
    (`pipeline.shadow`). Content-free: it holds neither answer, only whether they were the
    same, and the type of the exception when the step failed."""

    step: str
    page: int | None = None  # 0-based; None for a step over the whole document
    outcome: Literal["same", "differs", "failed"]
    error: str = ""  # the exception's class name when the outcome is "failed"
    # A step that measures a page (`Shadow.over_line`) says its figure: a share of the page's
    # words, 0 to 1. None for a step that only compares two answers.
    share: float | None = None


class StepFacts(BaseModel):
    """What a conversion measured of itself (`pipeline.step_facts`). Content-free."""

    calls: list[CallFact] = Field(default_factory=list)
    stages: list[StageFact] = Field(default_factory=list)
    not_finished: list[NotFinished] = Field(default_factory=list)
    shadow: list[ShadowFact] = Field(default_factory=list)


class ConversionOutput(BaseModel):
    """Return value of `pipeline.run.convert_pdf`: the result plus element crops (PNG), and
    the conversion's own measurements when it made any."""

    result: DocumentResult
    crops: dict[str, bytes] = Field(default_factory=dict)  # element_id -> PNG bytes
    facts: StepFacts | None = None


# --------------------------------------------------------------------------- notation

NotationMeaning = Literal["vector", "matrix", "set", "operator", "constant", "unit", "other"]


class NotationEntry(BaseModel):
    """One entry of a course's notation memory ("bold d is a vector").

    `symbol` is what the instructor wrote ("bold d", "\\mathbf{d}", or the styled letter
    itself); `canonical` is the key it is matched by, from `core.notation.canonical_symbol`
    (a styled Unicode letter such as U+1D41D for bold d and U+211D for the reals, or a
    letter followed by a combining arrow or hat); one entry per key in a course. `note` is
    the instructor's own short wording, if any. `source`: "user" (typed on the course
    page), "review" (remembered from a review decision) or "document" (ticked from the
    notation the course's documents use).

    User content: it belongs to the instructor's course, travels to a model only fenced
    as untrusted data (like document text), and is deleted with the course or the user.
    """

    symbol: str = Field(min_length=1, max_length=64)
    canonical: str = Field(min_length=1, max_length=64)
    meaning: NotationMeaning
    note: str | None = Field(default=None, max_length=200)
    source: Literal["user", "review", "document"] = "user"


class AppliedSymbol(BaseModel):
    """One symbol a conversion was handed from its course, as the job page lists it: the key
    (`NotationEntry.canonical`) and its meaning, nothing else (not the instructor's spelling or
    note). User content: stored with the result, deleted with the job, never logged."""

    canonical: str = Field(min_length=1, max_length=64)
    meaning: NotationMeaning


# --------------------------------------------------------------------------- run options


class ConversionOptions(BaseModel):
    """Per-conversion choices beyond `Settings`: the feature flags in force and the
    course's notation. `pipeline.run.convert_pdf` takes one (from the job: the flags and
    the job's course); with none it uses `from_settings`. Steps read the one in force
    with `pipeline.options.current()`.

    Options decide what the steps produce, never how a stored result is rendered: the
    review screen re-renders the HTML from the stored result alone.
    """

    notation: list[NotationEntry] = Field(default_factory=list)
    tables: bool = False
    long_descriptions: bool = False
    colour: bool = False
    # Write struck runs (CONTENT_RUNS, on by default); off, they are counted, not written.
    content_runs: bool = True
    notation_memory: bool = False
    # A page math pass places each page's equations (ADR 0006).
    page_math: bool = False
    # A page witness reads every formula of a maths page (`pipeline.page_witness`).
    page_witness: bool = False
    # Scanned and handwritten pages are read from an image of the page (ADR 0007).
    image_pages: bool = False
    # The document is an exam or graded assessment: figure alt text describes what is
    # shown and never the result (prompt `figure_exam.md`), and the long description and
    # data table, which give values and trends, are not asked for even when
    # `long_descriptions` is on. A per-job choice (the upload form), not a deployment
    # flag, so `from_settings` leaves it off.
    exam_mode: bool = False
    # The language the person chose at upload ("Language of the document"): the document's
    # language, stated (`text_pass`). Left out of the dump, so it is no part of a step's key:
    # no step reads the language, only the text pass and the page's `lang`.
    language: str | None = Field(default=None, exclude=True)

    @classmethod
    def from_settings(cls, settings: Settings) -> ConversionOptions:
        """The flags from settings (FEATURE_*), no notation."""
        return cls(
            tables=settings.feature_tables,
            long_descriptions=settings.feature_long_descriptions,
            colour=settings.feature_colour,
            content_runs=settings.content_runs,
            notation_memory=settings.feature_notation_memory,
            page_math=settings.feature_page_math,
            page_witness=settings.feature_page_witness,
            image_pages=settings.feature_image_pages,
        )


# Stages of a conversion as the job page shows them, in order.
Stage = Literal["queued", "layout", "equations", "figures", "tables", "assemble", "checks"]


class ProgressEvent(BaseModel):
    """Live progress of one stage (`convert_pdf(stage_progress=...)`). Content-free:
    `done` of `total` units (pages or elements, per stage) and `counts`, elements found
    per kind ("equation": 12), so it can be stored on the job row (`jobs.progress_json`)."""

    stage: Stage
    done: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
