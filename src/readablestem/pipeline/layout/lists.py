"""List markers: bullets (text or drawn), ordered markers confirmed by a sibling, roman numerals,
the columns of a list and its levels.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from readablestem.core.types import Kind
from readablestem.pipeline.layout.context import _PageGuard
from readablestem.pipeline.layout.glyphs import _BULLET_GLYPHS, _Char, _clean
from readablestem.pipeline.layout.shared import _Block, _Context, _Marker, _Path, _Row, _tokens

# Bullets only at the start of a line and followed by a space (LaTeX's second-level "\u2013",
# third-level "\u2217", Markdown's "-" and "*"); maths or punctuation anywhere else. They
# need a sibling (the same glyph starting another line at the same indent), because a
# wrapped sentence can start a line with a dash.
_LINE_START_BULLETS = frozenset("-\u2013\u2014*\u2217\u00b7\u2219\u22c5")

# Word's second-level bullet is the letter "o" set in Courier New: a plain letter, so no font
# table reads it as a bullet. It is one when the letter is monospaced and the text after it is
# not (code that starts a line with "o" is monospaced all through), and, like a dash, it needs
# a sibling.
_HOLLOW_BULLET = "\u25e6"

# Ordered list markers: "1.", "2)", "(3)", "a.", "b)", "(c)", "iv.", "(ii)", "A.", "IV)".
_ORDERED_MARKER = re.compile(
    r"^(?:\((?P<inner>\d{1,3}|[A-Za-z]|[ivxlc]{2,6}|[IVXLC]{2,6})\)"
    r"|(?P<plain>\d{1,3}|[A-Za-z]|[ivxlc]{2,6}|[IVXLC]{2,6})(?P<sep>[.)]))$"
)


MAX_MARKER_GAP = 3.0  # x font size: widest gap between a list marker and its text
# x font size: a bullet glyph at the start of a row is cut from the text it touches when that is
# set in another font and this far from it, or this far from it whatever the font (`_tokens` asks
# 0.3, and glyphs of one word are closer than 0.1: `glyph_math.NO_GAP`; `font_codes` asks 0.2).
BULLET_APART_OWN_FONT = 0.1
BULLET_APART = 0.2
MARKER_ALIGN = 3.0  # pt: sibling markers (or their item texts) start this close in x
MARKER_INDENT = 0.5  # x font size: a marker of another series set this far right opens a level
DRAWN_BULLET_RATIO = 0.6  # a drawn bullet is at most this x the row's font size


@dataclass(frozen=True, eq=False)
class _MarkerCandidate:
    """A row that starts like a list item, before siblings confirm it."""

    row: _Row
    marker: _Marker
    family: tuple[str, str]  # ("decimal" | "letter", "{}." | "{})" | "({})") or ("bullet", glyph)
    readings: tuple[tuple[str, int], ...]  # (style, ordinal) readings of an ordered marker
    x0: float  # left edge of the marker itself
    sure: bool  # needs no sibling: a bullet glyph, a drawn bullet, or "3." before a capital


_ROMAN_STEPS = (
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"),
    (1, "i"),
)  # fmt: skip


def roman_value(text: str) -> int | None:
    """ "iv" -> 4, "XII" -> 12; None unless `text` is a canonical roman numeral below 400."""
    lower = text.lower()
    if not lower or any(c not in _ROMAN for c in lower):
        return None
    total = 0
    for i, c in enumerate(lower):
        value = _ROMAN[c]
        total += -value if i + 1 < len(lower) and _ROMAN[lower[i + 1]] > value else value
    canonical, rest = "", total
    for number, letters in _ROMAN_STEPS:
        while rest >= number:
            canonical, rest = canonical + letters, rest - number
    return total if 0 < total < 400 and canonical == lower else None


def marker_readings(body: str) -> tuple[tuple[str, int], ...]:
    """Possible (style, ordinal) readings of an ordered marker's body.

    "3" -> decimal 3; "b" -> lower-alpha 2; "iv" -> lower-roman 4; "i" is both lower-alpha
    9 and lower-roman 1 (its siblings decide); "C" is upper-alpha 3 and upper-roman 100.
    """
    if body.isdigit():
        return (("decimal", int(body)),)
    readings: list[tuple[str, int]] = []
    case = "lower" if body.islower() else "upper"
    if len(body) == 1 and body.isascii() and body.isalpha():
        readings.append((f"{case}-alpha", ord(body.lower()) - ord("a") + 1))
    roman = roman_value(body)
    if roman is not None and (body.islower() or body.isupper()):
        readings.append((f"{case}-roman", roman))
    return tuple(readings)


def _ordered_marker(text: str) -> tuple[str, str] | None:
    """(form, body) of an ordered marker token: "(iv)" -> ("({})", "iv"), "3." -> ("{}.", "3")."""
    match = _ORDERED_MARKER.match(text)
    if not match:
        return None
    if match["inner"] is not None:
        return "({})", match["inner"]
    return "{}" + match["sep"], match["plain"]


def _bullet_apart(row: _Row, tokens: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], bool]:
    """`tokens` with a bullet glyph at the start of the row cut from the text it touches, and
    whether it was set so close that only a sibling can tell it from a word that starts with
    the sign. `_tokens` splits at 0.3 x the font size, and Word's Wingdings bullet, set as
    large as the text, leaves 0.23 with no space character in the text layer: the line came
    out as a paragraph that began with the bullet. A bullet with no gap at all ("\u25aaGrading")
    is cut too, and is `glued`."""
    if not tokens:
        return tokens, False
    start, end = tokens[0]
    chars = row.chars
    if end - start > 1 and chars[start].c in _BULLET_GLYPHS:
        gap = (chars[start + 1].x0 - chars[start].x1) / row.size
        own_font = chars[start].font != chars[start + 1].font
        apart = BULLET_APART_OWN_FONT if own_font else BULLET_APART
        if gap >= apart or (own_font and gap >= -apart):
            return [(start, start + 1), (start + 1, end), *tokens[1:]], gap < apart
    return tokens, False


BLANK_RUN = 3  # underscores in a blank to fill in (a drawn blank is written as five)


def lead_blank(row: _Row, tokens: list[tuple[int, int]]) -> bool:
    """Does the row open with a blank to fill in, typed or drawn ("___ 1. Osmosis"), and go on
    with at least a label and a word? (The blank is a run of underscores a token of its own.)"""
    if len(tokens) < 3:
        return False
    start, end = tokens[0]
    return end - start >= BLANK_RUN and all(c.c == "_" for c in row.chars[start:end])


def _marker_candidate(row: _Row, bullets: _DrawnBullets) -> _MarkerCandidate | None:
    """Does `row` start like a list item? A bullet glyph, an ordered marker ("a.", "(3)",
    "iv)", "3 ." as OpenStax prints it) or a drawn bullet just left of it, followed by
    the item's text within MAX_MARKER_GAP x font size."""
    if row.vertical:
        return None
    drawn = bullets.left_of(row)
    tokens, glued = _bullet_apart(row, _tokens(row))
    if drawn is not None:
        if not tokens:
            return None
        start = tokens[0][0]
        marker = _Marker(drawn[1], False, start, row.chars[start].x0)
        return _MarkerCandidate(row, marker, ("bullet", drawn[1]), (), drawn[0], True)
    if len(tokens) < 2:
        return None
    chars = row.chars
    blank = lead_blank(row, tokens)
    if blank:  # "___ 1. Osmosis": the label after the blank is the marker, the blank stays text
        tokens = tokens[1:]
    (m0, m1), rest = tokens[0], tokens[1:]
    first = chars[m0].c
    if m1 - m0 == 1 and first in _DASHES and _signed_value(chars[m0], chars[rest[0][0] :]):
        return None  # "- 40 m/s", "\u2013 5 \u00b0C": a negative value, not a dash bullet
    hollow = m1 - m0 == 1 and first == "o" and chars[m0].mono and not chars[rest[0][0]].mono
    if m1 - m0 == 1 and (first in _BULLET_GLYPHS or first in _LINE_START_BULLETS or hollow):
        if blank:
            return None
        glyph = _HOLLOW_BULLET if hollow else _clean(first)
        family: tuple[str, str] = ("bullet", glyph)
        readings: tuple[tuple[str, int], ...] = ()
        sure = first in _BULLET_GLYPHS and not glued
        text = glyph
    else:
        text = "".join(c.c for c in chars[m0:m1])
        if text.isdigit() and "".join(c.c for c in chars[slice(*rest[0])]) in (".", ")"):
            m1 = rest[0][1]  # "3 ." (OpenStax prints the number and its full stop apart)
            rest = rest[1:]
            text += chars[m1 - 1].c
        parsed = _ordered_marker(text)
        if parsed is None or not rest:
            return None
        form, body = parsed
        readings = marker_readings(body)
        if not readings:
            return None
        family = ("decimal" if body.isdigit() else "letter", form)
        following = chars[rest[0][0]].c
        sure = body.isdigit() and (following.isupper() or following in "(\"'\u201c")
    start = rest[0][0]
    if chars[start].x0 - chars[m1 - 1].x1 > MAX_MARKER_GAP * row.size:
        return None
    if not any(
        c.c.isalnum() or c.math or (sure and c.c not in _BULLET_GLYPHS) for c in chars[start:]
    ):
        return None  # a sure bullet may carry a lone operator ("◦ **"), nothing else is text
    marker = _Marker(text, family[0] != "bullet", start, chars[start].x0, start=m0 if blank else 0)
    return _MarkerCandidate(row, marker, family, readings, chars[m0].x0, sure)


