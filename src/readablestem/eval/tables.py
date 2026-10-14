"""Tables eval: detection, structure (TEDS), headers, maths cells and text fidelity.

The task (`a11y-stem eval pipeline --task tables`) prints the OpenStax pages of the table
gold set (`data/gold/tables/`, CC BY 4.0) in three table styles (`eval.render`
`table_style`: grid, booktabs, norules), adds the authored documents
(`data/authored/tables/`: LaTeX, Word, slide and HTML tables written for this project),
runs the pipeline on each PDF and scores every table against gold:

- local mode: `layout.analyze` finds the tables and builds their grid from the PDF's
  own words and lines (`Element.table`, source "local"); no model call. The review load
  is what the pipeline asks without rebuilding (`table_step.rebuild` with tables off).
- model mode: the same, then `table_step.rebuild` on each table's crop with tables on
  (`--max-usd` caps the spend); the proposal (`Fix.table`) is what is scored.

What is measured (docs/eval.md "tables"):

- Detection. A predicted table matches a gold table on the same PDF page by content,
  since gold has no boxes: word-token F1 >= 0.5, or F1 of the letters and digits >= 0.7
  (maths-heavy tables, where the text layer splits symbols differently from the gold
  MathML). Pairs are taken greedily, best first, one to one. A prediction matched to a
  layout table (OpenStax "Key Terms" grids) counts neither way.
- TEDS (Zhong et al. 2020): the tree edit distance between the two tables as
  table -> rows -> cells trees, a cell costing its normalised Levenshtein distance and 1
  for another tag or span; 1 - TED / max(nodes). `teds` ignores headers, as table-bench
  did (its 0.820 and 0.868 are reproduced in tests/test_eval_tables_bench.py); `teds_with_
  headers` also costs 1 for a th/td or row/column mismatch. Reported with misses as 0
  and on found tables only, plus TEDS-structure (cell text ignored).
- Headers: precision, recall and F1 on (row, column, scope, text), column and row
  headers apart; per table "exact" when the header texts (a multiset) equal gold.
- Data vs layout, maths cells (letter/digit F1; MathML equal after `eval.equations`
  lenient normalisation), text fidelity (cells whose text is in gold, and cell errors in
  tables nobody is asked about: must be 0; cell text compared after `fidelity_text`, so
  the ways gold and a reader write the same thing are not errors: 10 en dash 18 and
  10⁻¹⁸, ⅓
  and 13, a "____" fill-in blank and an empty cell, and gold's footnote rows, which the
  PDF prints under the table), review load and cost.

Statistics: rates get 95% Wilson intervals (`pipeline_eval.wilson_interval`); metrics over
gold tables get a cluster bootstrap over source tables (one OpenStax table printed three
ways is one cluster), 2,000 resamples, seed 0.

Gold pages: a PDF has no table boxes in gold, so each gold table is assigned the printed
page that holds its words (`assign_pages`): the first page holding nearly all of them,
moved only when that page clearly lacks the table in reading order (the fix for
statistics 4-2, whose small tables first landed on a page that merely mentions their
words). `data/gold/tables/reference_pages.json` records the assignment on the reference
prints; a report says how many differ from it.

Privacy: the gold and authored documents are public or written for this project, so per
item rows may hold their text. The real-document check (`real_documents`) reads private
PDFs from a directory outside git and reports counts only: no file names, no text.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import random
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Literal, Protocol

import defusedxml.ElementTree as SafeET

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, JobBudget
from readablestem.core.types import ConversionOptions, Element, Fix, Kind, TableGrid
from readablestem.eval import pipeline_eval as pe
from readablestem.eval import ted
from readablestem.eval.equations import MathMLError, canonical, normalize
from readablestem.models.base import VisionClient
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import layout, run, table_step
from readablestem.pipeline import options as run_options

TASK = "tables"
TABLE_STYLES = ("grid", "booktabs", "norules")
AUTHORED = "authored"  # the style label of the authored documents
MATCH_F1 = 0.5  # word-token F1 for a detection match
CHAR_F1 = 0.7  # letter/digit F1 for a detection match (maths-heavy tables)
MATH_CELL_OK = 0.9  # a maths cell whose letter/digit F1 reaches this is "ok"
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 0
# The 13 CC BY 4.0 OpenStax pages with tables (table-bench, 2026-10-08): 64 tables, 54 of
# them data tables. `a11y-stem gold fetch --set tables` fetches them into data/gold/tables.
TABLE_GOLD_PAGES: dict[str, tuple[str, ...]] = {
    "physics": (
        "1-3-the-language-of-physics-physical-quantities-and-units",
        "5-4-inclined-planes",
        "6-3-rotational-motion",
        "11-3-phase-change-and-latent-heat",
        "14-1-speed-of-sound-frequency-and-wavelength",
        "16-3-lenses",
        "22-2-nuclear-forces-and-radioactivity",
        "23-1-the-four-fundamental-forces",
        "23-2-quarks",
    ),
    "statistics": (
        "1-3-frequency-frequency-tables-and-levels-of-measurement",
        "2-2-histograms-frequency-polygons-and-time-series-graphs",
        "3-4-contingency-tables",
        "4-2-mean-or-expected-value-and-standard-deviation",
    ),
}
TABLE_GOLD_DATE = "2026-10-08"  # retrieval date of the committed table gold
TABLE_GOLD_DIR = Path("data/gold/tables")  # relative to the project root
AUTHORED_DIR = Path("data/authored/tables")
REFERENCE_PAGES = "reference_pages.json"  # in TABLE_GOLD_DIR

HeaderKind = Literal["col", "row"]
_SKIP_TEXT = {"annotation", "annotation-xml"}
_MATHML_NS = "http://www.w3.org/1998/Math/MathML"


# ============================================================================ text

# Characters the normalisation folds (written as code points: several look alike).
_INCREMENT, _GREEK_DELTA = chr(0x2206), chr(0x0394)
_MINUS, _EN_DASH, _NBSP = chr(0x2212), chr(0x2013), chr(0x00A0)
_ZERO_WIDTH = re.compile(
    "[" + chr(0x200B) + "-" + chr(0x200F) + chr(0x2060) + "-" + chr(0x2064) + chr(0xFEFF) + "]"
)


def norm(text: str | None) -> str:
    """Cell text for comparison: NFKC, one form of delta and minus, no spaces at all.

    The same normalisation table-bench used, so its numbers can be reproduced: U+2206
    INCREMENT as Greek delta, U+2212 MINUS and U+2013 EN DASH as "-", zero-width
    characters and every space removed.
    """
    s = unicodedata.normalize("NFKC", text or "")
    s = s.replace(_INCREMENT, _GREEK_DELTA).replace(_MINUS, "-").replace(_EN_DASH, "-")
    s = _ZERO_WIDTH.sub("", s.replace(_NBSP, " "))
    return re.sub(r"\s+", "", s)


# Raised text the grid read from the PDF marks "^(...)" (a footnote mark "^([1])"): its
# characters are compared, the mark is not, as gold's text drops the raise.
_RAISED = re.compile(r"\^\(([^()]*)\)")
# Exponent markup a text cell may carry (10^{-18}, 10^-18): not compared.
_EXPONENT_MARKS = str.maketrans("", "", "^{}")
# A fraction written across: "1/3", and ⅓ (a fraction slash after NFKC), are gold
# MathML's "13".
_DIGIT_SLASH = re.compile(r"(?<=\d)[/\u2044](?=[-\d])")
# A fill-in blank: underscores (escaped as LaTeX writes them, \_) or dots only.
_BLANK = re.compile(r"[_\\\s.\u2026]*")


def fidelity_text(text: str | None) -> str:
    """Cell text for the fidelity count (`cell_fidelity`): `norm`, then the notations
    that say the same thing made one. Gold's text drops the raise of a superscript, so
    10⁻¹⁸ is "10", an en dash and "18" there, while a cell may write 10^-18 or
    10⁻¹⁸ (both "10-18" here); gold's stacked fraction 1/3 reads "13" ("1/3" and
    "⅓" too); a fill-in blank ("____")
    is an empty cell. Only the fidelity count uses it: TEDS keeps table-bench's `norm`
    so its numbers stay comparable."""
    s = _RAISED.sub(r"\1", norm(text))
    if _BLANK.fullmatch(s):
        return ""
    return _DIGIT_SLASH.sub("", s.translate(_EXPONENT_MARKS))


def tokens(text: str | None) -> list[str]:
    """Words for detection matching: NFKC, lower case, runs of letters and digits."""
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text or "").lower())


def alnum_chars(text: str | None) -> Counter[str]:
    """Letters and digits as a multiset (order-free, so notation-robust)."""
    s = unicodedata.normalize("NFKC", text or "").replace(_INCREMENT, _GREEK_DELTA)
    return Counter(ch for ch in s if ch.isalnum())


def f1_of(pred: Counter[str], gold: Counter[str]) -> float:
    """F1 of two multisets (0 when they share nothing)."""
    inter = sum((pred & gold).values())
    p = inter / max(1, sum(pred.values()))
    r = inter / max(1, sum(gold.values()))
    return 2 * p * r / (p + r) if p + r else 0.0


@lru_cache(maxsize=200_000)
def levenshtein(a: str, b: str) -> int:
    """Edit distance (insert, delete, substitute; each 1) between two strings."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def normalized_levenshtein(a: str, b: str) -> float:
    """Edit distance / the longer length (0 for two empty strings)."""
    if not a and not b:
        return 0.0
    return levenshtein(a, b) / max(len(a), len(b))


