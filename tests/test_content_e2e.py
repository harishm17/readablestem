"""End to end with every content feature on (integration of the eleven content builders).

Each LaTeX fixture in tests/fixtures/layout/latex (the tables and the links, language and
colour fixtures included) goes through `convert_pdf` with tables, long descriptions,
colour and notation memory on and a course notation list, against a fake model client:

- equations get the offline placeholder ("x", confidence 0.5);
- figures get a long-prompt answer with a long description, a chart data table read
  from the image and the colour-only flag;
- tables get the answer a perfect reader would give: the grid layout read from the PDF
  (`Element.table`), registered for the exact crop the run sends.

Then the student HTML passes the static checks, re-renders byte for byte from the stored
result (content-features rule 3), carries the new features (rebuilt tables, links and
cross-references that resolve, the passage language, colour classes, long descriptions,
bold letters made maths), lists only known notices, and (browser suite) passes axe.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from pathlib import Path

import pytest

from a11y_stem.config import Settings
from a11y_stem.core import triage
from a11y_stem.core.notation import canonical_symbol
from a11y_stem.core.types import (
    NOTICE_CODES,
    ConversionOptions,
    ConversionOutput,
    DocumentResult,
    Kind,
    NotationEntry,
    Status,
    TableGrid,
)
from a11y_stem.models import OFFLINE_DEFAULTS
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.fake import FakeVisionClient, image_key
from a11y_stem.pipeline import assemble, table_step
from a11y_stem.pipeline.html_checks import axe_checks, static_checks
from a11y_stem.pipeline.run import convert_pdf
from a11y_stem.web.messages import NOTICE_MESSAGES

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex"
FIXTURES = sorted(p.name for p in LATEX.glob("*.pdf"))
OPTIONS = ConversionOptions(
    tables=True,
    long_descriptions=True,
    colour=True,
    notation_memory=True,
    notation=[
        NotationEntry(
            symbol="bold r", canonical=canonical_symbol("bold r") or "", meaning="vector"
        ),
        NotationEntry(
            symbol="bold v", canonical=canonical_symbol("bold v") or "", meaning="vector"
        ),
    ],
)
LONG_DESCRIPTION = (
    "A line chart of distance against time. The line starts at zero on the left and "
    "rises steadily to its highest point on the right."
)


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def figure_answer(request: VisionRequest) -> dict:
    answer = {
        "figure_type": "chart",
        "decorative": False,
        "alt": "Chart of distance rising steadily with time.",
        "confidence": 0.9,
    }
    if not request.prompt_version.startswith("figure_long-"):
        return answer
    return answer | {
        "long_description": LONG_DESCRIPTION,
        "long_description_unsure": False,
        "data_table": {"header": ["Time (s)", "Distance (m)"], "rows": [["0", "0"], ["1", "5"]]},
        "values_printed": False,
        "colour_only": True,
    }


def table_answer(grid: TableGrid) -> dict:
    """What a perfect reader of the crop answers: the local grid, row by row."""
    rows = []
    for r in range(grid.rows):
        cells = sorted((c for c in grid.cells if c.row == r), key=lambda c: c.col)
        rows.append(
            {
                "cells": [
                    {
                        "text": c.text,
                        "latex": "",
                        "header": c.header or "none",
                        "row_span": c.row_span,
                        "col_span": c.col_span,
                    }
                    for c in cells
                ]
            }
        )
    return {"is_data_table": grid.is_data, "caption": grid.caption or "", "rows": rows}


def answering_tables(client: FakeVisionClient) -> Callable:
    real = table_step.rebuild

    def rebuild(element, png, **kwargs):
        if element.table is not None:
            client.by_image[("table", image_key(png))] = table_answer(element.table)
        return real(element, png, **kwargs)

    return rebuild


@cache
def converted(name: str) -> ConversionOutput:
    client = FakeVisionClient(defaults=dict(OFFLINE_DEFAULTS), responders={"figure": figure_answer})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(table_step, "rebuild", answering_tables(client))
        return convert_pdf(
            (LATEX / name).read_bytes(), settings=settings(), client=client, options=OPTIONS
        )


@pytest.fixture(autouse=True)
def _no_table_model(monkeypatch, tmp_path):
    """CI has no SLANet-plus file; the detector then uses the drawn or PyMuPDF grid."""
    monkeypatch.setenv("TABLE_MODEL_DIR", str(tmp_path))


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_converts_with_every_feature_on(name):
    output = converted(name)
    result = output.result
    html = result.html
    # The static checks of the student file: no error-level failure.
    failed = [(c.name, c.code) for c in static_checks(html) if not c.passed]
    errors = [
        (c.name, c.code) for c in static_checks(html) if not c.passed and c.severity == "error"
    ]
    assert errors == [], failed
    # Rendering reads the stored result only: the same file from the stored JSON.
    stored = DocumentResult.model_validate_json(result.model_dump_json())
    again = assemble.render_html(
        title=stored.title,
        lang=stored.lang,
        elements=stored.elements,
        fixes=stored.fixes,
        crops=output.crops,
    )
    assert again == html
    # Notices: known codes with a sentence each; they never add a decision.
    assert all(n.code in NOTICE_CODES and n.code in NOTICE_MESSAGES for n in result.notices)
    without = stored.model_copy(update={"notices": []})
    assert triage.open_decisions(without.fixes) == triage.open_decisions(stored.fixes)
    # The text pass ran: a language and where it came from; the job page's facts.
    assert result.lang and result.lang_source in {"pdf", "detected", "default"}
    assert result.source_facts is not None
    assert "table" in result.prompt_versions or not any(
        f.kind is Kind.TABLE and f.calls for f in result.fixes
    )


def test_tables_are_rebuilt_and_checked():
    result = converted("tables.pdf").result
    tables = [f for f in result.fixes if f.kind is Kind.TABLE]
    assert len(tables) >= 4
    rebuilt = [f for f in tables if f.table is not None and f.status is not Status.FAILED]
    assert rebuilt, [(f.status, f.reason) for f in tables]
    accepted = [f for f in rebuilt if f.status is Status.ACCEPTED]
    assert accepted, [(f.status, [c.code for c in f.failed_checks]) for f in tables]
    html = result.html
    assert html.count("<table") >= len(rebuilt)
    assert '<th scope="col">' in html
    assert result.source_facts is not None and result.source_facts.tables == len(tables)
    # A rebuilt table is no "table not rebuilt" notice.
    kept = {n.element_id for n in result.notices if n.code == "table_not_rebuilt"}
    assert not kept & {f.element_id for f in accepted}


def test_links_cross_references_language_and_colour_are_written():
    output = converted("links_language_colour.pdf")
    result, html = output.result, output.result.html
    assert '<a href="https://example.org/physics/syllabus">' in html
    assert '<a href="#' in html  # cross-references and internal links resolve to ids
    assert 'lang="fr"' in html and '<html lang="en">' in html
    assert 'class="tc-' in html
    links = sum(len(e.links) for e in result.elements)
    assert links and result.source_facts is not None and result.source_facts.links >= 1


def test_figures_get_long_descriptions_and_data_tables():
    result = converted("calculus_notes.pdf").result
    figures = [f for f in result.fixes if f.kind is Kind.FIGURE and f.figure is not None]
    assert figures
    figure = figures[0].figure
    assert figure is not None and figure.long_description and figure.data_table is not None
    assert figure.data_values_from_text is False
    assert figures[0].status is Status.NEEDS_REVIEW  # values read from the image
    assert 'class="figure-description"' in result.html
    assert "colour_only" in {n.code for n in result.notices}


def test_bold_letters_in_prose_become_maths_with_notation_memory():
    result = converted("linear_algebra_twocol.pdf").result
    bold = [
        e
        for e in result.elements
        if e.kind is Kind.EQUATION and "bold letter in prose" in e.reasons
    ]
    assert bold
    fixes = [result.fix_for(e.id) for e in bold]
    assert all(f is not None and f.status is Status.ACCEPTED and not f.calls for f in fixes)


@pytest.mark.browser
@pytest.mark.parametrize("name", FIXTURES)
def test_axe_finds_nothing_with_every_feature_on(name):
    checks = axe_checks(converted(name).result.html)
    if len(checks) == 1 and checks[0].code == "skipped":
        pytest.skip("axe-core needs Playwright and its Chromium")
    assert [(c.name, c.code) for c in checks if not c.passed] == []
