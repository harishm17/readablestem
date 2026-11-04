"""LaTeXML's XHTML as the document model (`readablestem.source.ltx_reader`), on XHTML written by
hand: no LaTeXML and no sandbox needed. The same code on LaTeXML's real output is in
tests/test_source_convert.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from readablestem.core.types import Kind, Status
from readablestem.source import ltx_reader as reader
from source_support import png_bytes

XHTML = "http://www.w3.org/1999/xhtml"
MML = "http://www.w3.org/1998/Math/MathML"


def page(body: str, lang: str = "en") -> bytes:
    return (
        f'<?xml version="1.0" encoding="utf-8"?><html xmlns="{XHTML}" lang="{lang}">'
        f"<head><title>t</title></head><body>{body}</body></html>"
    ).encode()


def math(alt: str, inner: str, display: str = "inline") -> str:
    return (
        f'<math xmlns="{MML}" class="ltx_Math" alttext="{alt}" display="{display}">{inner}</math>'
    )


X_SQ = math("x^{2}", "<msup><mi>x</mi><mn>2</mn></msup>")


def read(body: str, tmp_path: Path, **kwargs) -> reader.Reading:
    return reader.read(page(body), tmp_path, **kwargs)


def kinds(result: reader.Reading) -> list[str]:
    return [e.kind.value for e in result.elements]


def test_headings_paragraphs_and_the_document_title(tmp_path):
    result = read(
        '<h1 class="ltx_title ltx_title_document">The Title</h1>'
        '<div class="ltx_section"><h2 class="ltx_title ltx_title_section">'
        '<span class="ltx_tag ltx_tag_section">1 </span>Intro</h2>'
        '<div class="ltx_para"><p class="ltx_p">Plain  text\nhere.</p></div></div>',
        tmp_path,
    )
    assert result.title == "The Title"
    assert kinds(result) == ["heading", "heading", "paragraph"]
    assert [e.text for e in result.elements] == ["The Title", "Intro", "Plain text here."]
    levels = [result.fixes[e.id].heading.level for e in result.elements[:2]]
    assert levels == [1, 2]
    assert all(result.fixes[e.id].status is Status.ACCEPTED for e in result.elements[:2])


def test_an_inline_formula_is_an_exact_equation_with_a_marker(tmp_path):
    result = read(f'<p class="ltx_p">Then {X_SQ} holds.</p>', tmp_path)
    paragraph, equation = result.elements
    assert paragraph.text == f"Then \u27e6{equation.id}\u27e7 holds."
    assert (equation.kind, equation.display, equation.parent_id) == (
        Kind.EQUATION,
        "inline",
        paragraph.id,
    )
    fix = result.fixes[equation.id]
    assert fix.status is Status.ACCEPTED and fix.review == "none" and fix.confidence == 1.0
    assert fix.equation is not None
    assert fix.equation.latex == "x^{2}" and "<msup>" in fix.equation.mathml
    assert "class=" not in fix.equation.mathml  # through the sanitiser: only allowed attributes
    assert fix.equation.speech  # read aloud like any equation
    assert fix.calls == []  # nothing was asked of a model


def test_a_display_equation_keeps_its_number(tmp_path):
    body = (
        '<table class="ltx_equation ltx_eqn_table"><tbody><tr class="ltx_equation">'
        f"<td>{math('v=at', '<mi>v</mi><mo>=</mo><mi>a</mi><mi>t</mi>', 'inline')}</td>"
        '<td><span class="ltx_tag ltx_tag_equation">(3)</span></td></tr></tbody></table>'
    )
    result = read(body, tmp_path)
    [equation] = result.elements
    assert equation.display == "block" and equation.equation_label == "(3)"
    fix = result.fixes[equation.id]
    assert fix.equation is not None and 'display="block"' in fix.equation.mathml


def test_an_aligned_row_joins_its_cells_into_one_equation(tmp_path):
    body = (
        '<table class="ltx_equationgroup"><tbody><tr class="ltx_equation">'
        f"<td>{math(r'\\displaystyle x', '<mi>x</mi>')}</td>"
        f"<td>{math(r'\displaystyle=1', '<mrow><mi></mi><mo>=</mo><mn>1</mn></mrow>')}</td>"
        "</tr></tbody></table>"
    )
    [equation] = read(body, tmp_path).elements
    assert equation.kind is Kind.EQUATION


def test_a_formula_with_an_error_is_a_required_decision_with_our_own_proposal(tmp_path):
    bad = math(
        r"\ce{H2O}",
        '<merror class="ltx_ERROR undefined"><mtext>\\ce</mtext></merror>',
    )
    result = read(f'<p class="ltx_p">Water {bad}.</p>', tmp_path)
    equation = result.elements[1]
    fix = result.fixes[equation.id]
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert fix.equation is not None and "H" in fix.equation.latex  # \ce written out by us
    assert [c.code for c in fix.failed_checks] == ["unknown_command"]
    assert any(n.code == "source_error" for n in result.notices)


def test_a_formula_nothing_can_read_fails_and_shows_its_source(tmp_path):
    bad = math(r"\frac{x", '<merror class="ltx_ERROR undefined"><mtext>\\frac</mtext></merror>')
    result = read(f'<p class="ltx_p">{bad}</p>', tmp_path)
    equation = result.elements[1]
    fix = result.fixes[equation.id]
    assert fix.status is Status.FAILED and fix.reason == "invalid_latex" and fix.equation is None
    assert equation.text == r"\frac{x"  # the TeX is shown where nothing better exists


def test_unsafe_mathml_is_not_accepted_as_it_is(tmp_path):
    evil = math("x", "<mi>x</mi><script>alert(1)</script>")
    result = read(f'<p class="ltx_p">{evil}</p>', tmp_path)
    fix = result.fixes[result.elements[1].id]
    assert fix.status is not Status.ACCEPTED
    assert fix.equation is None or "script" not in fix.equation.mathml


def test_lists_keep_their_markers_order_and_nesting(tmp_path):
    body = (
        '<ul class="ltx_itemize">'
        '<li class="ltx_item"><span class="ltx_tag ltx_tag_item">•</span>'
        '<div class="ltx_para"><p class="ltx_p">First</p></div></li>'
        '<li class="ltx_item"><span class="ltx_tag ltx_tag_item">•</span>'
        '<div class="ltx_para"><p class="ltx_p">Second</p>'
        '<ol class="ltx_enumerate"><li class="ltx_item"><span class="ltx_tag">1.</span>'
        '<div class="ltx_para"><p class="ltx_p">Inner</p></div></li></ol></div></li></ul>'
    )
    items = read(body, tmp_path).elements
    assert [e.text for e in items] == ["First", "Second", "Inner"]
    assert [e.list_level for e in items] == [1, 1, 2]
    assert [e.list_ordered for e in items] == [False, False, True]
    assert items[2].list_marker == "1."


def test_a_table_becomes_a_grid_with_spans_and_headers(tmp_path):
    body = (
        '<table class="ltx_tabular"><thead><tr><th class="ltx_th ltx_th_column">A</th>'
        '<th class="ltx_th ltx_th_column">B</th></tr></thead><tbody>'
        '<tr><td rowspan="2">x</td><td>1</td></tr><tr><td>2</td></tr>'
        '<tr><td colspan="2">wide</td></tr></tbody></table>'
    )
    result = read(body, tmp_path)
    [table] = result.elements
    grid = result.fixes[table.id].table.grid  # type: ignore[union-attr]
    assert (grid.rows, grid.cols, grid.header_rows) == (4, 2, 1)
    spans = {(c.row, c.col): (c.row_span, c.col_span) for c in grid.cells}
    assert spans[(1, 0)] == (2, 1) and spans[(3, 0)] == (1, 2)
    assert [c.text for c in grid.cells if c.header == "col"] == ["A", "B"]
    assert result.fixes[table.id].status is Status.ACCEPTED


def test_a_ragged_table_is_padded_and_a_huge_one_becomes_a_list(tmp_path):
    ragged = (
        '<table class="ltx_tabular"><tbody><tr><td>a</td><td>b</td></tr>'
        "<tr><td>c</td></tr></tbody></table>"
    )
    grid = read(ragged, tmp_path)
    [table] = grid.elements
    assert grid.fixes[table.id].table.grid.cols == 2  # type: ignore[union-attr]
    rows = "".join("<tr><td>x</td></tr>" for _ in range(301))
    huge = read(f'<table class="ltx_tabular"><tbody>{rows}</tbody></table>', tmp_path)
    assert set(kinds(huge)) == {"list_item"} and len(huge.elements) == 301


def test_a_figure_gets_the_projects_picture_and_its_caption(tmp_path):
    (tmp_path / "plot.png").write_bytes(png_bytes())
    body = (
        '<div class="ltx_figure" id="F1"><img src="" id="F1.g1" class="ltx_missing_image"/>'
        '<div class="ltx_caption"><span class="ltx_tag">Figure 1: </span>A plot.</div></div>'
    )
    result = read(body, tmp_path, graphics={"F1.g1": "plot.png"})
    [figure] = result.elements
    assert figure.kind is Kind.FIGURE and figure.caption == "A plot."
    assert result.images[figure.id].startswith(b"\x89PNG")
    assert figure.id not in result.fixes  # the figure step (a model) decides this one


def test_a_figure_without_a_usable_picture_is_a_required_decision(tmp_path):
    body = '<div class="ltx_figure"><span class="ltx_ERROR undefined">{tikzpicture}</span></div>'
    result = read(body, tmp_path)
    [figure] = result.elements
    fix = result.fixes[figure.id]
    assert fix.status is Status.FAILED and fix.reason == "source_figure_missing"
    assert fix.review == "required" and figure.id not in result.images


@pytest.mark.parametrize(
    "src",
    [
        "../outside.png",
        "/etc/passwd",
        "sub/../../outside.png",
        "~/x.png",
        "data:image/png;base64,AAAA",
        "http://example.org/x.png",
        "a\\b.png",
        "plot.svg",
        "notes.txt",
        "",
    ],
)
def test_a_picture_path_never_leaves_the_project(src, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "outside.png").write_bytes(png_bytes())
    (project / "plot.svg").write_text("<svg/>")
    (project / "notes.txt").write_text("secret")
    body = f'<div class="ltx_figure"><img src="{src}" id="g"/></div>'
    result = reader.read(page(body), project)
    assert result.images == {}  # no picture, whatever the path said


def test_a_symlink_picture_in_the_project_is_not_followed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "secret.png").write_bytes(png_bytes())
    (project / "link.png").symlink_to(tmp_path / "secret.png")
    result = reader.read(page('<div class="ltx_figure"><img src="link.png"/></div>'), project)
    assert result.images == {}


def test_a_picture_that_is_not_a_picture_is_dropped(tmp_path):
    (tmp_path / "fake.png").write_bytes(b"not a png")
    result = read('<div class="ltx_figure"><img src="fake.png"/></div>', tmp_path)
    assert result.images == {}


def test_a_huge_canvas_in_a_small_file_is_refused(tmp_path):
    from PIL import Image

    big = tmp_path / "big.png"
    Image.new("1", (30000, 30000)).save(big)  # tiny file, 900 megapixels
    assert big.stat().st_size < 10_000_000
    result = read('<div class="ltx_figure"><img src="big.png"/></div>', tmp_path)
    assert result.images == {}


def test_links_keep_safe_schemes_only_and_scripts_become_runs(tmp_path):
    body = (
        '<p class="ltx_p">See <a href="https://example.org/x">the page</a>, '
        '<a href="javascript:alert(1)">bad</a>, <a href="#S1">ref</a> and x<sup>2</sup>.</p>'
    )
    [paragraph] = read(body, tmp_path).elements
    assert [link.href for link in paragraph.links] == ["https://example.org/x"]
    link = paragraph.links[0]
    assert (paragraph.text or "")[link.start : link.end] == "the page"
    run = paragraph.script_runs[0]
    assert run.kind == "sup" and (paragraph.text or "")[run.start : run.end] == "2"


def test_a_footnote_is_kept_in_the_text(tmp_path):
    body = (
        '<p class="ltx_p">Text<span class="ltx_note ltx_role_footnote">'
        '<sup class="ltx_note_mark">1</sup>'
        '<span class="ltx_note_content">The note.</span></span> goes on.</p>'
    )
    assert "(note: The note.)" in (read(body, tmp_path).elements[0].text or "")


def test_a_theorem_title_leads_its_paragraph(tmp_path):
    body = (
        '<div class="ltx_theorem"><h6 class="ltx_title ltx_runin">Theorem 1.</h6>'
        '<div class="ltx_para"><p class="ltx_p">Every x is y.</p></div></div>'
    )
    assert read(body, tmp_path).elements[0].text == "Theorem 1. Every x is y."


def test_a_listing_keeps_its_lines(tmp_path):
    body = '<pre class="ltx_verbatim">\nfor i in x:\n    f(i)\n</pre>'
    [code] = read(body, tmp_path).elements
    assert code.preformatted and code.text == "for i in x:\n    f(i)"


def test_text_level_errors_stay_in_the_text_and_add_a_notice(tmp_path):
    body = '<p class="ltx_p">An <span class="ltx_ERROR undefined">\\foo</span>x here.</p>'
    result = read(body, tmp_path)
    assert result.elements[0].text == "An \\foox here."
    [notice] = result.notices
    assert notice.code == "source_error" and notice.count == 1


def test_scripts_and_styles_are_ignored(tmp_path):
    body = '<script>alert(1)</script><style>p{}</style><p class="ltx_p">ok</p>'
    assert [e.text for e in read(body, tmp_path).elements] == ["ok"]


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"<html",
        b'<!DOCTYPE html [<!ENTITY x "y">]><html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
        b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><html><body>&a;</body></html>',
    ],
)
def test_an_xhtml_that_is_not_plain_is_refused(bad, tmp_path):
    with pytest.raises(reader.ReadError):
        reader.read(bad, tmp_path)


def test_a_document_with_nothing_in_it_is_refused(tmp_path):
    with pytest.raises(reader.ReadError):
        read("", tmp_path)
    with pytest.raises(reader.ReadError):
        reader.read(f'<html xmlns="{XHTML}"/>'.encode(), tmp_path)


def test_a_very_deep_document_is_refused_not_a_crash(tmp_path):
    deep = '<div class="a">' * 400 + "x" + "</div>" * 400
    with pytest.raises((reader.ReadError, RecursionError)):
        read(deep, tmp_path)


def test_a_very_large_document_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_XHTML_BYTES", 100)
    with pytest.raises(reader.ReadError):
        read('<p class="ltx_p">' + "x" * 200 + "</p>", tmp_path)


def test_too_many_elements_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_ELEMENTS", 5)
    with pytest.raises(reader.ReadError):
        read('<p class="ltx_p">a</p>' * 10, tmp_path)


def test_the_graphics_map_reads_the_xml_ids(tmp_path):
    xml = (
        b'<?xml version="1.0"?><document xmlns="http://dlmf.nist.gov/LaTeXML">'
        b'<graphics graphic="fig/a.png" xml:id="S1.F1.g1"/><graphics xml:id="x"/></document>'
    )
    assert reader.graphics_of(xml) == {"S1.F1.g1": "fig/a.png"}
    assert reader.graphics_of(b"<broken") == {}


def test_the_language_comes_from_the_page(tmp_path):
    assert reader.read(page('<p class="ltx_p">x</p>', lang="fr"), tmp_path).lang == "fr"
