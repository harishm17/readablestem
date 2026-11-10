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

import json
from pathlib import Path

import pytest

from readablestem.eval import tables as T

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


# ------------------------------------------------------------------- the full set
