"""pipeline.tables, the second round on real documents (docs/tables.md, "Second held-out
measurement"): charts with a few bars, lists of relations, and what the residuals of the
first round were. Pages are generated with PyMuPDF or are extracts of the CC BY sets in
data/real-tables and data/real-tables-heldout (both development sets from this round on).
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from readablestem.core.types import Kind
from readablestem.eval import tables as eval_tables
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


# ------------------------------------------------- lists of relations: "left side  if condition"

SYSTEM = [("a = b + c", "(1)"), ("d = e + f", "(2)"), ("g = h + k", "(3)")]


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


# ------------------------------------------ rules between groups of rows: one table, not its top


def test_a_table_with_groups_of_rows_between_far_apart_rules_is_found_whole():
    # APS style: a double rule above and below, a rule under each group's title; the groups
    # are 90 and 155 pt tall, so `_stack` (rules within three lines) stopped after the first
    # two, and the detector's box with it: half of the table stayed in the text.
    result = scored(HELD, "2302.07336.pdf", 1)
    assert result["labelled"] == result["full"] == 1, result
    assert result["true"] == result["predicted"] == 1, result


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


# ------------------------------------- a grid of single digits under a "Table N" line is a table


def test_a_piecewise_definition_with_a_braced_set_of_cases_is_not_a_table():
    # `sgn(x) = { -1 if x < 0, 0 if x = 0, 1 if x > 0 }`: three lines that end in a condition,
    # as the lists of definitions do, but set in a brace built from glyph pieces.
    fixture = Path(__file__).parent / "fixtures" / "layout" / "latex" / "tables.pdf"
    assert data_tables(one_page(fixture.parent, fixture.name, 3)) == []
