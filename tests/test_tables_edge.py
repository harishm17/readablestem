"""Text the table's box leaves out (docs/tables.md, "Text at the edge of a table").

Detection puts the PDF's own words in the cells and the table step checks the model's answer
against them, so a word the box left out (the second line of a wrapped cell, under a box that
ended a line early) is in neither: the grid and the model agree and nothing asks a person. The
edge check reads what is *outside* the box: a line of the table's own size, right under it, that
starts where a column starts, is flagged (`REASON_EDGE_TEXT`, then the required check
`table.edge_text`, code `text_at_edge`); an ordinary paragraph below, a heading, a caption and a
title above are not.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import ConversionOptions, Kind, Status
from readablestem.pipeline import options, tables
from readablestem.pipeline.layout import analyze
from test_steps_table import (
    CAPTION,
    FREE_FALL,
    answer,
    by_model,
    codes,
    element,
    free_fall,
    local,
    run,
)
from test_tables_detect import BODY, grid_table, no_model, pdf, write  # noqa: F401

ROWS = [
    ["Quantity", "Value", "Unit"],
    ["Pressure", "101", "kPa"],
    ["Temperature", "298", "K"],
]


def table_of(fill) -> object:
    found = [e for e in analyze(pdf(fill)).elements if e.kind == Kind.TABLE]
    assert len(found) == 1
    return found[0]


def under(text: str, *, gap: float, x: float = 105.0, size: float = BODY):
    """A page with the table and `text` set `gap` points under its box."""

    def fill(page: pymupdf.Page) -> None:
        write(page, 72, 100, "The measurements are in the table below.")
        box = grid_table(page, ROWS, y0=130)
        write(page, x, box[3] + gap + size * 0.8, text, size=size)

    return fill


def test_a_line_of_the_tables_size_right_under_it_in_a_column_is_flagged():
    table = table_of(under("(at sea level)", gap=2.0))
    assert tables.REASON_EDGE_TEXT in table.reasons


def test_the_wrapped_last_line_of_a_cell_left_out_of_the_box_is_flagged():
    # The box ends a line early: the row's second line starts where the first column does.
    table = table_of(under("Celsius", gap=1.0, x=195.0))
    assert tables.REASON_EDGE_TEXT in table.reasons


@pytest.mark.parametrize(
    "fill",
    [
        # an ordinary paragraph a line's gap and more below
        under("The values agree within one per cent.", gap=26.0, x=72.0),
        # a heading below (another size), even close
        under("Results", gap=2.0, size=16.0),
        # a paragraph that follows the table closely, from the first column's left edge across
        under("Read the procedure before the lab session and record every measurement.", gap=3.0),
        # text under it that starts nowhere a column starts
        under("a note", gap=2.0, x=330.0),
    ],
    ids=["paragraph", "heading", "close-paragraph", "off-column"],
)
def test_text_that_is_not_a_cut_line_is_not_flagged(fill):
    assert tables.REASON_EDGE_TEXT not in table_of(fill).reasons


def test_a_clean_table_with_a_caption_above_and_text_after_is_not_flagged():
    def fill(page: pymupdf.Page) -> None:
        write(page, 72, 100, "The measurements are in the table below.")
        write(page, 100, 140, "Table 1. Pendulum measurements")
        box = grid_table(page, ROWS, y0=150)
        write(page, 72, box[3] + 30, "The values agree within one per cent.")

    table = table_of(fill)
    assert table.caption == "Table 1. Pendulum measurements"
    assert tables.REASON_EDGE_TEXT not in table.reasons


def test_a_caption_under_the_table_is_its_caption_not_edge_text():
    def fill(page: pymupdf.Page) -> None:
        write(page, 72, 100, "The measurements are in the table below.")
        box = grid_table(page, ROWS, y0=130)
        write(page, 100, box[3] + 12, "Table 2: Record your data here.")

    table = table_of(fill)
    assert table.caption == "Table 2: Record your data here."
    assert tables.REASON_EDGE_TEXT not in table.reasons


def test_a_title_above_the_table_is_not_flagged():
    def fill(page: pymupdf.Page) -> None:
        box = grid_table(page, ROWS, y0=150)
        write(page, 105, box[1] - 4, "Quantities measured")

    assert tables.REASON_EDGE_TEXT not in table_of(fill).reasons


# ------------------------------------------------------------------------- the step


@pytest.fixture
def _tables_on():
    with options.using(ConversionOptions(tables=True)):
        yield


def test_the_step_asks_a_person_when_text_was_left_at_the_edge(_tables_on):
    target = element(local(FREE_FALL)).model_copy(update={"reasons": [tables.REASON_EDGE_TEXT]})
    client = by_model(cheap=free_fall(), main=free_fall())
    fix = run(client, target)
    assert fix.status == Status.NEEDS_REVIEW and fix.review == "required"
    assert codes(fix) == {"text_at_edge"}
    assert fix.table is not None  # the proposal is kept for the person to compare
    # a second reading cannot see what the crop does not show: no escalation
    assert [r.model for r in client.requests] == [client.requests[0].model]


def test_the_same_table_without_the_reason_is_accepted(_tables_on):
    fix = run(by_model(cheap=free_fall()), element(local(FREE_FALL)))
    assert (fix.status, fix.review) == (Status.ACCEPTED, "none")
    assert "table.edge_text" not in [c.name for c in fix.checks]


def test_the_check_is_named_in_the_checks_of_a_flagged_table(_tables_on):
    target = element(local(FREE_FALL), caption=CAPTION).model_copy(
        update={"reasons": [tables.REASON_EDGE_TEXT]}
    )
    fix = run(by_model(cheap=free_fall()), target)
    assert [c.name for c in fix.checks if not c.passed] == ["table.edge_text"]
    assert answer(FREE_FALL) is not None
