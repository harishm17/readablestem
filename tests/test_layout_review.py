"""Regression tests for defects found in review of layout.analyze (2026-10-08).

Each test rebuilds the reviewer's reproduction as a small generated PDF and pins the fixed
behaviour: numbered-line headings, equation numbers, two-column reading order, Symbol-font
bullets, decoration in the margins, watermarks, text over images and panels, fake inline
markers, MuPDF's own output, and the cost of fragment resolution.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Kind, Status, inline_marker
from readablestem.pipeline import layout, table_model
from readablestem.pipeline.heading_step import heading_fixes
from readablestem.pipeline.layout import (
    REASON_NUMBERED,
    REASON_NUMBERED_LINE,
    analyze,
    crop,
    release,
    section_heading_number,
)

BODY = 11.0
PITCH = 15.0


def write(page: pymupdf.Page, x: float, y: float, rows: list[str], **kw) -> float:
    """Insert one text row per string; return the baseline after the last row."""
    size = kw.pop("fontsize", BODY)
    pitch = kw.pop("pitch", PITCH * size / BODY)
    for row in rows:
        page.insert_text((x, y), row, fontsize=size, **kw)
        y += pitch
    return y


def one_page(fill, width: float = 612, height: float = 792) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=width, height=height))
    return doc.tobytes()


def html_page(html: str) -> bytes:
    return one_page(lambda page: page.insert_htmlbox(pymupdf.Rect(72, 72, 540, 300), html))


def texts(elements: list[Element], kind: Kind | None = None) -> list[str]:
    return [e.text or "" for e in elements if kind is None or e.kind == kind]


def kinds_and_starts(elements: list[Element], length: int) -> list[tuple[Kind, str]]:
    return [(e.kind, (e.text or "")[:length]) for e in elements]


def red_pixmap(width: int = 64, height: int = 48) -> pymupdf.Pixmap:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (200, 40, 40))
    return pixmap


@pytest.fixture(autouse=True)
def empty_cache():
    release()
    yield
    release()


# ---------------------------------------------------------------- numbered-line headings


QUANTITY_LINES = [
    "12 Metres",
    "2.5 Kilograms",
    "10 October 2026",
    "9.8 metres per second squared",
    "0.5 s between photo frames",
    "1.2 Seconds for Trial A",
    "10 Students Took Part",
    "2 kg of sand in the bucket",
    "2.4",
]


def test_quantities_dates_and_bare_numbers_on_their_own_line_are_not_headings():
    def fill(page):
        y = write(page, 72, 60, ["The cart starts from rest and rolls down the ramp for a while."])
        for line in QUANTITY_LINES:
            y = write(page, 90, y + 10, [line])
            y = write(
                page, 72, y + 10, ["More body text follows the line above in the usual size."]
            )

    layout_ = analyze(one_page(fill))
    assert [e.kind for e in layout_.elements] == [Kind.PARAGRAPH] * (2 * len(QUANTITY_LINES) + 1)
    assert texts(layout_.elements)[1::2] == QUANTITY_LINES
    assert layout_.title is None
    assert heading_fixes(layout_.elements) == []


def table_page(page: pymupdf.Page) -> None:
    write(page, 72, 100, ["The table lists the distance of the cart at each second of the run."])
    columns = (200, 260, 320, 380)
    page.draw_line((190, 128), (420, 128), width=0.8)
    for x, head in zip(columns, ("Time", "Dist", "Speed", "Acc"), strict=True):
        page.insert_text((x, 142), head, fontsize=BODY)
    page.draw_line((190, 147), (420, 147), width=0.5)
    values = [("0", "0.0", "0.0", "2.0"), ("1", "1.0", "2.0", "2.0"), ("2", "4.0", "4.0", "2.0")]
    for r, row in enumerate(values):
        for x, value in zip(columns, row, strict=True):
            page.insert_text((x, 162 + 15 * r), value, fontsize=BODY)
    page.draw_line((190, 197), (420, 197), width=0.8)
    # A ruled "label | value" table whose values start with a number.
    for y in (240, 260, 280, 300):
        page.draw_line((72, y), (400, y), width=0.5)
    for x in (72, 236, 400):
        page.draw_line((x, 240), (x, 300), width=0.5)
    cells = (("Mass", "2 kg"), ("Speed", "3 m/s"), ("Distance", "12 Metres"))
    for y, (name, value) in zip((254, 274, 294), cells, strict=True):
        write(page, 78, y, [name])
        write(page, 242, y, [value])


def test_numeric_table_cells_are_not_headings():
    # Since pipeline 0.4.0 both tables are TABLE elements (pipeline/tables.py): their
    # cells leave the paragraph flow and none of them can become a heading. Without the
    # SLANet-plus file (CI) the unruled table is not claimed, and its cells still are not
    # headings.
    layout_ = analyze(one_page(table_page))
    assert not [e for e in layout_.elements if e.kind == Kind.HEADING]
    assert layout_.title is None
    if not table_model.model_path().is_file():
        assert Kind.TABLE in {e.kind for e in layout_.elements}
        return
    assert [e.kind for e in layout_.elements] == [Kind.PARAGRAPH, Kind.TABLE, Kind.TABLE]
    cells = {c.text for e in layout_.elements if e.table for c in e.table.cells}
    assert {"0.0", "2.0", "2 kg", "12 Metres"} <= cells
    assert not {"0.0", "2.0", "2 kg", "12 Metres"} & set(texts(layout_.elements, Kind.PARAGRAPH))


def test_a_numbered_section_title_on_its_own_line_is_still_an_accepted_heading():
    def fill(page):
        y = write(page, 72, 80, ["Newton's first law says that motion continues unless forced."])
        y = write(page, 72, y + 12, ["2.2 Second Law of Motion"])
        write(page, 72, y + 12, ["The second law relates the net force to the acceleration."])

    elements = analyze(one_page(fill)).elements
    (heading,) = [e for e in elements if e.kind == Kind.HEADING]
    assert heading.text == "2.2 Second Law of Motion"
    assert heading.reasons == [REASON_NUMBERED_LINE, REASON_NUMBERED]
    (fix,) = heading_fixes(elements)
    assert (fix.status, fix.confidence) == (Status.ACCEPTED, 0.88)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2.3 Units of Speed", True),
        ("2.3.1. Average speed", True),
        ("Chapter 2 Motion", True),
        ("Chapter 3", True),
        ("IV. Results", True),
        ("2.2 Second Law", True),
        ("3.1 Newton's Laws", True),
        ("2 kg", False),
        ("2 Motion", False),  # a bare integer needs a larger font or a prefix word
        ("12 Metres", False),
        ("2.5 Kilograms", False),
        ("1.2 Seconds for Trial A", False),
        ("10 October 2026", False),
        ("3.4 May", False),
        ("9.8 metres per second", False),
        ("0.5 Seconds", False),
        ("2.0", False),
        ("1. Measure the ramp", False),
        ("A) Units", False),
        ("B. Smith", False),
    ],
)
def test_section_heading_number(text, expected):
    assert section_heading_number(text) is expected


def test_a_bold_number_with_a_unit_is_not_a_heading_but_a_bold_ordinal_title_is():
    def fill(page):
        write(page, 72, 80, ["The mass of the loaded cart was measured before the run."])
        page.insert_text((72, 110), "2 kg", fontsize=BODY, fontname="hebo")
        write(page, 72, 135, ["That is enough to keep the cart on the track at all times."])
        page.insert_text((72, 170), "2nd Law of Motion", fontsize=BODY, fontname="hebo")
        write(page, 72, 195, ["The second law relates the net force to the acceleration."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH] * 3 + [Kind.HEADING, Kind.PARAGRAPH]
    assert elements[3].text == "2nd Law of Motion"


# ---------------------------------------------------------------- equation numbers


def fraction_with_label(label: str) -> bytes:
    def fill(page):
        write(page, 72, 100, ["Average speed is the distance travelled divided by the time taken:"])
        page.insert_text((280, 144), "v =", fontsize=12)
        page.insert_text((306, 136), "d", fontsize=12)
        page.draw_line((302, 140), (316, 140), width=0.6)
        page.insert_text((306, 153), "t", fontsize=12)
        page.insert_text((520, 144), label, fontsize=BODY)
        write(page, 72, 185, ["Speed is measured in metres per second in the SI system of units."])

    return one_page(fill)


@pytest.mark.parametrize("label", ["(2.4)", "2.4", "2.4a", "[2.4]", "Eq. 2.4"])
def test_an_equation_number_beside_a_fraction_is_its_label_not_its_text(label):
    elements = analyze(fraction_with_label(label)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]
    equation = elements[1]
    assert equation.text == "d v = t"  # numerator, main row, denominator
    assert equation.equation_label == label
    assert "fraction bar" in equation.reasons
    x0, y0, x1, y1 = equation.bbox
    assert x0 <= 281 and y0 <= 128 and y1 >= 152  # the crop includes t ...
    assert x1 < 330  # ... but not the number at x = 520


def test_a_bare_equation_number_halfway_down_a_fraction_belongs_to_it():
    """OpenStax: a fraction alone on its line, with "2.4" level with the bar."""

    def fill(page):
        write(page, 72, 100, ["The slope of the graph can also be written as a ratio of changes:"])
        page.insert_text((282, 130), "Δd", fontsize=BODY, fontname="symb")
        page.draw_line((280, 133), (295, 133), width=0.6)
        page.insert_text((282, 142), "Δt", fontsize=BODY, fontname="symb")
        page.insert_text((318, 136), "2.4", fontsize=BODY)
        write(page, 72, 170, ["This relationship was how we defined average velocity before."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.EQUATION, Kind.PARAGRAPH]
    equation = elements[1]
    assert equation.equation_label == "2.4" and "2.4" not in (equation.text or "")
    assert "2 lines merged" in equation.reasons
    assert equation.bbox[1] <= 125 and equation.bbox[3] >= 140 and equation.bbox[2] < 310


def test_a_bare_decimal_away_from_any_maths_stays_text():
    def fill(page):
        write(page, 72, 100, ["The ratio of the two lengths measured in the lab was about"])
        page.insert_text((300, 130), "2.4", fontsize=BODY)
        write(page, 72, 160, ["which is close to the value the textbook gives for this ramp."])

    assert [e.kind for e in analyze(one_page(fill)).elements] == [Kind.PARAGRAPH] * 3


# ---------------------------------------------------------------- two-column reading order


def column_lines(prefix: str, count: int) -> list[str]:
    return [f"{prefix} line {i:02d} of this column body" for i in range(count)]


def test_a_heading_in_one_column_beside_a_gap_in_the_other_does_not_split_the_flow():
    def fill(page):
        y = write(page, 72, 100, column_lines("Left one", 12), fontsize=10, pitch=13)
        page.insert_text((72, y + 14), "2.4 Second Topic", fontsize=13, fontname="hebo")
        write(page, 72, y + 34, column_lines("Left two", 14), fontsize=10, pitch=13)
        y = write(page, 320, 100, column_lines("Right one", 10), fontsize=10, pitch=13)
        write(page, 320, y + 50, column_lines("Right two", 18), fontsize=10, pitch=13)

    elements = analyze(one_page(fill)).elements
    assert kinds_and_starts(elements, 12) == [
        (Kind.PARAGRAPH, "Left one lin"),
        (Kind.HEADING, "2.4 Second T"),
        (Kind.PARAGRAPH, "Left two lin"),
        (Kind.PARAGRAPH, "Right one li"),
        (Kind.PARAGRAPH, "Right two li"),
    ]
    assert elements[0].text.endswith("line 11 of this column body")  # not cut at line 10


def test_space_before_a_heading_in_one_column_does_not_split_the_other_column():
    def fill(page):
        write(page, 72, 100, column_lines("Left", 30), fontsize=10, pitch=12.5)
        y = write(page, 320, 100, column_lines("Right", 8), fontsize=10, pitch=12.5)
        page.insert_text((320, y + 25.5), "Results", fontsize=14, fontname="hebo")  # Heading 1
        write(page, 320, y + 44, column_lines("Right after", 15), fontsize=10, pitch=12.5)

    elements = analyze(one_page(fill)).elements
    assert kinds_and_starts(elements, 11) == [
        (Kind.PARAGRAPH, "Left line 0"),
        (Kind.PARAGRAPH, "Right line "),
        (Kind.HEADING, "Results"),
        (Kind.PARAGRAPH, "Right after"),
    ]
    assert elements[0].text.endswith("line 29 of this column body")


def test_a_narrow_heading_at_a_break_across_both_columns_is_still_full_width():
    def fill(page):
        write(page, 72, 100, column_lines("Left A", 5), fontsize=10, pitch=13)
        write(page, 320, 100, column_lines("Right A", 5), fontsize=10, pitch=13)
        page.insert_text((72, 190), "2.6 Next Section", fontsize=13, fontname="hebo")
        write(page, 72, 215, column_lines("Left B", 5), fontsize=10, pitch=13)
        write(page, 320, 215, column_lines("Right B", 5), fontsize=10, pitch=13)

    elements = analyze(one_page(fill)).elements
    assert [(e.text or "")[:8] for e in elements] == [
        "Left A l",
        "Right A ",
        "2.6 Next",
        "Left B l",
        "Right B ",
    ]


# ---------------------------------------------------------------- bullets


def test_bullets_in_the_symbol_font_are_not_inline_maths():
    items = ["Measure the length of the ramp.", "Time the cart from top to bottom."]

    def fill(page):
        for i, item in enumerate(items):
            page.insert_text((72, 100 + 16 * i), "\u2022", fontsize=BODY, fontname="symb")
            page.insert_text((90, 100 + 16 * i), item, fontsize=BODY)
        write(page, 72, 160, ["Some ordinary body text follows the list on the page."])

    elements = analyze(one_page(fill)).elements
    assert [(e.kind, e.list_marker, e.text) for e in elements] == [
        (Kind.LIST_ITEM, "\u00b7", items[0]),  # Symbol's bullet extracts as a middle dot
        (Kind.LIST_ITEM, "\u00b7", items[1]),
        (Kind.PARAGRAPH, None, "Some ordinary body text follows the list on the page."),
    ]


def test_private_use_symbol_bullets_are_not_maths_and_read_as_bullets():
    bullet = layout._Char("", 0, 0, 5, 5, 5, 11.0, "ABCDEF+SymbolMT", 0)
    alpha = layout._Char("", 0, 0, 5, 5, 5, 11.0, "ABCDEF+SymbolMT", 0)  # Symbol alpha
    assert not bullet.math and alpha.math
    assert layout._clean(" Measure the ramp") == "• Measure the ramp"


# ---------------------------------------------------------------- decoration


def logo_pages(count: int, *, photo_on: int | None = None) -> bytes:
    doc = pymupdf.open()
    for number in range(count):
        page = doc.new_page(width=612, height=792)
        page.insert_image(pymupdf.Rect(480, 18, 570, 48), pixmap=red_pixmap(120, 40))
        page.insert_text((72, 36), "PHYS 101 Lecture Notes", fontsize=9)
        write(page, 72, 120, [f"Body text on page {number + 1} about carts on ramps."])
        if number == photo_on:
            page.insert_image(pymupdf.Rect(150, 200, 350, 350), pixmap=red_pixmap())
    return doc.tobytes()


def test_a_logo_repeated_in_the_header_band_is_not_a_figure():
    layout_ = analyze(logo_pages(6, photo_on=2))
    figures = [e for e in layout_.elements if e.kind == Kind.FIGURE]
    assert [(f.page, f.bbox) for f in figures] == [(2, (150.0, 200.0, 350.0, 350.0))]
    assert texts(layout_.elements, Kind.PARAGRAPH) == [
        f"Body text on page {n} about carts on ramps." for n in range(1, 7)
    ]


def test_a_logo_on_a_single_page_is_still_a_figure():
    def fill(page):
        page.insert_image(pymupdf.Rect(480, 18, 570, 48), pixmap=red_pixmap(120, 40))
        write(page, 72, 120, ["A single page has nothing to compare its header band with."])

    assert [e.kind for e in analyze(one_page(fill)).elements] == [Kind.FIGURE, Kind.PARAGRAPH]


def watermarked_pages(count: int) -> bytes:
    doc = pymupdf.open()
    for number in range(count):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), "Notes Week 3", fontsize=20, fontname="hebo")
        rows = [f"Body line {i} on page {number + 1} explains how carts move." for i in range(20)]
        write(page, 72, 140, rows)
        point = pymupdf.Point(150, 600)
        page.insert_text(
            point,
            "Do Not Distribute",
            fontsize=60,
            color=(0.85, 0.85, 0.85),
            morph=(point, pymupdf.Matrix(-35)),
        )
    return doc.tobytes()


@pytest.mark.parametrize("count", [1, 3])
def test_a_diagonal_watermark_is_dropped_and_is_never_a_heading(count):
    layout_ = analyze(watermarked_pages(count))
    assert [e.kind for e in layout_.elements] == [Kind.HEADING, Kind.PARAGRAPH] * count
    assert texts(layout_.elements, Kind.HEADING) == ["Notes Week 3"] * count
    assert layout_.title == "Notes Week 3"
    assert [f.heading.level for f in heading_fixes(layout_.elements) if f.heading] == [1] * count


def test_rotated_text_is_never_a_heading():
    def fill(page):
        write(page, 72, 100, ["Body text sits in the middle of the page in the usual way."])
        page.insert_text((40, 500), "Draft for comment", fontsize=16, fontname="hebo", rotate=90)

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.PARAGRAPH]
    assert "Draft for comment" in texts(elements)


# ---------------------------------------------------------------- text over images and panels


def test_text_in_a_rounded_callout_box_stays_in_the_reading_flow():
    def fill(page):
        y = write(
            page, 72, 80, ["The cart moves along a straight track and we time it over a run."]
        )
        box = pymupdf.Rect(72, y + 4, 540, y + 74)
        page.draw_rect(box, color=(0.2, 0.3, 0.6), fill=(0.9, 0.93, 1.0), radius=0.08)
        page.insert_text((84, y + 26), "Key idea", fontsize=BODY, fontname="hebo")
        callout = [
            "Average speed is the total distance divided by the total time taken.",
            "It says nothing about how the speed changed during the trip.",
        ]
        write(page, 84, y + 42, callout)
        write(page, 72, y + 100, ["After the box the notes continue with ordinary prose."])

    elements = analyze(one_page(fill)).elements
    assert kinds_and_starts(elements, 20) == [
        (Kind.PARAGRAPH, "The cart moves along"),
        (Kind.HEADING, "Key idea"),
        (Kind.PARAGRAPH, "Average speed is the"),
        (Kind.PARAGRAPH, "After the box the no"),
    ]


def test_a_rounded_rectangle_drawn_around_a_curve_is_still_a_figure():
    def fill(page):
        write(page, 72, 90, ["The plot below shows the position of the cart against time."])
        page.draw_rect(pymupdf.Rect(100, 120, 300, 260), color=(0, 0, 0), radius=0.05)
        page.draw_bezier((110, 250), (150, 240), (220, 160), (290, 130), color=(0, 0, 1))
        write(page, 72, 300, ["The curve gets steeper because the cart speeds up."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.FIGURE, Kind.PARAGRAPH]


def test_text_on_a_slide_background_image_stays_text_and_the_image_is_dropped():
    def fill(page):
        page.insert_image(pymupdf.Rect(40, 40, 680, 500), pixmap=red_pixmap(200, 150))  # 84 %
        page.insert_text((80, 110), "Newton's Second Law", fontsize=28)
        lines = [
            "The net force on a body equals its mass times its acceleration.",
            "Doubling the force doubles the acceleration of the same cart.",
        ]
        for i, line in enumerate(lines):
            page.insert_text((80, 170 + 30 * i), line, fontsize=16)

    layout_ = analyze(one_page(fill, width=720, height=540))
    assert kinds_and_starts(layout_.elements, 19) == [
        (Kind.HEADING, "Newton's Second Law"),
        (Kind.PARAGRAPH, "The net force on a "),
    ]
    assert layout_.title == "Newton's Second Law"


def test_body_text_over_a_centred_watermark_image_stays_one_paragraph():
    def fill(page):
        page.insert_image(pymupdf.Rect(156, 250, 456, 550), pixmap=red_pixmap(200, 200))
        rows = [
            f"Line {i:02d} of the body text explains the motion of the cart." for i in range(40)
        ]
        write(page, 72, 80, rows, pitch=14)

    (paragraph,) = analyze(one_page(fill)).elements
    assert paragraph.kind == Kind.PARAGRAPH
    assert "Line 16 " in paragraph.text and paragraph.text.endswith(
        "Line 39 of the body text explains the motion of the cart."
    )


def test_a_short_label_inside_a_photo_still_belongs_to_the_figure():
    def fill(page):
        write(page, 72, 90, ["The photograph below shows the cart at the top of the ramp."])
        page.insert_image(pymupdf.Rect(150, 130, 350, 280), pixmap=red_pixmap())
        page.insert_text((160, 150), "cart", fontsize=9)
        write(page, 72, 320, ["The cart has not started moving yet in this photograph."])

    elements = analyze(one_page(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.FIGURE, Kind.PARAGRAPH]
    assert elements[1].text == "cart"


# ---------------------------------------------------------------- inline markers


def assert_markers_are_real(elements: list[Element]) -> None:
    """Every marker in any text is an inline child of that element, exactly once."""
    by_id = {e.id: e for e in elements}
    for element in elements:
        text = element.text or ""
        for marker_id in re.findall("⟦([^⟧]*)⟧", text):
            child = by_id[marker_id]
            assert child.kind == Kind.EQUATION and child.display == "inline"
            assert child.parent_id == element.id
            assert text.count(inline_marker(marker_id)) == 1
    for child in elements:
        if child.parent_id is not None:
            assert inline_marker(child.id) in (by_id[child.parent_id].text or "")


def test_marker_brackets_in_the_document_cannot_fake_inline_equations():
    html = "<p>The speed is <i>v</i> = <i>d</i>/<i>t</i> here. Spoof ⟦p1-e1⟧ and ⟦p1-e0⟧ text.</p>"
    elements = analyze(html_page(html)).elements
    assert_markers_are_real(elements)
    for element in elements:  # where the brackets survive as text, they are look-alikes
        if element.kind == Kind.EQUATION:
            assert "⟦" not in (element.text or "") and "⟧" not in (element.text or "")


DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@pytest.mark.skipif(not DEJAVU.exists(), reason="needs DejaVu Sans: marker glyphs in a text font")
def test_marker_brackets_in_a_text_font_are_neutralised():
    lines = [
        "The speed of the cart is v = d/t on the track.",
        "Spoof ⟦p1-e1⟧ and ⟦p1-e0⟧ here.",
    ]

    def fill(page):
        page.insert_font(fontname="dv", fontfile=str(DEJAVU))
        write(page, 72, 100, lines, fontname="dv")

    paragraph, equation = analyze(one_page(fill)).elements
    assert_markers_are_real([paragraph, equation])
    assert paragraph.text == (
        f"The speed of the cart is {inline_marker(equation.id)} on the track. "
        "Spoof 〚p1-e1〛 and 〚p1-e0〛 here."
    )


def test_marker_brackets_are_mapped_in_every_text_path():
    assert layout._clean("a ⟦p1-e1⟧ b") == "a 〚p1-e1〛 b"
    assert layout._tidy(["a ⟦x⟧ b"]) == ["a 〚x〛 b"]


# ---------------------------------------------------------------- MuPDF output


def damaged_content_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    write(page, 72, 100, ["Hello world, this is a paragraph."])
    xref = page.get_contents()[0]
    stream = doc.xref_stream(xref) + b"\nSecretStudentToken BT ET\n/Secret_Resource_Name gs\n"
    doc.update_stream(xref, stream)
    return doc.tobytes()


def test_mupdf_messages_quoting_the_pdf_are_not_printed(capfd):
    pymupdf.TOOLS.mupdf_display_errors(True)  # as another library might leave it
    data = damaged_content_pdf()
    elements = analyze(data).elements
    assert texts(elements) == ["Hello world, this is a paragraph."]
    crop(data, elements[0])
    out, err = capfd.readouterr()
    assert "Secret" not in out + err and "MuPDF" not in out + err
    assert pymupdf.TOOLS.mupdf_warnings() == ""  # the in-memory warning store is emptied


# ---------------------------------------------------------------- margins


def test_margin_text_that_stands_out_and_changes_per_page_is_kept():
    doc = pymupdf.open()
    for q in range(1, 5):
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 45), f"Problem {q} ({5 * q} points)", fontsize=14)  # top band
        rows = [
            f"Part {i}: a ball is thrown upward at {q + i} m/s; find its height." for i in range(30)
        ]
        write(page, 40, 75, rows)
        page.insert_text((40, 775), f"Physics 101 handout, sheet {q}", fontsize=8)  # footer
    layout_ = analyze(doc.tobytes())
    assert texts(layout_.elements, Kind.HEADING) == [
        f"Problem {q} ({5 * q} points)" for q in range(1, 5)
    ]
    assert not [e for e in layout_.elements if "Physics 101" in (e.text or "")]


def test_a_fixed_running_header_is_dropped_even_in_a_large_font():
    doc = pymupdf.open()
    for number in range(1, 4):
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 40), "Kinematics Handout", fontsize=16, fontname="hebo")
        write(page, 40, 100, [f"Body text that differs on page {number} of the handout."])
    assert texts(analyze(doc.tobytes()).elements) == [
        f"Body text that differs on page {n} of the handout." for n in range(1, 4)
    ]


# ---------------------------------------------------------------- cost


def dense_fragment_page() -> bytes:
    """16 columns of tiny "12" rows, each ending in a maths row: a crafted worst case."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    writer = pymupdf.TextWriter(page.rect)
    font = pymupdf.Font("helv")
    x = 20.0
    while x < 580:
        y = 30.0
        while y < 740:
            writer.append((x, y), "12", font=font, fontsize=6)
            y += 6.5
        writer.append((x, y), "a=b", font=font, fontsize=6)
        x += 36
    writer.write_text(page)
    return doc.tobytes(garbage=3, deflate=True)


def test_fragment_resolution_stays_near_linear_on_a_dense_page(monkeypatch):
    calls = []
    real = layout._math_neighbours

    def counting(row, other):
        calls.append(1)
        return real(row, other)

    monkeypatch.setattr(layout, "_math_neighbours", counting)
    started = time.perf_counter()
    analyze(dense_fragment_page())
    elapsed = time.perf_counter() - started
    rows = 16 * 110  # about 1,760 fragment rows
    assert len(calls) < 20 * rows  # the old fixed point made a full n x n pass per row added
    assert elapsed < 20  # 66 s before the fix, well under a second after
