"""Data tables on a page: find them, check them, and build their grid from the PDF's words.

Called by `layout` for every analysed page (which has passed the probe), before columns,
paragraphs, headings and equations are worked out, so the rows of a table are claimed
from the text flow. Deterministic; no model calls. docs/tables.md explains the numbers.

1. **Propose** (`propose`): PyMuPDF Layout (`pymupdf-layout`, an ONNX layout model) with
   `page.find_tables(union=True, refine=True)`, under layout's PyMuPDF lock. The add-on is
   imported on first use, after onnxruntime is told to use only the CPUs this process may
   use (`cpu_allowance`): left alone it sizes its thread pools from the host's cores and
   runs 5-9 times slower on a 1-CPU container. MuPDF messages `find_tables` prints (they
   can quote the PDF) are captured and dropped.
2. **Arbitrate** (`judge`): on real course manuals the detector's precision was 0.68: it
   also boxes vector figures, tables of contents, equations and bullet lists. A box stays
   a table only when it has a grid: drawn rules with text in at least two columns, or text
   aligned in columns over at least three lines. Dot leaders (a table of contents), bullet
   columns (a list) and drawings or images with no real text grid (a figure) drop it. A
   blank worksheet table that the detector cut to its header row is extended along its
   vertical rules (`_extend`); a "Table N" caption boxed with the table is cut off
   (`_without_captions`).
3. **Structure** (`_build`): rows, columns and spans from the best fitting (`misfit`) of
   the drawn cell grid (`_ruled_slots`), SLANet-plus (PaddlePaddle, Apache-2.0, run as
   RapidTable does: `vendor/rapid_table.py`, `structure`) on a 144 dpi crop, and
   PyMuPDF's own refined grid. Without the model file (`table_model`) the other two
   compete.
4. **Fill** (`fill`): the cell text comes from the PDF's own glyphs, each word placed in the
   cell its box falls in (a subscript stays in its cell), never from OCR.
5. **Data or layout** (`is_term_grid`, `is_layout_grid`, `is_word_grid`): the local
   check of whether a claimed grid is a table of data. A grid of terms (`is_term_grid`:
   three or more columns of short, distinct terms, no digit, no "Table N" caption; an
   OpenStax "Key Terms" box) is claimed but marked not data (`TableGrid.is_data` False,
   reason `REASON_TERMS`): layout writes its terms in reading order as a list, with no
   decision and no image. A two-column grid of prose (or one column), or a two-column
   grid of single words, is text set out in boxes; it is not claimed and keeps the
   paragraph path (equations and headings in it are found as usual).

The result is a `PageTable` per data table: its box, its `TableGrid` (source "local"),
its "Table N" caption when one is printed just above or below it, the indices of the
lines and drawings it claims, and content-free reasons. Text is document content and only
leaves this module inside the grid; logs carry codes and counts.
"""

from __future__ import annotations

import bisect
import io
import itertools
import logging
import math
import os
import re
import statistics
import sys
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Generator, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pymupdf

from readablestem.core.types import (
    MAX_TABLE_CELLS,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    BBox,
    TableCell,
    TableGrid,
    grid_problems,
)
from readablestem.pipeline import table_model

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- thresholds
# Sizes are PDF points unless the name says otherwise.

CROP_DPI = 144  # structure crop resolution (what SLANet-plus was measured on)
CROP_PAD = 4.0  # the structure crop's margin around the table box
CLAIM_PAD = 2.0  # a glyph whose centre is this close to a table box belongs to it
SEGMENT_GAP = 0.8  # x font size: a wider gap between two glyphs of a line separates cells
WORD_GAP = 0.25  # x font size: a wider gap separates words
RULE_JOIN = 1.5  # rule pieces this close across belong to one rule
ROW_RULE_SPAN = 0.5  # a horizontal rule covers at least this share of the box width
COLUMN_RULE_SPAN = 0.35  # a vertical rule covers at least this share of the box height
MIN_GRID_LINES = 3  # lines with text in two or more columns for a table without rules
MIN_ALIGNMENT = 0.75  # share of a column's cells that line up (left, right or centre)
ALIGN_TOL = 2.0  # cells line up when their edges (or centres) are this close
CURVE_SHARE = 0.05  # one curve covering this share of the box makes it a drawing
IMAGE_SHARE = 0.2  # raster images covering this share of the box make it a picture
EDGE_GAP = 0.5  # x the table's text size: how close under the box a cut line sits
EDGE_SIZE_TOLERANCE = 0.6  # pt: the line's glyphs are the table's size, within this
EDGE_COLUMN_TOLERANCE = 3.0  # pt: it starts where a column of the grid starts, within this
CAPTION_MAX_GAP = 36.0  # furthest a "Table N" caption may sit from its table (as figures)
LAYOUT_CELL_CHARS = 60  # mean text length of a prose column in a layout grid
WORD_GRID_WORDS = 3  # a grid of terms: no cell longer than this (words) ...
WORD_GRID_ROWS = 6  # ... no more rows than this, no digit, and no "Table N" caption
TERM_GRID_WORDS = 4  # a term grid's terms: at most this many words ("method of adding
TERM_GRID_COLS = 3  # percents"), in at least this many columns (two are label | value)
TERM_MIN_LETTERS = 3  # every term has a word this long: "H", "kg", "m" are not terms
TOP_BAND = 0.07  # top share of the page where running headers live (as layout)
CONTINUED_TOL = 0.03  # column edges of a continued table match within this share of width
SNAP = 2.0  # x font size: a table box grows to the rules drawn this close around it
THIN_IMAGE = 1.6  # an image this thin is a rule (as RULE_THICKNESS in layout)
DOUBLE_RULE = 4.0  # two rules this close are one double rule
COLUMN_RULE_LINES = 2.2  # x font size: a column rule is at least about two lines long
GUTTER_PT = 3.0  # narrowest white gap between two columns of text
WORD_GUTTER_PT = (
    2.5  # narrowest strip between two columns that no word crosses (see `_word_gutters`)
)
WORD_GUTTER_LINES = 10  # a box needs this many lines of text before such a strip counts
WORD_GUTTER_FLUSH = 0.6  # share of the lines whose text ends and begins at the strip's edges
WORD_GUTTER_CROSSING = 0.1  # share of the lines a word may cross it on (a spanning title)
SCRIPT_SIZE = 0.85  # x the line's largest glyph: smaller glyphs may be scripts (as layout)
RAISED = 0.2  # x the full-size glyph: a smaller one with its baseline this far up is raised
STACK_GAP = 3.0  # x font size: furthest apart two rules of one booktabs table may be
BLOCK_ROW_GAP = 1.3  # x font size: widest white gap between two lines of one text block
TITLE_ROW_WORDS = 6  # a title row of a group of rows has no more words than this
HEADER_GAP = 3.5  # x font size: widest white gap under a header row of a block
BLOCK_MIN_LINES = 3  # lines with text in two or more cells that make a block of text columns
PROSE_WORDS = 6  # a cell of this many words is a line of running text, not a label or a term
FRAME_MIN_WIDTH = 100.0  # pt: a rule shorter than this does not frame a table
FRAME_MAX_RULES = 12  # rules of one extent on a page that are looked at together
FRAME_GRID_SHARE = 0.3  # share of a framed box's lines with text in two columns (callouts have few)
FRAME_WHOLE_RULES = 3  # a stack of this many rules (top, header, bottom) is one table as a whole
FRAME_MAX_HEIGHT = 0.8  # share of the page a framed table may take
PROSE_WIDTH = 0.25  # share of the page width a column's cells average to be running text
SCRIPT_CELL = 0.8  # x the text size: smaller cells are scripts or labels of a symbol
SMALL_TEXT = 0.7  # x the page's text size: lettering this small in a box made here is a diagram
PICTURE_REACH = 30.0  # pt around a box made here in which curves make it part of a plot
PICTURE_NEAR = 3  # curves reaching that far
PICTURE_MARKS = 4  # small curves in a box made here (a legend's markers, scatter dots)
PICTURE_FILLS = 12  # filled shapes in a box that make it a chart (bars), not a table
PICTURE_SHARE = 0.05  # raster images covering this share of a box made here make it a picture
MATH_SHARE = 0.6  # share of the lines with a relation or operator that makes a block maths
TURN_MIN_CHARS = 60  # a page needs this much text to be turned
TURN_SHARE = 0.6  # share of its characters set sideways, one way, to turn the page
EXTENT_TOL = 3.0  # pt: rules with ends this close have one extent

# The page test before detection (`may_hold_table`): a gap counts from this wide (x font
# size; the arbitration's own cut is SEGMENT_GAP, 0.8), and aligned text needs this many
# lines with such a gap at one x position (the arbitration's MIN_GRID_LINES).
PREFILTER_GAP = 0.5
PREFILTER_LINES = 3
# The detector (PyMuPDF Layout's model and `find_tables`) costs more the more there is drawn
# on a page: a lecture slide with 35,000 path items of pen strokes took 47 s on one core
# (the model's text extraction with vector collection 14 s of it, `get_drawings` four times
# after), a page with a table and a few hundred items under a second, a typed page with
# 5,000 items of pen strokes 0.4 s. A page with more items than this has no table
# proposed: its lines are read as text, as on a page the detector found nothing on. The
# largest page with a table in the repository's fixtures, the development set, the table
# sets and the research corpora has 26,500.
MAX_DETECTOR_ITEMS = 30_000
# A document's time budget for slow detections. Pages differ too much in cost to tell by
# what is drawn on them (a lecture slide with 200 pen-stroke paths of curves: 15 s, a page
# with 800 stroked paths of lines: 9 s, a page of 30,000 items of lines: 0.5 s), so the
# time is counted: each detection longer than DETECTOR_FREE_S (a page with a table takes
# under a second; 3 s leaves room for a slow core) draws the part beyond it from
# DETECTOR_EXCESS_S, and once that is used up the rest of the document's pages get no
# proposals. A document that stays within it reads exactly as without the budget.
DETECTOR_FREE_S = 3.0
DETECTOR_EXCESS_S = 60.0


class DetectorBudget:
    """What is left of a document's slow-detection time (`DETECTOR_EXCESS_S`)."""

    def __init__(self, excess_s: float = DETECTOR_EXCESS_S, free_s: float = DETECTOR_FREE_S):
        self.left = excess_s
        self.free = free_s

    def open(self) -> bool:
        return self.left > 0

    def spend(self, seconds: float) -> None:
        self.left -= max(0.0, seconds - self.free)


# Reasons on a TABLE element (`Element.reasons`), content-free like layout's.
REASON_RULED = "ruled grid"
REASON_TEXT_GRID = "aligned text grid"
REASON_SLANET = "SLANet-plus grid"
REASON_PYMUPDF = "PyMuPDF grid"
REASON_DRAWN_CELLS = "drawn cell grid"
REASON_EXTENDED = "box extended along rules"
REASON_TEXT_CELLS = "text cell grid"
REASON_CONTINUED = "continued from previous page"
REASON_TERMS = "grid of terms"  # claimed, not data: written as a list (`is_term_grid`)
# A row split by a page break (`join_split_row`): the table's last row goes on at the top
# of the next page's table, and that table's first row is the end of it.
REASON_ROW_SPLIT = "last row continues on the next page"
REASON_ROW_CONTINUED = "first row ends the last row of the page before"
# A line of the table's own size, outside its box, right under it and starting where a column
# starts: probably the rest of a cell the box ended a line too early (`_edge_text`). The grid
# and a model that reads the crop both miss it, so the table step asks a person.
REASON_EDGE_TEXT = "text at the table's edge outside its box"

# Verdicts of `judge` (codes, safe to log and count).
SOURCE_FRAME = "rule frame"  # `_rule_frames`: the span of a stack of rules of one extent
SOURCE_TEXT = "text block"  # `_text_blocks`: lines of text in aligned columns
TABLE = "table"
DROPPED = (
    "toc",
    "list",
    "labels",
    "figure",
    "no_grid",
    "overlap",
    "layout_grid",
    "no_structure",
    "page_text",
    "numbered",
    "no_words",
    "split",
    "container",
)

# "Table 2.3 Speeds", "TABLE 4: ...", "Tab. 1 ...", but not "Table 2 shows that ..." (as
# layout's "Figure N" CAPTION).
TABLE_CAPTION = re.compile(
    r"^(?:Table|TABLE|Tab\.|TAB\.)\s*\d+[A-Za-z]?(?:[.\-\u2013]\d+[A-Za-z]?)*"
    r"(?:\s*[.:|\u2013\u2014-]\s*|\s+(?=[A-Z(\"'\u201c])|\s*$)"
)
# Dot leaders of a table of contents: five dots (spaced or not), ellipses, middle dots.
_LEADERS = re.compile(r"(?:\.\s?){5,}|\u2026{2,}|(?:[\u00b7\u2219\u22c5]\s?){5,}")
# Spacing accents TeX and Word draw above a letter (as layout's _ACCENTS).
_ACCENTS = frozenset("\u02c6\u02dc\u00af\u02d9\u00a8\u02c7\u02d8")
# Bullets that start a list item (as layout's _BULLET_GLYPHS, with Wingdings' U+F0A7).
_BULLETS = frozenset(
    "\u2022\u25e6\u25aa\u25ab\u25a0\u25a1\u25cf\u25cb\u2023\u2043\u27a2\u25ba\u25b8\u25b6"
    "\u25b9\u27a4\u25c6\u25c7\uf0b7\uf0a7"
)
# Dots that are bullets when a space follows them (base-14 Helvetica prints U+2022 as
# a middle dot); maths (a dot product) otherwise.
_DOT_BULLETS = frozenset("\u00b7\u2219\u22c5")
_PAGE_REF = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,7})$", re.IGNORECASE)
_NUMBER = re.compile(r"^[\s(\[]*[-+\u2212\u00b1]?\$?\d[\d,.\s]*(?:[%)\]]|[eE][-+\u2212]?\d+)?\s*$")
_QUANTITY = re.compile(r"^[\s(\[]*[-+\u2212\u00b1]?\d")  # a value, never a column label


# ----------------------------------------------------------------------------- inputs


class GlyphLike(Protocol):
    """One character as layout reads it (`layout._Char`), in display coordinates."""

    @property
    def c(self) -> str: ...
    @property
    def x0(self) -> float: ...
    @property
    def y0(self) -> float: ...
    @property
    def x1(self) -> float: ...
    @property
    def y1(self) -> float: ...
    @property
    def size(self) -> float: ...
    @property
    def blank(self) -> bool: ...


class LineLike(Protocol):
    """One PyMuPDF line of glyphs (`layout._Row` before rows are merged)."""

    @property
    def chars(self) -> Sequence[GlyphLike]: ...
    @property
    def bbox(self) -> BBox: ...
    @property
    def vertical(self) -> bool: ...


class PathLike(Protocol):
    """One vector path as layout classifies it (`layout._Path`)."""

    @property
    def bbox(self) -> BBox: ...
    @property
    def kind(self) -> str: ...  # "hrule" | "vrule" | "radical" | "shape"
    @property
    def curved(self) -> bool: ...
    @property
    def filled(self) -> bool: ...


# ----------------------------------------------------------------------------- results


@dataclass
class Slot:
    """A cell of a candidate grid: where it sits and its box (PDF points, display space)."""

    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    bbox: BBox | None = None


@dataclass
class Proposal:
    """One box from the detector, with PyMuPDF's refined grid (the fallback grid)."""

    bbox: BBox
    slots: list[Slot]
    header_rows: int = 0
    source: str = "detector"  # "detector", or what found it here: SOURCE_FRAME, SOURCE_TEXT


@dataclass
class Judgement:
    """What `judge` decided about a proposal, and the evidence (numbers and codes only)."""

    verdict: str
    bbox: BBox  # the box after any extension along rules
    reasons: list[str] = field(default_factory=list)
    columns: int = 0
    grid_lines: int = 0
    alignment: float = 0.0
    row_rules: int = 0
    column_rules: int = 0


@dataclass
class PageTable:
    """A table found on a page, ready for layout to claim: a data table, or a grid of
    terms (`grid.is_data` False, `is_term_grid`), which layout writes as a list."""

    bbox: BBox
    grid: TableGrid
    caption: str | None
    lines: set[int]  # indices into the page's lines (table and caption)
    paths: set[int]  # indices into the page's paths (rules and cell shading)
    reasons: list[str]
    at_top: bool = False  # nothing but the running header above it on its page
    column_edges: list[float] = field(default_factory=list)  # for continued tables
    layout_grid: bool = False  # text set out in boxes (`is_layout_grid`): data only if captioned


