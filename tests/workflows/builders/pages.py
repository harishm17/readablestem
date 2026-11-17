"""Pages whose layout is the test: turned and sideways pages, mixed sizes, bleed, two pages
on a sheet, handouts, watermarks and stamps, margin numbers, running headers, annotations,
hidden layers and text nobody can see. First made in the edge-case round of November 10, 2026.

Every paragraph says its own number ("Paragraph 3 begins here ... ends paragraph 3"), so a
test can tell whether text came out whole and in order without a labeled answer.
"""

from __future__ import annotations

import pymupdf

from . import fixture
from .rawpdf import save

ORIGIN = "edge-case round ZE2, 2026-11-10"
CSS = (
    "body{font-family:sans-serif;font-size:11pt;line-height:1.35} h1{font-size:18pt} "
    "h2{font-size:14pt} p{margin:0 0 8pt 0}"
)
LETTER = {"width": 612, "height": 792}
BODY = pymupdf.Rect(72, 72, 540, 720)
PLAIN = "helv"
BOLD = "hebo"
RIVER = (
    "The river carries sediment from the mountains to the sea and the delta grows a little "
    "every year as the current slows and drops the finest grains near the coast line"
)


def paragraph(number: int) -> str:
    return (
        f"Paragraph {number} begins here. This sentence {number}a explains one idea about cell biology "
        f"in plain words. Sentence {number}b continues the same idea with a second clause, so a reader "
        f"can follow it. Sentence {number}c ends paragraph {number}."
    )


def _prose(title: str, first: int, count: int) -> str:
    return f"<h1>{title}</h1>" + "".join(
        f"<p>{paragraph(n)}</p>" for n in range(first, first + count)
    )


def _write(page: pymupdf.Page, where: pymupdf.Rect, html: str) -> None:
    page.insert_htmlbox(where, html, css=CSS)


def _prose_page(doc: pymupdf.Document, title: str, first: int, count: int) -> pymupdf.Page:
    page = doc.new_page(**LETTER)
    _write(page, BODY, _prose(title, first, count))
    return page


def _schedule_html(title: str) -> pymupdf.Document:
    """A landscape page with a schedule table laid out by the HTML engine (no ruling lines)."""
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)
    rows = "".join(
        f"<tr><td>Week {w}</td><td>Topic {w}: membranes part {w}</td><td>Lab {w} report due</td>"
        f"<td>{w * 5}%</td></tr>"
        for w in range(1, 9)
    )
    _write(
        page,
        pymupdf.Rect(54, 54, 738, 558),
        f"<h2>{title}</h2><p>This landscape page holds the course schedule. Its first sentence must be "
        "read upright.</p><table border='1' cellpadding='4'><tr><th>Week</th><th>Topic</th><th>Due</th>"
        f"<th>Weight</th></tr>{rows}</table>",
    )
    return doc


def _schedule_ruled(title: str) -> pymupdf.Document:
    """The same schedule with a drawn grid, and notes under the table."""
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)
    page.insert_text((54, 70), title, fontsize=16, fontname=BOLD)
    page.insert_text(
        (54, 95),
        "This landscape page holds the course schedule. Its first sentence must be read upright.",
        fontsize=11,
    )
    page.insert_text((54, 112), "Read the notes under the table before the first lab.", fontsize=11)
    columns = [54, 154, 454, 634, 738]
    top, row_height = 130, 22
    rows = [["Week", "Topic", "Due", "Weight"]] + [
        [f"Week {w}", f"Topic {w}: membranes part {w}", f"Lab {w} report due", f"{w * 5}%"]
        for w in range(1, 9)
    ]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            page.insert_text(
                (columns[c] + 4, top + r * row_height + 15),
                text,
                fontsize=10,
                fontname=BOLD if r == 0 else PLAIN,
            )
    for r in range(len(rows) + 1):
        page.draw_line(
            (columns[0], top + r * row_height), (columns[-1], top + r * row_height), width=0.6
        )
    for x in columns:
        page.draw_line((x, top), (x, top + len(rows) * row_height), width=0.6)
    under = top + len(rows) * row_height
    page.insert_text(
        (54, under + 30), "Note 1. Reports are due at noon on the day shown.", fontsize=10
    )
    page.insert_text(
        (54, under + 46),
        "Note 2. The weights add up to one hundred and eighty percent by design of this test.",
        fontsize=10,
    )
    return doc