class _ColumnRows:
    """One page's rows per column, top to bottom: the lines above and below a row."""

    def __init__(self, rows: Sequence[_Row]) -> None:
        self.columns: dict[str, list[_Row]] = {}
        for row in rows:
            if not row.vertical:
                self.columns.setdefault(row.column, []).append(row)
        for column in self.columns.values():
            column.sort(key=lambda r: r.baseline)
        self.baselines = {k: [r.baseline for r in v] for k, v in self.columns.items()}

    def neighbour(self, row: _Row, step: int) -> _Row | None:
        """The nearest row above (step -1) or below (+1) in `row`'s column that overlaps
        it in x."""
        rows, baselines = self.columns.get(row.column, []), self.baselines.get(row.column, [])
        if step < 0:
            k = bisect.bisect_left(baselines, row.baseline - 0.3 * row.size) - 1
            span = range(k, -1, -1)
        else:
            k = bisect.bisect_right(baselines, row.baseline + 0.3 * row.size)
            span = range(k, len(rows))
        return next((rows[i] for i in span if rows[i].x0 < row.x1 and rows[i].x1 > row.x0), None)

    def wraps_into(self, candidate: _MarkerCandidate, ctx: _Context) -> bool:
        """Is the candidate's row the next line of the paragraph above, which happens to
        start "2. The"? The line above is at the usual pitch and size, starts as far left
        as the marker or further right, and does not end a sentence or clause; and the
        line below does not hang at the item text's indent."""
        row = candidate.row
        above = self.neighbour(row, -1)
        if above is None:
            return False
        size = row.size
        pitch = row.baseline - above.baseline
        limit = max(1.3 * ctx.pitch * size / ctx.body, 1.25 * size)
        if abs(above.size - size) > max(1.0, 0.12 * size) or not 0.5 * size <= pitch <= limit:
            return False
        if candidate.x0 > above.x0 + 0.5 * size:
            return False  # the marker is indented past the text above
        if above.clean[-1:] in tuple(".:;!?"):
            return False
        below = self.neighbour(row, 1)
        hanging = (
            below is not None
            and below.baseline - row.baseline <= limit
            and abs(below.x0 - candidate.marker.x) <= 1.5
            and candidate.marker.x - candidate.x0 > 1.5
        )
        return not hanging


