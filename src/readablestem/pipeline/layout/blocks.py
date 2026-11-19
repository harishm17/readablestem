"""Blocks: rows grouped into paragraphs, headings, list items, captions, code, contents lists,
hanging entries and display-equation blocks, with the text of each (`_classify_run`,
`_paragraph_pieces`) and the bold-letter runs the blocks read.
"""

from __future__ import annotations

import bisect
import math
import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from readablestem.core.mathchars import base_char, char_variant, styled_char
from readablestem.core.notation import canonical_from_parts
from readablestem.core.types import BBox, Kind
from readablestem.pipeline import equation_images, hyphens, say_once, script_marks
from readablestem.pipeline import notices as notice_rules
from readablestem.pipeline import options as run_options
from readablestem.pipeline.layout.access import CROP_PAD
from readablestem.pipeline.layout.context import _PLAIN, _Document
from readablestem.pipeline.layout.drawings import _OPENING_BRACES
from readablestem.pipeline.layout.figures import (
    CAPTION,
    CODE_MIN_GLYPHS,
    HEADING_SIZE_RATIO,
    _continues,
    _RightEdges,
)
from readablestem.pipeline.layout.glyphs import _TEXT_MAP, _Char, _clean, _regular_letters, _union
from readablestem.pipeline.layout.inline_runs import (
    CAPTION_MAX_GAP,
    PICTURE_CHAR,
    ScriptKind,
    _classify_row,
    _inline_runs,
    _loose_scripts,
    _script_glyphs,
    _script_kinds,
)
from readablestem.pipeline.layout.lists import _ROMAN, _ordered_marker, marker_readings
from readablestem.pipeline.layout.rows import _join_rows
from readablestem.pipeline.layout.shared import (
    _MATH_WORDS,
    _ROW_RELATIONS,
    _SUPERSCRIPT_FLAG,
    SCRIPT_RATIO,
    _Block,
    _CharInfo,
    _Context,
    _Figure,
    _Inline,
    _lead_end,
    _Marker,
    _Row,
    _Run,
    _tokens,
)
from readablestem.pipeline.notation_memory import (
    BOLD_LETTER_REASON,
    EVIDENCE_COURSE,
    EVIDENCE_MATHS_FONT,
    bold_letter_latex,
)
from readablestem.pipeline.parse import equation_score

MAX_HEADING_CHARS = 200
MAX_HEADING_ROWS = 3
MAX_BOLD_HEADING_CHARS = 120
MAX_BOLD_HEADING_ROWS = 2
SET_OFF_FLAG = 1 << 20  # `_Char.flags` of a blank layout made between a note mark and a word
EQUATION_IMAGE_REASON = equation_images.REASON
SPACE_AFTER_SCRIPT = 0.3  # a gap this wide (x the glyph size) beside a script is a space

# Evidence codes on heading elements (`Element.reasons`). Content-free; heading_step reads
# them to decide confidence and review status.
REASON_LARGER_FONT = "larger font"
REASON_BOLD_BODY = "bold body-size line"
REASON_NUMBERED = "numbered section"
REASON_NUMBERED_LINE = "standalone numbered line"

# A capital letter alone on a line in the sequence of an index or a glossary (`_letter_rows`):
# a heading one level under the section it is in (`heading_levels`).
REASON_LETTER = "letter heading"
REASON_LIST_NUMBER = "list-style number"

# "2.3 Speed", "Chapter 2: Motion", "IV. Results", "A) Units". Letters and roman numerals
# need a separator or a prefix ("A ball rolls" is not numbered).
SECTION_NUMBER = re.compile(
    r"^(?P<prefix>(?:Chapter|CHAPTER|Section|SECTION|Unit|UNIT|Part|PART|Lecture|LECTURE"
    r"|Module|MODULE|Appendix|APPENDIX)\s+)?"
    r"(?P<num>\d{1,3}(?:\.\d{1,3}){0,4}|[IVX]{1,5}|[A-Z])(?P<sep>[.:)]?)(?:\s+|$)"
)


def section_number(text: str) -> tuple[int, bool] | None:
    """(depth, list_like) when `text` starts with a section number, else None.

    "2.3 Speed" -> (2, False); "Chapter 2" -> (1, False); "1. Measure" -> (1, True)
    because "1." also starts list items; "A) Units" -> (1, True).
    """
    match = SECTION_NUMBER.match(text.strip())
    if not match:
        return None
    num, sep, prefix = match["num"], match["sep"], match["prefix"]
    if num[0].isdigit():
        depth = num.count(".") + 1
        return depth, depth == 1 and sep in (".", ")") and not prefix
    if not prefix and not sep:
        return None
    return 1, len(num) == 1 and num not in "IVX" and not prefix


# Words that make "12 Metres", "2.5 Kilograms" or "10 October 2026" a quantity or a date,
# not a numbered section title.
_QUANTITY_WORDS = frozenset(
    (
        *("metre", "meter", "kilometre", "kilometer", "centimetre", "centimeter"),
        *("millimetre", "millimeter", "gram", "kilogram", "second", "minute", "hour"),
        *("day", "week", "month", "year", "newton", "joule", "watt", "volt", "ampere", "amp"),
        *("kelvin", "degree", "litre", "liter", "percent", "mile", "foot", "feet", "inch"),
        *("inches", "pound", "ounce"),
        *("january", "february", "march", "april", "may", "june", "july", "august"),
        *("september", "october", "november", "december"),
        *("jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"),
    )
)


def section_heading_number(text: str) -> bool:
    """Does `text` start like a numbered section title, not a quantity, date or list item?

    Body-size headings need this before a number counts as strong evidence: "2.3 Units of
    Speed", "Chapter 2 Motion", "IV. Results" pass; "2 kg", "12 Metres", "0.5 s",
    "10 October 2026", "9.8 metres per second" and "1. Measure the ramp" do not. A bare
    integer needs a prefix word ("Chapter 3"); a decimal must not start with "0." and
    must be followed by a capitalised word that is not a unit or a month.
    """
    match = SECTION_NUMBER.match(text.strip())
    if not match:
        return False
    num, sep, prefix = match["num"], match["sep"], match["prefix"]
    if not prefix:
        if num[0].isdigit() and ("." not in num or num.startswith("0")):
            return False  # "2 kg", "12 Metres", "0.5 s"; "1." is a list item
        if not num[0].isdigit() and (not sep or sep == ")" or (len(num) == 1 and num not in "IVX")):
            return False  # "A) Units", "B. Smith"
    words = text.strip()[match.end() :].split()
    if not words:
        return bool(prefix)  # "Chapter 2" on its own line
    first = words[0].strip("\"'(“")
    letters = sum(c.isalpha() for c in " ".join(words))
    if not first[:1].isupper() or letters < 3:
        return False
    stem = "".join(c for c in first.lower() if c.isalpha())
    unit = stem in _QUANTITY_WORDS or stem.rstrip("s") in _QUANTITY_WORDS
    after = words[1] if len(words) > 1 else ""
    return not (unit and not after[:1].isupper())  # "Second Law" is a title, "Seconds for" not


# With FEATURE_NOTATION_MEMORY on, a single bold letter in running text ("the projection
# of a vector **b**") becomes an inline equation that the equation step fixes without a
# model call (`pipeline.notation_memory.bold_letter_fix`). docs/layout.md "Bold letters
# in prose" has the rules and their measured precision and recall.

# What may touch a lone letter on either side: a space, the row's edge or punctuation. A
# letter, digit, hyphen or slash next to it makes it part of a word ("x-axis", "a2").
_LETTER_EDGES = frozenset(",.;:!?()[]'\"\u2019\u201c\u201d")
_LABEL_AFTER = frozenset(").:")  # "a)", "A." or "b:" starting a paragraph: a label


@dataclass
class _BoldLetters:
    """Bold-letter state while one paragraph's rows are cut into pieces."""

    course: frozenset[str]  # the course's notation keys (`NotationEntry.canonical`)
    first: bool = True  # the next row is the paragraph's first
    before: str = ""  # the last non-blank character of the rows so far

    @classmethod
    def start(cls) -> _BoldLetters | None:
        """None unless the conversion has the feature on (`pipeline.options`)."""
        current = run_options.current()
        if not current.notation_memory:
            return None
        return cls(frozenset(e.canonical for e in current.notation))


def _bold_key(text: str) -> str | None:
    """The notation key of one bold letter (bold italic folds to bold), else None."""
    if bold_letter_latex(text) is None:
        return None
    return canonical_from_parts(base_char(text), char_variant(text))


def _bold_word(chars: list[_Char], start: int, end: int) -> bool:
    """Two bold letters in a row between `start` and `end`: a bold word."""
    run = 0
    for c in chars[start:end]:
        run = run + 1 if c.bold and c.c.isalpha() else 0
        if run >= 2:
            return True
    return False