# ============================================================================ the detector

_LOAD_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"detector": None, "structurer": None, "warned": set()}


@dataclass(frozen=True)
class SessionInfo:
    """Thread settings of one onnxruntime session the detector created (no content)."""

    model: str  # file name of the ONNX model
    intra_op_threads: int
    inter_op_threads: int
    spinning: str


LAYOUT_SESSIONS: list[SessionInfo] = []  # filled when pymupdf.layout loads its models


def cpu_allowance() -> int:
    """CPUs this process may use: its CPU affinity, capped by a cgroup CPU quota
    (Cloud Run's `--cpu`, Docker's `--cpus`), rounded up, at least 1."""
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    quota = _cgroup_cpu_quota()
    if quota is not None:
        count = min(count, max(1, math.ceil(quota - 1e-9)))
    return max(1, count)


_CGROUP_V2 = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1 = (
    Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
    Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us"),
)


def _cgroup_cpu_quota() -> float | None:
    """The cgroup CPU quota in CPUs, None when there is none (or it cannot be read)."""
    try:
        if _CGROUP_V2.is_file():
            quota, _, period = _CGROUP_V2.read_text().strip().partition(" ")
            if quota == "max" or not period:
                return None
            return int(quota) / int(period)
        quota_path, period_path = _CGROUP_V1
        if quota_path.is_file() and period_path.is_file():
            quota_us = int(quota_path.read_text().strip())
            period_us = int(period_path.read_text().strip())
            return quota_us / period_us if quota_us > 0 and period_us > 0 else None
    except (OSError, ValueError, ZeroDivisionError):
        return None
    return None


def _pinned_options(options: Any) -> Any:
    """Session options using `cpu_allowance()` threads, one inter-op thread, no spinning."""
    import onnxruntime as ort

    options = options if options is not None else ort.SessionOptions()
    options.intra_op_num_threads = cpu_allowance()
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return options


def _session_info(session: Any, model: object) -> SessionInfo:
    options = session.get_session_options()
    return SessionInfo(
        model=Path(str(model)).name,
        intra_op_threads=options.intra_op_num_threads,
        inter_op_threads=options.inter_op_num_threads,
        spinning=options.get_session_config_entry("session.intra_op.allow_spinning"),
    )


def _pin_layout_sessions() -> None:
    """Make every onnxruntime session made from pymupdf's own model files use the pinned
    thread settings. pymupdf.layout builds its sessions with `ort.SessionOptions()` and no
    thread count (`common_util.make_session`) and offers no option for it, so the class
    it calls is replaced by a subclass that sets them; sessions of other models (ours
    sets its own) pass through unchanged."""
    import onnxruntime as ort

    if getattr(ort.InferenceSession, "_a11y_pinned", False):
        return
    package = str(Path(pymupdf.__file__).resolve().parent)
    base = ort.InferenceSession

    class PinnedSession(base):
        _a11y_pinned = True

        def __init__(self, path_or_bytes: Any, sess_options: Any = None, *args: Any, **kw: Any):
            ours = isinstance(path_or_bytes, str | os.PathLike) and str(
                Path(path_or_bytes).resolve()
            ).startswith(package)
            if ours:
                sess_options = _pinned_options(sess_options)
            super().__init__(path_or_bytes, sess_options, *args, **kw)
            if ours:
                LAYOUT_SESSIONS.append(_session_info(self, path_or_bytes))

    setattr(ort, "InferenceSession", PinnedSession)  # noqa: B010  (ty: a subclass)


def load_detector() -> bool:
    """Import pymupdf.layout once (it loads its ONNX models at import), thread-pinned.
    False, with one content-free warning, when it cannot be loaded: no table is then
    proposed and the page is read as before."""
    with _LOAD_LOCK:
        state = _STATE["detector"]
        if state is not None:
            return state
        try:
            _pin_layout_sessions()
            with _quiet_stdout():
                import pymupdf.layout  # noqa: F401  (activates find_tables' layout model)
            _STATE["detector"] = True
        except Exception as exc:  # a missing wheel, a broken model file
            log.warning("table detector unavailable: %s", type(exc).__name__)
            _STATE["detector"] = False
        return _STATE["detector"]


@contextmanager
def _quiet_stdout() -> Generator[list[str]]:
    """Capture what PyMuPDF prints during the block, on this thread only, and drop it.

    `find_tables` reports errors with `pymupdf.message` (stdout by default), and the layout
    add-on uses `print`: either can quote the PDF, and stdout goes to the service logs.
    PyMuPDF's message stream is swapped (every PyMuPDF call runs under layout's lock),
    and `sys.stdout` is wrapped so other threads still write to the real stream. The
    yielded list gets one entry per captured write (the number is all that is kept).
    """
    captured: list[str] = []
    sink = _Sink(captured)
    old_message = getattr(pymupdf, "_g_out_message", None)
    old_stdout = sys.stdout
    sys.stdout = _ThreadStdout(old_stdout, sink, threading.get_ident())
    pymupdf._g_out_message = sink
    try:
        yield captured
    finally:
        pymupdf._g_out_message = old_message
        if isinstance(sys.stdout, _ThreadStdout) and sys.stdout.real is old_stdout:
            sys.stdout = old_stdout


class _Sink(io.TextIOBase):
    def __init__(self, captured: list[str]) -> None:
        self.captured = captured

    def write(self, text: str) -> int:
        if text.strip():
            self.captured.append("")  # counted, never kept: it may quote the PDF
        return len(text)

    def flush(self) -> None:
        pass


class _ThreadStdout(io.TextIOBase):
    """stdout that sends this thread's writes to a sink and everyone else's through."""

    def __init__(self, real: Any, sink: _Sink, thread: int) -> None:
        self.real, self.sink, self.thread = real, sink, thread

    def write(self, text: str) -> int:
        if threading.get_ident() == self.thread:
            return self.sink.write(text)
        return self.real.write(text) if self.real is not None else len(text)

    def flush(self) -> None:
        if threading.get_ident() != self.thread and self.real is not None:
            self.real.flush()


def _warn_once(code: str, message: str) -> None:
    if code not in _STATE["warned"]:
        _STATE["warned"].add(code)
        log.warning(message)


@contextmanager
def _pdf_lock() -> Generator[None]:
    """Layout's PyMuPDF lock (re-entrant: layout already holds it when it calls here)."""
    from readablestem.pipeline import layout

    with layout._LOCK:
        yield


