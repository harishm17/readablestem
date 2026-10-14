"""The student file in Chromium, Firefox and WebKit (T3-8): does the page render, do its
pictures load, and is the MathML drawn as maths (fractions stacked, scripts raised and
lowered, nothing cut off or wider than the screen) at 1280 and 375 CSS px?

Two versions of the committed demo document are opened from disk: the file as the pipeline
writes it, and the Canvas export's `index.html` with its pictures as files (which carries the
maths font stack, `export_canvas.MATH_FONT_STACK`). Native MathML is what these browsers
draw; Canvas may replace it with its own MathJax, which this does not test
(docs/canvas-sandbox-test.md does, by hand).

Run with: uv run pytest -m browser tests/test_student_file_browsers.py
An engine that is not installed is skipped: install them with
`uv run playwright install chromium firefox webkit` (WebKit also needs the system packages
that `uv run playwright install-deps webkit` installs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import readablestem
from readablestem.pipeline.export_canvas import MATH_FONT_STACK, export_canvas

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

DEMO = Path(readablestem.__file__).parent / "web" / "static" / "demo" / "index.html"
ENGINES = ["chromium", "firefox", "webkit"]
WIDTHS = [1280, 375]

# One browser-side script reports everything the checks need.
INSPECT = """
() => {
  const box = (el) => { const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            width: r.width, height: r.height}; };
  const scrolled = (el) => !!el.closest('.equation-scroll, .table-scroll, [tabindex]');
  const maths = [...document.querySelectorAll('math')];
  const out = {
    maths: maths.length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    empty: [], cutOff: [], fractions: [], scripts: [], displays: [], families: [],
    wide: [...document.querySelectorAll('body *')].filter(el =>
      el.getBoundingClientRect().right > window.innerWidth + 1 && !scrolled(el)
      && !el.closest('math')).map(el => el.tagName),
    images: [...document.images].map(i => ({src: i.getAttribute('src').slice(0, 40),
      ok: i.complete && i.naturalWidth > 0})),
    viewport: window.innerWidth,
  };
  maths.forEach((m, n) => {
    const r = box(m);
    if (r.width <= 0 || r.height <= 0) out.empty.push(n);
    if (!scrolled(m) && r.right > window.innerWidth + 1) out.cutOff.push(n);
    out.displays.push(getComputedStyle(m).display);
    out.families.push(getComputedStyle(m).fontFamily);
    m.querySelectorAll('mfrac').forEach(f => {
      const [num, den] = f.children;
      if (num && den) out.fractions.push(box(num).bottom <= box(den).top + 1);
    });
    m.querySelectorAll('msup').forEach(s => {
      const [base, sup] = s.children;
      if (base && sup) out.scripts.push(
        ['sup', (box(sup).top + box(sup).bottom) / 2 < (box(base).top + box(base).bottom) / 2]);
    });
    m.querySelectorAll('msub').forEach(s => {
      const [base, sub] = s.children;
      if (base && sub) out.scripts.push(
        ['sub', (box(sub).top + box(sub).bottom) / 2 > (box(base).top + box(base).bottom) / 2]);
    });
  });
  return out;
}
"""


@pytest.fixture(scope="module")
def playwright_driver():
    with sync_api.sync_playwright() as driver:
        yield driver


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict[str, Path]:
    """The demo as written, and its Canvas version with the pictures beside it."""
    html = DEMO.read_text(encoding="utf-8")
    root = tmp_path_factory.mktemp("student-file")
    plain = root / "plain.html"
    plain.write_text(html, encoding="utf-8")
    export = export_canvas(html, title="Demo", folder="demo-000000")
    canvas = root / "canvas"
    (canvas / "images").mkdir(parents=True)
    (canvas / "index.html").write_text(export.index_html, encoding="utf-8")
    for name, data in export.images.items():
        (canvas / name).write_bytes(data)
    return {"as written": plain, "canvas version": canvas / "index.html"}


def launch(driver, engine: str):
    try:
        return getattr(driver, engine).launch()
    except Exception as exc:  # engine not installed, or its system libraries are missing
        pytest.skip(f"{engine} unavailable ({type(exc).__name__}); see the module docstring")


@pytest.fixture(scope="module", params=ENGINES)
def engine_browser(request, playwright_driver):
    browser = launch(playwright_driver, request.param)
    yield request.param, browser
    browser.close()


def inspect_page(browser, path: Path, width: int) -> tuple[dict, list[str], bytes]:
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda exc: problems.append(f"page error: {exc}"))
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.goto(path.as_uri())
    page.wait_for_load_state("load")
    report = page.evaluate(INSPECT)
    first = page.locator("math").first
    first.scroll_into_view_if_needed()
    shot = first.screenshot()
    context.close()
    return report, problems, shot


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("version", ["as written", "canvas version"])
def test_the_student_file_is_drawn_as_maths_in_every_browser(engine_browser, files, version, width):
    engine, browser = engine_browser
    report, problems, shot = inspect_page(browser, files[version], width)
    where = f"{engine}, {version}, {width}px"
    assert problems == [], where
    assert report["maths"] >= 10, where  # the demo has many equations
    assert report["empty"] == [], f"{where}: equations with no size"
    assert report["cutOff"] == [], f"{where}: equations past the right edge"
    if version == "as written":
        assert report["overflow"] <= 0, f"{where}: the page scrolls sideways"
    else:
        # Canvas keeps no `overflow-wrap` (docs/output.md), so a long unbroken web address in
        # the source note can run past a 375 px screen in the standalone Canvas page; nothing
        # else may.
        assert set(report["wide"]) <= {"A"}, f"{where}: {sorted(set(report['wide']))} too wide"
    assert report["fractions"] and all(report["fractions"]), f"{where}: a fraction is not stacked"
    assert all(ok for _, ok in report["scripts"]), f"{where}: a script is on the wrong side"
    assert all("math" in display for display in report["displays"]), (where, report["displays"][:3])
    assert all(i["ok"] for i in report["images"]), f"{where}: a picture did not load"
    assert len(shot) > 200  # a screenshot of the first equation is not an empty image


@pytest.mark.parametrize("engine", ENGINES)
def test_the_canvas_version_asks_for_the_maths_font_stack(engine, playwright_driver, files):
    browser = launch(playwright_driver, engine)
    try:
        report, _, _ = inspect_page(browser, files["canvas version"], 1280)
    finally:
        browser.close()
    first_family = MATH_FONT_STACK.split(",")[0].strip("'")
    assert all(first_family in family for family in report["families"]), (
        engine,
        report["families"][:2],
    )
