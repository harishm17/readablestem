"""One formula that layout cut into two boxes is written once.

Layout sometimes puts one inline formula into two to four boxes (it is cut at a wide space).
Each box is read with a picture of its line, and the line prompt tells the model to read on past
a box that stops early, so a reading can come back as the whole formula while the next box says
its own part again, and the page said it twice. Layout is left alone (a joined box is a new crop
and a new request); the repeat is taken out here, once every reading is in, where the results
are put into the document (`run._convert`, next to `table_math.settle`).

The evidence is the file's own characters, in the order they stand, never a bag of letters (a
bag cannot tell a repeat from a second formula that has the same letters).

The rule: a run of boxes on one line. Take the inline equations of one paragraph whose markers
follow each other on one line (the boxes share a line, and nothing but text stands between two
markers), and a window of two or more of them. A member's reading K says the whole window when
the letters, digits and Greek letters of K's reading, in document order, are the window's own
characters one for one and in order (each member's box, and the paragraph's text between two
members in its place), every sign, bracket or comma of the text between two members stands in
K's reading at that place, and every other member's reading is a run inside K's (it says nothing K
does not say). The window is then written once: K's marker stands where the window began, and
the other markers and the text between the first and the last go. So does a closing bracket
right after the window that the window's own characters opened and K's reading closes (layout
left its end in the paragraph). Which member is K: an accepted one before an asked one, then the
one with fewer failed checks, then one whose box has characters, then the first. A window whose
characters are K's letters in another order (a fraction, a matrix, a root that the file stores
in another order) is left as it is. So is a window with more than `MAX_BOXES` members, and one
where two members say the whole in two ways (`x_{t+1}` and `x^{t+1}`) and the one that would
stay was accepted: nobody would see the other reading.

K then stands for the whole formula, so its element takes the whole window: the box that holds
every member's box, and their text with the text between them. The caller cuts its picture again
from the page (`run._convert`), with no new request. A reviewer who keeps the original, or checks
the reading against the picture, has the whole formula in front of them, and a decision made on
an earlier upload about the part's picture is not carried onto it (`web.carry` matches pictures).

Not built: an accent that layout boxed apart from its letter (one on the development papers),
and an inline box inside a display's box. Where layout puts a block box over an inline one, the
inline formula belongs to its sentence, and taking it out for the display leaves the sentence
without it (`docs/steps.md`, "One formula in two boxes"): that is a fault of layout this step
does not mend.

The kept marker stands in the window's place, so the spaces around it stay as they were. The
paragraph's links, colours and runs move with the text (`core.inline`). A decision stays asked
wherever the reading kept is not accepted; one asked about the dropped copy only is gone with it.
What the dropped copy cost is handed back by the caller, so the document's cost and call counts
stay what was spent. A change that would leave a paragraph empty is not made.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import defusedxml.ElementTree as DET

from readablestem.core import inline
from readablestem.core.types import (
    INLINE_MARKER_RE,
    Element,
    Fix,
    Kind,
    Status,
    inline_marker,
)
from readablestem.pipeline import text_layer_compare

# The share of the shorter box's height that two boxes of one line share.
SAME_LINE = 0.5

# The most boxes one formula is cut into (real repeats are two or three): a longer window is
# not looked at, so a line of hundreds of boxes costs no more than a short one.
MAX_BOXES = 4

# The room a closing bracket takes after the window, as a share of the line's height: the
# bracket stands in the paragraph, outside every box, and the whole formula's box takes it in.
BRACKET_WIDTH = 0.35

_LEAVES = frozenset({"mi", "mn", "mo", "mtext", "ms"})
_WRAPPERS = frozenset({"math", "mrow", "mstyle", "semantics", "mpadded", "mphantom"})
_INVISIBLE = "\u2061\u2062\u2063\u2064\u200b\u200c\u200d"
_BRACKETS = {")": "(", "]": "[", "}": "{"}
# Signs a text layer and a reading write with different characters, as one.
_SAME_SIGN = str.maketrans(
    {
        "\u2212": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2010": "-",
        "\u2011": "-",
        "\u2217": "*",
        "\u2032": "'",
        "\u2019": "'",
        "\u22c5": "\u00b7",
    }
)


@dataclass(frozen=True)
class _Reading:
    element: Element
    fix: Fix
    reading: str  # letters, digits and Greek letters of the reading, in order
    box: str  # the same of the PDF's text in the box, in order
    tokens: tuple[str, ...]  # every symbol and operator of the reading, in order
    shape: tuple[str, ...]  # how the reading is built: its tags and tokens, in order


@dataclass(frozen=True)
class _Merge:
    keep: _Reading
    dropped: tuple[_Reading, ...]
    first: str  # the marker of the window's first member, and of its last
    last: str
    tail: int  # the closing brackets right after the window that the kept reading holds
    after: int  # the characters of the paragraph they take (the brackets and spaces before them)
    bbox: tuple[float, float, float, float]  # the box that holds every member's box
    text: str  # the window's own text: each box's and the text between two boxes


def _reading(element: Element, fix: Fix | None) -> _Reading | None:
    """What a settled equation says, or None when its fix is not a reading to compare."""
    if (
        fix is None
        or fix.equation is None
        or fix.status not in (Status.ACCEPTED, Status.NEEDS_REVIEW)
    ):
        return None
    try:
        reading = text_layer_compare.mathml_seq(fix.equation.mathml)
        tokens, shape = _tokens(fix.equation.mathml)
    except Exception:  # (a MathML that does not parse is no reading to compare)
        return None
    box = text_layer_compare.symbol_seq(element.text or "")
    return _Reading(element, fix, reading, box, tokens, shape)


def _tokens(mathml: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The symbols and operators of a MathML in document order, as a text layer would hold them
    (styled letters folded, invisible operators and spaces left out), and its shape: the same
    with the tag of every node that builds something (a script, a fraction, a root)."""
    root = DET.fromstring(mathml, forbid_dtd=True)
    tokens, shape = [], []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in _LEAVES and node.text:
            token = "".join(unicodedata.normalize("NFKC", node.text).casefold().split())
            if token.strip(_INVISIBLE):
                tokens.append(token.strip(_INVISIBLE))
                shape.append(token.strip(_INVISIBLE))
        elif tag not in _LEAVES and tag not in _WRAPPERS:
            shape.append(f"<{tag}>")
    return tuple(tokens), tuple(shape)


