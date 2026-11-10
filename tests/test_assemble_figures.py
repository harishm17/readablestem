"""How assemble writes figures with a long description or a chart data table (a folded
`<details>` after the figure), and the re-encoded figure images of the student file
(UX review MF23)."""

from __future__ import annotations

import base64
import random
import re

import pymupdf
import pytest

from readablestem.core.types import (
    Element,
    EquationFix,
    FigureFix,
    Fix,
    Kind,
    Status,
)
from readablestem.pipeline import figure_details, figure_images
from readablestem.pipeline.assemble import FIGURE_NOT_DESCRIBED_ALT, render_html
from readablestem.pipeline.html_checks import static_checks

LONG = (
    "Line graph of the position of a car against time.\n"
    "Position rises in a straight line from 400 m at 0 s to 2000 m at 6.40 s."
)
ALT = "Line graph of position against time: a straight line rising."


def png(width: int = 40, height: int = 20, *, noise: bool = False, alpha: bool = False) -> bytes:
    n = 4 if alpha else 3
    if noise:
        samples = random.Random(width * height).randbytes(width * height * n)
    else:
        samples = bytes([90] * (width * height * n))
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, width, height, samples, alpha)
    return pixmap.tobytes("png")


def grid() -> object:
    return figure_details.data_grid(
        ["Time (s)", "Position (m)"], [["0", "400"], ["0.50", "525"], ["6.40", "<2000>"]]
    )


def figure_fix(status: Status = Status.ACCEPTED, **kw) -> Fix:
    figure = FigureFix(figure_type="chart", alt=ALT, **kw)
    return Fix(element_id="p1-e5", kind=Kind.FIGURE, status=status, confidence=0.9, figure=figure)


def figure_element(caption: str | None = "Figure 2.3 Position of a car.") -> Element:
    return Element(
        id="p1-e5", kind=Kind.FIGURE, page=0, bbox=(72, 72, 300, 220), order=5, caption=caption
    )


def render(fix: Fix, element: Element | None = None, crops=None) -> str:
    return render_html(
        title="T",
        lang="en",
        elements=[element or figure_element()],
        fixes=[fix],
        crops={"p1-e5": png()} if crops is None else crops,
    )


def details(html: str) -> str:
    match = re.search(r"<details.*?</details>", html, re.S)
    assert match, html
    return match.group(0)


# ---------------------------------------------------------------------- long descriptions


def test_a_long_description_follows_the_figure_folded():
    html = render(figure_fix(long_description=LONG))
    figure_end = html.index("</figure>")
    assert html[figure_end:].startswith('</figure><details class="figure-description">')
    block = details(html)
    assert '<summary class="readablestem">Description of Figure 2.3</summary>' in block
    assert "<p>Line graph of the position of a car against time.</p>" in block
    assert "<p>Position rises in a straight line" in block
    assert "<table" not in block
    assert ".figure-description" in html  # its CSS, only in documents that use it
    assert all(c.passed for c in static_checks(html))


def test_a_table_read_from_the_image_is_not_written_until_a_reviewer_adds_it():
    # An optional extra: kept on the fix, offered on the review screen, not in the file.
    offered = figure_fix(long_description=LONG, data_table=grid(), data_values_from_text=False)
    html = render(offered)
    assert "<table" not in html and "th, td {" not in html
    assert '<summary class="readablestem">Description of Figure 2.3</summary>' in details(html)
    only_table = figure_fix(data_table=grid(), data_values_from_text=False)
    assert "<details" not in render(only_table) and "figure-description" not in render(only_table)


def test_values_read_from_the_image_are_captioned_as_approximate():
    html = render(
        figure_fix(
            long_description=LONG,
            data_table=grid(),
            data_values_from_text=False,
            data_table_added=True,  # the reviewer chose "Add the data table"
        )
    )
    block = details(html)
    assert (
        '<div class="table-scroll" role="group" aria-labelledby="p1-e5-data-caption" '
        'tabindex="0"><table><caption id="p1-e5-data-caption">Data read from Figure 2.3; '
        "the values are approximate</caption>"
    ) in block
    assert '<th scope="col">Time (s)</th><th scope="col">Position (m)</th>' in block
    assert '<th scope="row">0.50</th><td>525</td>' in block
    assert "&lt;2000&gt;" in block and "<2000>" not in block  # cells are escaped
    assert "th, td {" in html  # the table CSS came with it
    assert all(c.passed for c in static_checks(html))