# ---- A: page geometry


def _turned(turn: int) -> bytes:
    doc = pymupdf.open()
    _prose_page(doc, "Rotated page test", 1, 3)
    sheet = doc.new_page(**LETTER)
    sheet.show_pdf_page(
        sheet.rect, _schedule_html(f"Schedule on a page rotated {turn}"), 0, rotate=turn
    )
    sheet.set_rotation(turn)  # the content is drawn sideways and the viewer turns the page upright
    _prose_page(doc, "After the rotated page", 4, 2)
    return save(doc)


@fixture("rotate-90.pdf", origin=ORIGIN, shows="a landscape table on a page with /Rotate 90")
def page_rotated_90() -> bytes:
    return _turned(90)


@fixture("rotate-270.pdf", origin=ORIGIN, shows="a landscape table on a page with /Rotate 270")
def page_rotated_270() -> bytes:
    return _turned(270)


@fixture(
    "sideways-no-rotate.pdf",
    origin=ORIGIN,
    shows="a landscape table set sideways on a portrait page, with no /Rotate",
)
def table_sideways_without_rotate() -> bytes:
    doc = pymupdf.open()
    _prose_page(doc, "Sideways table test", 1, 3)
    sheet = doc.new_page(**LETTER)
    sheet.show_pdf_page(sheet.rect, _schedule_html("Schedule set sideways"), 0, rotate=90)
    _prose_page(doc, "After the sideways page", 4, 2)
    return save(doc)


@fixture("sideways-270.pdf", origin=ORIGIN, shows="the sideways table turned the other way")
def table_sideways_the_other_way() -> bytes:
    doc = pymupdf.open()
    _prose_page(doc, "Sideways table test two", 1, 2)
    sheet = doc.new_page(**LETTER)
    sheet.show_pdf_page(
        sheet.rect, _schedule_html("Schedule set sideways the other way"), 0, rotate=270
    )
    return save(doc)


@fixture(
    "mixed-sizes.pdf", origin=ORIGIN, shows="letter, A4, legal, A5 and tabloid pages in one file"
)
def mixed_page_sizes() -> bytes:
    doc = pymupdf.open()
    sizes = [
        ("Letter", 612, 792),
        ("A4", 595, 842),
        ("Legal", 612, 1008),
        ("A5", 420, 595),
        ("Tabloid landscape", 1224, 792),
    ]
    for number, (name, width, height) in enumerate(sizes):
        page = doc.new_page(width=width, height=height)
        _write(
            page,
            pymupdf.Rect(50, 50, width - 50, height - 50),
            _prose(f"Page {number + 1} is {name}", 10 * number + 1, 2),
        )
        page.insert_text((width / 2 - 10, height - 25), str(number + 1), fontsize=9)
    return save(doc)


@fixture(
    "cropbox-bleed.pdf",
    origin=ORIGIN,
    shows="a print file: a slug line, bleed text and crop marks outside the crop box",
)
def content_outside_the_crop_box() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=700, height=900)
    _write(page, pymupdf.Rect(94, 94, 606, 700), _prose("Crop box test", 1, 3))
    page.insert_text(
        (60, 880), "SLUG job_4471_bio101.indd  3  11/3/26  10:22 AM  PROOF ONLY", fontsize=7
    )
    page.insert_text((610, 400), "Bleed text outside the trim", fontsize=9)
    page.insert_text(
        (480, 760),
        "This line runs off the trimmed page edge into the bleed area here.",
        fontsize=11,
    )
    for x, y in [(44, 44), (656, 44), (44, 836), (656, 836)]:
        page.draw_line((x - 30, y), (x - 6, y), width=0.3)
        page.draw_line((x, y - 30), (x, y - 6), width=0.3)
    page.set_cropbox(pymupdf.Rect(44, 44, 656, 836))
    return save(doc)