def _within(small: tuple[str, ...], big: tuple[str, ...]) -> bool:
    """Is `small` a run of `big`?"""
    n = len(small)
    return n > 0 and any(big[i : i + n] == small for i in range(len(big) - n + 1))


def _rank(r: _Reading, position: int) -> tuple[bool, int, bool, int]:
    """Which reading stays: an accepted one before an asked one, then the one with fewer failed
    checks, then one whose box has characters, then the first."""
    failed = sum(1 for c in r.fix.checks if not c.passed)
    return (r.fix.status is not Status.ACCEPTED, failed, not r.box, position)


def _same_line(a: Element, b: Element) -> bool:
    height = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1])
    shared = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
    return a.page == b.page and height > 0 and shared / height >= SAME_LINE


Run = list[tuple[int, int, _Reading]]  # (where its marker starts, where it ends, its reading)


def _runs(text: str, readings: dict[str, _Reading]) -> list[Run]:
    """The markers of a paragraph's text that follow each other on one line, as runs of two or
    more equations (a marker that is not a reading, or a line change, ends a run)."""
    runs: list[Run] = []
    run: Run = []
    for m in INLINE_MARKER_RE.finditer(text):
        r = readings.get(m.group(1))
        if r is None or (run and not _same_line(run[-1][2].element, r.element)):
            if len(run) > 1:
                runs.append(run)
            run = []
        if r is not None:
            run.append((m.start(), m.end(), r))
    if len(run) > 1:
        runs.append(run)
    return runs


def _whole(text: str, run: Run, i: int, j: int) -> str:
    """The characters of members i to j in order: each box's, and the text between two boxes."""
    out = run[i][2].box
    for k in range(i + 1, j + 1):
        out += text_layer_compare.symbol_seq(text[run[k - 1][1] : run[k][0]]) + run[k][2].box
    return out


