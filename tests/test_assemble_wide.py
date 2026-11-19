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

from readablestem.core.types import (
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    inline_marker,
)
from readablestem.pipeline.assemble import (
    render_html,
    sanitize_mathml,
)
from readablestem.pipeline.html_checks import axe_checks, static_checks

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


def test_only_wide_equations_become_named_tab_stops():
    html = document([SHORT, long_sum(), long_sum()], [None, None, "(2.4)"])
    assert "tabindex" not in opening_tag(html, "p1-e0")
    wide = opening_tag(html, "p1-e1")
    assert 'tabindex="0"' in wide and 'role="group"' in wide and 'aria-label="Equation"' in wide
    assert 'aria-label="Equation (2.4)"' in opening_tag(html, "p1-e2")
    assert all(c.passed for c in static_checks(html))


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


def in_chromium(
    html: str,
    script: str,
    *,
    focus: str | None = None,
    keys: list[str] | None = None,
    until: str | None = None,
    width: int = 320,
    media: str | None = None,
):
    """`script`'s result before and after the keyboard. `focus` is a JavaScript condition:
    Tab is pressed until it holds (the skip link and the notice's link come first, and how
    many links the page opens with is not what these tests are about)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error:
            pytest.skip("Playwright's Chromium is not installed")
        try:
            page = browser.new_page(viewport={"width": width, "height": 640})
            if media:
                page.emulate_media(media=media)
            page.set_content(html)
            before = page.evaluate(script)
            for _ in range(8 if focus else 0):
                page.keyboard.press("Tab")
                if page.evaluate(focus):
                    break
            else:
                if focus:
                    raise AssertionError(f"Tab never reached: {focus}")
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
    before, after = in_chromium(
        html,
        script,
        focus="document.activeElement.id === 'p1-e0'",
        keys=["ArrowRight"],
        until=scrolled,
    )
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
              active: document.activeElement.classList.contains('equation-scroll')};
    }"""
    before, after = in_chromium(
        paragraph_with([SHORT, long_sum()]),
        script,
        focus="document.activeElement.classList.contains('equation-scroll')",
    )
    assert before["page"][0] <= before["page"][1] and before["overflows"]
    assert after["active"]  # the keyboard reaches the wide one


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


def table_and_equations() -> str:
    """A table of nine columns of unbreakable numbers (far wider than a sheet) between a
    long chain of terms, a medium one that fits when scaled, and a short one."""
    cells = [TableCell(row=0, col=c, text=f"Reading {c}", header="col") for c in range(9)]
    cells += [TableCell(row=1, col=c, text=f"0.{c}12345678901234") for c in range(9)]
    grid = TableGrid(rows=2, cols=9, cells=cells, header_rows=1, caption="Table 3 Readings.")
    elements = [Element(id="t", kind=Kind.TABLE, page=0, bbox=(0, 0, 1, 1), order=0, table=grid)]
    fixes = [Fix(element_id="t", kind=Kind.TABLE, status=Status.ACCEPTED,
                 table=TableFix(grid=grid))]  # fmt: skip
    for i, mathml in enumerate([long_sum(60), long_sum(10), SHORT], start=1):
        elements.append(
            Element(id=f"e{i}", kind=Kind.EQUATION, page=0, bbox=(0, i, 1, i + 1), order=i,
                    display="block")
        )  # fmt: skip
        fixes.append(
            Fix(element_id=f"e{i}", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.95,
                equation=EquationFix(latex="x", mathml=mathml, speech="x", display="block"))
        )  # fmt: skip
    return render_html(title="Wide", lang="en", elements=elements, fixes=fixes, crops={})


PAPER = """() => {
  const sheet = window.innerWidth;
  const table = document.querySelector('table').getBoundingClientRect();
  const noted = (box) => box.classList.contains('pw')
    && getComputedStyle(box, '::after').display !== 'none';
  return {
    pageScrolls: document.documentElement.scrollWidth > sheet,
    tableInside: table.left >= 0 && table.right <= sheet + 1,
    // Each equation is inside the sheet or says on paper that it is not.
    equations: [...document.querySelectorAll('.equation')].map((box) =>
      box.querySelector('math').getBoundingClientRect().right <= sheet + 1 || noted(box)),
    notes: [...document.querySelectorAll('.equation')].map(noted),
  };
}"""


@pytest.mark.browser
@pytest.mark.parametrize("sheet", [720, 480])  # Letter at 100% and at 150% (0.5 in margins)
def test_on_paper_a_wide_table_and_a_long_equation_are_not_cut_without_a_note(sheet):
    facts, _ = in_chromium(table_and_equations(), PAPER, width=sheet, media="print")
    assert not facts["pageScrolls"]
    assert facts["tableInside"]  # the table wraps inside the sheet, it does not scroll
    assert all(facts["equations"])
    assert facts["notes"] == [True, False, False]  # only the one too wide even when scaled
