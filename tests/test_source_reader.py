"""LaTeXML's XHTML as the document model (`readablestem.source.ltx_reader`), on XHTML written by
hand: no LaTeXML and no sandbox needed. The same code on LaTeXML's real output is in
tests/test_source_convert.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from readablestem.core.types import Element, Kind, Status
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


def test_a_contents_entry_with_sub_entries_says_its_own_words_once(tmp_path):
    def entry(target: str, words: str, *inner: str) -> str:
        nested = f'<ol class="ltx_toclist">{"".join(inner)}</ol>' if inner else ""
        return (
            f'<li class="ltx_tocentry"><a href="#{target}" class="ltx_ref">'
            f'<span class="ltx_text ltx_ref_title">{words}</span></a>{nested}</li>'
        )

    body = (
        '<ol class="ltx_toclist">'
        + entry("S1", "Alpha", entry("S1.SS1", "Beta"), entry("S1.SS2", "Gamma"))
        + entry("S2", "Delta")
        + "</ol>"
    )
    items = [e for e in read(body, tmp_path).elements if e.kind is Kind.LIST_ITEM]
    assert [(e.text, e.list_level) for e in items] == [
        ("Alpha", 1),
        ("Beta", 2),
        ("Gamma", 2),
        ("Delta", 1),
    ]


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


def test_row_heads_and_a_cell_of_words_and_a_formula_are_read_as_such(tmp_path):
    """`header_cols` comes from the `th` cells LaTeXML wrote; a cell that mixes words and a
    formula is the whole cell as maths (`\\text{}` for the words), not the formula's TeX in
    text (SW18.3, WS18.10)."""
    mixed = "Value " + math("y^{2}", "<msup><mi>y</mi><mn>2</mn></msup>") + " units"
    body = (
        '<table class="ltx_tabular"><thead><tr><th class="ltx_th ltx_th_column">Quantity</th>'
        '<th class="ltx_th ltx_th_column">Value</th></tr></thead><tbody>'
        f'<tr><th class="ltx_th ltx_th_row">Rate</th><td>{mixed}</td></tr>'
        '<tr><th class="ltx_th ltx_th_row">Mass</th><td>3</td></tr></tbody></table>'
    )
    result = read(body, tmp_path)
    [table] = result.elements
    grid = result.fixes[table.id].table.grid  # type: ignore[union-attr]
    assert (grid.header_rows, grid.header_cols) == (1, 1)
    assert [c.header for c in grid.cells if c.col == 0] == ["col", "row", "row"]
    cell = next(c for c in grid.cells if (c.row, c.col) == (1, 1))
    assert cell.latex == "\\text{Value } y^{2} \\text{ units}"
    assert cell.mathml is not None and "<mtext>Value" in cell.mathml and "<msup>" in cell.mathml
    assert result.fixes[table.id].status is Status.ACCEPTED


def test_a_ragged_table_is_padded_and_a_huge_one_becomes_a_list(tmp_path):
    ragged = (
        '<table class="ltx_tabular"><tbody><tr><td>a</td><td>b</td></tr>'
        "<tr><td>c</td></tr></tbody></table>"
    )
    grid = read(ragged, tmp_path)
    [table] = grid.elements
    assert grid.fixes[table.id].table.grid.cols == 2  # type: ignore[union-attr]
    # no header marked in the source: the choice a PDF table with no header has, not an accept
    headless = grid.fixes[table.id]
    assert (headless.status, headless.review) == (Status.NEEDS_REVIEW, "required")
    assert [c.code for c in headless.failed_checks] == ["no_headers"]
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
    figure = result.elements[0]  # the error's text follows it, as written (SW42.4)
    fix = result.fixes[figure.id]
    assert fix.status is Status.FAILED and fix.reason == "source_figure_missing"
    assert fix.review == "required" and figure.id not in result.images
    # the error node in block position is counted and tied to the figure (SW42.1)
    assert result.error_nodes == 1
    assert [(m.name, m.element_id) for m in result.marks] == [("{tikzpicture}", figure.id)]


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
        '<a href="javascript:alert(1)">bad</a>, <a href="#S1">ref</a> and x<sup>2</sup>, '
        '<span class="ltx_text" style="color:#FF0000;">red words</span> and '
        '<span class="ltx_text" style="color:#333333;">dark grey</span>.</p>'
    )
    [paragraph] = read(body, tmp_path).elements
    assert [link.href for link in paragraph.links] == ["https://example.org/x"]
    link = paragraph.links[0]
    assert (paragraph.text or "")[link.start : link.end] == "the page"
    run = paragraph.script_runs[0]
    assert run.kind == "sup" and (paragraph.text or "")[run.start : run.end] == "2"
    # the colour a source sets on words is kept as on the PDF path; body-dark text is not a run
    [colour] = paragraph.colour_runs
    assert (paragraph.text or "")[colour.start : colour.end] == "red words"
    assert colour.rgb == (255, 0, 0)


def test_a_reference_the_source_makes_is_a_link_to_the_element_it_names(tmp_path):
    def ref(target: str, words: str) -> str:
        return f'<a href="#{target}" class="ltx_ref"><span class="ltx_text">{words}</span></a>'

    body = (
        f'<ul class="ltx_toclist"><li class="ltx_tocentry">{ref("S2", "2 Next")}</li></ul>'
        '<div id="S1" class="ltx_section"><h2 class="ltx_title ltx_title_section">'
        '<span class="ltx_tag ltx_tag_section">1 </span>First</h2>'
        f'<div class="ltx_para"><p class="ltx_p">See Section {ref("S2", "2")}, equation '
        f"({ref('S1.E2', '2')}), {ref('bib.bib1', '1')} and {ref('nowhere', '?')}.</p></div>"
        '<div id="Thm1" class="ltx_theorem"><h6 class="ltx_title ltx_runin">'
        '<span class="ltx_tag">Theorem 1</span>.</h6><div class="ltx_para">'
        f'<p class="ltx_p">It holds, as {ref("Thm1", "Theorem 1")} says.</p></div></div>'
        '<table id="S1.EGx1" class="ltx_equationgroup"><tbody id="S1.E1"><tr class="ltx_equation">'
        f"<td>{math('a', '<mi>a</mi>')}</td></tr></tbody>"
        '<tbody id="S1.E2"><tr class="ltx_equation">'
        f"<td>{math('b', '<mi>b</mi>')}</td></tr></tbody></table></div>"
        '<div id="S2" class="ltx_section"><h2 class="ltx_title ltx_title_section">Next</h2></div>'
        '<ul class="ltx_biblist"><li id="bib.bib1" class="ltx_bibitem">'
        '<span class="ltx_tag ltx_tag_bibitem">[1]</span>'
        '<span class="ltx_bibblock">A. One, 1990.</span></li></ul>'
    )
    result = read(body, tmp_path)
    by_id = {e.id: e for e in result.elements}

    def found(element: Element) -> list[tuple[str, str, str]]:
        return [
            ((element.text or "")[k.start : k.end], k.kind, by_id[k.target_id].text or "")
            for k in element.links
        ]

    toc, _, paragraph, theorem, _, row_b, _, entry = result.elements
    assert found(toc) == [("2 Next", "internal", "Next")]
    # the number is the link's words; a link to nothing makes none, nor does a theorem citing
    # itself; the second row of a group is the equation the second number names
    assert found(paragraph) == [
        ("2", "internal", "Next"),
        ("2", "internal", ""),
        ("1", "internal", "A. One, 1990."),
    ]
    assert paragraph.links[1].target_id == row_b.id
    assert entry.kind is Kind.LIST_ITEM and not theorem.links


def test_a_footnote_is_kept_in_the_text(tmp_path):
    body = (
        '<p class="ltx_p">Text<span class="ltx_note ltx_role_footnote">'
        '<sup class="ltx_note_mark">1</sup>'
        '<span class="ltx_note_content">The note.</span></span> goes on.</p>'
    )
    assert "(note: The note.)" in (read(body, tmp_path).elements[0].text or "")


def test_the_front_matter_is_a_title_a_line_for_each_author_and_the_date(tmp_path):
    """LaTeXML's own front matter (SW18.3): `\\thanks` on the title is a line of its own and not
    part of it; each author is a line with where they work and their own note, never merged
    into the name; the date follows."""
    note = (
        '<span class="ltx_note ltx_role_thanks"><sup class="ltx_note_mark">†</sup>'
        '<span class="ltx_note_outer"><span class="ltx_note_content">'
        '<sup class="ltx_note_mark">†</sup><span class="ltx_note_type">thanks: </span>'
        "Funded by X.</span></span></span>"
    )
    body = (
        f'<h1 class="ltx_title ltx_title_document">A Study{note}</h1>'
        '<div class="ltx_authors"><span class="ltx_creator ltx_role_author">'
        '<span class="ltx_personname">Ada Lovelace\n<br class="ltx_break"/>Dept of Maths, Univ A\n'
        '</span><span class="ltx_author_notes">Corresponding author.</span></span>'
        '<span class="ltx_author_before">  </span><span class="ltx_creator ltx_role_author">'
        '<span class="ltx_personname">Bob Babbage\n<br class="ltx_break"/>Univ B\n'
        '<br class="ltx_break"/><span class="ltx_text ltx_font_typewriter">bob@b.edu</span>\n'
        '</span></span></div><div class="ltx_dates">(Fall 2026)</div>'
    )
    result = read(body, tmp_path)
    assert result.title == "A Study"
    assert [e.text for e in result.elements] == [
        "A Study",
        "(note: Funded by X.)",
        "Ada Lovelace, Dept of Maths, Univ A (note: Corresponding author.)",
        "Bob Babbage, Univ B, bob@b.edu",
        "Fall 2026",
    ]


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
    assert reader.graphics_of(xml, tmp_path) == {"S1.F1.g1": "fig/a.png"}
    assert reader.graphics_of(b"<broken", tmp_path) == {}


def test_the_language_comes_from_the_page(tmp_path):
    assert reader.read(page('<p class="ltx_p">x</p>', lang="fr"), tmp_path).lang == "fr"


# ------------------------------------------------------------- Word, read from the file itself
# (`source.docx_package`, `source.pandoc`, `source.docx_reader`): needs pandoc and a sandbox.

from fixtures import office  # noqa: E402  (grouped with the tests that use it)
from readablestem.core import sandbox  # noqa: E402
from readablestem.source import docx_package, docx_reader  # noqa: E402
from readablestem.source import pandoc as pandoc_step  # noqa: E402
from readablestem.web.office_convert import OfficeRejected  # noqa: E402

needs_pandoc = pytest.mark.skipif(
    not (pandoc_step.installed() and sandbox.available()),
    reason="needs pandoc and a working sandbox (bubblewrap or Landlock)",
)
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _cells(*rows: str, marked: bool) -> str:
    out = '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblLook w:firstRow="1"/></w:tblPr>'
    out += '<w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>'
    for number, row in enumerate(rows):
        head = "<w:trPr><w:tblHeader/></w:trPr>" if marked and number == 0 else ""
        out += f"<w:tr>{head}"
        for cell in row.split("|"):
            out += (
                "<w:tc><w:tcPr><w:tcW w:w='2000' w:type='dxa'/></w:tcPr>"
                f"<w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>"
            )
        out += "</w:tr>"
    return out + "</w:tbl>"


def word(body: str, rels: str = "") -> bytes:
    parts = office.docx_parts()
    document = (
        str(parts["word/document.xml"])
        .replace("<w:document ", f'<w:document xmlns:m="{M_NS}" ', 1)
        .split("<w:body>")[0]
        + f"<w:body>{body}</w:body></w:document>"
    )
    named = "".join(
        f'<w:style w:type="paragraph" w:styleId="{i}"><w:name w:val="{n}"/></w:style>'
        for i, n in (("Title", "Title"), ("ListParagraph", "List Paragraph"), ("Quote", "Quote"))
    )
    styles = str(parts["word/styles.xml"]).replace("</w:styles>", named + "</w:styles>")
    W = office.W_NS
    numbering = (
        f'{office.HEAD}<w:numbering xmlns:w="{W}">'
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
        '<w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
        "</w:lvl></w:abstractNum>"
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    )
    rels = str(parts["word/_rels/document.xml.rels"]).replace(
        "</Relationships>",
        f'<Relationship Id="rId20" Type="{office.OD}/numbering" Target="numbering.xml"/>'
        + rels
        + "</Relationships>",
    )
    types = str(parts["[Content_Types].xml"]).replace(
        "</Types>",
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.numbering+xml"/></Types>',
    )
    return office.rebuild(
        parts,
        replace={
            "word/document.xml": document,
            "word/styles.xml": styles,
            "word/_rels/document.xml.rels": rels,
            "[Content_Types].xml": types,
        },
        add={"word/numbering.xml": numbering},
    )


def read_word(data: bytes):
    prepared = docx_package.prepare(data)
    result = pandoc_step.read("docx", prepared.working)
    assert result.tree is not None, result.failure
    return docx_reader.read(prepared, result.tree)[0]


@needs_pandoc
def test_a_word_file_gives_its_structure_its_equations_and_leaves_hidden_text_out():
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Kinematics notes</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Motion</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>Speed is distance over time, page </w:t></w:r>"
        '<w:fldSimple w:instr=" PAGE "><w:r><w:t>7</w:t></w:r></w:fldSimple>'
        "<w:r><w:rPr><w:vanish/></w:rPr><w:t>SECRET-HIDDEN-WORDS</w:t></w:r></w:p>"
        "<w:p><m:oMathPara><m:oMath><m:r><m:t>v=</m:t></m:r><m:f><m:num><m:r><m:t>d</m:t></m:r>"
        "</m:num><m:den><m:r><m:t>t</m:t></m:r></m:den></m:f></m:oMath></m:oMathPara></w:p>"
        + "".join(
            '<w:p><w:pPr><w:pStyle w:val="ListParagraph"/>'
            '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/>'
            f"</w:numPr></w:pPr><w:r><w:t>{word}</w:t></w:r></w:p>"
            for word in ("First step", "Second step")
        )
        + _cells("Name|Value", "g|9.8", marked=True)
        + _cells("Name|Value", "c|3.0", marked=False)
        # a cell whose formula the checks refuse (`\phantom` is not a command they allow)
        + _cells("Name|Law", "first|PHANT", marked=True).replace(
            "<w:r><w:t>PHANT</w:t></w:r>",
            "<m:oMath><m:phant><m:e><m:r><m:t>x</m:t></m:r></m:e></m:phant></m:oMath>",
        )
        + '<w:p><w:pPr><w:pStyle w:val="Quote"/></w:pPr>'
        + "<w:r><w:t>Words of another.</w:t></w:r></w:p>"
    )
    reading = read_word(word(body))
    assert kinds(reading) == [
        "heading",
        "heading",
        "paragraph",
        "equation",
        "list_item",
        "list_item",
        "table",
        "table",
        "table",
        "paragraph",
    ]
    assert reading.title == "Kinematics notes" and reading.unread_words == 0
    text = " ".join(e.text or "" for e in reading.elements)
    assert "SECRET-HIDDEN-WORDS" not in text and "page 7" in text
    equation = reading.elements[3]
    fix = reading.fixes[equation.id]
    assert fix.status is Status.ACCEPTED and fix.equation is not None
    assert "frac" in fix.equation.latex and "<mfrac" in (fix.equation.mathml or "")
    items = reading.elements[4:6]  # a list item keeps its words under a styled paragraph
    assert [(i.text, i.list_marker) for i in items] == [("First step", "1."), ("Second step", "2.")]
    first, second, unread, quoted = reading.elements[6:]
    assert quoted.role == "quote" and reading.elements[2].role is None  # Word's Quote style
    # the author's header row is exact; the first-row look pandoc reads as a header is only a
    # proposal, and says so in a check
    assert first.table is not None and first.table.header_rows == 1
    flagged = {e.id: {c.name for c in reading.fixes[e.id].checks} for e in (first, second)}
    assert docx_reader.HEADER_CHECK not in flagged[first.id]
    assert docx_reader.HEADER_CHECK in flagged[second.id]
    assert {n.code for n in reading.notices} >= {"word_hidden_text"}
    # a formula in a cell the checks refuse stays in the cell as TeX, so the table is a
    # required decision for the owner, as a LaTeX source's table is (and a heading's here)
    held = reading.fixes[unread.id]
    assert (held.status, held.review) == (Status.NEEDS_REVIEW, "required")
    assert any(c.code == docx_reader.FORMULA_UNREAD for c in held.checks if not c.passed)
    assert unread.table is not None and "\\phantom" in unread.table.cells[-1].text


@needs_pandoc
def test_pandoc_is_given_no_file_it_could_read_and_no_address_it_could_fetch():
    parts = office.docx_parts()
    rels = str(parts["word/_rels/document.xml.rels"]).replace(
        'Target="media/image1.png"', 'Target="/etc/passwd"'
    )
    raw = office.rebuild(parts, replace={"word/_rels/document.xml.rels": rels})
    # even handed the raw file, pandoc's own sandbox flag reads nothing of the host's
    result = pandoc_step.read("docx", raw)
    assert "root:x:0:0" not in str(result.tree)
    # and the working copy has no relationship that leaves the package at all
    prepared = docx_package.prepare(office.docx())
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(prepared.working)) as archive:
        names = archive.namelist()
        assert not [n for n in names if n.startswith("word/embeddings")]
        assert b"External" not in archive.read("word/_rels/document.xml.rels")


@pytest.mark.parametrize(
    "member",
    ["word/../../evil.xml", "/etc/evil.xml", "word\\evil.xml"],
)
def test_a_member_name_that_leaves_the_package_is_refused_before_pandoc(member):
    parts = office.docx_parts()
    parts[member] = "<x/>"
    with pytest.raises(OfficeRejected) as refused:
        docx_package.check(office._zip(parts))
    assert refused.value.code == "office_unsafe"


@needs_pandoc
def test_a_word_files_typed_addresses_and_references_are_linked_and_its_own_links_kept():
    from readablestem.config import get_settings
    from readablestem.models.fake import FakeVisionClient
    from readablestem.source import envelope
    from readablestem.source.convert import convert_source

    link = f'<Relationship Id="rId30" Type="{office.OD}/hyperlink" Target="https://example.org/x" '
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:bookmarkStart w:id="1" w:name="_Ref1"/>'
        '<w:r><w:t>Motion</w:t></w:r><w:bookmarkEnd w:id="1"/></w:p>'
        "<w:p><w:r><w:t xml:space='preserve'>Write to a@b.org or see www.example.org, </w:t></w:r>"
        '<w:hyperlink r:id="rId30"><w:r><w:t>the page</w:t></w:r></w:hyperlink>'
        "<w:r><w:t xml:space='preserve'> and www.example.org/typed. See Table 1.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Theorem 1. A statement.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Then Theorem 1 gives the table's answer.</w:t></w:r></w:p>"
        '<w:p><w:r><w:t xml:space="preserve">Back to </w:t></w:r><w:hyperlink w:anchor="_Ref1">'
        "<w:r><w:t>Motion</w:t></w:r></w:hyperlink><w:r><w:t>.</w:t></w:r></w:p>"
    )
    data = word(body, rels=link + 'TargetMode="External"/>')
    blob = envelope.wrap("docx", "notes.docx", None, data)
    result = convert_source(blob, settings=get_settings(), client=FakeVisionClient()).result
    heading, first, theorem, last, back = result.elements

    def found(element: Element) -> list[tuple[str, str]]:
        return [(k.kind, (element.text or "")[k.start : k.end]) for k in element.links]

    assert found(first) == [
        ("typed", "a@b.org"),
        ("typed", "www.example.org"),
        ("uri", "the page"),  # the file's own link, unchanged
        ("typed", "www.example.org/typed"),
    ]
    assert found(last) == [("crossref", "Theorem 1")]
    assert last.links[0].target_id == theorem.id and not theorem.links
    assert found(back) == [("internal", "Motion")] and back.links[0].target_id == heading.id


@needs_pandoc
def test_a_word_files_bold_italic_and_code_are_runs_and_its_colour_is_counted():
    def run(words: str, props: str = "") -> str:
        return f"<w:r><w:rPr>{props}</w:rPr><w:t xml:space='preserve'>{words}</w:t></w:r>"

    mono = '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
    bold, italic = "<w:b/>", "<w:i/>"
    body = (
        "<w:p>"
        + run("Do ")
        + run("not", bold)
        + run(" open it, read ")
        + run("twice ", italic)
        + run("slowly", bold + italic)
        + run(" and run ")
        + run("make test", mono)
        + run(" in the ")
        + run("red", '<w:color w:val="FF0000"/>')
        + run(" part, a ")
        + run("link", '<w:color w:val="0563C1"/>').join(
            ["<w:hyperlink w:anchor='x'>", "</w:hyperlink>"]
        )
        + "</w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        + run("A heading ")
        + run("bold", bold)
        + "</w:p>"
        "<w:p>" + run("Set whole in bold, so the block's own style.", bold) + "</w:p>"
        '<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/>'
        "</w:numPr></w:pPr>"
        + run("Bring a ")
        + run("calculator", bold)
        + "</w:p>"
        + _cells("Item|Note", "Quiz|closed book", marked=True).replace(
            "<w:r><w:t>closed book</w:t></w:r>", run("closed", bold) + run(" book")
        )
    )
    reading = read_word(word(body))
    first, heading, whole = reading.elements[:3]
    assert [((first.text or "")[r.start : r.end], r.kind) for r in first.runs] == [
        ("not", "strong"),
        ("twice slowly", "emphasis"),
        ("slowly", "strong"),
        ("make test", "code"),
    ]
    assert not heading.runs and not whole.runs  # a heading is not bold text; a whole block is not
    [item] = [e for e in reading.elements if e.kind.value == "list_item"]
    assert [(item.text or "")[r.start : r.end] for r in item.runs] == ["calculator"]
    [table] = [e for e in reading.elements if e.table]
    assert [c.text[r.start : r.end] for c in table.table.cells for r in c.runs] == ["closed"]
    # pandoc keeps no colour: the passages are counted from the file, the link's own colour not
    assert [(n.code, n.count) for n in reading.notices if n.code == "colour_used"] == [
        ("colour_used", 1)
    ]