def _stream(text: str) -> list[tuple[bool, str]]:
    """A text character by character: (True, a letter or digit as `symbol_seq` writes it) or
    (False, a sign, bracket or comma); spaces and invisible characters left out."""
    out: list[tuple[bool, str]] = []
    for ch in text:
        symbols = text_layer_compare.symbol_seq(ch)
        if symbols:
            out.extend((True, c) for c in symbols)
            continue
        for c in unicodedata.normalize("NFKC", ch).translate(_SAME_SIGN):
            if not c.isspace() and c not in _INVISIBLE and unicodedata.category(c) != "Lm":
                out.append((False, c))
    return out


def _says(keep: _Reading, text: str, members: Run) -> list[str] | None:
    """Does the kept reading say the window with the signs between its boxes? The reading's
    signs after its last letter when it does (a closing bracket among them, perhaps), else None.

    The letters and digits are the window's one for one (the caller saw that). Here: every sign,
    bracket or comma of the text between two boxes stands in the reading at that place, between
    the letters either side of it, and so does a box that holds only signs (an "=" that layout
    boxed alone). What the reading does not say would be lost with the text."""
    said = _stream(" ".join(keep.tokens))
    if "".join(c for symbol, c in said if symbol) != keep.reading:
        said = None  # (the reading's signs cannot be placed: then the text may hold none)
    at = 0
    for k, (_, _, member) in enumerate(members):
        pieces: list[tuple[bool, str]] = [(True, c) for c in member.box]
        if not pieces:  # no letter or digit in the box: its signs are placed, as text would be
            pieces = [p for p in _stream(member.element.text or "") if not p[0]]
        if k:
            pieces = _stream(text[members[k - 1][1] : members[k][0]]) + pieces
        for symbol, c in pieces:
            if said is None:
                if not symbol:
                    return None
                continue
            if symbol:
                while at < len(said) and not said[at][0]:
                    at += 1
                at += 1  # (the same letter: the reading's letters are the window's)
                continue
            while at < len(said) and said[at] != (False, c):
                if said[at][0]:
                    return None  # the next letter comes first: the reading has no such sign
                at += 1
            if at == len(said):
                return None
            at += 1
    return [c for _, c in said[at:]] if said is not None else []


def _tail(text: str, members: Run, after: list[str]) -> tuple[int, int]:
    """How many closing brackets right after the window go with it, and how many characters of
    the paragraph they take (a space may stand before one): brackets the window's own characters
    (the boxes and the text between them) opened and did not close, which the kept reading
    closes after its last letter (`after`). A bracket the sentence opened is not owed."""
    held = "".join(m[2].element.text or "" for m in members) + text[members[0][1] : members[-1][0]]
    owed = {close: held.count(opener) - held.count(close) for close, opener in _BRACKETS.items()}
    count = at = length = 0
    for k, ch in enumerate(text[members[-1][1] :]):
        if ch.isspace():
            continue
        if owed.get(ch, 0) <= 0 or ch not in after[at:]:
            break
        at = after.index(ch, at) + 1
        owed[ch] -= 1
        count, length = count + 1, k + 1
    return count, length


def _around(
    boxes: list[tuple[float, float, float, float]], tail: int
) -> tuple[float, float, float, float]:
    """The box that holds every member's box, and the closing brackets after the last."""
    top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
    right = max(b[2] for b in boxes) + tail * BRACKET_WIDTH * (bottom - top)
    return (min(b[0] for b in boxes), top, right, bottom)


def _windows(text: str, run: Run) -> list[_Merge]:
    """The windows of a run that one of its readings says whole, longest first, none sharing a
    member with another."""
    n = len(run)
    taken: set[int] = set()
    merges: list[_Merge] = []
    spans = [(i, j) for i in range(n) for j in range(i + 1, min(n, i + MAX_BOXES))]
    for i, j in sorted(spans, key=lambda w: (w[0] - w[1], w[0])):
        if taken.intersection(range(i, j + 1)):
            continue
        members = run[i : j + 1]
        if not any(m[2].box for m in members):
            continue  # no characters of the file in the window: nothing to compare with
        whole = _whole(text, run, i, j)
        says = [
            (_rank(m[2], at), m[2])
            for at, m in enumerate(members)
            if m[2].reading == whole
            and all(
                o is m or (o[2].reading in m[2].reading and _within(o[2].tokens, m[2].tokens))
                for o in members
            )
        ]
        if not says:
            continue
        keep = min(says, key=lambda s: s[0])[1]
        if keep.fix.status is Status.ACCEPTED and any(r.shape != keep.shape for _, r in says):
            continue  # a second reading of the whole says it another way, and nobody is asked
        after = _says(keep, text, members)
        if after is None:
            continue
        tail, length = _tail(text, members, after)
        boxes = [m[2].element.bbox for m in members]
        end = members[-1][1]
        merges.append(
            _Merge(
                keep,
                tuple(m[2] for m in members if m[2] is not keep),
                inline_marker(members[0][2].element.id),
                inline_marker(members[-1][2].element.id),
                tail,
                length,
                _around(boxes, tail),
                "".join(
                    (text[members[k - 1][1] : m[0]] if k else "") + (m[2].element.text or "")
                    for k, m in enumerate(members)
                )
                + text[end : end + length],
            )
        )
        taken.update(range(i, j + 1))
    return merges


