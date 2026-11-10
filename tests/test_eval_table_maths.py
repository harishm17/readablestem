"""Formulas in table cells, for the document task (`eval.table_maths`): the gold table's MathML
cells against the `<math>` elements in the output's table cells, one to one by the symbols
scorer."""

from __future__ import annotations

from readablestem.eval import table_maths as tm

NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def gold_table(*cells: str, layout: bool = False) -> dict:
    body = "".join(f"<tr><td>{c}</td></tr>" for c in cells)
    return {"kind": "table", "is_layout": layout, "html": f"<table>{body}</table>"}


def math(inner: str) -> str:
    return f"<math {NS}>{inner}</math>"


def page(*cells: str, outside: str = "") -> str:
    rows = "".join(f"<tr><td>{c}</td></tr>" for c in cells)
    return f"<html><body><table>{rows}</table>{outside}</body></html>"


X1 = math("<mi>x</mi><mo>=</mo><mn>1</mn>")
Y2 = math("<mi>y</mi><mo>=</mo><mn>2</mn>")


def test_a_formula_outside_a_cell_does_not_count():
    got = tm.score_page(page("text", outside=f"<p>{X1}</p>"), [gold_table(X1)])
    assert got is not None and got["predicted"] == 0 and got["found"] == 0


def test_pairs_are_one_to_one():
    got = tm.score_page(page(X1), [gold_table(X1, X1)])
    assert got is not None and got["gold"] == 2 and got["found"] == 1


def test_summary_adds_pages_up():
    a = tm.score_page(page(X1), [gold_table(X1, Y2)])
    b = tm.score_page(page(X1), [gold_table(X1)])
    assert a is not None and b is not None
    total = tm.summarize([a, b])
    assert total["gold"] == 3 and total["found"] == 2 and total["pages"] == 2
    assert total["found_rate"] == 2 / 3
