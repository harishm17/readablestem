"""From blocks to elements: the pieces of a block turned into `Element`s (paragraphs, headings,
lists, tables, figures, equations) with the contents and footnote links between them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from itertools import count

from readablestem.core.types import Element, Kind, Link, Run, inline_marker
from readablestem.pipeline import script_marks
from readablestem.pipeline.layout.blocks import (
    _TEXT_BLOCKS,
    CONTENTS_ENTRY,
    TEXT_DECISIONS,
    _append_row,
    _tidy,
)
from readablestem.pipeline.layout.context import _Document, _PageGuard
from readablestem.pipeline.layout.glyphs import _rounded
from readablestem.pipeline.layout.lists import _list_levels
from readablestem.pipeline.layout.notices import _FootnoteMark
from readablestem.pipeline.layout.shared import _Block, _Context, _Inline, _PageBlocks

# On a paragraph (or list item): its text goes on over a page break and the element holds
# both parts, with the page and box of the first (`_join_page_splits`).
REASON_JOINED_PAGES = "joined across pages"


def _title_key(text: str) -> str:
    """A title for matching a contents entry with its heading: letters and digits of the
    words, case-folded, without the section number in front ("1.1 Drop times")."""
    words = re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", text).casefold()).split()
    while (
        words and re.fullmatch(r"[\d.]+|[ivxlc]+|chapter|section|part", words[0]) and len(words) > 1
    ):
        words.pop(0)
    return " ".join(words)


_NUMBER_TAIL = re.compile(r" (\d{1,4}|[ivx]{1,6})$")

# The number a heading or a contents entry starts with, after its label if it has one:
# "3.2 Title", "Section 3.2: Title", "Chapter 4", "Appendix A", "B.2 Title", "Part IV", "II. Title".
# A letter is a number only after "Appendix" or with a number after it ("B.2"); a roman numeral
# only after a label or with the point that follows it ("II.").
_HEADING_NUMBER = re.compile(
    r"\s*(?:(?P<label>chapter|section|sec\.|\u00a7|part|appendix)\s*)?"
    r"(?P<num>\d+(?:\.\d+)*|[a-z](?:\.\d+)*|[ivxlc]+)\.?(?=\s|$|[:\u2013\u2014])",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"\d+(?:\.\d+)*")
_LETTER = re.compile(r"[a-z]\.\d+(?:\.\d+)*")


def heading_number(text: str) -> tuple[str, str] | None:
    """The label ("chapter", "section", "part", "appendix" or "") and the number (lower case:
    "3.2", "a.1", "iv") that `text` starts with, or None. The one reading of "the number in
    front" for a heading and for the contents entry that names it (`_contents_links`) and for
    the references that name it (`text_pass`)."""
    shown = unicodedata.normalize("NFKC", text).lstrip()
    match = _HEADING_NUMBER.match(shown)
    if match is None:
        return None
    label = (match.group("label") or "").lower()
    label = "section" if label in ("sec.", "\u00a7") else label
    num = match.group("num").lower()
    if _DIGITS.fullmatch(num) or _LETTER.fullmatch(num):
        return label, num
    if len(num) == 1 and num.isalpha() and label == "appendix":
        return label, num
    if label and re.fullmatch(r"[ivxlc]+", num):
        return label, num
    if re.fullmatch(r"[ivx]+", num) and shown[len(num) : len(num) + 1] == ".":
        return label, num
    return None


def _roman(number: str) -> bool:
    """`number` (as `heading_number` gives it) is written in roman numerals."""
    return re.fullmatch(r"[ivxlc]+", number) is not None


def _contents_links(elements: list[Element]) -> list[Element]:
    """Each entry of a table of contents links to the heading it names, when the PDF's own
    links do not already say so (`text_pass` keeps those): a heading later in the document
    whose title is the entry's, once the section number is left out of both. When the entry and
    the heading both print a number ("2.1", "Appendix A"), it must be the same one: "3.1 The
    model" is not "2.1 The model", and of two headings with one title the number picks one.
    A section number and an outline's roman numeral are not compared ("4.1 Scope" in the
    contents of a file whose headings are "I. Scope", "II. ..."): there the title decides.
    An entry that names no heading, or more than one, is plain text."""
    headings: dict[str, list[Element]] = {}
    for e in elements:
        if e.kind == Kind.HEADING and e.text:
            headings.setdefault(_title_key(e.text), []).append(e)
    out = []
    for e in elements:
        if CONTENTS_ENTRY in e.reasons and e.text and not e.links:
            key = _title_key(e.text)
            # An entry with no leaders keeps its page number as its last word.
            candidates = headings.get(key) or headings.get(_NUMBER_TAIL.sub("", key), [])
            printed = heading_number(e.text)
            found = [
                h
                for h in candidates
                if h.order > e.order
                and (
                    printed is None
                    or not (there := heading_number(h.text or ""))
                    or there[1] == printed[1]
                    or _roman(there[1]) != _roman(printed[1])
                )
            ]
            if len(found) == 1:
                link = Link(start=0, end=len(e.text), kind="internal", target_id=found[0].id)
                e = e.model_copy(update={"links": [link]})
        out.append(e)
    return out


def _mark_spots(text: str, found: _FootnoteMark) -> list[int]:
    """Where in `text` the mark stands, by what is printed just before it. An inline equation
    stands in the text as a placeholder, so the glyphs it took from the line are not there:
    the context is cut from its left until it is found, down to the placeholder itself. A cut
    context that ends a number ("0.1") is no place for a mark."""
    before = found.before
    contexts = [before[start:] for start in range(len(before)) if before[start:].strip()]
    for k, context in enumerate([*contexts, "\u27e7"]):
        spots = [
            m.end() - len(found.mark)
            for needle in (context + found.mark, context + " " + found.mark)
            for m in re.finditer(re.escape(needle), text)
        ]
        if spots and k and any(text[at - 2 : at].strip(".,").isdigit() for at in spots):
            return []
        if spots:
            return spots
    return []


def _footnote_links(elements: list[Element], marks: dict[str, _FootnoteMark]) -> list[Element]:
    """The elements with an internal link from each footnote's mark in the text to the
    footnote. The mark is found again in the element's text by what is printed just before
    it; a mark that cannot be found once, exactly, gets no link."""
    by_id = {e.id: e for e in elements}
    added: dict[str, list[Link]] = {}
    for footnote, found in marks.items():
        owner = by_id.get(found.block_id)
        if owner is None or owner.text is None or not found.before.strip():
            continue
        spots = _mark_spots(owner.text, found)
        if len(spots) != 1:
            continue
        start = spots[0]
        added.setdefault(owner.id, []).append(
            Link(start=start, end=start + len(found.mark), kind="internal", target_id=footnote)
        )
    return [
        e.model_copy(update={"links": sorted([*e.links, *added[e.id]], key=lambda k: k.start)})
        if e.id in added
        else e
        for e in elements
    ]


def _whole_pieces(block: _Block, *, document: _Document) -> list[str | _Inline]:
    """The pieces of a paragraph with the parts that follow it over page breaks joined on
    (hyphenation repaired as between lines); an inline equation of a later part keeps its
    own page."""
    if not block.parts:
        return block.pieces
    pieces = list(block.pieces)
    for number, part in block.parts:
        later = [
            replace(p, page=number) if isinstance(p, _Inline) and p.page is None else p
            for p in part.pieces
        ]
        _append_row(pieces, later, document=document)
    if block.term_pieces is None:
        return _tidy(pieces)
    kept = block.term_pieces + 1  # a glossary entry: its term and the space after it stay
    return [*pieces[:kept], *_tidy(pieces[kept:])]


def _to_elements(
    pages: list[_PageBlocks],
    ctx: _Context,
    block_ids: dict[int, str],
    guard: _PageGuard,
    *,
    document: _Document,
) -> list[Element]:
    """Blocks -> Elements with ids "p{page+1}-e{index}" and a global reading order
    (`block_ids` gets each block's element id).

    A page whose blocks cannot be written (`guard`) gives no element: what it wrote so far
    is taken back, and the later part of a paragraph it was to carry over from the next page
    is written there instead (`_join_page_splits`)."""
    elements: list[Element] = []
    levels = _list_levels((b for page in pages for b in page.blocks), 0.6 * ctx.body)
    for page in pages:
        first = len(elements)
        with guard(page.number):
            _page_elements(page, ctx, levels, block_ids, elements, document=document)
        if not guard.ok(page.number):
            del elements[first:]
            for block in page.blocks:
                block_ids.pop(id(block), None)
                for _, part in block.parts:
                    block_ids.pop(id(part), None)
                    part.joined = False
    return elements


def _page_elements(
    page: _PageBlocks,
    ctx: _Context,
    levels: dict[int, int],
    block_ids: dict[int, str],
    elements: list[Element],
    *,
    document: _Document,
) -> None:
    """The elements of one page's blocks, appended to `elements`."""
    ids = (f"p{page.number + 1}-e{index}" for index in count())
    for block in page.blocks:
        if block.joined:  # written with the paragraph it goes on from (`_join_page_splits`)
            continue
        element_id = next(ids)
        block_ids[id(block)] = element_id
        block_ids.update({id(part): element_id for _, part in block.parts})
        if block.kind in _TEXT_BLOCKS:
            children = []
            parts = []
            term_length = 0
            for number, piece in enumerate(_whole_pieces(block, document=document), start=1):
                if isinstance(piece, _Inline):
                    child_id = next(ids)
                    children.append((child_id, piece))
                    parts.append(inline_marker(child_id))
                else:
                    parts.append(piece)
                if number == block.term_pieces:
                    # offsets are into the text without the script marks
                    term_length = len(script_marks.strip_marks("".join(parts)))
            joined = "".join(parts)
            plain = script_marks.strip_marks(joined)
            line_ends: list[int] = []
            if block.code:  # indentation is kept; code carries no raised or lowered runs
                text, script_runs = plain.strip("\n"), []
            else:
                text, script_runs, line_ends = script_marks.split_lines(joined)
            term_end = None
            if block.term_pieces is not None:
                term_end = max(0, term_length - (len(plain) - len(plain.lstrip())))
            marker = block.marker
            elements.append(
                Element(
                    id=element_id,
                    kind=Kind(block.kind),
                    page=page.number,
                    bbox=_rounded(block.bbox),
                    order=len(elements),
                    text=text,
                    font_size=block.font_size,
                    bold=block.bold,
                    preformatted=block.code,
                    term_end=term_end,
                    list_marker=marker.text if marker else None,
                    list_ordered=marker.ordered if marker else None,
                    list_level=block.contents_level or (levels.get(id(block)) if marker else None),
                    reasons=[
                        *([CONTENTS_ENTRY] if block.contents_level else []),
                        *(
                            [REASON_JOINED_PAGES]
                            if block.parts and not block.contents_level
                            else []
                        ),
                        *(r for r in block.reasons if r in TEXT_DECISIONS),
                    ],
                    script_runs=script_runs,
                    runs=[Run(start=at, end=at + 1, kind="break") for at in line_ends],
                    last_page=block.parts[-1][0] if block.parts else None,
                )
            )
            for child_id, inline in children:
                where = page.number if inline.page is None else inline.page
                elements.append(_inline_element(child_id, inline, where, element_id, elements))
            continue
        kind = Kind(block.kind)
        heading_text, heading_runs = (
            script_marks.split(block.marked) if block.marked else (None, [])
        )
        caption_children: list[tuple[str, _Inline]] = []
        caption_marked = None
        if kind is Kind.FIGURE and block.caption_pieces:
            parts = []
            for piece in block.caption_pieces:
                if isinstance(piece, _Inline):
                    child_id = next(ids)
                    caption_children.append((child_id, piece))
                    parts.append(inline_marker(child_id))
                else:
                    parts.append(piece)
            caption_marked = " ".join(script_marks.strip_marks("".join(parts)).split())
        table = block.table if kind is Kind.TABLE else None
        if table is not None and block.cell_inlines:
            # A cell that is one formula is an inline equation of the table: its id goes
            # on the cell, and the equation step reads the cell's crop.
            cell_ids: dict[tuple[int, int], str] = {}
            for key, inline in sorted(block.cell_inlines.items()):
                child_id = next(ids)
                caption_children.append((child_id, inline))
                cell_ids[key] = child_id
            table = table.model_copy(
                update={
                    "cells": [
                        c.model_copy(update={"equation_id": cell_ids[(c.row, c.col)]})
                        if (c.row, c.col) in cell_ids
                        else c
                        for c in table.cells
                    ]
                }
            )
        elements.append(
            Element(
                id=element_id,
                kind=kind,
                page=page.number,
                bbox=_rounded(block.bbox),
                order=len(elements),
                script_runs=heading_runs if heading_text == block.text else [],
                text=block.text,
                font_size=block.font_size,
                bold=block.bold,
                score=block.score,
                reasons=block.reasons,
                display="block" if kind == Kind.EQUATION else None,
                equation_label=block.label if kind == Kind.EQUATION else None,
                caption=block.caption,
                caption_marked=caption_marked,
                regular_letters=block.regular if kind == Kind.EQUATION else None,
                page_latex=block.page_latex if kind == Kind.EQUATION else None,
                page_status=block.page_status if kind == Kind.EQUATION else None,
                table=table,
            )
        )
        for child_id, inline in caption_children:
            elements.append(_inline_element(child_id, inline, page.number, element_id, elements))


def _inline_element(
    child_id: str, inline: _Inline, page: int, parent_id: str, elements: list[Element]
) -> Element:
    """The equation element of an inline formula of `parent_id` (a paragraph or a caption)."""
    return Element(
        id=child_id,
        kind=Kind.EQUATION,
        page=page,
        bbox=_rounded(inline.bbox),
        order=len(elements),
        text=inline.text or None,  # None: a picture, no text layer
        font_size=inline.font_size,
        score=inline.score,
        reasons=inline.reasons,
        display="inline",
        parent_id=parent_id,
        regular_letters=inline.regular,
        page_latex=inline.page_latex,
        page_status=inline.page_status,
    )