def _book() -> pymupdf.Document:
    """Four small book pages with a running header and a page number."""
    book = pymupdf.open()
    for number in range(4):
        page = book.new_page(width=396, height=612)
        _write(
            page,
            pymupdf.Rect(36, 50, 360, 570),
            f"<h2>Section {number + 1}</h2>"
            + "".join(f"<p>{paragraph(10 * number + k)}</p>" for k in range(1, 4)),
        )
        page.insert_text((36, 32), "Introductory Biology  Chapter 3", fontsize=8)
        page.insert_text((190, 595), str(number + 41), fontsize=8)
    return book


def _two_on_a_sheet(pairs: list[tuple[int, int]]) -> bytes:
    book = _book()
    doc = pymupdf.open()
    for left, right in pairs:
        sheet = doc.new_page(width=792, height=612)
        sheet.show_pdf_page(pymupdf.Rect(0, 0, 396, 612), book, left)
        sheet.show_pdf_page(pymupdf.Rect(396, 0, 792, 612), book, right)
    return save(doc)


@fixture("two-up.pdf", origin=ORIGIN, shows="two book pages side by side on each landscape sheet")
def two_book_pages_per_sheet() -> bytes:
    return _two_on_a_sheet([(0, 1), (2, 3)])


@fixture(
    "booklet.pdf",
    origin=ORIGIN,
    shows="booklet order: pages 4 and 1 on the front, 2 and 3 on the back",
)
def booklet_imposition() -> bytes:
    return _two_on_a_sheet([(3, 0), (1, 2)])


def _slides() -> pymupdf.Document:
    slides = pymupdf.open()
    for number in range(6):
        letter = chr(65 + number)
        page = slides.new_page(width=960, height=540)
        page.draw_rect(page.rect, color=(0.2, 0.3, 0.5), fill=(0.95, 0.96, 1.0))
        _write(
            page,
            pymupdf.Rect(60, 40, 900, 500),
            f"<h1 style='font-size:36pt'>Slide {number + 1}: Topic {letter}</h1><ul style='font-size:24pt'>"
            f"<li>Point {number + 1}.1 about topic {letter}</li><li>Point {number + 1}.2 about topic {letter}</li>"
            f"<li>Point {number + 1}.3 closes slide {number + 1}</li></ul>",
        )
    return slides


@fixture("handout-3-up.pdf", origin=ORIGIN, shows="three slides a page with note lines beside each")
def handout_three_slides_a_page() -> bytes:
    slides = _slides()
    doc = pymupdf.open()
    for sheet_number in range(2):
        sheet = doc.new_page(**LETTER)
        sheet.insert_text((54, 40), "BIO 101 Lecture 7 handout", fontsize=9)
        for k in range(3):
            top = 60 + k * 240
            frame = pymupdf.Rect(54, top, 54 + 280, top + 157.5)
            sheet.show_pdf_page(frame, slides, 3 * sheet_number + k)
            sheet.draw_rect(frame, color=(0, 0, 0), width=0.5)
            for line in range(8):
                y = top + 10 + line * 20
                sheet.draw_line((360, y), (560, y), color=(0.6, 0.6, 0.6), width=0.5)
        sheet.insert_text((300, 770), str(sheet_number + 1), fontsize=9)
    return save(doc)


@fixture("handout-6-up.pdf", origin=ORIGIN, shows="six slides on one page, read across then down")
def handout_six_slides_a_page() -> bytes:
    slides = _slides()
    doc = pymupdf.open()
    sheet = doc.new_page(**LETTER)
    sheet.insert_text((54, 40), "BIO 101 Lecture 7 handout (6 per page)", fontsize=9)
    for number in range(6):
        column, row = number % 2, number // 2
        frame = pymupdf.Rect(
            54 + column * 260, 70 + row * 230, 54 + column * 260 + 240, 70 + row * 230 + 135
        )
        sheet.show_pdf_page(frame, slides, number)
        sheet.draw_rect(frame, color=(0, 0, 0), width=0.5)
    return save(doc)


# ---- AR: the same rotations with a ruled table, and a control