def _display(page: pymupdf.Page) -> Callable[[Sequence[float]], BBox]:
    """Unrotated page box -> display box (the page's /Rotate applied), as layout does."""
    if page.rotation % 360 == 0:
        return lambda r: (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
    m = page.rotation_matrix

    def box(r: Sequence[float]) -> BBox:
        xs = (r[0] * m.a + r[1] * m.c + m.e, r[2] * m.a + r[3] * m.c + m.e)
        ys = (r[0] * m.b + r[1] * m.d + m.f, r[2] * m.b + r[3] * m.d + m.f)
        return min(xs), min(ys), max(xs), max(ys)

    return box


def may_hold_table(
    page: pymupdf.Page, lines: Sequence[LineLike], paths: Sequence[PathLike]
) -> bool:
    """Could `find` return a table on this page? False only when it cannot, and then the
    detector (an ONNX layout model, most of a page's analysis time) need not run.

    `judge` keeps a box only with a grid (module docstring, step 2), which is one of:

    - *aligned text*: text on at least three lines in two or more columns. Columns come
      from a gutter, an x position where each of those lines has a gap between two cells
      (wider than `SEGMENT_GAP`), or from a drawn column rule through them;
    - *ruled*: three row rules, or three column rules, or two of each, and text in two
      columns on at least one line;
    - *ruled cells*: three row rules and three column rules.

    So a page is left out when none of these can hold anywhere on it, counted loosely: a
    gap counts from `PREFILTER_GAP` (0.5 of the font size, looser than the 0.8 above, so a
    glyph the box leaves out cannot make the difference), a rule counts per position
    (pieces of one rule merged), and the three conditions are tested on the whole page,
    not in a box. It passes the pages it could keep a table on (checked on every PDF in
    the repository: tests/test_tables_prefilter.py) and many with none (columns of text, a
    callout box with a list in it), which the arbitration then drops as before.
    """
    if _page_turn(lines):
        return True  # the lines of a turned page are read in its turned frame (`_find_turned`)
    view = _PageView.of(page, lines, paths)
    rows, columns = _positions(y for _, _, y in view.hsegs), _positions(x for _, _, x in view.vsegs)
    cells = rows >= 3 and columns >= 3
    ruled = rows >= 3 or columns >= 3 or (rows >= 2 and columns >= 2)
    if cells:
        return True
    cuts = sorted({x for _, _, x in view.vsegs}) if columns < 3 else []
    gaps: list[tuple[float, int]] = []  # (x, +1 a gap opens / -1 it closes), over all lines
    lines_with_gap = 0
    for chars in _visual_lines(view.glyphs_in((-1e9, -1e9, 1e9, 1e9), pad=0.0)):
        last: GlyphLike | None = None
        wide = False
        for ch in chars:
            if ch.blank:
                continue
            if last is not None:
                gap = ch.x0 - last.x1
                size = max(ch.size, last.size, 1.0)
                if gap > PREFILTER_GAP * size or any(
                    last.x1 - 0.5 <= x <= ch.x0 + 0.5 for x in cuts
                ):
                    wide = True
                    gaps.append((min(last.x1, ch.x0), 1))
                    gaps.append((max(last.x1, ch.x0), -1))
            last = ch
        lines_with_gap += wide
    if ruled and lines_with_gap:
        return True
    # A gap opens before one closes at the same x: touching gaps share that position.
    depth = 0
    for _, step in sorted(gaps, key=lambda g: (g[0], -g[1])):
        depth += step
        if depth >= PREFILTER_LINES:
            return True
    return False


def _positions(values: Iterable[float]) -> int:
    """How many rule positions: values within `RULE_JOIN` of the one before are one."""
    count, last = 0, -1e9
    for value in sorted(values):
        if value - last > RULE_JOIN:
            count += 1
        last = value
    return count


def propose(page: pymupdf.Page, budget: DetectorBudget | None = None) -> list[Proposal]:
    """Candidate tables on a page that passed layout's probe (never run it on one that did
    not: the layout model renders the page, where the known MuPDF aborts are)."""
    if budget is not None and not budget.open():
        _warn_once("detector_budget", "table detection stopped: this document took too long")
        return []
    if not load_detector():
        return []
    with _pdf_lock():
        items = sum(len(d["items"]) for d in page.get_cdrawings())
    if items > MAX_DETECTOR_ITEMS:
        _warn_once("detector_items", "table detection skipped on a page with too many drawn items")
        return []
    started = time.perf_counter()
    with _pdf_lock(), _quiet_stdout() as captured:
        try:
            finder = page.find_tables(union=True, refine=True)
        except Exception as exc:  # find_tables catches most errors itself (returns None)
            log.warning("table detection failed on a page: %s", type(exc).__name__)
            return []
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()
            if budget is not None:
                budget.spend(time.perf_counter() - started)
        if captured:
            log.info("table detection: %d PyMuPDF messages dropped", len(captured))
        if finder is None:
            return []
        proposals = []
        for table in finder.tables:
            try:
                proposals.append(_proposal(table))
            except Exception as exc:  # a malformed grid from a damaged page
                log.warning("table grid unreadable: %s", type(exc).__name__)
    return proposals


def _proposal(table: Any) -> Proposal:
    """A found table as a proposal. `find_tables` already gives display coordinates: on a
    page with /Rotate it works on the turned page (measured: a 90-degree page's boxes
    match layout's display boxes), unlike `get_text`, whose boxes layout turns itself."""

    def box(r: Sequence[float]) -> BBox:
        return float(r[0]), float(r[1]), float(r[2]), float(r[3])

    slots: list[Slot] = []
    placements = getattr(table, "placements", None)
    if placements:
        occupied: set[tuple[int, int]] = set()
        for r, cells in enumerate(placements):
            c = 0
            for cell in cells:
                while (r, c) in occupied:
                    c += 1
                rs, cs = max(1, int(cell.rowspan)), max(1, int(cell.colspan))
                for dr in range(rs):
                    for dc in range(cs):
                        occupied.add((r + dr, c + dc))
                slots.append(Slot(r, c, rs, cs, box(cell.bbox) if cell.bbox else None))
                c += cs
    else:
        for r, row in enumerate(table.rows):
            for c, cell_box in enumerate(row.cells):
                if cell_box is not None:
                    slots.append(Slot(r, c, 1, 1, box(cell_box)))
    return Proposal(box(table.bbox), slots, int(getattr(table, "header_rows", 0) or 0))


# ============================================================================ arbitration


@dataclass
class _Seg:
    """Glyphs of one line with no wide gap between them: one cell's text on that line."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    line: int  # index of its visual line in the box
    columns: tuple[int, ...] = ()
    bold: bool = False


@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    line: int
    marked: str = ""  # `text` with its superscripts marked (`_marked`); cells read this

    def __post_init__(self) -> None:
        self.marked = self.marked or self.text


# Superscript forms; a raised run with a character not here is written "^(...)".
_SUPERSCRIPTS = {
    **dict(
        zip(
            "0123456789",
            "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079",
            strict=True,
        )
    ),
    "+": "\u207a",
    "-": "\u207b",
    "\u2212": "\u207b",  # minus sign
    "\u2013": "\u207b",  # en dash: some fonts print a raised minus as one
    "=": "\u207c",
    "(": "\u207d",
    ")": "\u207e",
    "n": "\u207f",
    "i": "\u2071",
}


def _raised(chars: Sequence[GlyphLike]) -> set[int]:
    """Indexes (into `chars`, one visual line, left to right) of the superscript glyphs:
    smaller than the full-size glyph before them (after them at the start of the line)
    and with their baseline above its baseline.

    Text layers flatten scripts (10^5 is "105", 10^-18 is "10-18" with an en dash),
    which would let a misread exponent pass the table step's word check, and would show a
    wrong number in the grid read from the PDF."""

    def baseline(c: GlyphLike) -> float:
        return getattr(c, "baseline", c.y1)  # layout._Char has it; ink bottoms otherwise

    def base_of(i: int) -> GlyphLike | None:
        """The nearest full-size glyph, looking past other script glyphs."""
        for j in itertools.chain(range(i - 1, -1, -1), range(i + 1, len(chars))):
            other = chars[j]
            if not other.blank and chars[i].size < SCRIPT_SIZE * other.size:
                return other
        return None

    largest = max((c.size for c in chars if not c.blank), default=0.0)
    raised: set[int] = set()
    for i, c in enumerate(chars):
        if c.blank or c.size >= SCRIPT_SIZE * largest:
            continue
        base = base_of(i)
        if base is not None and baseline(c) < baseline(base) - RAISED * base.size:
            raised.add(i)
    return raised


def _marked(chars: Sequence[GlyphLike], raised: Sequence[bool]) -> str:
    """The glyphs' text with each run of superscripts in superscript characters
    ("10⁻¹⁸"), or as "^(...)" when one has no superscript form."""
    out: list[str] = []
    i = 0
    while i < len(chars):
        if not raised[i]:
            out.append(chars[i].c)
            i += 1
            continue
        j = i
        while j < len(chars) and raised[j]:
            j += 1
        run = "".join(c.c for c in chars[i:j])
        if all(ch in _SUPERSCRIPTS for ch in run):
            out.append("".join(_SUPERSCRIPTS[ch] for ch in run))
        else:
            out.append(f"^({run})" if len(run) > 1 else f"^{run}")
        i = j
    return "".join(out)


@dataclass
class _PageView:
    """What arbitration needs from one page, gathered once."""

    lines: Sequence[LineLike]
    hsegs: list[tuple[float, float, float]]  # (x0, x1, y) of horizontal rule pieces
    vsegs: list[tuple[float, float, float]]  # (y0, y1, x) of vertical rule pieces
    curves: list[BBox]
    images: list[BBox]
    width: float
    height: float
    fills: list[BBox] = field(default_factory=list)  # filled areas that are not curved (bars)
    body_size: float = 10.0  # the page's median glyph size

    @classmethod
    def of(
        cls, page: pymupdf.Page, lines: Sequence[LineLike], paths: Sequence[PathLike]
    ) -> _PageView:
        to_display = _display(page)
        with _pdf_lock():
            images = [to_display(info["bbox"]) for info in page.get_image_info()]
        rect = page.rect
        return cls.build(lines, paths, images, rect.width, rect.height)

    @classmethod
    def build(
        cls,
        lines: Sequence[LineLike],
        paths: Sequence[PathLike],
        images: list[BBox],
        width: float,
        height: float,
    ) -> _PageView:
        hsegs: list[tuple[float, float, float]] = []
        vsegs: list[tuple[float, float, float]] = []
        curves: list[BBox] = []
        for path in paths:
            x0, y0, x1, y1 = path.bbox
            if path.kind == "hrule":
                hsegs.append((x0, x1, (y0 + y1) / 2))
            elif path.kind == "vrule":
                vsegs.append((y0, y1, (x0 + x1) / 2))
            elif path.kind == "shape" and path.curved:
                curves.append(path.bbox)
            elif path.kind == "shape" and not path.filled:  # a stroked rectangle: four rules
                hsegs += [(x0, x1, y0), (x0, x1, y1)]
                vsegs += [(y0, y1, x0), (y0, y1, x1)]
        pictures = []
        for x0, y0, x1, y1 in images:
            # dvips and Ghostscript draw TeX's rules as a one-pixel image stretched thin.
            if y1 - y0 <= THIN_IMAGE and x1 - x0 >= 3:
                hsegs.append((x0, x1, (y0 + y1) / 2))
            elif x1 - x0 <= THIN_IMAGE and y1 - y0 >= 3:
                vsegs.append((y0, y1, (x0 + x1) / 2))
            else:
                pictures.append((x0, y0, x1, y1))
        view = cls(lines, hsegs, vsegs, curves, pictures, width, height)
        view.fills = [p.bbox for p in paths if p.kind == "shape" and p.filled and not p.curved]
        sizes = [c.size for line in lines for c in line.chars if not c.blank and not line.vertical]
        if sizes:
            view.body_size = statistics.median(sizes)
        return view

    def glyphs_in(self, box: BBox, pad: float = CLAIM_PAD) -> list[list[GlyphLike]]:
        """The glyphs of each line whose centre lies in `box` (grown by `pad`)."""
        x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
        out = []
        for line in self.lines:
            lx0, ly0, lx1, ly1 = line.bbox
            if line.vertical or ly1 < y0 or ly0 > y1 or lx1 < x0 or lx0 > x1:
                continue
            chars = [
                ch
                for ch in line.chars
                if x0 <= (ch.x0 + ch.x1) / 2 <= x1 and y0 <= (ch.y0 + ch.y1) / 2 <= y1
            ]
            if any(not ch.blank for ch in chars):
                out.append(chars)
        return out


def _visual_lines(pieces: list[list[GlyphLike]]) -> list[list[GlyphLike]]:
    """Pieces that overlap vertically by half the smaller height form one visual line.
    Accents (TeX draws the arrow of a vector as its own glyph above the letter) go right
    after the glyph they sit over, so a cell reads "p\u20d7 = mv\u20d7"."""
    marks = [c for chars in pieces for c in chars if _is_mark(c)]
    boxes = []
    for chars in pieces:
        kept = [c for c in chars if not _is_mark(c)]
        ink = [c for c in kept if not c.blank]
        if ink:
            boxes.append((min(c.y0 for c in ink), max(c.y1 for c in ink), kept))
    boxes.sort(key=lambda b: (b[0] + b[1]) / 2)
    lines: list[list[Any]] = []  # [y0, y1, chars]
    for y0, y1, chars in boxes:
        if lines:
            line = lines[-1]
            overlap = min(y1, line[1]) - max(y0, line[0])
            if overlap >= 0.5 * min(y1 - y0, line[1] - line[0]):
                line[0], line[1] = min(line[0], y0), max(line[1], y1)
                line[2].extend(chars)
                continue
        lines.append([y0, y1, list(chars)])
    out = [sorted(line[2], key=lambda c: c.x0) for line in lines]
    for mark in marks:
        _attach_mark(out, mark)
    return out


def _is_mark(ch: GlyphLike) -> bool:
    return len(ch.c) == 1 and (unicodedata.combining(ch.c) > 0 or ch.c in _ACCENTS)


def _attach_mark(lines: list[list[GlyphLike]], mark: GlyphLike) -> None:
    """Insert an accent after the glyph it overlaps most, among the glyphs nearest to it
    vertically (within its font size); drop it when it sits over nothing.

    MuPDF gives TeX's combining arrow no width, at the point where it is drawn from: it
    belongs to the glyph that starts there, else to the one that ends there."""
    best: tuple[tuple[float, float], int, int] | None = None
    reach = 0.6 * max(mark.size, 1.0)
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line):
            gap = max(0.0, ch.y0 - mark.y1, mark.y0 - ch.y1)
            if ch.blank or gap > max(mark.size, 1.0):
                continue
            if mark.x1 - mark.x0 >= 0.5:
                overlap = min(ch.x1, mark.x1) - max(ch.x0, mark.x0)
                if overlap <= 0:
                    continue
                key = (gap, -overlap)
            elif -0.5 <= ch.x0 - mark.x0 <= reach:
                key = (gap, ch.x0 - mark.x0)
            elif -0.5 <= mark.x0 - ch.x1 <= reach:
                key = (gap, reach + mark.x0 - ch.x1)
            else:
                continue
            if best is None or key < best[0]:
                best = (key, li, ci)
    if best is not None:
        lines[best[1]].insert(best[2] + 1, mark)


def _segments(
    lines: list[list[GlyphLike]], cuts: Sequence[float], soft_cuts: Sequence[float] = ()
) -> tuple[list[_Seg], list[_Word], int]:
    """Cells-on-a-line (split at wide gaps, at vertical rules and at `soft_cuts`, the
    white strips between columns of text, `_word_gutters`), words, and how many times a
    vertical rule runs through a word (a grid drawn behind text, not a table)."""
    segs: list[_Seg] = []
    words: list[_Word] = []
    crossings = 0
    for index, chars in enumerate(lines):
        raised = {id(chars[k]) for k in _raised(chars)}
        seg_groups: list[list[GlyphLike]] = [[]]
        word_groups: list[list[GlyphLike]] = [[]]
        last: GlyphLike | None = None
        for ch in chars:
            if ch.blank:
                word_groups.append([])
                if seg_groups[-1]:
                    seg_groups[-1].append(ch)
                continue
            if last is not None:
                gap = ch.x0 - last.x1
                size = max(ch.size, last.size, 1.0)
                cut = any(last.x1 - 0.5 <= x <= ch.x0 + 0.5 for x in cuts)
                if cut and gap <= WORD_GAP * size:
                    crossings += 1
                soft = any(last.x1 - 0.5 <= x <= ch.x0 + 0.5 for x in soft_cuts)
                if gap > SEGMENT_GAP * size or cut or soft:
                    word_groups.append([])
                    seg_groups.append([])
                elif gap > WORD_GAP * size:
                    word_groups.append([])
            seg_groups[-1].append(ch)
            word_groups[-1].append(ch)
            last = ch
        words += [
            _Word(
                *_ink_box(g),
                "".join(c.c for c in g),
                index,
                _marked(g, [id(c) in raised for c in g]),
            )
            for g in word_groups
            if g
        ]
        for group in seg_groups:
            ink = [c for c in group if not c.blank]
            if ink:
                size = statistics.median(c.size for c in ink)
                bold = all(getattr(c, "bold", False) for c in ink)
                segs.append(_Seg(*_ink_box(ink), _spaced(group), size, index, bold=bold))
    return segs, words, crossings


def _ink_box(ink: Sequence[GlyphLike]) -> BBox:
    return (
        min(c.x0 for c in ink),
        min(c.y0 for c in ink),
        max(c.x1 for c in ink),
        max(c.y1 for c in ink),
    )


def _spaced(chars: Sequence[GlyphLike]) -> str:
    """Glyphs left to right, with one space for a blank glyph or a gap wider than a word
    space."""
    out: list[str] = []
    prev: GlyphLike | None = None
    space = False
    for ch in chars:
        if ch.blank:
            space = prev is not None
            continue
        if prev is not None and (
            space or ch.x0 - prev.x1 > WORD_GAP * max(ch.size, prev.size, 1.0)
        ):
            out.append(" ")
        out.append(ch.c)
        prev, space = ch, False
    return "".join(out)


def _merged_rules(
    pieces: Sequence[tuple[float, float, float]], lo: float, hi: float, fixed: tuple[float, float]
) -> list[tuple[float, float]]:
    """Rule pieces at one position joined: (position, length covered inside [lo, hi]), for
    pieces whose position lies in `fixed` (3 pt of slack)."""
    near = sorted(
        (p for p in pieces if fixed[0] - 3 <= p[2] <= fixed[1] + 3 and p[1] >= lo and p[0] <= hi),
        key=lambda p: (p[2], p[0]),
    )
    groups: list[list[tuple[float, float, float]]] = []
    for piece in near:
        if groups and piece[2] - groups[-1][-1][2] <= RULE_JOIN:
            groups[-1].append(piece)
        else:
            groups.append([piece])
    merged = []
    for group in groups:
        spans = sorted((max(lo, p[0]), min(hi, p[1])) for p in group)
        covered, start, end = 0.0, spans[0][0], spans[0][1]
        for s, e in spans[1:]:
            if s > end + RULE_JOIN:
                covered += end - start
                start, end = s, e
            else:
                end = max(end, e)
        covered += end - start
        merged.append((statistics.median(p[2] for p in group), covered))
    return merged


def _chains(
    pieces: Sequence[tuple[float, float, float]], across: tuple[float, float]
) -> list[tuple[float, float, float]]:
    """Continuous rules (position, start, end): pieces at one position whose ends touch.
    Word and LaTeX draw a column rule one row at a time; chained, it is one rule. Only
    pieces whose position lies in `across`."""
    near = sorted((p for p in pieces if across[0] <= p[2] <= across[1]), key=lambda p: (p[2], p[0]))
    chains: list[list[float]] = []  # [position, start, end]
    for start, end, position in near:
        joined = False
        for chain in reversed(chains):
            if position - chain[0] > RULE_JOIN:
                break
            if start <= chain[2] + RULE_JOIN and end >= chain[1] - RULE_JOIN:
                chain[1], chain[2] = min(chain[1], start), max(chain[2], end)
                joined = True
                break
        if not joined:
            chains.append([position, start, end])
    return [(c[0], c[1], c[2]) for c in chains]


def _rules(view: _PageView, box: BBox, size: float) -> tuple[list[float], list[float]]:
    """y positions of row rules and x positions of column rules in `box`: rules that run
    across half its width, or down a third of its height and at least two lines."""
    x0, y0, x1, y1 = box
    width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    rows = [
        pos
        for pos, covered in _merged_rules(view.hsegs, x0, x1, (y0, y1))
        if covered >= ROW_RULE_SPAN * width
    ]
    # A column rule crosses a third of the box and is at least about two lines long in all
    # (it may go on below the box: the detector cuts blank tables to their header row).
    cols = sorted(
        {
            round(pos, 1)
            for pos, start, end in _chains(view.vsegs, (x0 - 3, x1 + 3))
            if min(end, y1) - max(start, y0) >= COLUMN_RULE_SPAN * height
            and end - start >= COLUMN_RULE_LINES * size
        }
    )
    return rows, [x for i, x in enumerate(cols) if i == 0 or x - cols[i - 1] > RULE_JOIN]


def _gutters(segs: list[_Seg], extent: float) -> list[tuple[float, float]]:
    """x-intervals no cell crosses: column gaps. A line holding one cell that spans most
    of the text (a title row, a note) is left out; it does not cut the columns."""
    if not segs:
        return []
    per_line: dict[int, list[_Seg]] = {}
    for seg in segs:
        per_line.setdefault(seg.line, []).append(seg)
    used = [
        s
        for members in per_line.values()
        for s in members
        if len(members) > 1 or s.x1 - s.x0 < 0.5 * extent
    ]
    events = sorted([(s.x0, 1) for s in used] + [(s.x1, -1) for s in used])
    gaps = []
    depth = 0
    for (x, step), (following, _) in pairwise(events):
        depth += step
        if depth == 0 and following - x >= GUTTER_PT:
            gaps.append((x, following))
    return gaps


def _word_gutters(words: Sequence[_Word], lines: int, x0: float, x1: float) -> list[float]:
    """x positions in the middle of the white strips between columns of text whose gap
    is narrower than a cell gap: a table's justified cells (TeX's `p{}` columns) leave a
    column padding of 4 pt, below `SEGMENT_GAP`, so every line reads as one cell. A strip
    counts when it is at least `WORD_GUTTER_PT` wide, no word crosses it on more than
    `WORD_GUTTER_CROSSING` of the lines (a title across the columns), and the text on both
    sides of it is flush against it on `WORD_GUTTER_FLUSH` of the lines (justified text ends
    and begins at the same x; a gap inside a cell, or the free space left by a column of
    short words, has no text at its edges). Prose has none: the spaces of a justified
    paragraph do not line up through six lines."""
    if lines < WORD_GUTTER_LINES:
        return []
    allowed = int(WORD_GUTTER_CROSSING * lines)
    events = sorted([(w.x0, 1) for w in words] + [(w.x1, -1) for w in words])
    found = []
    depth = 0
    for (x, step), (following, _) in pairwise(events):
        depth += step
        if depth > allowed or following - x < WORD_GUTTER_PT or not x0 < x < following < x1:
            continue
        left = len({w.line for w in words if x - 2.0 <= w.x1 <= x + 0.5})
        right = len({w.line for w in words if following - 0.5 <= w.x0 <= following + 2.0})
        if min(left, right) >= WORD_GUTTER_FLUSH * lines:
            found.append((x + following) / 2)
    return found


def _assign_columns(segs: list[_Seg], cuts: list[float]) -> int:
    """Give each cell the columns it covers; return the number of columns."""
    edges = sorted(cuts)
    for seg in segs:
        first = sum(1 for e in edges if e <= seg.x0 + 0.5)
        last = sum(1 for e in edges if e < seg.x1 - 0.5)
        seg.columns = tuple(range(first, max(first, last) + 1))
    return len(edges) + 1


def _alignment(segs: list[_Seg], size: float = 0.0) -> float:
    """Share of cells that line up with the others in their column (left, right or
    centre, whichever fits most), over columns with two or more cells. Cells set in
    lettering smaller than `SCRIPT_CELL` of `size` (the text of a limit or an arrow, set
    over it) belong to the cell beside them, not to a column of their own."""
    by_column: dict[int, list[_Seg]] = {}
    for seg in segs:
        if len(seg.columns) == 1 and seg.size >= SCRIPT_CELL * size:
            by_column.setdefault(seg.columns[0], []).append(seg)
    total = aligned = 0
    for members in by_column.values():
        if len(members) < 2:
            continue
        best = 0
        for key in (lambda s: s.x0, lambda s: s.x1, lambda s: (s.x0 + s.x1) / 2):
            values = sorted(key(s) for s in members)
            j = 0
            for i in range(len(values)):
                while values[i] - values[j] > 2 * ALIGN_TOL:
                    j += 1
                best = max(best, i - j + 1)
        total += len(members)
        aligned += best
    return aligned / total if total else 0.0


@dataclass
class _Grid:
    """Text evidence in a box."""

    segs: list[_Seg]
    words: list[_Word]
    lines: int
    columns: int
    grid_lines: int
    alignment: float
    size: float
    crossings: int  # words a vertical rule runs through
    texts: list[str]  # each visual line's text (for leaders), never logged
    cuts: list[float] = field(default_factory=list)  # x positions between the columns

    @property
    def strong(self) -> bool:
        return (
            self.columns >= 2
            and self.grid_lines >= MIN_GRID_LINES
            and self.alignment >= MIN_ALIGNMENT
        )

    @property
    def weak(self) -> bool:
        return self.columns >= 2 and self.grid_lines >= 1

    @property
    def labels_only(self) -> bool:
        """Nothing longer than two letters or digits: the labels of a diagram (O, C, h0),
        or the pieces of a formula, never a table's header or values."""
        return all(sum(c.isalnum() for c in s.text) <= 2 for s in self.segs)

    @property
    def background_rules(self) -> bool:
        """Rules run through the words: a grid drawn behind text (graph paper)."""
        return self.crossings >= max(2, 0.1 * len(self.words))


