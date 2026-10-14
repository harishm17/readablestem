"""The student file in a reader's dark-colour preference (T3-14).

Static: every colour pair the stylesheet declares reaches its contrast in both schemes
(text 4.5:1, borders 3:1), colour classes get a dark-page rule when they need one, and the
static check reads both. Browser: Chromium asks for each scheme, axe finds no colour-contrast
problem, and MathML, borders and the page take the scheme's colours.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import (
    ColourRun,
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.pipeline import assemble, assemble_tables, contrast, html_checks, inline_text
from readablestem.pipeline.assemble import STYLE, render_html
from readablestem.pipeline.html_checks import split_dark_css, static_checks

GOLDEN = Path(__file__).parent / "fixtures" / "assemble" / "golden"
HEX = re.compile(r"#([0-9a-f]{6})\b")


def rgb(digits: str) -> tuple[int, int, int]:
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def declared(css: str, selector: str, prop: str) -> tuple[int, int, int]:
    """The colour a rule `selector { ... prop: #rrggbb ... }` gives."""
    block = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", css)
    assert block, selector
    value = re.search(re.escape(prop) + r":\s*(?:[^;#]*\s)?#([0-9a-fA-F]{6})", block.group(1))
    assert value, (selector, prop)
    return rgb(value.group(1))


def ratio(a, b) -> float:
    return contrast.contrast_ratio(a, b)


# --------------------------------------------------------------------------- static


def test_the_stylesheet_asks_for_both_schemes_and_prints_light():
    assert ":root { color-scheme: light dark; }" in STYLE
    plain, dark = split_dark_css(STYLE)
    assert "background: #121212" in dark and "background: #121212" not in plain
    assert "@media screen and (prefers-color-scheme: dark)" in STYLE  # not when printing


def test_the_page_text_reaches_contrast_in_both_schemes():
    plain, dark = split_dark_css(STYLE)
    light_bg, light_fg = declared(plain, "body", "background"), declared(plain, "body", "color")
    dark_bg, dark_fg = declared(dark, "body", "background"), declared(dark, "body", "color")
    assert light_bg == contrast.OUTPUT_BACKGROUND and dark_bg == contrast.DARK_BACKGROUND
    assert ratio(light_fg, light_bg) >= 7 and ratio(dark_fg, dark_bg) >= 7


def test_borders_and_focus_rings_reach_3_to_1_in_both_schemes():
    plain, dark = split_dark_css(STYLE)
    for css, bg in ((plain, contrast.OUTPUT_BACKGROUND), (dark, contrast.DARK_BACKGROUND)):
        boxes = (
            declared(css, ".flagged, .notice, .flagged-inline", "border-color")
            if css is dark
            else declared(css, ".flagged", "border")
        )
        assert ratio(boxes, bg) >= 3
        assert (
            ratio(
                declared(css, ":focus-visible", "outline-color" if css is dark else "outline"), bg
            )
            >= 3
        )
    # The note's rule above the about section, and a figure's edge in the dark page.
    assert ratio(declared(dark, ".about", "border-top-color"), contrast.DARK_BACKGROUND) >= 3
    assert ratio(declared(dark, "img", "outline"), contrast.DARK_BACKGROUND) >= 3


def test_code_and_table_rules_have_a_dark_version_that_reaches_contrast():
    _, code_dark = split_dark_css(assemble.CODE_CSS)
    body_dark = (236, 236, 236)
    assert ratio(body_dark, declared(code_dark, "pre", "background")) >= 7
    assert (
        ratio(declared(code_dark, "pre", "border-color"), declared(code_dark, "pre", "background"))
        >= 3
    )
    table_light, table_dark = split_dark_css(assemble_tables.TABLE_CSS)
    assert ratio(declared(table_light, "th, td", "border"), contrast.OUTPUT_BACKGROUND) >= 3
    assert ratio(declared(table_dark, "th, td", "border-color"), contrast.DARK_BACKGROUND) >= 3


@pytest.mark.parametrize(
    "printed",
    [(255, 0, 0), (0, 0, 255), (255, 235, 59), (210, 210, 210), (0, 100, 0), (128, 0, 128)],
)
def test_every_printed_colour_is_readable_on_both_pages(printed):
    css = inline_text.colour_css(printed)
    plain, dark = split_dark_css(css)
    light_colour = declared(plain, "." + inline_text.colour_class(printed), "color")
    assert ratio(light_colour, contrast.OUTPUT_BACKGROUND) >= 4.5
    dark_colour = (
        declared(dark, "." + inline_text.colour_class(printed), "color") if dark else light_colour
    )
    assert ratio(dark_colour, contrast.DARK_BACKGROUND) >= 4.5


def test_no_colour_reaches_4_5_to_1_on_both_pages_so_each_gets_a_dark_rule():
    """Contrast 4.5:1 on white needs a luminance of at most 0.18, on #121212 at least 0.20."""
    for printed in [(0, 0, 0), (255, 255, 255), (118, 118, 118), (255, 0, 0), (60, 60, 60)]:
        shown = contrast.with_contrast(printed)
        assert (
            ratio(shown, contrast.DARK_BACKGROUND) < 4.5
            or ratio(shown, contrast.OUTPUT_BACKGROUND) < 4.5
        )
        assert "prefers-color-scheme: dark" in inline_text.colour_css(printed)