def _bold_letter_runs(
    row: _Row,
    runs: list[tuple[int, int, list[str]]],
    state: _BoldLetters | None,
    *,
    document: _Document,
) -> list[tuple[int, int, list[str]]]:
    """`runs` (`_inline_runs`) plus the row's lone bold letters, by character index.

    A maths run that is one bold letter (a bold omega in CMMIB, a maths bold code point)
    is marked `BOLD_LETTER_REASON` with `EVIDENCE_MATHS_FONT`. Any other bold Latin
    letter that stands alone (a space, the row's edge or punctuation on both sides) is
    a candidate, except: a bold word on either side (a bold phrase: "Part A", a bold
    sentence); a label ("(b)" anywhere, "a)" or "A." starting the paragraph); a capital
    starting a sentence ("A" is also the article), unless the course lists it. A
    candidate the course lists keeps `EVIDENCE_COURSE`; the others wait for evidence
    from the whole document (`_settle_bold_letters`). Unchanged when the feature is off.
    """
    if state is None:
        return runs
    chars = row.chars
    info = _CharInfo.of(row, document=document)
    covered: set[int] = set()
    out: list[tuple[int, int, list[str]]] = []
    for start, end, reasons in runs:
        covered.update(range(start, end))
        ink = [c for c in chars[start:end] if not c.blank]
        if len(ink) == 1 and _bold_key(_clean(ink[0].styled)) is not None:
            reasons = [BOLD_LETTER_REASON, EVIDENCE_MATHS_FONT]
        out.append((start, end, reasons))
    ink = [i for i, c in enumerate(chars) if not c.blank]
    tokens = _tokens(row)  # (start, end) of each run of non-blank characters
    starts = [start for start, _ in tokens]
    for pos, i in enumerate(ink):
        c = chars[i]
        if i in covered or not c.bold or info.classes[i] != "L" or info.script[i]:
            continue
        # Its token (split at blanks and gaps) is the letter and punctuation only, and
        # the tokens either side hold no bold word.
        token = bisect.bisect_right(starts, i) - 1
        start, end = tokens[token]
        if any(chars[k].c not in _LETTER_EDGES for k in range(start, end) if k != i):
            continue  # part of a word ("x-axis", "a2", "vectors")
        sides = [tokens[k] for k in (token - 1, token + 1) if 0 <= k < len(tokens)]
        if any(_bold_word(chars, start, end) for start, end in sides):
            continue
        before = chars[ink[pos - 1]].c if pos else state.before
        after = chars[ink[pos + 1]].c if pos + 1 < len(ink) else ""
        then = chars[ink[pos + 2]].c if pos + 2 < len(ink) else ""
        opening = state.first and pos == 0
        if (before in "([" and after in ")]") or (opening and after in _LABEL_AFTER):
            continue  # a label
        if after in ".)" and (not then or then.isupper()) and (not before or before in ".!?:"):
            continue  # a run-in label after a sentence: "... question. a. Find the sum."
        key = styled_char(c.c, "bold")
        in_course = key in state.course
        if c.c.isupper() and (opening or not before or before in _SENTENCE_END) and not in_course:
            continue  # a capital starting a sentence
        out.append((i, i + 1, [BOLD_LETTER_REASON, *([EVIDENCE_COURSE] if in_course else [])]))
    state.first = False
    if ink:
        state.before = chars[ink[-1]].c
    return sorted(out)


def _dominant_size(rows: Sequence[_Row]) -> float:
    counts: Counter[float] = Counter()
    for row in rows:
        for c in row.ink:
            counts[round(c.size * 2) / 2] += 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if counts else 0.0


def _heading_sized(row: _Row, ctx: _Context) -> bool:
    return row.size >= HEADING_SIZE_RATIO * ctx.body


HANGING_ENTRIES = 3  # entries in a row that make a hanging indent a list of entries
HANGING_REACH = (1.0, 4.0)  # em: how far right of an entry's first line its other lines start
LEAD_ENTRIES = 3  # paragraphs in a row that start with a lead term, to make a list of entries
LETTER_SEQUENCE = 3  # letters in a row that make capitals alone on a line the sections of an index


def _starts_entry(prev: _Row, row: _Row, run: _Run, edges: _RightEdges) -> bool:
    """Does `row` start an entry of a glossary or a list of terms: a lead term (`_lead_end`)
    at the left edge of the paragraph `run`, after a line that does not end in that style. (An
    italic title that starts a line inside an entry of a bibliography is indented with it; an
    italic "et al." after a line that runs full and stops mid-sentence is part of it.)"""
    found = _lead_end(row)
    if found is None or row.marker is not None or row.x0 > run.box[0] + 0.5 * row.size:
        return False
    if prev.clean[-1:] not in '.:;?!)”"' and prev.x1 >= edges.of(prev) - 1.5 * prev.size:
        return False
    last = next((c for c in reversed(prev.chars) if not c.blank), None)
    return last is not None and not found[1](last)


def _letter_rows(pages: dict[int, list[_Row]], ctx: _Context) -> None:
    """Mark the capital letters that head the sections of an index or a glossary (`_Row.letter`).

    One capital letter alone on its line, bold or larger than the body text, with the line
    right under it an entry that starts with that letter, and at least `LETTER_SEQUENCE`
    different letters set that way in one size and typeface through the document. The test
    reads the line under the letter, not the order of the page's columns, so the letters of
    an index in three columns are found where they stand. A letter on its own line anywhere
    else is the label of a figure's panel or a variable, and is never marked."""
    found: dict[tuple[int, str], list[_Row]] = {}
    for rows in pages.values():
        lines = [r for r in rows if not r.vertical]
        for row in lines:
            if not (
                len(row.ink) == 1
                and "A" <= row.ink[0].c <= "Z"
                and not row.ink[0].math
                and (row.bold or row.size >= HEADING_SIZE_RATIO * ctx.body)
            ):
                continue
            middle, centre = (row.y0 + row.y1) / 2, (row.x0 + row.x1) / 2
            under = min(
                (r for r in lines if r.y0 > middle and r.x0 <= centre <= r.x1),
                key=lambda r: r.y0,
                default=None,
            )
            if under is not None and _first_letter(under.clean) == row.ink[0].c:
                found.setdefault((round(row.size * 2), row.family), []).append(row)
    for members in found.values():
        if len({row.ink[0].c for row in members}) >= LETTER_SEQUENCE:
            for row in members:
                row.letter = True


def _first_letter(text: str) -> str:
    """The first letter A to Z of the text, as a capital with any accent taken off ("" for
    none): an index files "2-butene" and "(E)-alkene" under the first letter they have."""
    for c in unicodedata.normalize("NFD", text):
        if "A" <= c.upper() <= "Z":
            return c.upper()
    return ""


def _hangs(group: Sequence[_Row]) -> list[tuple[int, int, float]]:
    """The entries of consecutive lines of one column, as (first line, after the last line,
    indent): a line at the left edge of the lines followed by one or more that start the
    same distance further right (`HANGING_REACH`)."""
    left = min(r.x0 for r in group)
    kinds = [
        "L"
        if r.x0 - left <= 0.5 * r.size
        else "I"
        if HANGING_REACH[0] * r.size <= r.x0 - left <= HANGING_REACH[1] * r.size
        else "?"
        for r in group
    ]
    out: list[tuple[int, int, float]] = []
    i = 0
    while i < len(group):
        end = i + 1
        if kinds[i] == "L" and end < len(group) and kinds[end] == "I":
            indent = group[end].x0 - left
            while (
                end < len(group)
                and kinds[end] == "I"
                and abs(group[end].x0 - left - indent) <= 0.5 * group[end].size
            ):
                end += 1
            out.append((i, end, indent))
        i = end
    return out


def _flows(entry: Sequence[_Row], edges: _RightEdges) -> bool:
    """Do the lines of an entry set with a hanging indent run on from one to the next: every
    line but the last reaches the right edge or stops where the next line's first word no
    longer fits (text set ragged), the first line when it is followed by only one line, and
    otherwise not the first, which may be a short term. The lines of a contents list, nested
    by indent, each stop where their own title does."""
    first = 1 if len(entry) > 2 else 0
    return all(
        row.x1 >= edges.of(row) - 1.5 * row.size
        or row.x1 + 0.25 * row.size + _first_word(following) > edges.of(row)
        for row, following in pairwise(entry[first:])
    )


def _one_line(row: _Row, left: float, edges: _RightEdges) -> bool:
    """Is the line an entry of one line between entries set with a hanging indent: at their
    left edge, and stopping short of the right edge (a line of running text reaches it)?"""
    return row.x0 - left <= 0.5 * row.size and row.x1 < edges.of(row) - 1.5 * row.size


def _first_word(row: _Row) -> float:
    """How wide the first word of the line is."""
    start = next((i for i, c in enumerate(row.chars) if not c.blank), None)
    if start is None:
        return 0.0
    end = next((i for i in range(start, len(row.chars)) if row.chars[i].blank), len(row.chars))
    return row.chars[end - 1].x1 - row.chars[start].x0