def _ruled(name: str, mode: str, turn: int = 0) -> bytes:
    doc = pymupdf.open()
    _prose_page(doc, f"Test {name}", 1, 3)
    schedule = _schedule_ruled(f"Schedule ({name})")
    if mode == "plain":
        sheet = doc.new_page(width=792, height=612)
        sheet.show_pdf_page(sheet.rect, schedule, 0)
    else:
        sheet = doc.new_page(**LETTER)
        sheet.show_pdf_page(sheet.rect, schedule, 0, rotate=turn)
        if mode == "rotate":
            sheet.set_rotation(turn)
    _prose_page(doc, "After the schedule", 4, 2)
    return save(doc)


@fixture(
    "ruled-landscape.pdf", origin=ORIGIN, shows="control: a ruled table on a plain landscape page"
)
def ruled_table_on_a_landscape_page() -> bytes:
    return _ruled("ar0_landscape_plain", "plain")


@fixture("ruled-rotate-90.pdf", origin=ORIGIN, shows="a ruled table on a page with /Rotate 90")
def ruled_table_rotated_90() -> bytes:
    return _ruled("ar1_rotate90", "rotate", 90)


@fixture("ruled-rotate-270.pdf", origin=ORIGIN, shows="a ruled table on a page with /Rotate 270")
def ruled_table_rotated_270() -> bytes:
    return _ruled("ar2_rotate270", "rotate", 270)


@fixture("ruled-sideways-90.pdf", origin=ORIGIN, shows="a ruled table set sideways, no /Rotate")
def ruled_table_sideways_90() -> bytes:
    return _ruled("ar3_sideways90", "sideways", 90)


@fixture("ruled-sideways-270.pdf", origin=ORIGIN, shows="a ruled table set sideways the other way")
def ruled_table_sideways_270() -> bytes:
    return _ruled("ar4_sideways270", "sideways", 270)


# ---- B: marks that are not content


def _three_pages(title: str) -> pymupdf.Document:
    doc = pymupdf.open()
    for number in range(3):
        heading = f"{title} page 1" if number == 0 else f"Part {number + 1}"
        page = _prose_page(doc, heading, 10 * number + 1, 4)
        page.insert_text((300, 760), str(number + 1), fontsize=9)
    return doc


@fixture(
    "watermark-diagonal.pdf",
    origin=ORIGIN,
    shows="a gray diagonal DRAFT behind the text of three pages",
)
def diagonal_draft_watermark() -> bytes:
    doc = _three_pages("Diagonal watermark")
    for page in doc:
        page.insert_text(
            (150, 560),
            "DRAFT",
            fontsize=120,
            color=(0.85, 0.85, 0.85),
            morph=(pymupdf.Point(306, 396), pymupdf.Matrix(-35)),
            overlay=False,
        )
    return save(doc)


@fixture(
    "watermark-horizontal.pdf",
    origin=ORIGIN,
    shows="a gray DO NOT DISTRIBUTE straight across the middle of three pages",
)
def horizontal_watermark() -> bytes:
    doc = _three_pages("Horizontal watermark")
    for page in doc:
        page.insert_text(
            (110, 400), "DO NOT DISTRIBUTE", fontsize=40, color=(0.8, 0.8, 0.8), overlay=False
        )
    return save(doc)


@fixture("watermark-one-page.pdf", origin=ORIGIN, shows="the horizontal mark on a file of one page")
def horizontal_watermark_on_one_page() -> bytes:
    doc = pymupdf.open()
    page = _prose_page(doc, "One page with a mark", 1, 5)
    page.insert_text(
        (110, 400), "DO NOT DISTRIBUTE", fontsize=40, color=(0.8, 0.8, 0.8), overlay=False
    )
    return save(doc)


@fixture(
    "watermark-over-text.pdf",
    origin=ORIGIN,
    shows="a red see-through diagonal mark drawn over the text of one page",
)
def diagonal_mark_over_the_text() -> bytes:
    doc = pymupdf.open()
    page = _prose_page(doc, "One page with a diagonal over the text", 1, 5)
    page.insert_text(
        (180, 520),
        "SOLUTIONS - INSTRUCTOR COPY",
        fontsize=30,
        color=(1, 0, 0),
        fill_opacity=0.35,
        morph=(pymupdf.Point(306, 396), pymupdf.Matrix(-30)),
    )
    return save(doc)


