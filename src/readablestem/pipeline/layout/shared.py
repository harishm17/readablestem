"""The types the stages hand to each other (`_Row`, `_Block`, `_Run`, `_Figure`, `_Region`,
`_Display`, `_Path`, `_Inline`, `_CharInfo`, `_Marker`, `_PageBlocks`, `_Context`) and the few
helpers those types, or several stages, need. A helper that only one stage uses is in that
stage's module.
"""

from __future__ import annotations

import bisect
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property

import pymupdf

from readablestem.core.types import BBox, Kind, TableGrid
from readablestem.pipeline import chem, glyph_math, hyphens, named_links
from readablestem.pipeline.layout.context import _PLAIN, PageStatus, _Document
from readablestem.pipeline.layout.glyphs import _Char, _char_class, _clean, _font_family, _union

SCRIPT_RATIO = 0.85  # characters smaller than this x the row size are sub/superscripts

# Raised and lowered text in a paragraph (`_script_kinds`): a glyph smaller than the row's
# text is a script when it sits this far (x the row size) off the baseline; a full-size
# glyph carrying MuPDF's superscript flag when it is raised this far. Word and LibreOffice
# lower a subscript by 8 % of its size, TeX by 15 %, a raised mark by 33 % or more.
SCRIPT_OFFSET = 0.04
# A small capital sits on the baseline, to within this much (x the row size): Word and TeX
# set it at 0, the least a script is lowered is about 0.1 (`mark_small_capitals`).
SMALL_CAP_BASELINE = 0.05
SCRIPT_MIN_RATIO = 0.62  # the smallest a script is, beside the text it belongs to
SCRIPT_MAX_LIFT = 0.7  # the furthest off the baseline a script is (x the text size)
_DOT_BULLETS = frozenset("\u00b7\u2219\u22c5")  # bullets at the start of a line, maths elsewhere

# Relations that make a maths row an equation of its own (arrows also appear in limits).
_ROW_RELATIONS = frozenset("=<>\u2264\u2265\u2260\u2248\u2261\u221d")

# Upright words that still belong to maths (functions, units) and English two-letter words.
_MATH_WORDS = frozenset(
    (
        *("sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh"),
        *("arcsin", "arccos", "arctan", "log", "ln", "lg", "exp", "lim", "max", "min"),
        *("sup", "inf", "det", "dim", "ker", "arg", "deg", "gcd", "lcm", "mod", "var"),
        *("cov", "const", "sgn", "tr", "mol", "rad", "proj", "rank", "diag", "grad", "curl"),
        *("erf", "coth", "sech", "csch", "arsinh", "arcosh", "artanh"),
    )
)


_SUPERSCRIPT_FLAG = 1


