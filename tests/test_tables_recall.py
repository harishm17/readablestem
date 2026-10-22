"""pipeline.tables, recall on real documents (docs/tables.md, "Recall on real documents"):
what a detector tuned on lab manuals missed in lecture notes, papers and tutorials.

Pages are generated with PyMuPDF, like tests/test_tables_detect.py; the real-document
pages are extracts of the CC BY set in data/real-tables.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Kind
from readablestem.pipeline import tables
from readablestem.pipeline.layout import analyze
from test_tables_detect import PENDULUM, PROSE, grid_table, no_model, pdf, write  # noqa: F401

REAL = Path(__file__).parent.parent / "data" / "real-tables"


def data_tables(data: bytes):
    return [
        e
        for e in analyze(data).elements
        if e.kind == Kind.TABLE and (e.table is None or e.table.is_data)
    ]


# ----------------------------------------------------------- a caption makes a grid data

ELEMENTS = [
    ["Element", "Description"],
    [
        "add_coloring",
        "Lets the user change the coloring of the plot; it takes one value for every point",
    ],
    [
        "draw_networkx",
        "Used for the plotting of the graph, for the final summary page and the legend",
    ],
    ["eps", "The ball radius used in the construction of the plot, of its edges and its balls"],
]


def test_a_captioned_ruled_grid_of_prose_is_a_table_but_not_without_its_caption():
    # A term and its description set out in a ruled box is layout (`is_layout_grid`) unless
    # the document calls it a table: "Table 2: Elements of an object" over the same grid.
    def captioned(page):
        write(page, 100, 120, "Table 2: Elements of an object")
        grid_table(page, ELEMENTS, x0=40, y0=130, widths=[100.0, 430.0], height=18)

    def bare(page):
        grid_table(page, ELEMENTS, x0=40, y0=130, widths=[100.0, 430.0], height=18)

    found = data_tables(pdf(captioned, width=612))
    assert [e.caption for e in found] == ["Table 2: Elements of an object"]
    assert data_tables(pdf(bare)) == []


# ------------------------------------------------- the real-document set, table by table


def real_labels() -> dict[str, dict]:
    import json

    return {d["file"]: d for d in json.loads((REAL / "labels.json").read_text())["documents"]}


def found_on(file: str, page: int) -> list[tuple[int, tuple[float, float, float, float]]]:
    """The data tables of one page of a data/real-tables PDF, as (page, box) pairs."""
    with pymupdf.open(REAL / file) as source:
        one = pymupdf.open()
        one.insert_pdf(source, from_page=page, to_page=page)
        data = one.tobytes()
    return [(page, e.bbox) for e in data_tables(data)]


def scored(file: str, page: int) -> dict:
    from readablestem.eval import tables as eval_tables

    labelled = [(page, t["bbox"]) for t in real_labels()[file]["tables"] if t["page"] == page]
    return eval_tables.score_real_document(found_on(file, page), labelled)


# (file, page of the file): the 11 tables the first measurement missed, and the one
# that was found, as the control that fixes cannot cost a table that was there.
MISSED = [
    ("2401.00649.pdf", 1),  # a list of acronyms, ruled above and below
    ("2412.19752.pdf", 2),  # two notation lists in one detector box
    ("2505.03022.pdf", 17),  # two ruled columns of long text under "Table 2:"
    ("2409.08224.pdf", 4),  # ruled above and below, long wrapped text: fragments
    ("2409.08224.pdf", 6),  # the same, justified text with narrow gutters
    ("2505.03022.pdf", 24),  # a table typeset sideways on an upright page
]
FOUND = [("2401.00649.pdf", 3), ("2409.08224.pdf", 9), ("2407.13734.pdf", 1)]


@pytest.mark.parametrize(("file", "page"), MISSED)
def test_the_real_tables_the_first_measurement_missed_are_found(file, page):
    result = scored(file, page)
    assert result["full"] == result["labelled"] > 0, result
    assert result["true"] == result["predicted"], result  # and nothing else on the page


def test_the_notation_lists_with_words_in_them_are_found_the_ones_of_relations_are_not():
    # Four unruled lists on a page: abbreviations and sets with their meanings, two of
    # relations (`X_n ~ f(n)  if X_n / f(n) -> 1`). The second pair is lines of symbols in
    # two aligned columns, which is also how a displayed system of equations looks, and
    # the page guard (`_text_block_dropped`) leaves them as text.
    result = scored("2412.19752.pdf", 1)
    assert result["labelled"] == 4 and result["full"] == 2, result
    assert result["true"] == result["predicted"] == 2, result


@pytest.mark.parametrize(("file", "page"), FOUND)
def test_the_real_tables_it_found_are_still_found(file, page):
    result = scored(file, page)
    assert result["full"] == result["labelled"] > 0, result
    assert result["true"] == result["predicted"], result


# ------------------------------------------- text set in columns, with no rules and no proposal


def rows_of(element) -> list[list[str]]:
    grid = element.table
    out: list[list[str]] = [[""] * grid.cols for _ in range(grid.rows)]
    for cell in grid.cells:
        out[cell.row][cell.col] = cell.text
    return out


NOTATION = [
    ("e.g.", "for example (exempli gratia)"),
    ("a.s.", "almost surely"),
    ("i.o.", "infinitely often"),
    ("#E", "cardinality of the set E"),
    ("N(m, s2)", "real Gaussian law with mean m and variance s2"),
]


def notation_list(page, *, y0=200.0, pitch=17.0, x0=90.0, x1=170.0, rows=NOTATION):
    for k, (left, right) in enumerate(rows):
        write(page, x0, y0 + k * pitch, left)
        write(page, x1, y0 + k * pitch, right)


def test_an_unruled_list_of_symbols_and_their_meanings_is_a_table():
    # The notation list of a set of lecture notes: a `tabular` with no rules, which the
    # layout model never proposes (it looks for ruled, shaded or header-marked grids).
    def fill(page):
        write(page, 72, 150, "We list here the notation we use through the lecture notes:")
        notation_list(page)
        write(page, 72, 330, "A paragraph follows, set across the whole width of the page.")

    found = data_tables(pdf(fill))
    assert len(found) == 1
    assert rows_of(found[0])[0] == ["e.g.", "for example (exempli gratia)"]
    assert len(rows_of(found[0])) == len(NOTATION)
    box = found[0].bbox
    assert box[1] < 192 and box[3] > 270 and box[3] < 300  # the list, not its paragraphs


def test_a_wrapped_line_of_the_second_column_stays_in_its_row():
    rows = [*NOTATION[:3], ("", "in particular this is a Poisson variable"), *NOTATION[3:]]

    def fill(page):
        notation_list(page, rows=rows, pitch=15.0)

    found = data_tables(pdf(fill))
    assert len(found) == 1 and len(rows_of(found[0])) == len(rows) - 1
    assert rows_of(found[0])[2][1] == "infinitely often in particular this is a Poisson variable"


COLUMN_PROSE = (
    "The cart rolls down the ramp and the photogate records the time it takes to pass, "
    "so that each group has a list of times for every release height they try. After "
    "the first session the groups compare their times with the ones the model predicts "
    "and they write down how large the difference is and what could have caused it. "
    "Some groups noticed that the wheels slipped on the ramp and repeated the whole run, "
    "which gave them times that agreed with the model much better than the first set."
)


def two_text_columns(page):
    import textwrap

    for x in (72.0, 320.0):
        lines = textwrap.wrap(COLUMN_PROSE, 44)
        for k, text in enumerate(lines):
            page.insert_text((x, 120 + 13 * k), text, fontsize=10)


def test_two_columns_of_running_text_are_not_a_table():
    assert data_tables(pdf(two_text_columns)) == []


def test_numbered_equations_a_bibliography_and_algorithm_lines_are_not_tables():
    def equations(page):
        for k, eq in enumerate(["f(x) = a x + b", "g(x) = c x^2 + d", "h(x) = e x^3 + f"]):
            write(page, 150, 200 + 24 * k, eq)
            write(page, 520, 200 + 24 * k, f"({k + 1}.{k + 2})")

    def references(page):
        for k, ref in enumerate(
            [
                "A. Author, A first paper about a subject, Journal of Things 4 (2010).",
                "B. Writer, A second paper about another subject, Annals of Stuff 9 (2012).",
                "C. Person, A third paper on the same subject, Review of Matters 2 (2015).",
            ]
        ):
            write(page, 72, 200 + 24 * k, f"[{k + 1}]")
            write(page, 110, 200 + 24 * k, ref)

    def algorithm(page):
        for k, step in enumerate(["initialise the table", "for each row do", "stop when full"]):
            write(page, 72, 200 + 14 * k, f"{k + 1}:")
            write(page, 100, 200 + 14 * k, step)

    assert data_tables(pdf(equations, references, algorithm)) == []


def test_a_block_of_bare_numbers_is_a_matrix_not_a_table():
    def matrix(page):
        for r, row in enumerate([("1", "0", "3"), ("4", "5", "6"), ("7", "8", "9")]):
            for c, value in enumerate(row):
                write(page, 150 + 40 * c, 200 + 18 * r, value)

    assert data_tables(pdf(matrix)) == []


def test_an_unruled_table_with_a_header_of_words_over_numbers_is_a_table():
    def fill(page):
        for c, label in enumerate(["Height (m)", "Time (s)", "Speed (m/s)"]):
            write(page, 100 + 110 * c, 200, label)
        for r in range(1, 5):
            for c, value in enumerate([0.25 * r, 0.31 * r, 1.97 * r]):
                write(page, 100 + 110 * c, 200 + 16 * r, f"{value:.2f}")

    found = data_tables(pdf(fill))
    assert len(found) == 1 and len(rows_of(found[0])) == 5


# ------------------------------------------------------------ a table set sideways on a page


def test_a_table_set_sideways_on_an_upright_page_is_read_upright():
    # A `sidewaystable` printed without /Rotate: every line of the page is vertical, so the
    # detector, the rows and the columns all saw nothing (the page was never looked at).
    from readablestem.pipeline import layout

    with pymupdf.open(REAL / "2505.03022.pdf") as source:
        one = pymupdf.open()
        one.insert_pdf(source, from_page=24, to_page=24)
        data = one.tobytes()
    assert one[0].rotation == 0
    [table] = data_tables(data)
    grid = table.table
    assert grid.turned == 90 and grid.caption == "Table 4: Summary of Balls"
    first = {c.text for c in grid.cells if c.row == 0}
    assert {"Dataset", "Ball", "Variable: X1", "Obs"} <= first
    assert "0.772" in {c.text for c in grid.cells}
    # The box is in the page's own frame (tall and narrow), the picture is turned upright.
    x0, y0, x1, y1 = table.bbox
    assert y1 - y0 > 2 * (x1 - x0)
    png = layout.crop(data, table)
    pixmap = pymupdf.Pixmap(png)
    assert pixmap.width > 2 * pixmap.height
    layout.release(data)


def test_a_few_sideways_labels_do_not_turn_a_page():
    def fill(page):
        write(page, 72, 150, "An ordinary page of text with a table below it and a label beside.")
        grid_table(page, PENDULUM)
        page.insert_text((40, 400), "a figure label set sideways", fontsize=10, rotate=90)

    found = data_tables(pdf(fill))
    assert len(found) == 1 and found[0].table.turned == 0


# ------------------------------------------------- a numbered reaction above a table


def test_a_numbered_reaction_above_a_table_is_not_part_of_it():
    # OpenStax sets "2NO + Cl2 -> 2NOCl   17.39" on the line above the data of a rate-law
    # table, and the layout model boxes the two together; the reaction (an equation) is not
    # a row.
    def fill(page):
        write(page, 150, 135, "2NO(g) + Cl2(g) = 2NOCl(g)")
        write(page, 500, 135, "17.39")
        grid_table(page, PENDULUM, y0=150)

    [found] = data_tables(pdf(fill))
    assert found.bbox[1] > 140, found.bbox
    assert "2NOCl" not in {c.text for c in found.table.cells}


def test_the_box_is_cut_below_a_numbered_equation_at_its_top_or_bottom():
    from test_tables_detect import view_of

    def fill(page):
        write(page, 150, 135, "2NO(g) + Cl2(g) = 2NOCl(g)")
        write(page, 500, 135, "17.39")
        grid_table(page, PENDULUM, y0=150)
        write(page, 150, 262, "x = 2y + 1")
        write(page, 500, 262, "(4.2)")

    view = view_of(pdf(fill))
    _, y0, _, y1 = tables._without_captions(view, (90, 120, 520, 270))
    assert 138 < y0 < 152 and 228 < y1 < 252