def _hanging_entries(rows: Sequence[_Row], edges: _RightEdges) -> dict[int, int]:
    """The rows of entries set with a hanging indent, by `id(row)`, each with its entry's
    number: the first row of an entry starts a block and the others go on from it.

    Entries are `HANGING_ENTRIES` or more in a row, each a line at the left edge of its lines
    followed by lines that all start the same distance further right, the same distance for
    every entry of the row (a bibliography, an index, a glossary). A paragraph with an
    indented first line has its lines the other way round; the entries of one or two lines
    that look like it, set with the short line first, count only when their first line runs
    to the right edge. The line after the last entry, if it is at the left edge, starts one
    too."""
    found: dict[int, int] = {}
    entry = 0

    def settle(group: Sequence[_Row]) -> None:
        nonlocal entry
        chains: list[list[tuple[int, int, float]]] = [[]]
        left = min(r.x0 for r in group)
        for item in _hangs(group):
            chain = chains[-1]
            between = range(chain[-1][1], item[0]) if chain else range(0)
            if chain and (
                abs(item[2] - chain[0][2]) > 0.5 * group[item[0]].size
                or not all(_one_line(group[i], left, edges) for i in between)
            ):
                chains.append(chain := [])
            chain += [(i, i + 1, item[2]) for i in between]  # entries of one line
            chain.append(item)
        for chain in chains:
            if sum(end - first > 1 for first, end, _ in chain) < HANGING_ENTRIES or not all(
                _flows(group[first:end], edges) for first, end, _ in chain
            ):
                continue
            for first, end, _ in chain:
                entry += 1
                for row in group[first:end]:
                    found[id(row)] = entry
            after = chain[-1][1]
            if after < len(group) and group[after].x0 - left <= 0.5 * group[after].size:
                entry += 1
                found[id(group[after])] = entry

    group: list[_Row] = []
    for row in rows:
        if not (
            row.kind == "text"
            and not row.vertical
            and not row.code
            and row.marker is None
            and not row.note
            and not row.contents_level
        ):
            if group:
                settle(group)
            group = []
            continue
        prev = group[-1] if group else None
        if prev is not None and not (
            row.column == prev.column
            and abs(row.size - prev.size) <= max(1.0, 0.12 * prev.size)
            and 0.5 * prev.size <= row.baseline - prev.baseline <= 2.2 * prev.size
        ):
            settle(group)
            group = []
        group.append(row)
    if group:
        settle(group)
    return found


def _same_style(prev: _Row, row: _Row, ctx: _Context) -> bool:
    """Two lines of one block: both heading-sized or neither; for heading-sized lines the
    same size (to half a point); the same weight; and for bold lines, which are headings
    or run-in labels, the same size and typeface too ("Practice Makes Perfect" in a bold
    sans over the bold serif of the section it introduces is two headings; T2-8)."""
    if _heading_sized(prev, ctx) != _heading_sized(row, ctx):
        return False
    if _heading_sized(row, ctx) and abs(prev.size - row.size) > 0.5:
        return False
    if prev.bold != row.bold:
        return False
    return not (row.bold and (abs(prev.size - row.size) > 0.5 or prev.family != row.family))


# `Element.reasons` of a table of contents' entry (a list item): the link step looks for
# the heading it names (`_contents_links`).
CONTENTS_ENTRY = "contents entry"
LEADER_DOTS = frozenset(".\u00b7\u2219\u22c5\u2026")
MIN_LEADER_DOTS = 4  # dots in a leader; an ellipsis character counts as three
MIN_CONTENTS_LEADER_ROWS = 2  # lines with leaders that make a page a contents page
_PAGE_NUMBER = re.compile(r"\d{1,4}|[ivxlcdm]{1,7}", re.IGNORECASE)


def _leader_split(row: _Row) -> list[_Char] | None:
    """The characters of the title of a line like "1.1 Drop times . . . . 4" (dot leaders,
    or ellipses, and then a page number, which may be a line of its own at the right
    margin instead); None for any other line."""
    chars = row.chars
    end = len(chars)
    while end and chars[end - 1].blank:
        end -= 1
    start = end
    while start and chars[start - 1].c.isalnum():
        start -= 1
    if _PAGE_NUMBER.fullmatch("".join(c.c for c in chars[start:end])):
        end = start
    cut, dots = end, 0
    while cut and (chars[cut - 1].blank or chars[cut - 1].c in LEADER_DOTS):
        dots += 0 if chars[cut - 1].blank else 3 if chars[cut - 1].c == "\u2026" else 1
        cut -= 1
    title = chars[:cut]
    if dots < MIN_LEADER_DOTS or not any(c.c.isalpha() for c in title):
        return None
    return title


CONTENTS_SHAPE_LINES = 3  # lines that end in a page number, at one right edge: a contents list
CONTENTS_START_PAGES = 3  # without a "Contents" heading over it, a list counts on these pages
CONTENTS_REACH = 1.5  # x size: how far the right edges of a list's page numbers differ
CONTENTS_RAGGED_LINES = 6  # lines of a list whose numbers follow their titles, at no one edge
MIN_TITLE_LETTERS = 3  # letters in the title of a contents entry that has no leaders
_CONTENTS_HEADING = re.compile(r"(table of )?contents( \(continued\))?", re.IGNORECASE)
_SHAPE_NUMBER = re.compile(r"\d{1,4}|[ivx]{1,6}")


def _page_order(word: str) -> tuple[int, int]:
    """Where a printed page number comes in a book: the roman numbers of the front matter, then
    the others."""
    if word.isdigit():
        return 1, int(word)
    total = 0
    for here, after in pairwise(word + " "):
        total += -_ROMAN[here] if _ROMAN.get(after, 0) > _ROMAN[here] else _ROMAN[here]
    return 0, total


def _numbered_line(row: _Row) -> str | None:
    """The page number a line ends in, after a title and a space or leader dots, not part of a
    word ("Units 12", "Units . . . 12"); None if it has no such number or the title has no
    letters to speak of."""
    chars = row.chars
    end = len(chars)
    while end and chars[end - 1].blank:
        end -= 1
    start = end
    while start and chars[start - 1].c.isalnum():
        start -= 1
    word = "".join(c.c for c in chars[start:end])
    if not start or not _SHAPE_NUMBER.fullmatch(word):
        return None
    if not (chars[start - 1].blank or chars[start - 1].c in LEADER_DOTS):
        return None
    return word if sum(c.c.isalpha() for c in chars[:start]) >= MIN_TITLE_LETTERS else None


def _shape_entries(rows: Sequence[_Row], number: int) -> list[list[tuple[_Row, _Row | None]]]:
    """The contents lists of a page found by their shape, with or without leaders.

    A list is `CONTENTS_SHAPE_LINES` or more lines one after the other in one column, each
    ending in a page number (or followed on its baseline by a line that is only that number: a
    column of its own), the numbers at one right edge and not decreasing. It counts under a
    "Contents" heading or on one of the first `CONTENTS_START_PAGES` pages. Each item is a line
    and the line with its number, when that stands apart. A line without a number ends the
    list, so a section heading right after it is not taken in."""
    flat = [r for r in rows if not r.vertical]
    alone = [r for r in flat if _SHAPE_NUMBER.fullmatch(r.clean)]
    stray = {id(r) for r in alone}
    taken: set[int] = set()
    seen: list[tuple[_Row, _Row | None, str | None]] = []
    for row in sorted(
        (r for r in flat if id(r) not in stray), key=lambda r: (r.column, r.baseline)
    ):
        word = _numbered_line(row)
        twin = None
        if word is None and sum(c.c.isalpha() for c in row.chars) >= MIN_TITLE_LETTERS:
            twin = next(
                (
                    n
                    for n in alone
                    if id(n) not in taken
                    and abs(n.baseline - row.baseline) <= 0.35 * row.size
                    and n.x0 > row.x1
                ),
                None,
            )
            if twin is not None:
                taken.add(id(twin))
                word = twin.clean
        seen.append((row, twin, word))
    headings = [r for r in flat if _CONTENTS_HEADING.fullmatch(r.clean.strip())]
    found: list[list[tuple[_Row, _Row | None]]] = []
    block: list[tuple[_Row, _Row | None, str]] = []

    def close() -> None:
        if len(block) >= CONTENTS_SHAPE_LINES and _is_contents_list(block, headings, number):
            found.append([(row, twin) for row, twin, _ in block])
        block.clear()

    for row, twin, word in seen:
        if word is None:
            close()
            continue
        if block and (
            row.column != block[-1][0].column or row.baseline - block[-1][0].baseline > 3 * row.size
        ):
            close()
        block.append((row, twin, word))
    close()
    return found


def _is_contents_list(
    block: Sequence[tuple[_Row, _Row | None, str]], headings: Sequence[_Row], number: int
) -> bool:
    """Do the lines of `block` (a line, its separate number, the number) read as a contents list:
    one right edge (a list of `CONTENTS_RAGGED_LINES` or more may have each number follow its
    title instead), numbers that do not decrease, and a "Contents" heading above or an early
    page."""
    edges = [(twin or row).x1 for row, twin, _ in block]
    middle = statistics.median(edges)
    size = statistics.median([row.size for row, _, _ in block])
    aligned = all(abs(e - middle) <= CONTENTS_REACH * size for e in edges)
    if not aligned and len(block) < CONTENTS_RAGGED_LINES:
        return False
    orders = [_page_order(word) for _, _, word in block]
    if any(a > b for a, b in pairwise(orders)):
        return False
    top = block[0][0].baseline
    return number < CONTENTS_START_PAGES or any(h.baseline < top for h in headings)


def _contents_lines(frags: Sequence[_Row], number: int) -> set[int]:
    """The indices of the fragments of `frags` (a page's text before its lines are worked out)
    that are the lines of its contents lists by shape (`_shape_entries`): fragments on one
    baseline close together make a line, and a number set apart stays a fragment of its own."""
    if (
        sum(
            1
            for f in frags
            if not f.vertical and (_numbered_line(f) or _SHAPE_NUMBER.fullmatch(f.clean))
        )
        < CONTENTS_SHAPE_LINES
    ):
        return set()
    owners: dict[int, list[int]] = {}
    rows: list[_Row] = []
    bands: list[list[int]] = []
    for i in sorted(
        (i for i, f in enumerate(frags) if not f.vertical), key=lambda i: frags[i].baseline
    ):
        top = frags[bands[-1][0]] if bands else None
        if top is not None and abs(frags[i].baseline - top.baseline) <= 0.3 * frags[i].size:
            bands[-1].append(i)
        else:
            bands.append([i])
    for band in bands:
        band.sort(key=lambda i: frags[i].x0)
        row, used = frags[band[0]], [band[0]]
        for i in band[1:]:
            if frags[i].x0 - row.x1 <= 4 * max(row.size, frags[i].size):
                row, used = _join_rows(row, frags[i]), [*used, i]
            else:
                rows.append(row)
                owners[id(row)] = used
                row, used = frags[i], [i]
        rows.append(row)
        owners[id(row)] = used
    return {
        i
        for block in _shape_entries(rows, number)
        for pair in block
        for r in pair
        if r is not None
        for i in owners[id(r)]
    }


