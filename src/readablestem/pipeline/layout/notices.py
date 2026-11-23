"""Notices of what the layout left out or could not read: ink it did not analyze, hidden text and
footnote marks.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

import pymupdf

from readablestem.core.page_checks import make
from readablestem.core.types import Notice
from readablestem.pipeline import page_type
from readablestem.pipeline.layout.blocks import _TEXT_BLOCKS
from readablestem.pipeline.layout.figures import CODE_MIN_GLYPHS
from readablestem.pipeline.layout.glyphs import _Char, _clean
from readablestem.pipeline.layout.page import (
    _FOOTNOTE_MARKS,
    FOOTNOTE_SIZE,
    FOOTNOTE_ZONE,
    _after_unit,
    _covers,
    _lead_mark,
    _mark_chars,
    _mark_groups,
    _range_marks,
)
from readablestem.pipeline.layout.shared import (
    CODE_MONO_SHARE,
    _Block,
    _Context,
    _PageBlocks,
    _Row,
)

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


def _actualtext_notices(
    doc: pymupdf.Document, types: dict[int, page_type.PageTypeResult], kept: dict[int, str]
) -> list[Notice]:
    """A typed page that carries ActualText and was read without it, because the two readings
    differ in more than spaces and hyphens and the plain one is not the worse
    (`page_type.actualtext_flag`): "Worth a look", per page. The words the file says it means
    are not in the text, and the printed ones are."""
    return [
        make("actualtext_ignored", page=number)
        for number in sorted(types)
        if number not in kept and page_type.actualtext_flag(doc[number])
    ]


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
    linked = (
        {r.name for refs in named.references.values() for r in refs} if named is not None else set()
    )
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
                # no link of the file points at this note: its mark is the raised one above,
                # on this page, that the note opens with
                lead = _lead_token(block.rows[0])
                if (
                    marks_out is not None
                    and lead in raised
                    and not any(name in linked for name in block.rows[0].note)
                ):
                    marks_out[element_id] = raised[lead]
            elif block.code or (
                len(ink) >= CODE_MIN_GLYPHS
                and sum(c.mono for c in ink) >= CODE_MONO_SHARE * len(ink)
            ):
                notices.append(make("code_block", page=page.number, element_id=element_id))
            elif named is None and ink and _footnote(block, ink, page, ctx, raised.keys()):
                notices.append(make("footnote", page=page.number, element_id=element_id))
                if marks_out is not None:
                    marks_out[element_id] = raised[_lead_token(block.rows[0])]
            for row in block.rows:
                i = 0
                while i < len(row.chars):
                    if not (row.is_script(row.chars[i]) and row.chars[i].c.strip()):
                        i += 1
                        continue
                    j = i  # a mark of several glyphs ("12") is one run of raised glyphs
                    while (
                        j < len(row.chars)
                        and row.is_script(row.chars[j])
                        and row.chars[j].c.strip()
                    ):
                        j += 1
                    for a, b in _mark_groups(row, i, j):  # "1,2" is two marks, "1)" is "1"
                        if row.chars[a].c.isdigit() and _after_unit(row, a):
                            continue  # the exponent of a unit, or of a math letter
                        token = "".join(c.c for c in row.chars[a:b])
                        before = _clean("".join(x.c for x in row.chars[:a]))[-FOOTNOTE_CONTEXT:]
                        raised[token] = _FootnoteMark(element_id, before, token)
                        # a raised range "1 to 3" marks notes 1, 2 and 3
                        ends, between = _range_marks(row, a, b)
                        for x, y in ends:
                            end = "".join(c.c for c in row.chars[x:y])
                            near = _clean("".join(c.c for c in row.chars[:x]))[-FOOTNOTE_CONTEXT:]
                            raised[end] = _FootnoteMark(element_id, near, end)
                        for number in between:
                            raised.setdefault(number, _FootnoteMark(element_id, before, number))
                    i = j
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


def _lead_token(row: _Row) -> str:
    """The mark a block opens with, read from its first line: the raised digits or footnote
    signs it starts with ("1" of a raised 1 before "2019 was the year"), or without a raised
    start its first glyphs up to the first blank or glyph that is no digit or footnote sign
    ("1" of "1 10 mL of water")."""
    if end := _lead_mark(row, named=False):
        return "".join(c.c for c in row.chars[:end] if not c.blank)
    token = ""
    for c in row.chars:
        if not token and c.blank:
            continue
        if c.blank or not (c.c.isdigit() or c.c in _FOOTNOTE_MARKS):
            break
        token += c.c
    return token


def _footnote(
    block: _Block,
    ink: Sequence[_Char],
    page: _PageBlocks,
    ctx: _Context,
    raised: Collection[str],
) -> bool:
    """A block of smaller type low on the page that opens with a mark printed raised in the
    text above it, and goes on in words: a lone number, a row of axis labels and a running
    footer ("12 Authors") are not notes."""
    small = block.font_size is not None and block.font_size < FOOTNOTE_SIZE * ctx.body
    low = block.bbox[1] >= FOOTNOTE_ZONE * page.size[1]
    mark = _lead_token(block.rows[0]) if block.rows else ""
    return (
        small
        and low
        and bool(mark)
        and mark in raised
        and any(c.c.isalpha() for c in ink[len(mark) :])
    )
