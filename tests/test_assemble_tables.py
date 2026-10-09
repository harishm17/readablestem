"""assemble: TABLE elements (pipeline.assemble_tables) and the inline-text seam.

A rebuilt table is written as a scrollable, named <table>; anything else keeps the image
of the table, flagged like an equation that was not converted. Documents without tables
are written exactly as before (no table CSS either).
"""

from __future__ import annotations

import pymupdf
import pytest

from a11y_stem.core.types import (
    INLINE_MARKER_RE,
    Element,
    Fix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    inline_marker,
)
from a11y_stem.pipeline import assemble, assemble_tables, html_checks, inline_text, safe_html
from a11y_stem.pipeline.assemble import FLAGGED_NOTE, STYLE, render_html
from a11y_stem.pipeline.assemble_tables import TABLE_CSS, TABLE_NOT_CONVERTED_ALT, TABLE_NOTE
from a11y_stem.pipeline.html_checks import static_checks


def png() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 20), False)
    pixmap.set_rect(pixmap.irect, (20, 20, 20))
    return pixmap.tobytes("png")


def grid(caption: str | None = "Table 1 Free fall.") -> TableGrid:
    cells = [
        TableCell(row=0, col=0, text="Time (s)", header="col"),
        TableCell(row=0, col=1, text="Speed (m/s)", header="col"),
        TableCell(row=1, col=0, text="1"),
        TableCell(row=1, col=1, text="9.8"),
    ]
    return TableGrid(rows=2, cols=2, cells=cells, header_rows=1, caption=caption, source="model")


def table_element(eid: str = "p1-e1", order: int = 1, caption: str | None = None) -> Element:
    return Element(
        id=eid,
        kind=Kind.TABLE,
        page=0,
        bbox=(72, 100, 300, 200),
        order=order,
        caption=caption,
        table=grid(caption).model_copy(update={"source": "local"}),
    )


def heading() -> Element:
    return Element(id="p1-e0", kind=Kind.HEADING, page=0, bbox=(72, 72, 300, 90), order=0,
                   text="Results")  # fmt: skip


def table_fix(eid: str = "p1-e1", status: Status = Status.ACCEPTED, **kw) -> Fix:
    return Fix(element_id=eid, kind=Kind.TABLE, status=status, **kw)


def render(elements: list[Element], fixes: list[Fix], crops: dict[str, bytes] | None = None):
    return render_html(title="Tables", lang="en", elements=elements, fixes=fixes, crops=crops or {})


# --------------------------------------------------------------------------- rebuilt tables


def wide_grid(caption: str | None = "Table 2 Planets.") -> TableGrid:
    """Seven columns of long words: far wider than a phone screen."""
    names = ["Planet", "Distance", "Diameter", "Temperature", "Gravitation", "Atmosphere", "Moons"]
    cells = [TableCell(row=0, col=c, text=name, header="col") for c, name in enumerate(names)]
    cells += [TableCell(row=1, col=c, text=f"value{c}") for c in range(len(names))]
    return TableGrid(rows=2, cols=len(names), cells=cells, header_rows=1, caption=caption)


def test_a_rebuilt_table_is_a_table_in_a_scroll_box():
    html = render([heading(), table_element()], [table_fix(table=TableFix(grid=grid()))])
    assert (
        '<div class="table-scroll" id="p1-e1"><table><caption id="p1-e1-caption">'
        "Table 1 Free fall.</caption>"
    ) in html
    assert '<th scope="col">Time (s)</th>' in html
    assert all(c.passed for c in static_checks(html)), static_checks(html)
    assert FLAGGED_NOTE not in html  # the student note: nothing shown as an image


def test_a_wide_table_is_a_named_tab_stop():
    assert assemble_tables.estimate_min_width_em(grid()) < assemble_tables.WIDE_TABLE_EM
    assert assemble_tables.estimate_min_width_em(wide_grid()) >= assemble_tables.WIDE_TABLE_EM
    html = render([table_element()], [table_fix(table=TableFix(grid=wide_grid()))])
    assert (
        '<div class="table-scroll" id="p1-e1" role="group" aria-labelledby="p1-e1-caption" '
        'tabindex="0"><table><caption id="p1-e1-caption">Table 2 Planets.</caption>'
    ) in html
    unnamed = render([table_element()], [table_fix(table=TableFix(grid=wide_grid(None)))])
    assert '<div class="table-scroll" id="p1-e1" role="group" aria-label="Table" tabindex="0">' in (
        unnamed
    )


