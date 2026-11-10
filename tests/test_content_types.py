"""The content-feature data model (core/types.py, pipeline 0.4.0): every new field has a
default so stored results load, the validators refuse what must never reach the output,
and `grid_problems` reports structural problems as content-free codes."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from readablestem.core import triage
from readablestem.core.types import (
    GRID_PROBLEMS,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    ColourRun,
    DocumentResult,
    Element,
    FigureFix,
    Fix,
    Kind,
    Link,
    NotationEntry,
    Notice,
    SourceFacts,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    grid_problems,
    inline_marker,
    is_safe_href,
)

FIXTURE = Path(__file__).parent / "fixtures" / "assemble" / "sample_document.json"


def cell(row: int, col: int, text: str = "x", **kw) -> TableCell:
    return TableCell(row=row, col=col, text=text, **kw)


def full_grid(rows: int, cols: int, *, header_rows: int = 0, header_cols: int = 0) -> TableGrid:
    """Every slot one cell; the first rows/columns flagged as headers."""
    cells = []
    for r in range(rows):
        for c in range(cols):
            header = "col" if r < header_rows else ("row" if c < header_cols else None)
            cells.append(cell(r, c, f"r{r}c{c}", header=header))
    return TableGrid(
        rows=rows, cols=cols, cells=cells, header_rows=header_rows, header_cols=header_cols
    )


# --------------------------------------------------------------------------- stored results


def test_a_result_stored_before_the_content_features_loads_with_defaults():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stored = {
        "pipeline_version": "0.3.0",
        "title": data["title"],
        "lang": data["lang"],
        "elements": data["elements"],
        "fixes": data["fixes"],
        "stats": {"pages": 2, "cost_usd": 0.0, "latency_ms": 0, "model_calls": 0},
    }
    result = DocumentResult.model_validate(stored)
    assert result.notices == [] and result.source_facts is None
    assert result.lang_source == "default"
    for element in result.elements:
        assert element.table is None and element.links == [] and element.colour_runs == []
        assert element.lang is None
    for fix in result.fixes:
        assert fix.table is None
        if fix.figure is not None:
            assert fix.figure.long_description is None and fix.figure.data_table is None
            assert fix.figure.data_values_from_text is None and fix.figure.colour_only is False


def test_a_result_with_every_new_field_round_trips_through_json():
    grid = TableGrid(
        rows=2,
        cols=2,
        cells=[
            cell(0, 0, "t", header="col"),
            cell(0, 1, "v", header="col"),
            cell(1, 0, "1"),
            cell(1, 1, "", latex=r"\frac{1}{2}", mathml="<math><mn>1</mn></math>"),
        ],
        header_rows=1,
        caption="Table 1 Data.",
        source="model",
    )
    text = f"See {inline_marker('p1-e2')} at https://example.org in red."
    url, red = text.index("https"), text.index("red")
    paragraph = Element(
        id="p1-e1",
        kind=Kind.PARAGRAPH,
        page=0,
        bbox=(0, 0, 1, 1),
        order=0,
        text=text,
        links=[Link(start=url, end=url + 19, kind="uri", href="https://example.org")],
        colour_runs=[ColourRun(start=red, end=red + 3, rgb=(200, 0, 0))],
        lang="fr",
    )
    table = Element(id="p1-e3", kind=Kind.TABLE, page=0, bbox=(0, 0, 1, 1), order=2, table=grid)
    result = DocumentResult(
        pipeline_version="0.4.0",
        elements=[paragraph, table],
        fixes=[
            Fix(
                element_id="p1-e3",
                kind=Kind.TABLE,
                status=Status.ACCEPTED,
                table=TableFix(grid=grid),
            ),
            Fix(
                element_id="p1-e4",
                kind=Kind.FIGURE,
                status=Status.NEEDS_REVIEW,
                figure=FigureFix(
                    figure_type="chart",
                    alt="Speed against time.",
                    long_description="Speed rises linearly.",
                    data_table=grid,
                    data_values_from_text=False,
                    colour_only=True,
                ),
            ),
        ],
        stats={"pages": 1, "cost_usd": 0.0, "latency_ms": 0, "model_calls": 0},
        notices=[Notice(code="colour_reference", page=0, element_id="p1-e1")],
        source_facts=SourceFacts(tagged=False, has_lang=True, images=3, equations_flattened=2),
        lang_source="detected",
    )
    again = DocumentResult.model_validate_json(result.model_dump_json())
    assert again == result
    assert again.elements[0].colour_runs[0].rgb == (200, 0, 0)
    assert again.fixes[0].review == "none" and again.fixes[1].review == "required"


# --------------------------------------------------------------------------- tables


def test_grids_are_bounded():
    with pytest.raises(ValidationError):
        TableGrid(rows=0, cols=1)
    with pytest.raises(ValidationError):
        TableGrid(rows=MAX_TABLE_ROWS + 1, cols=1)
    with pytest.raises(ValidationError):
        TableGrid(rows=1, cols=MAX_TABLE_COLS + 1)


def test_a_well_formed_grid_has_no_problems():
    assert grid_problems(full_grid(3, 3, header_rows=1, header_cols=1)) == []
    assert grid_problems(full_grid(4, 2, header_cols=1)) == []  # label/value, no thead
    spanning = TableGrid(
        rows=3,
        cols=3,
        cells=[
            cell(0, 0, "", row_span=2),  # empty corner over both header rows
            cell(0, 1, "Group", header="col", col_span=2),
            cell(1, 1, "a", header="col"),
            cell(1, 2, "b", header="col"),
            cell(2, 0, "row", header="row"),
            cell(2, 1, "1"),
            cell(2, 2, "2"),
        ],
        header_rows=2,
        header_cols=1,
    )
    assert grid_problems(spanning) == []


@pytest.mark.parametrize(
    ("grid", "codes"),
    [
        (TableGrid(rows=1, cols=1), ["no_cells", "grid_hole"]),
        (
            TableGrid(rows=1, cols=2, cells=[cell(0, 0, col_span=3)]),
            ["cell_out_of_bounds"],
        ),
        (
            TableGrid(rows=1, cols=2, cells=[cell(0, 0, col_span=2), cell(0, 1)]),
            ["cells_overlap"],
        ),
        (TableGrid(rows=2, cols=2, cells=[cell(0, 0), cell(0, 1), cell(1, 0)]), ["grid_hole"]),
        (
            TableGrid(rows=1, cols=2, cells=[cell(0, 0, header="col"), cell(0, 1)], header_rows=1),
            ["headers_cover_table", "data_in_header_rows"],
        ),
        (
            TableGrid(
                rows=3,
                cols=1,
                cells=[cell(0, 0, header="col", row_span=2), cell(2, 0)],
                header_rows=1,
            ),
            ["header_span_into_body"],
        ),
        (
            TableGrid(
                rows=3,
                cols=1,
                cells=[cell(0, 0, header="col"), cell(1, 0), cell(2, 0, header="col")],
                header_rows=1,
            ),
            ["header_below_body"],
        ),
        (
            TableGrid(rows=2, cols=2, cells=[cell(0, 0), cell(0, 1), cell(1, 0), cell(1, 1)],
                      header_cols=1),
            ["data_in_header_cols"],
        ),
    ],
)  # fmt: skip
def test_grid_problems_name_each_problem(grid, codes):
    assert grid_problems(grid) == codes
    assert set(codes) <= set(GRID_PROBLEMS)


def test_a_hostile_grid_is_judged_quickly():
    """Thousands of cells each spanning the whole grid: reported, not measured."""
    big = TableCell(row=0, col=0, row_span=MAX_TABLE_ROWS, col_span=MAX_TABLE_COLS)
    grid = TableGrid(rows=MAX_TABLE_ROWS, cols=MAX_TABLE_COLS, cells=[big] * 5000)
    started = time.perf_counter()
    assert "cells_overlap" in grid_problems(grid)
    assert time.perf_counter() - started < 2.0


# --------------------------------------------------------------------------- links, colour, lang


@pytest.mark.parametrize(
    "href",
    [
        "https://example.org/a?b=c#d",
        "http://example.org",
        "HTTPS://EXAMPLE.ORG/",
        "mailto:prof@example.org",
        "mailto:prof@example.org?subject=Week%201",
    ],
)
def test_web_and_email_links_are_kept(href):
    assert is_safe_href(href)
    assert Link(start=0, end=4, kind="uri", href=href).href == href


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "ftp://example.org/a",
        "//example.org/a",
        "/relative/path",
        "#p1-e3",
        "http://",
        "https:example.org",
        "mailto:",
        "https://exa mple.org",
        "https://example.org/\nnext",
        "java\tscript:alert(1)",
        "https://example.org/" + "a" * 3000,
        "",
    ],
)
def test_any_other_link_target_is_refused(href):
    assert not is_safe_href(href)
    with pytest.raises(ValidationError):
        Link(start=0, end=4, kind="uri", href=href)


def test_links_carry_exactly_one_kind_of_target():
    assert Link(start=0, end=4, kind="crossref", target_id="p2-e3").target_id == "p2-e3"
    assert Link(start=0, end=4, kind="internal", target_id="p5-e0").href is None
    with pytest.raises(ValidationError):
        Link(start=0, end=4, kind="uri")
    with pytest.raises(ValidationError):
        Link(start=0, end=4, kind="crossref")
    with pytest.raises(ValidationError):
        Link(start=0, end=4, kind="internal", target_id="p1-e1", href="https://example.org")
    with pytest.raises(ValidationError):
        Link(start=0, end=4, kind="uri", href="https://example.org", target_id="p1-e1")


@pytest.mark.parametrize("lang", ["fr", "pt-BR", "zh-Hant-TW", "de-CH-1996"])
def test_a_passage_language_is_a_language_tag(lang):
    assert Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 1, 1), order=0, lang=lang)


@pytest.mark.parametrize("lang", ["", "f", "fr fr", 'fr" onload="x', "fran\u00e7ais"])
def test_anything_else_is_not_a_language(lang):
    with pytest.raises(ValidationError):
        Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 1, 1), order=0, lang=lang)


# --------------------------------------------------------------------------- notices, facts


def test_source_facts_are_numbers_and_flags_only():
    facts = SourceFacts()
    assert facts.tagged is None and facts.images_with_alt is None
    for name, field in SourceFacts.model_fields.items():
        assert field.annotation in (bool, int, bool | None, int | None), name
    with pytest.raises(ValidationError):
        SourceFacts(images=-1)


def test_a_table_kept_as_an_image_is_one_required_decision():
    fix = Fix(element_id="p1-e3", kind=Kind.TABLE, status=Status.FAILED, reason="table_not_rebuilt")
    assert fix.review == "required" and fix.pending
    load = triage.review_load([fix])
    assert load.required_decisions == 1 and load.required_by_kind == {"table": 1}
    assert triage.tier_reasons(fix) == ["table_not_rebuilt"]


# --------------------------------------------------------------------------- notation, options


def test_notation_entries_are_short():
    entry = NotationEntry(symbol="bold d", canonical="\U0001d41d", meaning="vector")
    assert entry.source == "user" and entry.note is None
    with pytest.raises(ValidationError):
        NotationEntry(symbol="x" * 65, canonical="x", meaning="other")
    with pytest.raises(ValidationError):
        NotationEntry(symbol="x", canonical="x", meaning="other", note="n" * 201)
    with pytest.raises(ValidationError):
        NotationEntry(symbol="x", canonical="x", meaning="scalar")  # type: ignore[arg-type]
