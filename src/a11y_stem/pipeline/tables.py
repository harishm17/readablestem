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
import unicodedata
from collections import Counter
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pymupdf

from a11y_stem.core.types import (
    MAX_TABLE_CELLS,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    BBox,
    TableCell,
    TableGrid,
    grid_problems,
)
from a11y_stem.pipeline import table_model

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
SCRIPT_SIZE = 0.85  # x the line's largest glyph: smaller glyphs may be scripts (as layout)
RAISED = 0.2  # x the full-size glyph: a smaller one with its baseline this far up is raised
STACK_GAP = 3.0  # x font size: furthest apart two rules of one booktabs table may be

# Reasons on a TABLE element (`Element.reasons`), content-free like layout's.
REASON_RULED = "ruled grid"
REASON_TEXT_GRID = "aligned text grid"
REASON_SLANET = "SLANet-plus grid"
REASON_PYMUPDF = "PyMuPDF grid"
REASON_DRAWN_CELLS = "drawn cell grid"
REASON_EXTENDED = "box extended along rules"
REASON_CONTINUED = "continued from previous page"
REASON_TERMS = "grid of terms"  # claimed, not data: written as a list (`is_term_grid`)

# Verdicts of `judge` (codes, safe to log and count).
TABLE = "table"
DROPPED = ("toc", "list", "labels", "figure", "no_grid", "overlap", "layout_grid", "no_structure")

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
    from a11y_stem.pipeline import layout

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


def propose(page: pymupdf.Page) -> list[Proposal]:
    """Candidate tables on a page that passed layout's probe (never run it on one that did
    not: the layout model renders the page, where the known MuPDF aborts are)."""
    if not load_detector():
        return []
    with _pdf_lock(), _quiet_stdout() as captured:
        try:
            finder = page.find_tables(union=True, refine=True)
        except Exception as exc:  # find_tables catches most errors itself (returns None)
            log.warning("table detection failed on a page: %s", type(exc).__name__)
            return []
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()
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
        return cls(lines, hsegs, vsegs, curves, pictures, width, height)

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
    lines: list[list[GlyphLike]], cuts: Sequence[float]
) -> tuple[list[_Seg], list[_Word], int]:
    """Cells-on-a-line (split at wide gaps and at vertical rules), words, and how many
    times a vertical rule runs through a word (a grid drawn behind text, not a table)."""
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
                if gap > SEGMENT_GAP * size or cut:
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
                segs.append(_Seg(*_ink_box(ink), _spaced(group), size, index))
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


def _assign_columns(segs: list[_Seg], cuts: list[float]) -> int:
    """Give each cell the columns it covers; return the number of columns."""
    edges = sorted(cuts)
    for seg in segs:
        first = sum(1 for e in edges if e <= seg.x0 + 0.5)
        last = sum(1 for e in edges if e < seg.x1 - 0.5)
        seg.columns = tuple(range(first, max(first, last) + 1))
    return len(edges) + 1


def _alignment(segs: list[_Seg]) -> float:
    """Share of cells that line up with the others in their column (left, right or
    centre, whichever fits most), over columns with two or more cells."""
    by_column: dict[int, list[_Seg]] = {}
    for seg in segs:
        if len(seg.columns) == 1:
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
    columns = _assign_columns(segs, cuts)
    by_line: dict[int, set[int]] = {}
    for seg in segs:
        if len(seg.columns) == 1:
            by_line.setdefault(seg.line, set()).add(seg.columns[0])
    grid_lines = sum(1 for cols in by_line.values() if len(cols) >= 2)
    texts = [" ".join(s.text for s in segs if s.line == i) for i in range(len(lines))]
    return _Grid(
        segs, words, len(lines), columns, grid_lines, _alignment(segs), size, crossings, texts
    )


def _toc(grid: _Grid) -> bool:
    """A table of contents: dot leaders, or titles each ending in a page number."""
    leaders = sum(1 for t in grid.texts if _LEADERS.search(t))
    if leaders >= 2 and leaders >= 0.3 * grid.lines:
        return True
    if grid.columns < 2 or grid.lines < 4:
        return False
    last = grid.columns - 1
    numbers = [s for s in grid.segs if s.columns == (last,) and _PAGE_REF.match(s.text)]
    titles = [s for s in grid.segs if s.columns and s.columns[-1] < last]
    if len(numbers) < 0.7 * grid.lines or not titles:
        return False
    letters = sum(c.isalpha() for s in titles for c in s.text)
    mean_len = statistics.mean(len(s.text) for s in titles)
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
    box = _extend(view, snapped, size)
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
    if _toc(grid):
        verdict.verdict = "toc"
    elif _bullet_list(grid):
        verdict.verdict = "list"
    elif grid.labels_only:
        verdict.verdict = "labels"
    elif drawing and not (grid.strong and grid.grid_lines >= 0.5 * grid.lines):
        verdict.verdict = "figure"
    elif grid.strong or (ruled and grid.weak) or ruled_cells:
        verdict.verdict = TABLE
        if ruled:
            verdict.reasons.append(REASON_RULED)
        if grid.strong:
            verdict.reasons.append(REASON_TEXT_GRID)
        if extended:
            verdict.reasons.append(REASON_EXTENDED)
    return verdict


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

            from a11y_stem.pipeline.vendor.rapid_table import TableLabelDecode

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
    from a11y_stem.pipeline.vendor import rapid_table

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
) -> list[PageTable]:
    """The data tables of a page that passed layout's probe, in top-to-bottom order.

    `lines` and `paths` are what layout extracted from the page (display coordinates);
    `clean` is layout's text cleaning, applied to every cell and caption. Never raises:
    a page whose tables cannot be worked out is read as if it had none (one content-free
    warning), so a job is never lost to table detection.
    """
    try:
        tables, _ = find_with_judgements(page, lines, paths, clean=clean)
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
) -> tuple[list[PageTable], list[tuple[Proposal, Judgement]]]:
    """`find`, plus every proposal with its judgement (for the evaluation)."""
    proposals = propose(page)
    if not proposals:
        return [], []
    view = _PageView.of(page, lines, paths)
    judged = [(p, judge(p, view)) for p in proposals]
    kept: list[tuple[Proposal, Judgement]] = []
    for proposal, judgement in sorted(judged, key=lambda pj: -_area(pj[1].bbox)):
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
        elif table.caption is None and is_word_grid(table.grid):
            judgement.verdict = "layout_grid"  # its words stay in the text, as before
            continue
        tables.append(table)
    for table in tables:
        table.lines |= _claimed_lines(view, table.bbox)
        table.paths = _claimed_paths(paths, table.bbox)
        table.at_top = _at_top(view, table)
    return tables, judged


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
    best: tuple[int, int, TableGrid, str] | None = None
    for rank, (reason, slots, header_rows) in enumerate(candidates):
        score = misfit(slots, text)
        if score is None or (best is not None and (score, rank) >= best[:2]):
            continue
        grid = fill(slots, text.words, header_rows=header_rows, clean=clean)
        if grid is not None:
            best = (score, rank, grid, reason)
    if best is None or is_layout_grid(best[2]):
        judgement.verdict = "no_structure" if best is None else "layout_grid"
        return None
    return PageTable(
        bbox=_rounded(box),
        grid=best[2],
        caption=None,
        lines=set(),
        paths=set(),
        reasons=[*judgement.reasons, best[3]],
        column_edges=_column_edges(best[2]),
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