@fixture(
    "stamp.pdf", origin=ORIGIN, shows="a red RECEIVED stamp with a date, tilted, on page 1 of 3"
)
def received_stamp() -> bytes:
    doc = _three_pages("Stamp")
    tilt = (pymupdf.Point(380, 140), pymupdf.Matrix(-12))
    doc[0].insert_text((380, 140), "RECEIVED", fontsize=16, color=(0.8, 0, 0), morph=tilt)
    doc[0].insert_text((380, 160), "NOV 03 2026", fontsize=12, color=(0.8, 0, 0), morph=tilt)
    return save(doc)


@fixture(
    "line-numbers.pdf",
    origin=ORIGIN,
    shows="a reading passage with a number in the margin of every line",
)
def margin_line_numbers() -> bytes:
    doc = pymupdf.open()
    words = RIVER.split()
    for sheet in range(2):
        page = doc.new_page(**LETTER)
        page.insert_text(
            (72, 72),
            "Reading passage with line numbers" if sheet == 0 else "Passage, continued",
            fontsize=14,
        )
        for line in range(30):
            y = 90 + 20 * line
            start = (line * 7) % 20
            page.insert_text((96, y), " ".join(words[start : start + 12]), fontsize=11)
            page.insert_text((60, y), str(1 + sheet * 30 + line), fontsize=8, color=(0.4, 0.4, 0.4))
    return save(doc)


@fixture(
    "download-banner.pdf",
    origin=ORIGIN,
    shows="a publisher's download line: across the top of page 1 and up the right margin of every page",
)
def publisher_download_banner() -> bytes:
    doc = _three_pages("Download banner")
    doc[0].insert_text(
        (72, 30),
        "Downloaded from https://journals.example.org/doi/10.1000/xyz123 by University of Somewhere on "
        "11/03/2026. Personal use only.",
        fontsize=7,
    )
    for page in doc:
        page.insert_text(
            (590, 650),
            "Downloaded from journals.example.org by guest on 3 November 2026",
            fontsize=7,
            rotate=90,
        )
    return save(doc)


@fixture(
    "margin-identifier.pdf",
    origin=ORIGIN,
    shows="a one-page paper with a large vertical preprint identifier in the left margin",
)
def vertical_margin_identifier() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(**LETTER)
    _write(
        page,
        pymupdf.Rect(90, 72, 540, 720),
        _prose("One page with a vertical margin identifier", 1, 6),
    )
    page.insert_text(
        (40, 560),
        "arXiv:2611.01234v2  [physics.ed-ph]  3 Nov 2026",
        fontsize=20,
        color=(0.5, 0.5, 0.5),
        rotate=90,
    )
    return save(doc)


@fixture(
    "alternating-headers.pdf",
    origin=ORIGIN,
    shows="eight pages whose running header and page number swap sides on left and right pages",
)
def alternating_running_headers() -> bytes:
    doc = pymupdf.open()
    for number in range(8):
        title = "Chapter 2 Sets and Functions" if number == 0 else f"2.{number} Subsection {number}"
        page = _prose_page(doc, title, 10 * number + 1, 3)
        folio = number + 23
        if folio % 2 == 0:
            page.insert_text((72, 45), str(folio), fontsize=9)
            page.insert_text((250, 45), "CHAPTER 2. SETS AND FUNCTIONS", fontsize=9)
        else:
            page.insert_text((250, 45), f"2.{number} SUBSECTION {number}", fontsize=9)
            page.insert_text((530, 45), str(folio), fontsize=9)
    return save(doc)


# ---- C: annotations and hidden content


def _lines(
    page: pymupdf.Page, items: list[str], *, top: float = 90, gap: float = 20
) -> list[pymupdf.Rect]:
    """Write one line per item at 11 pt; return where each line is."""
    boxes = []
    for text in items:
        page.insert_text((72, top), text, fontsize=11, fontname=PLAIN)
        width = pymupdf.get_text_length(text, fontname=PLAIN, fontsize=11)
        boxes.append(pymupdf.Rect(72, top - 11, 72 + width, top + 3))
        top += gap
    return boxes


