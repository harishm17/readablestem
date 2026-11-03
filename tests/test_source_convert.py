"""LaTeX sources converted end to end with the real LaTeXML in the real sandbox and the fake model
client (no key, no network): the fixtures in tests/fixtures/source/. Skipped where LaTeXML or a
working bubblewrap is missing (docs/development.md, "LaTeX source tests")."""

from __future__ import annotations

import pytest

from readablestem.config import get_settings
from readablestem.core.types import ConversionOptions, DocumentResult, Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import html_checks
from readablestem.pipeline.run import convert_pdf
from readablestem.source import envelope
from readablestem.source.convert import SourceFailed, convert_source
from source_support import fixture, make_zip, needs_latexml, png_bytes

pytestmark = needs_latexml


def convert(name: str, extra: dict[str, bytes | str] | None = None, client=None):
    """A fixture as a `.tex` upload, or as a zip when it comes with other files."""
    text = fixture(f"{name}.tex")
    if extra:
        blob = envelope.wrap(
            "zip", f"{name}.zip", f"{name}.tex", make_zip({f"{name}.tex": text, **extra})
        )
    else:
        blob = envelope.wrap("tex", f"{name}.tex", None, text.encode())
    return convert_source(blob, settings=get_settings(), client=client or FakeVisionClient()).result


def by_status(result: DocumentResult, kind: Kind) -> dict[Status, int]:
    counts: dict[Status, int] = {}
    for fix in result.fixes:
        if fix.kind is kind:
            counts[fix.status] = counts.get(fix.status, 0) + 1
    return counts


def test_amsmath_gives_exact_equations_with_no_decision():
    result = convert("amsmath")
    assert result.origin == "latex" and result.title == "Equations"
    assert by_status(result, Kind.EQUATION) == {Status.ACCEPTED: 6}
    assert result.stats.required_decisions == 0 and result.stats.model_calls == 0
    latex = [f.equation.latex for f in result.fixes if f.equation]
    assert any("vmatrix" in text for text in latex) and any(r"\int" in text for text in latex)
    assert "<math" in result.html and "<mtable" in result.html  # the matrix and the cases
    assert "web version of a LaTeX document from your course" in result.html
    assert not [c for c in result.html_checks if not c.passed and c.severity == "error"]


def test_macros_are_expanded_by_the_source_not_guessed():
    result = convert("macros")
    assert by_status(result, Kind.EQUATION) == {Status.ACCEPTED: 6}
    latex = " ".join(f.equation.latex for f in result.fixes if f.equation)
    assert r"\mathbb{R}" in latex and r"\left\lVert" in latex and r"\operatorname{grad}" in latex


def test_siunitx_quantities_are_read_as_units():
    result = convert("siunitx")
    assert by_status(result, Kind.EQUATION) == {Status.ACCEPTED: 5}
    speech = " ".join(f.equation.speech for f in result.fixes if f.equation)
    assert speech  # every one is spoken


def test_mhchem_is_a_decision_with_our_own_reading_as_the_proposal():
    result = convert("mhchem")
    equations = [f for f in result.fixes if f.kind is Kind.EQUATION]
    assert [f.status for f in equations] == [Status.NEEDS_REVIEW, Status.NEEDS_REVIEW]
    assert all(f.review == "required" and f.equation is not None for f in equations)
    assert result.stats.required_decisions == 2
    assert [n.code for n in result.notices] == ["source_error"]


def test_a_tikz_figure_is_a_decision_without_a_picture():
    result = convert("tikz")
    [fix] = [f for f in result.fixes if f.kind is Kind.FIGURE]
    assert fix.status is Status.FAILED and fix.reason == "source_figure_missing"
    assert result.stats.model_calls == 0  # nothing to send a model
    assert "A circle inside a square." in result.html


def test_a_bibliography_becomes_a_list_with_no_repeats():
    result = convert("bib")
    items = [e.text for e in result.elements if e.kind is Kind.LIST_ITEM]
    assert items == ["I. Newton, Principia, 1687.", "G. Leibniz, Nova Methodus, 1684."]
    assert result.html.count("Newton") == 1