def test_values_printed_in_the_pdf_are_not_called_approximate():
    html = render(figure_fix(data_table=grid(), data_values_from_text=True))
    block = details(html)
    assert '<summary class="readablestem">Data for Figure 2.3</summary>' in block
    assert ">Data shown in Figure 2.3</caption>" in block


def test_without_a_figure_number_the_summary_names_the_figure_by_its_alt_text():
    # Two unnumbered figures must not both be "Description of the figure" in a screen
    # reader's list of controls (WCAG 2.4.6): the start of the alt text tells them apart.
    fix = figure_fix(long_description=LONG)
    assert fix.figure is not None
    words = fix.figure.alt.split()
    html = render(fix, figure_element(caption=None))
    start = html.index('<summary class="readablestem">Description of the figure \u201c')
    summary = html[start : html.index("</summary>", start)]
    assert " ".join(words[:3]) in summary
    from readablestem.pipeline import figure_details

    short = fix.figure.model_copy(update={"alt": "A cart."})
    assert figure_details.figure_name(short, None) == "the figure \u201cA cart\u201d"
    assert figure_details.figure_name(short.model_copy(update={"alt": ""}), None) == "the figure"
    assert figure_details.figure_name(short, "Figure 2.3 A cart.") == "Figure 2.3"


def test_model_text_in_the_description_is_escaped():
    html = render(figure_fix(long_description="A <b>bold</b> claim & more text here, enough."))
    assert "<p>A &lt;b&gt;bold&lt;/b&gt; claim &amp; more text here, enough.</p>" in html


# ---------------------------------------------------------------------- MF23 images


def embedded(html: str) -> list[tuple[str, bytes]]:
    return [
        (mime, base64.b64decode(data))
        for mime, data in re.findall(r'src="data:(image/\w+);base64,([^"]+)"', html)
    ]


def size_of(data: bytes) -> tuple[int, int]:
    pixmap = pymupdf.Pixmap(data)
    return pixmap.width, pixmap.height


def test_a_photo_becomes_a_jpeg():
    crop = png(300, 200, noise=True)
    mime, data = figure_images.student_image(crop, "photo")
    assert mime == "image/jpeg" and len(data) < len(crop)
    assert size_of(data) == (300, 200)


def test_line_art_keeps_its_png_bytes():
    crop = png(300, 200)
    assert figure_images.student_image(crop, "chart") == ("image/png", crop)
    assert figure_images.student_image(crop, "diagram") == ("image/png", crop)


@pytest.fixture
def fresh_cache():
    figure_images.clear_cache()
    yield
    figure_images.clear_cache()


