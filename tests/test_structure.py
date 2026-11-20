"""Code blocks, glossary entries and footnote links (T2-16): layout, then the HTML.

Code is `<pre><code>` with its lines and indentation; a glossary is a `<dl>`; a footnote's
mark in the text links to the footnote. Pages are drawn with PyMuPDF so each shape is exact.
"""

from __future__ import annotations

import pymupdf

from readablestem.core.types import Kind
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


def test_a_wide_code_block_is_a_named_tab_stop_a_narrow_one_is_not():
    def fill(page):
        y = prose(page, 100)
        y = code(page, y, [(0, "x = 1"), (0, "y = 2")])
        y = prose(page, y + 10)
        y = code(page, y, [(0, "result = compute_the_total(first_value, second_value, third)")])
        y = prose(page, y + 10)
        # 26 characters: scrolls at 320 px with text spacing (F-448), so it is a stop too
        code(page, y, [(0, "total = add(first, second)")])

    html = html_of(page_of(fill))
    assert '<pre id="p1-e1"><code>' in html
    assert 'tabindex="0" role="group" aria-label="Program code"><code>result' in html
    assert 'aria-label="Program code"><code>total = add(first, second)' in html


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


def test_a_monospaced_quotation_without_code_punctuation_stays_prose():
    quotation = [
        "The poem works because it never says what the woods mean.",
        "The reader supplies the meaning, and the poem lets each reader",
        "supply a different one.",
    ]

    def fill(page):
        y = prose(page, 100)
        for line in quotation:
            page.insert_text((72, y), line, fontsize=CODE, fontname="cour")
            y += 13
        y = code(page, y + 60, [(0, "total = add(first, second)"), (4, "print(total)")])

    elements = analyze(page_of(fill)).elements
    assert [e.text for e in elements if e.preformatted] == [
        "total = add(first, second)\n    print(total)"
    ]
    assert any((e.text or "").startswith("The poem works") for e in elements)


# ------------------------------------------------------------------- kept line ends


def column(page, y: float, text: str, width: float = 468.0) -> float:
    """A justified-width paragraph wrapped by PyMuPDF, 14 pt a line."""
    rect = pymupdf.Rect(72, y, 72 + width, y + 200)
    page.insert_textbox(rect, text, fontsize=BODY, fontname="helv", lineheight=14 / BODY)
    return y + 14 * 4 + 12


def lines_at(page, y: float, lines: list[str], x: float = 72.0) -> float:
    for text in lines:
        page.insert_text((x, y), text, fontsize=BODY, fontname="helv")
        y += 14
    return y + 8


LONG = (
    "The poem works because it never says what the woods mean, and the reader supplies the "
    "meaning. It lets each reader supply a different one, and the essay goes on to compare "
    "that choice with two other poems from the same book before it turns to the last stanza."
)
VERSE = [
    "Whose woods these are I think I know.",
    "His house is in the village though;",
    "He will not see me stopping here",
    "To watch his woods fill up with snow.",
]


WRAPPED = ["Round the answer to three", "decimal places and say what", "the year depends on."]


def breaks(element) -> list[int]:
    return [r.start for r in element.runs if r.kind == "break"]


def test_short_lines_keep_their_line_ends_and_a_paragraph_that_ends_short_does_not():
    def fill(page):
        y = column(page, 100, LONG)
        y = lines_at(page, y + 6, VERSE)
        lines_at(page, y + 20, WRAPPED)  # a narrow paragraph, wrapped in mid-sentence

    elements = analyze(page_of(fill)).elements
    paragraph, verse, narrow = [e for e in elements if e.kind is Kind.PARAGRAPH]
    assert breaks(paragraph) == [] and breaks(narrow) == []
    assert verse.text == " ".join(VERSE)
    assert [verse.text[at] for at in breaks(verse)] == [" "] * 3
    assert breaks(verse) == [len(VERSE[0]), len(" ".join(VERSE[:2])), len(" ".join(VERSE[:3]))]
    assert html_of(page_of(fill)).count("<br>") == 3


def test_two_short_lines_are_not_enough_to_keep_line_ends():
    def fill(page):
        y = column(page, 100, LONG)
        lines_at(page, y + 6, VERSE[:2])

    assert not [e for e in analyze(page_of(fill)).elements if breaks(e)]