@dataclass(eq=False)
class _Row:
    """Characters on one visual line of one column, left to right."""

    chars: list[_Char]
    vertical: bool = False  # rotated text: never merged with other rows
    diagonal: bool = False  # ... at an angle that is not a multiple of 90 degrees
    column: str = "L"  # "L", "R" or "S" (spans both columns / single-column page)
    kind: str = "text"  # "math" | "frag" | "text", see _classify_row
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    absorbed: bool = False  # belongs to a figure (labels, text inside a diagram)
    marker: _Marker | None = None  # the row starts a list item (see _list_markers)
    region: _Region | None = None  # the display equation it belongs to (_equation_regions)
    # An exercise number ("18.", "4)") cut off the front of a maths row: text, never maths.
    exercise_number: bool = False
    # An equation number made maths only for standing within `NUMBER_REACH` font sizes of a
    # piece of one (`_resolve_fragments`): `_equation_regions` gives it to a piece that has no
    # number yet, or sets it back to text.
    far_number: bool = False
    # The destinations the file names for a footnote that starts at this row (`_mark_notes`).
    note: tuple[str, ...] = ()
    # A line of a table of contents with its leaders and page number cut off
    # (`_contents_rows`): an entry of its own, at this nesting level (1 = outermost).
    contents_level: int = 0
    # A capital letter alone on its line, in a sequence of such lines (A, B, C ... of a
    # glossary or an index): a heading of its own (`_letter_rows`).
    letter: bool = False
    # A row of a function defined by cases (`_mark_case_rows`): its columns may stand
    # twice as far apart as those of an aligned row.
    case_row: bool = False
    # The page math pass (ADR 0006, `pipeline.page_math_reconcile`), only with a plan for
    # the page: `page_checked` says it ran; `page_inline` are the inline spans it placed in
    # this row (character indexes into `chars`, and its LaTeX); `page_nonmath` the stretches
    # it called look-alikes of maths.
    page_checked: bool = False
    page_inline: list[tuple[int, int, str]] = field(default_factory=list)
    page_nonmath: list[tuple[int, int]] = field(default_factory=list)

    @cached_property
    def ink(self) -> list[_Char]:
        return [c for c in self.chars if not c.blank] or self.chars

    @cached_property
    def bbox(self) -> BBox:
        return (
            min(c.x0 for c in self.ink),
            min(c.y0 for c in self.ink),
            max(c.x1 for c in self.ink),
            max(c.y1 for c in self.ink),
        )

    @cached_property
    def plain(self) -> list[_Char]:
        """The ink without the glyphs of inline fractions: they set the row's size and
        baseline no more than a sub- or superscript does."""
        ink = [c for c in self.ink if not c.stacked]
        if any(c.c in chem.ARROW_HEADS for c in ink):  # a label set over an arrow is no text
            ink = [c for c, label in zip(ink, _arrow_label_flags(ink), strict=True) if not label]
        return ink or self.ink

    @cached_property
    def size(self) -> float:
        """Font size covering most characters (the glyphs of inline fractions left out, as in
        `baseline`). A row whose small glyphs are all scripts of
        its larger ones (a nuclide's "92 U -> 234": five script glyphs, two full-size) is set
        in the larger size: the small glyphs are at least `SCRIPT_MIN_RATIO` of it, sit off
        its baseline by at least 4 % and at most `SCRIPT_MAX_LIFT` of it, and no more than
        60 % of it from a larger glyph; and the larger glyphs hold a letter and no word of three
        letters. The numbers of a fraction (beside an operator, or in a sentence) are not
        scripts of anything."""
        counts = Counter(round(c.size * 2) / 2 for c in self.plain)
        if not counts:  # a row with no characters has no size; the body default, as elsewhere
            return 10.0
        size = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        larger = [s for s in counts if s > 0 and SCRIPT_MIN_RATIO <= size / s <= SCRIPT_RATIO]
        if not larger:
            return size
        big = max(larger, key=lambda s: (counts[s], s))
        large = [c for c in self.plain if round(c.size * 2) / 2 == big]
        if not any(c.c.isalpha() for c in large):
            return size  # nothing to hang scripts on: an operator and the numbers of a fraction
        letters = 0  # a word in the larger size: a sentence with small numbers in it (a fraction)
        for c in sorted(large, key=lambda g: g.x0):
            letters = letters + 1 if c.c.isalpha() else 0
            if letters >= 3:
                return size
        base = statistics.median(c.baseline for c in large)
        reach = 0.6 * big  # a script sits next to its base, with at most a space between
        run: list[_Char] = []  # small glyphs side by side: one script, the end glyphs touch
        for c in [*sorted(self.plain, key=lambda g: g.x0), None]:
            if c is not None and round(c.size * 2) / 2 == size:
                if not SCRIPT_OFFSET * big <= abs(c.baseline - base) <= SCRIPT_MAX_LIFT * big:
                    return size
                run.append(c)
                continue
            if run and not any(
                max(g.x0 - b.x1, b.x0 - g.x1) <= reach for g in (run[0], run[-1]) for b in large
            ):
                return size
            run = []
        return big

    @cached_property
    def baseline(self) -> float:
        main = [c.baseline for c in self.plain if abs(c.size - self.size) <= 0.5]
        every = main or [c.baseline for c in self.plain]
        return statistics.median(every) if every else 0.0  # no characters: nothing to sit on

    @cached_property
    def text(self) -> str:
        return "".join(c.c for c in self.chars)

    @cached_property
    def styled_text(self) -> str:
        """`text` with styled letters as Unicode math letters (equation text layers only)."""
        return "".join(c.styled for c in self.chars)

    @cached_property
    def clean(self) -> str:
        return _clean(self.text)

    @cached_property
    def bold(self) -> bool:
        letters = [c for c in self.ink if c.c.isalnum()]
        return bool(letters) and all(c.bold for c in letters)

    @cached_property
    def code(self) -> bool:
        """A line of program code: monospaced throughout (`CODE_MONO_SHARE` of its glyphs),
        upright text. Whether a run of such lines is a code block is `_code_run`'s call."""
        ink = [c for c in self.ink if not c.blank]
        return (
            bool(ink)
            and not self.vertical
            and not self.diagonal
            and sum(c.mono for c in ink) >= CODE_MONO_SHARE * len(ink)
        )

    @cached_property
    def family(self) -> str:
        """The typeface most letters are set in (maths fonts and symbols left out)."""
        counts = Counter(_font_family(c.font) for c in self.ink if c.c.isalnum() and not c.math)
        return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if counts else ""

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    def is_script(self, c: _Char) -> bool:
        """Is `c` a sub- or superscript in this row?

        Smaller than the row's text, or raised with MuPDF's superscript flag. The flag
        alone is not trusted: MuPDF sets it on any span that starts above the previous
        one, which in TeX output is most of a line after an inline fraction. A small
        capital (`_Char.small_cap`, set by `mark_small_capitals`) is small and on the
        baseline: text, never a script. Any test of a glyph's size against its line's that
        means "a script" skips `small_cap` glyphs the same way.
        """
        if c.blank or c.small_cap:
            return False
        if c.size < SCRIPT_RATIO * self.size:
            return True
        return bool(c.flags & _SUPERSCRIPT_FLAG) and c.baseline < self.baseline - 0.2 * self.size