def test_the_width_estimate_counts_words_maths_and_spanning_cells():
    def width(cells, cols):
        return assemble_tables.estimate_min_width_em(TableGrid(rows=1, cols=cols, cells=cells))

    one_word = width([TableCell(row=0, col=0, text="abcdefghij klm")], 1)
    assert one_word == pytest.approx(10 * 0.6 + 1.4)  # the longest word, and the edges
    mathml = "<math><mi>a</mi><mo>+</mo><mi>b</mi></math>"
    maths = width([TableCell(row=0, col=0, text="a + b", latex="a+b", mathml=mathml)], 1)
    assert maths > 1.4
    spanning = width([TableCell(row=0, col=0, col_span=2, text="x" * 30)], 2)
    assert spanning == pytest.approx(30 * 0.6 + 1.4)  # two edges, one of them inside


def test_a_table_without_a_caption_has_none():
    html = render([table_element()], [table_fix(table=TableFix(grid=grid(caption=None)))])
    assert '<div class="table-scroll" id="p1-e1"><table><thead>' in html
    assert "<caption" not in html


def test_a_table_waiting_for_review_is_shown_as_rebuilt():
    fix = table_fix(status=Status.NEEDS_REVIEW, table=TableFix(grid=grid()))
    html = render([table_element()], [fix])
    assert "<table>" in html and TABLE_NOT_CONVERTED_ALT not in html


def test_table_css_is_added_once_and_only_with_a_table():
    fixes = [
        table_fix("p1-e1", table=TableFix(grid=grid())),
        table_fix("p1-e2", table=TableFix(grid=grid(caption="Table 2 Again."))),
    ]
    html = render([table_element("p1-e1", 1), table_element("p1-e2", 2)], fixes)
    assert f"<style>\n{STYLE}{TABLE_CSS}</style>" in html
    assert html.count(".table-scroll {") == 1
    without = render([heading()], [])
    assert f"<style>\n{STYLE}</style>" in without and "table" not in without


# --------------------------------------------------------------------------- kept as an image


def test_a_table_not_rebuilt_keeps_its_image_flagged():
    element = table_element(caption="Table 1 Free fall.")
    fix = table_fix(status=Status.FAILED, reason="table_not_rebuilt")
    html = render([element], [fix], {"p1-e1": png()})
    assert (
        '<div class="flagged" id="p1-e1" data-status="failed" data-reason="table_not_rebuilt">'
        in html
    )
    assert f'alt="{TABLE_NOT_CONVERTED_ALT}"' in html and 'width="304" height="133"' in html
    assert "<figcaption>Table 1 Free fall.</figcaption>" in html
    assert f'<span class="flag-label">Not converted.</span> {TABLE_NOTE}' in html
    assert "<table>" not in html and TABLE_CSS not in html
    assert all(c.passed for c in static_checks(html))
    assert FLAGGED_NOTE in html  # the student note says some parts are pictures


@pytest.mark.parametrize(
    ("fix", "reason"),
    [
        (None, "no_fix"),
        (table_fix(status=Status.ACCEPTED), "no_output"),  # accepted, but nothing to write
        (table_fix(status=Status.REJECTED, table=TableFix(grid=grid())), ""),
        (
            table_fix(table=TableFix(grid=grid().model_copy(update={"is_data": False}))),
            "no_output",
        ),
    ],
)
def test_anything_but_a_usable_rebuilt_table_keeps_the_image(fix, reason):
    html = render([table_element()], [fix] if fix else [], {"p1-e1": png()})
    assert TABLE_NOT_CONVERTED_ALT in html and "<table>" not in html
    if reason:
        assert f'data-reason="{reason}"' in html
    else:
        assert "data-reason" not in html


def test_without_a_crop_the_table_text_is_not_invented():
    html = render([table_element()], [table_fix(status=Status.FAILED, reason="table_not_rebuilt")])
    assert "The original image is not available." in html