_DASHES = frozenset("-\u2013\u2014")  # line-start bullets that are also how minus is typed


def _signed_value(dash: _Char, after: list[_Char]) -> bool:
    """Is a line-start dash the sign of the value after it? It is when the value starts
    with a digit or a decimal point ("- 40 m/s"; Word turns a spaced hyphen into "\u2013"),
    or when the dash is set in a maths font (a minus sign)."""
    text = "".join(c.c for c in after).lstrip()
    return dash.math or text[:1].isdigit() or (text[:1] in ".," and text[1:2].isdigit())


class _DrawnBullets:
    """Small drawn shapes that can be list bullets (Chromium draws CSS disc markers as
    paths), indexed by vertical centre."""

    def __init__(self, paths: Sequence[_Path], body: float) -> None:
        marks = []
        for path in paths:
            x0, y0, x1, y1 = path.bbox
            if path.kind == "shape" and 0.5 <= max(x1 - x0, y1 - y0) <= 1.2 * body:
                glyph = ("\u2022" if path.curved else "\u25aa") if path.filled else "\u25e6"
                marks.append(((y0 + y1) / 2, path.bbox, glyph))
        marks.sort(key=lambda m: m[0])
        self.marks = marks
        self.centres = [m[0] for m in marks]

    def left_of(self, row: _Row) -> tuple[float, str] | None:
        """(left edge, glyph) of a bullet drawn just left of `row` at its height."""
        lo = bisect.bisect_left(self.centres, row.y0)
        hi = bisect.bisect_right(self.centres, row.y1)
        for _, (x0, y0, x1, y1), glyph in self.marks[lo:hi]:
            side = max(x1 - x0, y1 - y0)
            if side <= DRAWN_BULLET_RATIO * row.size and 0 <= row.x0 - x1 <= 2.5 * row.size:
                return x0, glyph
        return None