def mark_small_capitals(chars: list[_Char]) -> list[_Char]:
    """`chars` with `small_cap` set on the small capitals of a line, and, in a word that also
    has full-size capitals, lower-cased: "L" and "OTKA" (Word, LibreOffice and TeX with a
    scaled font print the lower-case letters of "Lotka" as capitals at 80 %) read "Lotka".

    The unit is a word of capital letters in one font, all on the line's baseline
    (`SMALL_CAP_BASELINE`: a script is off it), set off from what is round it by anything but a
    letter or digit. A letter of it smaller than the word's largest (`SCRIPT_RATIO`) is a small
    capital, and a small capital is a lower-case letter of the source: the word is lower-cased
    there ("Lotka", "LongMemEval" set as `L ONG M EM E VAL`), and none of it is a script. A word
    of small capitals only is lower-cased too when the line shows what it is: its font and size
    are those of a small capital found on the same line ("and" in `P OLICIES AND P ROCEDURES`).
    On a line with no such word it is left alone, as is a capital next to a lower-case letter or
    a digit: an all-small-caps "OTKA" by itself does not say whether it is a name, an acronym or
    the name of a function in a formula (`OPT(x)` in a set, measured: read as text it cut that
    formula in three), so it stays what the size rule made of it before."""
    sizes = {round(c.size * 2) / 2 for c in chars if not c.blank}
    if len(sizes) < 2:
        return chars
    row = _Row(chars)
    size, base = row.size, row.baseline
    reach = SMALL_CAP_BASELINE * size

    def capital(c: _Char) -> bool:
        return c.c.isalpha() and c.c.isupper() and not c.math and abs(c.baseline - base) <= reach

    def face(c: _Char) -> tuple[str, float]:
        return c.font, round(c.size * 2) / 2

    out = list(chars)
    words: list[tuple[int, int]] = []
    n, i = len(chars), 0
    while i < n:
        if not capital(chars[i]):
            i += 1
            continue
        j = i + 1
        while j < n and capital(chars[j]) and chars[j].font == chars[i].font:
            j += 1
        start, i = i, j
        if (start and chars[start - 1].c.isalnum()) or (j < n and chars[j].c.isalnum()):
            continue
        words.append((start, j))
        largest = max(c.size for c in chars[start:j])
        small = [k for k in range(start, j) if chars[k].size < SCRIPT_RATIO * largest]
        for k in small:
            out[k] = replace(chars[k], c=chars[k].c.lower(), small_cap=True)
    faces = {face(c) for c in out if c.small_cap}
    for start, j in words if faces else ():
        if all(face(chars[k]) in faces for k in range(start, j)):
            for k in range(start, j):
                out[k] = replace(chars[k], c=chars[k].c.lower(), small_cap=True)
    return out