def _plan(elements: list[Element], fixes: dict[str, Fix]) -> list[_Merge]:
    """Every formula written twice that the file's characters show, as merges."""
    readings = {
        e.id: r
        for e in elements
        if e.kind is Kind.EQUATION
        and e.display == "inline"
        and (r := _reading(e, fixes.get(e.id))) is not None
    }
    merges: list[_Merge] = []
    for parent in elements:
        if parent.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM) and parent.text:
            for run in _runs(parent.text, readings):
                merges.extend(_windows(parent.text, run))
    return merges


def _edit(parent: Element, start: int, end: int, insert: str) -> Element | None:
    """`parent` with `text[start:end]` replaced by `insert` and its spans moved, or None when
    that cannot be done (not a paragraph, or nothing would be left)."""
    if parent.kind not in (Kind.PARAGRAPH, Kind.LIST_ITEM) or not parent.text:
        return None
    text = parent.text[:start] + insert + parent.text[end:]
    if not text.strip():
        return None
    shift = len(insert) - (end - start)

    def first(offset: int) -> int:
        return offset if offset <= start else offset + shift if offset >= end else start

    def last(offset: int) -> int:
        return (
            offset if offset <= start else offset + shift if offset >= end else start + len(insert)
        )

    def place(a: int, b: int) -> tuple[int, int] | None:
        return (first(a), last(b)) if last(b) > first(a) else None

    update, _ = inline.moved(parent, place)
    update["text"] = text
    if parent.term_end is not None:
        update["term_end"] = last(parent.term_end)
    return parent.model_copy(update=update)


def settle(
    elements: list[Element], fixes: dict[str, Fix]
) -> tuple[list[Element], dict[str, Fix], list[tuple[int, Fix]], list[Element]]:
    """The elements and fixes with each repeated formula written once, the dropped fixes with
    their pages (their cost and calls were spent: the caller counts them), and the kept
    elements, which now hold the whole formula's box and text (the caller cuts their pictures
    again)."""
    merges = _plan(elements, fixes)
    if not merges:
        return elements, fixes, [], []
    by_id = {e.id: e for e in elements}
    dropped: dict[str, Fix] = {}
    widened: list[Element] = []
    for merge in merges:
        parent = by_id.get(merge.keep.element.parent_id or "")
        if parent is None or not parent.text:
            continue
        start, last = parent.text.find(merge.first), parent.text.find(merge.last)
        if start < 0 or last < start:
            continue
        end = last + len(merge.last) + merge.after
        cut = _edit(parent, start, end, inline_marker(merge.keep.element.id))
        if cut is None:
            continue
        by_id[cut.id] = cut
        # The glyphs kept with the element were read under its own box: they are not the
        # whole formula's, and no check reads them after this step.
        whole = merge.keep.element.model_copy(
            update={"bbox": merge.bbox, "text": merge.text, "glyphs": None}
        )
        by_id[whole.id] = whole
        widened.append(whole)
        for d in merge.dropped:
            dropped[d.element.id] = d.fix
    if not dropped:
        return elements, fixes, [], []
    page_of = {e.id: e.page for e in elements}
    kept = [by_id[e.id] for e in elements if e.id not in dropped]
    return (
        kept,
        {i: f for i, f in fixes.items() if i not in dropped},
        [(page_of[i], f) for i, f in dropped.items()],
        widened,
    )