# ---------------------------------------------------------------- abstract and keywords


def test_abstract_alone_on_its_line_is_a_heading_and_keywords_start_their_own_block():
    def fill(page):
        page.insert_text((72, 80), "A Study of Fog", fontsize=20, fontname="hebo")
        page.insert_text((72, 130), "Abstract", fontsize=BODY, fontname="hebo")
        y = column(page, 150, LONG)
        # one more line of the abstract, then the keywords at the same indent, no gap
        page.insert_text((72, y - 12 - 14 + 14), "Keywords: fog, verse, reading", fontsize=BODY,
                         fontname="helv")  # fmt: skip

    elements = analyze(page_of(fill)).elements
    kinds = [(e.kind, e.text) for e in elements]
    assert (Kind.HEADING, "Abstract") in kinds
    texts = [e.text for e in elements if e.kind is Kind.PARAGRAPH]
    assert texts[-1] == "Keywords: fog, verse, reading"
    assert texts[0].startswith("The poem works")
    assert "Keywords" not in texts[0]


def test_abstract_at_the_start_of_a_sentence_is_not_a_heading():
    def fill(page):
        y = column(page, 100, "Abstract art is the subject of this chapter. " + LONG)
        prose(page, y)

    assert Kind.HEADING not in {e.kind for e in analyze(page_of(fill)).elements}


# ------------------------------------------------------------------------ algorithms

ALGORITHM = [
    (0, "input: a line of verse"),
    (0, "count = 0"),
    (0, "for each word w in the line do"),
    (1, "if w is stressed then"),
    (2, "count = count + 1"),
    (1, "end if"),
    (0, "end for"),
    (0, "return count"),
]


def algorithm(page, y: float, steps=ALGORITHM, caption="Algorithm 1 Count stressed syllables"):
    page.insert_text((72, y), caption, fontsize=BODY, fontname="helv")
    y += 14
    for level, text in steps:
        page.insert_text((72 + 11 * level, y), text, fontsize=BODY, fontname="helv")
        y += 14
    return y + 10


def test_an_algorithm_keeps_its_lines_and_three_indent_levels():
    def fill(page):
        y = prose(page, 100)
        y = algorithm(page, y + 10)
        prose(page, y + 10, "The count is printed after the last word.")

    elements = analyze(page_of(fill)).elements
    steps = [e for e in elements if e.preformatted]
    assert len(steps) == 1
    assert steps[0].text.split("\n") == [
        "input: a line of verse",
        "count = 0",
        "for each word w in the line do",
        "  if w is stressed then",
        "    count = count + 1",
        "  end if",
        "end for",
        "return count",
    ]
    captions = [e.text for e in elements if (e.text or "").startswith("Algorithm 1")]
    assert captions == ["Algorithm 1 Count stressed syllables"]  # its own block, above


def test_a_sentence_that_begins_with_algorithm_and_a_number_is_not_one():
    def fill(page):
        y = prose(page, 100, "Algorithm 1 shows how the verse is scanned for stress.")
        prose(page, y, "Each word is checked once, and the count is kept at the end.")
        prose(page, y + 22, "The result is then compared with the reader's own marks.")

    assert not [e for e in analyze(page_of(fill)).elements if e.preformatted]


NUMBERED = [
    (0, "count = 0"),
    (0, "for each word w in the line do"),
    (1, "if w is stressed then"),
    (2, "count = count + 1"),
    (1, "end if"),
    (0, "end for"),
    (0, "return count"),
]


def numbered_algorithm(
    page, y: float, steps=NUMBERED, caption="Algorithm 2 Scan a verse", wrapped=None
):
    """Steps numbered "1:", "2:" in a left gutter, indented by level; a step listed in `wrapped`
    goes on at the left edge of the text on a second line."""
    if caption:
        page.insert_text((72, y), caption, fontsize=BODY, fontname="helv")
        y += 14
    for n, (level, text) in enumerate(steps, 1):
        page.insert_text((72, y), f"{n}:", fontsize=BODY - 1, fontname="helv")
        page.insert_text((95 + 11 * level, y), text, fontsize=BODY, fontname="helv")
        y += 14
        if wrapped and n in wrapped:
            page.insert_text((95, y), wrapped[n], fontsize=BODY, fontname="helv")
            y += 14
    return y + 10


