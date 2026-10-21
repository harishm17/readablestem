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


def test_every_gold_formula_found_in_a_cell_counts():
    got = tm.score_page(page(X1, Y2), [gold_table(X1, Y2)])
    assert got == {
        "gold": 2,
        "predicted": 2,
        "found": 2,
        "found_exact_symbols": 2,
        "found_exact_lenient": 2,
    }


def test_a_table_kept_as_an_image_has_none():
    got = tm.score_page(
        "<html><body><figure><img alt='table'></figure></body></html>", [gold_table(X1)]
    )
    assert got is not None and got["gold"] == 1 and got["found"] == 0 and got["predicted"] == 0


def test_a_formula_outside_a_cell_does_not_count():
    got = tm.score_page(page("text", outside=f"<p>{X1}</p>"), [gold_table(X1)])
    assert got is not None and got["predicted"] == 0 and got["found"] == 0


def test_a_formula_that_is_close_is_found_but_not_exact():
    def long(last: str) -> str:
        return math("".join(f"<mi>{c}</mi><mo>+</mo>" for c in "abcdefghij") + f"<mi>{last}</mi>")

    got = tm.score_page(page(long("k")), [gold_table(long("z"))])
    assert got is not None and got["found"] == 1 and got["found_exact_symbols"] == 0


def test_pairs_are_one_to_one():
    got = tm.score_page(page(X1), [gold_table(X1, X1)])
    assert got is not None and got["gold"] == 2 and got["found"] == 1


def test_no_gold_formulas_in_tables_means_no_score():
    assert tm.score_page(page(X1), [gold_table("plain text")]) is None
    assert tm.score_page(page(X1), [gold_table(X1, layout=True)]) is None
    assert tm.score_page(page(X1), [{"kind": "equation", "html": X1}]) is None


def test_summary_adds_pages_up():
    a = tm.score_page(page(X1), [gold_table(X1, Y2)])
    b = tm.score_page(page(X1), [gold_table(X1)])
    assert a is not None and b is not None
    total = tm.summarize([a, b])
    assert total["gold"] == 3 and total["found"] == 2 and total["pages"] == 2
    assert total["found_rate"] == 2 / 3
