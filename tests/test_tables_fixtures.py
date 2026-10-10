"""Table detection on two realistic fixtures: a LaTeX handout compiled by Tectonic
(tests/fixtures/layout/latex/tables.tex: booktabs, a ruled grid, no rules, multirow and
multicolumn headers, maths in cells, a blank worksheet table, and a table of contents,
a grid of prose, matrices, an augmented array, align and cases that are not tables) and
a Word-style handout exported by LibreOffice Writer (tests/fixtures/tables/).

The SLANet-plus model file is hidden (as in CI), so grids come from the drawn rules or
PyMuPDF; `test_with_the_model_*` checks what the model adds and skips without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_stem.config import Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import Element, Kind, Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import table_model, tables
from a11y_stem.pipeline.assemble_tables import TABLE_NOT_CONVERTED_ALT
from a11y_stem.pipeline.layout import Layout, analyze, release
from a11y_stem.pipeline.run import convert_pdf

LATEX = Path(__file__).parent / "fixtures" / "layout" / "latex" / "tables.pdf"
WORD = Path(__file__).parent / "fixtures" / "tables" / "word_tables.pdf"
MODEL_PRESENT = table_model.model_path().is_file()
SUP1, SUP2 = "\u00b9", "\u00b2"  # superscript one and two


@pytest.fixture(scope="module", autouse=True)
def no_model(tmp_path_factory):
    """Module-wide, so the module's cached layouts are made without the model too."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(table_model.MODEL_DIR_ENV, str(tmp_path_factory.mktemp("models")))
        tables.reset_models()
        release()
        yield
    tables.reset_models()
    release()


@pytest.fixture(scope="module")
def latex() -> Layout:
    return analyze(LATEX.read_bytes())


@pytest.fixture(scope="module")
def word() -> Layout:
    return analyze(WORD.read_bytes())


def found(layout: Layout) -> list[Element]:
    return [e for e in layout.elements if e.kind == Kind.TABLE]


def rows(element: Element) -> list[list[str]]:
    assert element.table is not None
    out: list[list[str]] = [[] for _ in range(element.table.rows)]
    for cell in sorted(element.table.cells, key=lambda c: (c.row, c.col)):
        out[cell.row].append(cell.text)
    return out


def other_text(layout: Layout) -> str:
    return " ".join(e.text or "" for e in layout.elements if e.kind != Kind.TABLE)


# ---------------------------------------------------------------- LaTeX


def test_latex_tables_are_found_with_their_captions(latex):
    tables_ = found(latex)
    assert [(e.page, e.caption) for e in tables_] == [
        (0, "Table 1: Drop times for five heights."),
        (0, "Table 2: Frequency of quiz scores."),
        (1, "Table 4: Specific heat by phase."),
        (2, "Table 5: Mechanical quantities."),
        (2, "Table 6: Pendulum trials."),
    ]
    assert all(e.table is not None and e.table.source == "local" for e in tables_)
    assert "Table 1:" not in other_text(latex) and "Table 6:" not in other_text(latex)


def test_a_booktabs_table_of_numbers_is_a_table_not_an_equation(latex):
    booktabs = found(latex)[0]
    assert rows(booktabs) == [
        ["Height (m)", "Time (s)", "g (m/s\u00b2)"],  # the raised 2 kept
        ["0.50", "0.319", "9.83"],
        ["0.75", "0.391", "9.81"],
        ["1.00", "0.452", "9.79"],
        ["1.25", "0.505", "9.80"],
        ["1.50", "0.553", "9.81"],
    ]
    assert booktabs.table is not None and booktabs.table.header_rows == 1
    assert "0.319" not in other_text(latex)
    assert not [e for e in latex.elements if e.kind == Kind.HEADING and "Height" in (e.text or "")]


def test_a_ruled_grid_is_read_from_its_drawn_cells(latex):
    ruled = found(latex)[1]
    assert rows(ruled)[0] == ["Score", "Students", "Share"]
    assert rows(ruled)[-1] == ["9", "4", "0.20"]
    assert tables.REASON_DRAWN_CELLS in ruled.reasons