NUMBERED_LINES = [
    "1: count = 0",
    "2: for each word w in the line do",
    "3:   if w is stressed then",
    "4:     count = count + 1",
    "5:   end if",
    "6: end for",
    "7: return count",
]


def test_numbered_steps_keep_their_numbers_apart_and_their_indentation_with_or_without_a_caption():
    for caption in ("Algorithm 2 Scan a verse", None):

        def fill(page, caption=caption):
            y = prose(page, 100)
            y = numbered_algorithm(page, y + 10, caption=caption)
            prose(page, y + 10, "The count is printed after the last word.")

        elements = analyze(page_of(fill)).elements
        steps = [e for e in elements if e.preformatted]
        assert len(steps) == 1 and steps[0].text.split("\n") == NUMBERED_LINES
        assert caption is None or any(e.text == caption for e in elements)


def test_a_step_that_wraps_back_to_the_left_edge_stays_one_step():
    def fill(page):
        y = prose(page, 100)
        long = "count = count + 1 and then add the length of the word to the running total"
        steps = [*NUMBERED[:3], (2, long), *NUMBERED[4:]]
        y = numbered_algorithm(page, y + 10, steps, wrapped={4: "of the verse so far"})
        prose(page, y + 10, "The count is printed after the last word.")

    steps = [e for e in analyze(page_of(fill)).elements if e.preformatted]
    assert len(steps) == 1
    lines = steps[0].text.split("\n")
    assert len(lines) == 7
    assert lines[3] == (
        "4:     count = count + 1 and then add the length of the word to the running total"
        " of the verse so far"
    )


def test_an_equation_inside_an_algorithm_leaves_two_blocks_with_the_same_indent_levels():
    def fill(page):
        y = prose(page, 100)
        page.insert_text((72, y + 10), "Algorithm 3 Scan a verse", fontsize=BODY, fontname="helv")
        y += 24
        for n, (level, text) in enumerate(NUMBERED, 1):
            page.insert_text((72, y), f"{n}:", fontsize=BODY - 1, fontname="helv")
            page.insert_text((95 + 11 * level, y), text, fontsize=BODY, fontname="helv")
            y += 14
            if n == 3:  # a display equation with a fraction bar sits between steps 3 and 4
                page.insert_text((280, y + 14), "v =", fontsize=12)
                page.insert_text((306, y + 6), "d", fontsize=12)
                page.draw_line((302, y + 10), (316, y + 10), width=0.6)
                page.insert_text((306, y + 23), "t", fontsize=12)
                y += 40
        prose(page, y + 10, "The count is printed after the last word.")

    elements = analyze(page_of(fill)).elements
    assert [(e.kind, bool(e.preformatted)) for e in elements][2:5] == [
        (Kind.PARAGRAPH, True),
        (Kind.EQUATION, False),
        (Kind.PARAGRAPH, True),
    ]
    assert [e.text.split("\n") for e in elements if e.preformatted] == [
        NUMBERED_LINES[:3],
        NUMBERED_LINES[3:],
    ]


def test_numbered_exercises_and_a_numbered_table_are_not_algorithms():
    def exercises(page):
        y = prose(
            page, 100, "Ask each student to do the following exercises before the next class."
        )
        for n, text in enumerate(
            [
                "Describe how a verse is scanned for stress marks.",
                "Explain why the count of stressed syllables matters.",
                "Compare the scansion of two poems by the same author.",
                "Write a short note on what you found and why it matters.",
            ],
            1,
        ):
            page.insert_text((72, y), f"{n}.", fontsize=BODY, fontname="helv")
            page.insert_text((95, y), text, fontsize=BODY, fontname="helv")
            y += 14
        prose(page, y + 10, "Hand your answers in by Friday.")

    def table(page):
        y = 100.0
        rows = [("Alpha", "low"), ("Beta", "high"), ("Gamma", "mid"), ("Delta", "low")]
        for n, (name, level) in enumerate(rows, 1):
            page.insert_text((72, y), str(n), fontsize=BODY, fontname="helv")
            page.insert_text((120, y), name, fontsize=BODY, fontname="helv")
            page.insert_text((250, y), level, fontsize=BODY, fontname="helv")
            y += 16

    for fill in (exercises, table):
        assert not [e for e in analyze(page_of(fill)).elements if e.preformatted]


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


