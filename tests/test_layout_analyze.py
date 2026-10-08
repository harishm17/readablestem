"""layout.analyze on PDFs generated here with PyMuPDF (offline, no course material).

Each test builds the smallest page that exercises one rule (headings, paragraphs and
hyphenation, display and inline maths, figures and captions, columns, running headers,
errors) and asserts the exact elements that come out.
"""

from __future__ import annotations

import logging

import pymupdf
import pytest

from a11y_stem.core.types import Element, Kind, inline_marker
from a11y_stem.pipeline.layout import (
    REASON_BOLD_BODY,
    REASON_LARGER_FONT,
    REASON_NUMBERED,
    REASON_NUMBERED_LINE,
    LayoutError,
    analyze,
    heading_levels,
)

BODY = 11.0
PITCH = 15.0  # baseline-to-baseline distance used for body text in these pages


def write(page: pymupdf.Page, x: float, y: float, rows: list[str], **kw) -> float:
    """Insert one text row per string; return the baseline after the last row."""
    size = kw.pop("fontsize", BODY)
    pitch = kw.pop("pitch", PITCH * size / BODY)
    for row in rows:
        page.insert_text((x, y), row, fontsize=size, **kw)
        y += pitch
    return y


def one_page(fill, **save) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes(**save)


def texts(elements: list[Element], kind: Kind | None = None) -> list[str]:
    return [e.text or "" for e in elements if kind is None or e.kind == kind]


def red_pixmap(width: int = 64, height: int = 48) -> pymupdf.Pixmap:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (200, 40, 40))
    return pixmap


# ---------------------------------------------------------------- headings and text


def test_headings_and_paragraphs_in_reading_order():
    def fill(page):
        page.insert_text((72, 80), "Chapter 3 Forces", fontsize=22, fontname="hebo")
        page.insert_text((72, 120), "3.1 Newton's First Law", fontsize=16, fontname="hebo")
        y = write(
            page,
            72,
            150,
            [
                "An object keeps its velocity unless a net force acts on it. This",
                "tendency is called inertia, and the mass of an object measures how",
                "much inertia it has.",
            ],
        )
        page.insert_text((72, y + 12), "Inertia in Daily Life", fontsize=13, fontname="hebo")
        write(page, 72, y + 36, ["Passengers lurch forward when a bus brakes suddenly."])

    layout = analyze(one_page(fill))
    assert [(e.kind, e.text) for e in layout.elements] == [
        (Kind.HEADING, "Chapter 3 Forces"),
        (Kind.HEADING, "3.1 Newton's First Law"),
        (
            Kind.PARAGRAPH,
            "An object keeps its velocity unless a net force acts on it. This tendency is "
            "called inertia, and the mass of an object measures how much inertia it has.",
        ),
        (Kind.HEADING, "Inertia in Daily Life"),
        (Kind.PARAGRAPH, "Passengers lurch forward when a bus brakes suddenly."),
    ]
    assert [e.id for e in layout.elements] == [f"p1-e{i}" for i in range(5)]
    assert [e.order for e in layout.elements] == list(range(5))
    headings = [e for e in layout.elements if e.kind == Kind.HEADING]
    levels = heading_levels(layout.elements)
    assert [levels[h.id] for h in headings] == [1, 2, 3]
    assert all(REASON_LARGER_FONT in h.reasons and h.bold for h in headings)
    assert REASON_NUMBERED in headings[0].reasons and REASON_NUMBERED in headings[1].reasons
    assert headings[0].font_size == 22.0 and layout.elements[2].font_size == BODY
    assert layout.title == "Chapter 3 Forces"  # no metadata title: first level-1 heading
    assert (layout.page_count, layout.pages, layout.page_sizes) == (1, [0], [(612.0, 792.0)])


def test_hyphenation_is_repaired_only_before_a_lowercase_continuation():
    def fill(page):
        write(
            page,
            72,
            100,
            [
                "The cart keeps moving at a constant veloc-",
                "ity until friction slows it down. The Newton-",
                "Raphson method is not needed for this problem.",
            ],
        )

    (paragraph,) = analyze(one_page(fill)).elements
    assert paragraph.text == (
        "The cart keeps moving at a constant velocity until friction slows it down. "
        "The Newton-Raphson method is not needed for this problem."
    )


