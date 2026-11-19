"""Joins across columns and pages and the passes over the whole document: run-in labels, numbered
lines promoted to headings, paragraphs that go on over a page break and half fractions.
"""

from __future__ import annotations

import bisect
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import pairwise

from readablestem.core.types import Kind
from readablestem.pipeline.layout.blocks import (
    _QUANTITY_WORDS,
    _SENTENCE_END,
    _TEXT_BLOCKS,
    REASON_BOLD_BODY,
    REASON_LARGER_FONT,
    REASON_NUMBERED_LINE,
)
from readablestem.pipeline.layout.furniture import _is_page_number
from readablestem.pipeline.layout.page import FOOTNOTE_SIZE
from readablestem.pipeline.layout.shared import _Block, _Context, _Inline, _PageBlocks, _Row

# A bold body-size label that repeats ("Solution" under every example): bold text, not a
# heading (`_mark_run_in_labels`, `heading_step.heading_fixes`).
REASON_RUN_IN_LABEL = "run-in label"

# A plain numbered line found a heading only from its neighbours' numbers, in a sequence
# that does not look like a document's sections (`_promote_numbered_lines`): a decision.
REASON_NUMBERED_UNCONFIRMED = "numbered line, unconfirmed"


# Read across the whole document, after every page has its blocks: what a page cannot tell on
# its own (a numbered line is a heading only if its neighbours are numbered too; a label is
# a label only if it repeats; a paragraph goes on over a page break).


def _text_of(pieces: Sequence[str | _Inline]) -> str | None:
    """The text of a paragraph's pieces, or None when it holds inline maths."""
    if any(isinstance(piece, _Inline) for piece in pieces):
        return None
    return "".join(piece for piece in pieces if isinstance(piece, str))


_CLOSING = "\"')]’”"  # noqa: RUF001


def _ends_sentence(pieces: Sequence[str | _Inline]) -> bool:
    """Does the last piece of text end on a full stop, question or exclamation mark, colon or
    ellipsis (closing quotes and brackets aside)? A paragraph that ends on maths does not."""
    last = next((p for p in reversed(pieces) if not isinstance(p, str) or p.strip()), None)
    if not isinstance(last, str):
        return False
    end = last.rstrip().rstrip(_CLOSING)[-1:]
    return end != "" and end in _SENTENCE_END


def _ends_open(pieces: Sequence[str | _Inline]) -> bool:
    """Does the last piece of text end on a comma or a semicolon? A paragraph does not end
    there, so the next page's first paragraph goes on from it, whatever letter it starts with
    (an acronym or a name)."""
    last = next((p for p in reversed(pieces) if not isinstance(p, str) or p.strip()), None)
    return isinstance(last, str) and last.rstrip()[-1:] in (",", ";")


# A page's first text that starts with a citation closed by punctuation ("[12].") is the end
# of a sentence: a paragraph does not start with one (a reference list's "[12]" has none).
_CITATION_THEN_PUNCTUATION = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\][.,;)]")  # noqa: RUF001

RUN_IN_LABELS = frozenset({"solution", "answer", "discussion", "procedure", "proof", "hint"})
MIN_LABEL_COPIES = 2  # a label word that repeats this often is a label
LABEL_SIZE_TOLERANCE = 0.05  # of the body size: "exactly body size"
LABEL_INSET = 3.0  # pt a label sits in from its page's text edge: inside a box, not a section


def _text_edges(page: _PageBlocks) -> dict[str, float]:
    """Left edge of the body text of each column of a page: the leftmost x where at least
    three paragraphs of two lines or more start (within 2.5 pt), else the leftmost such
    paragraph's (a column with none has no entry)."""
    starts: dict[str, list[float]] = {}
    for block in page.blocks:
        if block.kind == Kind.PARAGRAPH and len(block.rows) >= 2:
            starts.setdefault(block.rows[0].column, []).append(block.bbox[0])
    edges: dict[str, float] = {}
    for column, xs in starts.items():
        xs.sort()
        edges[column] = next(
            (x for i, x in enumerate(xs) if bisect.bisect_right(xs, x + 2.5) - i >= 3), xs[0]
        )
    return edges