def test_text_outside_a_glossary_section_is_not_made_an_entry():
    def fill(page):
        page.insert_text((72, 80), "Notes", fontsize=18, fontname="hebo")
        page.insert_text((72, 120), "Limit", fontsize=BODY, fontname="hebo")
        page.insert_text(
            (72, 135), "The value that a function approaches.", fontsize=BODY, fontname="helv"
        )

    assert not [e for e in analyze(page_of(fill)).elements if e.term_end]


def lead_term_page(entries: list[tuple[str, str]]) -> bytes:
    def fill(page):
        page.insert_text((72, 80), "Notes", fontsize=18, fontname="hebo")
        y = 120.0
        for term, definition in entries:
            page.insert_text((72, y), term, fontsize=BODY, fontname="hebo")
            gap = pymupdf.get_text_length(term + " ", fontname="hebo", fontsize=BODY)
            page.insert_text((72 + gap, y), definition, fontsize=BODY, fontname="helv")
            y += 15

    return page_of(fill)


def test_three_lines_that_open_with_a_bold_lead_term_are_entries_not_one_paragraph():
    entries = [
        ("Impulse.", "the change in momentum of a body."),
        ("Inertia.", "the resistance of a body to a change in motion."),
        ("Momentum.", "the product of mass and velocity."),
    ]
    elements = analyze(lead_term_page(entries)).elements
    found = [e.text[: e.term_end].strip(" .:") for e in elements if e.term_end]
    assert found == ["Impulse", "Inertia", "Momentum"]
    # Two such lines are a sentence with a bold start, not a list of entries.
    assert not [e for e in analyze(lead_term_page(entries[:2])).elements if e.term_end]
    # A theorem, its proof and a case open with a lead too: three blocks, no list of terms.
    steps = [
        ("Theorem 2.", "every bounded sequence has a convergent subsequence."),
        ("Proof.", "split the interval in two and keep a half with many terms."),
        ("Case 1:", "the left half holds infinitely many terms of the sequence."),
    ]
    blocks = [e for e in analyze(lead_term_page(steps)).elements if e.kind == Kind.PARAGRAPH]
    assert len(blocks) == 3 and not [e for e in blocks if e.term_end]


def test_capital_letters_alone_on_their_lines_head_the_sections_of_an_index():
    def fill(page):
        page.insert_text((72, 80), "Index", fontsize=18, fontname="hebo")
        y = 120.0
        for letter, words in [
            ("A", ["acceleration, 4", "angle, 9"]),
            ("B", ["body, 12", "buoyancy, 15"]),
            ("C", ["circuit, 20", "current, 22"]),
        ]:
            page.insert_text((72, y), letter, fontsize=BODY, fontname="hebo")
            y += 18
            for word in words:
                page.insert_text((72, y), word, fontsize=BODY, fontname="helv")
                y += 14
            y += 10

    fixes = heading_fixes(analyze(page_of(fill)).elements)
    headings = [(f.heading.text, f.heading.level) for f in fixes if f.heading]
    assert headings == [("Index", 1), ("A", 2), ("B", 2), ("C", 2)]


def test_a_hanging_indent_shared_by_entries_makes_each_entry_one_block():
    filler = ["of", "the", "in", "on", "at", "to", "it", "is", "by"] * 2

    def line(width: float, start: str) -> str:
        words = [start]
        for word in filler * 3:
            if (
                pymupdf.get_text_length(" ".join([*words, word]), fontname="tiro", fontsize=BODY)
                > width
            ):
                break
            words.append(word)
        return " ".join(words)

    def fill(page):
        page.insert_text((72, 80), "References", fontsize=18, fontname="hebo")
        y = 120.0
        for k in range(4):
            page.insert_text(
                (72, y), line(468, f"Smith {k}, J., A study"), fontsize=BODY, fontname="tiro"
            )
            page.insert_text((96, y + 13), line(444, "and its"), fontsize=BODY, fontname="tiro")
            page.insert_text((96, y + 26), f"pages 10-{k}0 (2001).", fontsize=BODY, fontname="tiro")
            y += 39

    paragraphs = [e.text for e in analyze(page_of(fill)).elements if e.kind == Kind.PARAGRAPH]
    assert [p[:8] for p in paragraphs] == [f"Smith {k}," for k in range(4)]
    assert all(p.endswith("(2001).") for p in paragraphs)


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
    # the glyphs an inline equation took from the line are a placeholder in the text
    held = body.model_copy(update={"text": "so ⟦p1-e9⟧.1 Next", "links": []})
    marks = {note.id: layout_module._FootnoteMark(body.id, "x + y.", "1")}
    (link,) = layout_module._footnote_links([held, note], marks)[0].links
    assert (held.text or "")[link.start : link.end] == "1"
    assert link.start == (held.text or "").index(".1 Next") + 1
    # ... and a tail of the context is looked for only after one, never inside its name
    inside = body.model_copy(update={"text": "aim to map ⟦p1-e21⟧ across them", "links": []})
    marks = {note.id: layout_module._FootnoteMark(body.id, "here K > 2", "1")}
    assert layout_module._footnote_links([inside, note], marks)[0].links == []


