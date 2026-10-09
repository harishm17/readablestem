"""The tables scorer reproduces table-bench (2026-10-08) before it is trusted.

table-bench scored two local pipelines on 170 printed tables: A (PyMuPDF union+refine
grid) TEDS 0.820, B (same boxes, SLANet-plus grid) TEDS 0.868. The committed excerpt
(tests/fixtures/tables/table_bench_excerpt.json) holds both rows' predictions on five
documents and the numbers table-bench's own scorer gives on exactly them; `eval.tables`
must give the same, table by table. The full 170-table check needs the unpacked research
archive (TABLE_BENCH_EVIDENCE=/path with table-bench/ and lead/ inside) and is skipped
otherwise.
"""

from __future__ import annotations

import html.parser
import json
import os
from pathlib import Path

import pytest

from a11y_stem.eval import tables as T

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = Path(__file__).parent / "fixtures" / "tables" / "table_bench_excerpt.json"
GOLD_DIR = ROOT / "data" / "gold" / "tables"
AUTHORED_DIR = ROOT / "data" / "authored" / "tables"


def committed_gold() -> dict[str, dict]:
    """Every committed table gold record by id: OpenStax and authored."""
    records = {}
    for path in sorted(GOLD_DIR.glob("openstax/*/*.jsonl")):
        for record in T.load_jsonl(path):
            records[record["id"]] = record
    for path in sorted(AUTHORED_DIR.glob("*/gold.jsonl")):
        for record in T.load_jsonl(path):
            records[record["id"]] = record
    return records


def gold_tables(entries: list[dict]) -> list[T.GoldTable]:
    records = committed_gold()
    return [
        T.GoldTable(
            id=e["id"],
            doc=e["doc"],
            style=e["style"],
            page=e["page"],
            cells=T.cells_from_html(records[e["id"]]["html"]),
            is_layout=bool(records[e["id"]].get("is_layout")),
        )
        for e in entries
    ]


def predictions(rows: list[dict]) -> list[T.PredTable]:
    out = []
    for p in rows:
        cells = [
            dict(r=r, c=c, rs=rs, cs=cs, text=t, header=h) for r, c, rs, cs, t, h in p["cells"]
        ]
        out.append(T.PredTable(doc=p["doc"], page=p["page"], cells=T.cells_from_rows(cells)))
    return out