def test_bold_body_line_and_standalone_numbered_line_are_headings_list_items_are_not():
    def fill(page):
        y = write(page, 72, 80, ["A cart rolls down a ramp and speeds up as it goes."])
        page.insert_text((72, y + 10), "Worked example", fontsize=BODY, fontname="hebo")
        y = write(page, 72, y + 25, ["The ramp is two metres long and the cart starts at rest."])
        y = write(page, 72, y + 12, ["2.3 Units of Speed"])
        y = write(page, 72, y + 12, ["Speed is measured in metres per second in SI units."])
        write(
            page,
            72,
            y + 12,
            ["1. Measure the length of the ramp.", "2. Time the cart from top to bottom."],
        )

    elements = analyze(one_page(fill)).elements
    headings = [e for e in elements if e.kind == Kind.HEADING]
    assert [(h.text, h.reasons[0]) for h in headings] == [
        ("Worked example", REASON_BOLD_BODY),
        ("2.3 Units of Speed", REASON_NUMBERED_LINE),
    ]
    assert texts(elements, Kind.PARAGRAPH)[-2:] == [
        "1. Measure the length of the ramp.",
        "2. Time the cart from top to bottom.",
    ]
    levels = heading_levels(elements)
    assert [levels[h.id] for h in headings] == [1, 1]  # no size-based headings at all