def _contents_rows(rows: list[_Row], number: int = 0) -> list[_Row]:
    """The lines of a table of contents as entries without their leaders and page numbers.

    A page with at least `MIN_CONTENTS_LEADER_ROWS` lines that end in dot leaders (and a
    page number) is a contents page. There each such line keeps its title; a line with no
    leaders whose page number stands alone at the right margin on the same baseline (a
    chapter line, set in bold, with its number at the margin) keeps its text; the page
    number's own line goes; each entry gets a level from its indent. Page numbers are
    where the printed book had the pages, which nothing in this file has.

    A list with no leaders is found by its shape (`_shape_entries`); its entries keep their
    page numbers as their last words, joined to them where they stood apart."""
    split = {id(r): found for r in rows if not r.vertical and (found := _leader_split(r))}
    if len(split) < MIN_CONTENTS_LEADER_ROWS:
        return _shaped_rows(rows, number)
    numbers = [r for r in rows if _PAGE_NUMBER.fullmatch(r.clean) and not r.vertical]
    pairs: list[tuple[_Row, _Row | None]] = []  # (a line, the page number beside it)
    taken: set[int] = set()
    for row in rows:
        if row.vertical or any(row is n for n in numbers):
            continue
        twin = next(
            (
                n
                for n in numbers
                if id(n) not in taken
                and abs(n.baseline - row.baseline) <= 0.35 * row.size
                and n.x0 > row.x1
            ),
            None,
        )
        if twin is not None:
            taken.add(id(twin))
        pairs.append((row, twin))
    # The numbers of a contents page stand at one right edge.
    edge = statistics.median([t.x1 for _, t in pairs if t is not None] or [0.0])
    entries: list[tuple[_Row, _Row]] = []  # (the line, the line it becomes)
    dropped: set[int] = set()
    for row, twin in pairs:
        aligned = twin is not None and abs(twin.x1 - edge) <= 1.5 * row.size
        if id(row) in split:
            entries.append((row, _Row(split[id(row)], column=row.column)))
        elif aligned and row.kind == "text" and len(split) >= 1:
            entries.append((row, row))
        else:
            continue
        if aligned and twin is not None:
            dropped.add(id(twin))
    return _leveled(rows, entries, dropped)


def _shaped_rows(rows: list[_Row], number: int) -> list[_Row]:
    """`rows` with the lines of the contents lists found by shape as entries (`_contents_rows`)."""
    entries: list[tuple[_Row, _Row]] = []
    dropped: set[int] = set()
    for block in _shape_entries(rows, number):
        for row, twin in block:
            entries.append((row, row if twin is None else _join_rows(row, twin)))
            if twin is not None:
                dropped.add(id(twin))
    return _leveled(rows, entries, dropped)


def _leveled(rows: list[_Row], entries: list[tuple[_Row, _Row]], dropped: set[int]) -> list[_Row]:
    """`rows` with each (line, entry) pair of a contents list swapped for its entry, which gets
    a level from its indent, and the `dropped` lines (page numbers) taken out."""
    if not entries:
        return rows
    steps: list[float] = []  # the distinct indents, outermost first
    for x in sorted(new.x0 for _, new in entries):
        if not steps or x > steps[-1] + 0.6 * entries[0][1].size:
            steps.append(x)
    for old, new in entries:
        new.contents_level = 1 + max(i for i, x in enumerate(steps) if x <= new.x0 + 0.6 * new.size)
        new.kind = "text"
        new.column = old.column
    swap = {id(old): new for old, new in entries}
    return [swap.get(id(r), r) for r in rows if id(r) not in dropped]


def _code_groups(rows: Sequence[_Row]) -> dict[int, int]:
    """Rows of program code, by `id(row)`, with the number of the code block they are in.

    A code block is consecutive rows (in reading order) of upright text that is
    monospaced throughout (`_Row.code`), in one column, one line pitch apart (a blank line
    in the code is a gap of up to two) and of one size, with at least `CODE_MIN_GLYPHS`
    glyphs, or `CODE_MIN_SHORT` in two lines or more. Whatever their indentation: a
    Python loop's body is a line that starts further right, not a new paragraph. A single
    short monospaced line ("print") is not a block.
    """
    groups: list[list[_Row]] = []
    for row in rows:
        if row.kind != "text" or not row.code:
            groups.append([])  # ends the block
            continue
        last = groups[-1] if groups else []
        if last:
            prev = last[-1]
            pitch = row.baseline - prev.baseline
            if (
                row.column == prev.column
                and abs(row.size - prev.size) <= 0.5
                and 0.5 * prev.size <= pitch <= CODE_MAX_PITCH * prev.size
            ):
                last.append(row)
                continue
        groups.append([row])
    found: dict[int, int] = {}
    for group in groups:
        glyphs = sum(len(r.ink) for r in group)
        if glyphs >= CODE_MIN_GLYPHS or (len(group) >= 2 and glyphs >= CODE_MIN_SHORT):
            for row in group:
                found[id(row)] = id(group[0])
    return found


def _pitch(row: _Row) -> list[float]:
    """Distances between the starts of neighbouring characters of one word of a code line
    (no space between them), the advance of its monospaced font."""
    return [
        b.x0 - a.x0
        for a, b in pairwise(row.chars)
        if not a.blank and not b.blank and a.mono and b.mono and 0 < b.x0 - a.x0 < 2 * row.size
    ]


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _code_text(rows: Sequence[_Row]) -> str:
    """The text of a code block, line by line, with the indentation it was printed with.

    A line's spaces are the text layer's own; what it lacks is the indentation, which is
    the line's distance from the block's left edge over the font's advance (the usual
    distance between the starts of two neighbouring characters; a monospaced font moves
    the same distance for each). A gap between two neighbours with no space between them
    (a PDF that moves the pen instead of printing spaces) counts as spaces by the same
    measure. Blank lines are the gaps between lines that are bigger than the usual one."""
    advance = _median([d for r in rows for d in _pitch(r)])
    left = min(r.x0 for r in rows)
    pitches = [b.baseline - a.baseline for a, b in pairwise(rows) if b.baseline > a.baseline]
    unit = min(min(pitches), CODE_LINE * rows[0].size) if pitches else 0.0
    lines: list[str] = []
    for i, row in enumerate(rows):
        if i and unit > 0:
            lines.extend([""] * max(0, round((row.baseline - rows[i - 1].baseline) / unit) - 1))
        own = _median(_pitch(row)) or advance
        line = " " * (round((row.x0 - left) / advance) if advance > 0 else 0)
        before: _Char | None = None  # the character just before, with nothing between
        for c in row.chars:
            if c.blank:
                if (c.x1 - c.x0) < CODE_SPACE * own:
                    continue  # a space the text layer made up for a small gap, not printed
                if line.strip():
                    line += " "
                before = None
                continue
            if before is not None and own > 0 and (c.x0 - before.x0) >= CODE_GAP * own:
                line += " " * (round((c.x0 - before.x0) / own) - 1)
            line += c.c.translate(_TEXT_MAP)
            before = c
        lines.append(line.rstrip())
    return "\n".join(lines)


# The text decisions layout raises on a paragraph or list item (`Element.reasons`; the
# pipeline turns them into a decision, `notices.text_decisions`). WS2.4a, SW54a.
REASON_OPTION_LABELS = notice_rules.OPTION_LABELS
REASON_RTL_ORDER = notice_rules.RTL_TEXT
TEXT_DECISIONS = (REASON_OPTION_LABELS, REASON_RTL_ORDER)
OPTION_GAP = 0.5  # em: a gap wider than a word space before a later option label


def _later_labels(rows: Sequence[_Row]) -> list[tuple[int, int]]:
    """Where the option labels after the first of a block's rows are, in sequence.

    The block starts with a label (a list marker, or a first word like "(A)") and holds the
    next one of the same form, "(B)", the next letter or number each time, set off by more
    than a word space: a grid of options that reading order merged into lines of one block
    (RC-18). Returns (row index, index in the row's characters) of each, [] when none."""
    first = rows[0]
    tokens = _tokens(first)
    if first.marker is not None:
        label = first.marker.text
    elif len(tokens) >= 2:
        label = "".join(c.c for c in first.chars[slice(*tokens[0])])
    else:
        return []
    parsed = _ordered_marker(label)
    if parsed is None:
        return []
    form, body = parsed
    wanted = {(style, value + 1) for style, value in marker_readings(body)}
    found: list[tuple[int, int]] = []
    for r, row in enumerate(rows):
        tokens = _tokens(row)
        for k, (start, end) in enumerate(tokens):
            if (r == 0 and k == 0) or k == len(tokens) - 1 or not wanted:
                continue
            token = _ordered_marker("".join(c.c for c in row.chars[start:end]))
            if token is None or token[0] != form:
                continue
            readings = set(marker_readings(token[1])) & wanted
            gap = row.chars[start].x0 - row.chars[tokens[k - 1][1] - 1].x1 if k else math.inf
            if readings and gap >= OPTION_GAP * row.size:
                found.append((r, start))
                wanted = {(style, value + 1) for style, value in readings}
    return found