# =========================================================================== cells


@dataclass(frozen=True)
class Cell:
    """One cell of a table, anchored at (row, col), 0-based.

    `header` is "col" (labels the cells below) or "row" (the cells to its right), None
    for a data cell. `math`: the cell holds maths (gold: a MathML element or a maths
    span; prediction: MathML or LaTeX). `mathml`: the cell's MathML when the whole cell
    is one formula (gold) or the proposal's MathML (prediction).
    """

    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    text: str = ""
    header: HeaderKind | None = None
    math: bool = False
    mathml: str | None = None
    # Gold only: a cell of the table's footer (<tfoot>: a footnote such as "*The index
    # varies ..."), which the PDF prints under the table, so a prediction may leave it out.
    footnote: bool = False


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _node_text(elem: ET.Element) -> str:
    """Visible text of a cell, MathML annotations skipped, whitespace collapsed."""
    out: list[str] = []

    def walk(node: ET.Element) -> None:
        if _local(node.tag) in _SKIP_TEXT:
            return
        if node.text:
            out.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                out.append(child.tail)

    walk(elem)
    return " ".join("".join(out).split())


def _span(value: str | None) -> int:
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


def _cell_mathml(cell: ET.Element) -> str | None:
    """The cell's MathML when the whole cell is one <math> (no words beside it)."""
    maths = [e for e in cell.iter() if _local(e.tag) == "math"]
    if len(maths) != 1 or norm(_node_text(cell)) != norm(_node_text(maths[0])):
        return None
    clone = SafeET.fromstring(ET.tostring(maths[0], encoding="unicode"))
    for node in clone.iter():
        node.tag = _local(node.tag)
    clone.set("xmlns", _MATHML_NS)
    clone.tail = None
    return ET.tostring(clone, encoding="unicode")


def _gold_header_kind(scope: str | None, row: int, col: int, row_cells: list[str]) -> HeaderKind:
    """Which way a gold <th> points: its scope, else a leading cell of a body row whose
    other cells are data is a row header, anything else a column header."""
    if scope in {"row", "rowgroup"}:
        return "row"
    if scope in {"col", "colgroup"}:
        return "col"
    others = [tag for i, tag in enumerate(row_cells) if i]
    if col == 0 and row > 0 and others and all(tag == "td" for tag in others):
        return "row"
    return "col"


def cells_from_html(table_html: str) -> list[Cell]:
    """Gold table XHTML (OpenStax, authored) -> cells with grid positions.

    Spans are honoured (a slot covered from above pushes the next cell right). A cell
    holds maths when it contains a <math> element or a span of class "m" (authored
    tables mark maths that way).
    """
    root = SafeET.fromstring(table_html)
    rows = [e for e in root.iter() if _local(e.tag) == "tr"]
    footer = {
        id(tr)
        for foot in root.iter()
        if _local(foot.tag) == "tfoot"
        for tr in foot.iter()
        if _local(tr.tag) == "tr"
    }
    occupied: set[tuple[int, int]] = set()
    cells: list[Cell] = []
    for r, tr in enumerate(rows):
        children = [cell for cell in tr if _local(cell.tag) in {"th", "td"}]
        tags = [_local(cell.tag) for cell in children]
        c = 0
        for i, cell in enumerate(children):
            while (r, c) in occupied:
                c += 1
            rs, cs = _span(cell.get("rowspan")), _span(cell.get("colspan"))
            for dr in range(rs):
                for dc in range(cs):
                    occupied.add((r + dr, c + dc))
            has_math = any(
                _local(x.tag) == "math" or "m" in (x.get("class") or "").split()
                for x in cell.iter()
            )
            header: HeaderKind | None = None
            if tags[i] == "th":
                header = _gold_header_kind(cell.get("scope"), r, i, tags)
            cells.append(
                Cell(
                    row=r,
                    col=c,
                    row_span=rs,
                    col_span=cs,
                    text=_node_text(cell),
                    header=header,
                    math=has_math,
                    mathml=_cell_mathml(cell) if has_math else None,
                    footnote=id(tr) in footer,
                )
            )
            c += cs
    return cells


def _mathml_text(mathml: str | None) -> str:
    if not mathml:
        return ""
    try:
        return " ".join("".join(normalize(mathml, lenient=True).itertext()).split())
    except MathMLError:
        return ""


def cells_from_grid(grid: TableGrid) -> list[Cell]:
    """A pipeline grid (`TableGrid`) -> cells. A maths cell's text is its own text, else
    the token text of its MathML, else its LaTeX (what a reader would get)."""
    out = []
    for cell in grid.cells:
        text = cell.text.strip() or _mathml_text(cell.mathml) or (cell.latex or "")
        out.append(
            Cell(
                row=cell.row,
                col=cell.col,
                row_span=cell.row_span,
                col_span=cell.col_span,
                text=" ".join(text.split()),
                header=cell.header,
                math=bool(cell.mathml or cell.latex),
                mathml=cell.mathml,
            )
        )
    return out


def cells_from_rows(rows: Iterable[dict]) -> list[Cell]:
    """table-bench prediction cells ({r, c, rs, cs, text, header}) -> cells. A header
    there is a boolean (its tools only know header rows), so it becomes "col"."""
    return [
        Cell(
            row=int(x["r"]),
            col=int(x["c"]),
            row_span=int(x.get("rs", 1)),
            col_span=int(x.get("cs", 1)),
            text=str(x.get("text", "")),
            header="col" if x.get("header") else None,
        )
        for x in rows
    ]


def grid_rows(cells: Sequence[Cell]) -> list[list[Cell]]:
    """Cells grouped by anchor row (rows only covered by spans from above have none)."""
    rows: dict[int, list[Cell]] = defaultdict(list)
    for cell in cells:
        rows[cell.row].append(cell)
    return [sorted(rows[r], key=lambda c: c.col) for r in sorted(rows)]


def n_cols(cells: Sequence[Cell]) -> int:
    return max((c.col + c.col_span for c in cells), default=0)


def table_tokens(cells: Iterable[Cell]) -> Counter[str]:
    return Counter(t for c in cells for t in tokens(c.text))


def table_chars(cells: Iterable[Cell]) -> Counter[str]:
    total: Counter[str] = Counter()
    for cell in cells:
        total.update(alnum_chars(cell.text))
    return total


# ============================================================================ TEDS


def table_tree(cells: Sequence[Cell], *, headers: bool = False) -> ted.Node:
    """table -> tr -> td tree for TEDS. Labels carry the tag and spans (and, with
    `headers`, "th" and the header direction); `content` is the normalised text."""
    rows = []
    for row in grid_rows(cells):
        kids = []
        for cell in row:
            tag = f"th-{cell.header}" if headers and cell.header else "td"
            label = f"{tag} {cell.row_span}x{cell.col_span}"
            kids.append(ted.Node(label, content=norm(cell.text)))
        rows.append(ted.Node("tr", kids))
    return ted.Node("table", rows)


def _cell_relabel(a: ted.Node, b: ted.Node) -> float:
    if a.label != b.label:
        return 1.0
    if a.label in {"table", "tr"}:
        return 0.0
    return normalized_levenshtein(a.content, b.content)


def _structure_relabel(a: ted.Node, b: ted.Node) -> float:
    return float(a.label != b.label)


def teds(
    pred: Sequence[Cell],
    gold: Sequence[Cell],
    *,
    structure_only: bool = False,
    headers: bool = False,
) -> float:
    """TEDS of a predicted table against gold (0 with no prediction).

    Unit cost to delete or insert a node; relabelling a cell costs 1 for another tag or
    span (and header, with `headers`), else its text's normalised Levenshtein distance
    (0 with `structure_only`). TEDS = 1 - TED / max(nodes), at least 0.
    """
    if not pred:
        return 0.0
    tree_p = table_tree(pred, headers=headers)
    tree_g = table_tree(gold, headers=headers)
    relabel = _structure_relabel if structure_only else _cell_relabel
    return ted.teds(tree_p, tree_g, relabel)


# ========================================================================= headers