def test_with_contrast_lightens_on_a_dark_page_and_keeps_the_hue():
    light = contrast.with_contrast((0, 0, 200), contrast.DARK_BACKGROUND)
    assert ratio(light, contrast.DARK_BACKGROUND) >= 4.5
    assert light[2] > light[0] and light[2] > light[1]  # still blue
    assert contrast.with_contrast((200, 200, 200), contrast.DARK_BACKGROUND) == (200, 200, 200)
    # The light page behaves as before: darker, not lighter.
    assert sum(contrast.with_contrast((255, 235, 59))) < sum((255, 235, 59))


def paragraph_with_colours() -> str:
    text = "red words then light grey words then blue words."
    runs = [
        ColourRun(start=0, end=9, rgb=(255, 0, 0)),
        ColourRun(start=15, end=30, rgb=(210, 210, 210)),
        ColourRun(start=36, end=46, rgb=(0, 0, 255)),
    ]
    element = Element(
        id="p1-e0",
        kind=Kind.PARAGRAPH,
        page=0,
        bbox=(0, 0, 10, 10),
        order=0,
        text=text,
        colour_runs=runs,
    )
    return render_html(title="T", lang="en", elements=[element], fixes=[], crops={})


def test_a_document_with_colours_passes_the_static_checks_in_both_schemes():
    html = paragraph_with_colours()
    assert "@media screen and (prefers-color-scheme: dark) { .tc-ff0000" in html
    assert [c.code for c in static_checks(html) if not c.passed] == []


def test_a_colour_rule_too_dark_for_the_dark_page_fails_the_check():
    html = paragraph_with_colours()
    broken = re.sub(
        r"(prefers-color-scheme: dark\) \{ \.tc-ff0000 \{ color: )#[0-9a-f]{6}",
        r"\1#330000",
        html,
    )
    assert broken != html
    assert "low_contrast" in [c.code for c in static_checks(broken) if not c.passed]
    # A light rule that is only readable on white fails when it has no dark rule.
    dark_less = "<style>.tc-ffff00 { color: #ffff00; }</style>"
    assert "low_contrast" in [
        c.code
        for c in static_checks(html.replace("</head>", dark_less + "</head>"))
        if not c.passed
    ]


# --------------------------------------------------------------------------- browser


def png(width: int = 60, height: int = 20) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (255, 255, 255))
    return pixmap.tobytes("png")