def _text_grid(view: _PageView, box: BBox, column_rules: list[float]) -> _Grid:
    lines = _visual_lines(view.glyphs_in(box))
    segs, words, crossings = _segments(lines, column_rules)
    size = statistics.median([s.size for s in segs]) if segs else 10.0
    text_x0 = min((s.x0 for s in segs), default=box[0])
    text_x1 = max((s.x1 for s in segs), default=box[2])
    gaps = _gutters(segs, text_x1 - text_x0)
    # Drawn column rules cut wherever they are inside the box, text or not (a blank form);
    # a white gap cuts where no rule runs through it.
    drawn = [x for x in column_rules if box[0] + 2 < x < box[2] - 2]
    cuts = [
        (g0 + g1) / 2
        for g0, g1 in gaps
        if text_x0 < g0 and g1 < text_x1 and not any(g0 <= x <= g1 for x in drawn)
    ]
    cuts += drawn
    cuts.sort()
    cuts = [x for i, x in enumerate(cuts) if i == 0 or x - cuts[i - 1] > 2.0]
    # Justified cells: some strips between columns are narrower than a cell gap.
    wrapped = any(len(seg.text.split()) >= PROSE_WORDS + 2 for seg in segs)
    soft = [
        x
        for x in (_word_gutters(words, len(lines), text_x0, text_x1) if wrapped or not cuts else [])
        if x not in drawn and all(abs(x - c) > 2.0 for c in cuts)
    ]
    if soft:
        segs, words, crossings = _segments(lines, column_rules, [*cuts, *soft])
        cuts = sorted([*cuts, *soft])
    columns = _assign_columns(segs, cuts)
    by_line: dict[int, set[int]] = {}
    for seg in segs:
        if len(seg.columns) == 1:
            by_line.setdefault(seg.line, set()).add(seg.columns[0])
    grid_lines = sum(1 for cols in by_line.values() if len(cols) >= 2)
    texts = [" ".join(s.text for s in segs if s.line == i) for i in range(len(lines))]
    return _Grid(
        segs,
        words,
        len(lines),
        columns,
        grid_lines,
        _alignment(segs, size),
        size,
        crossings,
        texts,
        cuts,
    )


def _toc(grid: _Grid, min_lines: int = 4) -> bool:
    """A table of contents: dot leaders, or titles each ending in a page number."""
    leaders = sum(1 for t in grid.texts if _LEADERS.search(t))
    if leaders >= 2 and leaders >= 0.3 * grid.lines:
        return True
    if grid.columns < 2 or grid.lines < min_lines:
        return False
    last = grid.columns - 1
    numbers = [s for s in grid.segs if s.columns == (last,) and _PAGE_REF.match(s.text)]
    titles = [s for s in grid.segs if s.columns and s.columns[-1] < last]
    if len(numbers) < 0.7 * grid.lines or not titles:
        return False
    letters = sum(c.isalpha() for s in titles for c in s.text)
    # A title is the longest cell before the number ("E.1" and "Linear cost per reset").
    longest: dict[int, int] = {}
    for s in titles:
        longest[s.line] = max(longest.get(s.line, 0), len(s.text))
    mean_len = statistics.mean(longest.values())
    values = [int(s.text) for s in sorted(numbers, key=lambda s: s.y0) if s.text.isdigit()]
    ascending = len(values) >= 3 and all(a <= b for a, b in pairwise(values))
    return ascending and mean_len >= 15 and letters >= 0.6 * sum(len(s.text) for s in titles)


def _bullet_list(grid: _Grid) -> bool:
    """Cells that start with a bullet: a list set in columns, not a table."""
    if not grid.segs:
        return False
    bulleted = sum(
        1
        for s in grid.segs
        if s.text[:1] in _BULLETS or (s.text[:1] in _DOT_BULLETS and s.text[1:2] == " ")
    )
    return bulleted >= 2 and bulleted >= 0.5 * len(grid.segs)


def _drawing(view: _PageView, box: BBox, size: float) -> bool:
    """Curves or raster images that make the box a picture rather than a grid."""
    x0, y0, x1, y1 = box
    area = max((x1 - x0) * (y1 - y0), 1.0)
    curves = [
        c
        for c in view.curves
        if x0 - 1 <= (c[0] + c[2]) / 2 <= x1 + 1
        and y0 - 1 <= (c[1] + c[3]) / 2 <= y1 + 1
        and max(c[2] - c[0], c[3] - c[1]) > 0.8 * size  # not a drawn bullet or tick
    ]
    if len(curves) >= 3 or any(_area(c) >= CURVE_SHARE * area for c in curves):
        return True
    covered = sum(_area(_intersection(img, box)) for img in view.images)
    return covered >= IMAGE_SHARE * area


def _area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: BBox, b: BBox) -> BBox:
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def judge(proposal: Proposal, view: _PageView) -> Judgement:
    """Keep a proposed box as a data table, or say why not (`DROPPED` codes).

    The box first grows to the drawn table around it (`_snap`, `_extend`): the detector
    often stops at the text, or at the header row of a blank table. Rules are evidence
    only when they do not run through the words."""
    size = _median_size(view, proposal.bbox)
    snapped = _snap(view, _without_captions(view, proposal.bbox), size)
    # A span between two rules is what it is: grown along the column rules that end at its
    # edges it would take in the tables above and below it.
    box = snapped if proposal.source == SOURCE_FRAME else _extend(view, snapped, size)
    if box != snapped:
        box = _snap(view, box, size)
    # Grown by a row or a column, not just to a border a few points away.
    extended = any(abs(a - b) > size for a, b in zip(box, snapped, strict=True))
    row_rules, column_rules = _rules(view, box, size)
    grid = _text_grid(view, box, column_rules)
    verdict = Judgement(
        "no_grid",
        box,
        columns=grid.columns,
        grid_lines=grid.grid_lines,
        alignment=round(grid.alignment, 3),
        row_rules=len(row_rules),
        column_rules=len(column_rules),
    )
    if not grid.segs:
        return verdict
    ruled = not grid.background_rules and (
        len(row_rules) >= 3
        or len(column_rules) >= 3
        or (len(row_rules) >= 2 and len(column_rules) >= 2)
    )
    # Three row and three column rules: cells drawn in full (a blank form needs no text
    # in a second column to be a table).
    ruled_cells = ruled and len(row_rules) >= 3 and len(column_rules) >= 3
    drawing = _drawing(view, box, grid.size)
    own = proposal.source != "detector"
    if own and (_pictured(view, box, grid.size) or grid.size < SMALL_TEXT * view.body_size):
        verdict.verdict = "figure"  # a chart's frame and labels: no ML box vouches for a table
    elif _toc(grid, 3 if own else 4):
        verdict.verdict = "toc"
    elif _bullet_list(grid):
        verdict.verdict = "list"
    elif grid.labels_only:
        verdict.verdict = "labels"
    elif drawing and not (grid.strong and grid.grid_lines >= 0.5 * grid.lines):
        verdict.verdict = "figure"
    elif grid.strong or (ruled and grid.weak and not own) or ruled_cells:
        verdict.verdict = TABLE
        if ruled:
            verdict.reasons.append(REASON_RULED)
        if grid.strong:
            verdict.reasons.append(REASON_TEXT_GRID)
        if extended:
            verdict.reasons.append(REASON_EXTENDED)
        if proposal.source == SOURCE_TEXT:
            verdict.verdict = (
                _text_block_dropped(grid, view.width, _has_caption(view, box)) or TABLE
            )
        elif proposal.source == SOURCE_FRAME:
            if (
                _running_text(grid, box)
                or grid.lines < BLOCK_MIN_LINES
                or grid.grid_lines < FRAME_GRID_SHARE * grid.lines
            ):
                verdict.verdict = "no_grid"  # rules around paragraphs, or around one line
            elif _numbered(grid):
                verdict.verdict = "numbered"
        if verdict.verdict != TABLE:
            verdict.reasons.clear()
    return verdict


# ------------------------------------------------------ proposals made here, not by the detector

_ENUMERATION = re.compile(
    r"^(?:\(?[A-Za-z]\)|\(?\d{1,3}(?:\.\d{1,3})*[.)]?|\(?[ivxlc]{1,5}[.)]|\d{1,3}:"
    r"|\[[^\]]{1,12}\]"  # [3], [JP14], [ABC+20]: a reference
    r"|[-\u2013\u2014*+>\u2022\u00b7\u25e6\u25aa])$"  # a bullet, a dash, a prompt
)
_EQUATION_TAG = re.compile(r"^\([A-Za-z]{0,6}\d{0,3}(?:[.-]\d{1,3})*[a-z]?\)$")


def own_proposals(view: _PageView) -> list[Proposal]:
    """Boxes the detector does not give: the layout model looks for ruled, shaded or
    header-marked grids, so it never proposes a list of symbols and their meanings set in
    two aligned columns (a `tabular` with no rules), proposes only fragments of a table
    ruled at its top and bottom whose cells are paragraphs of justified text, and is not run
    at all on a page with too many drawn items. Each box goes through `judge` as the
    detector's do. `_rule_frames` and `_text_blocks`."""
    return [*_rule_frames(view), *_text_blocks(view)]


def _rule_frames(view: _PageView) -> list[Proposal]:
    """The span of rules of one extent: a booktabs table's top, header and bottom rules, or
    the double rules that frame a table of text, each stack read as one rule. Every two
    neighbouring rules are a box, and so is the whole of a stack of three (a table's top,
    header and bottom rules) when no "Table N" caption sits between them."""
    rules = [r for r in _chains(view.hsegs, (-1e9, 1e9)) if r[2] - r[1] >= FRAME_MIN_WIDTH]
    groups: list[list[tuple[float, float, float]]] = []
    for rule in sorted(rules):
        for group in groups:
            if (
                abs(group[0][1] - rule[1]) <= EXTENT_TOL
                and abs(group[0][2] - rule[2]) <= EXTENT_TOL
            ):
                group.append(rule)
                break
        else:
            groups.append([rule])
    out: list[Proposal] = []
    for group in groups:
        group.sort()
        distinct: list[tuple[float, float, float]] = []
        for rule in group:  # a double rule is one
            if not distinct or rule[0] - distinct[-1][0] > DOUBLE_RULE:
                distinct.append(rule)
        if not 2 <= len(distinct) <= FRAME_MAX_RULES:
            continue
        spans = list(pairwise(distinct))
        if len(distinct) == FRAME_WHOLE_RULES:  # top, header and bottom: one booktabs table
            spans.append((distinct[0], distinct[-1]))
        for top, bottom in spans:
            if bottom[0] - top[0] > FRAME_MAX_HEIGHT * view.height:
                continue
            box = (min(top[1], bottom[1]), top[0], max(top[2], bottom[2]), bottom[0])
            if (
                len(distinct) == FRAME_WHOLE_RULES
                and (top, bottom) == spans[-1]
                and _captions_between(view, box)
            ):
                continue
            out.append(Proposal(box, [], 0, SOURCE_FRAME))
    return out


def _captions_between(view: _PageView, box: BBox) -> bool:
    """Is there a "Table N" line strictly inside `box`, past its first two lines?"""
    lines = _visual_lines(view.glyphs_in(box, pad=0.0))
    return any(TABLE_CAPTION.match("".join(c.c for c in line).strip()) for line in lines[2:-1])


def _text_blocks(view: _PageView) -> list[Proposal]:
    """Runs of lines whose text sets out in cells: at least `BLOCK_MIN_LINES` lines with
    two or more cells (white gaps wider than `SEGMENT_GAP`) with at most `BLOCK_ROW_GAP`
    lines of white between neighbours, and the single cell of a wrapped line that starts
    where a later column starts. The box is the text's; `judge` decides whether the
    columns line up (and `_text_block_dropped` whether it is a table at all)."""
    lines = _visual_lines(view.glyphs_in((-1e9, -1e9, 1e9, 1e9), pad=0.0))
    if len(lines) < BLOCK_MIN_LINES:
        return []
    segs, _, _ = _segments(lines, [])
    by_line: dict[int, list[_Seg]] = {}
    for seg in segs:
        by_line.setdefault(seg.line, []).append(seg)
    ordered = sorted(by_line.values(), key=lambda members: min(m.y0 for m in members))
    out: list[Proposal] = []
    block: list[list[_Seg]] = []

    def close() -> None:
        if sum(len(members) >= 2 for members in block) >= BLOCK_MIN_LINES:
            members = [m for line in block for m in line]
            box = (
                min(m.x0 for m in members),
                min(m.y0 for m in members),
                max(m.x1 for m in members),
                max(m.y1 for m in members),
            )
            out.append(Proposal(box, [], 0, SOURCE_TEXT))
        block.clear()

    held: list[list[_Seg]] = []  # title rows waiting for a row of cells to follow them
    for members in ordered:
        members.sort(key=lambda m: m.x0)
        size = statistics.median(m.size for m in members)
        last = (held or block)[-1] if (held or block) else None
        above = max(m.y1 for m in last) if last else None
        gap = min(m.y0 for m in members) - above if above is not None else 0.0
        # A header row has more white under it than the rows have between them.
        header = len(block) == 1 and not held and len(block[0]) >= 2 and gap <= HEADER_GAP * size
        if above is not None and gap > BLOCK_ROW_GAP * size and not header:
            held.clear()
            close()
        if len(members) >= 2 or (block and _starts_a_column(members[0], block)):
            block.extend(held)
            held.clear()
            block.append(members)
        elif block and _is_title_row(members[0], block):
            held.append(members)  # "Gases at 0 C" over a group of rows: the table goes on
        else:
            held.clear()
            close()
    close()
    return out


def _is_title_row(seg: _Seg, block: list[list[_Seg]]) -> bool:
    """A short cell on a line of its own that starts where the first column does: the title
    of a group of rows (a table of media: "Gases", "Liquids", "Solids"). It joins the block
    when a row of cells follows it."""
    left = min(m.x0 for line in block for m in line)
    return abs(seg.x0 - left) <= ALIGN_TOL and len(seg.text.split()) <= TITLE_ROW_WORDS


def _starts_a_column(seg: _Seg, block: list[list[_Seg]]) -> bool:
    """Does the lone cell of a line start where a column after the first starts (the
    wrapped end of the cell above)?"""
    return any(
        abs(seg.x0 - other.x0) <= ALIGN_TOL
        for line in block
        if len(line) >= 2
        for other in line[1:]
    )


def _has_caption(view: _PageView, box: BBox) -> bool:
    """A "Table N" line just above or below the box (within `CAPTION_MAX_GAP`), over it."""
    for line in view.lines:
        if line.vertical:
            continue
        lx0, ly0, lx1, ly1 = line.bbox
        if min(lx1, box[2]) - max(lx0, box[0]) <= 0:
            continue
        gap = max(ly0 - box[3], box[1] - ly1)
        if 0 <= gap <= CAPTION_MAX_GAP and TABLE_CAPTION.match(_line_text(line).strip()):
            return True
    return False


def _wordy(text: str, letters_needed: int = 2) -> bool:
    """A word or words: letters make up at least half of it and it has at least
    `letters_needed`."""
    letters = sum(c.isalpha() for c in text)
    return letters >= letters_needed and letters >= 0.5 * sum(not c.isspace() for c in text)


_RELATION = frozenset("=\u2264\u2265<>\u2192\u2208\u2209\u2260\u2248\u223c\u2211\u222b\u2282\u2286")