def _split_options(runs: list[_Run], *, document: _Document) -> list[_Run]:
    """A line of options that reading order merged ("(A) 3 m (B) 5 m (C) 7 m") is cut at each
    later label into one run per option, the first keeping its list marker and the others
    getting one when it has one, when the line stands alone (pieces that are complete rows).
    Any other block that holds a later label stays whole and is flagged
    (`REASON_OPTION_LABELS`, `_flag_text`)."""
    out: list[_Run] = []
    for run in runs:
        if run.kind != "text" or run.code or run.contents or run.note or len(run.rows) != 1:
            out.append(run)
            continue
        row = run.rows[0]
        cuts = [start for _, start in _later_labels([row])]
        pieces = []
        for begin, end in pairwise([0, *cuts, len(row.chars)]):
            chars = row.chars[begin:end]
            while chars and chars[-1].blank:
                chars = chars[:-1]
            piece = _Row(chars, column=row.column)
            _classify_row(piece, document=document)
            pieces.append(piece)
        if not cuts or any(p.kind != "text" for p in pieces):
            out.append(run)
            continue
        pieces[0].marker = row.marker
        for piece in pieces[1:] if row.marker is not None else ():
            tokens = _tokens(piece)
            text = "".join(c.c for c in piece.chars[slice(*tokens[0])])
            piece.marker = _Marker(text, True, tokens[1][0], piece.chars[tokens[1][0]].x0)
        out += [_Run([p], "text", block_start=True) for p in pieces]
    return out


def _flag_text(blocks: Sequence[_Block]) -> None:
    """The text decisions of paragraphs and list items (`Element.reasons`): option labels
    inside one block (`_later_labels`), and right-to-left text, whose word order is not
    trusted (`_rtl_words`: at least two words, since one has no order to get wrong)."""
    for block in blocks:
        if block.kind not in _TEXT_BLOCKS or block.code or not block.rows:
            continue
        if _later_labels(block.rows):
            block.reasons.append(REASON_OPTION_LABELS)
        if sum(_rtl_words(r) for r in block.rows) >= 2:
            block.reasons.append(REASON_RTL_ORDER)


def _rtl_words(row: _Row) -> int:
    """Words of the row with a strong right-to-left letter (Unicode bidi class R or AL)."""
    words, inside = 0, False
    for c in row.chars:
        if c.blank:
            inside = False
        elif not inside and unicodedata.bidirectional(c.c[:1]) in ("R", "AL"):
            words, inside = words + 1, True
    return words


def _runs(rows: list[_Row], ctx: _Context, *, document: _Document) -> list[_Run]:
    runs: list[_Run] = []
    code = _code_groups(rows)
    edges = _RightEdges(rows)
    hanging = _hanging_entries(rows, edges)
    for row in rows:
        if runs:
            run = runs[-1]
            prev = run.rows[-1]
            if row.note:  # a footnote the file names: a block of its own, text whatever it holds
                row.kind, row.region = "text", None
                run.block_end = True
                runs.append(_Run([row], "text", block_start=True, note=True))
                continue
            if (
                run.note
                and row.kind == "text"
                and not row.contents_level
                and _continues(prev, row, run, ctx)
            ):
                run.add(row)
                continue
            if row.contents_level or run.contents:
                run.block_end = True
                runs.append(
                    _Run([row], row.kind, block_start=True, contents=bool(row.contents_level))
                )
                continue
            if id(row) in code or run.code:
                if id(row) in code and run.code and code[id(row)] == code[id(prev)]:
                    run.add(row)
                    continue
                run.block_end = True
                runs.append(_Run([row], row.kind, block_start=True, code=id(row) in code))
                continue
            entry = hanging.get(id(row))
            if row.letter or prev.letter or (entry is not None and entry != hanging.get(id(prev))):
                run.block_end = True  # a letter heading, or the first line of an entry
                runs.append(_Run([row], row.kind, block_start=True))
                continue
            if entry is not None and run.kind == "text" == row.kind:
                run.add(row)  # a later line of the entry (`_hanging_entries`)
                continue
            if run.kind == "math" and row.kind == "math" and row.region is prev.region:
                run.add(row)  # rows of one display equation (`_contiguous_regions`)
                continue
            if (
                run.kind == "text"
                and row.kind == "math"
                and _wrapped_maths(prev, row, run, ctx, document=document)
            ):
                row.kind, row.region = "text", None  # its maths becomes inline equations
                run.add(row)
                continue
            geometric = run.kind == "text" == row.kind and _continues(prev, row, run, ctx, edges)
            led = geometric and _starts_entry(prev, row, run, edges)  # a lead term: its own entry
            if geometric and not led and _same_style(prev, row, ctx):
                run.add(row)
                continue
            run.block_end = not geometric or led
            ends_sentence = prev.clean[-1:] in ".:?!"
            short = row.x1 < run.box[2] - 3 * row.size
            # Two bold lines that differ in size or typeface are two headings (or labels),
            # not a heading and the paragraph it leads: the second one starts a block.
            restyled = geometric and prev.bold and row.bold
            starts = not geometric or restyled or led or (ends_sentence and short)
            runs.append(_Run([row], row.kind, block_start=starts))
        else:
            if row.note:
                row.kind, row.region = "text", None
            runs.append(
                _Run(
                    [row],
                    row.kind,
                    block_start=True,
                    code=id(row) in code and not row.note,
                    contents=bool(row.contents_level),
                    note=bool(row.note),
                )
            )
    return runs


def _wrapped_maths(prev: _Row, row: _Row, run: _Run, ctx: _Context, *, document: _Document) -> bool:
    """Is maths row `row` the last line of the sentence above, not a display equation?

    "... the system has exactly one solution," / "x = A^-1 b." A display equation is
    centred or indented and set off by extra space; a line of the paragraph starts at its
    left margin at the usual line pitch; in a list item, where the item's text starts (after
    its marker; slides only). A numbered or multi-line equation is never one.
    """
    region = row.region
    if region is None or region.forced or region.labels or len(region.rows) != 1:
        return False
    first = run.rows[0]
    left = first.marker.x if first.marker is not None and document.slide_deck else run.box[0]
    return abs(row.x0 - left) <= 0.5 and _continues(prev, row, run, ctx)


def _title_like(text: str) -> bool:
    """Starts like a title: the first word that starts with a letter, after any number
    ("2 kg", "2.3 Units", "2nd Law"), is not lowercase, and it does not end like a clause."""
    for word in text.split():
        first = next((c for c in word if c.isalnum()), "")
        if first.isalpha():
            return not first.islower() and text[-1:] not in ",;"
    return False


MATHS_HEADING_SHARE = 0.7  # a heading candidate with this share of maths characters is a formula
_LONGEST_SYMBOL_WORD = 3  # letters: a longer plain word (outside _MATH_WORDS) is prose


def _mostly_maths(rows: Sequence[_Row], *, document: _Document) -> bool:
    """Is a heading candidate a formula? ("KE = m v", a large "F = m a" on a slide.)

    It needs a relation sign at full size, no plain word longer than three letters (a
    function name from `_MATH_WORDS` is fine), and at least MATHS_HEADING_SHARE of its
    characters (punctuation apart) are maths: digits, operators, brackets, maths symbols
    and letters that read as variables, or the letters of words up to three letters
    long, which beside a relation sign are products of variables ("mgh"). A heading that
    only contains a number or a sign has no relation sign and stays a heading.
    """
    counted = mathy = 0
    relation = False
    for row in rows:
        info = _CharInfo.of(row, document=document)
        chars = row.chars
        i = 0
        while i < len(chars):
            c = chars[i]
            if c.blank or c.c in ",.;:":
                i += 1
                continue
            if info.classes[i] == "L" and not info.script[i] and not info.maths_words[i]:
                j = i
                while j < len(chars) and info.classes[j] == "L" and not info.script[j]:
                    j += 1
                word = "".join(x.c for x in chars[i:j]).lower()
                if j - i > _LONGEST_SYMBOL_WORD and word not in _MATH_WORDS and info.words[i] > 1:
                    return False
                counted += j - i
                mathy += j - i
                i = j
                continue
            counted += 1
            mathy += info.mathy(i)
            relation = relation or (c.c in _ROW_RELATIONS and not row.is_script(c))
            i += 1
    return relation and counted > 0 and mathy / counted >= MATHS_HEADING_SHARE


def _equation_block(
    rows: list[_Row], box: BBox, size: float, ink: list[_Char], *, plain: bool = False
) -> _Block:
    """The equation block of maths rows (their region's equation number kept apart).

    `plain`: the text layer keeps no letter styles. For a line set bold as a whole (a
    heading's weight), where bold says nothing about vectors."""
    # A system's opening brace (a row of its own, standing left of all the others) is read first;
    # the brace of "f(x) = {" stays where it is.
    braces = [r for r in rows if r.ink and all(c.c in _OPENING_BRACES for c in r.ink)]
    others = [r for r in rows if r not in braces]
    left = min((r.x0 for r in others), default=0.0)
    first = [r for r in braces if r.x1 <= left + 0.5]
    ordered = [*first, *(r for r in rows if r not in first)]
    text = _clean(" ".join((r.text if plain else r.styled_text) for r in ordered))
    script = any(
        c.flags & _SUPERSCRIPT_FLAG or (c.size < SCRIPT_RATIO * size and not c.small_cap)
        for c in ink
    )
    score, reasons = equation_score(text, sorted({c.font for c in ink}), script)
    if len(rows) > 1:
        reasons.append(f"{len(rows)} lines merged")
    region = rows[0].region
    labels = sorted(region.labels, key=lambda r: (r.y0, r.x0)) if region else []
    if labels:
        reasons.append("equation number")
    if region is not None and region.page_pass is not None:
        reasons.append(PAGE_SPAN_REASON if region.page_pass == "span" else PAGE_LAYOUT_ONLY_REASON)
    return _Block(
        Kind.EQUATION,
        box,
        rows,
        text=text,
        font_size=size,
        score=max([score, *(r.score for r in rows)]),
        reasons=reasons,
        label=" ".join(r.clean for r in labels) or None,
        regular=_regular_letters(ink),
        page_latex=region.latex if region is not None else None,
        page_status=region.page_pass if region is not None else None,
    )


