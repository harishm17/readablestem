"""Display equations: rows that are math and no prose, split labels and braced systems, equation
lines and the regions of stacked lines, and the number beside a display.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property

from readablestem.core.types import BBox
from readablestem.pipeline.layout.context import _Document
from readablestem.pipeline.layout.drawings import (
    _OPENING_BRACES,
    EQUATION_NUMBER,
    STRAY_BRACE_REACH,
    _column_content_near,
    _number_beside,
    _number_column,
    _number_under,
    _rule_rows,
)
from readablestem.pipeline.layout.glyphs import _SIGNS, _Char, _clean, _union
from readablestem.pipeline.layout.inline_runs import (
    _CONNECTIVES,
    JOINED_REASON,
    _classify_row,
    _is_equation_number,
    _prose_words,
)
from readablestem.pipeline.layout.shared import (
    _ROW_RELATIONS,
    SCRIPT_RATIO,
    _CharInfo,
    _Marker,
    _Path,
    _Region,
    _Row,
    _RowIndex,
    _tokens,
)
from readablestem.pipeline.parse import EQUATION_THRESHOLD

_CONTINUATION_SIGNS = _ROW_RELATIONS | frozenset("+-\u2212\u00b1\u2213\u00d7\u22c5")
MAX_LINE_GAP = 0.8  # x font size: widest vertical gap between lines of one display equation
ELLIPSIS_DOTS = 3  # centered dots in a row that make an ellipsis, not a multiplication sign
TABLE_ROW_GAP = 0.7  # x font size: widest gap between whole equations that are rows of a table
MAX_COLUMN_GAP = 6.0  # x font size: widest gap between the columns of an aligned row

# Widest gap between a display's formula and the connective word set between two formulas of
# one printed line ("P_A = ... and P_B = ...": `\hfill`, `\quad` and `\qquad` all give an em
# or more; a centred "and" between two columns can stand 7 font sizes from either).
CONNECTIVE_GAP = 9.0


def _split_labels(rows: list[_Row], *, document: _Document) -> list[_Row]:
    """Cut an equation number off the end of a maths row into a row of its own.

    `_merge_baselines` joins pieces of a line up to 4 x font size apart, so an equation
    number set close to its equation ("= 2000 m - 525 m ,   2.8") arrives as part of the
    row. It is cut off when the last token reads as an equation number (or the last two
    do: "Eq. 2.4", "Equation (2.4)"), stands at least one font size clear of the rest,
    and the rest has maths in it (or is only the comma that ends the maths) and does not
    end in a relation or operator ("x =   2.4" keeps its right-hand side). Whether it is
    an equation number is decided later, by where it sits (`_equation_regions`).

    A bracketed number may stand closer (`CLOSE_NUMBER_GAP`) when it ends at the right edge of
    its column's text: a display that fills its column ("..., (2)", "] (106)") leaves less than
    a font size before the number. Then the rest must read as a display equation on its own,
    so a line of prose that ends in a reference ("... as in Eq. (2)") keeps it.
    """
    edges: dict[str, list[float]] = {}  # per column, the right ends of its rows, furthest first
    for row in rows:
        if not row.vertical:
            edges.setdefault(row.column, []).append(row.x1)
    for ends in edges.values():
        ends.sort(reverse=True)
    out: list[_Row] = []
    for row in rows:
        tokens = _tokens(row)
        if row.vertical or len(tokens) < 2:
            out.append(row)
            continue
        start = tokens[-1][0]
        label = _clean("".join(c.c for c in row.chars[start:]))
        if len(tokens) >= 3:
            prefixed = _clean("".join(c.c for c in row.chars[tokens[-2][0] :]))
            if EQUATION_NUMBER.match(prefixed):  # "Eq. 2.4": the prefix goes with it
                start, label = tokens[-2][0], prefixed
        before = [c for c in row.chars[:start] if not c.blank]
        if (
            _is_equation_number(label)
            and before
            and (
                row.chars[start].x0 - before[-1].x1 >= 1.0 * row.size
                or _closes_display(row, start, label, edges[row.column], document=document)
            )
            and before[-1].c not in _CONTINUATION_SIGNS
            and (
                any(c.math or c.c in _ROW_RELATIONS or c.c in _SIGNS for c in before)
                or all(c.c in ",.;:" for c in before)  # the comma that ends the maths
            )
        ):
            out.append(
                _Row(row.chars[:start], column=row.column, marker=_marker_left(row.marker, start))
            )
            out.append(_Row(row.chars[start:], column=row.column))
        else:
            out.append(row)
    return out


NUMBERED_REASON = "equation number beside it"
NUMBERED_SHARE = 0.45  # share of a numbered line's characters that must read as maths
NUMBERED_INDENT = 1.5  # x font size: how far a display stands in from its column's text edge
# `LAYOUT_DISPLAY_GROUPING`: a number set far from its line (at the margin, a display's own
# distance from it) is better evidence than one a word space away, so the line needs less.
NUMBERED_FAR_GAP = 4.0  # x font size: the space between the line and a number set off from it
NUMBERED_FAR_SHARE = 0.35
NUMBERED_FAR_INDENT = 1.0
# `LAYOUT_DISPLAY_GROUPING`: a bracketed number this far (x font size) right of a line that ends in
# "+" or a relation is the display's number, not the line's right-hand side ("x = (2)" is never
# set so far off): the first row of a display set in several rows, its number level with it.
NUMBERED_TRAILING_GAP = 3.0
TRAILING_ROW_GAP = 1.5  # x font size: the most blank between that row and the display's next one
# `LAYOUT_DISPLAY_GROUPING`: a number on a row of its own above the display, at its left edge.
NUMBER_ABOVE_GAP = 1.0  # x font size: the most blank between the number's row and the display


def _promote_numbered_lines(rows: list[_Row], *, document: _Document) -> None:
    """Make a line of maths and a few words a display equation when its number stands beside it.

    A line with an English word in it ("DiD Estimate = ...", "x = ... times switch", "... i.i.d.
    ...", "H0 : ... versus H1 : ...") reads as text, so the "(7)" at the right edge, on its
    baseline, was a paragraph of its own. Such a number is the evidence the row is a display:
    the nearest row to its left on its baseline is made maths when it is mostly maths
    (`NUMBERED_SHARE`, and the score of a maths row), is not a list item, is not another number,
    and stands in from the text edge of its column (a last line of prose that ends in a mark
    such as "(5)" starts at the edge). Only a bracketed number does this, not a bare "2.4".

    With `LAYOUT_DISPLAY_GROUPING`, a number set `NUMBERED_FAR_GAP` font sizes or more from its
    line, on either side ("(3)" at the left margin of a centered line), asks less of the line:
    `NUMBERED_FAR_SHARE` of it maths, `NUMBERED_FAR_INDENT` in from the text edge.
    """
    for number in rows:
        if (
            number.kind == "frag"
            or number.absorbed
            or number.vertical
            or not EQUATION_NUMBER.match(number.clean)
        ):
            continue
        size = max(number.size, 1.0)
        beside = [
            r
            for r in rows
            if r is not number
            and not r.absorbed
            and not r.vertical
            and (
                r.x1 <= number.x0
                or (document.grouping and number.x1 + NUMBERED_FAR_GAP * size <= r.x0)
            )
            and _number_beside(number, r.bbox, max(r.size, number.size))
        ]
        line = min(beside, key=lambda r: _gap_between(number, r), default=None)
        if (
            line is None
            or line.kind != "text"
            or line.marker is not None
            or line.exercise_number
            or _is_equation_number(line.clean)
            or line.score < EQUATION_THRESHOLD
        ):
            continue
        far = document.grouping and _gap_between(number, line) >= NUMBERED_FAR_GAP * size
        share = NUMBERED_FAR_SHARE if far else NUMBERED_SHARE
        indent = NUMBERED_FAR_INDENT if far else NUMBERED_INDENT
        info = _CharInfo.of(line)
        counted = [i for i, c in enumerate(line.chars) if not c.blank and c.c not in ",.;:"]
        if not counted or sum(info.mathy(i) for i in counted) < share * len(counted):
            continue
        edge = min(r.x0 for r in rows if r.kind == "text" and r.column == line.column)
        if line.x0 - edge < indent * line.size:
            continue
        line.kind = number.kind = "math"
        line.reasons = [*line.reasons, NUMBERED_REASON]


def _gap_between(a: _Row, b: _Row) -> float:
    """The horizontal space between two rows (negative when they overlap)."""
    return max(a.x0 - b.x1, b.x0 - a.x1)


SET_OFF_REASON = "set off like a display"
SET_OFF_WORDS = 4  # the most English words a promoted line may hold
SET_OFF_INDENT = 2.5  # x font size: least space on each side of the line inside its column's text
NEAR_LINES = 1.5  # x font size: how far above and below the lines that give the left edge are


def _promote_set_off_lines(rows: list[_Row]) -> None:
    """Make a printed line that is mostly maths, has a few words in it and is set off from the
    text of its column a display equation (`LAYOUT_DISPLAY_GROUPING`).

    "Diam(C) = sup ...", "Pr[...] <= 2 exp(...)", "f(q) <= p iff q <= g(p)", a system's
    "... in Omega x (0, T)" each read as text because of the word, so the formula was cut
    around it, and the word, or the bracket after it, stayed in a paragraph. The evidence is
    the line as a whole: all the rows on one baseline (`_same_line`), at least
    `SET_OFF_INDENT` font sizes in from the left and the right end of the column's running
    text (a line of prose reaches one of them), with `NUMBERED_SHARE` of its characters maths,
    maths in it (a row that is maths or reads as maths), and at most `SET_OFF_WORDS` words.
    Its text rows become maths, so the crop and the reading hold the word.
    """
    prose = [r for r in rows if r.kind == "text" and not r.absorbed and not r.vertical]
    if not any(len(r.clean) >= 30 for r in prose):
        return
    candidates = [
        r
        for r in rows
        if not r.absorbed
        and not r.vertical
        and r.marker is None
        and not r.exercise_number
        and not (r.kind == "frag" and _is_equation_number(r.clean))
    ]
    candidates.sort(key=lambda r: r.baseline)
    seen: set[int] = set()
    for i, first in enumerate(candidates):
        if id(first) in seen or first.kind != "text":
            continue
        line = [first]
        for other in candidates[i + 1 :]:
            if other.baseline - first.baseline > 0.4 * max(first.size, other.size):
                break
            if any(_same_line(other, member) for member in line):
                line.append(other)
        seen.update(id(r) for r in line)
        around = [t for t in prose if t.kind == "text" and t.column == first.column]
        around = [t for t in around if t not in line]
        edge = [t for t in around if len(t.clean) >= 30]
        if not edge:
            continue
        size = max(r.size for r in line)
        left, right = min(t.x0 for t in edge), max(t.x1 for t in edge)
        # The text edge on the left is the one of the lines around it: a list item or a
        # theorem sets its text in from the column's edge, and so does the tail of its sentence
        # after a display ("with A(x) in R, and b(x) in R."), which is not set off from it.
        top, bottom = min(r.y0 for r in line), max(r.y1 for r in line)
        near = [
            t.x0
            for t in around
            if top - NEAR_LINES * size <= t.y1 and t.y0 <= bottom + NEAR_LINES * size
        ]
        left = min(near, default=left)
        if (
            min(r.x0 for r in line) - left < SET_OFF_INDENT * size
            or right - max(r.x1 for r in line) < SET_OFF_INDENT * size
        ):
            continue
        if not any(r.kind == "math" or r.score >= EQUATION_THRESHOLD for r in line):
            continue
        if _tight_under_text(line, around, size):
            continue
        infos = [(r, _CharInfo.of(r)) for r in line]
        counted = mathy = words = 0
        for r, info in infos:
            ink = [k for k, c in enumerate(r.chars) if not c.blank and c.c not in ",.;:"]
            counted += len(ink)
            mathy += sum(info.mathy(k) for k in ink)
            words += _prose_words(r, info)
        if not counted or mathy < NUMBERED_SHARE * counted or words > SET_OFF_WORDS:
            continue
        for r in line:
            if r.kind == "text":
                r.kind = "math"
                r.reasons = [*r.reasons, SET_OFF_REASON]


SET_OFF_GAP = 0.3  # x font size: the least blank above a display line, from the text over it


def _tight_under_text(line: list[_Row], text: Sequence[_Row], size: float) -> bool:
    """Does a line of running text stand directly over `line`, no further off than the gap
    between two lines of a paragraph? Then `line` is the tail of that paragraph (the last line
    of a list item, "... for every t we have x = y") and not a display, which has blank above."""
    x0, x1 = min(r.x0 for r in line), max(r.x1 for r in line)
    top = min(r.y0 for r in line)
    return any(
        t.x0 < x1
        and t.x1 > x0
        and top - 3 * size <= t.y1 <= top + 0.5 * size
        and top - t.y1 < SET_OFF_GAP * size
        for t in text
    )


CLOSE_NUMBER_GAP = 0.3  # x font size: the least a bracketed number stands from the maths before it


def _number_clearance(row: _Row) -> float:
    """How far (x font size) a number stands clear of the maths it numbers: a font size, but
    `CLOSE_NUMBER_GAP` for a bracketed number with a digit in it ("(50)" after a fraction that
    fills the column's width)."""
    text = row.clean
    close = EQUATION_NUMBER.match(text) and text.endswith(")") and any(c.isdigit() for c in text)
    return CLOSE_NUMBER_GAP if close else 1.0


def _closes_display(
    row: _Row, start: int, label: str, ends: list[float], *, document: _Document
) -> bool:
    """Is the bracketed number from `row.chars[start]` on the number of a display equation set
    to the right edge of its column (`ends`: the right ends of the column's rows, furthest
    first), though it stands less than a font size from the maths?"""
    others = ends[1] if len(ends) > 1 and row.x1 >= ends[0] else ends[0]
    before = [c for c in row.chars[:start] if not c.blank]
    if (
        not label.endswith(")")
        or not any(c.isdigit() for c in label)
        or row.chars[start].x0 - before[-1].x1 < CLOSE_NUMBER_GAP * row.size
        or row.x1 < others - 0.5 * row.size
    ):
        return False
    probe = _Row(row.chars[:start], column=row.column)
    _classify_row(probe, document=document)
    return probe.kind in ("math", "frag")


def _marker_left(marker: _Marker | None, cut: int) -> _Marker | None:
    """The list marker a row keeps when its characters from `cut` on are cut off: the marker
    only while some of the item's text stays after it. A row that is the marker and nothing
    else ("1 ." with its equation number cut away) is a line of text, not an empty list item
    (`_paragraph_pieces` would slice the marker off and be left with no characters)."""
    return marker if marker is not None and marker.end < cut else None


def _mark_case_rows(rows: list[_Row]) -> None:
    """Make the rows beside a tall opening brace maths, whatever words they hold.

    A function defined by cases ("g(t) = {  9t for 0 <= t <= 5 / 45 for 5 < t <= 10") has
    words in its rows ("for", "if", "otherwise", "elsewhere"), so each row reads as text
    and the equation falls apart into a brace, a paragraph and several inline pieces. The
    brace is the evidence: opening-brace glyphs (or the pieces TeX builds a tall one from,
    stacked at one x) taller than two lines, with two or more lines of rows right of it,
    level with it. Those rows are one equation with the brace; `_equation_regions` then
    stacks them.
    """
    candidates = [r for r in rows if not r.vertical and not r.absorbed and not r.exercise_number]
    glyphs = [(r, c) for r in candidates for c in r.ink if c.c in _OPENING_BRACES]
    glyphs.sort(key=lambda rc: (rc[1].x0, rc[1].y0))
    used: set[int] = set()
    for i, (_, first) in enumerate(glyphs):
        if i in used:
            continue
        group = [i]
        for j in range(i + 1, len(glyphs)):
            c = glyphs[j][1]
            last = max((glyphs[k][1] for k in group), key=lambda g: g.y1)
            if (
                j not in used
                and abs(c.x0 - first.x0) <= 0.6 * first.size
                and -0.5 * first.size <= c.y0 - last.y1 <= 0.1 * first.size
            ):
                group.append(j)
        used.update(group)
        top = min(glyphs[k][1].y0 for k in group)
        bottom = max(glyphs[k][1].y1 for k in group)
        right = max(glyphs[k][1].x1 for k in group)
        size = first.size
        if bottom - top < 2.0 * size:
            continue
        holders = {id(glyphs[k][0]): glyphs[k][0] for k in group}
        beside = []
        for r in candidates:
            content = [
                c for c in r.ink if c.c not in _OPENING_BRACES and c.x0 >= right - 0.3 * size
            ]
            if not content or not 0 <= content[0].x0 - right <= 3 * size:
                continue
            if (
                top - 0.5 * size
                <= (min(c.y0 for c in content) + max(c.y1 for c in content)) / 2
                <= bottom + 0.5 * size
            ):
                beside.append(r)
        if len({round(r.baseline) for r in beside}) < 2:
            continue
        if any(
            c.c == "}"
            for r in candidates
            if top - 0.5 * size <= (r.y0 + r.y1) / 2 <= bottom + 0.5 * size
            for c in r.ink
        ):
            continue  # a set in braces, not cases
        grown = True
        while grown:  # the second column of a row ("for 0 <= t <= 5" after "9t")
            grown = False
            for r in candidates:
                if r in beside or id(r) in holders:
                    continue
                if any(
                    abs(r.baseline - b.baseline) <= 0.4 * size
                    and 0 <= r.x0 - b.x1 <= 2 * MAX_COLUMN_GAP * size
                    for b in [*beside, *holders.values()]
                ):
                    beside.append(r)
                    grown = True
        for r in [*holders.values(), *beside]:
            r.kind = "math"
            r.case_row = True


def _split_braced_systems(rows: list[_Row], *, document: _Document) -> list[_Row]:
    """Cut the text off the front of the first row of a braced system.

    OpenStax prints "Solve the system by substitution. { 2x + y = 7" with the opening
    brace in the text font, on the first row of the system, so the prose before it and
    the first equation read as one row of text, and the equations below it as a display
    equation of their own: the system comes out cut in two. The row is cut at its brace
    when the brace is followed by maths with no closing brace (not a set "{1, 2}") and a
    maths row sits right under that maths, overlapping it: the rows are a system.
    The text before the brace (a sentence, an exercise number) stays a row of its own;
    the brace goes with the equations, so the crop shows it.
    """
    index = _RowIndex([r for r in rows if not r.vertical])
    out: list[_Row] = []
    for row in rows:
        cut = _brace_cut(row, index, document=document)
        if cut is None:
            cut = _stray_brace_cut(row, index, document=document)
        if cut is None:
            out.append(row)
            continue
        out.append(_Row(row.chars[:cut], column=row.column, marker=_marker_left(row.marker, cut)))
        out.append(_Row(row.chars[cut:], column=row.column))
    return out


def _stray_brace_cut(row: _Row, index: _RowIndex, *, document: _Document) -> int | None:
    """Index of the opening brace that ends a row of text, when it is the brace of the system
    set to its right, or None.

    OpenStax also prints "Solve the system by substitution. {" with the brace the last glyph of
    the sentence's row and the two equations in rows of their own to its right, level with the
    brace, not with the sentence (the maths rows' baselines are above and below the brace's).
    The row is then text with a brace at its end, and the system's crop starts at its first
    equation, without the brace. The brace is cut off as a row of its own when a maths row starts
    within `STRAY_BRACE_REACH` font sizes to its right, with another maths row under that one,
    and the brace's middle is level with the two; `_math_neighbours` then takes it into the system.
    """
    chars = row.chars
    ink = [i for i, c in enumerate(chars) if not c.blank]
    if row.vertical or len(ink) < 3 or chars[ink[-1]].c != "{":
        return None
    start = ink[-1]
    if not any(not c.blank for c in chars[:start]):
        return None
    brace = chars[start]
    size = row.size
    for right in index.near(brace.y0, brace.y1):
        if right is row or right.column != row.column or right.vertical:
            continue
        if not 0 <= right.x0 - brace.x1 <= STRAY_BRACE_REACH * size:
            continue
        probe = _Row(right.chars, column=right.column)
        _classify_row(probe, document=document)
        if probe.kind != "math":
            continue
        for below in index.near(right.y1 - 0.3 * size, right.y1 + 1.0 * size):
            if below is row or below is right or below.y0 < right.y1 - 0.3 * size:
                continue
            shared = min(below.x1, right.x1) - max(below.x0, right.x0)
            if below.column == row.column and shared >= 0.5 * min(
                below.x1 - below.x0, right.x1 - right.x0
            ):
                trial = _Row(below.chars, column=below.column)
                _classify_row(trial, document=document)
                middle = (brace.y0 + brace.y1) / 2
                if (
                    trial.kind == "math"
                    and right.y0 - 0.5 * size <= middle <= below.y1 + 0.5 * size
                ):
                    return start  # level with the middle of the system's rows
    return None


def _brace_cut(row: _Row, index: _RowIndex, *, document: _Document) -> int | None:
    """Index of the brace to cut `row` at (see `_split_braced_systems`), or None."""
    chars = row.chars
    if row.vertical or len(chars) < 4 or not any(c.c == "{" for c in chars):
        return None
    start = next(i for i, c in enumerate(chars) if c.c == "{")
    if not any(not c.blank for c in chars[:start]) or any(c.c == "}" for c in chars[start:]):
        return None
    after = _Row(chars[start + 1 :], column=row.column)
    if not after.ink or after.ink[0].blank:
        return None
    _classify_row(after, document=document)
    if after.kind != "math" or after.x0 - chars[start].x1 > 1.5 * row.size:
        return None
    size = max(row.size, after.size)
    for below in index.near(after.y1 - 0.3 * size, after.y1 + 1.0 * size):
        if below is row or below.y0 < after.y1 - 0.3 * size or below.column != row.column:
            continue
        shared = min(below.x1, after.x1) - max(below.x0, after.x0)
        if shared >= 0.5 * min(below.x1 - below.x0, after.x1 - after.x0):
            probe = _Row(below.chars, column=below.column)  # not classified yet
            _classify_row(probe, document=document)
            if probe.kind == "math":
                return start
    return None


_EXERCISE_NUMBER = re.compile(r"^\d{1,3}[.)]$")


def _split_exercise_numbers(rows: list[_Row], *, document: _Document) -> list[_Row]:
    """Cut an exercise number ("18.", "4)") off the front of a maths row into a text row.

    A numbered exercise that is only an equation ("18. 0 = 5 t - 4.9 t") reads as one maths
    row because `_merge_baselines` joins the number and the equation when they sit on one
    baseline. The number would then be sent to the model as part of the equation, and
    would stand in its text layer. The first token is cut off when it is a one- to
    three-digit number with a full stop or bracket, drawn in a text font (no maths font,
    no maths character), with a gap before the rest, and the rest alone reads as a display
    equation. A row that already starts a list item (`row.marker`) keeps its item, and a
    row whose rest is prose or a lone fragment is not touched. The number stays in the
    document as a text row of its own, in front of the equation (`_Row.exercise_number`).
    """
    out: list[_Row] = []
    for row in rows:
        tokens = _tokens(row)
        if _lone_exercise_number(row, tokens):
            row.exercise_number = True
            out.append(row)
            continue
        if row.vertical or row.marker is not None or len(tokens) < 2:
            out.append(row)
            continue
        first, rest = tokens[0], tokens[1:]
        end = first[1]
        token = "".join(c.c for c in row.chars[first[0] : end])
        if token.isdigit() and "".join(c.c for c in row.chars[slice(*rest[0])]) in (".", ")"):
            end = rest[0][1]  # "18 ." (the number and its full stop in separate spans)
            rest = rest[1:]
            token += row.chars[end - 1].c
        number = row.chars[first[0] : end]
        if (
            not rest
            or not _EXERCISE_NUMBER.match(token)
            or any(c.math or c.mono for c in number)
            or row.chars[rest[0][0]].x0 - number[-1].x1 < 0.3 * row.size
        ):
            out.append(row)
            continue
        remainder = _Row(row.chars[rest[0][0] :], column=row.column)
        _classify_row(remainder, document=document)
        if remainder.kind != "math":
            out.append(row)
            continue
        out.append(_Row(number, column=row.column, exercise_number=True))
        out.append(_Row(row.chars[rest[0][0] :], column=row.column))
    return out


def _lone_exercise_number(row: _Row, tokens: list[tuple[int, int]]) -> bool:
    """Is the row only an exercise number ("6.", "14)", also "6 ." in two spans), drawn in a
    text font? Where an exercise is only maths (a fraction, a system), the number sits on a
    row of its own beside or above the first line of it; read as a fragment it would be
    taken into the equation (`_math_neighbours`). A row that starts a list item
    (`row.marker`) already has its number."""
    if row.vertical or row.marker is not None or row.exercise_number or not 0 < len(tokens) <= 2:
        return False
    if not _EXERCISE_NUMBER.match("".join(c.c for c in row.ink)):
        return False
    return not any(c.math or c.mono for c in row.ink)


@dataclass(eq=False)
class _Line:
    """Maths rows of one line of a display equation: a main row, the numerators and
    denominators of its fractions, and the columns of an aligned row."""

    rows: list[_Row]
    rules: list[_Path]
    labels: list[_Row] = field(default_factory=list)

    @cached_property
    def box(self) -> BBox:
        return _union([*(r.bbox for r in self.rows), *(p.bbox for p in self.rules)])

    @cached_property
    def size(self) -> float:
        return max(r.size for r in self.rows)

    @cached_property
    def ink(self) -> list[_Char]:
        return sorted((c for r in self.rows for c in r.ink), key=lambda c: c.x0)

    @cached_property
    def relations(self) -> list[_Char]:
        """Relation signs at full size (not in a sub/superscript), left to right."""
        return [
            c
            for r in self.rows
            for c in r.ink
            if c.c in _ROW_RELATIONS and not r.is_script(c) and c.size >= 0.75 * self.size
        ]

    @cached_property
    def big_fraction(self) -> bool:
        """A fraction set at the size of the line (a display fraction): a glyph at the
        size of the line's text on either side of one of its bars. The fractions of a
        system's rows are set smaller than the rows' text."""
        text = max(c.size for c in self.ink)  # rows are sized by their majority: not self.size
        for rule in self.rules:
            x0, y0, x1, y1 = rule.bbox
            middle = (y0 + y1) / 2
            above = below = False
            relation = False  # "x + y = 2" over a rule: a step of a derivation, not a fraction
            for glyph in self.ink:
                centre_x, centre_y = (glyph.x0 + glyph.x1) / 2, (glyph.y0 + glyph.y1) / 2
                if (
                    x0 - 1 <= centre_x <= x1 + 1
                    and abs(centre_y - middle) <= 1.2 * text
                    and glyph.size >= 0.9 * text
                ):
                    above |= centre_y < middle
                    below |= centre_y > middle
                    relation |= glyph.c in _ROW_RELATIONS
            if above and below and not relation:
                return True
        return False

    @cached_property
    def opens_system(self) -> bool:
        """Only the brace of a system, standing left of its rows, level with their middle."""
        return all(c.c in _OPENING_BRACES for c in self.ink)

    @cached_property
    def ink_x1(self) -> float:
        """Right edge of the line's glyphs (not of the rules drawn with it)."""
        return max(c.x1 for c in self.ink)

    @cached_property
    def body_x0(self) -> float:
        """Left edge of the line without the brace that opens a system."""
        ink = self.ink
        return next((c.x0 for c in ink if c.c not in _OPENING_BRACES), ink[0].x0)

    @cached_property
    def complete(self) -> bool:
        """An equation on its own: a relation with something on both sides."""
        ink = self.ink
        return any(
            ink[0].x1 <= rel.x0 + 0.5 and ink[-1].x0 >= rel.x1 - 0.5 for rel in self.relations
        )

    @cached_property
    def continues(self) -> bool:
        """Starts with a relation or operator: the next line of the equation above."""
        return self.ink[0].c in _CONTINUATION_SIGNS

    @cached_property
    def goes_on_after_ink(self) -> bool:
        """Ends in a relation or operator that the next row takes up. Not the row of dots that
        ends a series ("x + x^2 + x^3 + ..."): they are set as multiplication signs."""
        ink = self.ink
        if ink[-1].c not in _CONTINUATION_SIGNS:
            return False
        dots = 0
        for c in reversed(ink):
            if c.c != "\u22c5":
                break
            dots += 1
        return dots < ELLIPSIS_DOTS

    @cached_property
    def relation_x(self) -> float | None:
        return min((c.x0 for c in self.relations), default=None)


def _same_line(a: _Row, b: _Row) -> bool:
    """Two maths rows side by side on one line, in the same column: on one baseline at
    most MAX_COLUMN_GAP font sizes apart (aligned columns, an operator in its own span),
    or a lone punctuation mark next to a row it overlaps vertically (Chromium sets the
    full stop after a fraction on a baseline of its own)."""
    if a.column != b.column:
        return False
    size = max(a.size, b.size)
    gap = max(a.x0 - b.x1, b.x0 - a.x1)
    columns = (2 if a.case_row and b.case_row else 1) * MAX_COLUMN_GAP
    if abs(a.baseline - b.baseline) <= 0.4 * size and gap <= columns * size:
        return True
    lone = a if _punctuation_only(a) else b if _punctuation_only(b) else None
    if lone is None or gap > 1.5 * size:
        return False
    other = b if lone is a else a
    return min(lone.y1, other.y1) - max(lone.y0, other.y0) >= 0.5 * lone.height


def _punctuation_only(row: _Row) -> bool:
    return all(c.c in ",.;:" for c in row.ink)


JOINED_INDENT = 2.5  # x font size: least space on each side of a display line of two formulas


def _demote_joined_rows(rows: Sequence[_Row]) -> None:
    """Take back the display reading of a row of two formulas and a word ("x = 1 and y = 2")
    that is not set off like a display.

    `_joined_formulas` reads such a row from its characters alone, and a line of a sentence
    can look so too: the tail of a paragraph that goes on over a page break ("... rate = k[A]
    with k = 6.7"). A display line is centred or indented with space on both sides: the row
    must start and end at least `JOINED_INDENT` font sizes inside the text of its column (the
    leftmost and rightmost ends of its running text lines); with no text to measure against,
    it stays text.
    """
    for row in rows:
        if row.kind != "math" or JOINED_REASON not in row.reasons:
            continue
        text = [
            t
            for t in rows
            if t.kind == "text" and t.column == row.column and not t.absorbed and len(t.clean) >= 30
        ]
        if text:
            left, right = min(t.x0 for t in text), max(t.x1 for t in text)
            if (
                row.x0 - left >= JOINED_INDENT * row.size
                and right - row.x1 >= JOINED_INDENT * row.size
            ):
                continue
        row.kind = "text"
        row.reasons = [r for r in row.reasons if r != JOINED_REASON]


def _equation_regions(
    rows: list[_Row], rules: list[_Path], *, document: _Document
) -> list[_Region]:
    """Group the page's maths rows into display equations; sets `row.region`.

    1. Lines: rows touching one fraction bar or radical (numerator, denominator,
       radicand) and the main row at the bar's height, plus rows side by side on one
       baseline, are one line. Equation numbers ("2.4", "(3)") are not in any line:
       each goes to the line it sits beside (`_number_beside`), nearest centre first.
    2. Equations: a line joins the equation above it when the gap is at most
       MAX_LINE_GAP x font size, they overlap horizontally, at most one of them has an
       equation number, and the line either continues the one above (starts with "="
       or an operator), or one of the two is not a whole equation (no relation with
       something on both sides), or both are whole equations with their first relations
       aligned (the rows of an aligned derivation). Two whole equations stacked close
       together but centred on their own ("time = d / v" over "d = v t") stay apart.
    """
    _demote_joined_rows(rows)
    maths = [
        r
        for r in rows
        if r.kind == "math" and not r.absorbed and not r.vertical and r.region is None
    ]
    if not maths:
        return []
    parent = list(range(len(maths)))
    position = {id(r): i for i, r in enumerate(maths)}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index = _RowIndex(maths)
    on_rule: dict[int, list[_Path]] = {}
    for rule in rules:
        near = _rule_rows(rule, index)
        if not near:
            continue
        middle = (rule.bbox[1] + rule.bbox[3]) / 2
        level = [
            r
            for r in index.near(middle, middle)
            if r.y0 <= middle <= r.y1
            and r.column == near[0].column
            and max(r.x0 - rule.bbox[2], rule.bbox[0] - r.x1) <= 4 * r.size
            and not (
                _is_equation_number(r.clean)
                and r.x0 - rule.bbox[2] >= _number_clearance(r) * r.size
            )
        ]
        members = [*near, *(r for r in level if r not in near)]
        for row in members:
            on_rule.setdefault(id(row), []).append(rule)
            parent[find(position[id(row)])] = find(position[id(members[0])])
    labels = [r for r in maths if id(r) not in on_rule and _is_equation_number(r.clean)]
    label_ids = {id(r) for r in labels}
    body = sorted((r for r in maths if id(r) not in label_ids), key=lambda r: r.baseline)
    window = index.largest  # rows of one line: a shared baseline, or a lone full stop
    for i, row in enumerate(body):
        for other in body[i + 1 :]:
            if other.baseline - row.baseline > window:
                break
            if _same_line(row, other):
                parent[find(position[id(other)])] = find(position[id(row)])
    groups: dict[int, list[_Row]] = {}
    for row in body:
        groups.setdefault(find(position[id(row)]), []).append(row)
    lines = [
        _Line(members, list({id(p): p for r in members for p in on_rule.get(id(r), [])}.values()))
        for members in groups.values()
    ]
    if document.grouping:
        lines = _join_connectives(lines, rows)
    # Numbers beside a line first, then the far ones (`_Row.far_number`), which go only to a
    # line with no number yet, and not to one level with a numbered line (it would split that
    # equation): a number that fits none was never maths.
    for label in sorted(labels, key=lambda r: r.far_number):
        centre = (label.y0 + label.y1) / 2
        beside = [
            line
            for line in lines
            if _number_beside(label, line.box, line.size, far=label.far_number)
            and not (label.far_number and _level_with_numbered(line, lines))
            and not line.goes_on_after_ink  # "x =   2.4": its right-hand side
        ]
        if not beside and document.grouping:
            # only a row that goes on stands beside it: the first row of a display in several,
            # level with its number, with no number yet, and with something after it
            beside = [
                line
                for line in lines
                if not line.labels
                and _number_beside(label, line.box, line.size)
                and _trailing_number(label, line)
                and _goes_on(label, line, lines, rows)
            ]
        # Another column's maths only when the number's own column has no content beside it.
        own_column = [ln for ln in beside if ln.rows[0].column == label.column]
        if not own_column and not _column_content_near(label, rows):
            own_column = [
                ln for ln in beside if _number_column(label, ln.rows[0].column, ln.box[2])
            ]
        beside = own_column
        if beside:
            nearest = min(beside, key=lambda ln: abs((ln.box[1] + ln.box[3]) / 2 - centre))
            nearest.labels.append(label)
        elif label.far_number:
            label.kind, label.far_number = "text", False
        elif under := _line_over_number(label, lines):
            under.labels.append(label)
        elif document.grouping and (below := _line_below_number(label, lines)):
            below.labels.append(label)
        else:
            lines.append(_Line([label], []))  # a number inside the maths, not beside it
    return _stack_lines(lines, document=document)


def _line_over_number(label: _Row, lines: list[_Line]) -> _Line | None:
    """The line whose number `label` is, when it is set right-aligned on the line below it
    (`_number_under`): the nearest line with no number yet that it stands under."""
    over = [
        line
        for line in lines
        if not line.labels
        and line.rows[0].column == label.column
        and _number_under(label, line.box, max(label.size, line.size))
    ]
    return min(over, key=lambda ln: label.y0 - ln.box[3]) if over else None


def _trailing_number(label: _Row, line: _Line) -> bool:
    """Is `label`, level with a line that ends in a sign that goes on, a bracketed number set
    `NUMBERED_TRAILING_GAP` font sizes or more to its right (`LAYOUT_DISPLAY_GROUPING`)?"""
    return bool(EQUATION_NUMBER.match(label.clean)) and label.x0 - line.box[
        2
    ] >= NUMBERED_TRAILING_GAP * max(label.size, line.size)


def _goes_on(label: _Row, line: _Line, lines: list[_Line], rows: list[_Row]) -> bool:
    """Does the display go on after `line`, so that the number level with it is the display's:
    a line right under it (at most `TRAILING_ROW_GAP` font sizes down, over part of its width),
    or rows between the line and the number (the cases of a brace)? A bracketed number after
    "3x + 2 =" with nothing under it or beside it is an answer or a mark of a worksheet."""
    size = max(label.size, line.size)
    if any(
        other is not line
        and other.rows[0].column == line.rows[0].column
        and 0 < other.box[1] - line.box[3] <= TRAILING_ROW_GAP * size
        and other.box[0] <= line.box[2]
        and other.box[2] >= line.box[0]
        for other in lines
    ):
        return True
    own = {id(r) for r in line.rows}
    for row in rows:
        if id(row) in own or row is label or row.column != line.rows[0].column:
            continue
        shared = min(row.y1, line.box[3]) - max(row.y0, line.box[1])
        if (
            line.box[2] - 1 <= row.x0
            and row.x1 <= label.x0 + 1
            and shared > 0.25 * min(row.y1 - row.y0, line.box[3] - line.box[1])
        ):
            return True
    return False


def _line_below_number(label: _Row, lines: list[_Line]) -> _Line | None:
    """The line whose number `label` is, when it is set on a row of its own right above the
    display at the display's left edge ("(10.18)" over its equation; `LAYOUT_DISPLAY_GROUPING`):
    the nearest line with no number yet that starts under it, within `NUMBER_ABOVE_GAP` font
    sizes of blank, and that the number does not stand beyond."""
    size = max(label.size, 1.0)
    below = [
        line
        for line in lines
        if line.rows[0].column == label.column
        and 0 <= line.box[1] - label.y1 <= NUMBER_ABOVE_GAP * size
        and label.x0 <= line.box[2]
    ]
    if not below or any(line.labels for line in below):
        return None
    if abs(label.x0 - min(line.box[0] for line in below)) > size:
        return None
    return min(below, key=lambda ln: (ln.box[1], ln.box[0]))


def _level_with_numbered(line: _Line, lines: list[_Line]) -> bool:
    """Is `line` at the height of a line that has an equation number (or has one itself)?"""
    return any(
        other.labels and other.box[1] < line.box[3] and line.box[1] < other.box[3]
        for other in lines
    )


def _join_connectives(lines: list[_Line], rows: Sequence[_Row]) -> list[_Line]:
    """Join two display formulas set on one line with a connective word between them.

    "P_A = c / c   and   P_B = c / c ," is one printed display with one number. The word
    is a row of its own (a text font, a gap of an em or more on either side), so it ends
    the line on its left and the line on its right starts a new one, and the "and" is left
    in the text after both: the reader meets the two equations and then "and". A connective
    (`_CONNECTIVES`, alone in its row) joins the formula before it and the formula after it
    when both stand on its baseline within `CONNECTIVE_GAP` font sizes, the first is a whole
    equation, and the second is one too (or, after "for", a short condition: "for all i").
    The word becomes a maths row of the line, so the display's crop, text layer and reading
    hold it.
    """
    for word in rows:
        if (
            word.kind != "text"
            or word.absorbed
            or word.vertical
            or word.marker is not None
            or word.region is not None
            or _clean(word.text).strip(" ,;.").lower() not in _CONNECTIVES
        ):
            continue
        size = max(word.size, 1.0)

        def level(line: _Line, word: _Row = word, size: float = size) -> bool:
            """The word sits at the height of the line's middle (a fraction's bar, a row's
            centre), inside its vertical extent."""
            middle = (line.box[1] + line.box[3]) / 2
            return (
                line.rows[0].column == word.column
                and word.y0 >= line.box[1] - 0.5 * size
                and word.y1 <= line.box[3] + 0.5 * size
                and abs((word.y0 + word.y1) / 2 - middle)
                <= max(0.6 * size, 0.2 * (line.box[3] - line.box[1]))
            )

        before = [
            ln
            for ln in lines
            if level(ln) and 0 <= word.x0 - ln.box[2] <= CONNECTIVE_GAP * size and ln.complete
        ]
        after = [
            ln
            for ln in lines
            if level(ln) and 0 <= ln.box[0] - word.x1 <= CONNECTIVE_GAP * size and ln.ink
        ]
        if not before or not after:
            continue
        left = min(before, key=lambda ln: word.x0 - ln.box[2])
        right = min(after, key=lambda ln: ln.box[0] - word.x1)
        condition = _clean(word.text).lower().startswith("for") and len(right.ink) <= 3
        if left is right or not (right.complete or condition):
            continue
        if left.labels and right.labels:
            continue  # two numbered equations
        word.kind = "math"
        merged = _Line(
            [*left.rows, word, *right.rows],
            [*left.rules, *(p for p in right.rules if p not in left.rules)],
            [*left.labels, *right.labels],
        )
        lines = [ln for ln in lines if ln is not left and ln is not right]
        lines.append(merged)
    return lines


def _joins_line(
    above: _Line,
    box: BBox,
    line: _Line,
    earlier: Sequence[_Line] = (),
    *,
    grouping: bool = False,
) -> bool:
    """Does `line` continue the display equation whose last line is `above` (box `box`)?

    A line that starts with "=" or an operator may sit a little further down (half as
    far again): nothing else starts a line that way. A line whose middle is above the
    bottom of the equation sits beside it, not under it: "f(t) =" level with the middle
    row of a cases brace, whose rows are whole equations ("1 if t > 0") of their own.
    `earlier` are the lines of the equation above `above`: a row of a system may line up
    with the one before the last ("z = 1" under "y - 12 z = -15" lines up with the first).
    """
    size = max(above.size, line.size)
    reach = MAX_LINE_GAP * size * (1.5 if line.continues else 1.0)
    if line.box[1] - box[3] > reach or line.rows[0].column != above.rows[0].column:
        return False
    if line.box[0] > box[2] + size or (line.box[2] < box[0] - size and not line.opens_system):
        return False
    if above.labels and line.labels:
        return False  # two numbered equations
    if line.continues or not (above.complete and line.complete):
        return True
    if (line.box[1] + line.box[3]) / 2 < box[3]:
        return True  # beside the equation: rows of a brace, a matrix or an array
    if min(above.size, line.size) <= SCRIPT_RATIO * size:
        return True  # limits under a big operator ("i = 1" under a sum) or over it
    if grouping and (_limits_of(above, line, size) or _limits_of(line, above, size)):
        return True  # the same at the size of the row (a paper that sets limits at 7 pt)
    for peer in (above, *earlier):  # one row of the equation to line up with is enough
        if _table_rows(peer, line, box, size):
            return True
        x0, x1 = peer.relation_x, line.relation_x
        if x0 is not None and x1 is not None and abs(x0 - x1) <= 0.3 * size:
            return True
    return False


LIMIT_OPERATORS = frozenset("\u222b\u222e\u2211\u220f\u2210\u22c3\u22c2")  # int oint sum prod ...
LIMIT_REACH = 0.8  # x font size: how far above or below a big operator its limits sit


def _limits_of(operator: _Line, limits: _Line, size: float) -> bool:
    """Is `limits` the limits of a big operator (sum, product, integral) in `operator`?

    The limits of a sum hang right under its sign (or sit right over it), centred on it
    and no wider than twice the sign. A paper that sets them at the size of the row they
    belong to gives `_joins_line` no size to tell them by.
    """
    centre = (limits.box[0] + limits.box[2]) / 2
    for c in operator.ink:
        if c.c not in LIMIT_OPERATORS:
            continue
        gap = limits.box[1] - c.y1 if limits.box[1] >= c.y1 - 0.3 * size else c.y0 - limits.box[3]
        if (
            -0.3 * size <= gap <= LIMIT_REACH * size
            and c.x0 - 0.5 * size <= centre <= c.x1 + 0.5 * size
            and limits.box[2] - limits.box[0] <= 2 * (c.x1 - c.x0) + size
        ):
            return True
    return False


def _table_rows(above: _Line, line: _Line, box: BBox, size: float) -> bool:
    """Are two whole equations the rows of one table (a system, worked steps)?

    A table sets its rows closer than separate displays are set (0.7 of a font size at
    most, where a display gets an em or more above and below) and gives them one edge or
    one centre: each row centred as OpenStax prints a system, or flush left or right.
    """
    if line.box[1] - box[3] > TABLE_ROW_GAP * size or above.big_fraction or line.big_fraction:
        return False  # "time = d / v" over "d = v t": two display equations, not rows
    tolerance = 0.3 * size
    left, right = (above.body_x0 - line.body_x0), (above.ink_x1 - line.ink_x1)
    return abs(left) <= tolerance or abs(right) <= tolerance or abs(left + right) <= 2 * tolerance


def _stack_lines(lines: list[_Line], *, document: _Document) -> list[_Region]:
    """Join lines into display equations top to bottom (see `_equation_regions`)."""
    regions: list[_Region] = []
    last: list[_Line] = []  # the lowest line of each region so far
    peers: list[list[_Line]] = []  # the lines of each region so far, but its first
    first: list[_Line] = []  # the first line of each region
    active: list[int] = []  # regions whose bottom is still within reach
    for line in sorted(lines, key=lambda ln: (ln.box[1], ln.box[0])):
        reach = line.box[1] - 2 * MAX_LINE_GAP * line.size  # beyond any _joins_line reach
        active = [k for k in active if regions[k].box[3] >= reach]
        host = next(
            (
                k
                for k in reversed(active)
                if _joins_line(last[k], regions[k].box, line, peers[k], grouping=document.grouping)
            ),
            None,
        )
        if host is None:
            regions.append(_Region(box=line.box))
            last.append(line)
            first.append(line)
            peers.append([])
            active.append(len(regions) - 1)
            host = len(regions) - 1
        region = regions[host]
        region.rows.extend(line.rows)
        region.rules.extend(p for p in line.rules if p not in region.rules)
        region.labels.extend(line.labels)
        peers[host].append(line)
        region.box = _union([region.box, line.box])
        if any(c.c.isalnum() for c in line.ink) or not last[host].ink:
            last[host] = line  # a stray mark does not stand for the line above
    if document.grouping:
        regions = _merge_continued_regions(
            regions, [[f, *p] for f, p in zip(first, peers, strict=True)]
        )
        regions = _absorb_contained_regions(regions)
    for region in regions:
        for row in [*region.rows, *region.labels]:
            row.region = region
    return regions


def _merge_continued_regions(regions: list[_Region], members: list[list[_Line]]) -> list[_Region]:
    """Join a region that starts with "=" or an operator to the display equation above it.

    Lines are stacked top to bottom, one by one, and the pieces of one printed row do not
    all reach the equation above: the right half of a long row ("... ds + ∫ ...") starts
    clear of the box of the row above, so it opens a region of its own, and the left piece
    of the row, the "= -1" that starts it, then joins that region (it is the most recent
    one). A region whose leftmost top line starts with a relation or operator is the next
    row of the display above when it sits within the usual line gap of that display (or
    overlaps its box: limits and numerators hang below), shares its column, overlaps it
    horizontally, and the two do not both carry an equation number.

    The same order leaves the right half of a row alone when that half is a bare fraction
    (no main row on the baseline to chain it to the rest): it opens a region of its own
    to the right of the row above it. It joins the region whose line ends level with it
    and at most `MAX_COLUMN_GAP` font sizes to its left (the reach of the columns of an
    aligned row), when at least one of the two lines is not a whole equation.
    """
    merged: set[int] = set()
    for j, region in enumerate(regions):
        lines = members[j]
        if region.forced or not lines or id(region) in merged:
            continue
        top = min(ln.box[1] for ln in lines)
        band = [ln for ln in lines if ln.box[1] <= top + 0.8 * ln.size and not ln.opens_system]
        lead = min(band, key=lambda ln: ln.box[0], default=None)
        size = max(ln.size for ln in lines)
        reach = MAX_LINE_GAP * 1.5 * size
        hosts = [
            i
            for i, above in enumerate(regions)
            if i != j
            and id(above) not in merged
            and not above.forced
            and members[i][0].rows[0].column == lines[0].rows[0].column
            and not (above.labels and region.labels)
            and (
                (
                    lead is not None
                    and lead.continues
                    and _continues_below(above, region, lead, size, reach)
                )
                or _beside_row(members[i], lines, size)
            )
        ]
        if not hosts:
            continue
        host = max(hosts, key=lambda i: regions[i].box[3])
        target = regions[host]
        target.rows.extend(region.rows)
        target.rules.extend(p for p in region.rules if p not in target.rules)
        target.labels.extend(region.labels)
        target.box = _union([target.box, region.box])
        members[host].extend(lines)
        merged.add(id(region))
    return [r for r in regions if id(r) not in merged]


CONTAINED_SHARE = 0.9  # of a piece's own area that lies inside the display that takes it in


def _absorb_contained_regions(regions: list[_Region]) -> list[_Region]:
    """Join a region that lies inside the box of another display equation to it.

    A tall bracket or brace set in pieces (the "⎛" of a big parenthesis whose lines are
    placed one by one), the limits under a sum and the double bars of a norm are each a
    line of their own to the stacking above, and the piece is no formula of its own: it sits
    inside the box of the formula it belongs to. A region with at least `CONTAINED_SHARE` of
    its area inside a larger one is read as a part of that one, unless both carry an equation
    number (two numbered displays are never one).
    """
    area = [(r.box[2] - r.box[0]) * (r.box[3] - r.box[1]) for r in regions]
    merged: set[int] = set()
    for j in sorted(range(len(regions)), key=lambda k: area[k]):
        piece = regions[j]
        if piece.forced or area[j] <= 0:
            continue
        for i in sorted(range(len(regions)), key=lambda k: area[k]):
            host = regions[i]
            if i == j or id(host) in merged or host.forced or area[i] <= area[j]:
                continue
            if host.labels and piece.labels:
                continue
            width = min(piece.box[2], host.box[2]) - max(piece.box[0], host.box[0])
            height = min(piece.box[3], host.box[3]) - max(piece.box[1], host.box[1])
            if width > 0 and height > 0 and width * height >= CONTAINED_SHARE * area[j]:
                host.rows.extend(piece.rows)
                host.rules.extend(p for p in piece.rules if p not in host.rules)
                host.labels.extend(piece.labels)
                merged.add(id(piece))
                break
    return [r for r in regions if id(r) not in merged]


def _continues_below(
    above: _Region, region: _Region, lead: _Line, size: float, reach: float
) -> bool:
    """`region` starts with a continuation sign under `above`, in its column's reach."""
    return (
        above.box[1] + 0.5 * size <= region.box[1]
        and region.box[1] - above.box[3] <= reach
        and above.box[1] < region.box[1]
        and region.box[0] <= above.box[2] + size
        and region.box[2] >= above.box[0] - size
        and lead.box[1] >= above.box[1] + 0.5 * size
    )


def _beside_row(host: Sequence[_Line], lines: Sequence[_Line], size: float) -> bool:
    """Does a line of `lines` start level with, and close to the right of, a line of `host`
    (at most `MAX_COLUMN_GAP` sizes), with one of the two not a whole equation?"""
    for m in host:
        for x in lines:
            if any(_is_equation_number(r.clean) for ln in (m, x) for r in ln.rows):
                continue  # a number beside a display is not a column of it
            gap = x.box[0] - m.box[2]
            level = m.box[1] <= (x.box[1] + x.box[3]) / 2 <= m.box[3]
            if (
                0 <= gap <= MAX_COLUMN_GAP * size
                and level
                and m.box[1] < x.box[1] + 0.5 * size
                and not (m.complete and x.complete)
            ):
                return True
    return False


def _contiguous_regions(ordered: list[_Row]) -> list[_Row]:
    """Reading order with each display equation's rows together, at the place of its
    first row, and equation numbers taken out of the flow."""
    members: dict[int, list[_Row]] = {}
    for row in ordered:
        if row.region is not None and row not in row.region.labels:
            members.setdefault(id(row.region), []).append(row)
    out: list[_Row] = []
    for row in ordered:
        region = row.region
        if region is None:
            out.append(row)
        elif id(region) in members:
            out.extend(members.pop(id(region)))
    return out