def _pictured(view: _PageView, box: BBox, size: float) -> bool:
    """A chart or drawing in a box nothing but the geometry proposed: any curve (a plotted
    line, an arrow), a few small ones (scatter dots, a legend's markers) or curves running
    past it (a legend inside a plot), a raster image covering `PICTURE_SHARE` of it, or
    `PICTURE_FILLS` filled shapes (the bars of a histogram, a heat map). The detector's boxes
    get the weaker `_drawing` test: its model has seen the page."""
    x0, y0, x1, y1 = box
    area = max((x1 - x0) * (y1 - y0), 1.0)
    near = sum(
        1
        for c in view.curves
        if c[2] >= x0 - PICTURE_REACH
        and c[0] <= x1 + PICTURE_REACH
        and c[3] >= y0 - PICTURE_REACH
        and c[1] <= y1 + PICTURE_REACH
    )
    if near >= PICTURE_NEAR:
        return True  # a legend inside a plot: the plotted lines run past it
    marks = 0
    for c in view.curves:
        if x0 - 1 <= (c[0] + c[2]) / 2 <= x1 + 1 and y0 - 1 <= (c[1] + c[3]) / 2 <= y1 + 1:
            if max(c[2] - c[0], c[3] - c[1]) > 0.8 * size:
                return True
            marks += 1  # the dots of a scatter plot are small
    if marks >= PICTURE_MARKS:
        return True
    if sum(_area(_intersection(img, box)) for img in view.images) >= PICTURE_SHARE * area:
        return True
    inside = [
        f
        for f in view.fills
        if x0 <= (f[0] + f[2]) / 2 <= x1 and y0 <= (f[1] + f[3]) / 2 <= y1 and _area(f) < 0.5 * area
    ]
    if len(inside) < PICTURE_FILLS:
        return False
    # Shading is drawn behind text (in pieces, a word or a space each); a bar of a chart has none.
    centres = [
        ((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
        for chars in view.glyphs_in(box, pad=0.0)
        for c in chars
        if not c.blank
    ]
    empty = [
        f
        for f in inside
        if not any(f[0] - 4 <= cx <= f[2] + 4 and f[1] <= cy <= f[3] for cx, cy in centres)
    ]
    return len(empty) >= PICTURE_FILLS


def _running_text(grid: _Grid, box: BBox) -> bool:
    """Two lines of running text in the box: a single cell of eight words or more across
    most of its width (a table's cells are split by their column gutters first)."""
    width = max(box[2] - box[0], 1.0)
    per_line = Counter(s.line for s in grid.segs)
    return (
        sum(
            1
            for s in grid.segs
            if per_line[s.line] == 1 and len(s.text.split()) >= 8 and s.x1 - s.x0 >= 0.6 * width
        )
        >= 2
    )


def _bold_terms(grid: _Grid) -> bool:
    """Is the first column set in bold, term after term? LaTeX's `description` list sets
    its labels so, and a textbook's glossary; a `tabular` of notation is plain. An unruled
    list of bold terms with their prose descriptions is a list, as a ruled box of them is
    (`is_layout_grid`); the same list in plain type is what a table of notation looks like."""
    first = [m for m in grid.segs if m.columns == (0,)]
    return len(first) >= 2 and sum(m.bold for m in first) >= 0.7 * len(first)


def _numbered(grid: _Grid) -> bool:
    """A first column of numbers or letters in sequence ("1:", "(a)", "[3]") before text of
    several words: the lines of an algorithm, a numbered list, a bibliography. Or a
    caption that says so: "Algorithm 2".
    """
    first = min((m.line for m in grid.segs), default=0)
    if any(re.match(r"^(?:Algorithm|Listing)\s+\d", m.text) for m in grid.segs if m.line == first):
        return True
    labels = [m.text for m in grid.segs if m.columns == (0,)]
    others = [m for m in grid.segs if m.columns and m.columns[0] != 0]
    return (
        len(labels) >= 2
        and sum(bool(_ENUMERATION.match(t)) for t in labels) >= 0.7 * len(labels)
        and bool(others)
        and sum(_wordy(m.text) for m in others) >= 0.5 * len(others)
    ) or (
        len([m for m in grid.segs if m.line == first and re.match(r"^\([a-z]\)", m.text)]) >= 2
    )  # (a) ... (b) ... side by side: subfigure captions


def _text_block_dropped(grid: _Grid, page_width: float, captioned: bool = False) -> str | None:
    """Why lines of text in aligned columns are not a table, as a `DROPPED` code, or None.
    Aligned columns are everywhere in a document: the two text columns of a page, numbered
    equations, a bibliography, the line numbers of an algorithm, a matrix. What separates
    a table is a column of words (labels, terms) next to the other cells."""
    columns: dict[int, list[_Seg]] = {}
    for seg in grid.segs:
        if len(seg.columns) == 1:
            columns.setdefault(seg.columns[0], []).append(seg)
    prose = [
        c
        for c, members in columns.items()
        if len(members) >= 2
        and (
            sum(len(m.text.split()) >= PROSE_WORDS for m in members) >= 2
            or statistics.mean(m.x1 - m.x0 for m in members) >= PROSE_WIDTH * page_width
        )
    ]
    if len(prose) >= 2:
        return "page_text"
    if captioned:
        return None  # "Table 3: ..." under it: the document says it is one
    if any("@" in m.text for m in grid.segs):
        return "no_words"  # a block of authors and their addresses
    maths = len({m.line for m in grid.segs if any(ch in _RELATION for ch in m.text)})
    if maths >= MATH_SHARE * len({m.line for m in grid.segs}):
        return "no_words"
    last = max(columns, default=0)
    tags = [m.text for m in columns.get(last, [])]
    if any(_EQUATION_TAG.match(t) for t in tags) and not any(len(t.split()) > 2 for t in tags):
        return "numbered"
    if _numbered(grid):
        return "numbered"
    if any(
        len(members) >= 2
        and len({m.text for m in members}) >= 2  # a column of "kg" is a unit, not words
        and sum(_wordy(m.text, 3) for m in members) >= 0.6 * len(members)
        for members in columns.values()
    ):
        return None
    # No column of words: a header row of words over rows of values is still a table.
    first = min((m.line for m in grid.segs), default=0)
    head = [m for m in grid.segs if m.line == first]
    rest = [m for m in grid.segs if m.line != first]
    if (
        len(head) >= 2
        and all(_wordy(m.text) for m in head)
        and rest
        and sum(bool(_QUANTITY.match(m.text)) for m in rest) >= 0.7 * len(rest)
    ):
        return None
    return "no_words"


def _without_captions(view: _PageView, box: BBox) -> BBox:
    """The box without a "Table N" caption at its top or bottom (with the caption's
    other lines): the detector often boxes OpenStax's caption below its table. Lines with
    a wide gap in them are table rows and end the caption."""
    lines = _visual_lines(view.glyphs_in(box))
    if len(lines) < 3:
        return box
    segs, _, _ = _segments(lines, [])
    count = Counter(s.line for s in segs)
    texts = ["".join(c.c for c in line).strip() for line in lines]
    tops = [min(c.y0 for c in line if not c.blank) for line in lines]
    bottoms = [max(c.y1 for c in line if not c.blank) for line in lines]
    x0, y0, x1, y1 = box
    first = next((i for i, n in sorted(count.items()) if n >= 2), None)
    last = max((i for i, n in count.items() if n >= 2), default=None)
    if first is None or last is None:
        return box
    if first > 0 and TABLE_CAPTION.match(texts[0]):
        y0 = (bottoms[first - 1] + tops[first]) / 2
    for i in range(last + 1, len(lines)):
        if TABLE_CAPTION.match(texts[i]):
            y1 = (bottoms[i - 1] + tops[i]) / 2
            break
    return (x0, y0, x1, y1)


def _median_size(view: _PageView, box: BBox) -> float:
    sizes = [c.size for chars in view.glyphs_in(box) for c in chars if not c.blank]
    return statistics.median(sizes) if sizes else 10.0


def _snap(view: _PageView, box: BBox, size: float) -> BBox:
    """Grow a table box to the rules drawn just around it (the nearest within SNAP font
    sizes on each side, and the second line of a double rule), so its border and the
    rules above and below its text are part of it, and of its image."""
    x0, y0, x1, y1 = box
    width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    reach = SNAP * size
    rows = sorted(
        pos
        for pos, covered in _merged_rules(view.hsegs, x0, x1, (y0 - reach, y1 + reach))
        if covered >= ROW_RULE_SPAN * width
    )
    cols = sorted(
        pos
        for pos, covered in _merged_rules(view.vsegs, y0, y1, (x0 - reach, x1 + reach))
        if covered >= COLUMN_RULE_SPAN * height
    )
    return (
        _nearest(cols, x0, -reach),
        _nearest(rows, y0, -reach),
        _nearest(cols, x1, reach),
        _nearest(rows, y1, reach),
    )


def _nearest(positions: list[float], edge: float, reach: float) -> float:
    """The edge moved out to the nearest rule within `reach` (negative: before it), and
    on to a rule that doubles it. An edge already on a rule only takes its double: the
    next rule out is another table's (two ruled tables a line apart)."""
    step = 1.0 if reach > 0 else -1.0
    limit = DOUBLE_RULE if any(abs(p - edge) <= 2.0 for p in positions) else abs(reach)
    outside = sorted(
        (p for p in positions if 0 < (p - edge) * step <= limit),
        key=lambda p: (p - edge) * step,
    )
    if not outside:
        return edge
    found = outside[0]
    for later in outside[1:]:
        if (later - found) * step <= DOUBLE_RULE:
            found = later
    return found


def _extend(view: _PageView, box: BBox, size: float) -> BBox:
    """Grow a box to the whole drawn table: a blank worksheet table ("record your data
    here") was often cut to its header row. Along the column rules that run on below (or
    above) it, along the row rules that run on past its sides (two rules must agree),
    and down a stack of row rules as wide as the table's (booktabs, a closing "Avg" row).
    The new part must hold no prose and no "Table N" caption."""
    x0, y0, x1, y1 = box
    columns = [
        c for c in _chains(view.vsegs, (x0 - 3, x1 + 3)) if c[1] <= y1 + 1 and c[2] >= y0 - 1
    ]
    new_y1 = _shared_end([c[2] for c in columns if c[2] > y1 + 3], max)
    new_y0 = _shared_end([c[1] for c in columns if c[1] < y0 - 3], min)
    grown = (x0, y0 if new_y0 is None else new_y0, x1, y1 if new_y1 is None else new_y1)
    rows = [
        r for r in _chains(view.hsegs, (grown[1] - 1, grown[3] + 1)) if r[1] <= x1 and r[2] >= x0
    ]
    new_x1 = _shared_end([r[2] for r in rows if r[2] > x1 + 3], max)
    new_x0 = _shared_end([r[1] for r in rows if r[1] < x0 - 3], min)
    grown = (
        grown[0] if new_x0 is None else new_x0,
        grown[1],
        grown[2] if new_x1 is None else new_x1,
        grown[3],
    )
    if grown != box and _foreign_text(view, grown, box):
        grown = box
    return _stack(view, grown, size)


def _stack(view: _PageView, box: BBox, size: float) -> BBox:
    """Follow row rules as wide as the table's widest one down (and up) the page while
    they come close together: a booktabs table's last rows sit between such rules."""
    x0, y0, x1, y1 = box
    full = [
        r
        for r in _chains(view.hsegs, (y0 - 3, y1 + 3))
        if r[2] - r[1] >= 0.9 * (x1 - x0) and r[1] <= x1 and r[2] >= x0
    ]
    if not full:
        return box
    left, right = min(r[1] for r in full), max(r[2] for r in full)
    reach = STACK_GAP * size
    # A table with column rules goes on only where its column rules do: a second ruled
    # table a few lines further down is another table.
    columns = [
        c[0]
        for c in _chains(view.vsegs, (x0 - 3, x1 + 3))
        if min(c[2], y1) - max(c[1], y0) >= 0.5 * (y1 - y0)
    ]

    def joined(top: float, bottom: float) -> bool:
        ruled = sum(1 for x in columns if _ruled(view.vsegs, x, top, bottom))
        return len(columns) < 2 or ruled >= 2

    for _ in range(64):
        below = [
            r
            for r in _chains(view.hsegs, (y1 + 0.5, y1 + reach))
            if abs(r[1] - left) <= 3 and abs(r[2] - right) <= 3
        ]
        if not below:
            break
        grown = (x0, y0, x1, min(r[0] for r in below) + 0.5)
        if not joined(y1, grown[3] - 0.5) or _foreign_text(view, grown, (x0, y0, x1, y1)):
            break
        y1 = grown[3]
    for _ in range(64):
        above = [
            r
            for r in _chains(view.hsegs, (y0 - reach, y0 - 0.5))
            if abs(r[1] - left) <= 3 and abs(r[2] - right) <= 3
        ]
        if not above:
            break
        grown = (x0, max(r[0] for r in above) - 0.5, x1, y1)
        if not joined(grown[1] + 0.5, y0) or _foreign_text(view, grown, (x0, y0, x1, y1)):
            break
        y0 = grown[1]
    return (x0, y0, x1, y1)


def _shared_end(ends: list[float], pick: Callable[..., float]) -> float | None:
    """The furthest end (by `pick`) that at least two rules share (within 2 pt)."""
    ends = sorted(ends)
    shared = [e for e in ends if sum(1 for o in ends if abs(o - e) <= 2.0) >= 2]
    return pick(shared) if shared else None


def _foreign_text(view: _PageView, grown: BBox, old: BBox) -> bool:
    """Does the part of `grown` outside `old` hold running text (a line of eight words
    across most of the box) or a "Table N" caption?"""
    for chars in view.glyphs_in(grown, pad=0.0):
        ink = [c for c in chars if not c.blank]
        cx = (ink[0].x0 + ink[-1].x1) / 2
        cy = (min(c.y0 for c in ink) + max(c.y1 for c in ink)) / 2
        if old[0] <= cx <= old[2] and old[1] <= cy <= old[3]:
            continue
        text = "".join(c.c for c in chars)
        if TABLE_CAPTION.match(text.strip()):
            return True
        if len(text.split()) >= 8 and ink[-1].x1 - ink[0].x0 >= 0.6 * (grown[2] - grown[0]):
            return True
    return False


# ============================================================================ structure


@dataclass
class _Structurer:
    session: Any
    decoder: Any
    input_name: str
    output_names: list[str]


def _structurer() -> _Structurer | None:
    """The SLANet-plus session, loaded once; None (with one warning) without the file."""
    with _LOAD_LOCK:
        state = _STATE["structurer"]
        if state is not None:
            return state or None
        path = table_model.model_path()
        try:
            if not path.is_file():
                _warn_once(
                    "model_missing",
                    "table structure model not found; tables use PyMuPDF's grid "
                    "(fetch it with: make table-model)",
                )
                _STATE["structurer"] = False
                return None
            if table_model.sha256_of(path) != table_model.MODEL_SHA256:
                log.warning("table structure model has the wrong checksum; not loaded")
                _STATE["structurer"] = False
                return None
            import onnxruntime as ort

            from readablestem.pipeline.vendor.rapid_table import TableLabelDecode

            options = _pinned_options(ort.SessionOptions())
            options.enable_cpu_mem_arena = False
            options.log_severity_level = 4
            session = ort.InferenceSession(
                str(path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            characters = session.get_modelmeta().custom_metadata_map["character"].splitlines()
            structurer = _Structurer(
                session,
                TableLabelDecode(characters),
                session.get_inputs()[0].name,
                [o.name for o in session.get_outputs()],
            )
        except Exception as exc:
            log.warning("table structure model failed to load: %s", type(exc).__name__)
            _STATE["structurer"] = False
            return None
        _STATE["structurer"] = structurer
        return structurer


def preload() -> None:
    """Load the detector and the structure model now, not in the first page that needs
    them: importing `pymupdf.layout` builds its ONNX sessions and the structure model is
    checksummed (7.5 MB) and loaded, about 4-5 s together on the first conversion after
    a start. Holds layout's PyMuPDF lock, because the import patches PyMuPDF. Never
    raises (a model that cannot be loaded is reported once, as when a page needs it)."""
    try:
        with _pdf_lock():
            load_detector()
            _structurer()
    except Exception as exc:  # the first page that needs a model finds out for itself
        log.warning("table models not preloaded: %s", type(exc).__name__)


def reset_models() -> None:
    """Forget the loaded structure model (tests; after fetching the file)."""
    with _LOAD_LOCK:
        _STATE["structurer"] = None
        _STATE["warned"].discard("model_missing")


@dataclass
class Structure:
    """SLANet-plus's grid for a crop: one slot per cell (boxes in crop pixels)."""

    slots: list[Slot]
    header_rows: int
    score: float


def structure(image: np.ndarray) -> Structure | None:
    """Rows, columns and spans of the table in `image` (H x W x 3 uint8, RGB), or None
    when the model is unavailable or its answer is unusable."""
    from readablestem.pipeline.vendor import rapid_table

    model = _structurer()
    if model is None or image.ndim != 3 or min(image.shape[:2]) < 8:
        return None
    bgr = image[..., ::-1].copy()  # SLANet-plus was trained on BGR images
    pixels, shape_list = rapid_table.preprocess(bgr)
    outputs = model.session.run(model.output_names, {model.input_name: pixels})
    bbox_preds, structure_probs = outputs[0], outputs[1]
    tokens, boxes, score = model.decoder(
        bbox_preds, structure_probs, shape_list, (image.shape[0], image.shape[1])
    )
    points = rapid_table.logic_points(tokens)
    if not points or len(points) != len(boxes):
        return None
    slots = []
    for (r0, r1, c0, c1), box in zip(points, boxes, strict=True):
        bbox = None
        if box is not None:
            xs, ys = box[0::2], box[1::2]
            bbox = (min(xs), min(ys), max(xs), max(ys))
        slots.append(Slot(r0, c0, r1 - r0 + 1, c1 - c0 + 1, bbox))
    return Structure(slots, rapid_table.header_row_count(tokens), score)


def _crop(page: pymupdf.Page, box: BBox) -> tuple[np.ndarray, BBox, float] | None:
    """The table region at CROP_DPI as an RGB array, the clip (display space) and zoom."""
    import numpy as np

    with _pdf_lock():
        clip = (
            pymupdf.Rect(box[0] - CROP_PAD, box[1] - CROP_PAD, box[2] + CROP_PAD, box[3] + CROP_PAD)
            & page.rect
        )
        if clip.is_empty or clip.width < 2 or clip.height < 2:
            return None
        zoom = CROP_DPI / 72.0
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        if pixmap.n < 3:
            return None
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )[..., :3]
    return image, (clip.x0, clip.y0, clip.x1, clip.y1), zoom


# ============================================================================ fill


def fill(
    slots: list[Slot],
    words: Sequence[_Word],
    *,
    header_rows: int = 0,
    clean: Callable[[str], str] = lambda s: " ".join(s.split()),
) -> TableGrid | None:
    """The grid with the PDF's own words in its cells, or None when `slots` do not make a
    usable grid. Each word goes to the cell its box overlaps most (the nearest cell when
    it overlaps none); a cell's words are read line by line, left to right."""
    placed = _place(slots, words)
    if placed is None:
        return None
    kept, rows, cols, owners = placed
    texts: dict[int, list[_Word]] = {}
    for word, owner in zip(words, owners, strict=True):
        texts.setdefault(owner, []).append(word)
    cells = []
    for i, slot in enumerate(kept):
        members = sorted(texts.get(i, []), key=lambda w: (w.line, w.x0))
        text = clean(_cell_text(members))
        cells.append(
            TableCell(
                row=slot.row,
                col=slot.col,
                row_span=slot.row_span,
                col_span=slot.col_span,
                text=text,
                bbox=_rounded(slot.bbox) if slot.bbox is not None else None,
            )
        )
    grid = TableGrid(rows=rows, cols=cols, cells=cells)
    return _with_headers(grid, header_rows)


def _place(
    slots: list[Slot], words: Sequence[_Word]
) -> tuple[list[Slot], int, int, list[int]] | None:
    """The slots made into a full grid (holes become empty cells), its size, and the index
    of the cell each word goes to; None when the slots overlap, exceed the limits, or have
    no boxes to place words by."""
    if not slots:
        return None
    rows = max(s.row + s.row_span for s in slots)
    cols = max(s.col + s.col_span for s in slots)
    if rows > MAX_TABLE_ROWS or cols > MAX_TABLE_COLS or len(slots) > MAX_TABLE_CELLS:
        return None
    covered: set[tuple[int, int]] = set()
    kept: list[Slot] = []
    for slot in sorted(slots, key=lambda s: (s.row, s.col)):
        mine = {
            (r, c)
            for r in range(slot.row, slot.row + slot.row_span)
            for c in range(slot.col, slot.col + slot.col_span)
        }
        if mine & covered:
            return None  # overlapping cells: not a grid we can trust
        covered |= mine
        kept.append(slot)
    for r in range(rows):  # holes (a short row) become empty cells
        for c in range(cols):
            if (r, c) not in covered:
                kept.append(Slot(r, c))
    if len(kept) > MAX_TABLE_CELLS:
        return None
    boxed = [(i, s.bbox) for i, s in enumerate(kept) if s.bbox is not None]
    if words and not boxed:
        return None
    lattice = _lattice(kept, rows, cols)
    owner = {
        (r, c): i
        for i, s in enumerate(kept)
        for r in range(s.row, s.row + s.row_span)
        for c in range(s.col, s.col + s.col_span)
    }
    owners = []
    for word in words:
        if lattice is not None:
            xs, ys = lattice
            wx, wy = (word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2
            owners.append(owner[(bisect.bisect(ys, wy), bisect.bisect(xs, wx))])
        else:
            owners.append(max(boxed, key=lambda item: _placement(word, item[1]))[0])
    return kept, rows, cols, owners


def misfit(slots: list[Slot], text: _Grid) -> int | None:
    """How badly a grid fits the text on the page: the cells-on-a-line (`_Seg`, text
    with no wide gap) whose words it splits between cells, plus the extra ones it puts
    side by side in one cell. 0 is a perfect fit; None when the slots make no grid.

    SLANet-plus sometimes merges two columns of a booktabs table (no rules to see), and
    PyMuPDF's grid sometimes splits a column; the glyph gaps say which one is right."""
    placed = _place(slots, text.words)
    if placed is None:
        return None
    _, _, _, owners = placed
    by_line: dict[int, list[int]] = {}
    for i, seg in enumerate(text.segs):
        by_line.setdefault(seg.line, []).append(i)
    seg_cells: dict[int, set[int]] = {}
    for word, owner in zip(text.words, owners, strict=True):
        seg = _seg_of(word, text.segs, by_line.get(word.line, []))
        if seg is not None:
            seg_cells.setdefault(seg, set()).add(owner)
    split = sum(len(cells) - 1 for cells in seg_cells.values())
    side_by_side: dict[tuple[int, int], int] = {}
    for seg, cells in seg_cells.items():
        if len(cells) == 1:
            key = (next(iter(cells)), text.segs[seg].line)
            side_by_side[key] = side_by_side.get(key, 0) + 1
    return split + sum(n - 1 for n in side_by_side.values())


def _seg_of(word: _Word, segs: Sequence[_Seg], candidates: Sequence[int]) -> int | None:
    """The cell-on-a-line (index into `segs`, among `candidates`) a word lies in."""
    centre = (word.x0 + word.x1) / 2
    for i in candidates:
        if segs[i].x0 - 0.5 <= centre <= segs[i].x1 + 0.5:
            return i
    return None


def _lattice(slots: list[Slot], rows: int, cols: int) -> tuple[list[float], list[float]] | None:
    """The boundaries between columns (x) and between rows (y), from the medians of the
    one-column (one-row) cells' edges in each column (row), or None when a column or row
    has no such cell. One cell's box can be badly off (SLANet-plus often draws the header
    row's boxes across each other); the medians of a whole column are not."""

    def bounds(
        count: int, key: Callable[[Slot], tuple[int, int, float, float]]
    ) -> list[float] | None:
        starts: dict[int, list[float]] = {}
        ends: dict[int, list[float]] = {}
        for slot in slots:
            index, span, lo, hi = key(slot)
            if span == 1:
                starts.setdefault(index, []).append(lo)
                ends.setdefault(index, []).append(hi)
        if len(starts) < count:
            return None
        edges = []
        for i in range(count - 1):
            end, start = statistics.median(ends[i]), statistics.median(starts[i + 1])
            edges.append((end + start) / 2)
        return edges if edges == sorted(edges) else None

    boxed = [s for s in slots if s.bbox is not None]
    xs = bounds(
        cols, lambda s: (s.col, s.col_span, s.bbox[0], s.bbox[2]) if s.bbox else (0, 0, 0, 0)
    )
    ys = bounds(
        rows, lambda s: (s.row, s.row_span, s.bbox[1], s.bbox[3]) if s.bbox else (0, 0, 0, 0)
    )
    if not boxed or xs is None or ys is None:
        return None
    return xs, ys


def _placement(word: _Word, box: BBox) -> tuple[float, float]:
    """(share of the word inside the cell, minus distance between centres): larger fits."""
    inside = _area(_intersection((word.x0, word.y0, word.x1, word.y1), box))
    share = inside / max(_area((word.x0, word.y0, word.x1, word.y1)), 1e-6)
    wx, wy = (word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return round(share, 2), -(abs(wx - cx) + abs(wy - cy))


def _cell_text(words: list[_Word]) -> str:
    out = ""
    last_line = None
    for word in words:
        if out:
            if last_line is not None and word.line != last_line and out.endswith("-"):
                out = out[:-1] if word.text[:1].islower() and out[-2:-1].isalpha() else out
            else:
                out += " "
        out += word.marked
        last_line = word.line
    return out


def _rounded(box: BBox) -> BBox:
    return (round(box[0], 2), round(box[1], 2), round(box[2], 2), round(box[3], 2))


def _with_headers(grid: TableGrid, header_rows: int) -> TableGrid:
    """Mark the top `header_rows` rows as column headers, fewer if a cell would span from
    them into the body; none for a one-row grid. Empty cells there stay data cells."""
    count = max(0, min(header_rows, grid.rows - 1))
    while count and any(c.row < count < c.row + c.row_span for c in grid.cells):
        count -= 1
    if any(c.row < count and _QUANTITY.match(c.text) for c in grid.cells):
        count = 0  # "Mass | 2 kg": a label and its value, not a header row
    cells = [
        c.model_copy(update={"header": "col" if c.row < count and not c.is_empty else None})
        for c in grid.cells
    ]
    out = grid.model_copy(update={"cells": cells, "header_rows": count})
    return out if not grid_problems(out) else grid.model_copy(update={"header_rows": 0})


def _numeric(text: str) -> bool:
    return bool(_NUMBER.match(text))


def is_word_grid(grid: TableGrid) -> bool:
    """A grid of single terms set out in boxes (OpenStax's ruled "Key Terms": displacement
    | distance | kinematics | magnitude), not data: every cell filled (a blank worksheet
    is a table to fill in), no digit in any cell, every cell at most WORD_GRID_WORDS
    words, at most WORD_GRID_ROWS rows. Only without a "Table N" caption (`find`
    checks): a captioned table of words is data. Kept as text, its words stay readable;
    claimed as a table, they became an image and a decision."""
    texts = [c.text for c in grid.cells if c.text.strip()]
    return (
        bool(texts)
        and len(texts) == len(grid.cells)
        and grid.rows <= WORD_GRID_ROWS
        and not any(ch.isdigit() for text in texts for ch in text)
        and all(len(text.split()) <= WORD_GRID_WORDS for text in texts)
    )


def is_term_grid(grid: TableGrid) -> bool:
    """A grid of terms set out in boxes (OpenStax's ruled "Key Terms": accuracy | ampere |
    constant | conversion factor | ...), not a table of data: at least TERM_GRID_COLS
    columns and at most WORD_GRID_ROWS rows; no digit anywhere; every term at most
    TERM_GRID_WORDS words with a word of TERM_MIN_LETTERS letters or more (a column of
    symbols or units is data); no term twice (a data table repeats its categories); and
    the only empty cells are the last ones in reading order, less than a row of them (the
    terms ran out). Only without a "Table N" caption (`find` checks): a captioned table
    of words is data. Its terms are written as a list in reading order (layout), so they
    stay readable with no decision; as a table they became an image and a decision."""
    if grid.cols < TERM_GRID_COLS or grid.rows > WORD_GRID_ROWS:
        return False
    order = sorted(grid.cells, key=lambda c: (c.row, c.col))
    texts = [" ".join(c.text.split()) for c in order]
    filled = [t for t in texts if t]
    empty = len(texts) - len(filled)
    if not filled or empty >= grid.cols or any(not t for t in texts[: len(filled)]):
        return False
    if any(ch.isascii() and ch.isdigit() for t in filled for ch in t):
        return False
    if len({t.casefold() for t in filled}) != len(filled):
        return False
    return all(
        len(t.split()) <= TERM_GRID_WORDS
        and any(sum(ch.isalpha() for ch in w) >= TERM_MIN_LETTERS for w in t.split())
        for t in filled
    )


def term_list(grid: TableGrid) -> list[TableCell]:
    """A term grid's non-empty cells in reading order (row by row, left to right)."""
    return [c for c in sorted(grid.cells, key=lambda c: (c.row, c.col)) if c.text.strip()]


def is_layout_grid(grid: TableGrid) -> bool:
    """Text set out in boxes, not data: one column, or two columns whose second is prose
    (its cells average more than LAYOUT_CELL_CHARS characters) and neither numeric."""
    if grid.cols == 1:
        return True
    if grid.cols != 2:
        return False
    columns: dict[int, list[str]] = {0: [], 1: []}
    for cell in grid.cells:
        if cell.col_span == 1 and cell.text.strip():
            columns[cell.col].append(cell.text)
    if not columns[1]:
        return False
    if any(sum(map(_numeric, texts)) >= 0.5 * len(texts) for texts in columns.values() if texts):
        return False
    return statistics.mean(len(t) for t in columns[1]) > LAYOUT_CELL_CHARS


# ============================================================================ the page


def find(
    page: pymupdf.Page,
    lines: Sequence[LineLike],
    paths: Sequence[PathLike],
    *,
    clean: Callable[[str], str] = lambda s: " ".join(s.split()),
    budget: DetectorBudget | None = None,
) -> list[PageTable]:
    """The data tables of a page that passed layout's probe, in top-to-bottom order.

    `lines` and `paths` are what layout extracted from the page (display coordinates);
    `clean` is layout's text cleaning, applied to every cell and caption. Never raises:
    a page whose tables cannot be worked out is read as if it had none (one content-free
    warning), so a job is never lost to table detection.
    """
    try:
        tables, _ = find_with_judgements(page, lines, paths, clean=clean, budget=budget)
    except Exception as exc:
        log.warning("table detection failed on a page: %s", type(exc).__name__)
        return []
    return tables


def find_with_judgements(
    page: pymupdf.Page,
    lines: Sequence[LineLike],
    paths: Sequence[PathLike],
    *,
    clean: Callable[[str], str] = lambda s: " ".join(s.split()),
    budget: DetectorBudget | None = None,
) -> tuple[list[PageTable], list[tuple[Proposal, Judgement]]]:
    """`find`, plus every proposal with its judgement (for the evaluation)."""
    turn = _page_turn(lines)
    if turn:
        return _find_turned(page, lines, paths, turn, clean=clean, budget=budget)
    return _find_in(page, lines, paths, clean=clean, budget=budget)


def _find_in(
    page: pymupdf.Page,
    lines: Sequence[LineLike],
    paths: Sequence[PathLike],
    *,
    clean: Callable[[str], str],
    budget: DetectorBudget | None,
) -> tuple[list[PageTable], list[tuple[Proposal, Judgement]]]:
    view = _PageView.of(page, lines, paths)
    proposals = [*propose(page, budget), *own_proposals(view)]
    if not proposals:
        return [], []
    judged = [(p, judge(p, view)) for p in proposals]
    judged += _joined_frames(judged, view)
    _split_stacked(judged)
    _mark_containers(judged)
    kept: list[tuple[Proposal, Judgement]] = []

    def standing(pj: tuple[Proposal, Judgement]) -> tuple[int, float]:
        # The detector's boxes first, the larger of two overlapping ones staying; a box found
        # here only where no detector box is (it has no grid of its own, and no model vouched
        # for it).
        return (pj[0].source != "detector", -_area(pj[1].bbox))

    for proposal, judgement in sorted(judged, key=standing):
        if judgement.verdict != TABLE:
            continue
        if any(_overlaps(judgement.bbox, other.bbox) for _, other in kept):
            judgement.verdict = "overlap"  # nested or repeated box: the larger one stays
            continue
        kept.append((proposal, judgement))
    built: list[tuple[PageTable, Judgement]] = []
    for proposal, judgement in sorted(kept, key=lambda pj: (pj[1].bbox[1], pj[1].bbox[0])):
        table = _build(page, view, proposal, judgement, clean)
        if table is not None:
            built.append((table, judgement))
    _attach_captions(view, [table for table, _ in built], clean)
    tables = []
    for table, judgement in built:
        if table.caption is None and is_term_grid(table.grid):
            # Claimed (its lines leave the text flow, where they ran together), but not
            # data: layout writes its terms as a list.
            table.grid = table.grid.model_copy(update={"is_data": False})
            table.reasons.append(REASON_TERMS)
        elif table.caption is None and (table.layout_grid or is_word_grid(table.grid)):
            judgement.verdict = "layout_grid"  # its words stay in the text, as before
            continue
        tables.append(table)
    for table in tables:
        table.lines |= _claimed_lines(view, table.bbox)
        table.paths = _claimed_paths(paths, table.bbox)
        table.at_top = _at_top(view, table)
    inside = set().union(*(table.lines for table in tables)) if tables else set()
    for table in tables:
        if _edge_text(view, table, inside):
            table.reasons.append(REASON_EDGE_TEXT)
    return tables, judged


def _mark_containers(judged: list[tuple[Proposal, Judgement]]) -> None:
    """A box found here that holds another table, narrower than itself and under 60% of its
    area, is the outline of a callout or a worked example with the table inside it (OpenStax
    prints its examples so): the inner box is the table."""
    boxes = [j for _, j in judged if j.verdict == TABLE]
    for proposal, judgement in judged:
        if proposal.source == "detector" or judgement.verdict != TABLE:
            continue
        outer = judgement.bbox
        for inner in boxes:
            box = inner.bbox
            if (
                inner is not judgement
                and _area(box) < 0.6 * _area(outer)
                and _area(_intersection(box, outer)) >= 0.9 * _area(box)
                and (box[0] > outer[0] + EXTENT_TOL or box[2] < outer[2] - EXTENT_TOL)
            ):
                judgement.verdict = "container"
                break


def _joined_frames(
    judged: list[tuple[Proposal, Judgement]], view: _PageView
) -> list[tuple[Proposal, Judgement]]:
    """A table whose rules run between its rows (or between groups of rows) is a stack of
    spans between rules, each its own box. Spans of one extent that follow each other with
    no space between, at least two of them tables and the rest slivers (a header row on its
    own) or short of the evidence of a table of their own (a last row or two), are one table:
    the box of the stack, judged like the others. A span of text between two tables is none
    of these, and breaks the chain."""
    frames = sorted(
        ((p, j) for p, j in judged if p.source == SOURCE_FRAME),
        key=lambda pj: (round(pj[0].bbox[0]), pj[0].bbox[1]),
    )
    out: list[tuple[Proposal, Judgement]] = []
    chain: list[tuple[Proposal, Judgement]] = []

    def close() -> None:
        tables_ = [pj for pj in chain if pj[1].verdict == TABLE]
        if len(tables_) >= 2:
            box = (
                min(pj[0].bbox[0] for pj in chain),
                min(pj[0].bbox[1] for pj in chain),
                max(pj[0].bbox[2] for pj in chain),
                max(pj[0].bbox[3] for pj in chain),
            )
            whole = Proposal(box, [], 0, SOURCE_FRAME)
            out.append((whole, judge(whole, view)))
        chain.clear()

    for proposal, judgement in frames:
        sliver = judgement.verdict == "no_grid" and judgement.grid_lines <= 1
        cells = (
            judgement.verdict == "no_grid" and judgement.columns >= 2 and judgement.grid_lines >= 2
        )
        if judgement.verdict != TABLE and not sliver and not cells:
            close()
            continue
        if chain:
            last = chain[-1][0].bbox
            same = abs(last[0] - proposal.bbox[0]) <= EXTENT_TOL
            same = same and abs(last[2] - proposal.bbox[2]) <= EXTENT_TOL
            if not (same and 0 <= proposal.bbox[1] - last[3] <= EXTENT_TOL):
                close()
        chain.append((proposal, judgement))
    close()
    return out


def _split_stacked(judged: list[tuple[Proposal, Judgement]]) -> None:
    """The detector boxes two unruled lists set one under the other as one table, its
    columns the union of both lists'. When two or more tables found here from the text lie
    inside such a box and hold most of it, they are the tables and the box is not."""
    own = [j for p, j in judged if p.source == SOURCE_TEXT and j.verdict == TABLE]
    for proposal, judgement in judged:
        if proposal.source != "detector" or judgement.verdict != TABLE:
            continue
        if REASON_RULED in judgement.reasons:
            continue
        box = judgement.bbox
        parts = [
            j
            for j in own
            if _area(_intersection(j.bbox, box)) >= 0.9 * _area(j.bbox)
            and _area(j.bbox) < _area(box)
        ]
        if len(parts) >= 2 and sum(_area(j.bbox) for j in parts) >= 0.6 * _area(box):
            judgement.verdict = "split"


# ------------------------------------------------------------- a page whose text is set sideways


@dataclass(frozen=True, slots=True)
class _TurnedChar:
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    blank: bool
    bold: bool = False


@dataclass(frozen=True, slots=True)
class _TurnedLine:
    chars: Sequence[_TurnedChar]
    bbox: BBox
    vertical: bool


@dataclass(frozen=True, slots=True)
class _TurnedPath:
    bbox: BBox
    kind: str
    curved: bool
    filled: bool


def _page_turn(lines: Sequence[LineLike]) -> int:
    """90 or 270 when the text of the page reads upwards or downwards (a landscape table
    on a portrait page that has no /Rotate: `sidewaystable`, a page printed landscape and
    saved as it is), else 0. How far to turn the page clockwise to read it. Needs a page
    whose text is mostly set that way, so the sideways label of a figure does not turn it."""
    up = down = total = 0
    for line in lines:
        ink = [c for c in line.chars if not c.blank]
        total += len(ink)
        if len(ink) < 2 or not line.vertical:
            continue
        first, last = ink[0], ink[-1]
        dx = (last.x0 + last.x1 - first.x0 - first.x1) / 2
        dy = (last.y0 + last.y1 - first.y0 - first.y1) / 2
        if abs(dy) <= abs(dx):
            continue  # upside down, or not a line of text
        if dy < 0:
            up += len(ink)
        else:
            down += len(ink)
    if total < TURN_MIN_CHARS:
        return 0
    if up >= TURN_SHARE * total:
        return 90
    if down >= TURN_SHARE * total:
        return 270
    return 0


def _turner(turn: int, width: float, height: float) -> tuple[Callable, Callable]:
    """(forward, back) maps of a point of the page as displayed to the page turned
    clockwise by `turn` degrees (`width`, `height`: the page as displayed)."""
    if turn == 90:
        return (lambda x, y: (height - y, x)), (lambda x, y: (y, height - x))
    return (lambda x, y: (y, width - x)), (lambda x, y: (width - y, x))


def _map_box(box: BBox, move: Callable) -> BBox:
    (a, b), (c, d) = move(box[0], box[1]), move(box[2], box[3])
    return min(a, c), min(b, d), max(a, c), max(b, d)


def _find_turned(
    page: pymupdf.Page,
    lines: Sequence[LineLike],
    paths: Sequence[PathLike],
    turn: int,
    *,
    clean: Callable[[str], str],
    budget: DetectorBudget | None,
) -> tuple[list[PageTable], list[tuple[Proposal, Judgement]]]:
    """`_find_in` on the page turned upright: the lines and paths are turned to match (the
    indices stay, so the lines a table claims are the same ones), the detector and the
    structure model are run on a copy of the page given the extra /Rotate, and what comes
    back is turned again into the page's own frame. The grid says how far the picture of
    the table has to be turned to read it (`TableGrid.turned`)."""
    rect = page.rect
    forward, back = _turner(turn, rect.width, rect.height)
    turned_lines = [
        _TurnedLine(
            [
                _TurnedChar(
                    c.c,
                    *_map_box((c.x0, c.y0, c.x1, c.y1), forward),
                    c.size,
                    c.blank,
                    getattr(c, "bold", False),
                )
                for c in line.chars
            ],
            _map_box(line.bbox, forward),
            not _reads_along(line, turn),
        )
        for line in lines
    ]
    swap = {"hrule": "vrule", "vrule": "hrule"}
    turned_paths = [
        _TurnedPath(_map_box(p.bbox, forward), swap.get(p.kind, p.kind), p.curved, p.filled)
        for p in paths
    ]
    with _pdf_lock():
        copy = pymupdf.open()
        try:
            copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
            sideways = copy[0]
            sideways.set_rotation((page.rotation + turn) % 360)
        except Exception:
            copy.close()
            raise
    try:
        found, judged = _find_in(sideways, turned_lines, turned_paths, clean=clean, budget=budget)
    finally:
        with _pdf_lock():
            copy.close()
    for table in found:
        table.bbox = _rounded(_map_box(table.bbox, back))
        table.grid = table.grid.model_copy(
            update={
                "turned": turn,
                "cells": [
                    c.model_copy(update={"bbox": _rounded(_map_box(c.bbox, back))})
                    if c.bbox is not None
                    else c
                    for c in table.grid.cells
                ],
            }
        )
    for _, judgement in judged:
        judgement.bbox = _map_box(judgement.bbox, back)
    return found, judged


def _reads_along(line: LineLike, turn: int) -> bool:
    """Does this line read in the direction the page's text does (so it is horizontal once
    the page is turned)?"""
    ink = [c for c in line.chars if not c.blank]
    if len(ink) < 2 or not line.vertical:
        return False
    dy = (ink[-1].y0 + ink[-1].y1 - ink[0].y0 - ink[0].y1) / 2
    dx = (ink[-1].x0 + ink[-1].x1 - ink[0].x0 - ink[0].x1) / 2
    return abs(dy) > abs(dx) and (dy < 0) == (turn == 90)


def _edge_text(view: _PageView, table: PageTable, claimed: set[int]) -> bool:
    """Is there a line no table or caption claimed, of the table's own text size, just under
    its box and starting where one of its columns starts? The box of an unruled table can end a
    line early and leave the last line of a wrapped cell outside it; the grid then lacks the
    words and so does the crop the model reads, so nothing downstream sees the loss. Text
    above is not looked at (a title or a heading is not a cell), nor is a line of another size
    (a heading), one that starts off the columns (a note), one that runs on across the columns
    (a paragraph that follows the table closely) or one a line's gap further down."""
    size = _median_size(view, table.bbox)
    glyphs = [ch for chars in view.glyphs_in(table.bbox) for ch in chars if not ch.blank]
    edges: dict[float, float] = {}  # where the text of a column starts -> where its cells end
    for cell in table.grid.cells:
        if cell.bbox is None:
            continue
        cx0, cy0, cx1, cy1 = cell.bbox
        inside = [
            ch.x0
            for ch in glyphs
            if cx0 <= (ch.x0 + ch.x1) / 2 <= cx1 and cy0 <= (ch.y0 + ch.y1) / 2 <= cy1
        ]
        if inside:
            start = min(inside)
            edges[start] = max(edges.get(start, cx1), cx1)
    if not edges:
        return False
    x0, _, x1, bottom = table.bbox
    for i, line in enumerate(view.lines):
        if i in claimed or line.vertical:
            continue
        lx0, ly0, lx1, _ = line.bbox
        ink = [ch for ch in line.chars if not ch.blank]
        if not ink or not 0.0 <= ly0 - bottom <= EDGE_GAP * size:
            continue
        if lx1 < x0 or lx0 > x1:
            continue
        if any(abs(ch.size - size) > EDGE_SIZE_TOLERANCE for ch in ink):
            continue
        first = min(ink, key=lambda ch: ch.x0).x0
        # It stays in its column: a line that runs on across the columns is a paragraph.
        if any(
            abs(first - start) <= EDGE_COLUMN_TOLERANCE and lx1 <= end + EDGE_COLUMN_TOLERANCE
            for start, end in edges.items()
        ):
            return True
    return False


def _overlaps(a: BBox, b: BBox) -> bool:
    inter = _area(_intersection(a, b))
    return inter > 0.3 * min(_area(a), _area(b))


def _build(
    page: pymupdf.Page,
    view: _PageView,
    proposal: Proposal,
    judgement: Judgement,
    clean: Callable[[str], str],
) -> PageTable | None:
    """The table's grid from the best of up to three structures: the drawn cell grid
    (`_ruled_slots`), SLANet-plus, and PyMuPDF's refined grid. The one that fits the
    glyphs best wins (`misfit`); on a tie the drawn grid, then SLANet-plus (measured the
    better recogniser), then PyMuPDF. None, with the judgement's verdict changed, for a
    layout grid (`is_layout_grid`) or when no structure makes a grid."""
    box = judgement.bbox
    size = _median_size(view, box)
    _, column_rules = _rules(view, box, size)
    text = _text_grid(view, box, column_rules)
    extended = REASON_EXTENDED in judgement.reasons
    candidates: list[tuple[str, list[Slot], int]] = []
    ruled = _ruled_slots(view, box, size)
    if ruled is not None:
        candidates.append((REASON_DRAWN_CELLS, ruled, _header_rows_at(ruled, proposal)))
    found = _slanet_slots(page, box if extended else proposal.bbox)
    if found is not None:
        slots, header_rows = found
        candidates.append((REASON_SLANET, slots, header_rows or _header_rows_at(slots, proposal)))
    if not extended:  # PyMuPDF's grid stops where the detector's box did
        candidates.append((REASON_PYMUPDF, proposal.slots, proposal.header_rows))
    cells = _text_slots(view, box, text)
    if cells is not None and not proposal.slots:
        # A box found from the lines of text (no grid of its own): their rows and columns
        # are the evidence it was found by, so on a tie they beat a model's reading, which
        # joins neighbouring rows of a list; the drawn cells, when there are any, still win.
        candidates.insert(1 if ruled is not None else 0, (REASON_TEXT_CELLS, *cells))
    best: tuple[int, int, TableGrid, str] | None = None
    for attempt in range(2):
        for rank, (reason, slots, header_rows) in enumerate(candidates):
            score = misfit(slots, text)
            if score is None or (best is not None and (score, rank) >= best[:2]):
                continue
            grid = fill(slots, text.words, header_rows=header_rows, clean=clean)
            if grid is not None:
                best = (score, rank, grid, reason)
        if best is not None or cells is None or attempt:
            break
        # Nothing else gave a grid (no model, and a box grown past the detector's): the
        # text's own cells keep the table.
        candidates = [(REASON_TEXT_CELLS, *cells)]
    if best is None:
        judgement.verdict = "no_structure"
        return None
    return PageTable(
        bbox=_rounded(box),
        grid=best[2],
        caption=None,
        lines=set(),
        paths=set(),
        reasons=[*judgement.reasons, best[3]],
        column_edges=_column_edges(best[2]),
        layout_grid=is_layout_grid(best[2])
        and (proposal.source != SOURCE_TEXT or _bold_terms(text)),
    )


def _slanet_slots(page: pymupdf.Page, box: BBox) -> tuple[list[Slot], int] | None:
    """SLANet-plus's cells for the region (boxes mapped back to the page) and its header
    row count; None without the model or a usable answer.

    It reads the detector's own box best (measured: grown to the drawn border, a spanning
    header was lost), so `_build` passes that box unless the table was extended."""
    crop = _crop(page, box)
    if crop is None:
        return None
    image, clip, zoom = crop
    found = structure(image)
    if found is None:
        return None
    slots = [
        Slot(
            s.row,
            s.col,
            s.row_span,
            s.col_span,
            None
            if s.bbox is None
            else (
                clip[0] + s.bbox[0] / zoom,
                clip[1] + s.bbox[1] / zoom,
                clip[0] + s.bbox[2] / zoom,
                clip[1] + s.bbox[3] / zoom,
            ),
        )
        for s in found.slots
    ]
    return slots, found.header_rows


def _ruled_slots(view: _PageView, box: BBox, size: float) -> list[Slot] | None:
    """The cells as drawn, when the table is ruled on all four sides with at least two
    rows and two columns: the lattice of its row and column rules, with neighbouring
    cells merged where no rule separates them (a spanning header). None otherwise, or
    when the merges do not make rectangles."""
    rows, cols = _rules(view, box, size)
    ys = _distinct(rows, 0.5 * size)
    xs = _distinct(cols, 0.5 * size)
    reach = max(size, 3.0)
    if len(ys) < 3 or len(xs) < 3:
        return None
    if abs(ys[0] - box[1]) > reach or abs(ys[-1] - box[3]) > reach:
        return None
    if abs(xs[0] - box[0]) > reach or abs(xs[-1] - box[2]) > reach:
        return None
    n_rows, n_cols = len(ys) - 1, len(xs) - 1
    if n_rows > MAX_TABLE_ROWS or n_cols > MAX_TABLE_COLS:
        return None
    parent = list(range(n_rows * n_cols))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for r in range(n_rows):
        for c in range(n_cols):
            here = r * n_cols + c
            if c + 1 < n_cols and not _ruled(view.vsegs, xs[c + 1], ys[r], ys[r + 1]):
                parent[root(here + 1)] = root(here)
            if r + 1 < n_rows and not _ruled(view.hsegs, ys[r + 1], xs[c], xs[c + 1]):
                parent[root(here + n_cols)] = root(here)
    groups: dict[int, list[tuple[int, int]]] = {}
    for r in range(n_rows):
        for c in range(n_cols):
            groups.setdefault(root(r * n_cols + c), []).append((r, c))
    slots = []
    for members in groups.values():
        r0, r1 = min(r for r, _ in members), max(r for r, _ in members)
        c0, c1 = min(c for _, c in members), max(c for _, c in members)
        if len(members) != (r1 - r0 + 1) * (c1 - c0 + 1):
            return None  # an L-shaped cell: a broken rule, not a table's spans
        slots.append(
            Slot(r0, c0, r1 - r0 + 1, c1 - c0 + 1, (xs[c0], ys[r0], xs[c1 + 1], ys[r1 + 1]))
        )
    return slots


def _distinct(positions: Sequence[float], gap: float) -> list[float]:
    """Sorted positions with near-duplicates (a double rule) dropped."""
    out: list[float] = []
    for x in sorted(positions):
        if not out or x - out[-1] > gap:
            out.append(x)
    return out


def _ruled(pieces: Sequence[tuple[float, float, float]], at: float, lo: float, hi: float) -> bool:
    """Do rule pieces at `at` (within RULE_JOIN) cover most of [lo, hi]?"""
    spans = sorted((max(lo, p[0]), min(hi, p[1])) for p in pieces if abs(p[2] - at) <= RULE_JOIN)
    covered, end = 0.0, lo
    for start, stop in spans:
        if stop > end:
            covered += stop - max(start, end)
            end = stop
    return covered >= 0.6 * (hi - lo)


def _text_slots(view: _PageView, box: BBox, text: _Grid) -> tuple[list[Slot], int] | None:
    """The cells as the text sets them out, with no model and no drawn cells: columns
    between the gutters (`_Grid.cuts`), a row for each line with text in the first column
    (a line without is the wrapped end of the cell above, when it follows at the text's own
    leading), and the header rows above the first rule between rows. The structure of last
    resort: it only wins where no other fits the glyphs better (`misfit`), but it keeps a
    table found and extended along its rules from being dropped for want of a model."""
    if text.columns < 2 or not text.segs:
        return None
    by_line: dict[int, list[_Seg]] = {}
    for seg in text.segs:
        by_line.setdefault(seg.line, []).append(seg)
    order = sorted(by_line, key=lambda i: min(s.y0 for s in by_line[i]))
    tops = {i: min(s.y0 for s in by_line[i]) for i in order}
    bottoms = {i: max(s.y1 for s in by_line[i]) for i in order}
    pitches = [tops[b] - tops[a] for a, b in pairwise(order)]
    leading = min((p for p in pitches if p > 0.3 * text.size), default=text.size)
    rows: list[list[int]] = []
    for k, line in enumerate(order):
        starts = any(0 in s.columns for s in by_line[line])
        tight = k > 0 and tops[line] - tops[order[k - 1]] <= 1.15 * leading
        if starts or not rows or not tight:
            rows.append([line])
        else:
            rows[-1].append(line)
    if len(rows) < 2 or len(rows) > MAX_TABLE_ROWS or text.columns > MAX_TABLE_COLS:
        return None
    xs = [box[0], *text.cuts, box[2]]
    if len(xs) != text.columns + 1:
        return None
    edges = [box[1]]
    for above, below in pairwise(rows):
        edges.append((bottoms[above[-1]] + tops[below[0]]) / 2)
    edges.append(box[3])
    slots: list[Slot] = []
    for r, members in enumerate(rows):
        taken: set[int] = set()
        for seg in (s for line in members for s in by_line[line]):
            lo, hi = seg.columns[0], seg.columns[-1]
            if (
                hi > lo
                and not taken & set(range(lo, hi + 1))
                and not any(
                    lo <= c <= hi
                    for other in members
                    for o in by_line[other]
                    if o is not seg
                    for c in o.columns
                )
            ):
                taken |= set(range(lo, hi + 1))
                slots.append(
                    Slot(r, lo, 1, hi - lo + 1, (xs[lo], edges[r], xs[hi + 1], edges[r + 1]))
                )
        for c in range(text.columns):
            if c not in taken:
                slots.append(Slot(r, c, 1, 1, (xs[c], edges[r], xs[c + 1], edges[r + 1])))
    return slots, _text_header_rows(view, box, text, rows, tops, bottoms, by_line)


def _text_header_rows(
    view: _PageView,
    box: BBox,
    text: _Grid,
    rows: list[list[int]],
    tops: dict[int, float],
    bottoms: dict[int, float],
    by_line: dict[int, list[_Seg]],
) -> int:
    """Header rows of a text-set table: the rows above the first rule that runs between two
    rows (booktabs' midrule), at most three; without one, the first row when it holds no
    value and a row below does."""
    rules = [
        pos
        for pos, covered in _merged_rules(view.hsegs, box[0], box[2], (box[1], box[3]))
        if covered >= ROW_RULE_SPAN * (box[2] - box[0])
    ]
    for k in range(1, min(4, len(rows))):
        gap = (bottoms[rows[k - 1][-1]], tops[rows[k][0]])
        if any(gap[0] - 1.0 <= y <= gap[1] + 1.0 for y in rules):
            return k

    def values(row: list[int]) -> bool:
        return any(_QUANTITY.match(s.text) for line in row for s in by_line[line])

    return int(not values(rows[0]) and any(values(row) for row in rows[1:]))


def _header_rows_at(slots: list[Slot], proposal: Proposal) -> int:
    """SLANet-plus rarely marks a header; PyMuPDF's refined grid does. Its header rows
    end at some height on the page: the rows of `slots` above that height are headers."""
    if proposal.header_rows <= 0:
        return 0
    bottoms = [s.bbox[3] for s in proposal.slots if s.row < proposal.header_rows and s.bbox]
    if not bottoms:
        return 0
    bottom = max(bottoms)
    centres: dict[int, list[float]] = {}
    for slot in slots:
        if slot.bbox is not None and slot.row_span == 1:
            centres.setdefault(slot.row, []).append((slot.bbox[1] + slot.bbox[3]) / 2)
    count = 0
    for row in sorted(centres):
        if row != count or statistics.median(centres[row]) >= bottom:
            break
        count += 1
    return count


def _column_edges(grid: TableGrid) -> list[float]:
    """The left edge of each column (median over its one-column cells with a box)."""
    edges = []
    for col in range(grid.cols):
        lefts = [
            c.bbox[0] for c in grid.cells if c.col == col and c.col_span == 1 and c.bbox is not None
        ]
        if not lefts:
            return []
        edges.append(statistics.median(lefts))
    return edges


def _claimed_lines(view: _PageView, box: BBox) -> set[int]:
    """Lines whose centre lies inside the table box."""
    x0, y0, x1, y1 = box[0] - CLAIM_PAD, box[1] - CLAIM_PAD, box[2] + CLAIM_PAD, box[3] + CLAIM_PAD
    claimed = set()
    for i, line in enumerate(view.lines):
        lx0, ly0, lx1, ly1 = line.bbox
        if x0 <= (lx0 + lx1) / 2 <= x1 and y0 <= (ly0 + ly1) / 2 <= y1:
            claimed.add(i)
    return claimed


def _claimed_paths(paths: Sequence[PathLike], box: BBox) -> set[int]:
    """Rules and shading inside the table box (curves stay: a drawing in a cell)."""
    x0, y0, x1, y1 = box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3
    claimed = set()
    for i, path in enumerate(paths):
        px0, py0, px1, py1 = path.bbox
        if path.curved:
            continue
        if x0 <= px0 and px1 <= x1 and y0 <= py0 and py1 <= y1:
            claimed.add(i)
    return claimed


def _attach_captions(view: _PageView, tables: list[PageTable], clean: Callable[[str], str]) -> None:
    """A "Table N ..." caption printed just above or below a table (within
    CAPTION_MAX_GAP, overlapping it horizontally) becomes its caption, with the lines that
    continue it. Nearest first; one caption per table."""
    if not tables:
        return
    inside = set().union(*(_claimed_lines(view, t.bbox) for t in tables))
    candidates = []
    for i, line in enumerate(view.lines):
        if i in inside or line.vertical:
            continue
        text = clean("".join(c.c for c in line.chars))
        if not TABLE_CAPTION.match(text):
            continue
        for t_index, table in enumerate(tables):
            lx0, ly0, lx1, ly1 = line.bbox
            tx0, ty0, tx1, ty1 = table.bbox
            if min(lx1, tx1) - max(lx0, tx0) < 0.3 * min(lx1 - lx0, tx1 - tx0):
                continue
            above, below = ty0 - ly1, ly0 - ty1
            if -2 <= above <= CAPTION_MAX_GAP:
                candidates.append((above, i, t_index, "above"))
            elif -2 <= below <= CAPTION_MAX_GAP:
                candidates.append((below + 2.0, i, t_index, "below"))  # above wins ties
    used: set[int] = set()
    for _, i, t_index, side in sorted(candidates):
        table = tables[t_index]
        if i in used or table.caption is not None:
            continue
        members = _caption_lines(view, i, side, table.bbox, inside | used)
        used.update(members)
        table.caption = clean(" ".join(_line_text(view.lines[m]) for m in members))
        table.lines |= set(members)


def _line_text(line: LineLike) -> str:
    return "".join(c.c for c in line.chars)


def _caption_lines(
    view: _PageView, first: int, side: str, table: BBox, taken: set[int]
) -> list[int]:
    """The caption's first line and the lines that continue it (same size, line pitch,
    overlapping it), away from the table when below it, towards it when above."""
    members = [first]
    current = view.lines[first]
    size = max((c.size for c in current.chars if not c.blank), default=10.0)
    for _ in range(3):
        best = None
        for i, line in enumerate(view.lines):
            if i in taken or i in members or line.vertical:
                continue
            lx0, ly0, lx1, ly1 = line.bbox
            cx0, _, cx1, cy1 = current.bbox
            gap = ly0 - cy1
            if not -0.3 * size <= gap <= 0.8 * size or min(lx1, cx1) - max(lx0, cx0) <= 0:
                continue
            line_size = max((c.size for c in line.chars if not c.blank), default=0.0)
            if abs(line_size - size) > 0.6 or (side == "above" and ly1 > table[1] + 1):
                continue
            if best is None or ly0 < view.lines[best].bbox[1]:
                best = i
        if best is None:
            break
        members.append(best)
        current = view.lines[best]
    return members


def _at_top(view: _PageView, table: PageTable) -> bool:
    """Nothing but running-header text above the table (or its caption) on the page."""
    top = min([table.bbox[1], *(view.lines[i].bbox[1] for i in table.lines if i < len(view.lines))])
    band = TOP_BAND * view.height
    for i, line in enumerate(view.lines):
        if i in table.lines or line.vertical:
            continue
        _, y0, _, y1 = line.bbox
        if y1 <= top and (y0 + y1) / 2 > band:
            return False
    return True


def _row_cells(grid: TableGrid, row: int) -> list[TableCell] | None:
    """The cells of `row`, one per column left to right, when every one is a plain 1 x 1
    cell anchored there (no span reaches into or out of it); else None."""
    cells = sorted((c for c in grid.cells if c.row == row), key=lambda c: c.col)
    if [c.col for c in cells] != list(range(grid.cols)):
        return None
    if any(c.row_span != 1 or c.col_span != 1 for c in cells):
        return None
    if any(c.row < row < c.row + c.row_span for c in grid.cells):
        return None
    return cells


def _row_texts(grid: TableGrid, row: int) -> list[str]:
    return [
        " ".join(c.text.split())
        for c in sorted(grid.cells, key=lambda c: (c.row, c.col))
        if c.row == row
    ]


def _goes_on(start: str, rest: str) -> bool:
    """Is `rest` the end of a cell whose text began `start`? It begins with a lowercase
    letter or an opening bracket ("universe", "(current upper limit)"), or `start` ends
    with a hyphen (a word broken at the line end)."""
    if not start or not rest:
        return False
    return rest[0].islower() or rest[0] in "([" or start.endswith("-")


def _joined_text(start: str, rest: str) -> str:
    if start.endswith("-") and start[-2:-1].isalpha() and rest[:1].islower():
        return start[:-1] + rest
    return f"{start} {rest}"


def join_split_row(previous: PageTable, table: PageTable) -> bool:
    """A table row broken by a page break (Word and browsers break a tall row across
    pages): `table` goes on from `previous` (`continues`), and its first row after any
    header rows repeated from `previous` is the end of `previous`'s last row: its first
    cell is empty where the last row's is not, and every cell it has text in goes on a
    cell of the last row (`_goes_on`). That text is joined to the last row, so the cell
    reads "... the edges of the known universe", not "... the edges of the known" with
    "universe" alone in a row of the next table, and the row is taken out of `table`.

    Both tables are marked (`REASON_ROW_SPLIT`, `REASON_ROW_CONTINUED`) so the table step
    can read the model's answers the same way (each sees one page's crop). Returns True
    when `table` has nothing left but the repeated header rows: layout then drops it,
    its "Table N" caption going to `previous` if that has none. Changes nothing and
    returns False when the rows do not look like that, or when the result would not be
    a valid grid."""
    before, after = previous.grid, table.grid
    if before.cols != after.cols or before.rows < 1:
        return False
    limit = max(before.header_rows, after.header_rows, 1)
    repeated = 0
    while repeated < min(limit, after.rows - 1, before.rows) and _row_texts(
        after, repeated
    ) == _row_texts(before, repeated):
        repeated += 1
    last = _row_cells(before, before.rows - 1)
    rest = _row_cells(after, repeated)
    if last is None or rest is None:
        return False
    if rest[0].text.strip() or not last[0].text.strip():
        return False
    filled = [(a, b) for a, b in zip(last, rest, strict=True) if b.text.strip()]
    if not filled or not all(_goes_on(a.text.strip(), b.text.strip()) for a, b in filled):
        return False
    joined = {id(a): _joined_text(a.text.strip(), b.text.strip()) for a, b in filled}
    grown = before.model_copy(
        update={
            "cells": [
                c.model_copy(update={"text": joined[id(c)]}) if id(c) in joined else c
                for c in before.cells
            ]
        }
    )
    kept = [c for c in after.cells if c.row != repeated]
    shrunk = after.model_copy(
        update={
            "rows": after.rows - 1,
            "cells": [
                c.model_copy(update={"row": c.row - 1}) if c.row > repeated else c for c in kept
            ],
        }
    )
    left = shrunk.rows > repeated  # more than the repeated header rows is left
    if grid_problems(grown) or (left and grid_problems(shrunk)):
        return False
    previous.grid = grown
    previous.reasons.append(REASON_ROW_SPLIT)
    table.grid = shrunk
    table.reasons.append(REASON_ROW_CONTINUED)
    if left:
        return False
    if previous.caption is None:
        previous.caption = table.caption
    return True


def continues(previous: PageTable, table: PageTable, page_width: float) -> bool:
    """Is `table` the rest of `previous` (the last table of the page before)? Same number
    of columns, column edges in the same places, and nothing above it on its page. Both
    must be data tables (a grid of terms goes on from nothing)."""
    if not (previous.grid.is_data and table.grid.is_data):
        return False
    if not table.at_top or previous.grid.cols != table.grid.cols:
        return False
    a, b = previous.column_edges, table.column_edges
    if len(a) != len(b) or not a:
        return False
    tolerance = CONTINUED_TOL * page_width
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))
