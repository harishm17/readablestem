"""List detection in layout.analyze: bullets, ordered markers, nesting, false lists.

A row that starts a list item becomes a `Kind.LIST_ITEM` element with `list_marker` (as
printed), `list_ordered` and `list_level`, and `text` without the marker. Items found in
the 2026-10-08 smoke test: multiple-choice options "a. ... b. ... c. ... d. ..." had been
merged into one paragraph, and "b. - 40 m/s" had become a display equation.
"""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.core.types import Element, Kind
from readablestem.pipeline.layout import analyze

BODY = 11.0
PITCH = 15.0


def write(page: pymupdf.Page, x: float, y: float, rows: list[str], **kw) -> float:
    size = kw.pop("fontsize", BODY)
    for row in rows:
        page.insert_text((x, y), row, fontsize=size, **kw)
        y += PITCH * size / BODY
    return y


def one_page(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def html_page(html: str) -> bytes:
    return one_page(lambda page: page.insert_htmlbox(pymupdf.Rect(72, 72, 540, 500), html))


def items(elements: list[Element]) -> list[tuple[str | None, bool | None, int | None, str]]:
    return [
        (e.list_marker, e.list_ordered, e.list_level, e.text or "")
        for e in elements
        if e.kind == Kind.LIST_ITEM
    ]


def kinds(elements: list[Element]) -> list[Kind]:
    return [e.kind for e in elements]


# ---------------------------------------------------------------- bullets


# ---------------------------------------------------------------- ordered markers


def test_openstax_spaced_full_stop_is_part_of_the_marker():
    def fill(page):
        page.insert_text((60, 100), "3", fontsize=BODY)
        page.insert_text((68, 100), ".", fontsize=BODY)
        page.insert_text(
            (76, 100), "In 1999 the orbiter crashed because of a unit error.", fontsize=BODY
        )

    assert items(analyze(one_page(fill)).elements) == [
        ("3.", True, 1, "In 1999 the orbiter crashed because of a unit error.")
    ]


# ---------------------------------------------------------------- nesting and indents


# ---------------------------------------------------------------- not lists


# ---------------------------------------------------------------- marker readings


# ---------------------------------------------------------------- not lists (review round 2)


@pytest.mark.parametrize("dash", ["-", "\u2013", "\u2014"])
def test_a_spaced_minus_before_a_number_is_not_a_dash_bullet(dash):
    """Two lines starting "- 40 m/s" and "- 20 m/s" matched the sibling rule: the sign
    went into the marker and the values were read out as positive."""
    html = (
        "<p>The velocities of the two carts after the collision are:<br>"
        f"{dash} 40 m/s<br>{dash} 20 m/s<br>so both carts move to the left after they collide.</p>"
    )
    elements = analyze(html_page(html)).elements
    assert not any(e.kind == Kind.LIST_ITEM for e in elements)
    text = " ".join(e.text or "" for e in elements)
    assert f"{dash} 40 m/s" in text and f"{dash} 20 m/s" in text
