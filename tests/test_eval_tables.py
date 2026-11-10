"""Tables eval scorer: TEDS with the relabel hook, matching, headers, maths cells, text
fidelity, page assignment and the cluster bootstrap (offline, small hand-made tables)."""

from __future__ import annotations

from readablestem.core.types import TableCell, TableGrid
from readablestem.eval import tables as T

SPEEDS = (
    "<table><thead><tr><th scope='col'>Medium</th><th scope='col'>v (m/s)</th></tr></thead>"
    "<tbody><tr><td>Air</td><td>343</td></tr><tr><td>Water</td><td>1480</td></tr>"
    "<tr><td>Steel</td><td>5960</td></tr></tbody></table>"
)


def grid_cells(rows: list[list[str]], header_rows: int = 1) -> list[T.Cell]:
    return [
        T.Cell(row=r, col=c, text=text, header="col" if r < header_rows else None)
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]


GOLD = T.cells_from_html(SPEEDS)
SAME = grid_cells([["Medium", "v (m/s)"], ["Air", "343"], ["Water", "1480"], ["Steel", "5960"]])


# ------------------------------------------------------------------ TEDS and the hook


# ---------------------------------------------------------------------------- cells


def test_a_pipeline_grid_becomes_cells_with_maths_text_from_mathml():
    grid = TableGrid(
        rows=2,
        cols=2,
        header_rows=1,
        cells=[
            TableCell(row=0, col=0, text="Quantity", header="col"),
            TableCell(row=0, col=1, text="Value", header="col"),
            TableCell(row=1, col=0, text="g", header="row"),
            TableCell(row=1, col=1, latex="9.8", mathml="<math><mn>9.8</mn></math>"),
        ],
    )
    cells = T.cells_from_grid(grid)
    assert [c.header for c in cells] == ["col", "col", "row", None]
    assert cells[3].text == "9.8" and cells[3].math


# ------------------------------------------------------------------------- matching


def gold(id_: str, cells: list[T.Cell], page: int = 0, layout: bool = False) -> T.GoldTable:
    return T.GoldTable(id=id_, doc="d", style="grid", page=page, cells=cells, is_layout=layout)


def pred(cells: list[T.Cell], page: int = 0, **kw) -> T.PredTable:
    return T.PredTable(doc="d", page=page, cells=cells, **kw)


KEY_TERMS = grid_cells([["accuracy", "ampere", "constant"], ["meter", "second", "slope"]], 0)


def test_matching_is_by_content_on_the_same_page_and_layout_tables_count_neither_way():
    golds = [gold("g1", GOLD), gold("k", KEY_TERMS, layout=True)]
    preds = [pred(SAME), pred(KEY_TERMS), pred(SAME, page=1), pred(grid_cells([["x", "y"]]))]
    found = T.match_document(golds, preds)
    assert found["match"] == {0: (0, 1.0)}
    assert found["layout"] == {1: 1}  # neither a hit nor a false table
    assert sorted(found["false"]) == [2, 3]  # another page; no content in common


def test_a_missed_table_scores_zero_and_keeps_its_header_and_maths_denominators():
    rows, _, _ = T.score_documents([gold("g", GOLD)], [])
    (row,) = rows
    assert (row["teds"], row["header_exact"], row["column_header_exact"]) == (0.0, False, False)


# -------------------------------------------------------------------------- headers


def test_header_metrics_split_columns_and_rows_and_report_the_row_header_gap():
    gold_html = (
        "<table><tr><td></td><th scope='col'>Earth</th><th scope='col'>Mars</th></tr>"
        "<tr><th scope='row'>Mass</th><td>5.97</td><td>0.642</td></tr></table>"
    )
    gold_cells = T.cells_from_html(gold_html)
    only_columns = [
        T.Cell(0, 0, text=""),
        T.Cell(0, 1, text="Earth", header="col"),
        T.Cell(0, 2, text="Mars", header="col"),
        T.Cell(1, 0, text="Mass"),
        T.Cell(1, 1, text="5.97"),
        T.Cell(1, 2, text="0.642"),
    ]
    rows, false, _ = T.score_documents([gold("g", gold_cells)], [pred(only_columns)])
    s = T.summarize(rows, false, boot=False)
    assert s["headers"]["column"]["exact"] == 1.0
    assert s["headers"]["row"]["exact"] == 0.0 and s["headers"]["row"]["recall"] == 0.0
    assert s["header_exact"] == 0.0  # all header texts: "Mass" missing
    assert s["headers"]["share_found_without_headers"] == 0.0