def busy_document() -> str:
    """Colours, code, a flagged equation and a kept-as-image block, a converted equation,
    and a table: every colour the stylesheet can set."""
    text = "red words then light grey words then blue words."
    elements = [
        Element(
            id="p1-e0",
            kind=Kind.PARAGRAPH,
            page=0,
            bbox=(0, 0, 10, 10),
            order=0,
            text=text,
            colour_runs=[
                ColourRun(start=0, end=9, rgb=(255, 0, 0)),
                ColourRun(start=15, end=30, rgb=(210, 210, 210)),
                ColourRun(start=36, end=46, rgb=(0, 0, 255)),
            ],
        ),
        Element(
            id="p1-e1",
            kind=Kind.PARAGRAPH,
            page=0,
            bbox=(0, 0, 10, 10),
            order=1,
            text="x = 1\nprint(x)",
            preformatted=True,
        ),
        Element(
            id="p1-e2",
            kind=Kind.EQUATION,
            page=0,
            bbox=(0, 0, 30, 12),
            order=2,
            display="block",
            text="x",
        ),
        Element(
            id="p1-e3",
            kind=Kind.EQUATION,
            page=0,
            bbox=(0, 0, 30, 12),
            order=3,
            display="block",
            text="y",
        ),
    ]
    fixes = [
        Fix(
            element_id="p1-e2",
            kind=Kind.EQUATION,
            status=Status.ACCEPTED,
            equation=EquationFix(
                latex="x=\\frac{1}{2}",
                mathml='<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo>'
                "<mfrac><mn>1</mn><mn>2</mn></mfrac></math>",
                speech="x equals one half",
            ),
        ),
        Fix(element_id="p1-e3", kind=Kind.EQUATION, status=Status.ABSTAINED, reason="abstained"),
    ]
    html = render_html(title="T", lang="en", elements=elements, fixes=fixes, crops={"p1-e3": png()})
    table = (
        '<table><caption>Data</caption><thead><tr><th scope="col">a</th>'
        '<th scope="col">b</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>'
    )
    return html.replace("</style>", assemble_tables.TABLE_CSS + "</style>").replace(
        "</main>", table + "\n</main>"
    )


def documents() -> dict[str, str]:
    found = {path.stem: path.read_text(encoding="utf-8") for path in sorted(GOLDEN.glob("*.html"))}
    found["busy"] = busy_document()
    return found


def run_axe(html: str, scheme: str):
    checks = html_checks.axe_checks(html, color_scheme=scheme)  # type: ignore[arg-type]
    if len(checks) == 1 and checks[0].code == "skipped":
        pytest.skip("axe-core needs Playwright and its Chromium")
    return checks


@pytest.mark.browser
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", ["busy", "sample", "demo", "flags_off_kinematics_slides"])
def test_axe_finds_no_problem_in_either_scheme(name, scheme):
    checks = run_axe(documents()[name], scheme)
    assert [(c.name, c.severity) for c in checks if not c.passed] == []


@pytest.mark.browser
def test_chromium_shows_the_dark_page_colours_including_maths_and_borders():
    playwright = pytest.importorskip("playwright.sync_api")
    html = documents()["busy"]
    probe = """() => {
      const colour = (el) => getComputedStyle(el).color;
      const box = document.querySelector('.flagged');
      return {
        background: getComputedStyle(document.body).backgroundColor,
        text: colour(document.body),
        math: colour(document.querySelector('math')),
        mathBackground: getComputedStyle(document.querySelector('math')).backgroundColor,
        border: getComputedStyle(box).borderTopColor,
        red: colour(document.querySelector('.tc-ff0000')),
        pre: getComputedStyle(document.querySelector('pre')).backgroundColor,
        cell: getComputedStyle(document.querySelector('td')).borderTopColor,
        scheme: getComputedStyle(document.documentElement).colorScheme,
      };
    }"""
    seen = {}
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error:
            pytest.skip("Chromium is not installed")
        try:
            for scheme in ("light", "dark"):
                page = browser.new_context(offline=True, color_scheme=scheme).new_page()
                page.set_content(html, wait_until="load")
                seen[scheme] = page.evaluate(probe)
        finally:
            browser.close()
    light, dark = seen["light"], seen["dark"]
    assert light["background"] == "rgb(255, 255, 255)" and dark["background"] == "rgb(18, 18, 18)"
    assert light["math"] == light["text"] == "rgb(27, 27, 27)"
    assert dark["math"] == dark["text"] == "rgb(236, 236, 236)"
    assert dark["mathBackground"] in ("rgba(0, 0, 0, 0)", "rgb(18, 18, 18)")
    assert light["border"] == "rgb(138, 60, 0)" and dark["border"] == "rgb(255, 160, 102)"
    assert light["pre"] == "rgb(243, 243, 243)" and dark["pre"] == "rgb(31, 31, 31)"
    assert dark["cell"] == "rgb(166, 166, 166)"
    assert light["red"] != dark["red"]
    assert light["scheme"] == dark["scheme"] == "light dark"
