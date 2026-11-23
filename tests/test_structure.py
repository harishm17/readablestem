"""Code blocks, glossary entries and footnote links (T2-16): layout, then the HTML.

Code is `<pre><code>` with its lines and indentation; a glossary is a `<dl>`; a footnote's
mark in the text links to the footnote. Pages are drawn with PyMuPDF so each shape is exact.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import ConversionOptions, Kind
from readablestem.pipeline import options as run_options
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

RUNNING = (
    "It reads the verse once from left to right and keeps a single running count of the stressed|"
    "syllables for every word it meets, adding one to the count whenever the word carries a|"
    "stress, and moves on when it does not. The count is printed at the end of the verse."
)


def running(page, y: float, text: str, pitch: float = 13.5, indent: float = 0.0) -> float:
    """The lines of one paragraph ("|" ends a line), set one under the other, the first one
    `indent` further right."""
    for n, line in enumerate(text.split("|")):
        x = 72 + (indent if n == 0 else 0.0)
        page.insert_text((x, y), line, fontsize=BODY, fontname="helv")
        y += pitch
    return y + 8


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
    if caption:
        page.insert_text((72, y), caption, fontsize=BODY, fontname="helv")
        y += 14
    for level, text in steps:
        page.insert_text((72 + 11 * level, y), text, fontsize=BODY, fontname="helv")
        y += 14
    return y + 10


# A loop line that runs to the right edge: what the loop holds starts in lower case below it.
LONG_LOOP = [
    (0, "count = 0"),
    (0, "for each word w in the line of the verse that the reader has marked for the scan do"),
    (1, "count = count + stress(w)"),
    (1, "mark w as read"),
    (0, "end for"),
    (0, "return count"),
]


# Steps in plain words, on a page of nothing else: its right edge is where the longer ones end.
HANDOUT = [
    (0, "for each cup on the tray do"),
    (1, "put a tea bag in the cup and pour the water"),
    (1, "wait three minutes"),
    (1, "take the bag out"),
    (0, "end for"),
    (0, "return the tray"),
]


# A first step in plain words that runs to the right edge, before the first loop.
LONG_FIRST = [
    (0, "Initialize the table of counts with a zero for every word that the verse can hold in it"),
    (0, "for each word w in the line do"),
    (1, "count = count + stress(w)"),
    (0, "end for"),
    (0, "return count"),
]


def fraction(page, y: float) -> float:
    """A display equation with a fraction bar: v = d / t."""
    page.insert_text((280, y + 14), "v =", fontsize=12)
    page.insert_text((306, y + 6), "d", fontsize=12)
    page.draw_line((302, y + 10), (316, y + 10), width=0.6)
    page.insert_text((306, y + 23), "t", fontsize=12)
    return y + 40


# Blocks shown by their indentation alone: no line closes them.
NO_END = [
    (0, "count = 0"),
    (0, "for each word w in the line do"),
    (1, "if w is stressed then"),
    (2, "count = count + 1"),
    (0, "return count"),
]

# The same with no "do" or "then" either, and a long second step in plain words.
LONG_SECOND = [
    (0, "Set the count to zero."),
    LONG_FIRST[0],
    (0, "for each word w in the line"),
    (1, "if w is stressed"),
    (2, "count = count + 1"),
    (0, "output the count of the line"),
]


def test_an_algorithm_keeps_its_lines_and_three_indent_levels():
    for listed in (ALGORITHM, LONG_LOOP, HANDOUT, LONG_FIRST, NO_END, LONG_SECOND):

        def fill(page, listed=listed):
            y = 100.0 if listed is HANDOUT else running(page, 100, RUNNING)
            y = algorithm(page, y + 10, listed)
            # A display equation of the text, and the sentence going on after it (on two pages
            # set in from the left, as the first line of a paragraph is).
            y = fraction(page, y)
            where = "where d = 12 is the length of the verse and t = 3 its count."
            set_in = listed is ALGORITHM or listed is NO_END
            running(page, y + 10, where, indent=15.0 if set_in else 0.0)

        elements = analyze(page_of(fill)).elements
        steps = [e for e in elements if e.preformatted]
        assert len(steps) == 1
        assert steps[0].text.split("\n") == ["  " * level + text for level, text in listed]
        captions = [e.text for e in elements if (e.text or "").startswith("Algorithm 1")]
        assert captions == ["Algorithm 1 Count stressed syllables"]  # its own block, above
        assert any(  # a paragraph still, its two values inline equations
            (e.text or "").startswith("where ") and not e.preformatted for e in elements
        )

    # A bold letter in a step that waits for evidence (notation memory) is put back as a plain
    # letter: the lines stay as they were, one to a step, and keep their indentation.
    def bold_letter(page):
        y = algorithm(page, 100)
        end = 72 + pymupdf.get_text_length(ALGORITHM[0][1], fontname="helv", fontsize=BODY)
        page.insert_text((end + 4, 100 + 14), "W", fontsize=BODY, fontname="hebo")
        return y

    with run_options.using(ConversionOptions(notation_memory=True)):
        (step,) = [e for e in analyze(page_of(bold_letter)).elements if e.preformatted]
    assert step.text.split("\n")[3:5] == ["  if w is stressed then", "    count = count + 1"]
    assert step.text.split("\n")[0] == "input: a line of verse W"

    # The index of a tall expression hangs a little under the end of its step (the text layer
    # sets it apart): it is part of that step, and the steps after it stay in the block.
    def hanging(page):
        page.insert_text((72, 100), "Algorithm 3 Estimate a value", fontsize=BODY, fontname="helv")
        y = 114.0
        for text in ("1: Set the start value.", "2: for k = 0 to M do", "3: Set a = b + c."):
            page.insert_text((72, y), text, fontsize=BODY, fontname="helv")
            y += 14
        small = "4: y = SGDstep(x, z"  # a step set smaller than the others, an index under its end
        page.insert_text((72, y - 4), small, fontsize=8, fontname="helv")
        end = 72 + pymupdf.get_text_length(small, fontname="helv", fontsize=8)
        page.insert_text((end - 8, y + 3.5), "1:T).", fontsize=8, fontname="symb")
        for text in ("5: Stop if the criteria are met.", "6: end for"):
            page.insert_text((72, y + 18), text, fontsize=BODY, fontname="helv")
            y += 14

    (step,) = [e for e in analyze(page_of(hanging)).elements if e.preformatted]
    assert [line[:2] for line in step.text.split("\n")] == ["1:", "2:", "3:", "4:", "5:", "6:"]

    # The nearest thing the indent proof must not take: two paragraphs under a caption, each set
    # in on its first line, with lines that open "For" and "If" (set in, then back out).
    def paragraphs(page):
        page.insert_text((72, 100), "Algorithm 2 Overview of the method", fontsize=BODY)
        text = [
            (87, "We take every verse of the poem and scan it one word at a time until the"),
            (72, "end is reached. For every word of the verse we count the stressed syllables"),
            (72, "and add them to the running total, which is kept for the whole poem."),
            (87, "If the total is above the threshold then the verse is marked as heavy and"),
            (72, "we go on to the next one. For each marked verse we write a line in the margin"),
            (72, "and return to the start of the poem when all verses have been read."),
        ]
        for n, (x, line) in enumerate(text):
            page.insert_text((x, 128 + 13.5 * n), line, fontsize=BODY)

    assert not [e for e in analyze(page_of(paragraphs)).elements if e.preformatted]


def test_a_sentence_that_begins_with_algorithm_and_a_number_is_not_one():
    def fill(page):
        y = prose(page, 100, "Algorithm 1 shows how the verse is scanned for stress.")
        prose(page, y, "Each word is checked once, and the count is kept at the end.")
        prose(page, y + 22, "The result is then compared with the reader's own marks.")

    def paragraph(page):  # its lines run on, and three of them begin with a step word
        lines = (
            "We now turn to the second method, which is faster on long poems and is the one "
            "listed below in|Algorithm 2. The scan is run once over the whole verse and the "
            "marks are kept in a table that|for every word holds the count of its stressed "
            "syllables, so the reader can check the result,|if the reader wants to, against the "
            "marks in the printed edition of the poem that we used here|while the scan is still "
            "running on the next verse."
        )
        for n, line in enumerate(lines.split("|")):
            page.insert_text((72, 100 + 13.5 * n), line, fontsize=BODY, fontname="helv")
        prose(page, 190, "The count is printed after the last word of the verse is read.")

    steps = (  # a paragraph with a short last line: two of its lines begin with a step word
        "It reads the verse once from left to right and keeps a single running count of the stress|"
        "for every word it meets, adding one to the count whenever the word carries a stress and|"
        "if the word is unstressed it moves on. The count is printed at the end."
    )

    def sentence(page):  # a one-line paragraph, and the paragraph after the next opens "For"
        y = running(page, 100, RUNNING)
        y = running(page, y, "Algorithm 2 is the faster method.")
        y = running(page, y, steps)
        running(
            page,
            y,
            "For each verse the count is then compared with the marks in the printed edition of "
            "the|poem, and the two are listed side by side.",
        )

    goes_on = "if the reader stops at a word, the count starts again."

    def float_caption(page, text=(steps,), heading=None, formula=None, then=goes_on):
        # A picture's caption, the running text under it in paragraphs (or the next heading).
        y = running(page, 100, RUNNING)
        page.draw_rect(pymupdf.Rect(120, y, 480, y + 100), width=0.8)
        page.draw_line((140, y + 20), (460, y + 80), width=1.5)
        y = prose(page, y + 114, "Algorithm 2 Overview of the method") - 8
        for part in text:
            y = running(page, y, part, pitch=14) - 8
        if heading:
            page.insert_text((72, y + 6), heading, fontsize=BODY, fontname="hebo")
            y += 28
        if formula:  # a display equation of the text, and the sentence going on under it
            page.insert_text((250, y + 8), formula, fontsize=BODY, fontname="helv")
            y = running(page, y + 24, then) - 8
        running(page, y, RUNNING, pitch=14, indent=0 if heading else 15)

    def before_a_heading(page):
        float_caption(page, heading="4 Results for the long verse")

    def after_a_short_line(page):  # the last line of the paragraph that the picture cut
        passes = (
            "The first pass reads the verse from left to right and keeps the count of the stresses "
            "that fall|for every word, and the second pass sets the marks of the printed text over "
            "the words.|If the two counts differ, a third reader is asked."
        )
        float_caption(page, text=("The two passes are shown above.", passes))

    def a_paragraph_of_two_lines(page):  # under the short line, and its second line is short
        first = (
            "The first pass reads the verse from left to right and keeps the count of the stresses "
            "that fall|on every word of the verse."
        )
        second = "For every word the second pass sets a mark over it."
        third = "If the two counts differ, a third reader is asked."
        float_caption(page, text=("The two passes are shown above.", first, second, third))

    def over_a_formula(page):  # a line that opens "for" and ends in a colon, a formula under it
        shown = (
            "The figure shows the two passes of the method, the first for the count and the second "
            "one|for the marks of the printed text, which give the speed of the verse as follows:"
        )
        float_caption(page, text=(shown,), formula="v = d / t")

    def a_formula_then_until(page):  # the sentence goes on under its formula with "until"
        speed = (
            "The figure shows the two passes of the method, the first for the count and the second "
            "one|for the marks of the printed text. The speed of the verse is"
        )
        float_caption(page, text=(speed,), formula="v = d / t", then="until the last word is read.")

    pages = (fill, paragraph, sentence, float_caption, before_a_heading, after_a_short_line)
    for page in (*pages, a_paragraph_of_two_lines, over_a_formula, a_formula_then_until):
        assert not [e for e in analyze(page_of(page)).elements if e.preformatted], page.__name__


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
    page,
    y: float,
    steps=NUMBERED,
    caption="Algorithm 2 Scan a verse",
    wrapped=None,
    stop=":",
    preamble=None,
    apart=None,
):
    """Steps numbered "1:", "2:" in a left gutter, indented by level; a step listed in `wrapped`
    goes on at the left edge of the text on a second line. `preamble`: a line under the caption,
    in the gutter (an "Input:" line). `apart`: the number of a step whose words are set far from
    its number (the text layer then lists the number in a row of its own)."""
    if caption:
        page.insert_text((72, y), caption, fontsize=BODY, fontname="helv")
        y += 14
    if preamble:
        page.insert_text((72, y), preamble, fontsize=BODY, fontname="helv")
        y += 14
    for n, (level, text) in enumerate(steps, 1):
        page.insert_text((72, y), f"{n}{stop}", fontsize=BODY - 1, fontname="helv")
        x = 300 if n == apart else 95 + 11 * level
        page.insert_text((x, y), text, fontsize=BODY, fontname="helv")
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


NUMBERED_LINES_PLAIN = [" ".join(line.split()) for line in NUMBERED_LINES]


def test_numbered_steps_keep_their_numbers_apart_and_their_indentation_with_or_without_a_caption():
    # The last one is numbered as a word processor numbers a list ("1."), under its caption.
    for caption, stop in (
        ("Algorithm 2 Scan a verse", ":"),
        (None, ":"),
        ("Algorithm 2 Scan a verse", "."),
    ):

        def fill(page, caption=caption, stop=stop):
            y = prose(page, 100, "The scan of a verse is listed in")
            y = prose(page, y, "Algorithm 2.")  # a sentence's last line, not a caption
            y = numbered_algorithm(page, y, caption=caption, stop=stop)
            prose(page, y + 10, "The count is printed after the last word.")

        elements = analyze(page_of(fill)).elements
        steps = [e for e in elements if e.preformatted]
        assert len(steps) == 1
        assert steps[0].text.split("\n") == [n.replace(":", stop, 1) for n in NUMBERED_LINES]
        assert caption is None or any(e.text == caption for e in elements)

    # An appendix's caption ("Algorithm A-1") takes the "Input:" line under it into the block,
    # and a number alone in its row (its step's words are set far to the right) keeps its step.
    for caption, extra in (
        ("Algorithm A-1 Scan a verse", {"preamble": "Input: a line of verse"}),
        ("Algorithm 2 Scan a verse", {"apart": 4}),
    ):

        def appendix(page, caption=caption, extra=extra):
            y = prose(page, 100, "The scan of a verse is listed below.")
            y = numbered_algorithm(page, y + 10, caption=caption, **extra)
            prose(page, y + 10, "The count is printed after the last word.")

        elements = analyze(page_of(appendix)).elements
        (block,) = [e for e in elements if e.preformatted]
        lines = [" ".join(line.split()) for line in block.text.split("\n")]
        assert lines == [
            *([extra["preamble"]] if "preamble" in extra else []),
            *NUMBERED_LINES_PLAIN,
        ]
        assert any(e.text == caption for e in elements)


def test_a_step_that_wraps_back_to_the_left_edge_stays_one_step():
    def fill(page):
        y = prose(page, 100)
        long = "count = count + 1 and then add the length of the word to the running total"
        steps = [*NUMBERED[:3], (2, long), *NUMBERED[4:]]
        # Step 1 has a comment on a line of its own, though it stops far short of the edge.
        wrapped = {1: "// the running count", 4: "of the verse so far"}
        y = numbered_algorithm(page, y + 10, steps, wrapped=wrapped)
        prose(page, y + 10, "The count is printed after the last word.")

    steps = [e for e in analyze(page_of(fill)).elements if e.preformatted]
    assert len(steps) == 1
    lines = steps[0].text.split("\n")
    assert len(lines) == 7
    assert lines[0] == "1: count = 0 // the running count"
    assert lines[3] == (
        "4:     count = count + 1 and then add the length of the word to the running total"
        " of the verse so far"
    )


def justified(page, y: float, text: str = " ".join([LONG] * 3)) -> float:
    """A paragraph set to both margins (72 to 540), 14 pt a line: nine lines of `LONG`."""
    rect = pymupdf.Rect(72, y, 540, y + 130)
    left = page.insert_textbox(
        rect, text, fontsize=BODY, fontname="helv", lineheight=14 / BODY, align=3
    )
    assert left >= 0
    return y + 14 * 9 + 12


def broken(page, y: float, text: str, x: float = 72.0, hang: float = 0.0) -> float:
    """One step that runs over several lines: each is as long as the right margin (540) lets
    it be, and the lines after the first start `hang` further right."""
    line = ""
    for word in text.split():
        if line and pymupdf.get_text_length(f"{line} {word}", "helv", BODY) > 540 - x:
            page.insert_text((x, y), line, fontsize=BODY, fontname="helv")
            x, hang, y, line = x + hang, 0.0, y + 14, word
        else:
            line = f"{line} {word}".strip()
    page.insert_text((x, y), line, fontsize=BODY, fontname="helv")
    return y + 14


def test_a_numbered_line_that_runs_to_the_right_edge_does_not_take_the_next_number():
    # The affiliations of a paper: raised numbers, small type, each one line that reaches past
    # the right edge of the body text, one after the other.
    def fill(page):
        page.insert_textbox(
            pymupdf.Rect(44, 100, 440, 250),
            LONG,
            fontsize=10,
            fontname="helv",
            lineheight=1.3,
            align=3,
        )
        y = 300
        for n in (1, 2, 3):
            line = "Department of the study of verse, University of Somewhere in "
            while pymupdf.get_text_length(line + "Another", "helv", 8) < 440:
                line += "Another "
            page.insert_text((44, y - 3), str(n), fontsize=6.2, fontname="helv")
            page.insert_text((50, y), line.strip(), fontsize=8, fontname="helv")
            y += 10

    texts = [e.text for e in analyze(page_of(fill)).elements]
    assert [t[0] for t in texts[1:]] == ["1", "2", "3"]


def test_a_numbered_paragraph_with_an_indented_first_line_is_not_cut_at_its_second_line():
    # "2) Prompt Design: The controller ..." is set like a paragraph: its first line starts
    # indented, runs to the right edge, and the next lines start at the margin: one block.
    first = "2) Prompt Design With A Code Template: The controller of the system builds"
    while pymupdf.get_text_length(first + " each", "helv", BODY) < 468 - 12:
        first += " each"
    second = "prompt from the template and the state of the run so far, then sends it on to"
    while pymupdf.get_text_length(second + " the", "helv", BODY) < 468:
        second += " the"
    third = "the model that answers."

    def fill(page):
        page.insert_textbox(
            pymupdf.Rect(72, 90, 540, 230), LONG, fontsize=BODY, fontname="helv", align=3
        )
        page.insert_text((84, 260), first, fontsize=BODY, fontname="helv")
        page.insert_text((72, 274), second, fontsize=BODY, fontname="helv")
        page.insert_text((72, 288), third, fontsize=BODY, fontname="helv")
        page.insert_textbox(
            pymupdf.Rect(72, 304, 540, 430), LONG, fontsize=BODY, fontname="helv", align=3
        )

    elements = analyze(page_of(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.PARAGRAPH]
    assert elements[1].text.endswith(third)


def test_a_step_with_no_number_may_run_over_several_lines_and_text_under_the_last_is_no_step():
    title = "Algorithm 1 Count stressed syllables"
    given = (
        "input: a line of verse with the marks that the first reader made over each of its words "
        "while the verse was read out in class, and a list of the words that the second reader "
        "took to be strong when the same verse was read out again a week later by one who had not "
        "seen the marks or the printed text of the poem before that day"
    )
    append = (
        "append the word w to the list of the words that the second reader keeps beside the "
        "printed text of the poem and checks"
    )

    def hung(page):  # an "input:" line that goes on for three lines set under it, further right
        y = justified(page, 100)
        y = prose(page, y, title) - 8
        y = broken(page, y, given, hang=11)
        y = algorithm(page, y, ALGORITHM[1:], caption=None)
        justified(page, y)

    def flush(page):  # a step in a loop that goes on where it started, on a page set to a margin
        y = justified(page, 100)
        y = algorithm(page, y, ALGORITHM[1:3])
        y = broken(page, y - 10, append, x=83)
        y = algorithm(page, y, [(1, "mark w as read"), *ALGORITHM[6:]], caption=None)
        justified(page, y)

    resumed = (
        "for the whole poem, which is what the second method of this chapter needs when the verse "
        "is long and the reader has no time to scan it by hand, so that the count of the first "
        "method is kept beside the text and is used again later in the chapter, where the two "
        "methods are set side by side and the cost of each one is worked out for the three poems "
        "of the book."
    )

    def under(page):  # the text goes on right under the block, in the middle of a sentence
        y = algorithm(page, 100) - 4
        y = broken(page, y, resumed)
        justified(page, y + 12)

    listed = ["  " * level + text for level, text in ALGORITHM]
    for fill, lines in (
        (hung, [given, *listed[1:]]),
        (flush, [*listed[1:3], "  " + append, "  mark w as read", *listed[6:]]),
        (under, listed),
    ):
        elements = analyze(page_of(fill)).elements
        assert [e.text.split("\n") for e in elements if e.preformatted] == [lines]
        assert fill is not under or resumed in [e.text for e in elements]


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
                y = fraction(page, y)
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

    # No line numbers, and the step after the equation is written with "=": under a step of
    # the loop, then as the first thing the loop holds.
    for lead in ([(1, "set the speed to")], []):

        def plain(page, lead=lead):
            y = running(page, 100, RUNNING)
            y = algorithm(page, y + 10, [*ALGORITHM[1:3], *lead]) - 10
            y = fraction(page, y) + 10
            for level, text in [(1, "count = count + v"), (0, "end for"), (0, "return count")]:
                page.insert_text((72 + 11 * level, y), text, fontsize=BODY, fontname="helv")
                y += 14
            prose(page, y + 20, "The count is printed after the last word.")

        blocks = [e.text.split("\n") for e in analyze(page_of(plain)).elements if e.preformatted]
        assert blocks == [
            ["count = 0", "for each word w in the line do", *("  " + text for _, text in lead)],
            ["  count = count + v", "end for", "return count"],
        ]

    def body(page):  # a loop that holds one formula, after a long first step in plain words
        y = running(page, 100, RUNNING)
        first = (0, LONG_FIRST[0][1] + " today")  # to the right edge of the text above
        loop = [(0, "for each word w in the line do"), (1, "b <- A b"), (0, "end for")]
        y = algorithm(page, y + 10, [first, *loop, NO_END[4]])
        running(page, y + 10, RUNNING)

    (block,) = [e.text.split("\n") for e in analyze(page_of(body)).elements if e.preformatted]
    assert block[1] == "for each word w in the line do"
    assert block[2].startswith("  ") and block[3:] == ["end for", "return count"]


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

    def numbered(page, y, items, stop):
        for n, text in enumerate(items, 1):
            page.insert_text((72, y), f"{n}{stop}", fontsize=BODY, fontname="helv")
            page.insert_text((95, y), text, fontsize=BODY, fontname="helv")
            y += 14
        return y

    def euclid(page):  # a textbook's algorithm, written as a list of sentences
        y = prose(page, 100, "The oldest method we know is the one below.")
        y = prose(page, y, "Algorithm 5.1 (Euclid)") - 8
        items = [
            "If b is zero, stop: the greatest common divisor is a.",
            "For any other b, divide a by b.",
            "Call the remainder r.",
            "Write r under b.",
            "Check the sign of r.",
            "Replace a by b.",
            "Replace b by r.",
            "Write the new pair down.",
            "If r is not zero, go back to step 2.",  # "10." sets its words a digit further right
            "Stop: the answer is a.",
            "Return to the caller.",
        ]
        for n, text in enumerate(items, 1):  # no tab stop: the words follow the number
            page.insert_text((72, y), f"{n}. {text}", fontsize=BODY, fontname="helv")
            y += 14
        prose(page, y + 10, "The pair gets smaller at every pass.")

    def questions(page):  # numbers with no stop, and first words that are step words
        y = prose(page, 100, "Answer all questions.")
        items = [
            "If f(x) = 3x + 2, find f(4).",
            "For the function g(x) = x^2, find g(-3).",
            "If h(x) = f(g(x)), find h(1).",
            "Return to question 1.",
        ]
        numbered(page, y, items, "")

    for fill in (exercises, table, euclid, questions):
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


def test_a_lone_capital_far_above_an_entry_of_its_letter_is_not_an_index_heading():
    def fill(page):
        page.insert_text((72, 80), "Index", fontsize=18, fontname="hebo")
        y = 120.0
        for letter, words in [
            ("A", ["acceleration, 4"]),
            ("B", ["body, 12"]),
            ("C", ["circuit, 20"]),
        ]:
            page.insert_text((72, y), letter, fontsize=BODY, fontname="hebo")
            page.insert_text((72, y + 14), words[0], fontsize=BODY, fontname="helv")
            y += 44
        # A capital at the foot of the column, with a line of its letter only far below it.
        page.insert_text((72, 600), "D", fontsize=BODY, fontname="hebo")
        page.insert_text((72, 740), "diagram, 31", fontsize=BODY, fontname="helv")

    fixes = heading_fixes(analyze(page_of(fill)).elements)
    assert [f.heading.text for f in fixes if f.heading] == ["Index", "A", "B", "C"]


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


def body_with_marks(
    page, mark: str = "1", after: str = " at sea level.", font: str = "helv", **mark_font
):
    """A line of text with a raised mark after "degrees" (and a raised "0", as in "P0"), and the
    note under it that opens with the mark: the page every row below changes a little."""
    lead = "Water boils at one hundred degrees"
    page.insert_text(
        (72, 100), "The boiling point of water depends on the pressure.", fontsize=BODY
    )
    page.insert_text((72, 115), lead, fontsize=BODY, fontname="helv")
    end = 72 + pymupdf.get_text_length(lead, fontname="helv", fontsize=BODY)
    page.insert_text((end + 0.5, 110), mark, fontsize=7, **({"fontname": "helv"} | mark_font))
    page.insert_text((end + 5, 115), after, fontsize=BODY, fontname=font)
    page.insert_text((72, 140), "The curve P", fontsize=BODY, fontname="helv")
    page.insert_text((72 + 52, 143), "0", fontsize=7, fontname="helv")
    page.draw_line((72, 700), (200, 700))


def test_a_note_is_what_opens_with_its_mark_and_goes_on_in_words():
    # Row 1: a row of axis labels, a page number and a running footer under a real note are
    # not notes, though a "0" and a "1" are raised above ("P0", the mark itself).
    def noise(page):
        body_with_marks(page)
        page.insert_text((72, 712), "1 Pressure matters.", fontsize=8)
        page.insert_text((72, 738), "0 . 1   0 . 2   0 . 3", fontsize=8)
        page.insert_text((72, 754), "12 Water and Boiling", fontsize=8)
        page.insert_text((300, 770), "1", fontsize=8)

    layout = analyze(page_of(noise))
    by_id = {e.id: e for e in layout.elements}
    notes = [by_id[n.element_id].text for n in layout.notices if n.code == "footnote"]
    assert notes == ["1 Pressure matters."]

    # Row 2: a note that opens "In", "As", "He" ...: the mark and the word are no nuclide.
    def indium(page):
        body_with_marks(page)
        page.insert_text((72, 709), "1", fontsize=5.5, fontname="helv")
        page.insert_text((77, 712), "In fact the pressure changes with height.", fontsize=8)

    layout = analyze(page_of(indium))
    note = next(e for e in layout.elements if "In fact" in (e.text or ""))
    assert not [e for e in layout.elements if e.kind == Kind.EQUATION and "In" in (e.text or "")]
    assert any(link.target_id == note.id for e in layout.elements for link in e.links)

    # Row 3: its mark in the text, before a math letter, stays text ("system,1 n").
    def math_letter(page):
        body_with_marks(page, after="n", font="symb")
        page.insert_text((72, 709), "1", fontsize=5.5, fontname="helv")
        page.insert_text((77, 712), "Pressure matters.", fontsize=8)

    layout = analyze(page_of(math_letter))
    equations = {e.text for e in layout.elements if e.kind == Kind.EQUATION}
    assert equations == {"\u03bd", "P0"}
    body = layout.elements[0]
    assert body.text.startswith("The boiling point") and body.links[0].kind == "internal"

    # Row 4: the mark is read as it is printed. A note may go on in a number, touching its
    # raised mark or after a blank; two marks may share a comma in the text; a mark may have
    # a bracket after it; a mark may be only a little smaller than its note (7 points in a
    # note of 8: raised by its place, not its size). Each note is a note, with a link from
    # its mark.
    touching = pymupdf.get_text_length("1", fontname="helv", fontsize=5.5)
    shapes = [
        ("1", [("1", 5.5, 0), ("2019 was the first year.", 8, touching)]),
        ("1", [("1", 7, 0), ("2019 was the first year.", 8, 5)]),
        ("1", [("1 10 mL of water was used.", 8, 0)]),
        (
            "1,2",
            [("1", 5.5, 0), ("Pressure matters.", 8, 5)],
            [("2", 5.5, 0), ("Height matters too.", 8, 5)],
        ),
        ("1)", [("1 Pressure matters.", 8, 0)]),
        (  # a number and a sign printed together after a name: two marks
            "1*",
            [("1", 5.5, 0), ("Pressure matters.", 8, 5)],
            [("*", 5.5, 0), ("Corresponding author.", 8, 5)],
        ),
    ]
    for mark, *lines in shapes:

        def shape(page, mark=mark, lines=lines):
            body_with_marks(page, mark=mark, after="  at sea level.")
            for n, parts in enumerate(lines):
                for text, size, x in parts:
                    y = 712 + 12 * n - (3 if size < 8 else 0)
                    page.insert_text((72 + x, y), text, fontsize=size, fontname="helv")

        layout = analyze(page_of(shape))
        notes = {n.element_id for n in layout.notices if n.code == "footnote"}
        assert len(notes) == len(lines), mark
        linked = {link.target_id for e in layout.elements for link in e.links}
        assert linked == notes, mark

    # Row 5: the star of a script (a raised asterisk operator over a low one-word line, "Fro" under
    # a norm) is no note, while the same sign over a line of words is.
    sans = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for low, count in (("\u2217 Fro", 0), ("\u2217 Corresponding author.", 1)):

        def star(page, low=low):
            body_with_marks(
                page, mark="\u2217", fontname="dj", fontfile=str(sans), after="  at sea level."
            )
            page.insert_text((72, 712), low, fontsize=8, fontname="dj", fontfile=str(sans))

        if sans.is_file():
            layout = analyze(page_of(star))
            assert len([n for n in layout.notices if n.code == "footnote"]) == count, low

    # Row 6: an exponent set tight against a math letter ("p1", "n2") on the page is not the mark of
    # note 1: the note keeps its link from the paragraph that holds its mark.
    # The same holds for a starred letter ("n" and a raised asterisk operator) under a star note.
    marks = [("1", {"fontname": "helv"})]
    if sans.is_file():
        marks.append(("\u2217", {"fontname": "dj", "fontfile": str(sans)}))
    for mark, font in marks:

        def exponent(page, mark=mark, font=font):
            body_with_marks(page, mark=mark, after=".", **font)
            page.insert_text((72, 709), mark, fontsize=5.5, **font)
            page.insert_text((77, 712), "Pressure matters.", fontsize=8)
            x = 72 + pymupdf.get_text_length("The mass grows as ", fontname="helv", fontsize=BODY)
            page.insert_text((72, 200), "The mass grows as ", fontsize=BODY, fontname="helv")
            page.insert_text((x, 200), "n", fontsize=BODY, fontname="symb")
            page.insert_text((x + 5.5, 195), mark, fontsize=7, **font)

        layout = analyze(page_of(exponent))
        (note,) = [n.element_id for n in layout.notices if n.code == "footnote"]
        assert [k.target_id for k in layout.elements[0].links] == [note], mark

    # Nearest things the rows must not take: a raised "1" after a math letter is an exponent,
    # and a raised "14" before "C" in the text is a nuclide, with a note "1 ..." on the page.
    def nearest(page):
        body_with_marks(page, after=".")
        page.insert_text((72, 709), "1", fontsize=5.5, fontname="helv")
        page.insert_text((77, 712), "Pressure matters.", fontsize=8)
        x = 72 + pymupdf.get_text_length("The mass grows as ", fontname="helv", fontsize=BODY)
        page.insert_text((72, 200), "The mass grows as ", fontsize=BODY, fontname="helv")
        page.insert_text((x, 200), "n", fontsize=BODY, fontname="symb")
        page.insert_text((x + 7, 195), "1", fontsize=7, fontname="helv")
        at = (
            x
            + 20
            + pymupdf.get_text_length("and the sample holds ", fontname="helv", fontsize=BODY)
        )
        page.insert_text((x + 20, 200), "and the sample holds ", fontsize=BODY, fontname="helv")
        page.insert_text((at, 195), "14", fontsize=7, fontname="helv")
        page.insert_text((at + 8, 200), "C in it.", fontsize=BODY, fontname="helv")

    layout = analyze(page_of(nearest))
    equations = {e.text for e in layout.elements if e.kind == Kind.EQUATION}
    assert {"\u03bd 1", "14C"} <= equations


def test_a_note_the_file_names_but_no_link_points_at_is_linked_by_its_mark():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    body_with_marks(page, mark="2", after=". Next we measure it.")
    page.insert_text((72, 712), "2", fontsize=5.5, fontname="helv")
    page.insert_text((77, 715), "Pressure is measured in pascals.", fontsize=8)
    names = doc.get_new_xref()
    doc.update_object(names, f"<< /Names [(footnote@2) [{page.xref} 0 R /XYZ 72 77 null]] >>")
    tree = doc.get_new_xref()
    doc.update_object(tree, f"<< /Dests {names} 0 R >>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"{tree} 0 R")
    layout = analyze(doc.tobytes())
    (note,) = [e for e in layout.elements if (e.text or "").startswith("2")]
    body = layout.elements[0]
    (link,) = body.links
    assert (link.kind, link.target_id) == ("internal", note.id)
    assert (body.text or "")[link.start : link.end] == "2"


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


def test_a_contents_page_with_dot_leaders_is_nested_links_to_the_headings_without_leaders():
    pdf = contents_pdf()
    elements = analyze(pdf).elements
    entries = [e for e in elements if e.kind == Kind.LIST_ITEM]
    assert [(e.text, e.list_level) for e in entries] == [
        ("1 Motion", 1),
        ("1.1 Speed", 2),
        ("1.2 Velocity", 2),
        ("2 Forces", 1),
    ]
    assert not [e for e in elements if e.kind == Kind.PARAGRAPH]  # no stray page numbers
    # Two entries share a title with a heading ("1 Motion", "1.1 Speed"); "1.2" and "2" name none.
    by_text = {e.text: e for e in entries}
    heads = [e for e in elements if e.kind == Kind.HEADING and e.text != "Contents"]
    assert [h.text for h in heads] == ["1 Motion", "1.1 Speed"]
    assert [(k.kind, k.target_id) for k in by_text["1 Motion"].links] == [("internal", heads[0].id)]
    assert by_text["1.2 Velocity"].links == [] and by_text["2 Forces"].links == []
    html = html_of(pdf)
    assert f'<a href="#{heads[0].id}">1 Motion</a>' in html and ". . ." not in html


def test_a_contents_entry_whose_title_runs_up_to_its_page_number_is_an_entry():
    # "C ... air here 14" is one line of text with no leaders, ending where the other numbers do.
    bare = (
        "C Ablation study of the energy of a body that falls through a long column of cold dry air"
        " here 14"
    )

    def fill(page):
        page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        y = 120.0
        for title, number in [
            ("A Motion of a falling body", "3"),
            ("B Forces on a body at rest", "9"),
            (bare, ""),
            ("D Waves", "21"),
        ]:
            if number:
                page.insert_text((72, y), title + " ." * 40, fontsize=BODY, fontname="helv")
                page.insert_text((520, y), number, fontsize=BODY, fontname="helv")
            else:
                page.insert_text((72, y), title, fontsize=BODY, fontname="helv")
            y += 18

    elements = analyze(page_of(fill)).elements
    assert [e.kind for e in elements] == [Kind.HEADING] + [Kind.LIST_ITEM] * 4
    assert elements[3].text.startswith("C Ablation study")


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


def test_a_contents_entry_keeps_its_numbers_out_of_the_maths_it_ends_in():
    def entries(fill):
        elements = analyze(page_of(fill)).elements
        return [e for e in elements if e.kind == Kind.LIST_ITEM], [
            e for e in elements if e.kind == Kind.EQUATION
        ]

    def no_leaders(page):  # "Forces on a Sigma", then its page number at the right margin
        page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        for k, (title, math, number) in enumerate(
            [
                ("Motion", "", "3"),
                ("Forces on a", "S", "9"),
                ("Energy in", "L", "15"),
                ("Heat", "", "41"),
            ]
        ):
            y = 120.0 + 18 * k
            page.insert_text((72, y), title, fontsize=BODY, fontname="helv")
            if math:
                x = 72 + pymupdf.get_text_length(title, fontname="helv", fontsize=BODY)
                page.insert_text((x + 3, y), math, fontsize=BODY, fontname="symb")
            width = pymupdf.get_text_length(number, fontname="helv", fontsize=BODY)
            page.insert_text((540 - width, y), number, fontsize=BODY, fontname="helv")

    items, equations = entries(no_leaders)
    assert [e.text.split()[-1] for e in items] == ["3", "9", "15", "41"]  # the number is text
    assert [e.text for e in equations] == ["\u03a3", "\u039b"]

    def with_leaders(page):  # "6.1 Gamma = D", leaders, a page number
        page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        rows = [
            ("6.1 ", "G = D", "", "18"),
            ("6.2 ", "G = E", "", "19"),
            ("6.3 Index of ", "W", "", "21"),
        ]
        for k, (label, math, _, number) in enumerate(rows):
            y = 120.0 + 18 * k
            page.insert_text((72, y), label, fontsize=BODY, fontname="helv")
            x = 72 + pymupdf.get_text_length(label, fontname="helv", fontsize=BODY)
            page.insert_text((x, y), math, fontsize=BODY, fontname="symb")
            x += pymupdf.get_text_length(math, fontname="symb", fontsize=BODY)
            page.insert_text((x + 2, y), " ." * 40, fontsize=BODY, fontname="helv")
            page.insert_text((520, y), number, fontsize=BODY, fontname="helv")

    items, equations = entries(with_leaders)
    assert [e.text.split()[0] for e in items] == ["6.1", "6.2", "6.3"]  # the section number is text
    assert [e.text[0] for e in equations] == ["\u0393", "\u0393", "\u03a9"]  # all still maths


SUBS = ("1.1 Speed", "3.1 Work")


@pytest.mark.parametrize(
    ("label", "group", "intro", "wrap", "leaders"),
    [
        ("{n} ", None, False, ("(Theo-", "rem 1)"), True),
        ("{n} ", "Chapter 4", False, ("(Theo-", "rem 1)"), True),
        ("Week {n}: ", "Unit 4", False, ("(Theo-", "rem 1)"), True),
        # the chapter's first entry has no number ("Introduction")
        ("{n} ", "Chapter 4", True, ("(Theo-", "rem 1)"), True),
        # ... under a part line with a Roman numeral, among entries numbered 1, 2, 3
        ("{n} ", "IV", True, ("(Theo-", "rem 1)"), True),
        # the second line starts with a capital, under a label like the entries' own
        ("{n} ", "Chapter 4", False, ("(after", "Newton)"), True),
        # a LaTeX article: the sections carry no leaders, only their subsections do
        ("{n} ", None, False, ("(after", "Newton)"), False),
    ],
)
def test_a_contents_title_that_wraps_is_one_entry(label, group, intro, wrap, leaders):
    # With a chapter (or unit) line that has no page number of its own between two entries: it
    # runs as far right as a title's first line, starts with a label as the entries do, and is
    # not one.
    titles = [
        "Motion",
        "Proof of the long result on the forces of a falling body through air and water",
        "Energy",
        "Waves",
    ]
    first, second, third, fourth = (label.format(n=k + 1) + t for k, t in enumerate(titles))
    if intro:
        fourth = "Introduction"
    group_line = (
        f"{group} The first law of thermodynamics and the conservation of energy in closed systems"
    )

    def fill(page):
        page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
        y = 120.0
        for lines, number in [
            ([first], "3"),
            *([] if leaders else [([SUBS[0]], "5")]),
            ([f"{second} {wrap[0]}", wrap[1]], "9"),
            ([third], "15"),
            *([] if leaders else [([SUBS[1]], "17")]),
            ([group_line], None),
            ([fourth], "21"),
        ]:
            if number is None and not group:
                continue
            sub = lines[0] in SUBS
            for k, line in enumerate(lines):
                last = k == len(lines) - 1
                page.insert_text(
                    (72 + (14 if k or sub else 0), y), line, fontsize=BODY, fontname="helv"
                )
                if last and number and (leaders or sub):
                    page.insert_text((150, y), " ." * 40, fontsize=BODY, fontname="helv")
                if last and number:
                    page.insert_text((520, y), number, fontsize=BODY, fontname="helv")
                y += 13
            y += 5 if number else 0

    entries = [e for e in analyze(page_of(fill)).elements if e.kind == Kind.LIST_ITEM]
    joined = wrap[0][:-1] + wrap[1] if wrap[0].endswith("-") else " ".join(wrap)
    expected = [first, f"{second} {joined}", third, fourth]
    if not leaders:
        expected[1:1], expected[4:4] = [SUBS[0]], [SUBS[1]]
    assert [e.text for e in entries] == expected


@pytest.mark.parametrize("label", ["{n} ", "Week {n}: ", "Module {n} ", ""])
@pytest.mark.parametrize("heading", [True, False])
def test_a_line_above_a_contents_list_is_not_fused_into_its_first_entry(label, heading):
    """The last line of a paragraph, or a "Part One" line, at the entries' size and running to the
    right edge: whatever the entries start with, it is not the first line of the first entry."""

    def justified(page, y, text):  # a line filled to the right edge
        words = text.split()
        widths = [pymupdf.get_text_length(w, fontname="helv", fontsize=BODY) for w in words]
        gap = (468 - sum(widths)) / (len(words) - 1)
        x = 72.0
        for word, width in zip(words, widths, strict=True):
            page.insert_text((x, y), word, fontsize=BODY, fontname="helv")
            x += width + gap

    def fill(page):
        if heading:
            page.insert_text((72, 80), "Contents", fontsize=18, fontname="hebo")
            justified(
                page,
                116,
                "Part One, the laws of thermodynamics and the quantities they are written in",
            )
        else:
            justified(
                page,
                90,
                "This reader collects the notes for the whole term, and each unit has examples",
            )
            justified(
                page,
                104,
                "and a problem set, and the list below says on which page each of them begins",
            )
        for k, (title, number) in enumerate(
            [("Temperature", "3"), ("The first law", "11"), ("Entropy", "19")]
        ):
            y = (130 if heading else 118) + 14 * k
            text = label.format(n=k + 1) + title
            page.insert_text((72, y), text + " ." * 40, fontsize=BODY, fontname="helv")
            page.insert_text((520, y), number, fontsize=BODY, fontname="helv")

    elements = analyze(page_of(fill)).elements
    assert [e.text for e in elements if "contents entry" in e.reasons] == [
        label.format(n=k + 1) + title
        for k, title in enumerate(["Temperature", "The first law", "Entropy"])
    ]
    assert any(
        e.text.endswith(("written in", "each of them begins"))
        for e in elements
        if e.kind == Kind.PARAGRAPH
    )


def test_two_lines_of_prose_that_end_in_an_ellipsis_are_not_a_contents_page():
    def fill(page):
        page.insert_text(
            (72, 100), "The first prompt starts with the words of the task. . . . .", fontsize=BODY
        )
        page.insert_text(
            (72, 140),
            "A second line is cut off the same way after the rules. . . . .",
            fontsize=BODY,
        )

    assert not [e for e in analyze(page_of(fill)).elements if "contents entry" in e.reasons]


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
