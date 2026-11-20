"""WF19, WF22, WF23 and WF24. Files made for the test, uploaded as the owner and read where a
person reads them: the job page and its checks, the page picture and the student's file.

Nothing here comes from the corpus. Each file is built by `builders` or by a function below,
and its model answers are not recorded: a step that needs one carries on without it, as it
does in production when a model fails, so what is read is what a person gets then. One test
a walk, and it holds what the walk found that works; what it found that does not is in the
register (F-505, F-509, F-511, F-515, F-541).

Not walked here, and why: WF20 (the page check runs from `readablestem eval page-audit` and
in no job; its card waits with WS15.3) and WF21 (the offer, its price and the choice are read
by `tests/test_web_image_pages_option.py`; reading the pages needs a recorded model answer).
"""

from __future__ import annotations

import io
import math
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import harness
import lxml.html
import pymupdf
import pytest
from builders import load

pytestmark = pytest.mark.workflow

READY = "Ready to post"


@pytest.fixture(scope="module")
def made(site: harness.ReplaySite) -> Callable[[str, bytes], str]:
    """Converts a file once for the whole module and gives its job's id."""
    done: dict[str, str] = {}

    def convert(name: str, data: bytes) -> str:
        if name not in done:
            done[name] = site.convert_file(name, data)
        return done[name]

    return convert


def student_text(site: harness.ReplaySite, job_id: str) -> str:
    return harness.words(site.student_file(job_id).xpath("//main")[0])


def state(site: harness.ReplaySite, job_id: str) -> str:
    """The state the documents list tells for the job."""
    (badge,) = site.page("/app").xpath(
        f'//a[@id="doc-{job_id}"]/ancestor::tr[1]//*[contains(@class, "status-badge")]'
    )
    return harness.words(badge)


# ------------------------------------------------------------------------------------- WF19


def test_wf19_hidden_text_is_left_out_and_counted_and_a_sideways_page_is_read_upright(
    site: harness.ReplaySite, made: Callable[[str, bytes], str]
) -> None:
    files = load()
    hidden = made("invisible-text.pdf", files["invisible-text.pdf"].make())
    text = student_text(site, hidden)
    marks = ("WHITE", "TINY:", "MICRO", "MODE3", "OFFPAGE2", "BLACKONBLACK", "NEARWHITE")
    assert [mark for mark in marks if mark in text] == []
    assert "LIGHTGREY" in text  # grey 0.75 can be read by a sighted person: it stays
    assert "Name the two layers of the membrane" in text
    assert [n for n in site.result(hidden).notices if n.code == "hidden_text_ignored"]
    links = site.page(f"/jobs/{hidden}").xpath(
        '//main//ul[contains(@class, "check-list")]//a[starts-with(@href, "/jobs/")]/@href'
    )
    assert links
    for link in links:
        assert site.browser.get(link, follow_redirects=True).status_code == 200, link

    turned = made("sideways-no-rotate.pdf", files["sideways-no-rotate.pdf"].make())
    picture = site.browser.get(f"/jobs/{turned}/pages/2.png")
    assert picture.status_code == 200
    shown = pymupdf.Pixmap(io.BytesIO(picture.content))
    assert shown.width > shown.height  # the landscape page, upright
    text = student_text(site, turned)
    assert "Its first sentence must be read upright" in text
    assert text.index("Week 1") < text.index("Week 8")

    # F-541: what a reviewer wrote on the page is read as notes, the pen and the pushpins are
    # no figures, and the attached files are named, not converted.
    marked = made("annotations.pdf", files["annotations.pdf"].make())
    text = student_text(site, marked)
    for note in (
        "Note on the page: Safety first: this step is graded.",
        "Note on the page: Correction: use 30 mL, not 25 mL. The 25 is a typo.",
        "Note on the page: Not 40: use 37 degrees.",
    ):
        assert note in text
    assert "Answer key: the colour turns blue" not in text  # a hidden annotation stays hidden
    found = {n.code: n for n in site.result(marked).notices}
    assert found["annotations_read"].count == 3 and "Moved to May 8" in text
    assert sorted(found["attachments_not_converted"].names) == [
        "buffer_datasheet.txt",
        "rubric.txt",
    ]
    page = harness.words(site.page(f"/jobs/{marked}").xpath("//main")[0])
    assert "buffer_datasheet.txt" in page and "rubric.txt" in page