@dataclass(frozen=True)
class _Display:
    """Unrotated page coordinates (what PyMuPDF extracts) -> display coordinates.

    Plain arithmetic instead of pymupdf.Rect/Point: a dense plot has thousands of paths,
    and building PyMuPDF objects for each point costs seconds per page.
    """

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float
    rotated: bool

    @classmethod
    def of(cls, page: pymupdf.Page) -> _Display:
        m = page.rotation_matrix
        return cls(m.a, m.b, m.c, m.d, m.e, m.f, page.rotation % 360 != 0)

    def point(self, x: float, y: float) -> tuple[float, float]:
        if not self.rotated:
            return x, y
        return x * self.a + y * self.c + self.e, x * self.b + y * self.d + self.f

    def vector(self, dx: float, dy: float) -> tuple[float, float]:
        if not self.rotated:
            return dx, dy
        return dx * self.a + dy * self.c, dx * self.b + dy * self.d

    def box(self, rect: Sequence[float]) -> BBox:
        if not self.rotated:
            return rect[0], rect[1], rect[2], rect[3]
        (x0, y0), (x1, y1) = self.point(rect[0], rect[1]), self.point(rect[2], rect[3])
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)  # /Rotate is 90k degrees


def _arrow_spans(chars: Sequence[_Char]) -> list[tuple[int, int]]:
    """(index of the first glyph, index of the head) of each reaction arrow in `chars`: the
    head, and the dashes that are its shaft when the font sets a long arrow as a run of
    minus signs ending in the head (`−−−→`)."""
    spans = []
    for i, c in enumerate(chars):
        if c.c not in chem.ARROW_HEADS:
            continue
        first = i
        while (
            first > 0
            and chars[first - 1].c in _CHEM_DASHES
            and chars[first].x0 - chars[first - 1].x1 <= CHEM_WORD_GAP * c.size
        ):
            first -= 1
        spans.append((first, i))
    return spans


def _over_arrow(chars: Sequence[_Char], span: tuple[int, int], c: _Char) -> bool:
    """Is `c` a script-size glyph set over or under the arrow `span` of `chars`: its centre
    within the arrow's length and its baseline off the arrow's own? The arrow is the line's
    text size (`size`: a long label can outnumber the glyphs of the formulas)."""
    first, head = span
    size = chars[head].size
    if c.blank or c.size > SCRIPT_RATIO * size or c.c in chem.ARROW_HEADS:
        return False
    if abs(c.baseline - chars[head].baseline) < 0.25 * size:
        return False  # a script of the line, at the height of the arrow
    return chars[first].x0 <= (c.x0 + c.x1) / 2 <= chars[head].x1


def _arrow_label_flags(chars: Sequence[_Char]) -> list[bool]:
    """For each glyph of `chars`, is it part of a label set over or under an arrow?"""
    flags = [False] * len(chars)
    for span in _arrow_spans(chars):
        for k, c in enumerate(chars):
            if not flags[k] and _over_arrow(chars, span, c):
                flags[k] = True
    return flags


def _box_key(box: BBox) -> tuple[int, int, int, int]:
    return round(box[0]), round(box[1]), round(box[2]), round(box[3])


def _word_lengths(
    chars: list[_Char], classes: list[str], script: list[bool], *, document: _Document
) -> tuple[list[int], list[bool]]:
    """For letters (class L), the length of the plain-letter word they belong to, and
    whether that word is a maths word (`sin`, `det`, `lim`, units such as `mol`).

    Italic letters run together are maths variables set in a text font (beamer's
    sans-serif maths: "at" in v = v0 + at), not a word, when the run is short (three
    letters at most) and the row reads as maths around them: it also has upright letters
    (prose with a formula in it), or it has a relation sign and no longer italic run (a
    formula on its own; an italic sentence has longer words). Each letter then counts
    on its own.
    """
    lengths = [0] * len(chars)
    maths_word = [False] * len(chars)
    upright = any(k == "L" and not c.italic for k, c in zip(classes, chars, strict=True))
    longest = _longest_italic_run(chars, classes, script)
    if not upright and any(c.c in _ROW_RELATIONS for c in chars):
        upright = longest <= 3
    # An italic sentence with a function name in it ("... for all 1 <= k <= K", log(n) upright)
    # has italic words, not variables: a short English word beside a longer italic one is text.
    italic_text = longest > 3
    i = 0
    while i < len(chars):
        if classes[i] != "L":
            i += 1
            continue
        j = i
        while (
            j < len(chars)
            and classes[j] == "L"
            and script[j] == script[i]
            # TeX sets "ln" upright and the variable after it in italic: two words, not one
            and (not document.tex_made or chars[j].italic == chars[i].italic)
        ):
            j += 1
        italic_variables = upright and j - i <= 3 and all(c.italic for c in chars[i:j])
        word = "".join(c.c for c in chars[i:j]).lower()
        if italic_variables and italic_text and j - i > 1 and word in hyphens.words():
            italic_variables = False
        named = (
            document.tex_made
            and not italic_variables
            and not script[i]
            and not any(c.italic for c in chars[i:j])
            and glyph_math.function_name(chars, i, j)
        )
        for k in range(i, j):
            lengths[k] = 1 if italic_variables else j - i
            maths_word[k] = named or word in _MATH_WORDS
        i = j
    return lengths, maths_word


