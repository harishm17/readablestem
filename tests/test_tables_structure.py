"""pipeline.tables, structure fixes (docs/tables.md, "Known misses"): the rest of an unruled
table on the next page, a sparse table with text in one column, two tables in one box,
booktabs columns without the model file, and maths in cells.

Pages are generated with PyMuPDF, like tests/test_tables_detect.py.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Kind
from readablestem.pipeline import tables
from readablestem.pipeline.layout import analyze
from test_tables_detect import no_model, pdf, write  # noqa: F401

# ------------------------------------------------- the rest of an unruled table, next page


UNRULED_X = [72, 140, 310, 380]
UNRULED_HEAD = ["Length (m)", "Phenomenon", "Mass (kg)", "Phenomenon"]


def unruled_row(page: pymupdf.Page, y: float, cells: list[str]) -> None:
    for x, text in zip(UNRULED_X, cells, strict=True):
        if text:
            write(page, x, y, text)


def unruled_first_page(page: pymupdf.Page) -> None:
    write(page, 72, 650, "Table 1.3 Orders of magnitude")
    unruled_row(page, 680, UNRULED_HEAD)
    unruled_row(page, 700, ["10^-9", "Diameter of a proton", "10^-30", "Mass of an electron"])
    unruled_row(page, 720, ["10^-2", "Size of a bacterium", "10^-3", "Mass of a mosquito"])
    last = ["10^26", "Distance to the edge of the known", "10^53"]
    unruled_row(page, 740, [*last, "Mass of known universe (current upper"])


def table_texts(element) -> list[list[str]]:
    rows: dict[int, list[str]] = {}
    for c in sorted(element.table.cells, key=lambda c: (c.row, c.col)):
        rows.setdefault(c.row, []).append(c.text)
    return list(rows.values())


def test_the_split_row_of_an_unruled_table_is_joined_across_the_page_break():
    def second(page):
        unruled_row(page, 90, UNRULED_HEAD)  # the header repeated
        unruled_row(page, 110, ["", "universe", "", "limit)"])
        write(page, 72, 170, "The next paragraph goes on with the discussion of the values above.")

    result = analyze(pdf(unruled_first_page, second))
    found = [e for e in result.elements if e.kind == Kind.TABLE]
    assert [e.page for e in found] == [0]  # nothing is left of the second page's part
    assert table_texts(found[0])[-1] == [
        "10^26", "Distance to the edge of the known universe", "10^53",
        "Mass of known universe (current upper limit)",
    ]  # fmt: skip
    assert tables.REASON_ROW_SPLIT in found[0].reasons
    paragraphs = [e.text for e in result.elements if e.page == 1 and e.kind == Kind.PARAGRAPH]
    assert paragraphs == ["The next paragraph goes on with the discussion of the values above."]


def test_more_rows_after_the_repeated_header_continue_the_unruled_table():
    def second(page):
        unruled_row(page, 90, UNRULED_HEAD)
        unruled_row(page, 110, ["", "universe", "", "limit)"])
        unruled_row(
            page, 130, ["10^27", "Radius of the observable universe", "10^55", "Mass of a galaxy"]
        )

    result = analyze(pdf(unruled_first_page, second))
    found = [e for e in result.elements if e.kind == Kind.TABLE]
    assert [e.page for e in found] == [0, 1]
    assert tables.REASON_CONTINUED in found[1].reasons
    assert table_texts(found[1]) == [
        UNRULED_HEAD,
        ["10^27", "Radius of the observable universe", "10^55", "Mass of a galaxy"],
    ]


def test_a_line_that_only_looks_like_the_header_is_not_a_continued_table():
    def second(page):
        unruled_row(page, 90, ["Length (m)", "Phenomenon", "Mass (kg)", "Notes"])  # not repeated
        unruled_row(page, 110, ["", "universe", "", "limit)"])

    result = analyze(pdf(unruled_first_page, second))
    assert [e.page for e in result.elements if e.kind == Kind.TABLE] == [0]
    assert not any(tables.REASON_ROW_SPLIT in e.reasons for e in result.elements)


def test_a_repeated_header_under_text_in_the_page_is_not_a_continued_table():
    def second(page):
        write(page, 72, 100, "Some text that comes before anything else on this page.")
        unruled_row(page, 140, UNRULED_HEAD)
        unruled_row(page, 160, ["", "universe", "", "limit)"])

    result = analyze(pdf(unruled_first_page, second))
    assert [e.page for e in result.elements if e.kind == Kind.TABLE] == [0]


# ------------------------------------------------------ a sparse table, text in one column


def rule(page: pymupdf.Page, y: float, width: float = 0.8) -> None:
    page.draw_line((72, y), (480, y), width=width)


def sparse_page(body_x: float, items: list[str], *, caption: bool = False):
    def fill(page):
        if caption:
            write(page, 72, 90, "Table 2 Planned exercises")
        rule(page, 100, 1.2)
        for x, text in zip([80, 220, 360], ["Exercise", "Hours", "Notes"], strict=True):
            write(page, x, 116, text)
        rule(page, 124)
        for i, text in enumerate(items):
            write(page, body_x, 142 + 18 * i, text)
        rule(page, 142 + 18 * (len(items) - 1) + 10, 1.2)
        write(page, 72, 300, "The exercises above are done in order.")

    return fill


ITEMS = ["Measure the period", "Plot the data", "Fit a line", "Report the result"]


@pytest.fixture
def no_detector(monkeypatch):
    """The layout model proposes nothing: the page's own rules are the only evidence."""
    monkeypatch.setattr(tables, "propose", lambda page, budget=None: [])


