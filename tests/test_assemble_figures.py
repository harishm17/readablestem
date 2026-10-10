"""How assemble writes figures with a long description or a chart data table (a folded
`<details>` after the figure), and the re-encoded figure images of the student file
(UX review MF23)."""

from __future__ import annotations

import base64
import random
import re

import pymupdf
import pytest

from a11y_stem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    Kind,
    Status,
)
from a11y_stem.pipeline import figure_details, figure_images
from a11y_stem.pipeline.assemble import FIGURE_NOT_DESCRIBED_ALT, render_html
from a11y_stem.pipeline.html_checks import static_checks

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
    assert '<summary class="a11y-stem">Description of Figure 2.3</summary>' in block
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
    assert '<summary class="a11y-stem">Description of Figure 2.3</summary>' in details(html)
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
    assert '<summary class="a11y-stem">Data for Figure 2.3</summary>' in block
    assert ">Data shown in Figure 2.3</caption>" in block


def test_without_a_figure_number_the_summary_names_the_figure_by_its_alt_text():
    # Two unnumbered figures must not both be "Description of the figure" in a screen
    # reader's list of controls (WCAG 2.4.6): the start of the alt text tells them apart.
    fix = figure_fix(long_description=LONG)
    assert fix.figure is not None
    words = fix.figure.alt.split()
    html = render(fix, figure_element(caption=None))
    start = html.index('<summary class="a11y-stem">Description of the figure \u201c')
    summary = html[start : html.index("</summary>", start)]
    assert " ".join(words[:3]) in summary
    from a11y_stem.pipeline import figure_details

    short = fix.figure.model_copy(update={"alt": "A cart."})
    assert figure_details.figure_name(short, None) == "the figure \u201cA cart\u201d"
    assert figure_details.figure_name(short.model_copy(update={"alt": ""}), None) == "the figure"
    assert figure_details.figure_name(short, "Figure 2.3 A cart.") == "Figure 2.3"


def test_model_text_in_the_description_is_escaped():
    html = render(figure_fix(long_description="A <b>bold</b> claim & more text here, enough."))
    assert "<p>A &lt;b&gt;bold&lt;/b&gt; claim &amp; more text here, enough.</p>" in html


@pytest.mark.parametrize(
    "fix",
    [
        figure_fix(Status.REJECTED, long_description=LONG, data_table=grid()),
        figure_fix(Status.ABSTAINED, long_description=LONG),
        Fix(
            element_id="p1-e5",
            kind=Kind.FIGURE,
            status=Status.ACCEPTED,
            figure=FigureFix(figure_type="photo", alt="", decorative=True, long_description=LONG),
        ),
    ],
    ids=["rejected", "abstained", "decorative"],
)
def test_no_description_without_a_usable_proposal(fix):
    html = render(fix)
    assert "<details" not in html and "figure-description" not in html


def test_figures_without_details_are_written_as_before():
    html = render(figure_fix())
    assert "<details" not in html and ".figure-description" not in html
    assert "th, td {" not in html


def test_an_id_already_taken_drops_the_table_not_the_description():
    other = Element(
        id="p1-e5-data-caption",
        kind=Kind.PARAGRAPH,
        page=0,
        bbox=(0, 0, 1, 1),
        order=1,
        text="A paragraph whose id is taken.",
    )
    html = render_html(
        title="T",
        lang="en",
        elements=[other, figure_element()],
        fixes=[figure_fix(long_description=LONG, data_table=grid(), data_values_from_text=True)],
        crops={"p1-e5": png()},
    )
    assert "<table" not in html and "Description of Figure 2.3" in html
    assert all(c.passed for c in static_checks(html))


def test_a_stored_result_renders_the_same_file():
    fix = figure_fix(long_description=LONG, data_table=grid(), data_values_from_text=False)
    element = figure_element()
    result = DocumentResult(
        pipeline_version="0.4.0",
        elements=[element],
        fixes=[fix],
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
    )
    stored = DocumentResult.model_validate_json(result.model_dump_json())
    crops = {"p1-e5": png(300, 200, noise=True)}
    assert render(stored.fixes[0], stored.elements[0], crops) == render(fix, element, crops)


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


def test_a_chart_that_is_a_photograph_becomes_a_jpeg():
    # A raster chart scanned or saved as JPEG: as PNG it is several times larger.
    crop = png(300, 200, noise=True)
    mime, _ = figure_images.student_image(crop, "chart")
    assert mime == "image/jpeg"


def test_large_images_are_scaled_down_never_up():
    mime, data = figure_images.student_image(png(2000, 1000, noise=True), "photo")
    assert mime == "image/jpeg" and size_of(data) == (1600, 800)
    mime, data = figure_images.student_image(png(1000, 2400), "diagram")
    assert size_of(data) == (667, 1600)
    small = png(120, 80, noise=True)
    assert size_of(figure_images.student_image(small, "photo")[1]) == (120, 80)


def test_transparency_and_unreadable_images_are_kept_as_they_are():
    with_alpha = png(60, 40, noise=True, alpha=True)
    assert figure_images.student_image(with_alpha, "photo") == ("image/png", with_alpha)
    broken = b"\x89PNG\r\n\x1a\nnot really a png"
    assert figure_images.student_image(broken, "photo") == ("image/png", broken)


def test_the_same_crop_always_gives_the_same_bytes():
    crop = png(300, 200, noise=True)
    assert figure_images.student_image(crop, "photo") == figure_images.student_image(crop, "photo")


def test_the_student_file_embeds_the_reencoded_image():
    crop = png(300, 200, noise=True)
    fix = Fix(
        element_id="p1-e5",
        kind=Kind.FIGURE,
        status=Status.ACCEPTED,
        figure=FigureFix(figure_type="photo", alt="A jet car on a desert track."),
    )
    html = render(fix, crops={"p1-e5": crop})
    ((mime, data),) = embedded(html)
    assert mime == "image/jpeg" and data == figure_images.student_image(crop, "photo")[1]
    assert 'alt="A jet car on a desert track." width="304" height="197"' in html
    assert all(c.passed for c in static_checks(html))


def test_flagged_figures_and_scanned_pages_are_reencoded_too():
    crop = png(300, 200, noise=True)
    fix = Fix(element_id="p1-e5", kind=Kind.FIGURE, status=Status.FAILED, reason="scanned_page")
    html = render(fix, crops={"p1-e5": crop})
    ((mime, _),) = embedded(html)
    assert mime == "image/jpeg"
    assert f'alt="{FIGURE_NOT_DESCRIBED_ALT}"' in html


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
    from a11y_stem.pipeline import html_checks

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