def _list_markers(
    rows_by_page: dict[int, list[_Row]],
    paths: dict[int, list[_Path]],
    ctx: _Context,
    guard: _PageGuard,
) -> None:
    """Set `row.marker` on every row that starts a list item, in all analysed pages.

    A bullet glyph or a drawn bullet is enough, and so is a number before a capital
    letter ("3. Measure"), unless that row is the next line of a wrapped paragraph
    (`_ColumnRows.wraps_into`). Letters, roman numerals, bare numbers and line-start
    dashes need a sibling: another row with the same kind of marker, the next or
    previous value ("b." for "a."; the same glyph for a dash) and the marker or the item
    text starting within MARKER_ALIGN in x, anywhere in the document (lists cross
    pages). "A ball rolls", "2.3 Speed", "2.5 metres", a lone "B. Smith", "... is
    exactly" / "2. The heavier ball ...", a spaced minus ("- 40 m/s") or a sentence
    wrapped onto a new line after a dash do not start lists.

    The candidates are found page by page (a page that raises, `guard`, has none) and judged
    together; the rows of a page that failed get no marker.
    """
    candidates: list[_MarkerCandidate] = []
    for number, rows in rows_by_page.items():
        with guard(number):
            bullets = _DrawnBullets(paths.get(number, []), ctx.body)
            found = [c for row in rows if (c := _marker_candidate(row, bullets)) is not None]
            if any(c.sure and c.readings for c in found):
                columns = _ColumnRows(rows)
                found = [
                    replace(c, sure=False)
                    if c.sure and c.readings and columns.wraps_into(c, ctx)
                    else c
                    for c in found
                ]
            candidates.extend(found)
    index: dict[tuple, tuple[list[float], list[float]]] = {}
    for candidate in candidates:
        for reading in candidate.readings or (("bullet", 0),):
            lists = index.setdefault((candidate.family, reading), ([], []))
            lists[0].append(candidate.x0)
            lists[1].append(candidate.marker.x)
    for lists in index.values():
        lists[0].sort()
        lists[1].sort()

    def aligned(key: tuple, candidate: _MarkerCandidate, needed: int) -> bool:
        lists = index.get(key)
        if lists is None:
            return False
        for values, x in ((lists[0], candidate.x0), (lists[1], candidate.marker.x)):
            near = bisect.bisect_right(values, x + MARKER_ALIGN)
            if near - bisect.bisect_left(values, x - MARKER_ALIGN) >= needed:
                return True
        return False

    # A capital letter and a full stop starts a line of an author list as readily as an item
    # ("A. Smith, B. Jones"), and some line of a long document has the next letter: the sibling
    # of such a marker is the item just before or just after it.
    capitals = [c for c in candidates if _capital_letter(c)]
    around = {
        id(c): (capitals[k - 1] if k else None, capitals[k + 1] if k + 1 < len(capitals) else None)
        for k, c in enumerate(capitals)
    }

    def beside(candidate: _MarkerCandidate) -> bool:
        value = _capital_letter(candidate)
        return any(
            other is not None
            and _capital_letter(other) == value + step
            and (
                abs(other.x0 - candidate.x0) <= MARKER_ALIGN
                or abs(other.marker.x - candidate.marker.x) <= MARKER_ALIGN
            )
            for other, step in zip(around[id(candidate)], (-1, 1), strict=True)
        )

    for candidate in candidates:
        if candidate.readings:
            confirmed = any(
                aligned((candidate.family, (style, value + step)), candidate, 1)
                for style, value in candidate.readings
                for step in (-1, 1)
                if not (style == "upper-alpha" and _capital_letter(candidate))
            ) or (bool(_capital_letter(candidate)) and beside(candidate))
        else:  # the same bullet glyph on another row (this one counts itself)
            confirmed = aligned((candidate.family, ("bullet", 0)), candidate, 2)
        if candidate.sure or confirmed:
            candidate.row.marker = candidate.marker


