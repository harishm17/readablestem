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
links, language, colour, notation memory) are described in docs/design/content-features.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from a11y_stem.config import Settings

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


class Check(BaseModel):
    """Result of one deterministic validator. Content-free by design."""

    name: str  # e.g. "latex.allowed_commands", "mathml.well_formed", "alt.length"
    passed: bool
    severity: Literal["error", "warning"] = "error"
    code: str | None = None  # category when it fails, e.g. "unknown_command"


Step = Literal["equation", "figure", "judge", "table"]

Outcome = Literal[
    "ok", "refused", "invalid_output", "timeout", "api_error", "budget_exceeded", "skipped"
]


class ModelCall(BaseModel):
    """One request to a model provider (one attempt). Content-free."""

    step: Step
    model: str
    prompt_version: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens, which are billed as output
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempt: int = 1
    is_fallback: bool = False
    outcome: Outcome = "ok"


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
    display: Literal["block", "inline"] = "block"


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

    @property
    def is_empty(self) -> bool:
        return not (self.text.strip() or (self.latex or "").strip())


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
    """

    rows: int = Field(ge=1, le=MAX_TABLE_ROWS)
    cols: int = Field(ge=1, le=MAX_TABLE_COLS)
    cells: list[TableCell] = Field(default_factory=list, max_length=MAX_TABLE_CELLS)
    header_rows: int = Field(default=0, ge=0)
    header_cols: int = Field(default=0, ge=0)
    caption: str | None = None
    is_data: bool = True
    source: Literal["model", "local"] = "local"


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


class FigureFix(BaseModel):
    figure_type: FigureType
    alt: str  # short alt text; "" only when decorative
    decorative: bool = False
    # Complex figures (charts, diagrams): a longer description in plain text, written
    # after the figure. None when the figure needs none or the model gave none.
    long_description: str | None = None
    # Charts: the plotted values as a data table (`table_html.render_table` writes it).
    data_table: TableGrid | None = None
    # True when every number in `data_table` appears in the PDF text inside the figure
    # region; False when some were read from the image (approximate: a required
    # decision); None when there is no data table.
    data_values_from_text: bool | None = None
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
    - "crossref": text such as "Figure 2.3", "Equation (3)" or "Table 1"; `target_id` is
      the figure, equation or table it names.
    """

    kind: Literal["uri", "internal", "crossref"]
    href: str | None = None
    target_id: str | None = None

    @model_validator(mode="after")
    def _target(self) -> Link:
        if self.kind == "uri":
            if self.href is None or not is_safe_href(self.href):
                raise ValueError("a uri link needs an http, https or mailto href")
            if self.target_id is not None:
                raise ValueError("a uri link has no target_id")
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


class HeadingFix(BaseModel):
    level: int = Field(ge=1, le=6)
    text: str


class Element(BaseModel):
    """Something on a page, in document reading order."""

    id: str  # stable within a document: "p{page+1}-e{index}"
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
    # Equations: letters of `text` the PDF draws, not bold, in a font known to have a bold
    # face (Computer Modern, Times, ...), sorted, no repeats. Such a letter is known to be
    # plain, so a bold reading of it (a scalar read as a vector) is a real difference.
    # None when layout did not record it (older results, equations built outside layout).
    regular_letters: str | None = None
    # Tables: the grid layout built from the PDF's own words and lines (source "local").
    table: TableGrid | None = None
    # Links and coloured runs in `text` (offsets into `text` as stored, markers included).
    links: list[Link] = Field(default_factory=list)
    colour_runs: list[ColourRun] = Field(default_factory=list)
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


# Notice codes known to this release (docs/design/content-features.md "Notices"; each has
# a sentence in web/messages.py NOTICE_MESSAGES). `Notice.code` stays a plain string, so a
# result written by a newer release with a code this one does not know still loads.
NOTICE_CODES = frozenset(
    {
        "table_not_rebuilt",  # a data table kept as an image of the table
        "scanned_page",  # a page that is only a picture of a page
        "code_block",  # program code, kept as plain text
        "footnote",  # a footnote, left where it falls in the text
        "colour_reference",  # the text refers to colour ("shown in red")
        "colour_only",  # a figure tells things apart by colour alone
        "link_scheme_dropped",  # a PDF link that is not http, https or mailto: text only
        "link_text_unclear",  # link text that says nothing on its own ("click here")
        "lang_detected",  # the document language was detected from the text
        "lang_assumed",  # no language in the PDF and none detected: the default is used
        "long_description_missing",  # a complex figure without a long description
    }
)


class Notice(BaseModel):
    """Something the job page lists for the instructor, beyond the review decisions.

    Content-free like `Check`: a category `code` (see `NOTICE_CODES`), where it is
    (0-based `page`, `element_id`, either may be None for the whole document) and how
    many times it occurs there (`count`). Never document text, so notices can be shown,
    logged and counted after the document is deleted.

    `review` places it on the job page: "required" and "none" are listed under "Not
    handled in this file", "optional" under "Worth checking". A notice never adds a
    decision of its own: when something needs one (`review="required"`, e.g. a table
    kept as an image), the decision is the `Fix` with the same `element_id`, and only
    fixes count towards `DocumentStats.required_decisions`.
    """

    code: str
    page: int | None = Field(default=None, ge=0)
    element_id: str | None = None
    count: int = Field(default=1, ge=1)
    review: Review = "optional"


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


# Where `DocumentResult.lang` came from: the PDF's /Lang, detection from the text, the
# default ("en") when neither gave one, or the instructor's own choice on the job page.
LangSource = Literal["pdf", "detected", "default", "user"]


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
    # 0-based pages that are only a picture of a page (one image covering it, no text):
    # scanned pages. There is nothing to convert on them; the output keeps the image,
    # flagged as not converted.
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

    def fix_for(self, element_id: str) -> Fix | None:
        return next((f for f in self.fixes if f.element_id == element_id), None)


class ConversionOutput(BaseModel):
    """Return value of `pipeline.run.convert_pdf`: the result plus element crops (PNG)."""

    result: DocumentResult
    crops: dict[str, bytes] = Field(default_factory=dict)  # element_id -> PNG bytes


# --------------------------------------------------------------------------- notation

NotationMeaning = Literal["vector", "matrix", "set", "operator", "constant", "unit", "other"]


class NotationEntry(BaseModel):
    """One entry of a course's notation memory ("bold d is a vector").

    `symbol` is what the instructor wrote ("bold d", "\\mathbf{d}", or the styled letter
    itself); `canonical` is the key it is matched by, from `core.notation.canonical_symbol`
    (a styled Unicode letter such as U+1D41D for bold d and U+211D for the reals, or a
    letter followed by a combining arrow or hat); one entry per key in a course. `note` is
    the instructor's own short wording, if any. `source`: "user" (typed on the course
    page) or "review" (remembered from a review decision).

    User content: it belongs to the instructor's course, travels to a model only fenced
    as untrusted data (like document text), and is deleted with the course or the user.
    """

    symbol: str = Field(min_length=1, max_length=64)
    canonical: str = Field(min_length=1, max_length=64)
    meaning: NotationMeaning
    note: str | None = Field(default=None, max_length=200)
    source: Literal["user", "review"] = "user"


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
    notation_memory: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> ConversionOptions:
        """The flags from settings (FEATURE_*), no notation."""
        return cls(
            tables=settings.feature_tables,
            long_descriptions=settings.feature_long_descriptions,
            colour=settings.feature_colour,
            notation_memory=settings.feature_notation_memory,
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