def test_wf19_a_file_cut_off_is_not_ready_until_the_owner_answers_and_the_student_is_told_always(
    site: harness.ReplaySite, made: Callable[[str, bytes], str]
) -> None:
    """F-505. A six-page PDF cut in the middle opens only after repair, with pages 4 to 6 empty:
    the page asks before it says "Ready to post", and the student file says the pages are not
    there before and after the owner continues. A file with a damaged cross-reference table
    that repairs with content on every page asks nothing."""
    files = load()
    cut = made("truncated.pdf", files["truncated.pdf"].make())
    assert state(site, cut) != READY and "cut off" in state(site, cut)
    page = harness.words(site.page(f"/jobs/{cut}").xpath("//main")[0])
    assert "pages 4 to 6 are empty" in page and "continue with pages 1 to 3" in page
    assert "Every part the tool found was converted" not in page
    told = "pages 4 to 6 of the original pdf are empty"
    assert told in student_text(site, cut).lower()

    sent = site.browser.post(
        f"/jobs/{cut}/cut-off", data={"csrf_token": harness.token(site.browser, f"/jobs/{cut}")}
    )
    assert sent.status_code == 303
    assert state(site, cut) == "Ready"  # may be shared, but never "Ready to post"
    page = harness.words(site.page(f"/jobs/{cut}").xpath("//main")[0])
    assert "Pages 4 to 6 of the PDF are empty" in page and "Every part the tool found" not in page
    assert told in student_text(site, cut).lower()

    repaired = made("xref-damaged.pdf", files["xref-damaged.pdf"].make())
    assert state(site, repaired) == READY
    assert not [n for n in site.result(repaired).notices if n.review == "required"]
    assert "cut off" not in student_text(site, repaired)


# ------------------------------------------------------------------------------------- WF22

SENTENCES = (
    "The course covers the structure of cells, how energy moves through them and how they "
    "divide, and every week has a reading, a lab and a short quiz that count towards the grade. "
)


def syllabus() -> bytes:
    """A syllabus with a date struck and replaced, a bold warning, an answer key in red, a
    footnote, a typed email and web address, and a passage in Spanish."""
    css = (
        "body{font-family:sans-serif;font-size:11pt;line-height:1.35} "
        "h1{font-size:18pt} h2{font-size:14pt} p{margin:0 0 8pt 0}"
    )
    red = "<span style='color:#c00000'>{}</span>"
    spanish = (
        "Los estudiantes deben leer el capítulo uno antes de la primera clase y responder las "
        "preguntas al final de la sección. "
    )
    html = (
        "<h1>Biology 101 Syllabus</h1><h2>Dates</h2>"
        f"<p>{SENTENCES * 2}The midterm is on <del>March 3</del> March 10 in room 210, and the "
        f"final project is due <s>April 28</s> May 5.</p><p>{SENTENCES * 2}</p>"
        "<p><b>WARNING: Late work is not accepted after the final date.</b></p>"
        f"<h2>Contact</h2><p>{SENTENCES * 2}Email the instructor at jane.doe@university.edu or "
        "visit https://courses.example.edu/bio101 for notes.</p>"
        f"<h2>Answer key</h2><p>{SENTENCES}Quiz 1: {red.format('B')}, {red.format('D')}, "
        f"{red.format('A')}.</p><h2>Lectura</h2><p>{spanish * 2}</p>"
        f"<p>{SENTENCES * 2}Grading is based on labs and exams.<sup>1</sup></p>"
    )
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_htmlbox(pymupdf.Rect(72, 60, 540, 690), html, css=css)
    page.draw_line((72, 710), (200, 710))
    page.insert_text((72, 724), "1 Lab grades are averaged before the exams are added.", fontsize=9)
    return doc.tobytes()


