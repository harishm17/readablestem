"""Tables eval scorer: TEDS with the relabel hook, matching, headers, maths cells, text
fidelity, page assignment and the cluster bootstrap (offline, small hand-made tables)."""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import TableCell, TableGrid
from readablestem.eval import tables as T
from readablestem.eval import ted

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


def test_the_relabel_hook_prices_a_relabel_and_unit_costs_are_unchanged():
    a, b = ted.Node.build(("f", "a", "b")), ted.Node.build(("f", "a", "c"))
    assert ted.tree_edit_distance(a, b) == 1
    assert ted.weighted_edit_distance(a, b, lambda x, y: 0.25 * (x.label != y.label)) == 0.25
    # A relabel never costs more than a delete plus an insert.
    assert ted.weighted_edit_distance(a, b, lambda x, y: 5.0 * (x.label != y.label)) == 2
    assert ted.teds(a, b, lambda x, y: 0.5 * (x.label != y.label)) == pytest.approx(1 - 0.5 / 3)


def test_teds_is_one_for_the_same_table_and_counts_text_by_edit_distance():
    assert T.teds(SAME, GOLD) == 1.0
    misread = grid_cells(
        [["Medium", "v (m/s)"], ["Air", "343"], ["Water", "1430"], ["Steel", "5960"]]
    )
    # One digit of one 4-character cell: 0.25 of one relabel over 1 + 4 + 8 nodes.
    assert T.teds(misread, GOLD) == pytest.approx(1 - 0.25 / 13)
    assert T.teds(misread, GOLD, structure_only=True) == 1.0
    assert T.teds([], GOLD) == 0.0


def test_teds_with_headers_counts_a_header_cell_written_as_data():
    no_headers = grid_cells(
        [["Medium", "v (m/s)"], ["Air", "343"], ["Water", "1480"], ["Steel", "5960"]], 0
    )
    assert T.teds(no_headers, GOLD) == 1.0  # table-bench's TEDS ignores headers
    assert T.teds(no_headers, GOLD, headers=True) == pytest.approx(1 - 2 / 13)


def test_spans_are_structure():
    merged = [
        T.Cell(0, 0, col_span=2, text="Medium v (m/s)", header="col"),
        *[c for c in SAME if c.row > 0],
    ]
    assert T.teds(merged, GOLD, structure_only=True) < 1.0


# ---------------------------------------------------------------------------- cells


def test_gold_html_gives_positions_spans_header_directions_and_maths():
    html = (
        "<table><tr><th rowspan='2'>Material</th><th colspan='2' scope='colgroup'>Modulus</th></tr>"
        "<tr><th>E</th><th>G</th></tr>"
        "<tr><th>Steel</th><td>200</td><td><math><mn>80</mn></math></td></tr></table>"
    )
    cells = {(c.row, c.col): c for c in T.cells_from_html(html)}
    assert cells[(0, 0)].row_span == 2 and cells[(0, 0)].header == "col"
    assert cells[(0, 1)].col_span == 2 and cells[(0, 1)].header == "col"
    assert (cells[(1, 1)].text, cells[(1, 2)].text) == ("E", "G")
    assert cells[(2, 0)].header == "row"  # a leading th of a body row without scope
    assert cells[(2, 2)].math and cells[(2, 2)].mathml and "<mn>80</mn>" in cells[(2, 2)].mathml
    assert cells[(2, 1)].header is None and not cells[(2, 1)].math


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


def test_maths_heavy_tables_match_on_letters_and_digits():
    gold_cells = grid_cells([["x", "±23qe"], ["y", "±13qe"]], 0)
    split = grid_cells([["x", "± 2 3 q e"], ["y", "± 1 3 q e"]], 0)
    assert T.f1_of(T.table_tokens(split), T.table_tokens(gold_cells)) < T.MATCH_F1
    assert T.match_document([gold("g", gold_cells)], [pred(split)])["match"]


def test_predictions_called_layout_tables_do_not_take_part():
    found = T.match_document([gold("g", GOLD)], [pred(SAME, is_data=False)])
    assert found["match"] == {} and found["false"] == []
    rows, false, _ = T.score_documents([gold("g", GOLD)], [pred(SAME, is_data=False)])
    assert rows[0]["called_layout"] and not rows[0]["detected"] and false == []


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


def test_found_tables_given_no_header_are_counted():
    no_headers = [T.Cell(c.row, c.col, text=c.text) for c in SAME]
    rows, false, _ = T.score_documents(
        [gold("g", GOLD), gold("h", GOLD, page=1)], [pred(no_headers), pred(SAME, page=1)]
    )
    h = T.summarize(rows, false, boot=False)["headers"]
    assert h["found_tables_without_headers"] == 1 and h["share_found_without_headers"] == 0.5


