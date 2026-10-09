"""pipeline.table_html: accessible <table> markup from a TableGrid (W3C WAI tables tutorial).

Covers the patterns the tutorial names (one header row, two-level headers, row headers,
irregular headers with row groups, multi-level headers with headers/id), maths cells,
escaping of untrusted text and MathML, best-effort output for broken grids (always a
rectangular table), and axe-core on the result in Chromium (browser marker).
"""

from __future__ import annotations

import random
import re
from html.parser import HTMLParser

import pytest
from latex2mathml.converter import convert

from a11y_stem.core.types import TableCell, TableGrid, grid_problems
from a11y_stem.pipeline import html_checks
from a11y_stem.pipeline.table_html import needs_header_ids, render_table


def c(row: int, col: int, text: str = "", **kw) -> TableCell:
    return TableCell(row=row, col=col, text=text, **kw)


def simple_grid(caption: str | None = "Table 1 Free fall.") -> TableGrid:
    return TableGrid(
        rows=3,
        cols=2,
        cells=[
            c(0, 0, "Time (s)", header="col"),
            c(0, 1, "Speed (m/s)", header="col"),
            c(1, 0, "0"),
            c(1, 1, "0.0"),
            c(2, 0, "1"),
            c(2, 1, "9.8"),
        ],
        header_rows=1,
        caption=caption,
    )


def two_level_grid() -> TableGrid:
    """The tutorial's two-tier headers: groups over sub-columns, an empty corner."""
    return TableGrid(
        rows=4,
        cols=5,
        cells=[
            c(0, 0, row_span=2),
            c(0, 1, "Mars", header="col", col_span=2),
            c(0, 3, "Venus", header="col", col_span=2),
            c(1, 1, "Produced", header="col"),
            c(1, 2, "Sold", header="col"),
            c(1, 3, "Produced", header="col"),
            c(1, 4, "Sold", header="col"),
            c(2, 0, "Teddy bears", header="row"),
            c(2, 1, "50,000"),
            c(2, 2, "30,000"),
            c(2, 3, "100,000"),
            c(2, 4, "80,000"),
            c(3, 0, "Board games", header="row"),
            c(3, 1, "10,000"),
            c(3, 2, "5,000"),
            c(3, 3, "12,000"),
            c(3, 4, "9,000"),
        ],
        header_rows=2,
        header_cols=1,
        caption="Table 2 Toy sales.",
    )


def row_header_grid() -> TableGrid:
    """Column headers and row headers (the tutorial's "two headers")."""
    return TableGrid(
        rows=3,
        cols=3,
        cells=[
            c(0, 0),
            c(0, 1, "Mass (kg)", header="col"),
            c(0, 2, "Speed (m/s)", header="col"),
            c(1, 0, "Cart A", header="row"),
            c(1, 1, "2"),
            c(1, 2, "3"),
            c(2, 0, "Cart B", header="row"),
            c(2, 1, "4"),
            c(2, 2, "1.5"),
        ],
        header_rows=1,
        header_cols=1,
    )


def label_value_grid() -> TableGrid:
    """Row headers only, no header row: a label/value table."""
    return TableGrid(
        rows=3,
        cols=2,
        cells=[
            c(0, 0, "Mass", header="row"),
            c(0, 1, "2 kg"),
            c(1, 0, "Speed", header="row"),
            c(1, 1, "3 m/s"),
            c(2, 0, "Energy", header="row"),
            c(2, 1, "9 J"),
        ],
        header_cols=1,
    )


def spans_grid() -> TableGrid:
    """A row header spanning two rows, and a data cell spanning two columns."""
    return TableGrid(
        rows=4,
        cols=3,
        cells=[
            c(0, 0, "Planet", header="col"),
            c(0, 1, "Quantity", header="col"),
            c(0, 2, "Value", header="col"),
            c(1, 0, "Earth", header="row", row_span=2),
            c(1, 1, "g", header="row"),
            c(1, 2, "9.8 m/s\u00b2"),
            c(2, 1, "Radius", header="row"),
            c(2, 2, "6371 km"),
            c(3, 0, "Unknown", header="row"),
            c(3, 1, "not measured", col_span=2),
        ],
        header_rows=1,
        header_cols=2,
    )