def test_a_proposal_without_its_caption_gets_the_elements_caption():
    """Layout took the "Table N" caption out of the text, so an (edited) grid without it
    would lose it from the output entirely."""
    element = table_element(caption="Table 1 Free fall.")
    html = render([element], [table_fix(table=TableFix(grid=grid(caption=None)))])
    assert '<caption id="p1-e1-caption">Table 1 Free fall.</caption>' in html
    wide = render([element], [table_fix(table=TableFix(grid=wide_grid(None)))])
    assert 'aria-labelledby="p1-e1-caption"' in wide and "Table 1 Free fall.</caption>" in wide
    own = grid(caption="Table 1 Free fall (corrected).")
    html = render([element], [table_fix(table=TableFix(grid=own))])
    assert "Free fall (corrected).</caption>" in html and html.count("<caption") == 1


def test_element_ids_that_are_not_plain_ids_still_render():
    assert assemble_tables.id_prefix("p1-e3") == "p1-e3"
    assert assemble_tables.id_prefix("3 tables!") == "t3-tables-"
    element = table_element(eid="weird id")
    html = render([element], [table_fix("weird id", table=TableFix(grid=wide_grid()))])
    assert 'aria-labelledby="weird-id-caption"' in html and 'id="weird-id-caption"' in html


# --------------------------------------------------------------------------- html checks

TABLE_RULES = [
    "html.table_headers",
    "html.table_header_scope",
    "html.table_header_text",
    "html.table_caption",
    "html.table_layout",
]


def page(body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><title>T</title></head>'
        f"<body><main><h1>T</h1>{body}</main></body></html>"
    )


def table_failures(html: str) -> list[str | None]:
    return [c.code for c in static_checks(html) if not c.passed and c.name in TABLE_RULES]


def test_a_rebuilt_table_passes_the_table_rules():
    html = render([heading(), table_element()], [table_fix(table=TableFix(grid=grid()))])
    checks = static_checks(html)
    assert [c.name for c in checks][-5:] == TABLE_RULES
    assert all(c.passed for c in checks)


def test_documents_without_tables_get_no_table_rules():
    html = render([heading()], [])
    assert not any(c.name in TABLE_RULES for c in static_checks(html))
    kept = render([table_element()], [table_fix(status=Status.FAILED, reason="table_not_rebuilt")])
    assert not any(c.name in TABLE_RULES for c in static_checks(kept))  # an image, no table


@pytest.mark.parametrize(
    ("body", "codes"),
    [
        ("<table><tr><td>1</td><td>2</td></tr></table>", ["table_no_headers"]),
        ("<table><tr><th>A</th></tr><tr><td>1</td></tr></table>", ["th_without_scope"]),
        (
            '<table><tr><th scope="col"></th><th scope="col">B</th></tr>'
            "<tr><td>1</td><td>2</td></tr></table>",
            ["empty_th"],
        ),
        (
            '<table role="presentation"><tr><td>Name</td><td>Box</td></tr></table>',
            ["layout_table"],
        ),
        (
            '<p>Table 2.1 Speeds of cars.</p><div><table><tr><th scope="col">A</th></tr>'
            "<tr><td>1</td></tr></table></div>",
            ["table_caption_missing"],
        ),
        (
            '<div><table><tr><th scope="col">A</th></tr><tr><td>1</td></tr></table></div>'
            "<p>TABLE 3: Results</p>",
            ["table_caption_missing"],
        ),
    ],
)
def test_table_rules_fail_on_broken_tables(body, codes):
    assert table_failures(page(body)) == codes


@pytest.mark.parametrize(
    "body",
    [
        # headers/id instead of scope (the serialiser's irregular tables)
        '<table><tr><th id="h1">A</th></tr><tr><td headers="h1">1</td></tr></table>',
        # a header holding only maths, or an image with alt text
        '<table><tr><th scope="col"><math display="inline"><mi>x</mi></math></th></tr>'
        "<tr><td>1</td></tr></table>",
        '<table><tr><th scope="col"><img alt="Velocity" src="data:,"></th></tr>'
        "<tr><td>1</td></tr></table>",
        # a sentence about the table is not its caption, and a caption of its own is fine
        '<p>Table 2 shows that the speed doubles.</p><table><tr><th scope="col">A</th></tr>'
        "<tr><td>1</td></tr></table>",
        '<p>Table 2.1 Speeds.</p><table><caption>Table 2.1 Speeds.</caption><tr><th scope="col">'
        "A</th></tr><tr><td>1</td></tr></table>",
        # a heading between the paragraph and the table
        '<p>Table 2.1 Speeds.</p><h2>Results</h2><table><tr><th scope="col">A</th></tr>'
        "<tr><td>1</td></tr></table>",
    ],
)
def test_table_rules_pass_on_good_tables(body):
    assert table_failures(page(body)) == []