def test_a_two_row_header_with_spanning_cells(latex):
    heat = found(latex)[2]
    assert heat.table is not None and heat.table.header_rows == 2
    texts = {cell.text for cell in heat.table.cells}
    assert {"Solid", "Liquid", "Aluminium", "Ethanol", "4186"} <= texts


def test_maths_in_cells_keeps_its_symbols_with_their_letters(latex):
    quantities = found(latex)[3]
    texts = {cell.text for cell in quantities.table.cells} if quantities.table else set()
    assert "p⃗" in texts  # the arrow of \vec{p} right after its p
    assert {"Momentum", "Kinetic energy", "Power"} <= texts


def test_a_blank_worksheet_table_keeps_all_its_rows(latex):
    blank = found(latex)[4]
    assert (
        rows(blank)
        == [["Trial", "Length (m)", "Ten swings (s)", "Period (s)"]] + [["", "", "", ""]] * 4
    )
    assert Kind.FIGURE not in {e.kind for e in latex.elements if e.page == 2}


def test_latex_things_that_are_not_tables_stay_as_they_were(latex):
    assert [e.page for e in found(latex)].count(3) == 0  # prose grid, matrices, align, cases
    assert {Kind.EQUATION, Kind.PARAGRAPH} <= {e.kind for e in latex.elements if e.page == 3}
    assert "Displacement" in other_text(latex)  # the grid of prose keeps the paragraph path
    toc = [e for e in latex.elements if e.page == 0 and e.bbox[1] < 300]
    assert toc and Kind.TABLE not in {e.kind for e in toc}  # the table of contents


@pytest.mark.xfail(
    strict=True,
    reason="pymupdf-layout does not propose this all-number table set without rules; it "
    "stays a display equation (docs/tables.md, 'Known misses')",
)
def test_an_unruled_table_of_numbers(latex):
    assert any(e.page == 1 and "Table 3" in (e.caption or "") for e in found(latex))


# ---------------------------------------------------------------- Word-style


def test_word_tables_are_found_with_their_captions(word):
    measured, blank = found(word)
    assert measured.caption == "Table 1. Pendulum measurements from the demonstration"
    assert blank.caption == "Table 2. Record your data here"
    assert rows(measured)[0] == ["Length (m)", "Ten swings (s)", "Period (s)", "g (m/s\u00b2)"]
    assert rows(measured)[-1] == ["0.80", "17.94", "1.794", "9.81"]
    assert measured.table is not None and measured.table.header_rows == 1
    assert rows(blank)[0] == ["Trial", "Length (m)", "Ten swings (s)", "Period (s)"]
    assert len(rows(blank)) == 6 and all(not any(r) for r in rows(blank)[1:])


def test_word_layout_grid_form_line_and_choices_are_not_tables(word):
    assert len(found(word)) == 2
    text = other_text(word)
    for kept in ("Amplitude", "Name:", "2.54 s"):
        assert kept in text