def _mark_run_in_labels(pages: Sequence[_PageBlocks], ctx: _Context) -> None:
    """Mark the bold run-in labels that repeat (`REASON_RUN_IN_LABEL`).

    "Solution" or "Procedure" under every example is a label, not a heading: OpenStax's
    own outline does not count them, and a screen-reader user who navigates by headings
    would stop at each. The rule is narrow on purpose (docs/layout.md "Run-in labels"): a
    heading found only by the bold body-size rule (no larger font, no number, no tag), one
    line, at body size, whose text is one of `RUN_IN_LABELS` (a trailing full stop or colon
    aside), set in from its column's text edge (`LABEL_INSET`: inside a box, where an author
    sets labels; a section heading sits at the margin, as LaTeX's `\\subsubsection*{Solution}`
    and a Word heading style do), and the same label at least `MIN_LABEL_COPIES` times in
    the document."""
    found: dict[str, list[_Block]] = {}
    for page in pages:
        edges = _text_edges(page)
        for block in page.blocks:
            edge = edges.get(block.rows[0].column) if block.rows else None
            if (
                edge is not None
                and block.kind == Kind.HEADING
                and block.reasons[:1] == [REASON_BOLD_BODY]
                and REASON_LARGER_FONT not in block.reasons
                and len(block.rows) == 1
                and block.font_size is not None
                and abs(block.font_size / ctx.body - 1) <= LABEL_SIZE_TOLERANCE
                and block.bbox[0] - edge > LABEL_INSET
            ):
                label = (block.text or "").strip().rstrip(".:").strip().casefold()
                if label in RUN_IN_LABELS:
                    found.setdefault(label, []).append(block)
    for blocks in found.values():
        if len(blocks) >= MIN_LABEL_COPIES:
            for block in blocks:
                block.reasons.append(REASON_RUN_IN_LABEL)


_NUMBERED_PLAIN = re.compile(r"^(?P<n>\d{1,2})\s+(?P<title>[A-Z]\S*(?:\s+\S+){0,7})$")
_NO_TITLE_END = ".,;:!?"

# A numbered line that starts with one of these is an exercise or a question ("1 Find the
# final speed of the cart"), never a section title: problem sets are content.
_EXERCISE_OPENERS = frozenset(
    [
        "find",
        "calculate",
        "determine",
        "solve",
        "compute",
        "show",
        "prove",
        "explain",
        "describe",
        "write",
        "draw",
        "use",
        "give",
        "list",
        "state",
        "identify",
        "estimate",
        "evaluate",
        "simplify",
        "graph",
        "plot",
        "sketch",
        "derive",
        "verify",
        "convert",
        "express",
        "compare",
        "discuss",
        "define",
        "suppose",
        "consider",
        "let",
        "choose",
        "complete",
        "answer",
        "name",
        "predict",
        "measure",
        "what",
        "why",
        "how",
        "when",
        "where",
        "which",
        "who",
        "is",
        "are",
        "does",
        "do",
        "can",
        "will",
        "would",
        "should",
        "if",
    ]
)


MAX_STRONG_TITLE_WORDS = 6  # a longer title is a decision
MAX_FIRST_NUMBER = 2  # a sequence of section numbers starts at 1 (or 2: no section 0)


def _numbered_candidate(block: _Block, ctx: _Context) -> tuple[int, int] | None:
    """(integer, words in the title) of a plain body-size line that reads like "3 Forces"
    (one regular line, a capital after the number, no closing punctuation, not a quantity,
    not an exercise or a question), else None."""
    if (
        block.kind != Kind.PARAGRAPH
        or block.code
        or block.marker is not None
        or block.bold
        or len(block.rows) != 1
        or block.font_size is None
        or abs(block.font_size - ctx.body) > 0.5
    ):
        return None
    text = _text_of(block.pieces)
    match = _NUMBERED_PLAIN.match((text or "").strip())
    if match is None or match["title"][-1] in _NO_TITLE_END:
        return None
    words = match["title"].split()
    stem = "".join(c for c in words[0].lower() if c.isalpha())
    unit = stem in _QUANTITY_WORDS or stem.rstrip("s") in _QUANTITY_WORDS
    if unit and not (len(words) > 1 and words[1][:1].isupper()):
        return None
    if stem in _EXERCISE_OPENERS:
        return None
    if sum(c.isalpha() for c in match["title"]) < 3:
        return None
    return int(match["n"]), len(words)