def _capital_letter(candidate: _MarkerCandidate) -> int:
    """The place in the alphabet of a marker that is a capital letter and a full stop ("A." is 1),
    else 0."""
    text = candidate.marker.text
    if candidate.family != ("letter", "{}.") or len(text) != 2 or not text[0].isupper():
        return 0
    return ord(text[0]) - 64


def _list_levels(blocks: Iterable[_Block], tolerance: float) -> dict[int, int]:
    """Nesting level (1 = top) per list-item block id, from the indent of the item text.

    Consecutive items in reading order form one list (display equations inside an item
    do not end it; anything else does). A deeper indent opens a level, a shallower one
    closes levels until one matches. Indents are measured from the left edge of the
    item's column (`_Block.origin`), so a list that goes on in the next column or on a
    page with another margin keeps its levels.

    What the indent leaves open is read from the markers. A numbered series that starts again
    at its first value ("(a)" after "1.", "(i)" after "(a)") is a list under the item above
    even where the texts start at one indent (the answer lines of an exam). A list that a
    paragraph, a figure or a table cut ("(i)" ... "negative" ... "(ii)") goes on where the
    next item takes up the numbering of an open level: its levels are kept for that item.
    """
    levels: dict[int, int] = {}
    stack: list[_Level] = []  # the open levels: the x of the text, the marker series
    cut: list[_Level] = []  # the levels a block that is not an item closed, to be taken up
    options: float | None = None  # the x the options of the grid being read are placed at
    before: tuple[str, float] | None = None  # (series, x of the marker) of the item before
    for block in blocks:
        if block.joined:  # the rest of the item before it, on the next page: no element of its own
            continue
        if block.marker is None:
            if block.kind != Kind.EQUATION:
                cut = stack or cut
                stack = []
                before = None
            continue
        x = block.marker.x - block.origin
        series, own = _series(block.marker), _marker_x(block)
        number = _numbered(block.marker, stack[-1] if stack else cut[-1] if cut else None)
        if not stack and cut and number is not None and block.marker.option is None:
            taken = _taken_up(cut, number, x, tolerance)
            stack = cut[: taken + 1] if taken is not None else []
        cut = []
        # The marker of another series set right of the one above opens a level even where the
        # item texts agree: sub-bullets under "1)" in slides set larger than the text indent.
        nested = (
            before is not None
            and own is not None
            and block.marker.option is None
            and series != before[0]
            and own - before[1] > MARKER_INDENT * block.rows[0].size
        )
        before = (series, own) if own is not None else None
        if block.marker.option is None:
            options = None
        elif options is None:
            # The options of a grid are one list below the item they follow, whichever column
            # each is set in (a row-major grid reads A, B across, C, D below), and all at one level.
            options = max(x, stack[-1].x + 2 * tolerance) if stack else x
            x = options
        else:
            x = options
        while stack and (
            x < stack[-1].x - tolerance
            or (len(stack) > 1 and abs(x - stack[-2].x) < abs(x - stack[-1].x))
        ):
            stack.pop()
        # "(a)" after "1.": a series that starts again is a list under the item above.
        starts = (
            block.marker.option is None
            and number is not None
            and number.value == 1
            and bool(stack)
            and stack[-1].number is not None
            and stack[-1].number.key != number.key
        )
        # Sub-bullets whose text starts on the text edge of their numbered parent (Word and
        # Google Docs set them so): the x does not tell which open level "2)" goes back to, its
        # series does.
        if (
            stack
            and not nested
            and not starts
            and block.marker.option is None
            and not stack[-1].is_series(series, number)
        ):
            for depth in range(len(stack) - 2, -1, -1):
                if stack[depth].is_series(series, number) and abs(x - stack[depth].x) <= tolerance:
                    del stack[depth + 1 :]
                    break
        if not stack or x > stack[-1].x + tolerance or nested or starts:
            stack.append(_Level(x, series, number))
        else:
            stack[-1] = _Level(stack[-1].x, stack[-1].series, number)
        levels[id(block)] = len(stack)
    return levels