def test_row_headers_on_a_table_whose_gold_has_none_are_counted_apart():
    with_rows = [
        T.Cell(c.row, c.col, text=c.text, header=c.header or ("row" if c.col == 0 else None))
        for c in SAME
    ]
    rows, false, _ = T.score_documents([gold("g", GOLD)], [pred(with_rows)])
    s = T.summarize(rows, false, boot=False)
    assert s["headers"]["row_headers_where_gold_has_none"] == 1
    assert s["column_header_exact"] == 1.0


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


def pdf_with_pages(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    return doc.tobytes()


def test_pages_follow_document_order_and_reading_order_fixes_a_bag_of_words_miss():
    """Statistics 4-2: a small table of common words first landed on a page that only
    mentions them; reading order puts it where its rows are printed in sequence."""
    small = grid_cells([["k", "P(k)"], ["7", "0.25"], ["8", "0.45"], ["9", "0.30"]])
    big = grid_cells([["Medium", "v"], ["Air", "343"], ["Water", "1480"]])
    pages = [
        "Medium v Air 343 Water 1480",  # the big table
        "Text that mentions 0.30 and 9, 0.45 and 8, 0.25 and 7, then k and P(k).",
        "Some prose first. k P(k) 7 0.25 8 0.45 9 0.30 and prose after.",  # the table
    ]
    texts, words = T.pdf_page_text(pdf_with_pages(pages))
    assert T.assign_pages([big, small], texts, words) == [0, 2]


# ------------------------------------------------------------------------ bootstrap


def test_cluster_bootstrap_keeps_a_clusters_values_together_and_is_seeded():
    values = [("a", 1.0), ("a", 1.0), ("a", 1.0), ("b", 0.0)]
    low, high = T.cluster_bootstrap(values, samples=500)
    assert 0.0 <= low <= high <= 1.0
    assert T.cluster_bootstrap(values, samples=500) == (low, high)
    assert T.cluster_bootstrap([]) is None


# ------------------------------------------------------------------ notation in gold


def gold_of(html: str) -> list[T.Cell]:
    return T.cells_from_html(html)


def pred_of(texts: list[list[str]]) -> list[T.Cell]:
    return [T.Cell(row=r, col=c, text=t) for r, row in enumerate(texts) for c, t in enumerate(row)]


POWERS = (
    "<table><tr><th>Length (m)</th></tr><tr><td>10<sup>\u201318</sup></td></tr>"
    "<tr><td>10<sup>2</sup></td></tr></table>"
)


@pytest.mark.parametrize("written", ["10^-18", "10\u207b\u00b9\u2078", "10^{-18}", "10\u221218"])
def test_a_power_of_ten_written_another_way_is_not_an_error(written):
    pred = pred_of([["Length (m)"], [written], ["10^2"]])
    fidelity = T.cell_fidelity(pred, gold_of(POWERS))
    assert (fidelity["errors"], fidelity["faithful"]) == (0, 3)


def test_a_stacked_fraction_written_across_is_not_an_error():
    gold = gold_of(
        "<table><tr><td>WIN</td><td><math><mfrac><mn>1</mn><mn>3</mn></mfrac></math></td></tr>"
        "<tr><td>LOSE</td><td><math><mfrac><mn>\u201312</mn><mn>3</mn></mfrac></math></td></tr></table>"
    )
    for third, other in (("1/3", "\u201312/3"), ("\u2153", "-12/3")):
        assert T.cell_fidelity(pred_of([["WIN", third], ["LOSE", other]]), gold)["errors"] == 0


def test_fill_in_blanks_are_empty_cells_however_long():
    gold = gold_of("<table><tr><td>Female</td><td>18</td><td>___</td></tr></table>")
    for blank in ("", "______", "\\_\\_\\_"):
        pred = pred_of([["Female", "18", blank]])
        fidelity = T.cell_fidelity(pred, gold)
        assert (fidelity["errors"], fidelity["gold_cells"]) == (0, 2), blank


def test_a_footnote_row_gold_keeps_in_the_table_counts_only_when_predicted():
    gold = gold_of(
        "<table><tbody><tr><td>Lens</td><td>1.41 average<sup>*</sup></td></tr></tbody>"
        "<tfoot><tr><td colspan='2'><sup>*</sup>The index varies through the lens.</td></tr>"
        "</tfoot></table>"
    )
    assert [c.footnote for c in gold] == [False, False, True]
    without = T.cell_fidelity(pred_of([["Lens", "1.41 average*"]]), gold)
    assert (without["errors"], without["gold_cells"]) == (0, 2)
    with_it = pred_of([["Lens", "1.41 average*"], ["*The index varies through the lens."]])
    assert T.cell_fidelity(with_it, gold)["faithful"] == 3


def test_real_errors_still_count():
    gold = gold_of(
        "<table><tr><td>Water</td><td>1.33</td></tr><tr><td>Air</td><td>___</td></tr></table>"
    )
    assert T.cell_fidelity(pred_of([["Water", "1.35"], ["Air", ""]]), gold)["errors"] == 1
    assert T.cell_fidelity(pred_of([["Water", "1.33"], ["Air", "1.00"]]), gold)["errors"] == 1


def test_a_raised_footnote_mark_read_from_the_pdf_is_not_an_error():
    # The grid read from the PDF marks raised text "^(...)"; gold's text drops the raise.
    gold = gold_of(
        "<table><thead><tr><th>Charge<sup>[1]</sup></th></tr></thead><tbody><tr><td>+1</td>"
        "</tr></tbody><tfoot><tr><td><sup>[1]</sup>The lower sign is for antiquarks.</td></tr>"
        "</tfoot></table>"
    )
    pred = pred_of([["Charge^([1])"], ["+1"], ["^([1]) The lower sign is for antiquarks."]])
    fidelity = T.cell_fidelity(pred, gold)
    assert (fidelity["errors"], fidelity["faithful"]) == (0, 3)


def test_a_raised_mark_in_a_header_does_not_make_the_headers_wrong():
    gold = gold_of(
        "<table><tr><th scope='col'>Force</th><th scope='col'>Relative Strength<sup>[1]</sup>"
        "</th></tr><tr><td>Strong</td><td>1</td></tr></table>"
    )
    marked = [
        T.Cell(0, 0, text="Force", header="col"),
        T.Cell(0, 1, text="Relative Strength^([1])", header="col"),
        T.Cell(1, 0, text="Strong"),
        T.Cell(1, 1, text="1"),
    ]
    assert T.kind_exact(marked, gold, "col") is True
    assert T.header_exact(marked, gold) is True
    other = [*marked[:1], T.Cell(0, 1, text="Relative Range^([1])", header="col"), *marked[2:]]
    assert T.kind_exact(other, gold, "col") is False  # a different word still counts


def test_the_marks_of_latex_in_a_formula_header_are_not_compared():
    # A header the model reads as maths has the text of its LaTeX (d_i, v_w (m/s)); the PDF's own
    # text, and so the gold's, has the letters (di, vw (m/s)).
    gold = gold_of(
        "<table><tr><th scope='col'>Medium</th><th scope='col'>v<sub>w</sub> (m/s)</th>"
        "<th scope='col'>d<sub>i</sub></th></tr><tr><td>Air</td><td>331</td><td>2</td></tr></table>"
    )
    read_as_maths = [
        T.Cell(0, 0, text="Medium", header="col"),
        T.Cell(0, 1, text="v_w (m/s)", header="col"),
        T.Cell(0, 2, text="d_{i}", header="col"),
        T.Cell(1, 0, text="Air"),
        T.Cell(1, 1, text="331"),
        T.Cell(1, 2, text="2"),
    ]
    assert T.kind_exact(read_as_maths, gold, "col") is True
    wrong = [*read_as_maths[:2], T.Cell(0, 2, text="d_o", header="col"), *read_as_maths[3:]]
    assert T.kind_exact(wrong, gold, "col") is False
    assert T.header_text("10^-18") == "10-18"


TITLED = (
    "<table><thead><tr><th colspan='2' scope='colgroup'>Frequency Distribution for Final Scores"
    "</th></tr><tr><th scope='col'>Bound</th><th scope='col'>Frequency</th></tr></thead>"
    "<tbody><tr><td>49.5</td><td>5</td></tr></tbody></table>"
)
UNTITLED = [["Bound", "Frequency"], ["49.5", "5"]]


def test_a_title_row_the_pipeline_kept_as_the_text_above_the_table_is_not_a_lost_cell():
    # Gold writes the table's title as a spanning header cell; layout sets it as a heading
    # right above the box. Nothing is lost, only placed outside the table.
    gold = gold_of(TITLED)
    kept = "Frequency Distribution for Final Scores"
    assert T.cell_fidelity(pred_of(UNTITLED), gold)["errors"] == 1
    assert T.cell_fidelity(pred_of(UNTITLED), gold, kept_text=kept)["errors"] == 0
    assert T.cell_fidelity(pred_of(UNTITLED), gold, kept_text="Another heading")["errors"] == 1


def test_a_title_the_prediction_has_in_the_table_is_matched_as_before():
    pred = pred_of([["Frequency Distribution for Final Scores", ""], *UNTITLED])
    fidelity = T.cell_fidelity(pred, gold_of(TITLED), kept_text="Frequency Distribution")
    assert fidelity["errors"] == 0


def test_only_a_title_over_the_whole_table_is_given_this_way_not_a_body_cell():
    gold = gold_of(
        "<table><tr><th>Name</th><th>Note</th></tr><tr><td>Water</td><td>Liquid at 25</td></tr>"
        "</table>"
    )
    pred = pred_of([["Name", "Note"], ["Water", ""]])
    assert T.cell_fidelity(pred, gold, kept_text="Liquid at 25")["errors"] == 1
