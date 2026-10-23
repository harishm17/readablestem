"""pipeline.tables, the second round on real documents (docs/tables.md, "Second held-out
measurement"): charts with a few bars, lists of relations, and what the residuals of the
first round were. Pages are generated with PyMuPDF or are extracts of the CC BY sets in
data/real-tables and data/real-tables-heldout (both development sets from this round on).
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Kind
from readablestem.eval import tables as eval_tables
from readablestem.pipeline import tables
from readablestem.pipeline.layout import analyze
from test_tables_detect import PENDULUM, grid_table, no_model, pdf, view_of, write  # noqa: F401

DATA = Path(__file__).parent.parent / "data"
DEV = DATA / "real-tables"
HELD = DATA / "real-tables-heldout"


def data_tables(data: bytes):
    return [
        e
        for e in analyze(data).elements
        if e.kind == Kind.TABLE and (e.table is None or e.table.is_data)
    ]


def one_page(directory: Path, file: str, page: int) -> bytes:
    with pymupdf.open(directory / file) as source:
        one = pymupdf.open()
        one.insert_pdf(source, from_page=page, to_page=page)
        return one.tobytes()


def scored(directory: Path, file: str, page: int) -> dict:
    labels = {
        d["file"]: d for d in json.loads((directory / "labels.json").read_text())["documents"]
    }
    labelled = [(page, t["bbox"]) for t in labels[file]["tables"] if t["page"] == page]
    found = [(page, e.bbox) for e in data_tables(one_page(directory, file, page))]
    return eval_tables.score_real_document(found, labelled)


# ----------------------------------------------------------- bar charts of a few bars


def test_a_bar_chart_of_a_few_bars_is_not_a_table():
    # Two bar charts drawn by pgfplots (a series of bars is one drawing of `re` items, ten
    # and six bars; the file's pages 0 and 1): the layout model took their axes and tick
    # labels for a ruled grid. Page 1 also holds a table, which must stay.
    assert data_tables(one_page(HELD, "2312.15334.pdf", 0)) == []
    result = scored(HELD, "2312.15334.pdf", 1)
    assert result["full"] == result["labelled"] == 1, result
    assert result["true"] == result["predicted"], result


def bars(page, heights, *, x0=120.0, base=300.0, width=22.0, gap=10.0, lying=False):
    """Filled bars standing on `base` (or lying on a vertical axis at `x0`), as one drawing
    the way pgfplots and matplotlib's PDF backend write a series; with axes and ticks."""
    shape = page.new_shape()
    for k, h in enumerate(heights):
        if lying:
            shape.draw_rect(
                pymupdf.Rect(x0, 150 + k * (width + gap), x0 + h, 150 + k * (width + gap) + width)
            )
        else:
            left = x0 + k * (width + gap)
            shape.draw_rect(pymupdf.Rect(left, base - h, left + width, base))
    shape.finish(color=(0, 0, 1), fill=(0.7, 0.7, 1))
    shape.commit()
    page.draw_line((x0 - 5, base), (x0 + 300, base), width=0.5)
    page.draw_line((x0 - 5, 140), (x0 - 5, base), width=0.5)
    for tick in range(4):
        write(page, x0 - 30, base - tick * 40 + 3, str(tick * 20), 8)


@pytest.mark.parametrize("lying", [False, True])
def test_bars_standing_on_a_line_are_a_chart(lying):
    def fill(page):
        bars(page, [30, 80, 55, 110, 20, 70], lying=lying)

    view = view_of(pdf(fill))
    box = (90.0, 130.0, 430.0, 330.0)
    assert tables._pictured(view, box, 8.0, strict=False)


def test_two_series_of_bars_in_two_drawings_are_a_chart():
    def fill(page):
        bars(page, [30, 80, 55, 110, 20], x0=120.0, width=10.0, gap=22.0)
        bars(page, [50, 40, 90, 60, 100], x0=131.0, width=10.0, gap=22.0)

    assert tables._pictured(view_of(pdf(fill)), (90.0, 130.0, 430.0, 330.0), 8.0, strict=False)


def test_three_bars_or_bars_of_one_length_are_not_called_a_chart():
    def three(page):
        bars(page, [30, 80, 55])

    def stripes(page):
        bars(page, [60, 60, 60, 60, 60, 60])

    for fill in (three, stripes):
        assert not tables._pictured(
            view_of(pdf(fill)), (90.0, 130.0, 430.0, 330.0), 8.0, strict=False
        )