def maths_cell(row: int, col: int, text: str, latex: str) -> TableCell:
    return c(row, col, text, latex=latex, mathml=convert(latex))


def maths_grid() -> TableGrid:
    return TableGrid(
        rows=3,
        cols=2,
        cells=[
            c(0, 0, "Quantity", header="col"),
            c(0, 1, "Formula", header="col"),
            c(1, 0, "Kinetic energy", header="row"),
            maths_cell(1, 1, "KE = 1/2 mv2", r"\frac{1}{2}mv^2"),
            c(2, 0, "Momentum", header="row"),
            maths_cell(2, 1, "p = mv", r"\mathbf{p}=m\mathbf{v}"),
        ],
        header_rows=1,
        header_cols=1,
    )


def side_by_side_grid() -> TableGrid:
    """Two label/value tables next to each other: the second labels sit after data."""
    return TableGrid(
        rows=3,
        cols=4,
        cells=[
            c(0, 0, "Quantity", header="col"),
            c(0, 1, "Value", header="col"),
            c(0, 2, "Quantity", header="col"),
            c(0, 3, "Value", header="col"),
            c(1, 0, "Mass", header="row"),
            c(1, 1, "2 kg"),
            c(1, 2, "Speed", header="row"),
            c(1, 3, "3 m/s"),
            c(2, 0, "Force", header="row"),
            c(2, 1, "6 N"),
            c(2, 2, "Time", header="row"),
            c(2, 3, "2 s"),
        ],
        header_rows=1,
        header_cols=1,
    )


def sections_grid() -> TableGrid:
    """Section headings across the full width split the body (irregular headers)."""
    return TableGrid(
        rows=5,
        cols=2,
        cells=[
            c(0, 0, "Quantity", header="col"),
            c(0, 1, "Unit", header="col"),
            c(1, 0, "Base units", header="row", col_span=2),
            c(2, 0, "length"),
            c(2, 1, "m"),
            c(3, 0, "Derived units", header="row", col_span=2),
            c(4, 0, "force"),
            c(4, 1, "N"),
        ],
        header_rows=1,
    )


def mismatched_grid() -> TableGrid:
    """Two header rows whose spans do not nest: the second-level header straddles the
    first level's groups, so scope cannot say which group a column belongs to."""
    return TableGrid(
        rows=3,
        cols=4,
        cells=[
            c(0, 0, "Before", header="col", col_span=2),
            c(0, 2, "After", header="col", col_span=2),
            c(1, 0, "x", header="col"),
            c(1, 1, "y (both)", header="col", col_span=2),
            c(1, 3, "z", header="col"),
            c(2, 0, "1"),
            c(2, 1, "2"),
            c(2, 2, "3"),
            c(2, 3, "4"),
        ],
        header_rows=2,
    )


ALL_GRIDS = {
    "simple": simple_grid,
    "two_level": two_level_grid,
    "row_headers": row_header_grid,
    "label_value": label_value_grid,
    "spans": spans_grid,
    "maths": maths_grid,
    "side_by_side": side_by_side_grid,
    "sections": sections_grid,
    "mismatched": mismatched_grid,
}


# --------------------------------------------------------------------------- a minimal HTML model