def _longest_italic_run(chars: list[_Char], classes: list[str], script: list[bool]) -> int:
    longest = run = 0
    for c, k, small in zip(chars, classes, script, strict=True):
        run = run + 1 if k == "L" and c.italic and not small else 0
        longest = max(longest, run)
    return longest


@dataclass
class _CharInfo:
    """Per-character maths evidence for one row."""

    classes: list[str]
    script: list[bool]
    words: list[int]
    maths_words: list[bool]

    @classmethod
    def of(cls, row: _Row, *, document: _Document = _PLAIN) -> _CharInfo:
        classes = ["M" if c.stacked else _char_class(c) for c in row.chars]
        first = next((i for i, c in enumerate(row.chars) if not c.blank), None)
        if (
            first is not None
            and row.chars[first].c in _DOT_BULLETS
            and (first + 1 == len(row.chars) or row.chars[first + 1].blank)
        ):
            classes[first] = "P"  # "\u00b7 Measure the ramp": a bullet, not a product
        script = [row.is_script(c) for c in row.chars]
        words, maths_words = _word_lengths(row.chars, classes, script, document=document)
        return cls(classes, script, words, maths_words)

    def mathy(self, i: int) -> bool:
        kind = self.classes[i]
        if kind in "MDOB":
            return True
        return kind == "L" and (self.words[i] == 1 or self.script[i] or self.maths_words[i])


@dataclass
class _Inline:
    """An inline equation found inside a paragraph row."""

    bbox: BBox
    text: str
    score: float
    reasons: list[str]
    font_size: float
    regular: str | None = None  # `Element.regular_letters`
    # The page math pass (ADR 0006): its LaTeX reading of this run, and whether it placed
    # the run ("span") or only layout found it ("layout"); None without a pass.
    page_latex: str | None = None
    page_status: PageStatus | None = None
    # The page it is on, only when that is not its paragraph's page (the paragraph goes on
    # over a page break, `_join_page_splits`).
    page: int | None = None


CHEM_WORD_GAP = 0.25  # x glyph size: a wider gap between two glyphs of a row ends a word
_CHEM_DASHES = frozenset("\u2212-\u2013")


@dataclass(frozen=True)
class _Marker:
    """The list marker that starts a row: what is printed and where the item text starts."""

    text: str  # as printed: "a.", "(iii)", "3)", "\u2022"
    ordered: bool
    end: int  # index in row.chars where the item text starts (after the marker)
    x: float  # left edge of the item text: the same for every item of one list level


def _tokens(row: _Row) -> list[tuple[int, int]]:
    """(start, end) character ranges of the ink tokens of a row: split at blanks and at
    gaps of at least 0.3 x font size (a marker and its text in separate spans)."""
    tokens: list[tuple[int, int]] = []
    start = None
    chars = row.chars
    for i, c in enumerate(chars):
        if c.blank:
            if start is not None:
                tokens.append((start, i))
                start = None
            continue
        if start is not None and c.x0 - chars[i - 1].x1 >= 0.3 * row.size:
            tokens.append((start, i))
            start = None
        if start is None:
            start = i
    if start is not None:
        tokens.append((start, len(chars)))
    return tokens