def _classify_run(run: _Run, ctx: _Context, *, document: _Document) -> _Block:
    rows = run.rows
    box = _union(r.bbox for r in rows)
    size = _dominant_size(rows)
    ink = [c for r in rows for c in r.ink]
    if run.contents:
        return _Block(
            Kind.LIST_ITEM,
            box,
            rows,
            font_size=size,
            bold=all(r.bold for r in rows),
            contents_level=rows[0].contents_level,
        )
    if run.code:
        return _Block(
            Kind.PARAGRAPH, box, rows, text=_code_text(rows), font_size=size, bold=False, code=True
        )
    if rows[0].letter and run.kind == "text" and len(rows) == 1:
        return _Block(
            Kind.HEADING,
            box,
            rows,
            text=rows[0].clean,
            font_size=size,
            bold=rows[0].bold,
            reasons=[REASON_LETTER, *(["bold"] if rows[0].bold else [])],
        )
    # A lone ">4" or "+8" (a table cell, a stray sign) is too little to call an equation.
    trivial = len(rows) == 1 and len(ink) <= 2 and not any(c.math for c in ink)
    if run.kind == "math" and not trivial:
        return _equation_block(rows, box, size, ink)
    text = _join_text(rows, document=document)
    letters = sum(c.isalpha() for c in text)
    bold = all(r.bold for r in rows)
    ratio = size / ctx.body
    larger = ratio >= HEADING_SIZE_RATIO
    # A section number is strong evidence in a large font ("3 Forces" is a chapter title);
    # at body size only a real section shape counts, not "2 kg", "12 Metres" or a date.
    number = section_number(text)
    titled = section_heading_number(text)
    evidence: list[str] = []
    if number and number[1]:
        evidence.append(REASON_LIST_NUMBER)
    elif number and (larger or titled):
        evidence.append(REASON_NUMBERED)
    # Rotated and diagonal text (a watermark, a stamp in the margin) is never a heading.
    upright = not any(r.vertical for r in rows)
    heading = None
    if (
        upright
        and larger
        and len(rows) <= MAX_HEADING_ROWS
        and len(text) <= MAX_HEADING_CHARS
        and (letters >= 2 or number)
    ):
        heading = [REASON_LARGER_FONT, f"font {ratio:.2f}x body", *evidence]
    elif CAPTION.match(text):
        return _Block("caption", box, rows, text=readable_math_letters(text), font_size=size)
    elif (
        upright
        and bold
        and ratio >= 0.95
        and run.block_start
        and len(rows) <= MAX_BOLD_HEADING_ROWS
        and len(text) <= MAX_BOLD_HEADING_CHARS
        and letters >= 2
        and _title_like(text)
    ):
        heading = [REASON_BOLD_BODY, *evidence]
    elif (
        upright
        and titled
        and len(rows) == 1
        and run.block_start
        and run.block_end
        and len(text) <= 80
        and len(text.split()) <= 12
        and text[-1:] != "."
    ):
        heading = [REASON_NUMBERED_LINE, *evidence]
    if heading is not None and _mostly_maths(rows, document=document):
        # A formula set large or bold on a slide is not a title: it goes to the equation
        # step like any display equation, and the page keeps its real headings.
        block = _equation_block(rows, box, size, ink, plain=bold)
        block.reasons.append("heading candidate that is mostly maths")
        return block
    if heading is not None:
        if bold:
            heading.append("bold")
        marked = readable_math_letters(_join_text(rows, marked=True, document=document))
        return _Block(
            Kind.HEADING,
            box,
            rows,
            text=readable_math_letters(text),
            font_size=size,
            bold=bold,
            reasons=heading,
            marked=marked if script_marks.has_marks(marked) else None,
        )
    if rows[0].marker is not None:
        return _Block(Kind.LIST_ITEM, box, rows, font_size=size, bold=bold, marker=rows[0].marker)
    return _Block(Kind.PARAGRAPH, box, rows, font_size=size, bold=bold)


# The style variants of a variable that a plain letter says as well: bold, italic and bold
# italic Latin (U+1D400-1D49B), the sans-serif and monospace Latin and Greek, the digits
# except the double-struck ones (U+1D7D8-1D7E1), and Planck's h (U+210E, the italic h).
# Script, fraktur and double-struck letters (the number sets R C N Z Q, and the letterlike
# symbols l and L) are names of things, not styles of a letter: they stay.
_FLATTEN = ((0x1D400, 0x1D49B), (0x1D5A0, 0x1D7D7), (0x1D7E2, 0x1D7FF), (0x210E, 0x210E))


def readable_math_letters(text: str) -> str:
    """`text` with the italic and bold variable letters of the Mathematical Alphanumeric
    Symbols block (and Planck's h) as the plain letters and digits they style; nothing else
    changes, so the number sets and the letterlike symbols keep their meaning.

    A heading or a caption's plain text keeps no inline equation (a paragraph, and a
    figure's caption when it has maths, do), so a variable in a title such as "Free fall
    and g" set in math italic would be read out as "mathematical italic small g". The
    letter is what a reader and a screen reader need; the italics are lost."""
    if text.isascii():
        return text
    return "".join(
        unicodedata.normalize("NFKC", c)
        if any(lo <= ord(c) <= hi for lo, hi in _FLATTEN)
        and unicodedata.decomposition(c).startswith("<font>")
        else c
        for c in text
    )


def _join_text(rows: Sequence[_Row], *, marked: bool = False, document: _Document) -> str:
    """Rows joined with spaces, end-of-line hyphenation repaired (see `_line_break`). With
    `marked`, raised and lowered text of upright text is between marks
    (`pipeline.script_marks`)."""
    out = ""
    for row in rows:
        source = (
            _marked(row.chars, _script_kinds(row, document=document), 0, len(row.chars))
            if marked
            else row.text
        )
        text = " ".join(source.split())  # soft hyphens survive until the final _clean
        if out:
            out, separator = _line_break(out, text, document=document)
            out += separator
        out += text
    return _clean(out)


def _line_break(before: str, after: str, *, document: _Document = _PLAIN) -> tuple[str, str]:
    """(`before` with any hyphen removed, separator) for joining two lines of a paragraph.

    "veloc-" + "ity" -> "velocity": a letter, a hyphen at the line end and a lowercase
    start make one word. "Newton-" + "Raphson" keeps its hyphen but gets no space, and
    so does "well-" + "known": a compound is told from a broken word by the word list
    (`hyphens.keeps_hyphen`: kept when both pieces are words and the joined word is not,
    or when the piece before the hyphen is a single letter, as in "x-" + "ray"). A soft
    hyphen always disappears. A break inside one token (a date, an address, a compound
    joined by a non-breaking hyphen) keeps the token whole (`hyphens.joins_inside_token`).
    Two lines that end and start in a script written without spaces (Han, Kana, Thai, Lao,
    Khmer, and their full-width punctuation) join with no separator. Anything else is
    joined with a space.
    """
    if before.endswith("\u00ad"):
        return before[:-1], ""
    if hyphens.joins_inside_token(before, after):
        return before, ""
    if len(before) > 1 and before[-1] in hyphens.HYPHENS and before[-2].isalpha():
        if after[:1].islower() and not hyphens.keeps_hyphen(
            before, after, document.compounds, document.whole_words
        ):
            return before[:-1], ""
        return before, ""
    if before and after and _unspaced(before[-1]) and _unspaced(after[0]):
        return before, ""
    return before, " "


# Scripts written without spaces between words, and the punctuation set with them.
_UNSPACED = (
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x1780, 0x17FF),  # Khmer
    (0x19E0, 0x19FF),  # Khmer symbols
    (0x2E80, 0x2FDF),  # CJK radicals
    (0x3000, 0x30FF),  # CJK punctuation, Hiragana, Katakana
    (0x31F0, 0x31FF),  # Katakana extensions
    (0x3400, 0x4DBF),  # Han, extension A
    (0x4E00, 0x9FFF),  # Han
    (0xF900, 0xFAFF),  # Han compatibility
    (0xFF00, 0xFFEF),  # full-width and half-width forms
    (0x20000, 0x2FFFF),  # Han, extensions B onward
)


def _unspaced(c: str) -> bool:
    return any(lo <= ord(c) <= hi for lo, hi in _UNSPACED)


def _merge_style_splits(blocks: list[_Block], runs: list[_Run]) -> list[_Block]:
    """Re-join paragraphs that were split only by a bold line that did not become a heading."""
    out: list[_Block] = []
    out_runs: list[_Run] = []
    for block, run in zip(blocks, runs, strict=True):
        if (
            out
            and block.kind == Kind.PARAGRAPH
            and out[-1].kind == Kind.PARAGRAPH
            and not run.block_start
            and not block.code
            and not out[-1].code
        ):
            prev = out[-1]
            prev.rows.extend(block.rows)
            prev.bbox = _union([prev.bbox, block.bbox])
            prev.font_size = _dominant_size(prev.rows)
            prev.bold = prev.bold and block.bold
            out_runs[-1].block_end = run.block_end
            continue
        out.append(block)
        out_runs.append(run)
    return out