def named_footnote_pdf() -> bytes:
    """A page whose file names its footnote the way hyperref does (a destination `Hfootnote.2`
    above the note's line, a link to it from the mark), with a numbered line in small type low
    on the page that no destination points at, and a mark touching the word after it."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 85), "A sentence long enough to make a paragraph.", fontsize=BODY)
    page.insert_text(
        (72, 100), "The pressure matters a great deal here.", fontsize=BODY, fontname="helv"
    )
    width = pymupdf.get_text_length(
        "The pressure matters a great deal here.", fontname="helv", fontsize=BODY
    )
    page.insert_text((72 + width, 95), "2", fontsize=7, fontname="helv")
    page.insert_text((72 + width + 4.5, 100), "Next we measure it.", fontsize=BODY, fontname="helv")
    # a line of terms becomes list items made from its text, with no rows of their own
    page.insert_text((72, 160), "Section Key Terms", fontsize=16, fontname="hebo")
    x = 72.0
    for term in ("absolute pressure", "gauge pressure", "pascal"):
        page.insert_text((x, 190), term, fontsize=BODY)
        x += pymupdf.get_text_length(term, fontname="helv", fontsize=BODY) + 13.0
    page.draw_line((72, 700), (200, 700))
    page.insert_text((72, 715), "1 A line number in small type.", fontsize=8)
    page.insert_text((72, 735), "2 Pressure is measured in pascals.", fontsize=8)
    names = doc.get_new_xref()
    doc.update_object(names, f"<< /Names [(Hfootnote.2) [{page.xref} 0 R /XYZ 72 66 null]] >>")
    tree = doc.get_new_xref()
    doc.update_object(tree, f"<< /Dests {names} 0 R >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"{tree} 0 R")
    annot = doc.get_new_xref()
    x0, x1 = 72 + width, 72 + width + 4.5
    doc.update_object(
        annot,
        f"<< /Type /Annot /Subtype /Link /Rect [{x0} {792 - 102} {x1} {792 - 88}] "
        "/Border [0 0 0] /A << /S /GoTo /D (Hfootnote.2) >> >>",
    )
    doc.xref_set_key(page.xref, "Annots", f"[{annot} 0 R]")
    return doc.tobytes()


def test_a_file_that_names_its_footnotes_decides_which_blocks_are_footnotes():
    layout = analyze(named_footnote_pdf())
    by_id = {e.id: e for e in layout.elements}
    notes = [by_id[n.element_id] for n in layout.notices if n.code == "footnote"]
    # the destination's line is the footnote; the numbered small line next to it is not one
    assert [n.text for n in notes] == ["2 Pressure is measured in pascals."]
    body = layout.elements[0]
    (link,) = body.links
    assert (link.kind, link.target_id) == ("internal", notes[0].id)
    # the raised mark is set off from the word it touches, not read as part of it
    assert "here.2 Next we" in (body.text or "")
    # a list made from a line of terms is read too (it stopped the whole document once)
    terms = [e.text for e in layout.elements if e.kind == Kind.LIST_ITEM]
    assert terms == ["absolute pressure", "gauge pressure", "pascal"]


def named_notes_pdf() -> bytes:
    """Two columns, with the file naming two footnotes the way hyperref does. The note at the
    foot of the left column ends without a full stop and the right column opens with a
    lower-case word. Its destination (`Hfootnote.1`) sits a little under its line; the one for
    the symbol note in the title block (`Hfootnote.2`) points at the corner of the page."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for k in range(6):
        left = f"Left column line {k} of ordinary text here."
        page.insert_text((72, 100 + 14 * k), left, fontsize=BODY)
        right = "where the right column goes on" if k == 0 else f"Right column line {k} is here."
        page.insert_text((330, 100 + 14 * k), right, fontsize=BODY)
    page.insert_text((72, 60), "A line of the author block with a note", fontsize=BODY)
    page.insert_text((72 + 175, 56), "*", fontsize=7, fontname="helv")
    page.insert_text((72 + 205, 166), "1", fontsize=7, fontname="helv")
    page.draw_line((72, 700), (200, 700))
    page.insert_text((72, 712), "1", fontsize=5.5, fontname="helv")
    page.insert_text((77, 715), "https://example.org/linmix", fontsize=8, fontname="helv")
    page.insert_text((72, 735), "*", fontsize=5.5, fontname="helv")
    page.insert_text((77, 738), "Corresponding author", fontsize=8, fontname="helv")
    names = doc.get_new_xref()
    # (72, 77) from the bottom is 715 from the top: under the one-line note, not above it
    doc.update_object(
        names,
        f"<< /Names [(Hfootnote.1) [{page.xref} 0 R /XYZ 72 76.5 null] "
        f"(Hfootnote.2) [{page.xref} 0 R /XYZ 0 0 null]] >>",
    )
    tree = doc.get_new_xref()
    doc.update_object(tree, f"<< /Dests {names} 0 R >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"{tree} 0 R")
    refs = []
    for x0, x1, top, bottom, name in (
        (72 + 205, 72 + 209, 160, 168, "Hfootnote.1"),
        (72 + 175, 72 + 179, 50, 58, "Hfootnote.2"),
    ):
        annot = doc.get_new_xref()
        doc.update_object(
            annot,
            f"<< /Type /Annot /Subtype /Link /Rect [{x0} {792 - bottom} {x1} {792 - top}] "
            f"/Border [0 0 0] /A << /S /GoTo /D ({name}) >> >>",
        )
        refs.append(f"{annot} 0 R")
    doc.xref_set_key(page.xref, "Annots", f"[{' '.join(refs)}]")
    return doc.tobytes()


def test_named_footnotes_the_destination_does_not_sit_above_are_still_found():
    layout = analyze(named_notes_pdf())
    by_id = {e.id: e for e in layout.elements}
    notes = [by_id[n.element_id] for n in layout.notices if n.code == "footnote"]
    texts = sorted((n.text or "")[:12] for n in notes)
    # both notes are found, each alone: the first is not run into the right column
    assert texts == ["* Correspond", "1 https://ex"]
    assert all(len(n.text or "") < 40 for n in notes)
    right = next(e for e in layout.elements if (e.text or "").startswith("where the right"))
    assert right.kind is Kind.PARAGRAPH
    linked = {link.target_id for e in layout.elements for link in e.links}
    assert {n.id for n in notes} <= linked


# ------------------------------------------------------------------ where a block starts


def note_lines(page):
    """Two notes at the foot of the page, each starting with its raised mark."""
    y = column(page, 100, LONG)
    for number, text in (("1", "The first note is short."), ("2", "The second note follows it.")):
        y = 700.0 if number == "1" else y
        page.insert_text((72, y - 3), number, fontsize=5.5, fontname="helv")
        page.insert_text((77, y), text, fontsize=8, fontname="helv")
        y += 10


def theorem_lines(page):
    y = column(page, 100, LONG)
    lines_at(
        page,
        y - 10,
        ["Definition 2. A graph is connected when any two", "of its vertices are linked."],
    )
    page.insert_text((72, y + 14), "Proof. Take two vertices and follow the path.", fontsize=BODY)


def step_lines(page):
    y = column(page, 100, LONG)
    lines_at(page, y, ["1 Read the line", "2 Count the words", "3 Print the sum"])


def test_a_note_a_theorem_and_a_numbered_step_each_start_a_block():
    starts = {
        note_lines: [
            "The poem works",
            "1The first note is short.",
            "2The second note follows it.",
        ],
        theorem_lines: ["The poem works", "Definition 2.", "Proof."],
        step_lines: ["The poem works", "1 Read the line", "2 Count the words", "3 Print the sum"],
    }
    for fill, expected in starts.items():
        paragraphs = [e.text for e in analyze(page_of(fill)).elements if e.kind is Kind.PARAGRAPH]
        assert [p[: len(want)] for p, want in zip(paragraphs, expected, strict=False)] == expected
        assert len(paragraphs) == len(expected), fill.__name__


def test_a_reference_to_a_theorem_or_a_raised_digit_inside_a_sentence_goes_on():
    def fill(page):
        y = column(page, 100, LONG)
        # lines that run to the right edge and end in the middle of a sentence
        for first, rest in (
            (
                "The result follows from the argument of the second section and from",
                "Theorem 1.1. It",
            ),
            ("The proof of the sharper bound in the third section compares it with", "14"),
        ):
            words = first.split()
            while pymupdf.get_text_length(" ".join(words), fontname="helv", fontsize=BODY) < 456:
                words.append("and")
            page.insert_text((72, y), " ".join(words), fontsize=BODY, fontname="helv")
            if rest[0].isdigit():
                page.insert_text((72, y + 11), rest, fontsize=6, fontname="helv")
                page.insert_text((82, y + 14), "C, which decays slowly.", fontsize=BODY)
            else:
                page.insert_text((72, y + 14), rest + " is sharp.", fontsize=BODY, fontname="helv")
            y += 40

    paragraphs = [e.text for e in analyze(page_of(fill)).elements if e.kind is Kind.PARAGRAPH]
    assert len(paragraphs) == 3  # the poem paragraph, and two sentences of two lines each


def test_the_numbers_in_the_margin_of_every_line_do_not_start_blocks():
    def fill(page):
        words = LONG.split()
        for line in range(8):  # ragged lines, each with the next number set in the margin
            y = 100 + 20 * line
            start = (line * 5) % 12
            page.insert_text((96, y), " ".join(words[start : start + 9 + line % 3]), fontsize=BODY)
            page.insert_text((60, y), str(1 + line), fontsize=8)

    paragraphs = [e.text for e in analyze(page_of(fill)).elements if e.kind is Kind.PARAGRAPH]
    assert len(paragraphs) == 1


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


def numbers_only_contents(page_index: int = 0, heading: bool = True) -> bytes:
    """A contents list with no leaders: each title at the margin and its page number apart,
    at the right edge, and a real section heading right under the list."""

    def fill(page):
        if heading:
            page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        y = 120.0
        for title, number in [
            ("Motion", "3"),
            ("Forces", "9"),
            ("Energy", "15"),
            ("Waves", "21"),
            ("Light", "30"),
            ("Heat", "41"),
            ("Fields", "52"),
            ("Atoms", "63"),
        ]:
            page.insert_text((72, y), title, fontsize=BODY, fontname="helv")
            width = pymupdf.get_text_length(number, fontname="helv", fontsize=BODY)
            page.insert_text((540 - width, y), number, fontsize=BODY, fontname="helv")
            y += 18
        page.insert_text((72, y + 14), "Motion", fontsize=16, fontname="hebo")
        page.insert_text((72, y + 40), "A body stays at rest.", fontsize=BODY, fontname="helv")

    doc = pymupdf.open()
    for _ in range(page_index):
        doc.new_page(width=612, height=792)
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def test_a_contents_list_with_no_leaders_keeps_its_numbers_and_is_not_a_table():
    elements = analyze(numbers_only_contents()).elements
    assert [e.kind for e in elements] == [Kind.HEADING] + [Kind.LIST_ITEM] * 8 + [
        Kind.HEADING,
        Kind.PARAGRAPH,
    ]
    entries = elements[1:9]
    assert [e.text for e in entries[:3]] == ["Motion 3", "Forces 9", "Energy 15"]
    # "Motion" under the list is the real section heading; the first entry links to it.
    assert [(k.kind, k.target_id) for k in entries[0].links] == [("internal", elements[9].id)]


def test_lines_that_end_in_numbers_far_from_the_front_are_not_a_contents_list():
    elements = analyze(numbers_only_contents(page_index=5, heading=False)).elements
    assert not [e for e in elements if "contents entry" in e.reasons]


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