def test_a_broken_macro_is_a_required_decision_showing_the_tex():
    result = convert("broken_macro")
    statuses = by_status(result, Kind.EQUATION)
    assert statuses == {Status.ACCEPTED: 1, Status.FAILED: 2}
    failed = [
        e for e in result.elements if (f := result.fix_for(e.id)) and f.status is Status.FAILED
    ]
    assert {e.text for e in failed} == {r"\mysterycommand{x}+y", r"\anotherunknown{z}=1"}
    assert result.stats.required_decisions == 2
    assert r"\mysterycommand{x}+y" in result.html  # shown as written, flagged


def test_a_table_keeps_its_spans():
    result = convert("tables")
    [fix] = [f for f in result.fixes if f.kind is Kind.TABLE]
    grid = fix.table.grid  # type: ignore[union-attr]
    assert (grid.rows, grid.cols) == (4, 3) and grid.caption == "Grouped data"
    assert any(c.row_span == 2 for c in grid.cells) and any(c.col_span == 2 for c in grid.cells)
    assert "<table" in result.html and 'rowspan="2"' in result.html


def test_code_footnotes_links_and_scripts():
    result = convert("listing")
    assert any(e.preformatted for e in result.elements)
    text = next(e for e in result.elements if e.text and "footnote" in e.text)
    assert "(note: A note.)" in (text.text or "")
    assert [link.href for link in text.links] == ["https://example.org/page"]
    assert text.script_runs and 'href="https://example.org/page"' in result.html


def test_figures_from_the_project_go_through_the_figure_step():
    def figure(request):
        return {
            "figure_type": "chart",
            "decorative": False,
            "alt": "A red box",
            "confidence": 0.95,
        }

    client = FakeVisionClient(responders={"figure": figure})
    result = convert("rich_figure", extra={"fig1.png": png_bytes()}, client=client)
    figures = [f for f in result.fixes if f.kind is Kind.FIGURE]
    assert len(figures) == 1
    assert [r.step for r in client.requests] == ["figure"]  # one picture, one request
    assert figures[0].status in {Status.ACCEPTED, Status.NEEDS_REVIEW}
    assert "data:image/png;base64" in result.html  # the project's picture is in the file


def test_a_source_job_with_an_unreadable_picture_still_converts():
    result = convert("rich_figure", extra={"fig1.png": b"not an image"})
    [fix] = [f for f in result.fixes if f.kind is Kind.FIGURE]
    assert fix.status is Status.FAILED and fix.reason == "source_figure_missing"


def test_convert_pdf_hands_a_source_envelope_to_the_source_path():
    blob = envelope.wrap("tex", "a.tex", None, fixture("bib.tex").encode())
    output = convert_pdf(blob, settings=get_settings(), client=FakeVisionClient())
    assert output.result.origin == "latex"


def test_the_assembled_file_passes_the_static_checks():
    result = convert("amsmath")
    assert all(c.passed for c in html_checks.static_checks(result.html) if c.severity == "error")


def test_no_pdf_facts_are_invented_for_a_source():
    result = convert("amsmath")
    assert result.source_facts is None and result.page_types == [] and result.page_sizes == {}
    assert result.image_only_pages == [] and result.page_labels == {}


def test_options_do_not_change_the_exact_equations():
    on = ConversionOptions(tables=True, long_descriptions=True, colour=True)
    blob = envelope.wrap("tex", "a.tex", None, fixture("amsmath.tex").encode())
    result = convert_source(
        blob, settings=get_settings(), client=FakeVisionClient(), options=on
    ).result
    assert by_status(result, Kind.EQUATION) == {Status.ACCEPTED: 6}


def test_a_project_that_is_not_latex_fails_with_a_plain_code():
    blob = envelope.wrap(
        "tex", "a.tex", None, b"\\documentclass{article}\n\\begin{document}\\end{document}"
    )
    with pytest.raises(SourceFailed) as raised:
        convert_source(blob, settings=get_settings(), client=FakeVisionClient())
    assert raised.value.code in {"source_unreadable", "source_failed"}
