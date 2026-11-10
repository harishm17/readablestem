"""Inline maths in a figure's caption (docs/eval-realworld.md: "figure (caption or panel)").

A caption was kept as one plain string, so `$K(t)$` in "Figure 3: the capacity K(t) of ..." was
never an equation: 177 of the real-world development set's gold equations are typed in a figure
caption and ReadableSTEM found 16%. The caption is cut like a paragraph: `Element.caption`
stays plain text, `caption_marked` has a marker where each formula was, and each formula is an
inline equation whose parent is the figure.
"""

from __future__ import annotations

import pymupdf

from readablestem.core.types import (
    Kind,
    inline_marker,
)
from readablestem.pipeline.layout import analyze

BODY = 11.0


def figure_page(*caption: tuple[str, str]) -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"producer": "pdfTeX-1.40.25"})
    page = doc.new_page(width=612, height=792)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 180), False)
    pix.set_rect(pix.irect, (200, 200, 230))
    page.insert_image(pymupdf.Rect(150, 100, 450, 280), pixmap=pix)
    x = 120
    for text, font in caption:
        page.insert_text((x, 300), text, fontsize=9, fontname=font)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=9)
    page.insert_text((72, 340), "Body text of the paper continues below the figure.", fontsize=BODY)
    return doc.tobytes()


def test_a_caption_with_a_variable_has_an_inline_equation_whose_parent_is_the_figure():
    elements = analyze(
        figure_page(
            ("Figure 3: Growth of the carrying capacity ", "helv"),
            ("K", "heit"),
            ("(", "helv"),
            ("t", "heit"),
            (") over the whole experiment.", "helv"),
        )
    ).elements
    figure = next(e for e in elements if e.kind is Kind.FIGURE)
    children = [e for e in elements if e.kind is Kind.EQUATION and e.parent_id == figure.id]
    assert [c.text.replace(" ", "") for c in children if c.text] == ["K(t)"]
    assert (
        figure.caption
        == "Figure 3: Growth of the carrying capacity K(t) over the whole experiment."
    )
    assert figure.caption_marked is not None
    assert inline_marker(children[0].id) in figure.caption_marked
    assert children[0].id.startswith(figure.id.split("-")[0])