def test_long_runs_of_large_text_and_bold_sentences_inside_paragraphs_are_not_headings():
    def fill(page):
        y = write(
            page,
            72,
            90,
            [
                "This opening text is printed large on purpose.",
                "It runs over four lines, which is too long for",
                "any heading, so it has to stay a paragraph even",
                "though its font is bigger than the body text.",
            ],
            fontsize=16,
        )
        y = write(page, 72, y + 20, ["Body text continues here in the normal size of eleven."])
        page.insert_text(
            (72, y),
            "and this entire line happens to be bold for emphasis",
            fontsize=BODY,
            fontname="hebo",
        )
        write(page, 72, y + PITCH, ["before the sentence ends in plain type again."])
        write(page, 72, y + 80, ["More body text so that eleven points is the body size here."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH] * 3
    assert elements[1].text == (
        "Body text continues here in the normal size of eleven. and this entire line happens "
        "to be bold for emphasis before the sentence ends in plain type again."
    )


def test_bullets_start_new_paragraphs():
    html = "<p>\u2022 Measure the ramp length.<br>\u2022 Time the cart over that length.</p>"
    assert texts(analyze(html_page(html)).elements) == [
        "\u2022 Measure the ramp length.",
        "\u2022 Time the cart over that length.",
    ]


def test_first_line_indent_separates_paragraphs_without_a_gap():
    """LaTeX style: no space between paragraphs, the first line of each is indented."""

    def fill(page):
        y = write(
            page,
            90,
            100,
            ["Momentum is mass times velocity, and it is conserved in every"],
        )
        y = write(page, 72, y, ["collision between the carts on the track."])
        y = write(page, 90, y, ["Kinetic energy is conserved only when the collision is fully"])
        write(page, 72, y, ["elastic, which real carts never quite manage."])

    assert texts(analyze(one_page(fill)).elements) == [
        "Momentum is mass times velocity, and it is conserved in every collision between the "
        "carts on the track.",
        "Kinetic energy is conserved only when the collision is fully elastic, which real carts "
        "never quite manage.",
    ]


def test_a_bare_chapter_number_above_the_title_joins_it():
    def fill(page):
        page.insert_text((72, 90), "4", fontsize=36, fontname="hebo")
        page.insert_text((72, 130), "Energy and Work", fontsize=24, fontname="hebo")
        write(page, 72, 170, ["Work is done when a force moves its point of application."])

    elements = analyze(one_page(fill)).elements
    assert [(e.kind, e.text) for e in elements] == [
        (Kind.HEADING, "4 Energy and Work"),
        (Kind.PARAGRAPH, "Work is done when a force moves its point of application."),
    ]


# ---------------------------------------------------------------- display equations


def fraction_page(page: pymupdf.Page) -> None:
    write(page, 72, 100, ["Average speed is the distance travelled divided by the time:"])
    page.insert_text((280, 144), "v =", fontsize=12)
    page.insert_text((306, 136), "d", fontsize=12)
    page.draw_line((302, 140), (316, 140), width=0.6)
    page.insert_text((306, 153), "t", fontsize=12)
    write(page, 72, 185, ["Speed is measured in metres per second in the SI system."])


def test_vector_fraction_bar_ties_numerator_and_denominator_into_one_display_equation():
    elements = analyze(one_page(fraction_page)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]
    equation = elements[1]
    assert equation.display == "block" and equation.parent_id is None
    assert equation.text == "d v = t"  # reading order of the three rows: d, "v =", t
    assert "fraction bar" in equation.reasons and "3 lines merged" in equation.reasons
    x0, y0, x1, y1 = equation.bbox
    assert x0 <= 280.5 and x1 >= 316 and y0 <= 128 and y1 >= 152  # covers d, bar and t
    assert equation.score is not None and equation.score >= 0.5
    assert not any(" d " in t or t.endswith(" t") for t in texts(elements, Kind.PARAGRAPH))


def test_stacked_numbers_with_a_bar_are_a_fraction_without_a_relation_symbol():
    def fill(page):
        write(page, 72, 100, ["One half is written as a fraction like this one:"])
        page.insert_text((300, 140), "1", fontsize=12)
        page.draw_line((296, 143.5), (310, 143.5), width=0.6)
        page.insert_text((300, 153), "2", fontsize=12)

    elements = analyze(one_page(fill)).elements
    assert [(e.kind, e.text) for e in elements] == [
        (Kind.PARAGRAPH, "One half is written as a fraction like this one:"),
        (Kind.EQUATION, "1 2"),
    ]


def test_a_lone_short_line_without_maths_neighbours_stays_text():
    def fill(page):
        write(page, 72, 100, ["The final speed of the cart after the ramp is", "12 m/s."])

    (paragraph,) = analyze(one_page(fill)).elements
    assert paragraph.text == "The final speed of the cart after the ramp is 12 m/s."


def test_adjacent_maths_lines_and_an_equation_number_merge_into_one_region():
    def fill(page):
        write(page, 72, 100, ["The position after the cart has rolled for two seconds:"])
        page.insert_text((250, 140), "x = v t + 2", fontsize=12)
        page.insert_text((262, 156), "= 14 m", fontsize=12)
        page.insert_text((500, 140), "(2.1)", fontsize=12)
        write(page, 72, 190, ["That is fourteen metres from the start of the track."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]
    equation = elements[1]
    assert equation.text == "x = v t + 2 (2.1) = 14 m"
    assert equation.bbox[2] > 520  # the equation number is part of the region


# ---------------------------------------------------------------- inline maths


def html_page(html: str):
    def fill(page):
        page.insert_htmlbox(pymupdf.Rect(72, 72, 540, 300), html)

    return one_page(fill)


def test_inline_maths_becomes_child_equations_with_markers_in_the_paragraph():
    layout = analyze(
        html_page(
            "<p>The speed is <i>v</i> = <i>d</i>/<i>t</i>, and the <i>average</i> speed uses "
            "\u0394<i>x</i> over the whole trip.</p>"
        )
    )
    paragraph, first, second = layout.elements
    assert paragraph.kind == Kind.PARAGRAPH
    assert paragraph.text == (
        f"The speed is {inline_marker(first.id)}, and the average speed uses "
        f"{inline_marker(second.id)} over the whole trip."
    )
    assert (first.id, second.id) == ("p1-e1", "p1-e2")
    for child, text in ((first, "v = d/t"), (second, "\u0394x")):
        assert child.kind == Kind.EQUATION and child.display == "inline"
        assert child.parent_id == paragraph.id and child.text == text
        assert child.order == paragraph.order + (1 if child is first else 2)
    assert first.reasons == ["italic variable with operator"]
    assert second.reasons == ["math font or symbol"]  # Greek capital delta
    px0, py0, px1, py1 = paragraph.bbox
    for child in (first, second):
        x0, y0, x1, y1 = child.bbox
        assert px0 <= x0 < x1 <= px1 and py0 <= y0 < y1 <= py1


def test_superscripts_count_as_maths_but_italic_words_citations_and_ranges_do_not():
    elements = analyze(
        html_page(
            "<p>This is <i>important</i>: see [3] and pages 10-12 of the 2019 edition, a "
            "classic. The area is x<sup>2</sup> square metres.</p>"
        )
    ).elements
    paragraph, square = elements
    assert square.text == "x2" and square.reasons == ["letter with sub/superscript"]
    assert paragraph.text == (
        "This is important: see [3] and pages 10-12 of the 2019 edition, a classic. "
        f"The area is {inline_marker(square.id)} square metres."
    )


def test_code_option_dashes_and_markup_are_not_maths():
    def fill(page):
        page.insert_text((72, 100), "x = 5", fontsize=BODY, fontname="cour")  # code listing
        page.insert_text((72, 115), "y = x + 1", fontsize=BODY, fontname="cour")
        page.insert_htmlbox(
            pymupdf.Rect(72, 140, 540, 200),
            "<p>Run the tool with \u2212v for verbose output and \u2212q to stop.</p>"
            "<p>The element &lt;a&gt;&lt;b/&gt;&lt;/a&gt; nests b inside a for this example.</p>",
        )

    elements = analyze(one_page(fill)).elements
    assert {e.kind for e in elements} == {Kind.PARAGRAPH}  # no equation of any kind
    assert texts(elements)[0] == "x = 5 y = x + 1"  # the code rows stay text
    prose = " ".join(texts(elements)[1:])
    assert "Run the tool with \u2212v for verbose output and \u2212q to stop." in prose
    assert "The element <a><b/></a> nests b inside a for this example." in prose


def test_signed_numbers_are_inline_maths():
    elements = analyze(
        html_page("<p>The cart ends at \u22123 m, so its displacement is negative.</p>")
    ).elements
    paragraph, number = elements
    assert (number.text, number.reasons) == ("\u22123 m", ["signed number"])
    assert paragraph.text == (
        f"The cart ends at {inline_marker(number.id)}, so its displacement is negative."
    )


def test_a_wide_table_rule_next_to_a_short_cell_is_not_a_fraction_bar():
    def fill(page):
        write(page, 72, 100, ["Offsets in the table below are counted in bytes from the start."])
        page.draw_line((72, 140), (540, 140), width=0.5)
        page.insert_text((300, 152), ">4", fontsize=BODY)
        page.draw_line((72, 158), (540, 158), width=0.5)

    elements = analyze(one_page(fill)).elements
    assert [(e.kind, e.text) for e in elements] == [
        (Kind.PARAGRAPH, "Offsets in the table below are counted in bytes from the start."),
        (Kind.PARAGRAPH, ">4"),
    ]


# ---------------------------------------------------------------- figures


@pytest.mark.parametrize("caption_above", [False, True])
def test_raster_figure_gets_the_nearby_caption(caption_above):
    def fill(page):
        write(page, 72, 90, ["The block in the photograph below is at rest on a table."])
        page.insert_image(pymupdf.Rect(150, 130, 350, 280), pixmap=red_pixmap())
        caption_y = 120 if caption_above else 296
        page.insert_text((150, caption_y), "Figure 1: A red block on a table.", fontsize=10)
        write(page, 72, 330, ["Nothing pushes the block, so it does not move at all."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.FIGURE, Kind.PARAGRAPH]
    figure = elements[1]
    assert figure.caption == "Figure 1: A red block on a table."
    assert figure.bbox == (150.0, 130.0, 350.0, 280.0)
    assert figure.reasons == ["raster image"] and figure.text is None
    assert not any("Figure 1" in t for t in texts(elements))


def test_tiny_images_and_full_page_backgrounds_are_not_figures():
    def fill(page):
        page.insert_image(page.rect, pixmap=red_pixmap(100, 100))  # background
        page.insert_image(pymupdf.Rect(72, 100, 84, 112), pixmap=red_pixmap(16, 16))  # bullet
        page.insert_image(pymupdf.Rect(72, 200, 82, 210), pixmap=red_pixmap(40, 40))  # icon
        write(page, 90, 110, ["A page with only decorative images on it."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH]


def test_vector_drawing_is_a_figure_with_its_labels_but_rules_and_boxes_are_not():
    def fill(page):
        page.draw_line((100, 300), (300, 300))  # axes
        page.draw_line((100, 300), (100, 180))
        page.draw_bezier((100, 290), (150, 280), (220, 220), (290, 190), color=(0, 0, 1))
        page.draw_circle((150, 262), 3, color=(1, 0, 0), fill=(1, 0, 0))
        page.insert_text((180, 309), "time (s)", fontsize=8)  # axis label just below
        page.insert_text((100, 330), "Figure 2 Distance against time.", fontsize=10)
        page.draw_line((72, 400), (540, 400), width=0.5)  # separator rule
        page.draw_rect(pymupdf.Rect(70, 440, 420, 500), color=(0, 0, 0))  # box around text
        write(page, 80, 460, ["Remember: speed is distance over time.", "Always check units."])

    elements = analyze(one_page(fill)).elements
    figures = [e for e in elements if e.kind == Kind.FIGURE]
    assert len(figures) == 1
    (figure,) = figures
    assert figure.caption == "Figure 2 Distance against time."
    assert figure.text == "time (s)"
    assert figure.reasons == ["vector drawing (4 paths)"]
    x0, y0, x1, y1 = figure.bbox
    assert x0 <= 100 and y0 <= 180 and x1 >= 300 and y1 >= 309  # axes, curve and label
    assert texts(elements, Kind.PARAGRAPH) == [
        "Remember: speed is distance over time. Always check units."
    ]


def test_caption_without_a_figure_stays_a_paragraph():
    def fill(page):
        write(page, 72, 100, ["Figure 9: Not drawn on this page."])

    (paragraph,) = analyze(one_page(fill)).elements
    assert (paragraph.kind, paragraph.text) == (Kind.PARAGRAPH, "Figure 9: Not drawn on this page.")


# ---------------------------------------------------------------- columns and margins


LEFT = [
    [
        "Column A one. A ball thrown sideways",
        "keeps its sideways speed while it",
        "falls under gravity.",
    ],
    ["Column A two. Its path is a curve", "called a parabola, the same shape", "for every throw."],
]
RIGHT = [
    [
        "Column B one. The time in the air",
        "depends only on the height and",
        "not on the sideways speed.",
    ],
    ["Column B two. A faster throw lands", "further away but at the same time", "as a slower one."],
]


def two_column_page(page: pymupdf.Page) -> None:
    page.insert_text((72, 80), "2.5 Projectile Motion", fontsize=16, fontname="hebo")
    for x, column in ((72, LEFT), (320, RIGHT)):
        y = 120.0
        for paragraph in column:
            y = write(page, x, y, paragraph, fontsize=10) + 8
    write(
        page,
        72,
        260,
        [
            "Summary row one spans the full width of the page, below both of the columns above,",
            "and it is read last.",
        ],
        fontsize=10,
    )


def test_two_columns_read_left_then_right_with_full_width_items_around_them():
    elements = analyze(one_page(two_column_page)).elements
    starts = [(e.text or "")[:13] for e in elements]
    assert starts == [
        "2.5 Projectil",
        "Column A one.",
        "Column A two.",
        "Column B one.",
        "Column B two.",
        "Summary row o",
    ]
    assert elements[-1].text.endswith("columns above, and it is read last.")


def running_pages(count: int) -> bytes:
    doc = pymupdf.open()
    for number in range(1, count + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((200, 36), "Physics 101 - Week 3 Notes", fontsize=9)
        page.insert_text((280, 770), f"Page {number} of {count}", fontsize=9)
        write(page, 72, 120, [f"Body text that is different on page {number} of this handout."])
        write(page, 72, 300, ["Key idea: forces change motion."])  # repeats, but not a margin
    return doc.tobytes()


def test_running_headers_footers_and_page_numbers_are_dropped():
    layout = analyze(running_pages(4))
    assert texts(layout.elements) == [
        line
        for number in range(1, 5)
        for line in (
            f"Body text that is different on page {number} of this handout.",
            "Key idea: forces change motion.",
        )
    ]
    assert [e.page for e in layout.elements] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert [e.id for e in layout.elements[:3]] == ["p1-e0", "p1-e1", "p2-e0"]


def test_margin_repetition_is_judged_on_neighbouring_pages_when_one_page_is_analysed():
    layout = analyze(running_pages(4), pages=[2])
    assert texts(layout.elements) == [
        "Body text that is different on page 3 of this handout.",
        "Key idea: forces change motion.",
    ]
    assert layout.elements[0].id == "p3-e0" and layout.elements[0].order == 0


def test_a_single_page_keeps_unrepeated_margin_text_but_drops_the_page_number():
    def fill(page):
        page.insert_text((72, 36), "Draft handout for discussion", fontsize=9)
        write(page, 72, 120, ["The only body paragraph on this page."])
        page.insert_text((300, 770), "7", fontsize=9)

    assert texts(analyze(one_page(fill)).elements) == [
        "Draft handout for discussion",
        "The only body paragraph on this page.",
    ]


# ---------------------------------------------------------------- document properties


@pytest.mark.parametrize(
    ("metadata_title", "expected"),
    [
        ("Forces and Motion", "Forces and Motion"),
        ("Microsoft Word - notes3.docx", "Kinematics"),
        ("lecture3.pdf", "Kinematics"),
        ("", "Kinematics"),
    ],
)
def test_title_is_metadata_when_meaningful_else_first_level_one_heading(metadata_title, expected):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Kinematics", fontsize=20, fontname="hebo")
    write(page, 72, 120, ["Kinematics describes motion without asking what causes it."])
    doc.set_metadata({"title": metadata_title})
    assert analyze(doc.tobytes()).title == expected


@pytest.mark.parametrize(("raw", "expected"), [("(de-DE)", "de-DE"), ("(not a tag!)", "en")])
def test_lang_comes_from_the_catalog(raw, expected):
    doc = pymupdf.open()
    write(doc.new_page(), 72, 100, ["Ein kurzer Satz."])
    doc.xref_set_key(doc.pdf_catalog(), "Lang", raw)
    assert analyze(doc.tobytes()).lang == expected


def test_lang_defaults_to_english():
    assert analyze(one_page(lambda p: write(p, 72, 100, ["Hello."]))).lang == "en"


def test_page_selection_is_sorted_and_ids_follow_page_numbers():
    doc = pymupdf.open()
    for number in range(3):
        write(doc.new_page(width=500, height=700), 72, 100, [f"Text on page {number + 1}."])
    layout = analyze(doc.tobytes(), pages=[2, 0, 2], max_pages=2)
    assert layout.pages == [0, 2] and layout.page_count == 3
    assert layout.page_sizes == [(500.0, 700.0), (500.0, 700.0)]
    assert [(e.id, e.page, e.order, e.text) for e in layout.elements] == [
        ("p1-e0", 0, 0, "Text on page 1."),
        ("p3-e0", 2, 1, "Text on page 3."),
    ]


# ---------------------------------------------------------------- errors


def two_pages() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    return doc.tobytes()


ZERO_PAGES = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)


def png_bytes() -> bytes:
    return red_pixmap().tobytes("png")


@pytest.mark.parametrize(
    ("make", "kwargs", "code"),
    [
        (lambda: b"", {}, "unreadable"),
        (lambda: b"this is not a pdf at all", {}, "unreadable"),
        (png_bytes, {}, "unreadable"),  # MuPDF would open a PNG as a one-page document
        (lambda: ZERO_PAGES, {}, "no_pages"),
        (two_pages, {"pages": []}, "no_pages"),
        (two_pages, {"pages": [2]}, "page_out_of_range"),
        (two_pages, {"pages": [-1]}, "page_out_of_range"),
        (two_pages, {"max_pages": 1}, "too_many_pages"),
        (two_pages, {"pages": [0, 1], "max_pages": 1}, "too_many_pages"),
    ],
)
def test_errors_have_category_codes(make, kwargs, code):
    with pytest.raises(LayoutError) as exc:
        analyze(make(), **kwargs)
    assert exc.value.code == code
    assert isinstance(exc.value, ValueError)


def test_password_protected_pdf_is_refused_but_owner_only_protection_is_fine():
    secret = one_page(
        lambda p: write(p, 72, 100, ["Private lecture text."]),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="user",
        owner_pw="owner",
    )
    with pytest.raises(LayoutError) as exc:
        analyze(secret)
    assert exc.value.code == "encrypted"
    assert "Private" not in str(exc.value)

    restricted = one_page(
        lambda p: write(p, 72, 100, ["Readable lecture text."]),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        permissions=0,
    )
    assert texts(analyze(restricted).elements) == ["Readable lecture text."]


def test_max_pages_applies_to_the_requested_pages_only():
    assert analyze(two_pages(), pages=[1], max_pages=1).pages == [1]


def test_analysis_logs_nothing_about_the_document(caplog):
    caplog.set_level(logging.DEBUG)
    analyze(one_page(lambda p: write(p, 72, 100, ["Confidential student feedback here."])))
    assert not [r for r in caplog.records if r.name.startswith("a11y_stem")]
    assert "Confidential" not in caplog.text