@pytest.fixture(scope="module")
def excerpt() -> dict:
    return json.loads(EXCERPT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("row", ["A", "B"])
def test_the_excerpt_scores_as_table_bench_scored_it(excerpt, row):
    golds = gold_tables(excerpt["gold"])
    rows, false, _ = T.score_documents(golds, predictions(excerpt["predictions"][row]))
    expected = excerpt["expected"][row]
    ours = {(r["gold_id"], r["style"]): r for r in rows}
    assert len(ours) == len(expected["per_table"])
    for gold_id, style, detected, teds, teds_s, header_exact in expected["per_table"]:
        r = ours[(gold_id, style)]
        assert r["detected"] == detected, gold_id
        assert r["teds"] == pytest.approx(teds, abs=1e-9), gold_id
        assert r["teds_structure"] == pytest.approx(teds_s, abs=1e-9), gold_id
        assert r["header_exact"] == header_exact, gold_id
    s = T.summarize(rows, false, boot=False)
    want = expected["summary"]
    assert (s["gold_data_tables"], s["detected"], s["false_tables"]) == (
        want["n_gold"],
        want["detected"],
        want["fp"],
    )
    assert s["detection_recall"] == pytest.approx(want["recall"])
    assert s["detection_precision"] == pytest.approx(want["precision"])
    assert s["teds"] == pytest.approx(want["teds_all"])
    assert s["teds_structure"] == pytest.approx(want["teds_s_all"])
    assert s["teds_found"] == pytest.approx(want["teds_det"])
    assert s["header_exact"] == pytest.approx(want["hdr_exact_all"])


def test_the_excerpt_covers_misses_false_tables_and_layout_tables(excerpt):
    """So the check above exercises every branch of the matcher, not only clean hits."""
    golds = gold_tables(excerpt["gold"])
    assert any(g.is_layout for g in golds)
    for row in ("A", "B"):
        rows, false, extra = T.score_documents(golds, predictions(excerpt["predictions"][row]))
        assert any(not r["detected"] for r in rows)
        assert any(not x["kept_out"] for x in extra["layout"])  # a match to a layout table
        assert any(r["has_row_headers"] for r in rows) and any(r["has_span"] for r in rows)
    assert T.score_documents(golds, predictions(excerpt["predictions"]["B"]))[1]  # a false table


# ------------------------------------------------------------------- the full set

EVIDENCE = os.environ.get("TABLE_BENCH_EVIDENCE")


class _Cells(html.parser.HTMLParser):
    """table-bench's lenient HTML table reading (lxml.html there): rows, cells, spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[dict] = []
        self._row = -1
        self._occupied: set[tuple[int, int]] = set()
        self._col = 0
        self._cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self._row += 1
            self._col = 0
        elif tag in {"td", "th"}:
            while (self._row, self._col) in self._occupied:
                self._col += 1
            rs, cs = int(a.get("rowspan") or 1), int(a.get("colspan") or 1)
            for dr in range(rs):
                for dc in range(cs):
                    self._occupied.add((self._row + dr, self._col + dc))
            self._cell = dict(r=self._row, c=self._col, rs=rs, cs=cs, text="")
            self._col += cs

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._cell is not None:
            self._cell["text"] = " ".join(self._cell["text"].split())
            self.cells.append(self._cell)
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def _full_set(root: Path) -> tuple[list[T.GoldTable], dict[str, list[T.PredTable]]]:
    bench = root / "table-bench"
    pages = json.loads((bench / "out" / "gold_pages.json").read_text(encoding="utf-8"))
    records = committed_gold()
    golds = []
    renamed = {"latex": "authored/tablebench-latex/", "word": "authored/tablebench-word/"}
    for key, page in pages.items():
        style, gold_id = key.split("|", 1)
        set_ = gold_id.split("/", 1)[0]
        repo_id = renamed[set_] + gold_id.split("/", 1)[1] if set_ in renamed else gold_id
        doc_name = (
            records[repo_id]["book_slug"] + "-" + records[repo_id]["page_slug"]
            if set_ == "openstax"
            else f"{set_}-tables"
        )
        record = records[repo_id]
        golds.append(
            T.GoldTable(
                id=repo_id,
                doc=f"{style}/{doc_name}",
                style=style,
                page=page,
                cells=T.cells_from_html(record["html"]),
                is_layout=bool(record.get("is_layout")),
            )
        )
    # Gold in document order inside each document, as table-bench's loader keeps it.
    golds.sort(key=lambda g: (g.doc, g.id))
    pml = [json.loads(x) for x in (bench / "out/pred/pymupdf-layout-union-refine.jsonl").open()]
    items = json.loads((root / "lead/rapid_e2e/items.json").read_text(encoding="utf-8"))
    rapid = {r["i"]: r for r in map(json.loads, (root / "lead/rapid_e2e/pred.jsonl").open())}
    a, b = [], []
    for p, it in zip(pml, items, strict=True):
        doc = f"{p['style']}/{p['doc']}"
        a.append(T.PredTable(doc=doc, page=p["page"], cells=T.cells_from_rows(p["cells"])))
        parser = _Cells()
        parser.feed(rapid[it["i"]]["html"] or "")
        cells = [dict(c, header=c["r"] < it["pml_header_rows"]) for c in parser.cells]
        b.append(T.PredTable(doc=doc, page=p["page"], cells=T.cells_from_rows(cells)))
    return golds, {"A": a, "B": b}


@pytest.mark.skipif(
    not EVIDENCE, reason="set TABLE_BENCH_EVIDENCE to the unpacked research archive"
)
def test_the_full_table_bench_numbers_are_reproduced():
    golds, rows_by = _full_set(Path(str(EVIDENCE)))
    want = {"A": (0.820, 0.912, 0.957), "B": (0.868, 0.912, 0.951)}
    for name, preds in rows_by.items():
        rows, false, _ = T.score_documents(golds, preds)
        s = T.summarize(rows, false, boot=False)
        assert s["gold_data_tables"] == 170
        teds, recall, precision = want[name]
        assert s["teds"] == pytest.approx(teds, abs=5e-4), name
        assert s["detection_recall"] == pytest.approx(recall, abs=5e-4), name
        assert s["detection_precision"] == pytest.approx(precision, abs=5e-4), name
