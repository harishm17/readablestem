"""The demo site in a real browser (Playwright Chromium): axe-core at 1280 and 320 CSS px,
reflow, keyboard order, and the pages as a browser without JavaScript sees them.

Run with: uv run pytest -m browser tests/test_site_browser.py
The site is built once into a temporary folder and opened from disk (file: URLs), so the
test also shows that it needs no server. The axe-core file is the one the pipeline vendors.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from readablestem.pipeline import html_checks

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

BUILD = Path(__file__).resolve().parents[1] / "site" / "build.py"
# WCAG 2.0, 2.1 and 2.2 levels A and AA, plus axe's own best-practice rules.
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
RUN = """async (tags) => {
  const r = await axe.run(document, {
    runOnly: {type: "tag", values: tags}, resultTypes: ["violations"],
  });
  return r.violations.map(v => ({
    id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target.join(" ")).slice(0, 3),
  }));
}"""
# Pages the site itself writes. The converted pages and the stemcheck report are other tools'
# output that the site links to: they are checked too, in their own test.
OWN_PAGES = [
    "index.html",
    "examples/index.html",
    "before-after/index.html",
    "stemcheck/index.html",
    "method/index.html",
    "attribution/index.html",
    "404.html",
]


def load_build():
    spec = importlib.util.spec_from_file_location("site_build_for_tests", BUILD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site") / "out"
    load_build().build_site(out)
    return out


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - browsers not installed
            pytest.skip(f"Chromium unavailable ({type(exc).__name__}); run playwright install")
        yield chromium
        chromium.close()


def example_pages(site: Path) -> list[str]:
    return sorted(str(p.relative_to(site)) for p in site.glob("examples/*/index.html"))


def converted_pages(site: Path) -> list[str]:
    return sorted(str(p.relative_to(site)) for p in site.glob("examples/*/converted.html"))


def violations(browser, url: str, width: int) -> list[dict]:
    # offline: the page may not load anything, not even by mistake. bypass_csp only so that
    # axe can be injected into the stemcheck report, which carries its own policy.
    context = browser.new_context(
        viewport={"width": width, "height": 900}, offline=True, bypass_csp=True
    )
    try:
        page = context.new_page()
        page.goto(url)
        page.add_script_tag(content=html_checks.axe_source())
        return page.evaluate(RUN, TAGS)
    finally:
        context.close()


def describe(found: list[dict]) -> str:
    return "; ".join(f"{v['id']} ({v['impact']}): {v['nodes']}" for v in found)


@pytest.mark.parametrize("width", [1280, 320])
def test_own_pages_have_no_axe_violations(site, browser, width):
    problems = {}
    for rel in [*OWN_PAGES, *example_pages(site)]:
        found = violations(browser, (site / rel).as_uri(), width)
        if found:
            problems[rel] = describe(found)
    assert not problems


@pytest.mark.parametrize("width", [1280, 320])
def test_converted_pages_have_no_serious_axe_violations(site, browser, width):
    """The tool's own output, linked from every example."""
    problems = {}
    for rel in converted_pages(site):
        found = [
            v
            for v in violations(browser, (site / rel).as_uri(), width)
            if v["impact"] in ("serious", "critical")
        ]
        if found:
            problems[rel] = describe(found)
    assert not problems


@pytest.mark.parametrize("width", [1280, 320])
def test_stemcheck_report_has_no_serious_axe_violations(site, browser, width):
    found = [
        v
        for v in violations(browser, (site / "stemcheck/sample-report.html").as_uri(), width)
        if v["impact"] in ("serious", "critical")
    ]
    assert not found, describe(found)


def test_nothing_scrolls_sideways_at_320_px(site, browser):
    context = browser.new_context(viewport={"width": 320, "height": 900}, offline=True)
    page = context.new_page()
    wide = {}
    for rel in [*OWN_PAGES, *example_pages(site)]:
        page.goto((site / rel).as_uri())
        width = page.evaluate("document.documentElement.scrollWidth")
        if width > 320:
            wide[rel] = width
    context.close()
    assert not wide


def test_text_spacing_does_not_clip_or_overflow(site, browser):
    """WCAG 1.4.12: with the author's spacing overridden, the page still reflows."""
    css = (
        "* { line-height: 1.5 !important; letter-spacing: 0.12em !important; "
        "word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }"
    )
    context = browser.new_context(viewport={"width": 320, "height": 900}, offline=True)
    page = context.new_page()
    wide = {}
    for rel in ["index.html", "examples/index.html", "before-after/index.html"]:
        page.goto((site / rel).as_uri())
        page.add_style_tag(content=css)
        width = page.evaluate("document.documentElement.scrollWidth")
        if width > 320:
            wide[rel] = width
    context.close()
    assert not wide


def test_skip_link_is_the_first_tab_stop_and_shows(site, browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900}, offline=True)
    page = context.new_page()
    page.goto((site / "index.html").as_uri())
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement.textContent.trim()")
    box = page.evaluate(
        "(() => { const r = document.activeElement.getBoundingClientRect();"
        " return [r.top, r.left]; })()"
    )
    context.close()
    assert focused == "Skip to the content"
    assert box[0] >= 0 and box[1] >= 0


def test_every_scrollable_region_can_be_reached_by_keyboard(site, browser):
    """A scrolling table or equation must be a tab stop, or a keyboard user cannot read it."""
    js = """() => [...document.querySelectorAll('body *')].filter(e => {
      const s = getComputedStyle(e);
      return (s.overflowX === 'auto' || s.overflowX === 'scroll')
        && e.scrollWidth > e.clientWidth + 1
        && e.tabIndex < 0 && !e.querySelector('a, button, input, [tabindex]');
    }).map(e => e.tagName + '.' + e.className)"""
    context = browser.new_context(viewport={"width": 320, "height": 900}, offline=True)
    page = context.new_page()
    stuck = {}
    for rel in [*OWN_PAGES, *example_pages(site)]:
        page.goto((site / rel).as_uri())
        found = page.evaluate(js)
        if found:
            stuck[rel] = found
    context.close()
    assert not stuck


def test_pages_work_without_javascript(site, browser):
    context = browser.new_context(java_script_enabled=False, offline=True)
    page = context.new_page()
    for rel in [*OWN_PAGES, *example_pages(site)]:
        page.goto((site / rel).as_uri())
        assert page.locator("h1").count() == 1, rel
        assert page.locator("main").inner_text().strip(), rel
    context.close()
