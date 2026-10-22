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
from readablestem.pipeline import table_model, tables
from readablestem.pipeline.layout import analyze, release
from test_tables_detect import PROSE, grid_table, no_model, pdf, write  # noqa: F401

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
    ["add_coloring", "Lets the user change the coloring of the plot; it takes one value for every point"],
    ["draw_networkx", "Used for the plotting of the graph, for the final summary page and the legend"],
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

    labelled = [
        (page, t["bbox"]) for t in real_labels()[file]["tables"] if t["page"] == page
    ]
    return eval_tables.score_real_document(found_on(file, page), labelled)


# (file, page of the file): the 11 tables the first measurement missed, and the one
# that was found, as the control that fixes cannot cost a table that was there.
MISSED = [
    ("2401.00649.pdf", 1),  # a list of acronyms, ruled above and below
    ("2412.19752.pdf", 1),  # three notation lists, unruled
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


@pytest.mark.parametrize(("file", "page"), FOUND)
def test_the_real_tables_it_found_are_still_found(file, page):
    result = scored(file, page)
    assert result["full"] == result["labelled"] > 0, result
    assert result["true"] == result["predicted"], result
