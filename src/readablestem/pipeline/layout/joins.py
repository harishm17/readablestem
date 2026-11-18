"""Joins across columns and pages and the passes over the whole document: run-in labels, numbered
lines promoted to headings, paragraphs that go on over a page break and half fractions.
"""

from __future__ import annotations

import bisect
import re
import statistics
from collections import Counter
from collections.abc import Sequence
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


def _last_body_block(blocks: Sequence[_Block], ctx: _Context) -> _Block | None:
    """The lowest block of a page that is not a footnote (smaller type below the text)."""
    return next((b for b in reversed(blocks) if _body_sized(b, ctx)), None)


def _goes_on(
    last: _Block, first: _Block, limits: tuple[float, float], edges: Sequence[float] = ()
) -> bool:
    """Is `first`, the top block of a page, the rest of the paragraph that `last` ends on the
    page before? The first part must not end a sentence, the second must start in the middle
    of one (a lower-case letter), and the two must look alike: the same size, typeface and
    weight, the left edge of the second at the first's text edge, or at the left edge of
    another body block of its page (`edges`: a two-column page ends in the right column and
    the next starts in the left), with no first-line indent.
    Both must sit at the text area's edges (`limits`: its bottom and its top, with slack)."""
    if (
        last.kind != Kind.PARAGRAPH
        or last.code
        or first.kind != Kind.PARAGRAPH
        or first.code
        or first.marker is not None
        or first.joined
        or not last.rows
        or not first.rows
        or last.font_size is None
        or first.font_size is None
        or abs(last.font_size - first.font_size) > 0.5
        or last.bbox[3] < limits[0]
        or first.bbox[1] > limits[1]
    ):
        return False
    tail, head = last.rows[-1], first.rows[0]
    at_edge = abs(tail.x0 - head.x0) <= 3.0 or (
        # across columns, a one-line tail is too often a license or copyright line
        len(last.rows) >= 2 and any(abs(head.x0 - e) <= 3.0 for e in edges)
    )
    if not at_edge or head.x0 - first.bbox[0] > 3.0:
        return False
    if tail.family != head.family or tail.bold != head.bold:
        return False
    start = next((p for p in first.pieces if not isinstance(p, str) or p.strip()), None)
    if not isinstance(start, str) or not start.lstrip()[:1].islower():
        return False
    return not _ends_sentence(last.pieces)


def _join_page_splits(pages: Sequence[_PageBlocks], ctx: _Context) -> None:
    """A paragraph that goes on over a page break is one block again.

    The top block of a page continues the lowest body block of the page before (footnotes
    below it, and the running header and footer, are not in the way: the margin filter has
    dropped the latter and footnotes are smaller type) when `_goes_on`. The first part
    keeps its page and box (an element has one page and one box); the later part stays in
    its page's blocks, marked `joined`, so the notices and the safety net still see it, and
    `_to_elements` writes both parts as one element. A paragraph that runs over a second
    break joins again."""
    bodies = [
        b
        for page in pages
        for b in page.blocks
        if b.kind in _TEXT_BLOCKS and _body_sized(b, ctx) and not b.code
    ]
    if not bodies:
        return
    slack = 3 * ctx.pitch
    limits = (max(b.bbox[3] for b in bodies) - slack, min(b.bbox[1] for b in bodies) + slack)
    owner: dict[int, _Block] = {}
    for before, page in pairwise(pages):
        if page.number != before.number + 1 or not page.blocks:
            continue
        last = _last_body_block(before.blocks, ctx)
        edges = [
            b.bbox[0]
            for b in page.blocks[1:]
            if b.kind in _TEXT_BLOCKS and _body_sized(b, ctx) and not b.code
        ]
        if last is None or not _goes_on(last, page.blocks[0], limits, edges):
            continue
        host, part = owner.get(id(last), last), page.blocks[0]
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