def _merge_chapter_numbers(blocks: list[_Block]) -> list[_Block]:
    """A bare "2" or "Chapter 2" heading directly above the title becomes one heading."""
    out: list[_Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == Kind.HEADING == block.kind
            and REASON_LARGER_FONT in prev.reasons
            and REASON_LARGER_FONT in block.reasons
            and re.fullmatch(
                r"(?:(?:Chapter|CHAPTER|Unit|UNIT|Part|PART)\s+)?\d{1,3}", prev.text or ""
            )
            and 0 <= block.bbox[1] - prev.bbox[3] <= 2.5 * (block.font_size or 10)
        ):
            prev.text = f"{prev.text} {block.text}"
            prev.rows.extend(block.rows)
            prev.bbox = _union([prev.bbox, block.bbox])
            continue
        out.append(block)
    return out


def _before_symbol(
    chars: list[_Char], kinds: Sequence[ScriptKind | None], k: int, start: int, end: int
) -> bool:
    """Is the blank at `k` the one between a nuclide's numbers and its symbol: digits written
    raised or lowered that start a word, a blank, a capital letter?"""
    if k <= start or k + 1 >= end or kinds[k - 1] is None or kinds[k + 1] is not None:
        return False
    if chars[k].flags & SET_OFF_FLAG:
        return False  # the blank `_set_off` made after a note mark: a note, not a nuclide
    if not chars[k + 1].c.isupper():
        return False
    j = k
    while j > start and kinds[j - 1] is not None:
        j -= 1
    group = chars[j:k]
    prefix = j == start or chars[j - 1].blank
    return prefix and all(c.c.isdigit() or c.c in "-\u2212+" for c in group)


def _marked(chars: list[_Char], kinds: list[ScriptKind | None], start: int, end: int) -> str:
    """The text of `chars[start:end]` with its raised and lowered runs between marks
    (`pipeline.script_marks`). TeX sets no space glyph after a script ("Cl-" and "and" in
    "Cl- and"), so a visible gap at either edge of a script gets one."""
    text: list[str] = []
    marks: list[ScriptKind | None] = []
    for k in range(start, end):
        c = chars[k]
        if c.blank and _before_symbol(chars, kinds, k, start, end):
            continue  # the text layer's space between 14 6 and the C of a nuclide
        if k > start and kinds[k] != kinds[k - 1]:
            before = chars[k - 1]
            right = before.x1
            j = k - 1
            while j > start and kinds[j - 1] is not None and kinds[k - 1] is not None:
                j -= 1  # a stacked pair ends where its wider half does
                right = max(right, chars[j].x1)
            if not before.blank and not c.blank and c.x0 - right >= SPACE_AFTER_SCRIPT * c.size:
                text.append(" ")
                marks.append(None)
        text.append(c.c)
        marks.append(kinds[k])
    return script_marks.wrap(text, marks)


# `Element.reasons` the page math pass leaves on what it placed and on what it did not confirm.
PAGE_SPAN_REASON = "page pass span"
PAGE_LAYOUT_ONLY_REASON = "page pass did not confirm"


def _carry_page_math(old: _Row, new: _Row, offset: int) -> None:
    """The page math marks of `old` on `new`, a row made of `old.chars[offset:]`."""
    new.page_checked = old.page_checked
    new.page_inline = [(a - offset, b - offset, t) for a, b, t in old.page_inline if a >= offset]
    new.page_nonmath = [(a - offset, b - offset) for a, b in old.page_nonmath if a >= offset]


def _with_page_spans(
    row: _Row, runs: list[tuple[int, int, list[str]]]
) -> tuple[list[tuple[int, int, list[str]]], dict[tuple[int, int], str]]:
    """Layout's inline runs of a row with the page pass's spans laid over them.

    A run that touches a span the pass placed is replaced by that span (the pass settles
    where the maths starts and ends); a run inside a stretch the pass called a look-alike of
    maths is dropped; the other runs stay, marked as not confirmed. The pass's own spans
    come with their LaTeX."""
    spans = sorted(row.page_inline)
    out: list[tuple[int, int, list[str]]] = []
    for start, end, reasons in runs:
        if any(start < b and a < end for a, b, _ in spans):
            continue
        if any(start < b and a < end for a, b in row.page_nonmath):
            continue
        out.append((start, end, [*reasons, PAGE_LAYOUT_ONLY_REASON]))
    latex = {(a, b): t for a, b, t in spans}
    out += [(a, b, [PAGE_SPAN_REASON]) for a, b, _ in spans]
    return sorted(out, key=lambda r: (r[0], r[1])), latex


_NO_SPACE_BEFORE = frozenset(",.;:!?)]}\u2019\u201d")
_NO_SPACE_AFTER = frozenset("([{\u2018\u201c")


def _gap_before(chars: list[_Char], start: int) -> bool:
    """Is there a visible gap between the glyph before an inline equation and its first
    glyph, with no blank in the text layer for it ("ions" and "SO42-" in TeX output, which
    sets no space after a script)? Then the text before it gets one."""
    if start == 0 or chars[start - 1].blank or chars[start].blank:
        return False
    before, first = chars[start - 1], chars[start]
    return (
        before.c not in _NO_SPACE_AFTER and first.x0 - before.x1 >= SPACE_AFTER_SCRIPT * first.size
    )


def _gap_after(chars: list[_Char], start: int, end: int) -> bool:
    """The same after an inline equation: a visible gap to the next glyph, no blank."""
    if end >= len(chars) or chars[end].blank or chars[end - 1].blank:
        return False
    nxt = chars[end]
    right = max((c.x1 for c in chars[start:end] if not c.blank), default=chars[end - 1].x1)
    return nxt.c not in _NO_SPACE_BEFORE and nxt.x0 - right >= SPACE_AFTER_SCRIPT * nxt.size


def _paragraph_pieces(block: _Block, *, document: _Document) -> list[str | _Inline]:
    """Paragraph (or list item) text with inline equations cut out, hyphenation repaired
    between rows. A list item's text starts after its marker."""
    if block.code:
        return [block.text or ""]  # kept as printed: lines, indentation, no inline maths
    pieces: list[str | _Inline] = []
    rows = list(block.rows)
    if block.marker is not None and block.marker.end:
        first = rows[0]
        rows[0] = _Row(first.chars[block.marker.end :], column=first.column)
        _carry_page_math(first, rows[0], block.marker.end)
    letters = _BoldLetters.start()
    for row in rows:
        row_pieces: list[str | _Inline] = []
        cursor = 0
        info = _CharInfo.of(row, document=document)
        raw, kinds = _script_glyphs(row, info, document=document)
        runs = _inline_runs(row, info, kinds, document=document)
        page_latex: dict[tuple[int, int], str] = {}
        if row.page_checked:
            runs, page_latex = _with_page_spans(row, runs)
        found = _bold_letter_runs(row, runs, letters, document=document)
        found = sorted(
            [
                *(
                    run
                    for run in found
                    if not any(row.chars[i].c == PICTURE_CHAR for i in range(run[0], run[1]))
                ),
                *(
                    (i, i + 1, [EQUATION_IMAGE_REASON])
                    for i, c in enumerate(row.chars)
                    if c.c == PICTURE_CHAR
                ),
            ]
        )
        if _loose_scripts(row, info, raw, kinds, found):
            block.flat_scripts = True  # raised or lowered text that is written flat
        for start, end, reasons in found:
            row_pieces.append(_marked(row.chars, kinds, cursor, start))
            if _gap_before(row.chars, start):
                row_pieces.append(" ")
            chars = row.chars[start:end]
            text = (
                ""
                if reasons == [EQUATION_IMAGE_REASON]
                else _clean("".join(c.styled for c in chars))
            )
            ink = [c for c in chars if not c.blank]
            fonts = sorted({c.font for c in ink})
            score, _ = equation_score(text, fonts, any(c.flags & _SUPERSCRIPT_FLAG for c in ink))
            box = _clear_of_punctuation(_union((c.x0, c.y0, c.x1, c.y1) for c in ink), row, end)
            status = (
                ("span" if (start, end) in page_latex else "layout") if row.page_checked else None
            )
            row_pieces.append(
                _Inline(
                    box,
                    text,
                    score,
                    reasons,
                    _dominant_size([row]),
                    _regular_letters(ink),
                    page_latex.get((start, end)),
                    status,
                )
            )
            cursor = end
            if _gap_after(row.chars, start, end):
                row_pieces.append(" ")
        row_pieces.append(_marked(row.chars, kinds, cursor, len(row.chars)))
        _append_row(pieces, row_pieces, document=document)
    return _strip_teacher_tags(_tidy(pieces))


# OpenStax's teacher-support tags at the start of a paragraph: the level of student a
# prompt is for, "[BL][OL] Describe a scenario ...", "[AL] ...", "[All] ...". They are
# notes to a teacher, not words of the lesson, and a screen reader would read them out
# letter by letter. Only these four, in capitals as OpenStax prints them, and only before
# other text; "[1]", "[Visual]" and a "[BL]" in the middle of a sentence stay.
_TEACHER_TAGS = re.compile(r"^\s*(?:\[(?:BL|OL|AL|All)\]\s*)+")