@dataclass(frozen=True)
class _Number:
    """An ordered marker read in its series: the form and style ("({})|lower-roman"), the value."""

    key: str
    value: int


@dataclass(frozen=True)
class _Level:
    """One open level of `_list_levels`: the x of its item text, its marker series, and the
    last numbered marker on it."""

    x: float
    series: str
    number: _Number | None = None

    def is_series(self, series: str, number: _Number | None) -> bool:
        """Is a marker of this series and place on this level? Numbers are compared as places in a
        series ("(i)" is not "(a)"), anything else by its kind."""
        if self.number is not None and number is not None:
            return self.number.key == number.key
        return self.series == series


def _numbered(marker: _Marker, level: _Level | None) -> _Number | None:
    """The marker of an ordered item as a place in a series, or None for a bullet. "i" is the
    letter after "h" and the roman numeral one otherwise; the level above, when it is numbered,
    decides which."""
    parsed = _ordered_marker(marker.text) if marker.ordered else None
    if parsed is None:
        return None
    form, body = parsed
    readings = marker_readings(body)
    if not readings:
        return None
    if len(readings) > 1:
        above = level.number if level is not None else None
        follows = [
            r
            for r in readings
            if above is not None and above.key == f"{form}|{r[0]}" and r[1] == above.value + 1
        ]
        readings = tuple(follows or [r for r in readings if r[1] == 1] or readings[:1])
    style, value = readings[0]
    return _Number(f"{form}|{style}", value)


def _taken_up(cut: list[_Level], number: _Number, x: float, tolerance: float) -> int | None:
    """The depth, among the levels a block closed, whose numbering the item takes up: the next
    value of the same series at that level's indent."""
    for depth in range(len(cut) - 1, -1, -1):
        last = cut[depth].number
        if (
            last is not None
            and last.key == number.key
            and last.value + 1 == number.value
            and abs(x - cut[depth].x) <= tolerance
        ):
            return depth
    return None


def _series(marker: _Marker) -> str:
    """What kind of marker this is, so that "1)" and "2)" are one series and "(a)", "o" or
    "\u25aa" are others: a digit stands for any number, a letter for any letter."""
    if not marker.ordered:
        return marker.text
    return "".join("0" if c.isdigit() else "a" if c.isalpha() else c for c in marker.text)


def _marker_x(block: _Block) -> float | None:
    """Left edge of the item's marker, from the left edge of its column; None when the marker
    is drawn beside the row and not in it."""
    marker = block.marker
    if marker is None or marker.end <= marker.start:
        return None
    return block.rows[0].chars[marker.start].x0 - block.origin


_ROMAN = {"i": 1, "v": 5, "x": 10}