def grid_rows(element) -> list[list[str]]:
    return table_texts(element)


@pytest.mark.parametrize(("body_x", "filled"), [(80, 0), (220, 1), (360, 2)])
def test_a_booktabs_table_with_text_in_one_column_is_kept(no_detector, body_x, filled):
    result = analyze(pdf(sparse_page(body_x, ITEMS)))
    found = [e for e in result.elements if e.kind == Kind.TABLE]
    assert len(found) == 1
    rows = grid_rows(found[0])
    assert rows[0] == ["Exercise", "Hours", "Notes"]
    assert [row[filled] for row in rows[1:]] == ITEMS
    assert found[0].table.header_rows == 1
    assert [e.text for e in result.elements if e.kind == Kind.PARAGRAPH] == [
        "The exercises above are done in order."
    ]


def test_a_sparse_table_under_a_caption_is_kept_too(no_detector):
    result = analyze(pdf(sparse_page(80, ITEMS, caption=True)))
    found = [e for e in result.elements if e.kind == Kind.TABLE]
    assert [e.caption for e in found] == ["Table 2 Planned exercises"]


def test_a_framed_note_with_a_title_and_text_is_not_a_sparse_table(no_detector):
    def note(page):
        rule(page, 100, 1.2)
        write(page, 80, 116, "Note")
        rule(page, 124)
        write(page, 80, 142, "Keep the cart on the track while it rolls down the ramp so")
        write(page, 80, 160, "that the photogate sees it pass at its full speed each time.")
        rule(page, 172, 1.2)

    result = analyze(pdf(note))
    assert not [e for e in result.elements if e.kind == Kind.TABLE]


def test_body_text_that_matches_no_header_column_is_not_a_sparse_table(no_detector):
    def loose(page):
        rule(page, 100, 1.2)
        for x, text in zip([80, 220, 360], ["Exercise", "Hours", "Notes"], strict=True):
            write(page, x, 116, text)
        rule(page, 124)
        for i, text in enumerate(ITEMS):
            write(page, 150, 142 + 18 * i, text)  # under no header cell
        rule(page, 142 + 18 * 3 + 10, 1.2)

    result = analyze(pdf(loose))
    assert not [e for e in result.elements if e.kind == Kind.TABLE]


# ------------------------------------------------------------ two tables in one box

PENDULUM_ROWS = [
    ["Length", "Time", "Period"],
    ["0.20", "8.98", "0.898"],
    ["0.40", "12.69", "1.269"],
]
SPRING_ROWS = [["Mass", "Stretch"], ["0.10", "2.1"], ["0.20", "4.0"]]


def lines_at(page: pymupdf.Page, y: float, rows: list[list[str]], xs: tuple[int, ...]) -> float:
    for row in rows:
        for x, text in zip(xs, row, strict=False):
            write(page, x, y, text)
        y += 18
    return y


def stacked(first, second, *, xs_first=(80, 220, 360), xs_second=(80, 150), gap=46.0):
    def fill(page):
        y = lines_at(page, 100, first, xs_first)
        y = lines_at(page, y - 18 + gap, second, xs_second)
        write(page, 72, y + 40, "The tables above show the two experiments.")

    return fill