def test_wf22_a_footnote_and_typed_addresses_are_links_and_the_spanish_is_told(
    site: harness.ReplaySite, made: Callable[[str, bytes], str]
) -> None:
    student = site.student_file(made("syllabus.pdf", syllabus()))
    (mark,) = student.xpath('//a[@role="doc-noteref"]')
    (note,) = student.xpath(f'//*[@id="{mark.get("href").lstrip("#")}"]')
    assert "averaged before the exams" in harness.words(note)
    assert student.xpath('//a[starts-with(@href, "mailto:jane.doe@university.edu")]')
    assert student.xpath('//a[starts-with(@href, "https://courses.example.edu/")]')
    (spanish,) = student.xpath('//*[contains(text(), "Los estudiantes deben leer")]')
    assert spanish.xpath("ancestor-or-self::*[@lang][1]/@lang")[0].startswith("es")
    (english,) = student.xpath('//*[contains(text(), "Grading is based on labs")]')
    assert english.xpath("ancestor-or-self::*[@lang][1]/@lang")[0].startswith("en")


# ------------------------------------------------------------------------------------- WF23

FILL = (
    "The wave carries energy from one place to another without carrying matter along with it, "
    "and the speed of the wave depends on the medium it moves through. "
)
CONTENTS = (
    ("Chapter 1  Waves", 3),
    ("Chapter 2  Sound", 5),
    ("Chapter 3  Light", 7),
    ("Appendix A  Units", 10),
)


def book() -> bytes:
    """Thirteen pages: a contents page, three chapters and an appendix, references by number
    to a chapter and a section, and a paragraph that runs over from page 4 to page 5."""
    css = (
        "body{font-family:serif;font-size:11pt;line-height:1.4} h1{font-size:20pt} "
        "h2{font-size:15pt} p{margin:0 0 8pt 0;text-align:justify}"
    )
    doc = pymupdf.open()

    def page(html: str) -> None:
        doc.new_page(width=612, height=792).insert_htmlbox(
            pymupdf.Rect(72, 60, 540, 720), html, css=css
        )

    def para(n: int) -> str:
        return f"<p>{FILL * 3}Paragraph marker {n}.</p>"

    first = doc.new_page(width=612, height=792)
    first.insert_text((72, 90), "Introductory Physics", fontsize=22, fontname="hebo")
    first.insert_text((72, 140), "Contents", fontsize=15, fontname="hebo")
    for row, (entry, number) in enumerate(CONTENTS):
        dots = "." * (62 - len(entry))
        text = f"{entry} {dots} {number}"
        first.insert_text((72, 170 + 20 * row), text, fontsize=11, fontname="helv")
    page("<h2>Preface</h2>" + para(0) + para(1))
    page(
        "<h1>Chapter 1  Waves</h1>"
        + para(2)
        + "<h2>1.1  Speed</h2>"
        + para(3)
        + f"<p>As Section 1.2 shows and as Chapter 2 explains further, {FILL * 2}</p>"
    )
    page(
        "<h2>1.2  Frequency</h2>"
        + para(4)
        + para(5)
        + f"<p>{FILL * 5} This paragraph crosses to the next page and then continues there</p>"
    )
    page("<p>for several more lines until it ends. Paragraph marker 6.</p>" + para(7))
    page("<h1>Chapter 2  Sound</h1>" + para(8) + "<h2>2.1  Pitch</h2>" + para(9))
    page("<h2>2.2  Loudness</h2>" + para(10) + para(11))
    page("<h1>Chapter 3  Light</h1>" + para(12) + "<h2>3.1  Colour</h2>" + para(13))
    page("<h2>3.2  Rays</h2>" + para(14) + f"<p>See Section 3.1 and Appendix A. {FILL}</p>")
    page(para(15) + para(16))
    page("<h1>Appendix A  Units</h1>" + para(17) + "<h2>A.1  Metric</h2>" + para(18))
    page(para(19) + para(20))
    page("<h2>A.2  Constants</h2>" + para(21))
    return doc.tobytes()


@pytest.fixture(scope="module")
def combined() -> Iterator[lxml.html.HtmlElement]:
    """The book converted in three parts (a job takes five pages here) and put together."""
    # A site of its own: the shared one takes sixty pages a job.
    with tempfile.TemporaryDirectory() as folder:
        small = harness.ReplaySite(Path(folder), max_pages_per_job=5, max_source_pages=40)
        try:
            jobs = small.convert_parts("book.pdf", book(), ["1-5", "6-10", "11-13"])
            reply = harness.fetch(small.browser, f"/jobs/{jobs[1]}/combine")
            assert reply.status_code == 200
            yield lxml.html.fromstring(reply.text)
        finally:
            small.close()