WHEN = "D:20261103120000Z"


def _settle(annot: pymupdf.Annot, **info: str) -> None:
    """Give the annotation fixed dates and a fixed name, so two runs write the same bytes."""
    annot.set_info(creationDate=WHEN, modDate=WHEN, **info)
    annot.update()
    annot.set_info(creationDate=WHEN, modDate=WHEN)
    doc = annot.parent.parent
    doc.xref_set_key(annot.xref, "NM", f"(mark-{annot.parent.number}-{annot.xref})")


@fixture(
    "annotations.pdf",
    origin=ORIGIN,
    shows="lab steps corrected by a highlight, a strikeout, a text box, a sticky note, a box, ink, "
    "an attached file and a hidden answer",
)
def annotations_that_change_the_meaning() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(**LETTER)
    page.insert_text((72, 60), "Lab 4 instructions", fontsize=18, fontname=BOLD)
    step = _lines(
        page,
        [
            "Step 1. Put on gloves and goggles before you open the cabinet.",
            "Step 2. The report is due May 1 at noon in the lab office.",
            "Step 3. Measure 25 mL of the buffer into the beaker.",
            "Step 4. Heat the beaker to 40 degrees and hold it there for five minutes.",
            "Step 5. Record the colour change in your notebook.",
            "Step 6. Dispose of the waste in the labelled container only.",
        ],
    )
    _settle(page.add_highlight_annot(step[0]), content="Safety first: this step is graded.")
    _settle(
        page.add_strikeout_annot(
            pymupdf.Rect(step[1].x0 + 105, step[1].y0, step[1].x0 + 150, step[1].y1)
        )
    )
    _settle(
        page.add_freetext_annot(
            pymupdf.Rect(330, 105, 560, 135),
            "Moved to May 8",
            fontsize=12,
            text_color=(0.8, 0, 0),
            fill_color=(1, 1, 0.8),
        )
    )
    _settle(
        page.add_text_annot((560, 128), "Correction: use 30 mL, not 25 mL. The 25 is a typo."),
        title="Prof. Lee",
    )
    box = page.add_rect_annot(pymupdf.Rect(66, step[3].y0 - 3, step[3].x1 + 6, step[3].y1 + 3))
    box.set_colors(stroke=(1, 0, 0))
    _settle(box, content="Not 40: use 37 degrees.")
    ink = page.add_ink_annot([[(400, 210), (420, 200), (440, 215), (460, 198), (480, 212)]])
    ink.set_colors(stroke=(0, 0, 1))
    _settle(ink)
    page.insert_text(
        (72, 260), "Questions about safety go to the lab manager.", fontsize=11, fontname=PLAIN
    )
    _settle(
        page.add_file_annot(
            (540, 300),
            b"Data sheet: buffer pH 7.4, 0.1 M phosphate.",
            "buffer_datasheet.txt",
            desc="Buffer data sheet",
        )
    )
    doc.embfile_add(
        "rubric.txt", b"Rubric: 10 points for safety, 20 for data.", desc="Grading rubric"
    )
    hidden = page.add_freetext_annot(
        pymupdf.Rect(72, 300, 400, 320), "Answer key: the colour turns blue.", fontsize=11
    )
    hidden.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
    _settle(hidden)
    for xref in range(1, doc.xref_length()):  # an attached file records when it was attached
        if doc.xref_get_key(xref, "Type")[1] == "/EmbeddedFile":
            doc.xref_set_key(xref, "Params/CreationDate", f"({WHEN})")
            doc.xref_set_key(xref, "Params/ModDate", f"({WHEN})")
    return save(doc)


QUIZ = [
    ("Which molecule lowers the activation energy of a reaction?", "Answer: an enzyme (option C)."),
    ("At what pH does pepsin work best?", "Answer: about pH 2 (option A)."),
    (
        "What is the name of the site where the substrate binds?",
        "Answer: the active site (option D).",
    ),
]