def found_tables(data: bytes):
    return [e for e in analyze(data).elements if e.kind == Kind.TABLE]


def test_two_unruled_tables_in_one_box_with_other_columns_are_split():
    found = found_tables(pdf(stacked(PENDULUM_ROWS, SPRING_ROWS)))
    assert [table_texts(e) for e in found] == [PENDULUM_ROWS, SPRING_ROWS]


def test_two_tables_with_the_same_columns_are_split_when_each_has_a_header_row():
    second = [["Mass", "Stretch", "Constant"], ["0.10", "2.1", "46.7"], ["0.20", "4.0", "49.0"]]
    found = found_tables(pdf(stacked(PENDULUM_ROWS, second, xs_second=(80, 220, 360))))
    assert [table_texts(e) for e in found] == [PENDULUM_ROWS, second]


def test_a_table_with_a_wide_gap_between_its_rows_stays_one_table():
    rest = [["0.60", "15.55", "1.555"], ["0.80", "17.94", "1.794"], ["1.00", "20.06", "2.006"]]
    found = found_tables(pdf(stacked(PENDULUM_ROWS, rest, xs_second=(80, 220, 360))))
    assert len(found) == 1
    assert table_texts(found[0]) == [*PENDULUM_ROWS, *rest]


def test_a_table_caption_in_the_middle_of_a_box_separates_two_tables():
    # Close together, so the detector boxes them as one; the "Table 2" line is the separator.
    def fill(page):
        y = lines_at(page, 100, PENDULUM_ROWS, (80, 220, 360))
        write(page, 72, y, "Table 2 Springs")
        lines_at(page, y + 18, SPRING_ROWS, (80, 220))

    found = found_tables(pdf(fill))
    assert len(found) == 2
    assert found[1].caption == "Table 2 Springs"


# --------------------------------------------- booktabs columns without the model file

LATEX_TABLES = Path(__file__).parent / "fixtures" / "layout" / "latex" / "tables.pdf"


def test_a_booktabs_cell_with_a_gap_in_it_is_not_split_into_a_column_without_the_model():
    # "p = mv" has a wide gap after the "=": PyMuPDF's grid from the rules between rows
    # made a fourth column of it. The glyphs fit three columns better.
    found = found_tables(LATEX_TABLES.read_bytes())
    table = next(e for e in found if any("Momentum" in c.text for c in e.table.cells))
    assert table.table.cols == 3
    rows = table_texts(table)
    assert rows[1] == ["Momentum", "p⃗", "p = mv⃗"]
    assert rows[2][:2] == ["Kinetic energy", "K"]


# ------------------------------------------ a new caption over the same header is a new table


def test_a_new_caption_above_a_repeated_header_in_the_top_band_is_not_a_continuation():
    def second(page):
        write(page, 72, 40, "Table 1.4 Other lengths")  # inside the top band
        unruled_row(page, 90, UNRULED_HEAD)
        unruled_row(page, 110, ["", "universe", "", "limit)"])
        write(page, 72, 170, "The next paragraph goes on with the discussion of the values.")

    result = analyze(pdf(unruled_first_page, second))
    assert not any(tables.REASON_ROW_SPLIT in e.reasons for e in result.elements)
    assert any(e.text and e.text.startswith("Table 1.4") for e in result.elements)


def test_a_continued_caption_does_not_stop_the_continuation():
    def second(page):
        write(page, 72, 40, "Table 1.3 (continued)")
        unruled_row(page, 90, UNRULED_HEAD)
        unruled_row(page, 110, ["", "universe", "", "limit)"])
        write(page, 72, 170, "The next paragraph goes on with the discussion of the values.")

    found = found_tables(pdf(unruled_first_page, second))
    assert tables.REASON_ROW_SPLIT in found[0].reasons


def test_the_same_columns_under_a_new_caption_are_not_marked_continued():
    from test_tables_detect import PENDULUM, grid_table

    def first(page):
        write(page, 72, 100, "Some text.")
        grid_table(page, PENDULUM, y0=600)

    def second(page):
        write(page, 100, 60, "Table 4 Other pendulum")
        grid_table(page, [["0.80", "17.94", "1.794"], ["1.00", "20.06", "2.006"]], y0=72)

    found = found_tables(pdf(first, second))
    assert [e.page for e in found] == [0, 1]
    assert tables.REASON_CONTINUED not in found[1].reasons
