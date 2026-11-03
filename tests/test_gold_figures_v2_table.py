"""Table cell accuracy for figure gold v2."""

from __future__ import annotations

from readablestem.gold.figures_v2_table import score_table

REF = {
    "header": ["Year", "Sales"],
    "rows": [["2001", "10"], ["2002", "20"], ["2003", "30"], ["2004", "40"]],
}


def _table(*rows: list[str]) -> dict:
    return {"header": ["Year", "Sales"], "rows": list(rows)}


def test_perfect_table_scores_one() -> None:
    score = score_table(REF, REF)
    assert score["cell_accuracy"] == 1.0
    assert score["rows_matched"] == 4 and score["rows_extra"] == 0 and score["columns_match"] == 1


def test_values_within_five_percent_of_the_range_count() -> None:
    near = _table(["2001", "11"], ["2002", "20"], ["2003", "31"], ["2004", "40"])
    far = _table(["2001", "14"], ["2002", "20"], ["2003", "30"], ["2004", "40"])
    assert score_table(near, REF)["cell_accuracy"] == 1.0
    assert score_table(far, REF)["cell_accuracy"] == 0.75  # 4 off; tolerance is 1.5


def test_missing_table_or_row_cannot_raise_the_score() -> None:
    assert score_table(None, REF)["cell_accuracy"] == 0.0
    score = score_table(_table(["2001", "10"], ["2002", "20"]), REF)
    assert score["cell_accuracy"] == 0.5 and score["rows_matched"] == 2


def test_extra_rows_are_counted_not_rewarded() -> None:
    extra = _table(*REF["rows"], ["2005", "50"])
    score = score_table(extra, REF)
    assert score["cell_accuracy"] == 1.0 and score["rows_extra"] == 1


def test_long_form_tables_match_on_series_and_x() -> None:
    ref = {"header": ["series", "t", "v"], "rows": [["a", "1", "2"], ["b", "1", "5"]]}
    swapped = {"header": ["series", "t", "v"], "rows": [["b", "1", "5"], ["a", "1", "2"]]}
    assert score_table(swapped, ref)["cell_accuracy"] == 1.0


def test_number_formats_are_normalised() -> None:
    ref = {"header": ["x", "y"], "rows": [["1", "1,200"], ["2", "-3"]]}
    got = {"header": ["x", "y"], "rows": [["1.0", "1200"], ["2", "−3"]]}
    assert score_table(got, ref)["cell_accuracy"] == 1.0


def test_text_cells_compare_case_insensitively() -> None:
    ref = {"header": ["Method", "Label"], "rows": [["A", "Yes"], ["B", "No"]]}
    got = {"header": ["Method", "Label"], "rows": [["a", "yes"], ["b", "maybe"]]}
    assert score_table(got, ref)["cell_accuracy"] == 0.5