def header_text(text: str | None) -> str:
    """A header cell's text for the header numbers: `norm`, with a raised mark the grid
    read from the PDF writes "^([1])" compared as its characters ("[1]"), as gold's
    text drops the raise ("Relative Strength<sup>[1]</sup>"). Nothing else changes, so
    table-bench's predictions (which carry no such mark) score as before."""
    return _RAISED.sub(r"\1", norm(text))


def header_texts(cells: Iterable[Cell], kind: HeaderKind | None = None) -> Counter[str]:
    """Normalised texts of the non-empty header cells (of one direction, or all)."""
    return Counter(
        header_text(c.text)
        for c in cells
        if c.header and (kind is None or c.header == kind) and header_text(c.text)
    )


def header_keys(cells: Iterable[Cell], kind: HeaderKind) -> Counter[tuple[int, int, str, str]]:
    """(row, col, scope, text) of the non-empty header cells of one direction."""
    return Counter(
        (c.row, c.col, kind, header_text(c.text))
        for c in cells
        if c.header == kind and header_text(c.text)
    )


def header_exact(pred: Sequence[Cell], gold: Sequence[Cell]) -> bool | None:
    """table-bench's "header exact": the header texts equal gold's (None: gold has none)."""
    want = header_texts(gold)
    if not want:
        return None
    return header_texts(pred) == want


def kind_exact(pred: Sequence[Cell], gold: Sequence[Cell], kind: HeaderKind) -> bool | None:
    """Column (or row) headers' texts equal gold's (None when gold has none of them)."""
    want = header_texts(gold, kind)
    if not want:
        return None
    return header_texts(pred, kind) == want


# =========================================================================== maths


def math_cell_scores(pred: Sequence[Cell], gold: Sequence[Cell]) -> list[dict]:
    """Per gold maths cell with letters or digits: the best letter/digit F1 of any
    predicted cell, and (when the gold cell is one formula) whether the predicted cell at
    the same place has MathML equal to gold after lenient normalisation."""
    out = []
    for g in gold:
        if not g.math:
            continue
        want = alnum_chars(g.text)
        if not want:
            continue
        best = max((f1_of(alnum_chars(p.text), want) for p in pred), default=0.0)
        mathml_match: bool | None = None
        if g.mathml:
            same = [p for p in pred if p.row == g.row and p.col == g.col and p.mathml]
            mathml_match = any(_same_maths(p.mathml, g.mathml) for p in same)
        out.append({"f1": best, "mathml_match": mathml_match})
    return out


def _same_maths(pred: str | None, gold: str) -> bool:
    if not pred:
        return False
    try:
        return canonical(pred, lenient=True) == canonical(gold, lenient=True)
    except MathMLError:
        return False


# =================================================================== text fidelity


def _fidelity_key(cell: Cell) -> str:
    if not fidelity_text(cell.text):
        return ""  # a fill-in blank has no letters to match
    return "".join(sorted(alnum_chars(cell.text).elements()))


def cell_fidelity(pred: Sequence[Cell], gold: Sequence[Cell]) -> dict:
    """How many cells carry gold's text, and how many cell errors there are.

    A predicted non-empty cell is faithful when its text (`fidelity_text`) is a gold
    cell's (each gold cell used once), or, failing that, its letters and digits are
    (maths written another way). Errors per table = max(predicted cells left over, gold
    cells left over): a misread number is one error, a dropped or invented one is one,
    two cells merged into one are two. Fill-in blanks count as empty cells on both
    sides; gold's footnote cells count only when the prediction has them too (the PDF
    prints them under the table, not in it).
    """
    printed = {fidelity_text(c.text) for c in pred} | {_fidelity_key(c) for c in pred}
    gold = [
        c
        for c in gold
        if not c.footnote or fidelity_text(c.text) in printed or _fidelity_key(c) in printed
    ]
    gold_left = Counter(fidelity_text(c.text) for c in gold if fidelity_text(c.text))
    pred_left: list[Cell] = []
    for cell in pred:
        key = fidelity_text(cell.text)
        if not key:
            continue
        if gold_left[key] > 0:
            gold_left[key] -= 1
        else:
            pred_left.append(cell)
    gold_rest = [c for c in gold if fidelity_text(c.text)]
    # Second pass on letters and digits for what is left on both sides.
    remaining = Counter(gold_left)
    gold_by_key: Counter[str] = Counter()
    for cell in gold_rest:
        if remaining[fidelity_text(cell.text)] > 0:
            remaining[fidelity_text(cell.text)] -= 1
            gold_by_key[_fidelity_key(cell)] += 1
    still: list[Cell] = []
    for cell in pred_left:
        key = _fidelity_key(cell)
        if key and gold_by_key[key] > 0:
            gold_by_key[key] -= 1
        else:
            still.append(cell)
    gold_cells = sum(1 for c in gold if fidelity_text(c.text))
    gold_unmatched = sum(gold_by_key.values())
    return {
        "gold_cells": gold_cells,
        "faithful": gold_cells - gold_unmatched,
        "pred_unmatched": len(still),
        "gold_unmatched": gold_unmatched,
        "errors": max(len(still), gold_unmatched),
    }


# ======================================================================= matching


@dataclass
class GoldTable:
    """One gold table in one printed document (`doc`), on its 0-based PDF `page`."""

    id: str
    doc: str
    style: str
    page: int
    cells: list[Cell]
    is_layout: bool = False
    cluster: str = ""  # source table: the three prints of one OpenStax table share it

    def __post_init__(self) -> None:
        self.cluster = self.cluster or self.id
        self.tok = table_tokens(self.cells)
        self.chars = table_chars(self.cells)

    @property
    def has_math(self) -> bool:
        return any(c.math for c in self.cells)

    @property
    def has_span(self) -> bool:
        return any(c.row_span > 1 or c.col_span > 1 for c in self.cells)

    @property
    def has_row_headers(self) -> bool:
        return any(c.header == "row" for c in self.cells)


@dataclass
class PredTable:
    """One table the pipeline found (`is_data` False: it called it a layout table).

    `match_cells` are what detection matching reads (the detector's own grid); `cells`
    are what is scored for structure (the proposal: the local grid in local mode, the
    rebuilt grid in model mode; empty when the table was kept as an image). `review`,
    `status`, `reasons` come from the table's fix; `bbox` is in PDF points.
    """

    doc: str
    page: int
    cells: list[Cell]
    match_cells: list[Cell] | None = None
    is_data: bool = True
    bbox: tuple[float, float, float, float] | None = None
    element_id: str | None = None
    status: str | None = None
    review: str | None = None
    reasons: list[str] = field(default_factory=list)
    source: str | None = None

    @property
    def detected_cells(self) -> list[Cell]:
        return self.cells if self.match_cells is None else self.match_cells


def match_document(golds: Sequence[GoldTable], preds: Sequence[PredTable]) -> dict:
    """Detection for one document: which prediction matched which gold table.

    Returns {"match": {gold index: (pred index, score)}, "false": [pred indexes],
    "layout": {layout gold index: pred index} (matched, so neither true nor false),
    "best": {pred index: score}}. Only data-table predictions take part (`is_data`).
    """
    cands: list[tuple[float, int, int]] = []
    best: dict[int, float] = {}
    candidates = [i for i, p in enumerate(preds) if p.is_data]
    for pi in candidates:
        p = preds[pi]
        cells = p.detected_cells
        pt, pch = table_tokens(cells), table_chars(cells)
        best[pi] = 0.0
        for gi, g in enumerate(golds):
            if g.page != p.page:
                continue
            f = f1_of(pt, g.tok)
            fc = f1_of(pch, g.chars)
            if fc >= CHAR_F1:
                f = max(f, fc - (CHAR_F1 - MATCH_F1))  # rank on a common scale
            best[pi] = max(best[pi], f)
            if f >= MATCH_F1:
                cands.append((f, pi, gi))
    cands.sort(reverse=True)
    used_g: set[int] = set()
    used_p: set[int] = set()
    match: dict[int, tuple[int, float]] = {}
    layout: dict[int, int] = {}
    for f, pi, gi in cands:  # greedy one-to-one assignment, best pairs first
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        if golds[gi].is_layout:
            layout[gi] = pi  # a layout table (Key Terms): neither TP nor FP
            continue
        match[gi] = (pi, f)
    false = [pi for pi in candidates if pi not in used_p]
    return {"match": match, "false": false, "layout": layout, "best": best}


