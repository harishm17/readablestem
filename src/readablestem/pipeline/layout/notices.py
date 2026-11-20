"""Notices of what the layout left out or could not read: ink it did not analyze, hidden text and
footnote marks.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from readablestem.core.page_checks import make
from readablestem.core.types import Notice
from readablestem.pipeline import page_type
from readablestem.pipeline.layout.blocks import _TEXT_BLOCKS
from readablestem.pipeline.layout.figures import CODE_MIN_GLYPHS
from readablestem.pipeline.layout.glyphs import _clean
from readablestem.pipeline.layout.page import (
    _FOOTNOTE_MARKS,
    FOOTNOTE_SIZE,
    FOOTNOTE_ZONE,
    _covers,
    _mark_chars,
)
from readablestem.pipeline.layout.shared import CODE_MONO_SHARE, _Block, _Context, _PageBlocks

NOT_ANALYSED = "drawings_not_analysed"
TABLES_NOT_SEARCHED = "tables_not_searched"


def _ink_notices(types: dict[int, page_type.PageTypeResult], kept: dict[int, str]) -> list[Notice]:
    """A page read for its text whose drawings were not analysed (`page_type.InkBudget`: a
    count of work ran out, or the clock guard fired) may hold handwriting nobody looked for:
    "Worth a look". A page kept whole as its image has a notice and a decision of its own."""
    return [
        make(NOT_ANALYSED, page=number)
        for number, kind in sorted(types.items())
        if not kind.ink.analysed and number not in kept
    ]


def _not_searched_notices(pages: list[int]) -> list[Notice]:
    """A page whose table search the document's time for it refused
    (`tables.DetectorBudget`) may have a table read as text: "Worth a look", and it holds
    the job back from "Ready" (`web.states`)."""
    return [make(TABLES_NOT_SEARCHED, page=number) for number in pages]


def _hidden_text_notices(
    types: dict[int, page_type.PageTypeResult], kept: dict[int, str]
) -> list[Notice]:
    """Hidden text left out, per page, as counts (docs/layout.md "Hidden text and page
    types"): on a page kept as an image, the tablet's or OCR program's guesses that were
    not used (`ocr_text_layer_ignored`, review "none"); on a typed page, invisible or
    covered words that were left out (`hidden_text_ignored`, "optional": if students need
    them, the instructor adds them; `hidden_text_check`, "required", from a paragraph on)."""
    notices = []
    for number, kind in sorted(types.items()):
        hidden = kind.text.left_out
        if hidden < page_type.HIDDEN_FEW:
            continue
        if number in kept:
            notices.append(make("ocr_text_layer_ignored", page=number, count=hidden))
        else:
            big = hidden >= page_type.HIDDEN_BLOCK
            code = "hidden_text_check" if big else "hidden_text_ignored"
            notices.append(make(code, page=number, count=hidden))
    return notices


def _turned_notices(types: dict[int, page_type.PageTypeResult]) -> list[Notice]:
    """A page saved sideways with no /Rotate was turned to be read (`page_type.turn_page`): what
    the pictures of the page show is the turned page. Information, counted per page."""
    return [make("page_turned", page=number) for number, kind in sorted(types.items()) if kind.turn]


FOOTNOTE_CONTEXT = 10  # characters printed before a footnote's mark that find it again


@dataclass(frozen=True)
class _FootnoteMark:
    """A footnote's mark in the text above it: the block (by its element id) and what is
    printed just before the mark, to find it again in the element's text."""

    block_id: str
    before: str
    mark: str


def _layout_notices(
    pages: list[_PageBlocks],
    ctx: _Context,
    block_ids: dict[int, str],
    marks_out: dict[str, _FootnoteMark] | None = None,
) -> list[Notice]:
    """The code blocks and footnotes the job page lists under "Not handled in this file"
    (review "none": no decision). `marks_out`
    gets each footnote's element id with the place of its mark (`_FootnoteMark`).

    A file that names its footnotes (`ctx.notes`) is read from the file: the block that
    starts at a destination is a footnote, the glyphs of a link to it are its mark, and no
    other block is a footnote. Otherwise a footnote is a block of smaller type low on the
    page that starts with a mark printed raised in the text above it."""
    notices: list[Notice] = []
    named = ctx.notes
    started: dict[str, str] = {}  # destination name -> the footnote's element id
    marks: dict[str, _FootnoteMark] = {}  # destination name -> the link's glyphs in the text
    for page in pages:
        raised: dict[str, _FootnoteMark] = {}  # marks printed raised above, on this page
        refs = named.references.get(page.number, []) if named is not None else []
        for block in page.blocks:
            element_id = block_ids.get(id(block))
            if element_id is not None and block.flat_scripts:
                notices.append(make("scripts_flattened", page=page.number, element_id=element_id))
            if block.kind not in _TEXT_BLOCKS or element_id is None:
                continue
            ink = [c for row in block.rows for c in row.chars if not c.blank]
            # a list item made from a line or grid of terms has text and no rows
            if named is not None and block.rows and block.rows[0].note:
                notices.append(make("footnote", page=page.number, element_id=element_id))
                started.update(dict.fromkeys(block.rows[0].note, element_id))
            elif block.code or (
                len(ink) >= CODE_MIN_GLYPHS
                and sum(c.mono for c in ink) >= CODE_MONO_SHARE * len(ink)
            ):
                notices.append(make("code_block", page=page.number, element_id=element_id))
            elif named is None and ink and _footnote(block, ink[0].c, page, ctx, raised.keys()):
                notices.append(make("footnote", page=page.number, element_id=element_id))
                if marks_out is not None:
                    marks_out[element_id] = raised[ink[0].c]
            for row in block.rows:
                for i, c in enumerate(row.chars):
                    if row.is_script(c) and c.c.strip():
                        before = _clean("".join(x.c for x in row.chars[:i]))[-FOOTNOTE_CONTEXT:]
                        raised[c.c] = _FootnoteMark(element_id, before, c.c)
                if row.note:
                    continue
                for ref in refs:
                    hits = _mark_chars(row, ref.box)
                    if hits and ref.name not in marks and _covers(row, ref.box):
                        before = _clean("".join(x.c for x in row.chars[: hits[0]]))
                        marks[ref.name] = _FootnoteMark(
                            element_id,
                            before[-FOOTNOTE_CONTEXT:],
                            "".join(row.chars[i].c for i in hits),
                        )
    if marks_out is not None:
        for name, mark in marks.items():
            if name in started:
                marks_out.setdefault(started[name], mark)
    return notices


def _footnote(
    block: _Block, first: str, page: _PageBlocks, ctx: _Context, raised: Collection[str]
) -> bool:
    small = block.font_size is not None and block.font_size < FOOTNOTE_SIZE * ctx.body
    low = block.bbox[1] >= FOOTNOTE_ZONE * page.size[1]
    mark = first.isdigit() or first in _FOOTNOTE_MARKS
    return small and low and mark and first in raised