def _promote_numbered_lines(pages: Sequence[_PageBlocks], ctx: _Context) -> None:
    """Plain body-size numbered lines in sequence are headings ("3 Forces", "4 Energy").

    At body size a bare integer before a word is too often a quantity or a list line to
    call a heading on its own (`section_heading_number`). Here it counts when the document
    corroborates it: the next such line, in the same type and column, carries the next
    integer, with at least one paragraph of two lines or more between them (so a run of
    short numbered lines is a list, not headings). A line that opens like an exercise or a
    question is never a candidate ("1 Find the final speed", "2 What is the mass"). The
    evidence is the weak "standalone numbered line" (an optional note), never "numbered
    section"; and where the sequence does not look like a document's sections (it does not
    start at 1 or 2, or the title is longer than `MAX_STRONG_TITLE_WORDS`) the heading is
    `REASON_NUMBERED_UNCONFIRMED`: a required decision."""
    flat = [block for page in pages for block in page.blocks]
    found: list[tuple[int, int, int, tuple[float, str, str], _Block]] = []
    for position, block in enumerate(flat):
        candidate = _numbered_candidate(block, ctx)
        if candidate is not None:
            style = (
                round((block.font_size or 0.0) * 2) / 2,
                block.rows[0].family,
                block.rows[0].column,
            )
            found.append((position, candidate[0], candidate[1], style, block))
    start_of: dict[int, int] = {}  # a promoted block -> the integer its sequence starts at
    for (first_at, first_n, _, first_style, first), (
        next_at,
        next_n,
        _,
        next_style,
        second,
    ) in pairwise(found):
        if next_n != first_n + 1 or next_style != first_style:
            continue
        if not any(
            b.kind == Kind.PARAGRAPH and len(b.rows) >= 2 for b in flat[first_at + 1 : next_at]
        ):
            continue
        start_of.setdefault(id(first), first_n)
        start_of[id(second)] = start_of[id(first)]
    for _, _, words, _, block in found:
        if id(block) not in start_of:
            continue
        block.text = (_text_of(block.pieces) or "").strip()
        block.kind = Kind.HEADING
        block.reasons = [REASON_NUMBERED_LINE]
        if start_of[id(block)] > MAX_FIRST_NUMBER or words > MAX_STRONG_TITLE_WORDS:
            block.reasons.append(REASON_NUMBERED_UNCONFIRMED)
        block.pieces = []


def _body_sized(block: _Block, ctx: _Context) -> bool:
    return block.font_size is not None and block.font_size >= FOOTNOTE_SIZE * ctx.body


# What may stand above the first text of a page, or below the last of it, without being part of
# the text's flow: a picture, a table or a caption floats to the top or the foot of a page.
_FLOATS = (Kind.FIGURE, Kind.TABLE, "caption")
EDGE_TOLERANCE = 3.0  # pt: two left edges that are one
MAX_INDENT = 3.0  # x font size: how far a one-line tail's first line may stand in
FAR_COLUMN = 12.0  # x font size: a text edge this far in is the other column's, not an indent
LOW_PAGE_NUMBER = 0.8  # of the page height: a number below this is a page's own number


def _page_number(block: _Block, page: _PageBlocks) -> bool:
    """A lone page number in the foot of a page that the margin filter left in the body (a
    footer set low on a tall page is outside its band)."""
    return (
        block.kind == Kind.PARAGRAPH
        and len(block.rows) == 1
        and block.bbox[1] > LOW_PAGE_NUMBER * page.size[1]
        and _is_page_number(_text_of(block.pieces) or "")
    )


def _flow_tail(page: _PageBlocks, ctx: _Context) -> _Block | None:
    """The last block of the body flow of a page: below it may be pictures and tables that
    float to the foot, footnotes (smaller type) and the page's number, none of which a
    paragraph goes on after. A display equation or a heading is the tail itself (small or
    not), so nothing joins across it."""

    def below_the_flow(b: _Block) -> bool:
        if b.kind in _FLOATS or _page_number(b, page):
            return True
        return b.kind in _TEXT_BLOCKS and not _body_sized(b, ctx)

    return next((b for b in reversed(page.blocks) if not below_the_flow(b)), None)


def _flow_head(page: _PageBlocks) -> _Block | None:
    """The first block of the body flow of a page: a picture or a table on top is not it."""
    return next((b for b in page.blocks if b.kind not in _FLOATS), None)


def _goes_on(
    last: _Block,
    first: _Block,
    tail_edges: Mapping[str, float],
    head_edges: Mapping[str, float],
) -> bool:
    """Is `first`, the first text of a page, the rest of the paragraph (or list item) that
    `last` ends on the page before? The first part must not end a sentence, the second must
    start in the middle of one (a lower-case letter, no list marker), and the two must look
    alike: the same size, typeface and weight, and no first-line indent on the second.

    The two stand at one edge: the lines of the first at the same x as the second's, or, in
    its own column, each at its column's text edge (`tail_edges` for the page before,
    `head_edges` for this page: odd and even pages have other margins, and a two-column page
    ends in its right column and goes on in the left one) or, for a list item, the second
    at the item's text. A first part of one line may stand in by a first-line indent. A
    one-line tail across columns is too often a license or copyright line."""
    if (
        last.kind not in _TEXT_BLOCKS
        or last.code
        or last.lines
        or first.kind != Kind.PARAGRAPH
        or first.code
        or first.lines
        or first.marker is not None
        or first.joined
        or not last.rows
        or not first.rows
        or last.font_size is None
        or first.font_size is None
        or abs(last.font_size - first.font_size) > 0.5
    ):
        return False
    tail, head = last.rows[-1], first.rows[0]
    if head.x0 - first.bbox[0] > EDGE_TOLERANCE:
        return False
    if tail.family != head.family or tail.bold != head.bold:
        return False
    start = next((p for p in first.pieces if not isinstance(p, str) or p.strip()), None)
    if not isinstance(start, str) or _ends_sentence(last.pieces):
        return False
    if not (
        start.lstrip()[:1].islower()
        or _CITATION_THEN_PUNCTUATION.match(start.lstrip())
        or _ends_open(last.pieces)
    ):
        return False
    return _same_edge(last, first, tail_edges, head_edges)


