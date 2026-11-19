"""Bold-letter runs and entries: lead entries, glossary entries and the settling of bold letters
over a page's blocks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import pairwise

from readablestem.core.mathchars import base_char
from readablestem.core.types import BBox, Kind
from readablestem.pipeline.layout.blocks import (
    _TEXT_BLOCKS,
    LEAD_ENTRIES,
    REASON_BOLD_BODY,
    REASON_LARGER_FONT,
    REASON_LETTER,
    REASON_NUMBERED,
    _bold_key,
    _paragraph_pieces,
    _tidy,
)
from readablestem.pipeline.layout.context import _Document
from readablestem.pipeline.layout.glyphs import _clean, _union
from readablestem.pipeline.layout.page import _has_content
from readablestem.pipeline.layout.shared import (
    MAX_TERM_WORDS,
    _Block,
    _Inline,
    _lead_end,
    _PageBlocks,
    _Row,
)
from readablestem.pipeline.notation_memory import (
    BOLD_LETTER_REASON,
    EVIDENCE_DOCUMENT,
    EVIDENCE_NEARBY,
)

GLOSSARY_HEADING = re.compile(
    r"\b(?:key\s+terms?|glossary|vocabulary|terms\s+and\s+definitions)\s*:?\s*$", re.IGNORECASE
)


GLOSSARY_INDENT = 1.2  # x font size: a definition starts this much further right than its term
GLOSSARY_TERM_ROWS = 2  # lines a term may take
GLOSSARY_GAP = 1.6  # x font size: most space between a term line and its definition


def _term_words(text: str) -> bool:
    """Reads like a term: a few words, not ending like a sentence."""
    words = text.split()
    return 0 < len(words) <= MAX_TERM_WORDS and text.rstrip()[-1:] not in ".?!;,"


def _entry_rows(block: _Block) -> int | None:
    """In a paragraph that is a term and its definition (the term at the margin, the
    definition on the lines indented below it: how OpenStax sets "Key Terms"), the number
    of lines the term takes; None for any other paragraph."""
    rows = block.rows
    if block.kind != Kind.PARAGRAPH or block.code or block.marker is not None or len(rows) < 2:
        return None
    margin, size = rows[0].x0, rows[0].size
    taken = 0
    while taken < min(len(rows), GLOSSARY_TERM_ROWS + 1) and rows[taken].x0 <= margin + 0.5 * size:
        taken += 1
    if not 1 <= taken <= GLOSSARY_TERM_ROWS or taken >= len(rows):
        return None
    if any(r.x0 < margin + GLOSSARY_INDENT * size for r in rows[taken:]):
        return None
    if not _term_words(_clean(" ".join(r.text for r in rows[:taken]))):
        return None
    return taken


def _bold_lead(row: _Row) -> int:
    """Where a line's bold lead ends (an index into `row.chars`; 0 if it has none): the
    line starts in bold and goes on in regular type, "Limit The value that ...". A line
    that is bold throughout has no lead."""
    chars = row.chars
    first = next((i for i, c in enumerate(chars) if not c.blank), None)
    if first is None or not chars[first].bold:
        return 0
    end = first
    for i in range(first, len(chars)):
        if chars[i].blank:
            continue
        if not chars[i].bold:
            return end if end > first or chars[first].c.strip() else 0
        end = i + 1
    return 0


def _run_in_entries(block: _Block, *, document: _Document) -> list[_Block] | None:
    """A paragraph whose lines start entries of a glossary, each with a bold term that runs
    into its definition ("Derivative The limit of ..."), as one block per entry; None when
    the paragraph is not that. An entry starts at a line with a bold lead of at most
    MAX_TERM_WORDS words, after a line that ends a sentence; every entry needs a
    definition."""
    rows = block.rows
    if block.kind != Kind.PARAGRAPH or block.code or block.marker is not None:
        return None
    starts = []
    for i, row in enumerate(rows):
        lead = _bold_lead(row)
        if not lead or not _term_words(_clean("".join(c.c for c in row.chars[:lead]))):
            continue
        if i == 0 or rows[i - 1].clean[-1:] in ".?!)":
            starts.append(i)
    if not starts or starts[0] != 0:
        return None
    entries = []
    for number, first in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(rows)
        group = rows[first:end]
        entry = _lead_entry(group, _bold_lead(group[0]), block.bbox, document=document)
        if entry is None:
            return None
        entries.append(entry)
    return entries


def _lead_entry(group: list[_Row], lead: int, box: BBox, *, document: _Document) -> _Block | None:
    """The lines of one entry, whose first line has its term in its first `lead` characters, as
    an entry block (`_entry`); None when the entry has no definition."""
    term = _Block(Kind.PARAGRAPH, box, [_Row(group[0].chars[:lead])])
    rest = [_Row(group[0].chars[lead:], column=group[0].column), *group[1:]]
    definition = _paragraph_pieces(_Block(Kind.PARAGRAPH, box, rest), document=document)
    if not _has_content(definition):
        return None
    return _entry(
        _paragraph_pieces(term, document=document),
        definition,
        _union(r.bbox for r in group),
        group,
        group[0].size,
    )


def _lead_entries(pages: list[_PageBlocks], *, document: _Document) -> None:
    """Three or more paragraphs in a row that each start with a lead term (`_lead_end`), the
    terms in alphabetical order (`_listed_terms`), are the entries of a glossary: each is
    written as a term and its definition (in place; `Element.term_end`), which the assembler
    makes one definition list. The letter headings of an index between them do not end the
    row; anything else does. Fewer than three, or leads in any other order (a theorem and its
    proof, the cases of a proof, the speakers of a dialogue), are paragraphs that begin with
    an emphasised phrase, and stay paragraphs."""
    row: list[tuple[_Block, int]] = []

    def settle() -> None:
        entries = [(b, _lead_entry(b.rows, lead, b.bbox, document=document)) for b, lead in row]
        terms = ["".join(c.c for c in b.rows[0].chars[:lead]) for b, lead in row]
        if (
            len(row) >= LEAD_ENTRIES
            and all(e is not None for _, e in entries)
            and _listed_terms(terms)
        ):
            for block, entry in entries:
                assert entry is not None
                block.pieces, block.term_pieces = entry.pieces, entry.term_pieces
        row.clear()

    for block in (b for page in pages for b in page.blocks):
        if block.kind == Kind.HEADING and REASON_LETTER in block.reasons:
            continue
        found = (
            _lead_end(block.rows[0])
            if block.kind == Kind.PARAGRAPH
            and block.rows
            and not block.code
            and block.marker is None
            and block.term_pieces is None
            else None
        )
        if found is None:
            settle()
        else:
            row.append((block, found[0]))
    settle()


def _listed_terms(terms: Sequence[str]) -> bool:
    """Are these the terms of a glossary: names in alphabetical order? A label with a number
    ("Case 2:", "Theorem 3.1.") is no name, and labels that differ only in their last word
    ("Part A:", "Part B:") are parts in order, not terms. One term in ten may be out of place
    (a glossary sorted by another rule, a misread letter)."""
    keys = ["".join(c for c in term.casefold() if c.isalnum()) for term in terms]
    stems = {tuple(term.strip(" .:").split()[:-1]) for term in terms}
    if len(stems) == 1 and stems != {()}:
        return False
    odd = sum(a >= b for a, b in pairwise(keys)) + sum(any(c.isdigit() for c in k) for k in keys)
    return odd <= 0.1 * len(keys)


def _entry(term: list[str | _Inline], definition: list[str | _Inline], box: BBox, rows, size):
    """One glossary entry as a paragraph block: the term's pieces, a space, the definition's;
    `term_pieces` says how many pieces are the term."""
    return _Block(
        Kind.PARAGRAPH,
        box,
        list(rows),
        font_size=size,
        pieces=[*term, " ", *definition],
        term_pieces=len(term),
    )


def _term_over(block: _Block, following: _Block | None) -> bool:
    """Is `block` a term on a line or two of its own, with the paragraph indented right
    below it as its definition (OpenStax sets some entries as two blocks)?"""
    if following is None or block.kind != Kind.PARAGRAPH or following.kind != Kind.PARAGRAPH:
        return False
    if block.code or following.code or block.marker is not None or following.marker is not None:
        return False
    rows = block.rows
    margin, size = rows[0].x0, rows[0].size
    return (
        len(rows) <= GLOSSARY_TERM_ROWS
        and all(abs(r.x0 - margin) <= 0.5 * size for r in rows)
        and _term_words(_clean(" ".join(r.text for r in rows)))
        and all(r.x0 >= margin + GLOSSARY_INDENT * size for r in following.rows)
        and 0 <= following.bbox[1] - block.bbox[3] <= GLOSSARY_GAP * size
        and _has_content(following.pieces)
    )


def _glossary_entries(pages: list[_PageBlocks], *, document: _Document) -> None:
    """Under a heading that ends in "Key Terms", "Glossary" or "Vocabulary", a term and its
    definition become one paragraph block that the assembler writes as a `<dt>` and `<dd>`
    (in place; `Element.term_end`). Two shapes: the term on a line of its own at the margin
    with the definition indented below it, in one paragraph (OpenStax), and a bold term of
    a few words that layout took for a heading, directly over its definition paragraph.
    The second one makes the terms stop being headings: a heading for every term of a
    glossary would be an outline of hundreds of entries.

    The section lasts until a heading that is not a bold line (a larger font or a section
    number); a bold line that is not directly over a paragraph stays a heading."""
    active = False
    for page in pages:
        blocks, out, i = page.blocks, [], 0
        while i < len(blocks):
            block = blocks[i]
            if block.kind == Kind.HEADING:
                strong = REASON_LARGER_FONT in block.reasons or REASON_NUMBERED in block.reasons
                if GLOSSARY_HEADING.search(block.text or ""):
                    active = True
                elif strong:
                    active = False
            following = blocks[i + 1] if i + 1 < len(blocks) else None
            if active and (entries := _run_in_entries(block, document=document)):
                out.extend(entries)
            elif active and block.kind == Kind.PARAGRAPH and (taken := _entry_rows(block)):
                head = _Block(Kind.PARAGRAPH, block.bbox, block.rows[:taken])
                tail = _Block(Kind.PARAGRAPH, block.bbox, block.rows[taken:])
                out.append(
                    _entry(
                        _paragraph_pieces(head, document=document),
                        _paragraph_pieces(tail, document=document),
                        block.bbox,
                        block.rows,
                        block.font_size,
                    )
                )
            elif active and _term_over(block, following):
                assert following is not None
                out.append(
                    _entry(
                        _paragraph_pieces(block, document=document),
                        _paragraph_pieces(following, document=document),
                        _union([block.bbox, following.bbox]),
                        [*block.rows, *following.rows],
                        block.font_size,
                    )
                )
                i += 1
            elif (
                active
                and block.kind == Kind.HEADING
                and REASON_BOLD_BODY in block.reasons
                and REASON_LARGER_FONT not in block.reasons
                and _term_words(block.text or "")
                and following is not None
                and following.kind == Kind.PARAGRAPH
                and not following.code
                and not following.bold
                and following.marker is None
                and following.bbox[1] - block.bbox[3] <= GLOSSARY_GAP * (block.font_size or 10.0)
                and _has_content(following.pieces)
            ):
                out.append(
                    _entry(
                        [block.text or ""],
                        following.pieces,
                        _union([block.bbox, following.bbox]),
                        [*block.rows, *following.rows],
                        following.font_size,
                    )
                )
                i += 1
            else:
                out.append(block)
            i += 1
        page.blocks = out


def _settle_bold_letters(pages: list[_PageBlocks]) -> None:
    """Keep a waiting bold letter (`_bold_letter_runs`) as maths only with evidence:
    the same bold letter in the document's maths (a display or inline equation,
    `EVIDENCE_DOCUMENT`), or, for a small letter, another bold letter that is maths in
    the same paragraph ("x or s is used instead of d", `EVIDENCE_NEARBY`). Any other
    goes back into the text as the plain letter it was."""

    def waiting(piece: str | _Inline) -> bool:
        return isinstance(piece, _Inline) and piece.reasons == [BOLD_LETTER_REASON]

    def bold_keys(text: str) -> set[str]:
        return {key for ch in text if (key := _bold_key(ch)) is not None}

    blocks = [b for page in pages for b in page.blocks]
    if not any(waiting(p) for b in blocks if b.kind in _TEXT_BLOCKS for p in b.pieces):
        return
    in_maths: set[str] = set()
    for block in blocks:
        if block.kind == Kind.EQUATION:
            in_maths |= bold_keys(block.text or "")
        elif block.kind in _TEXT_BLOCKS:
            for piece in block.pieces:
                if isinstance(piece, _Inline) and not waiting(piece):
                    in_maths |= bold_keys(piece.text)
    for block in blocks:
        if block.kind not in _TEXT_BLOCKS or not any(waiting(p) for p in block.pieces):
            continue
        for piece in block.pieces:
            if isinstance(piece, _Inline) and waiting(piece) and _bold_key(piece.text) in in_maths:
                piece.reasons = [BOLD_LETTER_REASON, EVIDENCE_DOCUMENT]
        nearby = any(
            isinstance(p, _Inline) and not waiting(p) and bold_keys(p.text) for p in block.pieces
        )
        pieces: list[str | _Inline] = []
        for piece in block.pieces:
            if isinstance(piece, _Inline) and waiting(piece):
                letter = base_char(piece.text)
                if nearby and letter.islower():
                    piece.reasons = [BOLD_LETTER_REASON, EVIDENCE_NEARBY]
                else:
                    pieces.append(letter)
                    continue
            pieces.append(piece)
        block.pieces = _tidy(pieces)