def test_a_detected_table_is_kept_as_an_image_with_a_required_decision():
    """FEATURE_TABLES off (the default): the table is claimed from the text flow but the
    table step keeps its image, flagged, as a required decision."""
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    output = convert_pdf(
        WORD.read_bytes(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(budget=budget),
    )
    result = output.result
    fixes = [f for f in result.fixes if f.kind == Kind.TABLE]
    assert len(fixes) == 2
    assert all(
        (f.status, f.reason, f.review) == (Status.FAILED, "table_not_rebuilt", "required")
        for f in fixes
    )
    assert result.html.count(f'alt="{TABLE_NOT_CONVERTED_ALT}"') == 2
    assert "<figcaption>Table 2. Record your data here</figcaption>" in result.html
    assert "17.94" not in result.html  # the numbers are in the image, not in paragraphs


def test_flattened_maths_in_the_grid_read_from_the_pdf_never_reaches_the_student_file():
    """Table 5 "Mechanical quantities" (K = 1/2 mv^2, vectors): its grid read from the PDF
    gives the maths flat. Neither the one-click "Use the grid read from the PDF" nor the
    model-outage fallback writes it into the file; the decision stays open until the
    flattened cells are LaTeX."""
    from a11y_stem.pipeline import table_step
    from a11y_stem.pipeline.assemble_tables import flattened_cells
    from a11y_stem.web.review import apply_review, can_accept, rerender_html

    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    output = convert_pdf(
        LATEX.read_bytes(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(budget=budget),
    )
    result = output.result
    element = next(e for e in result.elements if e.caption and "Mechanical" in e.caption)
    assert element.table is not None
    # The stacked 1/2 is visible as such, not "12" (without the SLANet-plus file the
    # Definition column may be split in two).
    assert any(SUP1 + "2mv" + SUP2 in c.text for c in element.table.cells)

    apply_review(result, element.id, "edit", {"change": "use_local"})
    fix = result.fix_for(element.id)
    assert fix is not None and fix.status is Status.NEEDS_REVIEW and not can_accept(fix)
    assert "flattened_maths" in {c.code for c in fix.failed_checks}
    html = rerender_html(result, output.crops)
    assert "mv" + SUP2 not in html and "12mv2" not in html
    assert 'data-reason="flattened_maths"' in html

    # The model-outage fallback is the same grid: an error check, so no Accept.
    fallback = table_step._fix(element, table_step._Candidate(None, []), [])
    assert fallback.status is Status.NEEDS_REVIEW and not can_accept(fallback)

    # Every flattened cell written as LaTeX: the table can be used.
    assert fix.table is not None
    for cell in flattened_cells(fix.table.grid):
        typed = next(
            latex
            for part, latex in (
                ("2mv", r"\frac{1}{2}mv^2"),
                ("F", r"W = \vec{F}\cdot\vec{d}"),
                ("mv", r"\vec{p} = m\vec{v}"),
                ("p", r"\vec{p}"),
            )
            if part in cell.text
        )
        if cell.text.startswith("K = "):
            typed = "K = " + typed
        form = {"change": "cell", "row": str(cell.row), "col": str(cell.col), "cell": typed}
        # Without the SLANet-plus file a formula may be split over two cells, so the LaTeX
        # typed here says more than this cell's PDF text: used anyway (web.edit_check).
        apply_review(result, element.id, "edit", form, anyway=True)
    fix = result.fix_for(element.id)
    assert fix is not None and fix.table is not None
    assert not flattened_cells(fix.table.grid)
    assert "flattened_maths" not in {c.code for c in fix.failed_checks}
    html = rerender_html(result, output.crops)
    assert "<mfrac>" in html and "12mv2" not in html


# ---------------------------------------------------------------- with the model


@pytest.mark.skipif(not MODEL_PRESENT, reason="SLANet-plus model not fetched (make table-model)")
def test_with_the_model_spans_and_maths_cells_come_out_whole(monkeypatch):
    monkeypatch.delenv(table_model.MODEL_DIR_ENV)
    tables.reset_models()
    release()
    layout = analyze(LATEX.read_bytes())
    heat, quantities = found(layout)[2:4]
    assert heat.table is not None and quantities.table is not None
    spanning = [c for c in heat.table.cells if c.text == "Specific heat (J/kg K)"]
    assert spanning and spanning[0].col_span == 2
    assert quantities.table.cols == 3
    assert "p = mv⃗" in {c.text for c in quantities.table.cells}
    assert tables.REASON_SLANET in heat.reasons


def test_fixture_files_are_small_and_have_their_sources():
    for pdf, source in ((LATEX, LATEX.with_suffix(".tex")), (WORD, WORD.with_suffix(".html"))):
        assert pdf.stat().st_size < 300_000
        assert source.is_file()
    assert (WORD.parent / "make_word_pdf.py").is_file()
