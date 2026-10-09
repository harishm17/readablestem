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
from a11y_stem.pipeline.assemble import STYLE, render_html
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


def test_a_rebuilt_table_is_a_named_scrollable_table():
    html = render([heading(), table_element()], [table_fix(table=TableFix(grid=grid()))])
    assert (
        '<div class="table-scroll" id="p1-e1" role="group" aria-labelledby="p1-e1-caption" '
        'tabindex="0"><table><caption id="p1-e1-caption">Table 1 Free fall.</caption>'
    ) in html
    assert '<th scope="col">Time (s)</th>' in html
    assert all(c.passed for c in static_checks(html)), static_checks(html)
    assert "<li>Tables (1): 1 accepted</li>" in html


def test_a_table_without_a_caption_is_named_table():
    html = render([table_element()], [table_fix(table=TableFix(grid=grid(caption=None)))])
    assert '<div class="table-scroll" id="p1-e1" role="group" aria-label="Table"' in html
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
    assert "1 item could not be converted" in html


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


def test_element_ids_that_are_not_plain_ids_still_render():
    assert assemble_tables.id_prefix("p1-e3") == "p1-e3"
    assert assemble_tables.id_prefix("3 tables!") == "t3-tables-"
    element = table_element(eid="weird id")
    html = render([element], [table_fix("weird id", table=TableFix(grid=grid()))])
    assert 'aria-labelledby="weird-id-caption"' in html and 'id="weird-id-caption"' in html


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


def test_text_seams_change_nothing_yet():
    element = Element(id="p1-e0", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 1, 1), order=0,
                      text="Plain text.", lang="fr")  # fmt: skip
    assert inline_text.passage_attrs(element) == ""
    assert '<p id="p1-e0">Plain text.</p>' in render([element], [])


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
        table_fix("p1-e3", table=TableFix(grid=grid(caption=None))),
    ]
    html = render(elements, fixes, {"p1-e2": png()})
    checks = html_checks.axe_checks(html)
    if checks == [html_checks.AXE_SKIPPED]:
        pytest.skip("Chromium for Playwright is not installed")
    assert checks == [html_checks.Check(name="axe", passed=True)]