class _Table(HTMLParser):
    """Rows of (tag, attrs, text) per row group, as a browser's table model sees them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.groups: list[tuple[str, list[list[dict]]]] = []
        self.caption: str | None = None
        self.colgroups: list[int] = []
        self.tags: list[str] = []
        self._cell: dict | None = None
        self._in_caption = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attrs = dict(attrs)
        if tag in ("thead", "tbody"):
            self.groups.append((tag, []))
        elif tag == "tr":
            if not self.groups:
                self.groups.append(("tbody", []))
            self.groups[-1][1].append([])
        elif tag in ("td", "th"):
            self._cell = {"tag": tag, "attrs": attrs, "text": ""}
            self.groups[-1][1][-1].append(self._cell)
        elif tag == "caption":
            self._in_caption, self.caption = True, ""
        elif tag == "colgroup":
            self.colgroups.append(int(attrs.get("span") or 1))

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._cell = None
        elif tag == "caption":
            self._in_caption = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data
        elif self._in_caption:
            self.caption = (self.caption or "") + data

    def slots(self) -> list[list[dict]]:
        """The grid every row group makes (rowspans stay inside their group); asserts no
        slot is covered twice and every row has the same width."""
        grid: list[list[dict]] = []
        for _, rows in self.groups:
            taken: dict[tuple[int, int], dict] = {}
            for r, row in enumerate(rows):
                col = 0
                for cell in row:
                    while (r, col) in taken:
                        col += 1
                    rowspan = int(cell["attrs"].get("rowspan", 1))
                    colspan = int(cell["attrs"].get("colspan", 1))
                    assert r + rowspan <= len(rows), "rowspan leaves its row group"
                    for dr in range(rowspan):
                        for dc in range(colspan):
                            assert (r + dr, col + dc) not in taken, "slot covered twice"
                            taken[(r + dr, col + dc)] = cell
                    col += colspan
            width = max((cc for _, cc in taken), default=-1) + 1
            for r in range(len(rows)):
                grid.append([taken.get((r, cc), {}) for cc in range(width)])
        return grid


def parse(html: str) -> _Table:
    table = _Table()
    table.feed(html)
    return table


def cells_named(table: _Table) -> dict[str, dict]:
    return {cell["text"]: cell for _, rows in table.groups for row in rows for cell in row}


def assert_rectangular(html: str, rows: int, cols: int) -> None:
    grid = parse(html).slots()
    assert len(grid) == rows
    assert all(len(row) == cols and all(row) for row in grid), grid


# --------------------------------------------------------------------------- the patterns


def test_one_header_row_is_a_thead_with_column_scopes():
    html = render_table(simple_grid(), id_prefix="p1-e3")
    assert html.startswith('<table><caption id="p1-e3-caption">Table 1 Free fall.</caption>')
    assert '<thead><tr><th scope="col">Time (s)</th><th scope="col">Speed (m/s)</th>' in html
    assert "<tbody><tr><td>0</td><td>0.0</td></tr>" in html
    assert "headers=" not in html and " id=" not in html.removeprefix("<table><caption id")
    assert_rectangular(html, 3, 2)


def test_no_caption_is_invented():
    html = render_table(simple_grid(caption=None), id_prefix="p1-e3")
    assert "<caption" not in html
    assert "<caption" not in render_table(simple_grid(caption="  "), id_prefix="p1-e3")


def test_two_level_headers_use_column_groups():
    html = render_table(two_level_grid(), id_prefix="p1-e3")
    table = parse(html)
    assert table.colgroups == [1, 2, 2]
    named = cells_named(table)
    assert named["Mars"]["attrs"] == {"scope": "colgroup", "colspan": "2"}
    assert named["Produced"]["attrs"] == {"scope": "col"}
    assert named["Teddy bears"]["attrs"] == {"scope": "row"}
    assert '<td rowspan="2"></td>' in html  # the empty corner is not a header
    assert [tag for tag, _ in table.groups] == ["thead", "tbody"]
    assert len(table.groups[0][1]) == 2  # both header rows in <thead>
    assert not needs_header_ids(two_level_grid())
    assert_rectangular(html, 4, 5)


def test_row_headers_have_row_scope():
    html = render_table(row_header_grid(), id_prefix="t1")
    named = cells_named(parse(html))
    assert named["Cart A"]["tag"] == "th" and named["Cart A"]["attrs"] == {"scope": "row"}
    assert named["Mass (kg)"]["attrs"] == {"scope": "col"}
    assert named["2"]["tag"] == "td"
    assert html.count("<td></td>") == 1  # the corner
    assert_rectangular(html, 3, 3)


def test_a_label_value_table_has_no_thead():
    html = render_table(label_value_grid(), id_prefix="t1")
    assert "<thead>" not in html
    assert html.count('<th scope="row">') == 3 and html.count("<td>") == 3
    assert_rectangular(html, 3, 2)


def test_spans_are_written_and_cover_the_grid():
    html = render_table(spans_grid(), id_prefix="t1")
    named = cells_named(parse(html))
    assert named["Earth"]["attrs"] == {"scope": "row", "rowspan": "2"}
    assert named["not measured"]["attrs"] == {"colspan": "2"}
    assert "9.8 m/s\u00b2" in html
    assert not needs_header_ids(spans_grid())
    assert_rectangular(html, 4, 3)


def test_maths_cells_are_sanitised_mathml():
    html = render_table(maths_grid(), id_prefix="t1")
    assert html.count('<math display="inline">') == 2
    assert "<mfrac><mrow><mn>1</mn></mrow><mrow><mn>2</mn></mrow></mfrac>" in html
    assert "<mi>\U0001d429</mi>" in html  # bold p, as latex2mathml writes it
    assert "KE = 1/2 mv2" not in html  # the text layer is only the fallback
    block = render_table(maths_grid(), id_prefix="t1", math_display="block")
    assert block.count('<math display="block">') == 2


def test_a_maths_cell_whose_mathml_cannot_be_shown_falls_back_to_its_text():
    grid = TableGrid(
        rows=1,
        cols=3,
        cells=[
            c(0, 0, "a", mathml="<math><mi>x</mi><script>alert(1)</script></math>"),
            c(0, 1, "b", mathml="<b>bold</b>"),
            c(0, 2, "c", mathml="<math><mi>"),
        ],
    )
    html = render_table(grid, id_prefix="t1")
    assert html == "<table><tbody><tr><td>a</td><td>b</td><td>c</td></tr></tbody></table>"


def test_mathml_attributes_outside_the_allowlist_are_dropped():
    mathml = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi href="javascript:alert(1)" '
        'onclick="alert(1)" style="color:red" mathvariant="bold">v</mi></math>'
    )
    html = render_table(TableGrid(rows=1, cols=1, cells=[c(0, 0, mathml=mathml)]), id_prefix="t")
    assert '<mi mathvariant="bold">v</mi>' in html
    for bad in ("href", "onclick", "style", "javascript"):
        assert bad not in html


@pytest.mark.parametrize(
    "text",
    [
        "<script>alert(1)</script>",
        '"><img src=x onerror=alert(1)>',
        "a & b < c > d",
        "<math><mi>x</mi></math>",
        "\x00\x08control",
    ],
)
def test_cell_and_caption_text_is_escaped(text):
    grid = TableGrid(
        rows=2,
        cols=1,
        cells=[c(0, 0, text, header="col"), c(1, 0, text)],
        header_rows=1,
        caption=text,
    )
    html = render_table(grid, id_prefix="t1")
    assert "<script" not in html and "<img" not in html and "<mi>" not in html
    assert "\x00" not in html and "\x08" not in html
    table = parse(html)
    expected = " ".join(text.replace("\x00", "").replace("\x08", "").split())
    assert table.caption == expected
    assert [cell["text"] for _, rows in table.groups for row in rows for cell in row] == [
        text.replace("\x00", "").replace("\x08", "")
    ] * 2


@pytest.mark.parametrize("prefix", ["", "1abc", 'p1" onclick="x', "p1 e3", "<p>", "x" * 65])
def test_the_id_prefix_must_be_a_plain_id(prefix):
    with pytest.raises(ValueError, match="id_prefix"):
        render_table(simple_grid(), id_prefix=prefix)


# --------------------------------------------------------------------------- irregular tables


def test_sections_split_the_body_with_rowgroup_headers():
    html = render_table(sections_grid(), id_prefix="t1")
    table = parse(html)
    assert [tag for tag, _ in table.groups] == ["thead", "tbody", "tbody"]
    named = cells_named(table)
    assert named["Base units"]["attrs"] == {"scope": "rowgroup", "colspan": "2"}
    assert not needs_header_ids(sections_grid())
    assert "headers=" not in html
    assert_rectangular(html, 5, 2)


def test_a_row_header_after_data_cells_needs_headers_and_ids():
    grid = side_by_side_grid()
    assert needs_header_ids(grid)
    html = render_table(grid, id_prefix="p2-e5")
    named = cells_named(parse(html))
    assert named["Speed"]["attrs"] == {"id": "p2-e5-r1c2", "scope": "row"}
    # Each value gets the nearest column header above and row header to its left only.
    assert named["3 m/s"]["attrs"]["headers"] == "p2-e5-r0c3 p2-e5-r1c2"
    assert named["2 kg"]["attrs"]["headers"] == "p2-e5-r0c1 p2-e5-r1c0"
    assert named["2 s"]["attrs"]["headers"] == "p2-e5-r0c3 p2-e5-r2c2"
    assert_rectangular(html, 3, 4)


def test_mismatched_multi_level_headers_need_headers_and_ids():
    grid = mismatched_grid()
    assert grid_problems(grid) == []  # valid, just irregular
    assert needs_header_ids(grid)
    html = render_table(grid, id_prefix="t9")
    named = cells_named(parse(html))
    assert named["2"]["attrs"]["headers"] == "t9-r0c0 t9-r1c1"
    assert named["3"]["attrs"]["headers"] == "t9-r0c2 t9-r1c1"
    assert named["4"]["attrs"]["headers"] == "t9-r0c2 t9-r1c3"
    assert_rectangular(html, 3, 4)


def test_headers_below_the_header_rows_start_a_new_block():
    """A repeated header row inside the body: the rows under it belong to it alone."""
    grid = TableGrid(
        rows=5,
        cols=2,
        cells=[
            c(0, 0, "t (s)", header="col"),
            c(0, 1, "x (m)", header="col"),
            c(1, 0, "0"),
            c(1, 1, "zero"),
            c(2, 0, "t (min)", header="col"),
            c(2, 1, "x (km)", header="col"),
            c(3, 0, "1"),
            c(3, 1, "2"),
            c(4, 0, ""),
            c(4, 1, "5"),
        ],
        header_rows=1,
    )
    assert grid_problems(grid) == ["header_below_body"]
    assert needs_header_ids(grid)
    named = cells_named(parse(render_table(grid, id_prefix="t1")))
    assert named["0"]["attrs"]["headers"] == "t1-r0c0"
    assert named["zero"]["attrs"]["headers"] == "t1-r0c1"
    assert named["2"]["attrs"]["headers"] == "t1-r2c1"
    assert named["5"]["attrs"]["headers"] == "t1-r2c1"
    assert "headers" not in named[""]["attrs"]  # an empty cell needs no headers


def test_sections_in_an_irregular_table_are_listed_first():
    grid = TableGrid(
        rows=4,
        cols=4,
        cells=[
            c(0, 0, "Group A", header="row", col_span=4),
            c(1, 0, "Mass", header="row"),
            c(1, 1, "2 kg"),
            c(1, 2, "Speed", header="row"),
            c(1, 3, "3 m/s"),
            c(2, 0, "Group B", header="row", col_span=4),
            c(3, 0, "Mass", header="row"),
            c(3, 1, "5 kg"),
            c(3, 2, "Speed", header="row"),
            c(3, 3, "1 m/s"),
        ],
    )
    html = render_table(grid, id_prefix="t1")
    named = cells_named(parse(html))
    assert named["5 kg"]["attrs"]["headers"] == "t1-r2c0 t1-r3c0"
    assert named["1 m/s"]["attrs"]["headers"] == "t1-r2c0 t1-r3c2"
    assert named["Group B"]["attrs"] == {"id": "t1-r2c0", "scope": "rowgroup", "colspan": "4"}


# --------------------------------------------------------------------------- broken grids


@pytest.mark.parametrize(
    "grid",
    [
        TableGrid(rows=2, cols=2),  # no cells at all
        TableGrid(rows=2, cols=2, cells=[c(0, 0, "a", row_span=9, col_span=9)]),
        TableGrid(rows=2, cols=2, cells=[c(0, 0, "a", col_span=2), c(0, 1, "lost")]),
        TableGrid(rows=2, cols=3, cells=[c(1, 1, "lonely")]),
        TableGrid(rows=3, cols=1, cells=[c(5, 5, "far")]),
        TableGrid(rows=1, cols=2, cells=[c(0, 0, "h", header="col"), c(0, 1, "d")], header_rows=1),
        TableGrid(
            rows=3,
            cols=2,
            cells=[c(0, 0, "h", header="col", row_span=3), c(0, 1, "h2", header="col")],
            header_rows=1,
        ),
        TableGrid(
            rows=3,
            cols=2,
            cells=[c(0, 0, "a", row_span=2), c(1, 0, "Section", header="row", col_span=2)],
        ),
    ],
)
def test_broken_grids_still_render_a_rectangular_table(grid):
    assert grid_problems(grid)
    html = render_table(grid, id_prefix="t1")
    assert_rectangular(html, grid.rows, grid.cols)


def test_random_grids_always_render_a_rectangular_table():
    rng = random.Random(20261009)
    for _ in range(300):
        rows, cols = rng.randint(1, 6), rng.randint(1, 6)
        cells = [
            c(
                rng.randint(0, rows),
                rng.randint(0, cols),
                rng.choice(["", "x", "1.5", "<b>"]),
                row_span=rng.randint(1, 3),
                col_span=rng.randint(1, 3),
                header=rng.choice([None, None, "col", "row"]),
            )
            for _ in range(rng.randint(0, rows * cols + 2))
        ]
        grid = TableGrid(
            rows=rows,
            cols=cols,
            cells=cells,
            header_rows=rng.randint(0, rows),
            header_cols=rng.randint(0, cols),
        )
        html = render_table(grid, id_prefix="t1")
        assert_rectangular(html, rows, cols)
        ids = re.findall(r' id="([^"]+)"', html)
        assert len(ids) == len(set(ids))
        for refs in re.findall(r' headers="([^"]+)"', html):
            assert set(refs.split()) <= set(ids)


def test_a_layout_table_is_presentational():
    grid = label_value_grid().model_copy(update={"is_data": False, "caption": "Table 3 x"})
    html = render_table(grid, id_prefix="t1")
    assert html.startswith('<table role="presentation"><tr>')
    assert "<th" not in html and "<caption" not in html and "scope" not in html


# --------------------------------------------------------------------------- browser


def page(tables: list[str]) -> str:
    body = "\n".join(f"<h2>Example {i}</h2>\n{t}" for i, t in enumerate(tables, start=1))
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        "<title>Tables</title></head><body><main><h1>Tables</h1>\n"
        f"{body}\n</main></body></html>"
    )


@pytest.mark.browser
def test_axe_finds_no_problems_in_any_pattern():
    pytest.importorskip("playwright.sync_api")
    tables = [render_table(make(), id_prefix=f"t-{name}") for name, make in ALL_GRIDS.items()]
    checks = html_checks.axe_checks(page(tables))
    if checks == [html_checks.AXE_SKIPPED]:
        pytest.skip("Chromium for Playwright is not installed")
    assert checks == [html_checks.Check(name="axe", passed=True)]