def score_table(pred: PredTable | None, gold: GoldTable) -> dict:
    """Every per-table number for one gold data table (pred None: missed)."""
    pc = pred.cells if pred is not None else []
    gc = gold.cells
    row: dict[str, Any] = {
        "gold_id": gold.id,
        "cluster": gold.cluster,
        "doc": gold.doc,
        "style": gold.style,
        "page": gold.page,
        "has_math": gold.has_math,
        "has_span": gold.has_span,
        "has_row_headers": gold.has_row_headers,
        "detected": pred is not None,
        "gold_has_headers": bool(header_texts(gc)),
        "gold_has_col_headers": bool(header_texts(gc, "col")),
        "gold_has_row_headers": bool(header_texts(gc, "row")),
    }
    exact = header_exact(pc, gc)
    col_exact = kind_exact(pc, gc, "col")
    row_exact = kind_exact(pc, gc, "row")
    row.update(
        teds=teds(pc, gc) if pred is not None else 0.0,
        teds_structure=teds(pc, gc, structure_only=True) if pred is not None else 0.0,
        teds_with_headers=teds(pc, gc, headers=True) if pred is not None else 0.0,
        shape_ok=pred is not None
        and len(grid_rows(pc)) == len(grid_rows(gc))
        and n_cols(pc) == n_cols(gc),
        header_exact=bool(exact) if exact is not None else None,
        column_header_exact=bool(col_exact) if col_exact is not None else None,
        row_header_exact=bool(row_exact) if row_exact is not None else None,
        maths=math_cell_scores(pc, gc),
    )
    if pred is None:
        return row
    keys = {}
    for kind in ("col", "row"):
        p, g = header_keys(pc, kind), header_keys(gc, kind)
        keys[kind] = {
            "pred": sum(p.values()),
            "gold": sum(g.values()),
            "hit": sum((p & g).values()),
        }
    fidelity = cell_fidelity(pc, gc)
    row.update(
        page_pred=pred.page,
        element_id=pred.element_id,
        source=pred.source,
        status=pred.status,
        review=pred.review,
        reasons=list(pred.reasons),
        kept_as_image=not pc,
        header_cells=keys,
        pred_has_headers=bool(header_texts(pc)),
        pred_row_headers=bool(header_texts(pc, "row")),
        fidelity=fidelity,
    )
    # Wrong, for the review flags: a cell error, another structure, or column headers
    # that differ (header direction conventions aside: row headers are not counted).
    row["wrong"] = bool(
        fidelity["errors"]
        or row["teds_structure"] < 1.0
        or row["column_header_exact"] is False
        or not pc
    )
    return row


def score_documents(
    golds: Sequence[GoldTable], preds: Sequence[PredTable]
) -> tuple[list[dict], list[dict], dict]:
    """Score every document: per gold data table rows, false tables, and counts.

    Gold and predictions are grouped by `doc`; detection is per page inside a document.
    """
    gold_by_doc: dict[str, list[GoldTable]] = defaultdict(list)
    pred_by_doc: dict[str, list[PredTable]] = defaultdict(list)
    for g in golds:
        gold_by_doc[g.doc].append(g)
    for p in preds:
        pred_by_doc[p.doc].append(p)
    rows: list[dict] = []
    false: list[dict] = []
    counts = Counter()
    layout_rows: list[dict] = []
    for doc in sorted(set(gold_by_doc) | set(pred_by_doc)):
        gs, ps = gold_by_doc.get(doc, []), pred_by_doc.get(doc, [])
        found = match_document(gs, ps)
        counts["predicted"] += sum(1 for p in ps if p.is_data)
        counts["predicted_layout"] += sum(1 for p in ps if not p.is_data)
        counts["ignored_layout_matches"] += len(found["layout"])
        for gi, g in enumerate(gs):
            if g.is_layout:
                # Kept out: no data-table prediction matched it (not found, or found and
                # called a layout table, which is right).
                kept_out = gi not in found["layout"]
                layout_rows.append(
                    {"gold_id": g.id, "doc": doc, "style": g.style, "kept_out": kept_out}
                )
                continue
            pi = found["match"].get(gi, (None, 0.0))[0]
            pred = ps[pi] if pi is not None else None
            row = score_table(pred, g)
            if pi is not None:
                row["match_score"] = round(found["match"][gi][1], 4)
            else:
                row["called_layout"] = _called_layout(g, ps)
            rows.append(row)
        for pi in found["false"]:
            p = ps[pi]
            false.append(
                {
                    "doc": doc,
                    "style": next((g.style for g in gs), p.doc),
                    "page": p.page,
                    "best_score": round(found["best"].get(pi, 0.0), 2),
                    "cells": len(p.detected_cells),
                    "review": p.review,
                    "element_id": p.element_id,
                }
            )
    return rows, false, {"counts": dict(counts), "layout": layout_rows}


def _called_layout(gold: GoldTable, preds: Sequence[PredTable]) -> bool:
    """A missed data table that a layout-table prediction covers (data called layout)."""
    for p in preds:
        if p.is_data or p.page != gold.page:
            continue
        cells = p.detected_cells
        if f1_of(table_tokens(cells), gold.tok) >= MATCH_F1:
            return True
    return False


# ======================================================================= statistics


