"""Table cell accuracy for figure gold v2."""

from __future__ import annotations

from readablestem.gold.figures_v2_table import score_table

REF = {
    "header": ["Year", "Sales"],
    "rows": [["2001", "10"], ["2002", "20"], ["2003", "30"], ["2004", "40"]],
}


def _table(*rows: list[str]) -> dict:
    return {"header": ["Year", "Sales"], "rows": list(rows)}


def test_missing_table_or_row_cannot_raise_the_score() -> None:
    assert score_table(None, REF)["cell_accuracy"] == 0.0
    score = score_table(_table(["2001", "10"], ["2002", "20"]), REF)
    assert score["cell_accuracy"] == 0.5 and score["rows_matched"] == 2
