"""assemble: TABLE elements (pipeline.assemble_tables) and the inline-text seam.

A rebuilt table is written as a scrollable, named <table>; anything else keeps the image
of the table, flagged like an equation that was not converted. Documents without tables
are written exactly as before (no table CSS either).
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import (
    Element,
    Fix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from readablestem.pipeline import html_checks
from readablestem.pipeline.assemble import FLAGGED_NOTE, render_html
from readablestem.pipeline.assemble_tables import TABLE_CSS, TABLE_NOT_CONVERTED_ALT, TABLE_NOTE
from readablestem.pipeline.html_checks import static_checks


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


def test_a_table_waiting_for_review_is_shown_as_rebuilt():
    fix = table_fix(status=Status.NEEDS_REVIEW, table=TableFix(grid=grid()))
    html = render([table_element()], [fix])
    assert "<table>" in html and TABLE_NOT_CONVERTED_ALT not in html


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
            # Not a table of data, but its grid holds maths its text gives flat: written
            # out it would read as something else, so the image stays.
            table_fix(
                table=TableFix(
                    grid=TableGrid(
                        rows=1,
                        cols=2,
                        is_data=False,
                        source="local",
                        cells=[
                            TableCell(row=0, col=0, text="K = \u00b92mv\u00b2"),
                            TableCell(row=0, col=1, text="energy"),
                        ],
                    )
                )
            ),
            "flattened_maths",
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


def text_fix(rows: list[list[str]], caption: str | None = None) -> Fix:
    cells = [
        TableCell(row=r, col=c, text=t) for r, row in enumerate(rows) for c, t in enumerate(row)
    ]
    grid = TableGrid(
        rows=len(rows), cols=len(rows[0]), cells=cells, is_data=False, source="local",
        caption=caption,
    )  # fmt: skip
    return table_fix(table=TableFix(grid=grid), review="optional")


def test_a_grid_of_terms_that_is_not_data_is_written_as_a_list():
    fix = text_fix(
        [["accuracy", "ampere", "constant"], ["kilogram", "method of adding percents", ""]]
    )
    html = render([table_element(caption="Table 1 Free fall.")], [fix], {"p1-e1": png()})
    assert (
        '<div id="p1-e1"><p>Table 1 Free fall.</p><ul><li>accuracy</li><li>ampere</li>'
        "<li>constant</li><li>kilogram</li><li>method of adding percents</li></ul></div>"
    ) in html
    assert "<table" not in html and TABLE_NOT_CONVERTED_ALT not in html
    assert TABLE_CSS not in html and 'class="flagged"' not in html
    assert all(c.passed for c in static_checks(html))


def test_other_text_grids_are_one_paragraph_per_cell_and_escaped():
    fix = text_fix([["Name: <b>", "Date:", "Signature of the student who did it"]])
    element = table_element().model_copy(update={"caption": None})
    html = render([element], [fix], {"p1-e1": png()})
    assert (
        '<div id="p1-e1"><p>Name: &lt;b&gt;</p><p>Date:</p>'
        "<p>Signature of the student who did it</p></div>"
    ) in html


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
            # only maths: Chromium gives the header no name, so screen readers announce
            # none while moving through its column
            '<table><tr><th scope="col"><math display="inline"><mi>x</mi></math></th></tr>'
            "<tr><td>1</td></tr></table>",
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


# --------------------------------------------------------------------------- shared seams


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
            page.keyboard.press("Tab")  # the skip link is the first tab stop
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


@pytest.mark.parametrize(
    ("text", "flat"),
    [
        ("K = \u00b92mv\u00b2", True),  # a stacked 1/2 (raised 1, baseline 2)
        ("p = mv\u20d7", True),  # a vector arrow as a combining mark
        ("x\u0302", True),  # a hat on one letter
        ("e^(i\u03c9t)", True),  # a raised run written out
        ("g (m/s\u00b2)", False),  # a superscript on its own reads fine
        ("1.01\u00d710\u2075", False),
        ("M\u00fcller", False),  # an accented word, precomposed ...
        ("Mu\u00a8ller", False),  # ... or as an OT1 font draws it: text, not maths
        ("Ethanol", False),
    ],
)
def test_flattened_maths_in_the_grid_read_from_the_pdf(text, flat):
    from readablestem.pipeline.assemble_tables import flattened_maths

    assert flattened_maths(TableCell(row=0, col=0, text=text)) is flat