def cluster_bootstrap(
    values: Sequence[tuple[str, float]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | None:
    """95% percentile interval of a mean, resampling clusters (source tables).

    `values` are (cluster, value) pairs; one cluster's values travel together, so the
    three prints of one OpenStax table count as one draw. None when there is nothing.
    """
    by_cluster: dict[str, list[float]] = defaultdict(list)
    for cluster, value in values:
        by_cluster[cluster].append(float(value))
    keys = sorted(by_cluster)
    if not keys:
        return None
    rng = random.Random(seed)
    sums = [(sum(by_cluster[k]), len(by_cluster[k])) for k in keys]
    stats = []
    for _ in range(samples):
        total = n = 0.0
        for _ in keys:
            s, c = sums[rng.randrange(len(keys))]
            total += s
            n += c
        stats.append(total / n)
    stats.sort()
    return stats[int(0.025 * samples)], stats[min(samples - 1, int(0.975 * samples))]


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def _wilson(successes: int, n: int) -> tuple[float, float] | None:
    return pe.wilson_interval(successes, n)


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None if precision is None or recall is None else 0.0
    return 2 * precision * recall / (precision + recall)


def summarize(rows: Sequence[dict], false: Sequence[dict], *, boot: bool = True) -> dict:
    """Detection, TEDS, header, maths and fidelity numbers over per-table rows."""
    n = len(rows)
    found = [r for r in rows if r["detected"]]
    tp, fp = len(found), len(false)
    clusters = sorted({r["cluster"] for r in rows})

    def ci(key: str, subset: Sequence[dict] | None = None) -> tuple[float, float] | None:
        if not boot:
            return None
        pick = rows if subset is None else subset
        pairs = [(r["cluster"], float(r[key])) for r in pick if r.get(key) is not None]
        return cluster_bootstrap(pairs)

    out: dict[str, Any] = {
        "gold_data_tables": n,
        "clusters": len(clusters),
        "detected": tp,
        "false_tables": fp,
        "detection_recall": _div(tp, n),
        "detection_recall_ci95": ci("detected"),
        "detection_precision": _div(tp, tp + fp),
        "detection_precision_ci95": _wilson(tp, tp + fp),
        "teds": _mean(r["teds"] for r in rows),
        "teds_ci95": ci("teds"),
        "teds_structure": _mean(r["teds_structure"] for r in rows),
        "teds_structure_ci95": ci("teds_structure"),
        "teds_found": _mean(r["teds"] for r in found),
        "teds_structure_found": _mean(r["teds_structure"] for r in found),
        "teds_with_headers": _mean(r["teds_with_headers"] for r in rows),
        "teds_with_headers_found": _mean(r["teds_with_headers"] for r in found),
        "shape_ok": _mean(float(r["shape_ok"]) for r in rows),
    }
    with_headers = [r for r in rows if r["header_exact"] is not None]
    with_cols = [r for r in rows if r["column_header_exact"] is not None]
    with_rows = [r for r in rows if r["row_header_exact"] is not None]
    keys = {
        kind: {k: sum(r["header_cells"][kind][k] for r in found) for k in ("pred", "gold", "hit")}
        for kind in ("col", "row")
    }
    headers: dict[str, Any] = {}
    for kind, name in (("col", "column"), ("row", "row")):
        k = keys[kind]
        precision, recall = _div(k["hit"], k["pred"]), _div(k["hit"], k["gold"])
        subset = with_cols if kind == "col" else with_rows
        exact_key = "column_header_exact" if kind == "col" else "row_header_exact"
        hits = sum(bool(r[exact_key]) for r in subset)
        headers[name] = {
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
            "tables": len(subset),
            "tables_exact": hits,
            "exact": _div(hits, len(subset)),
            "exact_ci95": ci(exact_key, subset),
        }
    headers["header_exact"] = _mean(float(bool(r["header_exact"])) for r in with_headers)
    headers["header_exact_ci95"] = ci("header_exact", with_headers)
    headers["gold_tables_without_headers"] = sum(1 for r in rows if not r["gold_has_headers"])
    headers["found_tables_without_headers"] = sum(1 for r in found if not r["pred_has_headers"])
    headers["share_found_without_headers"] = _div(headers["found_tables_without_headers"], tp)
    headers["row_headers_where_gold_has_none"] = sum(
        1 for r in found if r["pred_row_headers"] and not r["gold_has_row_headers"]
    )
    out["headers"] = headers
    out["column_header_exact"] = headers["column"]["exact"]
    out["column_header_exact_ci95"] = headers["column"]["exact_ci95"]
    out["header_exact"] = headers["header_exact"]
    maths = [m for r in rows for m in r["maths"]]
    with_mathml = [m for m in maths if m["mathml_match"] is not None]
    out["maths"] = {
        "cells": len(maths),
        "letter_digit_f1": _mean(m["f1"] for m in maths),
        "cells_ok": _mean(float(m["f1"] >= MATH_CELL_OK) for m in maths),
        "mathml_cells": len(with_mathml),
        "mathml_match": _mean(float(bool(m["mathml_match"])) for m in with_mathml),
    }
    fidelity = [r["fidelity"] for r in found if r.get("fidelity")]
    cells = sum(f["gold_cells"] for f in fidelity)
    faithful = sum(f["faithful"] for f in fidelity)
    out["text_fidelity"] = {
        "gold_cells": cells,
        "faithful": faithful,
        "faithful_rate": _div(faithful, cells),
        "wrong_cells": sum(f["errors"] for f in fidelity),
        "wrong_cells_not_flagged": sum(
            r["fidelity"]["errors"] for r in found if r.get("fidelity") and not _flagged(r)
        ),
        "tables_with_wrong_cells": sum(1 for f in fidelity if f["errors"]),
    }
    return out


def _flagged(row: dict) -> bool:
    """The table is a required decision (a person is asked about it)."""
    return row.get("review") == "required"


def data_vs_layout(rows: Sequence[dict], layout_rows: Sequence[dict]) -> dict:
    """Layout tables kept out of the data tables, and data tables called layout."""
    kept = sum(1 for r in layout_rows if r["kept_out"])
    return {
        "layout_negatives": len(layout_rows),
        "layout_kept_out": kept,
        "accuracy_on_negatives": _div(kept, len(layout_rows)),
        "accuracy_on_negatives_ci95": _wilson(kept, len(layout_rows)),
        "data_called_layout": sum(1 for r in rows if r.get("called_layout")),
    }


def breakdowns(rows: Sequence[dict], false: Sequence[dict]) -> dict:
    """Per style and per kind of table (spans, maths, row headers): the main numbers."""
    out: dict[str, dict] = {"by_style": {}, "by_feature": {}}
    styles = [*TABLE_STYLES, AUTHORED]
    for style in styles:
        picked = [r for r in rows if r["style"] == style]
        if not picked:
            continue
        s = summarize(picked, [f for f in false if f["style"] == style], boot=False)
        out["by_style"][style] = _brief(s)
    for name, key in (
        ("spans", "has_span"),
        ("maths", "has_math"),
        ("row_headers", "has_row_headers"),
    ):
        picked = [r for r in rows if r[key]]
        if picked:
            s = summarize(picked, [], boot=False)
            out["by_feature"][name] = {k: v for k, v in _brief(s).items() if k != "precision"}
    return out


def _brief(s: dict) -> dict:
    return {
        "n": s["gold_data_tables"],
        "recall": s["detection_recall"],
        "precision": s["detection_precision"],
        "teds": s["teds"],
        "teds_found": s["teds_found"],
        "column_header_exact": s["column_header_exact"],
    }


# ==================================================================== page assignment


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0]
        for j, y in enumerate(b):
            current.append(previous[j] + 1 if x == y else max(previous[j + 1], current[j]))
        previous = current
    return previous[-1]


def _reading_score(sequence: Sequence[str], words: Sequence[str]) -> float:
    """Share of a table's tokens (row-major) found in order in a window of a page."""
    n = len(sequence)
    if not n:
        return 0.0
    width = max(2 * n, n + 10)
    step = max(1, n // 2)
    best = 0
    for i in range(0, max(1, len(words) - n // 2), step):
        best = max(best, _lcs(sequence, words[i : i + width]))
        if best == n:
            break
    return best / n


def assign_pages(
    tables: Sequence[Sequence[Cell]], page_texts: Sequence[str], page_words: Sequence[Sequence[str]]
) -> list[int]:
    """The 0-based PDF page of each gold table of one document, in document order.

    First pass (bag of words): the first page at or after the previous table's page
    whose text holds at least 95% of the most any page holds of the table's tokens (a
    repeated table, as in an exercise, then lands on its own later page). Second pass
    (reading order): each page scored by the longest common subsequence of the table's
    tokens in row-major order with a window of the page's words, pages chosen in order
    to maximise the total; it replaces a first-pass page only when that page scores under
    0.6 and the new one at least 0.15 more. `page_texts` are the pages' text,
    `page_words` their words in reading order. Never looks at predictions.
    """
    if not tables or not page_texts:
        return [0 for _ in tables]
    bags = [Counter(tokens(text)) for text in page_texts]
    first: list[int] = []
    previous = 0
    for cells in tables:
        want = table_tokens(cells)
        held = [sum((bag & want).values()) for bag in bags]
        top = max(held)
        candidates = [i for i, v in enumerate(held) if v >= 0.95 * top and i >= previous]
        page = candidates[0] if candidates else held.index(top)
        first.append(page)
        previous = page
    words = [[t for w in page for t in tokens(w)] for page in page_words]
    sequences = [
        [t for row in grid_rows(cells) for c in row for t in tokens(c.text)] for cells in tables
    ]
    scores = [[_reading_score(seq, page) for page in words] for seq in sequences]
    n_pages = len(words)
    best = [[0.0] * n_pages for _ in tables]
    back = [[0] * n_pages for _ in tables]
    for i in range(len(tables)):
        run_best, run_arg = -1.0, 0
        for p in range(n_pages):
            if i == 0:
                prev_val, prev_arg = 0.0, 0
            else:
                if best[i - 1][p] > run_best:
                    run_best, run_arg = best[i - 1][p], p
                prev_val, prev_arg = run_best, run_arg
            best[i][p] = prev_val + scores[i][p]
            back[i][p] = prev_arg
    p = max(range(n_pages), key=lambda q: (best[-1][q], -q))
    second = [0] * len(tables)
    for i in range(len(tables) - 1, -1, -1):
        second[i] = p
        p = back[i][p]
    out = []
    for i, page in enumerate(first):
        s = scores[i]
        out.append(second[i] if s[page] < 0.6 and s[second[i]] >= s[page] + 0.15 else page)
    return out


def pdf_page_text(pdf: bytes) -> tuple[list[str], list[list[str]]]:
    """Each page's text and its words in reading order (PyMuPDF)."""
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        texts = [page.get_text() for page in doc]
        words = [[w[4] for w in page.get_text("words", sort=True)] for page in doc]
    return texts, words


# ========================================================================= hashing


def sha256_files(paths: Iterable[Path], root: Path) -> str:
    """One hash over files (relative path and content), for the report's dataset."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def gold_table_from_record(record: dict, *, doc: str, style: str, page: int) -> GoldTable:
    """A gold record (OpenStax or authored) as a `GoldTable` of one printed document."""
    return GoldTable(
        id=record["id"],
        doc=doc,
        style=style,
        page=page,
        cells=cells_from_html(record["html"]),
        is_layout=bool(record.get("is_layout")),
        cluster=record.get("cluster") or record["id"],
    )


def load_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ======================================================================== documents

Mode = Literal["local", "model"]
MODES: tuple[Mode, ...] = ("local", "model")


@dataclass(frozen=True)
class TableDocument:
    """One PDF the task converts: an OpenStax page printed in one table style, or an
    authored document. `records` are its gold tables in document order (OpenStax: the
    page's gold records; authored: gold.jsonl lines with their `page`)."""

    id: str
    style: str
    records: tuple[dict, ...]
    pdf_path: Path | None = None


def table_documents(
    gold_dir: Path, authored_dir: Path | None, styles: Sequence[str] = TABLE_STYLES
) -> list[TableDocument]:
    """Every document of the task: each gold page in each style (a page's three prints
    next to each other, so `--limit 3` is one page three ways), then the authored ones."""
    by_page: dict[tuple[str, str], list[dict]] = {}
    for record in pe.load_gold(gold_dir):
        if record.get("kind") == "table" and record.get("source", "openstax") == "openstax":
            by_page.setdefault((record["book_slug"], record["page_slug"]), []).append(record)
    docs = []
    for (book, slug), records in by_page.items():
        records.sort(key=lambda r: r.get("index_on_page", 0))
        for style in styles:
            docs.append(TableDocument(f"{book}/{slug}/{style}", style, tuple(records)))
    if authored_dir is not None and (authored_dir / "index.json").is_file():
        index = json.loads((authored_dir / "index.json").read_text(encoding="utf-8"))
        for entry in index.get("documents", []):
            # A document's gold file may also hold other kinds (the extended development
            # set's records of headings, equations, ...); only its tables are this task's.
            records = [
                r
                for r in load_jsonl(authored_dir / entry["gold"])
                if r.get("kind", "table") == "table"
            ]
            docs.append(
                TableDocument(
                    f"{AUTHORED}/{entry['id']}",
                    AUTHORED,
                    tuple(records),
                    pdf_path=authored_dir / entry["pdf"],
                )
            )
    return docs


def authored_sha256(authored_dir: Path | None) -> str | None:
    """One hash over the authored documents' index, gold and PDFs (None without them)."""
    if authored_dir is None or not (authored_dir / "index.json").is_file():
        return None
    index = json.loads((authored_dir / "index.json").read_text(encoding="utf-8"))
    paths = [authored_dir / "index.json"]
    for entry in index.get("documents", []):
        paths += [authored_dir / entry["gold"], authored_dir / entry["pdf"]]
    return sha256_files(paths, authored_dir)


def reference_pages(gold_dir: Path) -> dict[str, dict[str, int]]:
    """The committed page of each OpenStax gold table per style (empty when absent)."""
    path = gold_dir / REFERENCE_PAGES
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("pages", {})


def document_gold(doc: TableDocument, pdf: bytes) -> list[GoldTable]:
    """The document's gold tables with their PDF page (assigned on this print for an
    OpenStax page, as written in gold for an authored document)."""
    cells = [cells_from_html(r["html"]) for r in doc.records]
    if doc.pdf_path is not None:
        pages = [int(r.get("page", 0)) for r in doc.records]
    else:
        texts, words = pdf_page_text(pdf)
        pages = assign_pages(cells, texts, words)
    return [
        GoldTable(
            id=r["id"],
            doc=doc.id,
            style=doc.style,
            page=page,
            cells=c,
            is_layout=bool(r.get("is_layout")),
            cluster=r.get("cluster") or r["id"],
        )
        for r, c, page in zip(doc.records, cells, pages, strict=True)
    ]


class TableMedia(Protocol):
    """The PDFs of the task's documents. `CachedTableMedia` is the real one."""

    def pdf(self, doc: TableDocument) -> bytes: ...

    def close(self) -> None: ...


class CachedTableMedia:
    """OpenStax pages printed by Chromium in their table style (cached under
    `cache_dir/pages`, images under `cache_dir/images`); authored PDFs read as they are."""

    def __init__(
        self, gold_dir: Path, cache_dir: Path, fetcher: Any, *, paper: str = "Letter"
    ) -> None:
        from readablestem.eval import render

        self._render = render
        self.gold_dir = gold_dir
        self.cache_dir = cache_dir
        self.fetcher = fetcher
        self.paper = paper
        self.images = render.ImageCache(cache_dir / "images")
        self.browser = render.ChromiumRenderer()

    def __enter__(self) -> CachedTableMedia:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.browser.close()

    def pdf(self, doc: TableDocument) -> bytes:
        if doc.pdf_path is not None:
            return doc.pdf_path.read_bytes()
        render = self._render
        book = doc.records[0]["book_slug"]
        page = render.page_source_from_records(
            list(doc.records), self.fetcher, authors=render.book_authors(self.gold_dir, book)
        )
        style = doc.style if doc.style in TABLE_STYLES else None
        return render.render_page_pdf(
            page,
            images=self.images,
            renderer=self.browser,
            paper="A4" if self.paper == "A4" else "Letter",
            cache_dir=self.cache_dir / "pages",
            table_style=style,  # type: ignore[arg-type]
        )


# =========================================================================== runner


@dataclass
class TablesConfig:
    """One run of the tables task. `client` and `budget` only in model mode."""

    settings: Settings
    media: TableMedia
    date: str  # YYYY-MM-DD, passed in by the caller
    gold_dir: Path
    authored_dir: Path | None = None
    mode: Mode = "local"
    client: VisionClient | None = None
    styles: tuple[str, ...] = TABLE_STYLES
    limit: int | None = None
    client_label: str = "none"  # "openai", "fake", or "none" (local mode: no model)
    gold_label: str | None = None
    concurrency: int = 4
    budget: JobBudget | None = None
    real_dir: Path | None = None  # private real documents with labels (labels.json)


def _no_model_client() -> VisionClient:
    """Local mode never calls a model: a fake client with no answers, so a step that
    tried anyway would get nothing, at no cost."""
    return FakeVisionClient()


def _table_options(mode: Mode) -> ConversionOptions:
    return ConversionOptions(tables=mode == "model")


def _rebuild(element: Element, png: bytes, cfg: TablesConfig, context: str | None) -> Fix:
    """The step's fix, isolated as `run` isolates it: an error in the step is a FAILED
    fix ("internal_error"), not a broken run. Spend and configuration errors propagate."""
    from readablestem.config import ConfigError
    from readablestem.core.types import Status

    client = cfg.client if cfg.mode == "model" and cfg.client is not None else _no_model_client()
    try:
        return table_step.rebuild(
            element, png, client=client, settings=cfg.settings, context_text=context
        )
    except (BudgetExceeded, ConfigError):
        raise
    except Exception:
        return Fix(
            element_id=element.id, kind=Kind.TABLE, status=Status.FAILED, reason="internal_error"
        )


def _table_fixes(cfg: TablesConfig, pdf: bytes, elements: list[Element]) -> dict[str, Fix]:
    """The table step's fix for every table element, with the mode's options in force.

    Crops are made here (PyMuPDF on this thread); model calls run on a pool, each in a
    copy of this context so the options reach them. A `BudgetExceeded` propagates.
    """
    tables = [e for e in elements if e.kind is Kind.TABLE]
    if not tables:
        return {}
    contexts = run.context_texts(elements) if cfg.mode == "model" else {}
    with run_options.using(_table_options(cfg.mode)):
        crops = {e.id: layout.crop(pdf, e) for e in tables}
        if cfg.mode == "local":
            return {e.id: _rebuild(e, crops[e.id], cfg, None) for e in tables}
        with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
            futures = {}
            for e in tables:
                work = partial(_rebuild, e, crops[e.id], cfg, contexts.get(e.id))
                context = contextvars.copy_context()
                futures[e.id] = pool.submit(context.run, work)
            return {element_id: future.result() for element_id, future in futures.items()}


def _prediction(doc: TableDocument, element: Element, fix: Fix | None, mode: Mode) -> PredTable:
    """What the pipeline gives for one table element: the local grid in local mode, the
    fix's grid in model mode (nothing when the table was kept as an image)."""
    local = cells_from_grid(element.table) if element.table is not None else []
    grid = fix.table.grid if fix is not None and fix.table is not None else None
    if mode == "local":
        cells, is_data = local, element.table.is_data if element.table is not None else True
    else:
        cells = cells_from_grid(grid) if grid is not None else []
        is_data = (
            grid.is_data
            if grid is not None
            else (element.table.is_data if element.table is not None else True)
        )
    return PredTable(
        doc=doc.id,
        page=element.page,
        cells=cells,
        match_cells=local or cells,
        is_data=is_data,
        bbox=element.bbox,
        element_id=element.id,
        status=fix.status.value if fix is not None else None,
        review=fix.review if fix is not None else None,
        reasons=_tier_reasons(fix),
        source=grid.source if grid is not None else ("local" if local else None),
    )


def _tier_reasons(fix: Fix | None) -> list[str]:
    from readablestem.core import triage

    return triage.tier_reasons(fix) if fix is not None else []


def _document_item(cfg: TablesConfig, doc: TableDocument, pdf: bytes) -> dict:
    """Convert one document and score its tables (raises BudgetExceeded)."""
    started = time.perf_counter()
    golds = document_gold(doc, pdf)
    reference = reference_pages(cfg.gold_dir).get(doc.style, {})
    differ = sum(1 for g in golds if g.id in reference and reference[g.id] != g.page)
    item: dict[str, Any] = {
        "id": doc.id,
        "style": doc.style,
        "gold_ids": [g.id for g in golds],
        "pages_differ_from_reference": differ,
    }
    try:
        found = layout.analyze(pdf)
    except layout.LayoutError as exc:  # the pipeline could not read it: nothing found
        rows, false, extra = score_documents(golds, [])
        return (
            item
            | {
                "error": exc.code,
                "pdf_pages": 0,
                "predicted": [],
                "rows": rows,
                "false": false,
                "layout": extra["layout"],
                "review": pe.load_counts([]),
            }
            | pe._calls_summary([])
            | {"wall_ms": 0}
        )
    try:
        elements = sorted(found.elements, key=lambda e: e.order)
        fixes = _table_fixes(cfg, pdf, elements)
    finally:
        layout.release(pdf)
    tables = [e for e in elements if e.kind is Kind.TABLE]
    preds = [_prediction(doc, e, fixes.get(e.id), cfg.mode) for e in tables]
    rows, false, extra = score_documents(golds, preds)
    table_fixes = [fixes[e.id] for e in tables if e.id in fixes]
    calls = [c for f in table_fixes for c in f.calls]
    item.update(
        error=None,
        pdf_pages=len(found.pages),
        predicted=[
            {
                "element_id": p.element_id,
                "page": p.page,
                "is_data": p.is_data,
                "status": p.status,
                "review": p.review,
                "reasons": p.reasons,
                "source": p.source,
                "cells": len(p.cells),
                "kept_as_image": not p.cells,
            }
            for p in preds
        ],
        rows=rows,
        false=false,
        layout=extra["layout"],
        review=pe.load_counts(table_fixes),
        needs_review=sum(1 for f in table_fixes if f.status.value == "needs_review"),
    )
    item.update(pe._calls_summary(calls))
    item["verifier_cost_usd"] = sum(f.verifier_cost_usd for f in table_fixes)
    item["table_latency_ms"] = [sum(c.latency_ms for c in f.calls) for f in table_fixes]
    item["wall_ms"] = round((time.perf_counter() - started) * 1000)
    return item


def _budget_stop(item: dict) -> str | None:
    """A table whose step stopped for spend: the run must stop (the rest is not tried)."""
    for pred in item.get("predicted", []):
        for reason in pred.get("reasons", []):
            if reason.startswith("budget"):
                return reason
    return None


def run_tables_eval(cfg: TablesConfig) -> pe.EvalRun:
    """Run the task end to end and return the report plus one row per document.

    Inputs first, as the other tasks: every PDF is printed (or read) before the first
    model call, then the browser closes. Raises RenderError before any spend when a
    print fails.
    """
    docs = table_documents(cfg.gold_dir, cfg.authored_dir, cfg.styles)
    docs = docs[: cfg.limit] if cfg.limit is not None else docs
    planned = [d.id for d in docs]
    pdfs = [cfg.media.pdf(d) for d in docs]
    cfg.media.close()
    items: list[dict] = []
    stopped: str | None = None
    for doc, pdf in zip(docs, pdfs, strict=True):
        try:
            item = _document_item(cfg, doc, pdf)
        except BudgetExceeded as exc:
            stopped = f"budget_{exc.scope}"
            break
        if (reason := _budget_stop(item)) is not None:
            stopped = reason
            break
        items.append(item)
    real = real_document_metrics(cfg.real_dir) if cfg.real_dir is not None else None
    report = build_report(cfg, items, planned_ids=planned, stopped=stopped, real=real)
    return pe.EvalRun(report=report, items=items)


# ========================================================================= metrics


def table_review_load(items: Sequence[dict], rows: Sequence[dict]) -> dict:
    """Decisions the table step asks for, and whether they point at real errors.

    Flags: a found table is wrong when a cell's text differs from gold (`cell_fidelity`),
    its structure differs (TEDS-structure below 1), its column headers differ, or it was
    kept as an image; flag precision and recall are over found tables (false tables count
    in the load, not in the flags: they have no gold).
    """
    load = pe._sum_loads(i["review"] for i in items if "review" in i)
    tables = sum(sum(1 for p in i.get("predicted", []) if p["is_data"]) for i in items)
    fixes = sum(len(i.get("predicted", [])) for i in items)
    pdf_pages = sum(i.get("pdf_pages", 0) for i in items)
    found = [r for r in rows if r["detected"]]
    flags = pe.flag_metrics((r.get("review") == "required", int(r["wrong"])) for r in found)
    wrong_not_required = [
        {
            "doc": r["doc"],
            "gold_id": r["gold_id"],
            "review": r.get("review"),
            "reasons": r.get("reasons", []),
            "teds": round(r["teds"], 4),
            "teds_structure": round(r["teds_structure"], 4),
            "wrong_cells": r["fidelity"]["errors"] if r.get("fidelity") else None,
            "column_header_exact": r["column_header_exact"],
        }
        for r in found
        if r["wrong"] and r.get("review") != "required"
    ]
    return {
        **load,
        "tables": tables,
        "pdf_pages": pdf_pages,
        "required_per_table": _div(load["required_decisions"], tables),
        "required_per_pdf_page": _div(load["required_decisions"], pdf_pages),
        "optional_per_pdf_page": _div(load["optional"], pdf_pages),
        "needs_review_share": _div(sum(i.get("needs_review", 0) for i in items), fixes),
        "kept_as_image": sum(
            sum(1 for p in i.get("predicted", []) if p["kept_as_image"]) for i in items
        ),
        "flags": flags,
        "wrong_not_required": wrong_not_required,
    }


def tables_metrics(items: Sequence[dict]) -> dict:
    rows = [r for i in items for r in i["rows"]]
    false = [f for i in items for f in i["false"]]
    layout_rows = [x for i in items for x in i["layout"]]
    out = summarize(rows, false)
    out["data_vs_layout"] = data_vs_layout(rows, layout_rows)
    out.update(breakdowns(rows, false))
    out["page_errors"] = dict(Counter(i["error"] for i in items if i.get("error")))
    out["pages_differ_from_reference"] = sum(i.get("pages_differ_from_reference", 0) for i in items)
    out["review_load"] = table_review_load(items, rows)
    return out


def tables_cost(items: Sequence[dict], budget: JobBudget | None) -> dict:
    """Dollars and seconds per table, per document and per PDF page."""
    cost = sum(i.get("cost_usd", 0.0) for i in items) + sum(
        i.get("verifier_cost_usd", 0.0) for i in items
    )
    tables = sum(len(i.get("predicted", [])) for i in items)
    pdf_pages = sum(i.get("pdf_pages", 0) for i in items)
    latencies = [ms for i in items for ms in i.get("table_latency_ms", [])]
    out: dict[str, Any] = {
        "total_usd": cost,
        "pipeline_usd": cost,
        "per_item_usd": _div(cost, len(items)),
        "per_item_unit": "document",
        "per_table_usd": _div(cost, tables),
        "pdf_pages": pdf_pages,
        "per_pdf_page_usd": _div(cost, pdf_pages),
        "model_calls": sum(i.get("model_calls", 0) for i in items),
        "seconds_per_table_p50": (pe.percentile(latencies, 50) or 0) / 1000 if latencies else None,
        "seconds_per_table_p95": (pe.percentile(latencies, 95) or 0) / 1000 if latencies else None,
    }
    if budget is not None:
        out["budget_spent_usd"] = budget.spent_usd
        out["budget_calls"] = budget.calls
        out["dropped_usd"] = max(0.0, budget.spent_usd - cost)
    return out


def _prompt_versions(items: Sequence[dict]) -> dict[str, str]:
    seen: dict[str, set[str]] = defaultdict(set)
    for item in items:
        for step, version in item.get("prompt_versions", {}).items():
            seen[step].add(version)
    versions = {"table": table_step.PROMPT_VERSION} if table_step.PROMPT_VERSION else {}
    versions.update({step: ",".join(sorted(v)) for step, v in seen.items()})
    return dict(sorted(versions.items()))


def build_report(
    cfg: TablesConfig,
    items: list[dict],
    *,
    planned_ids: list[str],
    stopped: str | None,
    real: dict | None = None,
) -> dict:
    """The report, in the format of the other tasks (`pipeline_eval.build_report`)."""
    metrics = tables_metrics(items)
    metrics["real_documents"] = real
    walls = [float(i.get("wall_ms", 0)) for i in items]
    return {
        "schema_version": pe.SCHEMA_VERSION,
        "task": TASK,
        "mode": cfg.mode,
        "date": cfg.date,
        "pipeline_version": run.PIPELINE_VERSION,
        "prompt_versions": _prompt_versions(items),
        "models": {"main": cfg.settings.main_model, "cheap": cfg.settings.cheap_model},
        "models_used": sorted({m for i in items for m in i.get("models", [])}),
        "client": cfg.client_label,
        "dataset": {
            "gold_dir": cfg.gold_label or cfg.gold_dir.name,
            "manifest_sha256": pe.manifest_sha256(cfg.gold_dir),
            "authored_sha256": authored_sha256(cfg.authored_dir),
            "styles": list(cfg.styles),
            "limit": cfg.limit,
            "unit": "document",
            "selection_sha256": hashlib.sha256("\n".join(planned_ids).encode()).hexdigest(),
        },
        "n_planned": len(planned_ids),
        "n": len(items),
        "complete": stopped is None and len(items) == len(planned_ids),
        "stopped_reason": stopped,
        "metrics": metrics,
        "cost": tables_cost(items, cfg.budget),
        "latency_ms": pe.latency_summary(walls)
        | {"basis": "wall-clock per document: layout, crops and the table step (no printing)"},
    }


# ================================================================ real documents

LABELS_FILE = "labels.json"
# A prediction is a real table when it shares at least half of the smaller box with a
# labelled table. A labelled table is found at full extent when one prediction covers
# at least 90% of it and at least 75% of that prediction lies inside it (not cut to its
# header row, not merged with a plot or a neighbour).
REAL_OVERLAP = 0.5
FULL_COVER = 0.9
FULL_PURITY = 0.75


def _area(box: Sequence[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: Sequence[float], b: Sequence[float]) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def score_real_document(
    predicted: Sequence[tuple[int, Sequence[float]]],
    labelled: Sequence[tuple[int, Sequence[float]]],
    pages: Iterable[int] | None = None,
) -> dict:
    """Box-level detection on one real document: (page, bbox) of each predicted data
    table and each labelled real table, PDF points. Only `pages` count when given."""
    allowed = set(pages) if pages is not None else None
    preds = [(p, b) for p, b in predicted if allowed is None or p in allowed]
    golds = [(p, b) for p, b in labelled if allowed is None or p in allowed]
    true = 0
    for page, box in preds:
        if any(
            gp == page and _intersection(box, gb) >= REAL_OVERLAP * min(_area(box), _area(gb))
            for gp, gb in golds
        ):
            true += 1
    full = anywhere = 0
    for page, gbox in golds:
        overlaps = [
            (_intersection(box, gbox), box)
            for p, box in preds
            if p == page and _intersection(box, gbox) > 0
        ]
        if any(i >= REAL_OVERLAP * min(_area(b), _area(gbox)) for i, b in overlaps):
            anywhere += 1
        if any(i >= FULL_COVER * _area(gbox) and i >= FULL_PURITY * _area(b) for i, b in overlaps):
            full += 1
    return {
        "predicted": len(preds),
        "true": true,
        "labelled": len(golds),
        "full": full,
        "anywhere": anywhere,
    }


def real_document_metrics(real_dir: Path) -> dict | None:
    """Detection on private real documents (`labels.json` beside the PDFs).

    The gate's second part (docs/eval.md "Real documents"). Runs `layout.analyze` on
    each labelled PDF and compares its data-table boxes with the labelled tables. Only
    counts leave this function: no file name, no page, no text. None when the directory
    has no labels file.
    """
    labels_path = real_dir / LABELS_FILE
    if not labels_path.is_file():
        return None
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    totals = Counter()
    errors: Counter[str] = Counter()
    documents = 0
    for entry in labels.get("documents", []):
        path = real_dir / entry["file"]
        if not path.is_file():
            errors["missing_file"] += 1
            continue
        pdf = path.read_bytes()
        try:
            found = layout.analyze(pdf)
        except layout.LayoutError as exc:
            errors[exc.code] += 1
            continue
        finally:
            layout.release(pdf)
        documents += 1
        predicted = [
            (e.page, e.bbox)
            for e in found.elements
            if e.kind is Kind.TABLE and (e.table is None or e.table.is_data)
        ]
        labelled = [(int(t["page"]), t["bbox"]) for t in entry.get("tables", [])]
        totals.update(score_real_document(predicted, labelled, entry.get("pages")))
        totals["pdf_pages"] += len(found.pages)
    return {
        "documents": documents,
        "pdf_pages": totals["pdf_pages"],
        "labelled_tables": totals["labelled"],
        "predicted_tables": totals["predicted"],
        "true_tables": totals["true"],
        "precision": _div(totals["true"], totals["predicted"]),
        "precision_ci95": _wilson(totals["true"], totals["predicted"]),
        "full_extent_recall": _div(totals["full"], totals["labelled"]),
        "full_extent_recall_ci95": _wilson(totals["full"], totals["labelled"]),
        "any_extent_recall": _div(totals["anywhere"], totals["labelled"]),
        "errors": dict(errors),
        "labels_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
    }


# ========================================================================== output

HEADLINE = [
    "detection_recall",
    "detection_recall_ci95",
    "detection_precision",
    "detection_precision_ci95",
    "teds",
    "teds_ci95",
    "teds_found",
    "teds_structure",
    "teds_structure_found",
    "teds_with_headers",
    "column_header_exact",
    "column_header_exact_ci95",
    "header_exact",
    "gold_data_tables",
    "clusters",
    "false_tables",
    "pages_differ_from_reference",
    "page_errors",
]


def format_markdown(report: dict) -> str:
    """Human summary of a tables report (numbers only; no document text)."""
    m = report["metrics"]
    status = "complete" if report["complete"] else f"PARTIAL ({report['stopped_reason']})"
    lines = [
        f"## Eval: tables, {report['mode']} mode ({report['date']}, {report['client']} client)",
        "",
        f"n = {report['n']} of {report['n_planned']} documents, {status}. Pipeline "
        f"{report['pipeline_version']}; gold manifest {report['dataset']['manifest_sha256'][:12]}.",
        "",
    ]
    lines += pe._table(["metric", "value"], ((k, m.get(k)) for k in HEADLINE))
    h = m["headers"]
    lines += ["", "### Headers", ""]
    lines += pe._table(
        ["direction", "precision", "recall", "f1", "tables exact", "tables"],
        (
            (
                name,
                h[name]["precision"],
                h[name]["recall"],
                h[name]["f1"],
                h[name]["exact"],
                h[name]["tables"],
            )
            for name in ("column", "row")
        ),
    )
    lines.append(
        f"Gold data tables without headers: {h['gold_tables_without_headers']}; found tables "
        f"given no header: {h['found_tables_without_headers']}; found tables given row "
        f"headers where gold has none: {h['row_headers_where_gold_has_none']}."
    )
    dl, maths, fid = m["data_vs_layout"], m["maths"], m["text_fidelity"]
    lines += ["", "### Data vs layout, maths cells, text fidelity", ""]
    lines += pe._table(
        ["metric", "value"],
        [
            ("layout tables kept out", f"{dl['layout_kept_out']} of {dl['layout_negatives']}"),
            ("data tables called layout", dl["data_called_layout"]),
            ("maths cells", maths["cells"]),
            ("maths letter/digit F1", maths["letter_digit_f1"]),
            ("maths MathML match", maths["mathml_match"]),
            ("cells faithful", fid["faithful_rate"]),
            ("wrong cells", fid["wrong_cells"]),
            ("wrong cells not flagged", fid["wrong_cells_not_flagged"]),
        ],
    )
    lines += ["", "### By style", ""]
    lines += pe._table(
        ["style", "n", "recall", "precision", "teds", "teds found", "column headers exact"],
        (
            (
                s,
                v["n"],
                v["recall"],
                v["precision"],
                v["teds"],
                v["teds_found"],
                v["column_header_exact"],
            )
            for s, v in m["by_style"].items()
        ),
    )
    load = m["review_load"]
    lines += ["", "### Review load", ""]
    lines += pe._table(
        ["metric", "value"],
        [
            ("tables", load["tables"]),
            ("required_decisions", load["required_decisions"]),
            ("required_per_table", load["required_per_table"]),
            ("required_per_pdf_page", load["required_per_pdf_page"]),
            ("optional", load["optional"]),
            ("needs_review_share", load["needs_review_share"]),
            ("kept_as_image", load["kept_as_image"]),
            ("flag_precision", load["flags"]["flag_precision"]),
            ("flag_recall", load["flags"]["flag_recall"]),
            ("wrong_not_required", len(load["wrong_not_required"])),
        ],
    )
    cost = report["cost"]
    lines += [
        "",
        f"Cost: ${cost['total_usd']:.4f} total, ${pe._fmt(cost['per_table_usd'], 5)} per table, "
        f"${pe._fmt(cost['per_pdf_page_usd'], 5)} per PDF page ({cost['pdf_pages']} pages), "
        f"{cost['model_calls']} model calls; "
        f"{pe._fmt(cost['seconds_per_table_p50'], 2)} s per table (p50, model time).",
    ]
    real = m.get("real_documents")
    lines += ["", "### Real documents", ""]
    if real is None:
        lines.append("Not run: no private input directory with labels.")
    else:
        lines += pe._table(
            ["metric", "value"],
            [
                ("documents", real["documents"]),
                ("labelled tables", real["labelled_tables"]),
                ("predicted tables", real["predicted_tables"]),
                ("precision", real["precision"]),
                ("full_extent_recall", real["full_extent_recall"]),
                ("any_extent_recall", real["any_extent_recall"]),
            ],
        )
    return "\n".join(lines) + "\n"
