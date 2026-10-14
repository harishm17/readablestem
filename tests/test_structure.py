"""Code blocks, glossary entries and footnote links (T2-16): layout, then the HTML.

Code is `<pre><code>` with its lines and indentation; a glossary is a `<dl>`; a footnote's
mark in the text links to the footnote. Pages are drawn with PyMuPDF so each shape is exact.
"""

from __future__ import annotations

import pymupdf

from readablestem.core.types import Element, Kind
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.heading_step import heading_fixes
from readablestem.pipeline.layout import analyze

BODY = 11.0
CODE = 10.0
ADVANCE = pymupdf.get_text_length("x", fontname="cour", fontsize=CODE)


def page_of(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def html_of(pdf: bytes) -> str:
    layout = analyze(pdf)
    return render_html(
        title="T",
        lang="en",
        elements=layout.elements,
        fixes=heading_fixes(layout.elements),
        crops={},
    )


def prose(page, y: float, text: str = "The loop below adds the numbers from one to ten.") -> float:
    page.insert_text((72, y), text, fontsize=BODY, fontname="helv")
    return y + 22


def code(page, y: float, lines: list[tuple[int, str]], x: float = 72.0) -> float:
    """Courier lines, each indented by this many columns, 13 pt apart; "" is a blank line."""
    for indent, text in lines:
        if text:
            page.insert_text((x + indent * ADVANCE, y), text, fontsize=CODE, fontname="cour")
        y += 13
    return y + 10


LOOP = [(0, "for i in range(1, 11):"), (4, "total += i"), (8, "print(total)"), (0, "done = True")]


# ----------------------------------------------------------------------------- code


def test_a_run_of_monospaced_lines_is_one_code_block_with_its_indentation():
    def fill(page):
        y = prose(page, 100)
        y = code(page, y, LOOP)
        prose(page, y, "Run it and check that it prints 55.")

    elements = analyze(page_of(fill)).elements
    blocks = [e for e in elements if e.preformatted]
    assert [e.text for e in blocks] == [
        "for i in range(1, 11):\n    total += i\n        print(total)\ndone = True"
    ]
    assert [e.kind for e in elements] == [Kind.PARAGRAPH] * 3  # prose, code, prose


def test_a_blank_line_in_code_is_kept():
    def fill(page):
        y = prose(page, 100)
        code(page, y, [(0, "x = 1"), (0, ""), (0, "y = 2")])

    (block,) = [e for e in analyze(page_of(fill)).elements if e.preformatted]
    assert block.text == "x = 1\n\ny = 2"


def test_code_is_written_as_pre_code_and_escaped():
    def fill(page):
        y = prose(page, 100)
        code(page, y, [(0, "if a < b and c > d:"), (4, 'print("ok & done")')])

    html = html_of(page_of(fill))
    assert (
        '<pre id="p1-e1"><code>if a &lt; b and c &gt; d:\n'
        "    print(&quot;ok &amp; done&quot;)</code></pre>"
    ) in html
    assert "pre { " in html  # the CSS is added because the document has code


def test_a_document_without_code_has_no_code_css():
    html = html_of(page_of(lambda page: prose(page, 100)))
    assert "<pre" not in html and "pre {" not in html and "<dl" not in html


def test_a_wide_code_block_is_a_named_tab_stop_a_narrow_one_is_not():
    def fill(page):
        y = prose(page, 100)
        y = code(page, y, [(0, "x = 1"), (0, "y = 2")])
        y = prose(page, y + 10)
        code(page, y, [(0, "result = compute_the_total(first_value, second_value, third)")])

    html = html_of(page_of(fill))
    assert '<pre id="p1-e1"><code>' in html
    assert 'tabindex="0" role="group" aria-label="Program code"><code>result' in html


def test_one_short_monospaced_word_is_not_a_code_block():
    def fill(page):
        page.insert_text((72, 100), "Type ", fontsize=BODY, fontname="helv")
        page.insert_text((100, 100), "ls", fontsize=BODY, fontname="cour")
        page.insert_text(
            (115, 100), " to list the files in the folder.", fontsize=BODY, fontname="helv"
        )

    assert not [e for e in analyze(page_of(fill)).elements if e.preformatted]


def test_a_box_drawn_round_one_line_of_code_is_not_a_figure():
    def fill(page):
        y = prose(page, 100)
        page.draw_rect(
            pymupdf.Rect(66, y - 12, 520, y + 6), color=(0.6, 0.6, 0.6), fill=(0.95, 0.95, 0.95)
        )
        page.insert_text(
            (72, y),
            "cut -d, -f2 grades.csv | sort -n | uniq -c | head",
            fontsize=CODE,
            fontname="cour",
        )

    elements = analyze(page_of(fill)).elements
    assert Kind.FIGURE not in {e.kind for e in elements}
    assert [e.text for e in elements if e.preformatted] == [
        "cut -d, -f2 grades.csv | sort -n | uniq -c | head"
    ]


# ------------------------------------------------------------------------- glossary


def terms_page(fill_entries):
    def fill(page):
        page.insert_text((72, 80), "Key Terms", fontsize=18, fontname="hebo")
        fill_entries(page)

    return page_of(fill)


def test_a_term_at_the_margin_over_its_indented_definition_is_a_definition_list():
    def entries(page):
        y = 120.0
        for term, lines in [
            ("alpha decay", ["loss of an alpha particle", "during radioactive decay"]),
            ("half-life", ["time for half of the atoms", "in a sample to decay"]),
        ]:
            page.insert_text((72, y), term, fontsize=BODY, fontname="tiro")
            for k, line in enumerate(lines):
                page.insert_text((102, y + 15 * (k + 1)), line, fontsize=BODY, fontname="tiro")
            y += 15 * (len(lines) + 1) + 14

    elements = analyze(terms_page(entries)).elements
    found = [(e.text[: e.term_end], e.text[e.term_end :].strip()) for e in elements if e.term_end]
    assert found == [
        ("alpha decay", "loss of an alpha particle during radioactive decay"),
        ("half-life", "time for half of the atoms in a sample to decay"),
    ]
    html = html_of(terms_page(entries))
    assert (
        '<dl><dt id="p1-e1"><strong>alpha decay</strong></dt>'
        "<dd>loss of an alpha particle during radioactive decay</dd>"
        '<dt id="p1-e2"><strong>half-life</strong></dt>'
        "<dd>time for half of the atoms in a sample to decay</dd></dl>"
    ) in html


def test_bold_terms_over_their_paragraphs_stop_being_headings():
    def entries(page):
        y = 120.0
        for term, definition in [
            ("Loop", "A piece of code that repeats until a condition is met."),
            ("Compiler", "A program that translates source code into machine instructions."),
        ]:
            page.insert_text((72, y), term, fontsize=BODY, fontname="hebo")
            page.insert_text((72, y + 15), definition, fontsize=BODY, fontname="helv")
            y += 15 + 26

    pdf = terms_page(entries)
    elements = analyze(pdf).elements
    assert [e.kind for e in elements] == [Kind.HEADING, Kind.PARAGRAPH, Kind.PARAGRAPH]
    html = html_of(pdf)
    assert '<h2 id="p1-' not in html
    assert html.count("<dt") == 2
    assert '<dt id="p1-e1"><strong>Loop</strong></dt><dd>A piece of code' in html


def test_bold_terms_running_into_their_definitions_make_one_entry_each():
    def entries(page):
        y = 120.0
        for term, definition in [
            ("Limit", "The value that a function approaches."),
            ("Matrix", "A rectangular array of numbers."),
            ("Vector", "A quantity with a size and a direction."),
        ]:
            page.insert_text((72, y), term, fontsize=BODY, fontname="hebo")
            width = pymupdf.get_text_length(term + " ", fontname="hebo", fontsize=BODY)
            page.insert_text((72 + width, y), definition, fontsize=BODY, fontname="helv")
            y += 15  # no space between entries

    elements = analyze(terms_page(entries)).elements
    found = [(e.text[: e.term_end], e.text[e.term_end :].strip()) for e in elements if e.term_end]
    assert found == [
        ("Limit", "The value that a function approaches."),
        ("Matrix", "A rectangular array of numbers."),
        ("Vector", "A quantity with a size and a direction."),
    ]


def test_text_outside_a_glossary_section_is_not_made_an_entry():
    def fill(page):
        page.insert_text((72, 80), "Notes", fontsize=18, fontname="hebo")
        page.insert_text((72, 120), "Limit", fontsize=BODY, fontname="hebo")
        page.insert_text(
            (72, 135), "The value that a function approaches.", fontsize=BODY, fontname="helv"
        )

    assert not [e for e in analyze(page_of(fill)).elements if e.term_end]


def test_a_paragraph_that_only_looks_like_an_entry_is_left_alone():
    def entries(page):  # a first-line indent: the first line is the one that is indented
        page.insert_text(
            (102, 120),
            "Some words at the start of a paragraph that goes",
            fontsize=BODY,
            fontname="tiro",
        )
        page.insert_text(
            (72, 135),
            "on to the margin on its second line and ends.",
            fontsize=BODY,
            fontname="tiro",
        )

    assert not [e for e in analyze(terms_page(entries)).elements if e.term_end]


def test_an_entry_is_written_as_dt_and_dd_by_the_assembler():
    entry = Element(
        id="p1-e1",
        kind=Kind.PARAGRAPH,
        page=0,
        bbox=(72, 100, 300, 130),
        order=0,
        text="beta decay breakdown of a neutron <into> a proton",
        term_end=10,
    )
    html = render_html(title="T", lang="en", elements=[entry], fixes=[], crops={})
    assert (
        '<dl><dt id="p1-e1"><strong>beta decay</strong></dt>'
        "<dd>breakdown of a neutron &lt;into&gt; a proton</dd></dl>"
    ) in html
    broken = entry.model_copy(update={"term_end": 99})  # a bad offset: a plain paragraph
    assert "<dl" not in render_html(title="T", lang="en", elements=[broken], fixes=[], crops={})


# ------------------------------------------------------------------------ footnotes


def footnote_pdf() -> bytes:
    def fill(page):
        lead = "Water boils at one hundred degrees"
        page.insert_text(
            (72, 100),
            "The boiling point of water depends on the pressure above it.",
            fontsize=BODY,
            fontname="helv",
        )
        page.insert_text((72, 115), lead, fontsize=BODY, fontname="helv")
        width = pymupdf.get_text_length(lead, fontname="helv", fontsize=BODY)
        page.insert_text((72 + width + 0.5, 110), "1", fontsize=7, fontname="helv")
        page.insert_text((72 + width + 5, 115), " at sea level.", fontsize=BODY, fontname="helv")
        page.draw_line((72, 720), (200, 720))
        page.insert_text((72, 735), "1 Pressure matters.", fontsize=8)

    return page_of(fill)


def test_a_footnote_mark_links_to_its_footnote():
    layout = analyze(footnote_pdf())
    body, note = layout.elements
    assert note.text.startswith("1 Pressure")
    (link,) = body.links
    assert (link.kind, link.target_id) == ("internal", note.id)
    assert (body.text or "")[link.start : link.end] == "1"
    html = html_of(footnote_pdf())
    # the mark is raised text too (T2-4): the link wraps the <sup>
    assert f'<a href="#{note.id}"><sup>1</sup></a>' in html
    assert f'<p id="{note.id}">1 Pressure matters.</p>' in html


def test_a_mark_that_cannot_be_found_once_gets_no_link():
    layout = analyze(footnote_pdf())
    body, note = layout.elements
    twice = body.model_copy(update={"text": "x", "links": []})
    from readablestem.pipeline import layout as layout_module

    marks = {note.id: layout_module._FootnoteMark(body.id, "no such text", "1")}
    assert layout_module._footnote_links([twice, note], marks)[0].links == []


# ------------------------------------------------------------------------- contents


def contents_pdf() -> bytes:
    def fill(page):
        page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        y = 120.0
        for indent, title, number in [
            (0, "1 Motion", "3"),
            (16, "1.1 Speed", "4"),
            (16, "1.2 Velocity", "9"),
            (0, "2 Forces", "15"),
        ]:
            dots = " ." * 40
            page.insert_text((72 + indent, y), title + dots, fontsize=BODY, fontname="helv")
            page.insert_text((520, y), number, fontsize=BODY, fontname="helv")
            y += 18
        page.insert_text((72, 300), "1 Motion", fontsize=16, fontname="hebo")
        page.insert_text((72, 330), "1.1 Speed", fontsize=13, fontname="hebo")

    return page_of(fill)


def test_a_contents_page_with_dot_leaders_is_a_list_of_titles_without_leaders_or_numbers():
    elements = analyze(contents_pdf()).elements
    entries = [e for e in elements if e.kind == Kind.LIST_ITEM]
    assert [(e.text, e.list_level) for e in entries] == [
        ("1 Motion", 1),
        ("1.1 Speed", 2),
        ("1.2 Velocity", 2),
        ("2 Forces", 1),
    ]
    assert not [e for e in elements if e.kind == Kind.PARAGRAPH]  # no stray page numbers


def test_a_contents_entry_links_to_the_heading_it_names_once():
    elements = analyze(contents_pdf()).elements
    by_text = {e.text: e for e in elements if e.kind == Kind.LIST_ITEM}
    motion = by_text["1 Motion"]
    # Two entries share a title with a heading ("1 Motion", "1.1 Speed"); "1.2" and "2" name none.
    heads = [e for e in elements if e.kind == Kind.HEADING and e.text != "Contents"]
    assert [h.text for h in heads] == ["1 Motion", "1.1 Speed"]
    assert [(k.kind, k.target_id) for k in motion.links] == [("internal", heads[0].id)]
    assert by_text["1.2 Velocity"].links == [] and by_text["2 Forces"].links == []


def test_a_contents_page_is_written_as_nested_links():
    html = html_of(contents_pdf())
    assert '<ul><li id="p1-e1"><a href="#p1-e5">1 Motion</a><ul><li id="p1-e2">' in html
    assert ". . ." not in html


def test_a_page_with_one_dotted_line_is_not_a_contents_page():
    def fill(page):
        page.insert_text(
            (72, 100),
            "The answer is given on page " + ". " * 10 + "12",
            fontsize=BODY,
            fontname="helv",
        )

    assert [e.kind for e in analyze(page_of(fill)).elements] == [Kind.PARAGRAPH]


def test_chapter_lines_with_their_number_at_the_margin_are_entries_on_a_contents_page():
    def fill(page):
        page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        page.insert_text((72, 120), "1 Motion", fontsize=BODY, fontname="hebo")
        page.insert_text((520, 120), "1", fontsize=BODY, fontname="helv")
        for i, title in enumerate(["1.1 Speed", "1.2 Velocity"]):
            page.insert_text((88, 138 + 18 * i), title + " ." * 40, fontsize=BODY, fontname="helv")
            page.insert_text((520, 138 + 18 * i), "1", fontsize=BODY, fontname="helv")

    entries = [e for e in analyze(page_of(fill)).elements if e.kind == Kind.LIST_ITEM]
    assert [(e.text, e.list_level) for e in entries] == [
        ("1 Motion", 1),
        ("1.1 Speed", 2),
        ("1.2 Velocity", 2),
    ]


# ------------------------------------------------------------------------- axe


def test_code_definitions_and_contents_pass_axe_in_chromium():
    import pytest

    from readablestem.pipeline.html_checks import axe_checks

    def fill(page):
        page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        for i, title in enumerate(["1 Motion", "2 Forces"]):
            page.insert_text((72, 110 + 18 * i), title + " ." * 30, fontsize=BODY, fontname="helv")
            page.insert_text((520, 110 + 18 * i), str(3 + 4 * i), fontsize=BODY, fontname="helv")
        y = prose(page, 200)
        code(page, y, [(0, "result = compute_the_total(first_value, second_value, third_value)")])
        page.insert_text((72, 300), "Key Terms", fontsize=18, fontname="hebo")
        page.insert_text((72, 330), "Loop", fontsize=BODY, fontname="hebo")
        page.insert_text((72, 345), "A piece of code that repeats.", fontsize=BODY, fontname="helv")

    checks = axe_checks(html_of(page_of(fill)))
    if checks == [] or (len(checks) == 1 and checks[0].code == "skipped"):
        pytest.skip("axe-core needs Playwright and its Chromium")
    assert [c.name for c in checks] == ["axe"] and checks[0].passed