def test_an_image_not_used_for_a_while_is_dropped(fresh_cache, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(figure_images.time, "monotonic", lambda: now[0])
    crop = png(100, 60, noise=True)
    figure_images.student_image(crop, "photo")
    now[0] += figure_images.CACHE_TTL_S - 1
    figure_images.student_image(crop, "photo")  # used again: the clock starts over
    now[0] += figure_images.CACHE_TTL_S - 1
    figure_images.student_image(png(100, 61, noise=True), "photo")
    assert len(figure_images._cache) == 2
    now[0] += figure_images.CACHE_TTL_S + 1
    figure_images.student_image(png(100, 62, noise=True), "photo")
    assert len(figure_images._cache) == 1  # the two before it expired
    figure_images.clear_cache()
    assert len(figure_images._cache) == 0 and figure_images._cache_bytes == 0


def test_deleting_a_job_forgets_the_images_made_from_its_figures(fresh_cache):
    from datetime import UTC, datetime

    from readablestem.ops.retention import delete_jobs
    from readablestem.web import db

    figure_images.student_image(png(100, 60, noise=True), "photo")
    assert figure_images._cache
    engine = db.make_engine("sqlite://")
    db.create_all(engine)
    with db.session_scope(db.make_sessionmaker(engine)) as session:
        user = db.User(email="x@example.org", is_allowed=True)
        session.add(user)
        session.flush()
        job = db.Job(
            user_id=user.id, filename="f.pdf", retain_until=datetime(2099, 1, 1, tzinfo=UTC)
        )
        session.add(job)
        session.flush()
        delete_jobs(session, [job.id])
    assert not figure_images._cache
    engine.dispose()


def test_transparency_and_unreadable_images_are_kept_as_they_are():
    with_alpha = png(60, 40, noise=True, alpha=True)
    assert figure_images.student_image(with_alpha, "photo") == ("image/png", with_alpha)
    broken = b"\x89PNG\r\n\x1a\nnot really a png"
    assert figure_images.student_image(broken, "photo") == ("image/png", broken)


def test_flagged_figures_and_scanned_pages_are_reencoded_too():
    crop = png(300, 200, noise=True)
    fix = Fix(element_id="p1-e5", kind=Kind.FIGURE, status=Status.FAILED, reason="scanned_page")
    html = render(fix, crops={"p1-e5": crop})
    ((mime, _),) = embedded(html)
    assert mime == "image/jpeg"
    # A page kept as an image says what it is, never "a figure without a description".
    assert 'alt="Page 1: a scanned page, not converted to text."' in html
    assert FIGURE_NOT_DESCRIBED_ALT not in html


def test_equation_images_are_left_alone():
    crop = png(300, 200, noise=True)
    equation = Element(
        id="p1-e2", kind=Kind.EQUATION, page=0, bbox=(72, 72, 300, 120), order=2, display="block"
    )
    fix = Fix(
        element_id="p1-e2",
        kind=Kind.EQUATION,
        status=Status.FAILED,
        reason="model_timeout",
        equation=EquationFix(latex="x", mathml="<math><mi>x</mi></math>", speech="x"),
    )
    html = render_html(
        title="T", lang="en", elements=[equation], fixes=[fix], crops={"p1-e2": crop}
    )
    assert embedded(html) == [("image/png", crop)]


# ---------------------------------------------------------------------- browser


def wide_grid():
    header = ["Time (s)", *(f"Position of cart {n} (m)" for n in range(1, 8))]
    rows = [[str(t), *(str(t * n) for n in range(1, 8))] for t in range(6)]
    return figure_details.data_grid(header, rows)


def described_page() -> str:
    return render(
        figure_fix(
            long_description=LONG,
            data_table=wide_grid(),
            data_values_from_text=False,
            data_table_added=True,
        ),
        crops={"p1-e5": png(600, 300, noise=True)},
    )


def opened(html: str) -> str:
    return html.replace(
        '<details class="figure-description">', '<details class="figure-description" open>'
    )


@pytest.mark.browser
@pytest.mark.parametrize("state", ["folded", "open"])
def test_axe_finds_no_problems_with_a_described_figure(state):
    pytest.importorskip("playwright.sync_api")
    from readablestem.pipeline import html_checks

    html = described_page()
    checks = html_checks.axe_checks(opened(html) if state == "open" else html)
    if checks == [html_checks.AXE_SKIPPED]:
        pytest.skip("Chromium for Playwright is not installed")
    assert checks == [html_checks.Check(name="axe", passed=True)]


@pytest.mark.browser
def test_the_keyboard_opens_the_description_and_reaches_the_table_at_320_px():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error:
            pytest.skip("Playwright's Chromium is not installed")
        try:
            page = browser.new_page(viewport={"width": 320, "height": 640})
            page.set_content(described_page())
            page.keyboard.press("Tab")  # the skip link is the first tab stop
            page.keyboard.press("Tab")
            focused = page.evaluate("() => document.activeElement.tagName")
            page.keyboard.press("Enter")
            is_open = page.evaluate("() => document.querySelector('details').open")
            page.keyboard.press("Tab")
            box = page.evaluate(
                """() => [document.activeElement.className,
                          document.activeElement.scrollWidth > document.activeElement.clientWidth,
                          document.documentElement.scrollWidth,
                          document.documentElement.clientWidth]"""
            )
        finally:
            browser.close()
    assert focused == "SUMMARY" and is_open
    name, table_overflows, page_width, viewport = box
    assert name == "table-scroll" and table_overflows  # the table scrolls in its box...
    assert page_width <= viewport  # ...and the page does not (WCAG 1.4.10)