@dataclass(eq=False, slots=True)
class _Path:
    bbox: BBox
    kind: str  # "hrule" | "vrule" | "radical" | "shape"
    curved: bool  # has curves or diagonal lines (charts, diagrams; not tables or boxes)
    filled: bool  # a visible filled area (bars in a bar chart, shapes)
    source: int = -1  # index in the page's get_cdrawings list (handwriting is left out)
    frame: bool = False  # an outline along the edges of its own box only (a frame's shape)
    rules: bool = False  # paper rules: long separate horizontal and vertical lines only
    open_stroke: bool = False  # a stroked line or curve that does not close: a pen mark's shape
    axis: bool = False  # holds one of two lines meeting at a corner: a plot's axes (`_mark_axes`)
    ruler: bool = False  # holds a line with tick marks across or on it, or one of the ticks
    # The L pairs (horizontal line, vertical line) the path holds a line of (`_mark_axes`).
    axes: tuple[tuple[_Straight, _Straight], ...] = ()
    plot: bool = False  # one of those Ls has evidence of a plot (`_mark_plots`)
    # A ruler whose ticks all stand on one side of its line, a letter cell apart: an
    # answer field's cells when no label is near (`_answer_fields`).
    comb: bool = False
    # Where a curved open stroke's subpaths start and end (display space): a plotted
    # line starts at its axes' corner (`_mark_plots`).
    ends: tuple[tuple[float, float], ...] = ()
    # Set only where a later test reads them (`_page_paths`): an open stroke 12 pt or more
    # that has a curve or lines in two directions; a curved closed or filled shape's sRGB
    # colour, and the share of its box a filled one covers.
    turns: bool = False
    colour: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fill_share: float = 0.0
    # The rectangles of a filled shape drawn as several (the bars of one series of a chart
    # are one drawing, `re` after `re`): what the table detector counts as bars. Empty for
    # any other path.
    rects: tuple[BBox, ...] = ()
    # Painted as a fill with no stroke: how a glyph drawn as an outline is painted
    # (`_glyph_outlines`).
    fill_only: bool = False
    # The straight lines (and the chords of the curves) of the path in display space, when
    # it is drawn by lines alone and has few (MAX_EDGES): its strokes, which a label's
    # distance is measured to (`_stroke_gap`). Empty: the path's box stands for it.
    edges: tuple[tuple[float, float, float, float], ...] = ()
    # Pen evidence of an open stroke, set in `_page_paths`: the page type's pen tests at
    # any length (`page_type.pen_line`: a signature), or, for a short stroke of straight
    # lines, those lines (`_short_lines`: a tick, a bond), which `_hand_strokes` judges
    # with the rest of their group.
    pen: bool = False
    lines: tuple[tuple[float, float, float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class _Straight:
    """A horizontal or vertical line of a path: at `c` (y for a horizontal line, x for a
    vertical one) from `lo` to `hi` along it, display space."""

    horizontal: bool
    c: float
    lo: float
    hi: float

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        if self.horizontal:
            return (self.lo, self.c), (self.hi, self.c)
        return (self.c, self.lo), (self.c, self.hi)


class _RowIndex:
    """Rows sorted by top edge, for finding rows within a vertical window quickly."""

    def __init__(self, rows: list[_Row]) -> None:
        self.rows = sorted(rows, key=lambda r: r.y0)
        self.tops = [r.y0 for r in self.rows]
        self.tallest = max((r.height for r in self.rows), default=0.0)
        self.largest = max((r.size for r in self.rows), default=0.0)  # font size

    def near(self, y0: float, y1: float) -> list[_Row]:
        """Rows whose vertical extent meets [y0, y1]."""
        lo = bisect.bisect_left(self.tops, y0 - self.tallest)
        hi = bisect.bisect_right(self.tops, y1)
        return [r for r in self.rows[lo:hi] if r.y1 >= y0]


@dataclass(eq=False)
class _Region:
    """One display equation: the maths rows that belong together, the rules it uses
    (fraction bars, radicals) and the rows of its equation number, kept apart."""

    rows: list[_Row] = field(default_factory=list)
    rules: list[_Path] = field(default_factory=list)
    labels: list[_Row] = field(default_factory=list)
    box: BBox = (0.0, 0.0, 0.0, 0.0)  # rows and rules, while lines are being stacked
    # Placed by the page math pass instead of by the rules above: `forced`; its LaTeX
    # reading; and, on a page that had a pass, whether the pass confirmed the region
    # ("span") or only layout found it ("layout").
    forced: bool = False
    page_pass: PageStatus | None = None
    latex: str | None = None


@dataclass(eq=False)
class _Figure:
    bbox: BBox
    images: int = 0  # raster images in it
    paths: int = 0  # vector paths in it
    labels: list[_Row] = field(default_factory=list)
    caption: str | None = None
    # The caption cut into text and inline maths (`_paragraph_pieces`), when it has any maths.
    caption_pieces: list[str | _Inline] | None = None
    caption_size: float | None = None
    xref: int = 0  # the image of a figure that is one raster image (0: inline, or several)
    flat_scripts: bool = False  # its caption has raised or lowered text, written flat
    equation: bool = False  # a picture of an equation (`_equation_pictures`)
    number: str | None = None  # ... and the equation number printed beside it
    fragments: bool = False  # a region of text fragments and short strokes (`_fragment_figures`)
    members: list[_Path] = field(default_factory=list)  # the vector paths it is drawn with
    # The box of what is drawn (the box before any label is added to it).
    drawing: BBox = field(init=False)

    def __post_init__(self) -> None:
        self.drawing = self.bbox

    @property
    def reasons(self) -> list[str]:
        reasons = ["raster image"] if self.images == 1 else []
        if self.images > 1:
            reasons.append(f"{self.images} raster images")
        if self.paths:
            reasons.append(f"vector drawing ({self.paths} paths)")
        if self.fragments:
            reasons.append(REASON_DRAWING_FRAGMENTS)
        return reasons


def _area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


REASON_DRAWING_FRAGMENTS = "drawing_fragments"


@dataclass
class _Context:
    body: float  # body font size (pt)
    pitch: float  # typical baseline-to-baseline distance of body text (pt)
    # The footnotes the file names, when it names any (`named_links`); None: it names none,
    # and a footnote is decided by its mark and its place (`_footnote`).
    notes: named_links.NamedNotes | None = None


@dataclass(eq=False)
class _Run:
    """Consecutive rows (in reading order) of one paragraph, heading or display equation.

    The extents are kept up to date as rows are added, so growing a run is O(1) per row
    (a crafted page with thousands of rows in one run must not be quadratic).
    """

    rows: list[_Row]
    kind: str  # "math" | "text"
    block_start: bool  # the run starts after a geometric break, not just a style change
    block_end: bool = True
    code: bool = False  # rows of program code, grouped whatever their indentation
    contents: bool = False  # a line of a table of contents: a list item of its own
    note: bool = False  # a footnote the file names, from its first line on (`_mark_notes`)
    box: BBox = (0.0, 0.0, 0.0, 0.0)  # union of the rows' boxes
    margin: float = 0.0  # left edge of the rows after the first (the first's when alone)

    def __post_init__(self) -> None:
        rows, self.rows = self.rows, []
        for row in rows:
            self.add(row)

    def add(self, row: _Row) -> None:
        if not self.rows:
            self.box, self.margin = row.bbox, row.x0
        else:
            self.box = _union([self.box, row.bbox])
            self.margin = row.x0 if len(self.rows) == 1 else min(self.margin, row.x0)
        self.rows.append(row)


@dataclass(eq=False)
class _Block:
    """A future Element: heading, paragraph, display equation, caption or figure."""

    kind: Kind | str  # Kind, or "caption" until captions are attached
    bbox: BBox
    rows: list[_Row] = field(default_factory=list)
    text: str | None = None
    pieces: list[str | _Inline] = field(default_factory=list)  # paragraphs: text + inline maths
    font_size: float | None = None
    bold: bool = False
    score: float | None = None
    reasons: list[str] = field(default_factory=list)
    caption: str | None = None
    caption_pieces: list[str | _Inline] | None = None  # figures: the caption's text and maths
    marker: _Marker | None = None  # list items: the marker of the first row
    origin: float = 0.0  # list items: left edge of their column's text (`_column_origins`)
    label: str | None = None  # display equations: the equation number beside them
    regular: str | None = None  # display equations: `Element.regular_letters`
    table: TableGrid | None = None  # tables: the grid built from the PDF (`_table_blocks`)
    # tables: the cells that are one formula, as inline equations of the table (`_cell_maths`)
    cell_inlines: dict[tuple[int, int], _Inline] = field(default_factory=dict)
    code: bool = False  # paragraphs: program code, lines and indentation kept (`_code_text`)
    term_pieces: int | None = None  # glossary entries: how many of `pieces` are the term
    contents_level: int = 0  # list items: an entry of a table of contents, at this level
    # Raised or lowered text this block's element cannot carry (`Element.script_runs`): it is
    # written flat, and a notice says so (`scripts_flattened`).
    flat_scripts: bool = False
    marked: str | None = None  # headings: `text` with its raised and lowered runs marked
    # Display equations placed by the page math pass: its LaTeX, and "span" or "layout".
    page_latex: str | None = None
    page_status: PageStatus | None = None
    # Paragraphs that go on over a page break: this block is the first part, `parts` the
    # later ones with their page numbers (`_join_page_splits`); a later part is `joined`,
    # stays in its page's blocks for the notices and the safety net, and makes no element.
    parts: list[tuple[int, _Block]] = field(default_factory=list)
    joined: bool = False


def _lead_end(row: _Row) -> tuple[int, Callable[[_Char], bool]] | None:
    """(where the lead term ends, the test for its style) when the line starts with one.

    A lead term is a stretch at the start of a line in bold, in italic or in small capitals
    that ends in a period or a colon and is followed by regular type on the same line: a
    few words, "Biodiversity. The variety of ...", "Bias: a tendency ...". A line that is
    in that style throughout has no lead (a heading, an emphasised paragraph), and neither
    has a letter or a number ("A.", "2.") or a variable (an italic x)."""
    chars = row.chars
    first = next((i for i, c in enumerate(chars) if not c.blank), None)
    if first is None:
        return None
    tests: list[Callable[[_Char], bool]] = [
        lambda c: c.bold,
        lambda c: c.italic and not c.math,
        lambda c: c.c.isupper() or not c.c.isalpha(),  # small capitals, checked below
    ]
    for number, test in enumerate(tests):
        if not test(chars[first]):
            continue
        end = first
        for i in range(first, len(chars)):
            if chars[i].blank:
                continue
            if not test(chars[i]) or (number == 2 and chars[i].c in ".:" and i > end):
                break
            end = i + 1
        else:
            continue  # the whole line is in the style
        if end < len(chars) and chars[end].c in ".:":
            end += 1  # a period or a colon set in regular type after the lead
        lead = chars[first:end]
        text = "".join(c.c for c in lead).strip()
        words = text.rstrip(".:").split()
        if (
            text[-1:] in (".", ":")
            and 1 <= len(words) <= MAX_TERM_WORDS
            and sum(c.isalpha() for c in text) >= 2
            and not any(c.math for c in lead)
            and (number < 2 or _small_capitals(lead))
            and any(not c.blank for c in chars[end:])
        ):
            return end, test
    return None


def _small_capitals(chars: Sequence[_Char]) -> bool:
    """Capitals of two sizes on one baseline: a large one where a word starts and smaller
    ones after it, the way small capitals are set."""
    letters = [c for c in chars if c.c.isalpha()]
    if len(letters) < 3 or not all(c.c.isupper() for c in letters):
        return False
    sizes = {round(c.size, 1) for c in letters}
    biggest = max(sizes)
    return (
        len(sizes) == 2
        and min(sizes) <= 0.9 * biggest
        and max(c.baseline for c in letters) - min(c.baseline for c in letters) <= 0.1 * biggest
    )


MAX_TERM_WORDS = 6  # a term is a few words; more is a sentence, and stays a paragraph


@dataclass
class _PageBlocks:
    number: int
    size: tuple[float, float]
    blocks: list[_Block]
    # Drawings that fill the page and were not made a figure because the page's ink was
    # rejected or not analysed (`_vector_figures`): the safety net keeps the page image.
    left_out: list[BBox] = field(default_factory=list)
    # On a page with less than a line of text: something painted lies outside every block
    # (`_painted`), so the safety net keeps the page image.
    unread: bool = False


# Consecutive lines set in a monospaced font (at least this share of each line's glyphs,
# and this many of them in all) are program code: a block of its own, with its line breaks
# and indentation (which decide a Python loop's body) kept (`_code_groups`).
CODE_MONO_SHARE = 0.8