def _same_edge(
    last: _Block,
    first: _Block,
    tail_edges: Mapping[str, float],
    head_edges: Mapping[str, float],
) -> bool:
    """The place test of `_goes_on`."""
    tail, head = last.rows[-1], first.rows[0]
    item = last.marker is not None
    size = last.font_size or 0.0
    x = last.marker.x if last.marker is not None else tail.x0
    xs = [x]
    if not item and len(last.rows) >= 2:
        before = last.rows[-2]
        if before.column == tail.column and 0 < tail.x0 - before.x0 <= MAX_INDENT * size:
            xs.append(before.x0)  # the last line may be the first of a paragraph, in by its indent
    if any(abs(c - head.x0) <= EDGE_TOLERANCE for c in xs):
        return True
    tail_edge = last.origin if item else tail_edges.get(tail.column)
    if tail_edge is None and not item and len(last.rows) >= 2:
        # A block that begins in the other column has no edge of its column's: its last two
        # lines standing at one x are the column's body text.
        before = last.rows[-2]
        if before.column == tail.column and abs(before.x0 - tail.x0) <= EDGE_TOLERANCE:
            tail_edge = tail.x0
    head_edge = head_edges.get(head.column)
    if tail_edge is None or head_edge is None:
        return False
    across = tail.column != head.column
    if across and len(last.rows) < 2:
        return False
    lefts = [r.x0 for r in last.rows]
    one_column = max(lefts) - min(lefts) <= MAX_INDENT * size  # not an index's columns of entries
    for c in xs:
        inset = c - tail_edge  # a paragraph's lines stand at its edge; an item's text stands in
        if len(last.rows) == 1 and not item and not across and 0 < inset <= MAX_INDENT * size:
            inset = 0.0  # the one line is the first of its paragraph, in by the first-line indent
        if one_column and not item and len(last.rows) >= 2 and inset > FAR_COLUMN * size:
            inset = 0.0  # in so far that it is another column, on a page with unlabeled columns
        if abs(head.x0 - head_edge - inset) <= EDGE_TOLERANCE:
            return True
    return False


def _join_page_splits(pages: Sequence[_PageBlocks], ctx: _Context) -> None:
    """A paragraph that goes on over a page break is one block again.

    The first text of a page continues the last block of the body flow of the page before
    (`_flow_tail`, `_flow_head`: running heads, page numbers and footnotes, pictures and
    tables on top or at the foot are not in the way) when `_goes_on`. The first part
    keeps its page and box (an element has one page and one box); the later part stays in
    its page's blocks, marked `joined`, so the notices and the safety net still see it, and
    `_to_elements` writes both parts as one element. A paragraph that runs over a second
    break joins again."""
    owner: dict[int, _Block] = {}
    edges: dict[int, Mapping[str, float]] = {}
    for before, page in pairwise(pages):
        if page.number != before.number + 1 or not page.blocks:
            continue
        last, part = _flow_tail(before, ctx), _flow_head(page)
        if last is None or part is None:
            continue
        for p in (before, page):
            edges.setdefault(p.number, _text_edges(p))
        if not _goes_on(last, part, edges[before.number], edges[page.number]):
            continue
        host = owner.get(id(last), last)
        host.parts.append((page.number, part))
        part.joined = True
        owner[id(part)] = host


def _body_and_pitch(rows_by_page: dict[int, list[_Row]]) -> _Context:
    counts: Counter[float] = Counter()
    for rows in rows_by_page.values():
        for row in rows:
            for c in row.ink:
                counts[round(c.size * 2) / 2] += 1
    body = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0] if counts else 10.0
    pitches = []
    for rows in rows_by_page.values():
        by_column: dict[str, list[_Row]] = {}
        for row in rows:
            if abs(row.size - body) <= 0.5 and not row.vertical:
                by_column.setdefault(row.column, []).append(row)
        for column_rows in by_column.values():
            column_rows.sort(key=lambda r: r.baseline)
            for a, b in pairwise(column_rows):
                step = b.baseline - a.baseline
                if 0.8 * body <= step <= 2.2 * body:
                    pitches.append(step)
    pitch = statistics.median(pitches) if pitches else 1.2 * body
    return _Context(body=max(body, 1.0), pitch=pitch)