def test_wf23_no_link_of_the_combined_file_goes_nowhere_and_no_paragraph_is_cut(
    combined: lxml.html.HtmlElement,
) -> None:
    ids = set(combined.xpath("//@id"))
    assert [h for h in combined.xpath('//a/@href[starts-with(., "#")]') if h[1:] not in ids] == []
    # F-515, F-516: a reference ("Chapter 2") and a contents entry whose target is printed in
    # another part are links, and each lands on the heading it names.
    for shown in ("Chapter 2", "Chapter 2 Sound", "Chapter 3 Light", "Appendix A Units"):
        hrefs = combined.xpath(f'//a[normalize-space(.)="{shown}"]/@href[starts-with(., "#")]')
        assert hrefs, shown
        for href in hrefs:
            (target,) = combined.xpath(f'//*[@id="{href[1:]}"]')
            assert harness.words(target).startswith(shown), shown
    (crossing,) = combined.xpath('//p[contains(., "crosses to the next page")]')
    assert "Paragraph marker 6." in harness.words(crossing)
    text = harness.words(combined.xpath("//main")[0])
    for n in range(22):
        assert f"Paragraph marker {n}." in text, n


# ------------------------------------------------------------------------------------- WF24


def exam() -> bytes:
    """A fillable cover sheet, then an exam: options two and four across, blanks, an answer
    box, a structure to draw, and a line set in a symbol font."""
    sheet = pymupdf.open(stream=load()["form-acroform.pdf"].make())
    paper = pymupdf.open()
    first = paper.new_page(width=612, height=792)

    def line(x: float, y: float, words: str, size: float = 11, font: str = "helv") -> None:
        first.insert_text((x, y), words, fontsize=size, fontname=font)

    line(72, 70, "Chemistry 110 Midterm Exam", 18, "hebo")
    line(72, 100, "Answer every question. Show your work in the boxes provided.", 10)
    line(72, 135, "1. Which gas is produced when zinc reacts with hydrochloric acid?")
    line(90, 155, "(A) Oxygen")
    line(330, 155, "(B) Hydrogen")
    line(90, 173, "(C) Nitrogen")
    line(330, 173, "(D) Helium")
    line(72, 210, "2. The SI unit of pressure is the:")
    for x, option in (
        (90, "(A) pascal"),
        (200, "(B) bar"),
        (300, "(C) torr"),
        (400, "(D) atmosphere"),
    ):
        line(x, 230, option)
    line(72, 270, "3. A solution with pH 3 is ________ and turns litmus paper ________.")
    line(72, 305, "4. State the ideal gas law and define each symbol.")
    first.draw_rect(pymupdf.Rect(72, 315, 540, 385))
    line(72, 420, "5. Draw the structure of benzene in the space below.")
    ring = [
        (300 + 40 * math.cos(i * math.pi / 3), 490 + 40 * math.sin(i * math.pi / 3))
        for i in range(6)
    ]
    for i in range(6):
        first.draw_line(ring[i], ring[(i + 1) % 6], width=1.5)
    first.draw_rect(pymupdf.Rect(72, 440, 540, 550))
    second = paper.new_page(width=612, height=792)
    second.insert_text((72, 70), "6. Greek letters:", fontsize=11, fontname="helv")
    second.insert_text((190, 70), "abgdw", fontsize=12, fontname="symb")
    sheet.insert_pdf(paper)
    return sheet.tobytes()


def test_wf24_an_exam_keeps_its_options_in_order_names_its_blanks_and_asks_about_the_drawing(
    site: harness.ReplaySite, made: Callable[[str, bytes], str]
) -> None:
    job_id = made("exam.pdf", exam())
    text = student_text(site, job_id)
    for options in (
        ("(A) Oxygen", "(B) Hydrogen", "(C) Nitrogen", "(D) Helium"),
        ("(A) pascal", "(B) bar", "(C) torr", "(D) atmosphere"),
    ):
        at = [text.index(option) for option in options]
        assert at == sorted(at)
    assert "Checkbox, not checked" in text  # the cover sheet's check boxes
    assert "blank" in text  # the blanks of question 3 are told as blanks
    assert "Greek letters" in text
    assert site.job(job_id).open_decisions > 0  # the drawing of benzene is a decision
    assert state(site, job_id) != READY