def test_a_table_with_shaded_rows_and_cells_is_not_a_chart():
    rows = [["Length", "Time", "Period"], *([f"{k}.0", "8.98", "0.898"] for k in range(1, 8))]

    def fill(page):
        grid_table(page, rows, x0=100.0, y0=150.0, height=20.0)
        shape = page.new_shape()
        for k in range(1, len(rows), 2):  # zebra stripes, drawn as one drawing, text on top
            shape.draw_rect(pymupdf.Rect(100, 150 + 20 * k, 370, 170 + 20 * k))
        shape.finish(fill=(0.9, 0.9, 0.9), color=None)
        shape.commit()
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                write(page, 105 + 90 * c, 164 + 20 * r, text)

    data = pdf(fill)
    assert not tables._pictured(view_of(data), (100.0, 150.0, 370.0, 310.0), 11.0, strict=False)
    assert len(data_tables(data)) == 1


# ------------------------------------------------- lists of relations: "left side  if condition"

CONDITIONS = [
    ("X ~ f(n)", "if the ratio of X to f(n) tends to one"),
    ("X = o(f(n))", "if the ratio of X to f(n) tends to zero"),
    ("X = O(f(n))", "if the ratio of X to f(n) is bounded"),
]
SYSTEM = [("a = b + c", "(1)"), ("d = e + f", "(2)"), ("g = h + k", "(3)")]


def two_columns(page, rows, *, y0=200.0, pitch=18.0, x0=90.0, x1=230.0):
    write(page, 72, y0 - 30, "We write these relations as follows, with the usual meaning of each:")
    for k, (left, right) in enumerate(rows):
        write(page, x0, y0 + k * pitch, left)
        write(page, x1, y0 + k * pitch, right)
    write(
        page, 72, y0 + len(rows) * pitch + 30, "A paragraph follows, across the width of the page."
    )


def test_three_lines_that_end_in_a_condition_are_a_list_of_definitions():
    found = data_tables(pdf(lambda page: two_columns(page, CONDITIONS)))
    assert len(found) == 1
    assert found[0].bbox[1] > 150 and found[0].bbox[3] < 290  # the list, not its paragraphs


def test_the_same_lines_without_a_condition_are_a_system_of_equations():
    assert data_tables(pdf(lambda page: two_columns(page, SYSTEM))) == []
    plain = [(left, "with the ratio of X to f(n) tending to a limit") for left, _ in CONDITIONS]
    assert data_tables(pdf(lambda page: two_columns(page, plain))) == []


def test_two_lines_that_end_in_a_condition_are_not_enough():
    # A piecewise definition has two, often three, cases; one list needs three lines.
    assert data_tables(pdf(lambda page: two_columns(page, CONDITIONS[:2]))) == []


# --------------------------------------- two rules and a caption: a table whose columns are ragged


def booktabs_numbers(page, *, caption: bool):
    """Header and five rows of numbers set ragged (centred on no column) between a top and
    a bottom rule, with "Table 1: ..." under it: no row rule between the rows."""
    page.draw_line((90, 200), (500, 200), width=0.8)
    page.draw_line((90, 320), (500, 320), width=0.8)
    for c, head in enumerate(["Runs", "N", "nu", "Re", "kmax", "Nreal"]):
        write(page, 96 + 66 * c, 214, head)
    for r in range(5):
        for c in range(6):
            jitter = ((r * 7 + c * 3) % 5) * 3.0  # ragged: no common left or right edge
            write(
                page,
                96 + 66 * c + jitter,
                234 + 17 * r,
                f"{(r + 2) * (c + 3) / 7:.{1 + (r + c) % 3}f}",
            )
    if caption:
        write(page, 90, 340, "Table 1: Numerical settings for the runs described in the text.")


def test_two_rules_round_ragged_columns_under_a_caption_are_a_table():
    assert len(data_tables(pdf(lambda page: booktabs_numbers(page, caption=True)))) == 1


def test_the_same_rules_and_numbers_without_a_caption_are_left_alone():
    # Ragged numbers between two rules are as likely a drawn figure's data: the caption is
    # what this rule adds.
    assert data_tables(pdf(lambda page: booktabs_numbers(page, caption=False))) == []


# ------------------------------------------ rules between groups of rows: one table, not its top