@fixture(
    "layer-answer-key-off.pdf", origin=ORIGIN, shows="an answer key on a layer that is switched off"
)
def answer_key_on_a_hidden_layer() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(**LETTER)
    key = doc.add_ocg("Answer key", on=False)
    questions = doc.add_ocg("Questions", on=True)
    page.insert_text((72, 60), "Quiz 3: enzymes", fontsize=18, fontname=BOLD)
    for number, (question, answer) in enumerate(QUIZ, start=1):
        y = 40 + 60 * number
        page.insert_text(
            (72, y), f"{number}. {question}", fontsize=11, fontname=PLAIN, oc=questions
        )
        page.insert_text(
            (90, y + 18), answer, fontsize=11, fontname=PLAIN, color=(0.8, 0, 0), oc=key
        )
    return save(doc)


@fixture(
    "layer-answer-key-on.pdf",
    origin=ORIGIN,
    shows="control: the same answer key on a layer that is on",
)
def answer_key_on_a_visible_layer() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(**LETTER)
    key = doc.add_ocg("Answer key", on=True)
    page.insert_text((72, 60), "Quiz 3: enzymes (key on)", fontsize=18, fontname=BOLD)
    page.insert_text((72, 100), f"1. {QUIZ[0][0]}", fontsize=11, fontname=PLAIN)
    page.insert_text((90, 118), QUIZ[0][1], fontsize=11, fontname=PLAIN, color=(0.8, 0, 0), oc=key)
    return save(doc)


@fixture(
    "invisible-text.pdf",
    origin=ORIGIN,
    shows="a worksheet with text a sighted reader cannot see: white, tiny, render mode 3, off the page, "
    "under a picture, same color as its box, near white, 10% opaque",
)
def text_nobody_can_see() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(**LETTER)

    def line(at: tuple[float, float], text: str, **style: object) -> None:
        page.insert_text(at, text, **{"fontsize": 11, "fontname": PLAIN, **style})

    page.insert_text((72, 60), "Worksheet 5: cell membranes", fontsize=18, fontname=BOLD)
    _lines(
        page,
        [
            "1. Name the two layers of the membrane.",
            "2. Which molecules pass the membrane freely?",
            "3. What does a channel protein do?",
        ],
        top=100,
        gap=40,
    )
    line(
        (72, 118),
        "WHITE: The answer to question 1 is phospholipid heads and tails.",
        color=(1, 1, 1),
    )
    line((72, 158), "TINY: The answer to question 2 is oxygen and carbon dioxide.", fontsize=1.0)
    line((300, 158), "MICRO: grader note, give half marks for water.", fontsize=0.3)
    line((72, 198), "MODE3: The answer to question 3 is it lets ions through.", render_mode=3)
    line((72, 230), "OFFPAGE: Hidden note placed beyond the right edge of the page.")
    line((640, 250), "OFFPAGE2: this whole line is to the right of the page.")
    line((72, 300), "UNDERIMG: This sentence sits under an opaque picture.")
    cover = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 10), 0)
    cover.set_rect(cover.irect, (120, 160, 200))
    page.insert_image(pymupdf.Rect(66, 285, 420, 305), pixmap=cover)
    page.draw_rect(pymupdf.Rect(66, 320, 420, 340), color=(0, 0, 0), fill=(0, 0, 0))
    line((72, 335), "BLACKONBLACK: The quiz is on Friday in room 210.", color=(0, 0, 0))
    line((72, 370), "NEARWHITE: Almost white text, grey level 0.96.", color=(0.96, 0.96, 0.96))
    line(
        (72, 390),
        "LIGHTGREY: Light grey text that a sighted reader can read, grey 0.75.",
        color=(0.75, 0.75, 0.75),
    )
    line((72, 420), "LOWALPHA: Text at ten percent opacity.", fill_opacity=0.1)
    line((72, 440), "YELLOW: Pale yellow text on white.", color=(1, 1, 0.6))
    page.draw_rect(pymupdf.Rect(66, 450, 420, 470), color=(0.2, 0.2, 0.6), fill=(0.2, 0.2, 0.6))
    line((72, 465), "SAMECOLOUR: Navy text on a navy box.", color=(0.2, 0.2, 0.6))
    line((72, 500), "4. Explain why the membrane is called a fluid mosaic.")
    return save(doc)