def test_a_nested_tables_header_belongs_to_the_inner_table():
    body = (
        '<table><tr><th scope="col">Outer</th></tr><tr><td>'
        "<table><tr><td>1</td></tr></table></td></tr></table>"
    )
    assert table_failures(page(body)) == ["table_no_headers"]


# --------------------------------------------------------------------------- shared seams


def test_assemble_keeps_its_old_names():
    assert assemble.INLINE_MARKER_RE is INLINE_MARKER_RE
    assert assemble.sanitize_mathml is safe_html.sanitize_mathml
    assert assemble.MATHML_ELEMENTS is safe_html.MATHML_ELEMENTS
    assert {Status.ABSTAINED, Status.FAILED, Status.REJECTED} == assemble.NOT_CONVERTED


def test_inline_text_places_equations_and_escapes_the_rest():
    text = f"a < b {inline_marker('p1-e2')} and {inline_marker('p9-e9')}."
    out = inline_text.render_inline_text(
        text, element=None, equation=lambda eid: "<math>" if eid == "p1-e2" else None
    )
    assert out == f"a &lt; b <math> and {inline_marker('p9-e9')}."


def test_the_passage_seam_writes_the_language():
    # The text work's tests (tests/test_text_*.py) cover links, colour and language.
    element = Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 1, 1), order=0,
                      text="Plain text.", lang="fr")  # fmt: skip
    assert inline_text.passage_attrs(element) == ' lang="fr"'
    assert '<p id="p1-e0" lang="fr">Plain text.</p>' in render([element], [])


# --------------------------------------------------------------------------- browser


@pytest.mark.browser
def test_axe_finds_no_problems_with_tables_rebuilt_or_kept():
    pytest.importorskip("playwright.sync_api")
    elements = [
        heading(),
        table_element("p1-e1", 1),
        table_element("p1-e2", 2, caption="Table 2 Kept."),
        table_element("p1-e3", 3),
    ]
    fixes = [
        table_fix("p1-e1", table=TableFix(grid=grid())),
        table_fix("p1-e2", status=Status.FAILED, reason="table_not_rebuilt"),
        table_fix("p1-e3", table=TableFix(grid=wide_grid(caption=None))),
    ]
    html = render(elements, fixes, {"p1-e2": png()})
    checks = html_checks.axe_checks(html)
    if checks == [html_checks.AXE_SKIPPED]:
        pytest.skip("Chromium for Playwright is not installed")
    assert checks == [html_checks.Check(name="axe", passed=True)]


MEASURE = """() => ({
  page: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
  tables: [...document.querySelectorAll('.table-scroll')].map((box) => ({
    id: box.id,
    overflows: box.scrollWidth > box.clientWidth + 1,
    focusable: box.tabIndex === 0,
  })),
})"""


@pytest.mark.browser
def test_at_320_px_a_wide_table_scrolls_in_its_focusable_box_and_the_page_does_not():
    sync_api = pytest.importorskip("playwright.sync_api")
    elements = [heading(), table_element("p1-e1", 1), table_element("p1-e2", 2)]
    fixes = [
        table_fix("p1-e1", table=TableFix(grid=grid())),
        table_fix("p1-e2", table=TableFix(grid=wide_grid())),
    ]
    html = render(elements, fixes)
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error:
            pytest.skip("Playwright's Chromium is not installed")
        try:
            page = browser.new_page(viewport={"width": 320, "height": 640})
            page.set_content(html)
            facts = page.evaluate(MEASURE)
            page.keyboard.press("Tab")
            focused = page.evaluate("() => document.activeElement.id")
        finally:
            browser.close()
    scroll_width, client_width = facts["page"]
    assert scroll_width <= client_width, facts["page"]
    assert facts["tables"] == [
        {"id": "p1-e1", "overflows": False, "focusable": False},
        {"id": "p1-e2", "overflows": True, "focusable": True},
    ]
    assert focused == "p1-e2"  # the only tab stop