def test_a_table_with_groups_of_rows_between_far_apart_rules_is_found_whole():
    # APS style: a double rule above and below, a rule under each group's title; the groups
    # are 90 and 155 pt tall, so `_stack` (rules within three lines) stopped after the first
    # two, and the detector's box with it: half of the table stayed in the text.
    result = scored(HELD, "2302.07336.pdf", 1)
    assert result["labelled"] == result["full"] == 1, result
    assert result["true"] == result["predicted"] == 1, result


def test_a_paragraph_between_two_ruled_tables_is_not_joined_to_the_first():
    def fill(page):
        write(page, 100, 140, "Table 1. Pendulum measurements")
        grid_table(page, [["Length", "Ten swings", "Period"], *PENDULUM[1:]], y0=150)
        write(page, 72, 260, "The values agree within one per cent.")
        grid_table(page, [["Trial", "Length (m)", "Time (s)"]], y0=300, blank_rows=4)

    found = data_tables(pdf(fill))
    assert [round(e.bbox[1]) for e in found] == [150, 300]


# ------------------------------- a bold "term, description" list (LaTeX `description`): a list

GLOSSARY = [
    ("Precision", "the share of what the detector calls a table that is one, as a fraction"),
    ("Recall", "the share of the tables in a document that the detector finds, in full"),
    ("Caption", "a line that starts with the word Table and a number, just above or below"),
    ("Gutter", "a strip of white space that no word crosses, between two columns of a table"),
    ("Booktabs", "a style of rules: a thick one above, a thin one under the header, one below"),
]


def glossary(page, *, bold: bool, caption: bool):
    if caption:
        write(page, 90, 170, "Table 3: Terms used in this chapter and what they mean.")
    for k, (term, text) in enumerate(GLOSSARY):
        y = 200 + 20 * k
        page.insert_text((90, y), term, fontsize=11, fontname="hebo" if bold else "helv")
        write(page, 170, y, text, 9)
    write(page, 72, 330, "A paragraph follows, set across the whole width of the page.")


def test_a_bold_term_list_stays_a_list_unless_a_caption_calls_it_a_table():
    # No instance of one is among the real documents of either development set (the guard
    # fired on none of 181 papers' pages), so this pins the behaviour the guard was written for: the
    # terms of a `description` list are bold, and that is all that tells it from a notation
    # `tabular`, which is set in plain type and is a table.
    assert data_tables(pdf(lambda p: glossary(p, bold=True, caption=False))) == []
    assert len(data_tables(pdf(lambda p: glossary(p, bold=True, caption=True)))) == 1
    assert len(data_tables(pdf(lambda p: glossary(p, bold=False, caption=False)))) == 1


# ------------------------------------- a grid of single digits under a "Table N" line is a table


def binary_array(page, caption: str | None, *, below: bool = False):
    if caption:
        write(page, 200, 300 if below else 170, caption)
    rows = [
        "0 1 1 0 1 0",
        "1 1 0 1 0 0",
        "1 0 1 0 0 1",
        "0 1 0 0 1 1",
        "1 0 0 1 1 0",
        "0 0 1 1 0 1",
    ]
    for r, row in enumerate(rows):
        for c, digit in enumerate(row.split()):
            write(page, 230 + 22 * c, 200 + 16 * r, digit)
    write(page, 72, 330, "A paragraph follows, set across the whole width of the page.")


def test_a_grid_of_single_digits_is_a_table_under_a_table_caption_not_under_a_figure_caption():
    assert (
        len(
            data_tables(
                pdf(lambda p: binary_array(p, "Table 3: An orthogonal array of strength 2."))
            )
        )
        == 1
    )
    assert (
        data_tables(pdf(lambda p: binary_array(p, "Figure 3: The same digits as a drawing."))) == []
    )
    assert data_tables(pdf(lambda p: binary_array(p, None))) == []
    # A line under a matrix in the text is the caption of the table that follows it.
    assert data_tables(pdf(lambda p: binary_array(p, "Table 4: A code.", below=True))) == []


def test_a_piecewise_definition_with_a_braced_set_of_cases_is_not_a_table():
    # `sgn(x) = { -1 if x < 0, 0 if x = 0, 1 if x > 0 }`: three lines that end in a condition,
    # as the lists of definitions do, but set in a brace built from glyph pieces.
    fixture = Path(__file__).parent / "fixtures" / "layout" / "latex" / "tables.pdf"
    assert data_tables(one_page(fixture.parent, fixture.name, 3)) == []