@fixture("clipped-text.pdf", origin=ORIGIN, shows="a line of text cut away by a clip path")
def text_cut_away_by_a_clip_path() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(**LETTER)
    page.insert_text((72, 60), "Clip test", fontsize=18, fontname=BOLD)
    page.insert_text((72, 100), "VISIBLE: This line is visible.", fontsize=11, fontname=PLAIN)
    page.clean_contents()
    font = next(f[4] for f in page.get_fonts() if f[3] == "Helvetica")
    xref = page.get_contents()[0]
    clipped = f"\nq 0 0 1 1 re W n BT /{font} 11 Tf 72 650 Td (CLIPPED: The answer is forty two.) Tj ET Q\n"
    doc.update_stream(xref, doc.xref_stream(xref) + clipped.encode())
    return save(doc)


# ---- CI: one kind of annotation at a time

LAB = (
    "<h1>Lab 4 instructions</h1>"
    "<p>Before the lab, read the safety sheet and put on gloves and goggles. The report is due May 1 at "
    "noon in the lab office, and late reports lose ten percent a day.</p>"
    "<p>Measure 25 mL of the buffer into the beaker. Heat the beaker to 40 degrees and hold it there for "
    "five minutes, then record the colour change in your notebook.</p>"
    "<p>Dispose of the waste in the labelled container only. Questions about safety go to the lab "
    "manager.</p>"
)


def _lab_page() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    page = doc.new_page(**LETTER)
    page.insert_htmlbox(pymupdf.Rect(72, 72, 460, 500), LAB, css=CSS)
    return doc, page


@fixture(
    "annot-strikeout-caret.pdf",
    origin=ORIGIN,
    shows="a date struck out, with a caret that gives the new date",
)
def strikeout_with_a_caret() -> bytes:
    doc, page = _lab_page()
    date = page.search_for("May 1")[0]
    _settle(page.add_strikeout_annot(date))
    _settle(page.add_caret_annot(pymupdf.Point(date.x1, date.y1)), content="May 8")
    return save(doc)


@fixture(
    "annot-freetext-margin.pdf",
    origin=ORIGIN,
    shows="a typed note in the margin that moves a due date",
)
def text_box_in_the_margin() -> bytes:
    doc, page = _lab_page()
    _settle(
        page.add_freetext_annot(
            pymupdf.Rect(470, 150, 590, 190),
            "Due date moved to May 8",
            fontsize=10,
            text_color=(0.8, 0, 0),
        )
    )
    return save(doc)


@fixture(
    "annot-freetext-inline.pdf",
    origin=ORIGIN,
    shows="a typed correction placed just above the number it corrects",
)
def text_box_above_a_number() -> bytes:
    doc, page = _lab_page()
    amount = page.search_for("25 mL")[0]
    _settle(
        page.add_freetext_annot(
            pymupdf.Rect(amount.x0, amount.y0 - 14, amount.x0 + 90, amount.y0 - 1),
            "use 30 mL",
            fontsize=9,
            text_color=(0.8, 0, 0),
        )
    )
    return save(doc)


@fixture("annot-sticky-note.pdf", origin=ORIGIN, shows="a sticky note whose text corrects a number")
def sticky_note() -> bytes:
    doc, page = _lab_page()
    _settle(page.add_text_annot((470, 160), "Correction: use 30 mL, not 25 mL."))
    return save(doc)


@fixture("annot-highlight.pdf", origin=ORIGIN, shows="a highlighted phrase")
def highlighted_phrase() -> bytes:
    doc, page = _lab_page()
    _settle(page.add_highlight_annot(page.search_for("late reports lose ten percent a day")[0]))
    return save(doc)


@fixture("annot-underline.pdf", origin=ORIGIN, shows="an underlined phrase with a comment")
def underlined_phrase_with_a_comment() -> bytes:
    doc, page = _lab_page()
    _settle(
        page.add_underline_annot(page.search_for("Dispose of the waste")[0]), content="Important"
    )
    return save(doc)