def _strip_teacher_tags(pieces: list[str | _Inline]) -> list[str | _Inline]:
    """`pieces` without OpenStax level tags at the start (see `_TEACHER_TAGS`); unchanged
    when nothing but the tags would be left."""
    if not pieces or not isinstance(pieces[0], str):
        return pieces
    stripped = _TEACHER_TAGS.sub("", pieces[0], count=1)
    if stripped == pieces[0]:
        return pieces
    if not stripped.strip() and len(pieces) == 1:
        return pieces
    rest = pieces[1:]
    return [stripped, *rest] if stripped else rest


def _leave_out_teacher_tags(source: say_once.PageSource, block: _Block) -> None:
    """Tell the say-once measure which words `_strip_teacher_tags` took off the start of
    `block`: the tags are no words of the lesson, so no element says them."""
    if not block.rows or not isinstance(block.pieces[0] if block.pieces else None, str):
        return
    chars = block.rows[0].chars[(block.marker.end if block.marker is not None else 0) :]
    found = _TEACHER_TAGS.match("".join(c.c for c in chars))
    if found and not _TEACHER_TAGS.match(str(block.pieces[0])):
        source.leave_out_at(
            "teacher_tag",
            [(c.x0, c.y0, c.x1, c.y1) for c in chars[: found.end()] if not c.blank],
        )


def _clear_of_punctuation(box: BBox, row: _Row, end: int) -> BBox:
    """Pull an inline equation's right edge in so its padded crop stops short of the
    punctuation that follows it ("d_0," in "positions d_0, d_f and ...", "in 1.5 s?").

    The comma stays in the paragraph text after the equation's marker; if the crop shows
    it too, the model transcribes it and the output reads "d0, ,". The edge moves left by
    at most CROP_PAD, so a crop with the default padding still holds every glyph of the
    equation.
    """
    after = next((c for c in row.chars[end:] if not c.blank), None)
    if after is None or after.c not in ",.;:?":
        return box
    x1 = max(min(box[2], after.x0 - CROP_PAD), box[2] - CROP_PAD, box[0] + 0.5)
    return (box[0], box[1], x1, box[3])


REASON_WRAPPED_MATHS = "wrapped across lines"

# A line of a paragraph ends on one of these (TeX breaks inline maths after a relation or a
# binary operator; Word may break before one) when one formula goes on at the next line.
_WRAP_OPERATORS = frozenset("=+−×·<>≈≠≤≥∼→±∓∝*")  # noqa: RUF001
_MAX_WRAP_CONNECTOR = 2  # operator characters between the two halves


def _connector(strings: list[str]) -> str | None:
    """The operator characters in `strings` (blanks aside), or None when any of them is a
    word or other text."""
    joined = "".join("".join(s.split()) for s in strings)
    if all(c in _WRAP_OPERATORS for c in joined) and len(joined) <= _MAX_WRAP_CONNECTOR:
        return joined
    return None


_RELATIONS = frozenset("=<>≈≠≤≥∼→∝")  # noqa: RUF001


def _wraps_here(first: str, second: str, connector: str) -> bool:
    """Do the two halves read as one formula broken at this place? A relation sign at the
    break always says so. A plus or minus does only when its half is a longer expression
    (it has a space in it): "Na+" at the end of a line and "Cl-" at the start of the next
    are two ions, not a sum."""
    if connector:
        return any(c in _RELATIONS for c in connector) or " " in first
    if first[-1] in _RELATIONS or second[0] in _RELATIONS:
        return True
    return (first[-1] in _WRAP_OPERATORS and " " in first) or (
        second[0] in _WRAP_OPERATORS and " " in second
    )


def _wrapped_inline(pieces: list[str | _Inline], row_pieces: list[str | _Inline]) -> bool:
    """Is the inline equation that starts the next row the rest of the one that ends this
    row? Then they are one equation (in place) and the next row's pieces go on after it.

    "x + y =" at the end of a line and "z + w" at the start of the next is one formula that
    wrapped, not two. It needs the two to touch: only operator characters (or nothing)
    between them, and either half next to the break is an operator ("... = " / "z") or
    the connector is one. Two formulas with a word between them, or two variables set side
    by side, stay two."""
    tail: list[str] = []
    index = len(pieces) - 1
    while index >= 0 and isinstance(piece := pieces[index], str):
        tail.append(piece)
        index -= 1
    head: list[str] = []
    start = 0
    while start < len(row_pieces) and isinstance(piece := row_pieces[start], str):
        head.append(piece)
        start += 1
    if index < 0 or start >= len(row_pieces):
        return False
    first, second = pieces[index], row_pieces[start]
    if not (isinstance(first, _Inline) and isinstance(second, _Inline)):
        return False
    if not (first.text and second.text) or first.page != second.page:
        return False
    connector = _connector([*tail, *head])
    if connector is None:
        return False
    if not _wraps_here(first.text, second.text, connector):
        return False
    words = [first.text, connector, second.text]
    pieces[index:] = [
        _Inline(
            _union([first.bbox, second.bbox]),
            _clean(" ".join(w for w in words if w)),
            max(first.score, second.score),
            [
                *first.reasons,
                *(r for r in second.reasons if r not in first.reasons),
                REASON_WRAPPED_MATHS,
            ],
            first.font_size,
            (first.regular or "") + (second.regular or "") or None,
            None,
            None,
            first.page,
        )
    ]
    pieces.extend(row_pieces[start + 1 :])
    return True


def _append_row(
    pieces: list[str | _Inline], row_pieces: list[str | _Inline], *, document: _Document
) -> None:
    """Join the next row of a paragraph, repairing end-of-line hyphenation and inline maths
    that wraps from one row to the next (`_wrapped_inline`)."""
    if not pieces:
        pieces.extend(row_pieces)
        return
    if _wrapped_inline(pieces, row_pieces):
        return
    last, first = pieces[-1], row_pieces[0] if row_pieces else ""
    if isinstance(last, str) and isinstance(first, str):
        tail, separator = _line_break(last.rstrip(), first.lstrip(), document=document)
        pieces[-1] = tail + separator
        if not separator:
            row_pieces[0] = first.lstrip()
    else:
        pieces.append(" ")
    pieces.extend(row_pieces)


def _tidy(pieces: list[str | _Inline]) -> list[str | _Inline]:
    """Merge adjacent strings, clean them, keep single spaces around inline maths."""
    merged: list[str | _Inline] = []
    for piece in pieces:
        if isinstance(piece, str) and merged and isinstance(merged[-1], str):
            merged[-1] += piece
        else:
            merged.append(piece)
    out: list[str | _Inline] = []
    for i, piece in enumerate(merged):
        if isinstance(piece, _Inline):
            out.append(piece)
            continue
        text = piece.translate(_TEXT_MAP)
        text = "".join(c for c in text if c.isspace() or unicodedata.category(c) != "Cc")
        if i > 0 and text.lstrip()[:1] in tuple(".,;:!?)"):
            text = text.lstrip()  # no space between a marker and "." or ","
        lead = " " if i > 0 and text[:1].isspace() else ""
        trail = " " if i < len(merged) - 1 and text[-1:].isspace() else ""
        body = " ".join(text.split())
        text = (lead + body + trail) if body else (" " if lead or trail else "")
        if text:
            out.append(text)
    return out


def _attach_captions(blocks: list[_Block], figures: list[_Figure], *, document: _Document) -> None:
    """Nearest caption (below preferred) within CAPTION_MAX_GAP; one per figure."""
    pairs = []
    for c_index, block in enumerate(blocks):
        if block.kind != "caption":
            continue
        for f_index, figure in enumerate(figures):
            fx0, fy0, fx1, fy1 = figure.bbox
            cx0, cy0, cx1, cy1 = block.bbox
            overlap = min(fx1, cx1) - max(fx0, cx0)
            if overlap < 0.3 * min(fx1 - fx0, cx1 - cx0):
                continue
            below, above = cy0 - fy1, fy0 - cy1
            if -2 <= below <= CAPTION_MAX_GAP:
                pairs.append((below, c_index, f_index))
            elif -2 <= above <= CAPTION_MAX_GAP:
                pairs.append((above + 2.0, c_index, f_index))  # small bias towards below
    used_captions: set[int] = set()
    for _, c_index, f_index in sorted(pairs):
        if c_index in used_captions or figures[f_index].caption is not None:
            continue
        figures[f_index].caption = blocks[c_index].text
        figures[f_index].flat_scripts = any(
            kind for row in blocks[c_index].rows for kind in _script_kinds(row, document=document)
        )
        pieces = _paragraph_pieces(blocks[c_index], document=document)
        if any(isinstance(piece, _Inline) for piece in pieces):
            figures[f_index].caption_pieces = pieces
            figures[f_index].caption_size = blocks[c_index].font_size
        used_captions.add(c_index)
    for index, block in enumerate(blocks):
        if block.kind == "caption":
            block.kind = Kind.PARAGRAPH if index not in used_captions else "attached"


_TEXT_BLOCKS = (Kind.PARAGRAPH, Kind.LIST_ITEM)  # blocks whose text may hold inline maths
_SENTENCE_END = ".?!:…"
CODE_MIN_SHORT = 6  # glyphs that are enough for a block of two lines or more
CODE_SPACE = 0.5  # x the advance: a narrower space in the text layer is not a printed one
CODE_LINE = 1.6  # x font size: a line pitch bigger than this has a blank line in it
CODE_GAP = 1.8  # x the advance: a gap this wide between two characters is a missing space
CODE_MAX_PITCH = 3.4  # x font size: line to line in code, one blank line included
