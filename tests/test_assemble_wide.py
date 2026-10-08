"""Wide display equations scroll inside their own box, not the page (WCAG 1.4.10).

A display equation cannot wrap, so on a 320 CSS px screen a long one used to widen the
whole document. `.equation` now scrolls sideways, and the equations that may be too wide
for a phone (`assemble.estimate_width_em` >= `WIDE_EQUATION_EM`) are named tab stops so
keyboard users can scroll them too. The browser tests check that against Chromium's
real layout, for synthetic equations and for every equation in the gold set.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from a11y_stem.core.types import Element, EquationFix, Fix, Kind, Status, inline_marker
from a11y_stem.pipeline.assemble import (
    WIDE_EQUATION_EM,
    estimate_width_em,
    render_html,
    sanitize_mathml,
)
from a11y_stem.pipeline.html_checks import axe_checks, static_checks

GOLD = Path(__file__).resolve().parents[1] / "data" / "gold" / "openstax"
SHORT = "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"


def long_sum(terms: int = 40) -> str:
    """a_1 + a_2 + ... : far wider than a phone screen."""
    body = "<mo>+</mo>".join(f"<msub><mi>a</mi><mn>{i}</mn></msub>" for i in range(1, terms + 1))
    return f"<math><mrow>{body}</mrow></math>"


def document(mathmls: list[str], labels: list[str | None] | None = None) -> str:
    labels = labels or [None] * len(mathmls)
    elements, fixes = [], []
    for i, (mathml, label) in enumerate(zip(mathmls, labels, strict=True)):
        eid = f"p1-e{i}"
        elements.append(
            Element(id=eid, kind=Kind.EQUATION, page=0, bbox=(0, i, 1, i + 1), order=i,
                    display="block", equation_label=label)
        )  # fmt: skip
        fixes.append(
            Fix(element_id=eid, kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.95,
                equation=EquationFix(latex="x", mathml=mathml, speech="x", display="block"))
        )  # fmt: skip
    return render_html(title="Wide", lang="en", elements=elements, fixes=fixes, crops={})


def paragraph_with(mathmls: list[str]) -> str:
    """One paragraph with each MathML as an inline equation in it."""
    ids = [f"p1-e{i + 1}" for i in range(len(mathmls))]
    text = "The sum " + " and ".join(inline_marker(i) for i in ids) + " is long."
    elements = [Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 1, 1), order=0,
                        text=text)]  # fmt: skip
    fixes = []
    for order, (eid, mathml) in enumerate(zip(ids, mathmls, strict=True), start=1):
        elements.append(
            Element(id=eid, kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=order,
                    display="inline", parent_id="p1-e0")
        )  # fmt: skip
        fixes.append(
            Fix(element_id=eid, kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.95,
                equation=EquationFix(latex="x", mathml=mathml, speech="x", display="inline"))
        )  # fmt: skip
    return render_html(title="Wide", lang="en", elements=elements, fixes=fixes, crops={})


def opening_tag(html: str, element_id: str) -> str:
    start = html.index(f'id="{element_id}"')
    return html[html.rindex("<", 0, start) : html.index(">", start) + 1]


# --------------------------------------------------------------------------- offline


def test_the_width_estimate_tells_short_from_long():
    assert estimate_width_em(SHORT) < 3
    assert estimate_width_em(long_sum(40)) > 3 * WIDE_EQUATION_EM
    assert estimate_width_em(long_sum(3)) < WIDE_EQUATION_EM
    # A fraction is as wide as its wider part, not the sum of both.
    frac = "<math><mfrac><mrow>{0}</mrow><mrow>{0}</mrow></mfrac></math>"
    row = "<mi>x</mi><mo>+</mo><mi>y</mi>"
    assert estimate_width_em(frac.format(row)) == pytest.approx(
        estimate_width_em(f"<math>{row}</math>") + 0.2
    )
    assert estimate_width_em("not maths") == 0.0


def test_only_wide_equations_become_named_tab_stops():
    html = document([SHORT, long_sum(), long_sum()], [None, None, "(2.4)"])
    assert "tabindex" not in opening_tag(html, "p1-e0")
    assert opening_tag(html, "p1-e1") == (
        '<div class="equation" id="p1-e1" tabindex="0" role="group" aria-label="Equation">'
    )
    assert 'aria-label="Equation (2.4)"' in opening_tag(html, "p1-e2")
    assert all(c.passed for c in static_checks(html))


def test_only_wide_inline_equations_get_a_scroll_box():
    html = paragraph_with([SHORT, long_sum()])
    assert '<p id="p1-e0">The sum <math display="inline" id="p1-e1">' in html
    assert (
        ' and <span class="equation-scroll" tabindex="0" role="group" aria-label="Equation">'
        '<math display="inline" id="p1-e2">'
    ) in html
    assert all(c.passed for c in static_checks(html))


def test_not_converted_equations_are_images_that_shrink_instead():
    eq = Element(id="p1-e0", kind=Kind.EQUATION, page=0, bbox=(0, 0, 900, 20), order=0,
                 display="block")  # fmt: skip
    fix = Fix(element_id="p1-e0", kind=Kind.EQUATION, status=Status.ABSTAINED, reason="x")
    html = render_html(title="T", lang="en", elements=[eq], fixes=[fix], crops={})
    assert "tabindex" not in html


# --------------------------------------------------------------------------- Chromium


def gold_mathml() -> list[str]:
    found = []
    for path in sorted(glob.glob(str(GOLD / "*" / "*.jsonl"))):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("kind") == "equation" and row.get("mathml"):
                found.append(row["mathml"])
    return found


MEASURE = """() => ({
  page: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
  equations: [...document.querySelectorAll('.equation')].map((box) => ({
    id: box.id,
    overflows: box.scrollWidth > box.clientWidth + 1,
    focusable: box.tabIndex === 0,
    role: box.getAttribute('role'),
    name: box.getAttribute('aria-label'),
  })),
})"""


def in_chromium(html: str, script: str, *, keys: list[str] | None = None, until: str | None = None):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error:
            pytest.skip("Playwright's Chromium is not installed")
        try:
            page = browser.new_page(viewport={"width": 320, "height": 640})
            page.set_content(html)
            before = page.evaluate(script)
            for key in keys or []:
                page.keyboard.press(key)
            if until:  # keyboard scrolling is smooth, so it ends a little later
                page.wait_for_function(until, timeout=5000)
            return before, page.evaluate(script)
        finally:
            browser.close()


@pytest.mark.browser
def test_a_wide_equation_scrolls_in_its_box_and_the_page_does_not():
    html = document([SHORT, long_sum(), long_sum()], [None, None, "(2.4)"])
    facts, _ = in_chromium(html, MEASURE)
    scroll_width, client_width = facts["page"]
    assert scroll_width <= client_width, facts["page"]  # no sideways scrolling of the page
    by_id = {e["id"]: e for e in facts["equations"]}
    assert not by_id["p1-e0"]["overflows"] and not by_id["p1-e0"]["focusable"]
    for eid, name in (("p1-e1", "Equation"), ("p1-e2", "Equation (2.4)")):
        assert by_id[eid] == {
            "id": eid, "overflows": True, "focusable": True, "role": "group", "name": name,
        }  # fmt: skip


@pytest.mark.browser
def test_the_keyboard_reaches_and_scrolls_a_wide_equation():
    html = document([long_sum()])
    script = """() => [document.activeElement.id,
                       document.getElementById('p1-e0').scrollLeft]"""
    scrolled = "document.getElementById('p1-e0').scrollLeft > 0"
    before, after = in_chromium(html, script, keys=["Tab", "ArrowRight"], until=scrolled)
    assert before == ["", 0]
    assert after[0] == "p1-e0" and after[1] > 0


@pytest.mark.browser
def test_every_gold_equation_that_overflows_at_320_px_is_a_tab_stop():
    """The width estimate decides which boxes get a tab stop; Chromium decides which
    really scroll. Every one that scrolls must be reachable from the keyboard."""
    mathmls = [m for m in gold_mathml() if sanitize_mathml(m, display="block")]
    assert len(mathmls) >= 25
    html = document([*mathmls, long_sum()])
    facts, _ = in_chromium(html, MEASURE)
    assert facts["page"][0] <= facts["page"][1]
    overflowing = [e for e in facts["equations"][:-1] if e["overflows"]]  # gold only
    assert len(overflowing) >= 2  # the gold set has some too wide for a phone
    missed = [e["id"] for e in facts["equations"] if e["overflows"] and not e["focusable"]]
    assert missed == []
    assert sum(e["focusable"] for e in facts["equations"]) <= len(mathmls) // 3  # few stops


@pytest.mark.browser
def test_a_wide_inline_equation_scrolls_in_its_box_and_the_page_does_not():
    script = """() => {
      const box = document.querySelector('.equation-scroll');
      return {page: [document.documentElement.scrollWidth,
                     document.documentElement.clientWidth],
              overflows: box.scrollWidth > box.clientWidth + 1,
              active: document.activeElement.className};
    }"""
    before, after = in_chromium(paragraph_with([SHORT, long_sum()]), script, keys=["Tab"])
    assert before["page"][0] <= before["page"][1] and before["overflows"]
    assert after["active"] == "equation-scroll"  # the first tab stop on the page


@pytest.mark.browser
def test_axe_finds_no_problems_with_wide_equations():
    checks = axe_checks(document([SHORT, long_sum()], [None, "2.4"]))
    if len(checks) == 1 and checks[0].code == "skipped":
        pytest.skip("axe-core needs Playwright and its Chromium")
    assert [c.name for c in checks] == ["axe"] and checks[0].passed
    checks = axe_checks(paragraph_with([SHORT, long_sum()]))
    if len(checks) == 1 and checks[0].code == "skipped":
        pytest.skip("axe-core needs Playwright and its Chromium")
    assert [c.name for c in checks] == ["axe"] and checks[0].passed