# ------------------------------------------------------------- maths and fidelity


def test_maths_cells_score_letters_and_digits_and_mathml_after_normalisation():
    html = "<table><tr><th>q</th></tr><tr><td><math><mo>−</mo><mn>1.6</mn></math></td></tr></table>"
    gold_cells = T.cells_from_html(html)
    good = [
        T.Cell(0, 0, text="q", header="col"),
        T.Cell(1, 0, text="-1.6", math=True, mathml="<math><mo>-</mo><mn>1.6</mn></math>"),
    ]
    (score,) = T.math_cell_scores(good, gold_cells)
    assert score == {"f1": 1.0, "mathml_match": True}
    (flat,) = T.math_cell_scores([T.Cell(0, 0, text="q"), T.Cell(1, 0, text="1.6")], gold_cells)
    assert flat["mathml_match"] is False and flat["f1"] == 1.0  # the sign is not a digit


def test_text_fidelity_counts_misreads_drops_inventions_and_merges_once_each():
    assert T.cell_fidelity(SAME, GOLD)["errors"] == 0
    misread = [T.Cell(c.row, c.col, text="1430" if c.text == "1480" else c.text) for c in SAME]
    assert T.cell_fidelity(misread, GOLD)["errors"] == 1
    dropped = [c for c in SAME if c.text != "5960"]
    assert T.cell_fidelity(dropped, GOLD)["errors"] == 1
    merged = [c for c in SAME if c.text not in {"Water", "1480"}] + [
        T.Cell(2, 0, text="Water 1480")
    ]
    assert T.cell_fidelity(merged, GOLD)["errors"] == 2
    spaced = [T.Cell(c.row, c.col, text=c.text.replace("(m/s)", "( m / s )")) for c in SAME]
    assert T.cell_fidelity(spaced, GOLD)["errors"] == 0  # spaces are not text


def test_wrong_cells_in_a_table_nobody_is_asked_about_are_the_gate_number():
    misread = [
        T.Cell(c.row, c.col, text="1430" if c.text == "1480" else c.text, header=c.header)
        for c in SAME
    ]
    golds = [gold("g", GOLD)]
    accepted = T.summarize(
        *T.score_documents(golds, [pred(misread, review="none")])[:2], boot=False
    )
    asked = T.summarize(
        *T.score_documents(golds, [pred(misread, review="required")])[:2], boot=False
    )
    assert accepted["text_fidelity"]["wrong_cells_not_flagged"] == 1
    assert asked["text_fidelity"]["wrong_cells_not_flagged"] == 0
    assert asked["text_fidelity"]["wrong_cells"] == 1


# ------------------------------------------------------------------ data vs layout


def test_layout_tables_kept_out_and_data_tables_called_layout():
    golds = [gold("g", GOLD), gold("k", KEY_TERMS, layout=True)]
    rows, _, extra = T.score_documents(golds, [pred(SAME, is_data=False), pred(KEY_TERMS)])
    dl = T.data_vs_layout(rows, extra["layout"])
    assert dl["layout_negatives"] == 1 and dl["layout_kept_out"] == 0  # called data
    assert dl["data_called_layout"] == 1


# ------------------------------------------------------------------ page assignment


# ------------------------------------------------------------------------ bootstrap


# ------------------------------------------------------------------ notation in gold


def gold_of(html: str) -> list[T.Cell]:
    return T.cells_from_html(html)


def pred_of(texts: list[list[str]]) -> list[T.Cell]:
    return [T.Cell(row=r, col=c, text=t) for r, row in enumerate(texts) for c, t in enumerate(row)]


def test_real_errors_still_count():
    gold = gold_of(
        "<table><tr><td>Water</td><td>1.33</td></tr><tr><td>Air</td><td>___</td></tr></table>"
    )
    assert T.cell_fidelity(pred_of([["Water", "1.35"], ["Air", ""]]), gold)["errors"] == 1
    assert T.cell_fidelity(pred_of([["Water", "1.33"], ["Air", "1.00"]]), gold)["errors"] == 1
