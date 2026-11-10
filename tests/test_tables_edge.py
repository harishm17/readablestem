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
    FREE_FALL,
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


def test_a_clean_table_with_a_caption_above_and_text_after_is_not_flagged():
    def fill(page: pymupdf.Page) -> None:
        write(page, 72, 100, "The measurements are in the table below.")
        write(page, 100, 140, "Table 1. Pendulum measurements")
        box = grid_table(page, ROWS, y0=150)
        write(page, 72, box[3] + 30, "The values agree within one per cent.")

    table = table_of(fill)
    assert table.caption == "Table 1. Pendulum measurements"
    assert tables.REASON_EDGE_TEXT not in table.reasons


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
