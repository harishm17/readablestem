"""Inline math: where a row's characters turn into inline equations (scripts, italic variables,
ellipses, brackets), the pictures of equations, chemistry and nuclides, and the trimming of a
run to what is math.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

import pymupdf

from readablestem.core.mathchars import base_char
from readablestem.pipeline import chem, equation_images, glyph_math, hyphens, script_marks
from readablestem.pipeline.layout.context import _PLAIN, _Document
from readablestem.pipeline.layout.drawings import BARE_EQUATION_NUMBER, EQUATION_NUMBER
from readablestem.pipeline.layout.figures import CAPTION, _prose_spans, _prose_words
from readablestem.pipeline.layout.glyphs import (
    _ACCENTS,
    _SIGNS,
    NOTE_MARK_FLAG,
    _Char,
    _font_traits,
)
from readablestem.pipeline.layout.rows import _DEGREE_SIGNS, _join_rows, _text_char
from readablestem.pipeline.layout.shared import (
    _CHEM_DASHES,
    _ROW_RELATIONS,
    _SUPERSCRIPT_FLAG,
    CHEM_WORD_GAP,
    SCRIPT_OFFSET,
    SCRIPT_RATIO,
    _arrow_label_flags,
    _CharInfo,
    _Context,
    _Figure,
    _Row,
    _RowIndex,
)
from readablestem.pipeline.parse import (
    EQUATION_THRESHOLD,
    RELATIONS,
    equation_score,
    is_math_alphanumeric,
)

CAPTION_MAX_GAP = 36.0  # furthest a "Figure N" caption may sit from its figure
ScriptKind = script_marks.Kind
FLAGGED_RAISE = 0.2

# A script belongs to the glyph before it (or, for a nuclide's mass number, after it) when
# the gap is at most this x the row size.
SCRIPT_BASE_GAP = 0.35

# A picture of an equation sits in a line of text when prose is this close beside it (x the
# prose row's size) and level with it; its place in the row is this character.
PICTURE_REACH = 3.0
PICTURE_CHAR = "\ufffc"
_OPENING = frozenset("([{")
# A glyph of a Computer Modern or Latin Modern face ("CMR8", "ABCDEF+LMMathItalic8"): TeX set it,
# whatever the producer field of the file says (a file re-written by a viewer or a converter).
_TEX_FACE = re.compile(r"(?:^|\+)(?:CM|LM)[A-Za-z]+\d*", re.IGNORECASE)
_MARKUP = re.compile(r"</?[A-Za-z][\w:.-]*\s*/?>|</")
_ADDRESS = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)  # a typed web address


def _bracket_base(chars: list[_Char], info: _CharInfo, at: int) -> bool:
    """Is the closing bracket at `at` the end of a group of upright text, "(SO4)" in
    Al2(SO4)3, "(OH)", "[Zn2+]"? Its opening bracket is in the row and holds only
    letters, digits, scripts and brackets of upright text."""
    depth = 0
    for j in range(at, -1, -1):
        c = chars[j]
        if c.c in ")]":
            depth += 1
        elif c.c in "([":
            depth -= 1
            if depth == 0:
                inner = [x for x in chars[j + 1 : at] if not x.blank]
                return bool(inner) and all(
                    _text_char(x) or x.c in "()[]" or info.script[k]
                    for k, x in enumerate(chars[j + 1 : at], start=j + 1)
                    if not x.blank
                )
        elif not (c.blank or _text_char(c) or info.script[j] or c.c in "+-\u2212,."):
            return False
    return False


def _script_kinds(
    row: _Row, info: _CharInfo | None = None, *, document: _Document
) -> list[ScriptKind | None]:
    """`_script_glyphs(row, info)[1]`."""
    return _script_glyphs(row, info, document=document)[1]


def _script_glyphs(
    row: _Row, info: _CharInfo | None = None, *, document: _Document
) -> tuple[list[ScriptKind | None], list[ScriptKind | None]]:
    """(raised or lowered glyphs, those among them that are scripts of upright text), per
    character of a prose row, each "sup", "sub" or None. The second marks what belongs to
    upright text (H2O, SO4 2-, Na+, cm3, m s-1, a nuclide's 226Ra).

    A glyph is raised or lowered when it is smaller than the row's text and off its
    baseline by `SCRIPT_OFFSET` of the row size, or full size with MuPDF's superscript flag
    and raised by `FLAGGED_RAISE`. A run of them belongs to the letter, digit or closing
    bracket of upright text just before it (`SCRIPT_BASE_GAP`), or, when it is only digits
    and a capital letter follows at once, to that letter ("226Ra": a mass number). A script
    of an italic letter or a letter in a maths font is left for the equation step: it is
    not given a kind.
    """
    info = info or _CharInfo.of(row, document=document)
    chars = row.chars
    size, base = row.size, row.baseline
    raw: list[ScriptKind | None] = [None] * len(chars)
    for i, c in enumerate(chars):
        if c.blank or not info.script[i]:
            continue
        lift = base - c.baseline  # positive: raised
        if c.size < SCRIPT_RATIO * size:
            if abs(lift) >= SCRIPT_OFFSET * size:
                raw[i] = "sup" if lift > 0 else "sub"
        elif c.flags & _SUPERSCRIPT_FLAG and lift >= FLAGGED_RAISE * size:
            raw[i] = "sup"
    kinds: list[ScriptKind | None] = [None] * len(chars)
    i = 0
    n = len(chars)
    while i < n:
        if raw[i] is None:
            i += 1
            continue
        j = i
        # One group: scripts side by side, a lowered and a raised run (SO4 2-) included.
        while j < n and raw[j] is not None:
            j += 1
        if _script_base(chars, info, i, j, size, document=document):
            kinds[i:j] = raw[i:j]
        i = j
    return raw, kinds


def _power_of_ten(chars: list[_Char], last: int, script: int, *, document: _Document) -> bool:
    """Is the raised glyph at `script` the exponent of "10", whose last digit is at `last`
    ($10^{-6}$, $6 \\times 10^3$)? In a TeX paper that is maths, not the unit exponent of "cm3"."""
    if not document.tex_made or chars[last].c != "0" or last < 1 or chars[last - 1].c != "1":
        return False
    before = chars[last - 2].c if last >= 2 else " "
    if before.isalnum() or before in ".,":
        return False  # "210", "0.10": not the number ten
    return chars[script].baseline < chars[last].baseline - 0.2 * chars[last].size


def _bold_variable(chars: list[_Char], at: int, *, document: _Document) -> bool:
    """Is the glyph at `at` a lone bold Latin letter, a vector in a text font (`\\mathbf{v}_2` set
    in Times Bold)? Its script is an index, not the count of "H2O"."""
    base = chars[at]
    return document.tex_made and base.bold and base.c.isalpha() and (at == 0 or chars[at - 1].blank)


def _script_base(
    chars: list[_Char], info: _CharInfo, start: int, end: int, size: float, *, document: _Document
) -> bool:
    """Does the group of script glyphs `chars[start:end]` belong to upright text?"""
    reach = SCRIPT_BASE_GAP * size
    before = start - 1
    while before >= 0 and chars[before].blank is False and info.script[before]:
        before -= 1  # an earlier script of the same word
    if before >= 0 and not chars[before].blank and chars[start].x0 - chars[before].x1 <= reach:
        base = chars[before]
        if (
            _text_char(base)
            and not _bold_variable(chars, before, document=document)
            and not _power_of_ten(chars, before, start, document=document)
        ):
            return True
        if base.c in ")]":
            return _bracket_base(chars, info, before)
        return False
    after = end
    if after < len(chars) and chars[after].blank and after + 1 < len(chars):
        after += 1  # the text layer may put a space between the numbers and the symbol
    if (
        after < len(chars)
        and not chars[after].blank
        and chars[after].x0 - max(c.x1 for c in chars[start:end]) <= reach + 0.3 * size
        and (before < 0 or chars[before].blank or chars[start].x0 - chars[before].x1 > reach)
    ):
        # A mass number: digits before the capital letter of an element symbol.
        nxt = chars[after]
        digits = all(chars[k].c.isdigit() for k in range(start, end))
        return digits and nxt.c.isalpha() and nxt.c.isupper() and _text_char(nxt)
    return False


def _picture_host(
    figure: _Figure, rows: Sequence[_Row], has_prose: Callable[[_Row], bool]
) -> _Row | None:
    """The prose row the picture stands in the middle of: it has text on both sides."""
    x0, y0, x1, y1 = figure.bbox
    for row in rows:
        if row.kind != "text" or row.vertical or row.absorbed:
            continue
        overlap = min(row.y1, y1) - max(row.y0, y0)
        if (
            overlap >= 0.3 * min(row.height, y1 - y0)
            and row.x0 < x0 - 1
            and row.x1 > x1 + 1
            and has_prose(row)
        ):
            return row
    return None


def _picture_neighbours(
    figure: _Figure, rows: Sequence[_Row], has_prose: Callable[[_Row], bool]
) -> tuple[_Row | None, _Row | None]:
    """The prose rows level with a picture and just left and right of it (None: none)."""
    x0, y0, x1, y1 = figure.bbox
    left = right = None
    for row in rows:
        if row.vertical or row.absorbed or row.marker is not None:
            continue
        overlap = min(row.y1, y1) - max(row.y0, y0)
        if overlap < 0.3 * min(row.height, y1 - y0):
            continue
        # Prose, or the punctuation that ends the sentence the picture stands in.
        ends = 0 < len(row.clean) <= 2 and all(c in ",.;:!?)" for c in row.clean)
        if not ends and (row.kind != "text" or not has_prose(row)):
            continue
        reach = PICTURE_REACH * row.size
        if row.x1 <= x0 + 2 and x0 - row.x1 <= reach and (left is None or row.x1 > left.x1):
            left = row
        elif row.x0 >= x1 - 2 and row.x0 - x1 <= reach and (right is None or row.x0 < right.x0):
            right = row
    return left, right


def _picture_row(figure: _Figure, anchor: _Row) -> _Row:
    """A row of one character standing for the picture, on the baseline of `anchor`."""
    x0, y0, x1, y1 = figure.bbox
    char = _Char(PICTURE_CHAR, x0, y0, x1, y1, anchor.baseline, anchor.size, "image", 0)
    return _Row([char], column=anchor.column)


def _equation_pictures(
    page: pymupdf.Page,
    figures: list[_Figure],
    rows: list[_Row],
    ctx: _Context,
    small: Sequence[_Figure] = (),
    *,
    document: _Document,
) -> tuple[list[_Figure], list[_Row]]:
    """Pictures of equations (module `equation_images`) leave the figures.

    One in a line of text becomes a character of the line: the prose rows beside it and the
    picture join into one row, so the sentence stays one paragraph and the picture an inline
    equation with no text layer. One on a line of its own stays a figure-sized block but is
    an equation (`_Figure.equation`), and the equation number printed to its right, if any,
    is its label. A figure with labels or a caption ("Figure 2.1" close by) is never one.
    `small` pictures (under MIN_IMAGE_PT on a side) count only in a line of text.
    """
    kept: list[_Figure] = []
    rows = list(rows)
    captions = [r for r in rows if CAPTION.match(r.clean)]
    prose: dict[int, tuple[_Row, bool]] = {}  # the row is kept so its id stays its own

    def has_prose(row: _Row) -> bool:
        if id(row) not in prose:
            prose[id(row)] = (row, _prose_words(row, _CharInfo.of(row, document=document)) >= 1)
        return prose[id(row)][1]

    for figure in [*figures, *small]:
        is_small = figure in small
        x0, y0, x1, y1 = figure.bbox
        if (
            figure.labels
            or figure.caption
            or not equation_images.size_fits(x1 - x0, y1 - y0, ctx.body)
            or any(
                min(c.x1, x1) - max(c.x0, x0) >= 0.3 * min(c.x1 - c.x0, x1 - x0)
                and (0 <= c.y0 - y1 <= CAPTION_MAX_GAP or 0 <= y0 - c.y1 <= CAPTION_MAX_GAP)
                for c in captions
            )
        ):
            if not is_small:
                kept.append(figure)
            continue
        profile = equation_images.profile_of(page, figure.bbox)
        if profile is None or not equation_images.looks_like_equation(profile):
            if not is_small:
                kept.append(figure)
            continue
        host = _picture_host(figure, rows, has_prose)
        if host is not None:  # in the middle of a line: between its characters
            centre = (x0 + x1) / 2
            at = sum(1 for c in host.chars if (c.x0 + c.x1) / 2 < centre)
            chars = [*host.chars[:at], *_picture_row(figure, host).chars, *host.chars[at:]]
            joined = _Row(chars, column=host.column)
            joined.kind, joined.marker = "text", host.marker
            joined.exercise_number = host.exercise_number
            rows[rows.index(host)] = joined
            continue
        left, right = _picture_neighbours(figure, rows, has_prose)
        if left is None and right is None:
            if is_small:
                continue  # alone, this small: decoration, as before
            if profile.components < equation_images.MIN_DISPLAY_COMPONENTS:
                kept.append(figure)  # a right triangle with two letters: a drawing
                continue
            figure.equation = True
            number = next(
                (
                    r
                    for r in rows
                    if not r.absorbed
                    and not r.vertical
                    and r.x0 >= x1 - 2
                    and _is_equation_number(r.clean)
                    and y0 - 0.5 * r.height <= (r.y0 + r.y1) / 2 <= y1 + 0.5 * r.height
                ),
                None,
            )
            if number is not None:
                number.absorbed = True
                figure.number = number.clean
            kept.append(figure)
            continue
        anchor = left or right
        assert anchor is not None
        joined = _picture_row(figure, anchor)
        if left is not None:
            joined = _join_rows(left, joined)
        if right is not None:
            joined = _join_rows(joined, right)
        joined.kind = "text"
        joined.region = None
        for source in (left, right):
            if source is not None:
                rows.remove(source)
        rows.append(joined)
    return kept, rows


def _loose_scripts(
    row: _Row,
    info: _CharInfo,
    raw: list[ScriptKind | None],
    kinds: list[ScriptKind | None],
    runs: Sequence[tuple[int, int, list[str]]],
) -> bool:
    """Give `kinds` the raised and lowered glyphs that no equation took and that are not
    scripts of upright text either, when anything sits before them to be their base (the 2
    of "(a + b)2" beyond a line break, the "cell" of "E°cell", a footnote mark after a full
    stop): written raised or lowered they read better than flat. True when some are left
    that no base explains."""
    covered = {i for a, b, _ in runs for i in range(a, b)}
    chars = row.chars
    left_flat = False
    i = 0
    while i < len(chars):
        if raw[i] is None or kinds[i] is not None or i in covered:
            i += 1
            continue
        j = i
        while j < len(chars) and raw[j] is not None and kinds[j] is None and j not in covered:
            j += 1
        before = i - 1
        while before >= 0 and not chars[before].blank and info.script[before]:
            before -= 1
        base = chars[before] if before >= 0 else None
        if (
            base is not None
            and not base.blank
            and chars[i].x0 - base.x1 <= SCRIPT_BASE_GAP * row.size
        ):
            kinds[i:j] = raw[i:j]
        else:
            left_flat = True
        i = j
    return left_flat


def _joined_formulas(row: _Row, info: _CharInfo) -> bool:
    """Is the row two formulas joined by one connective word ("P(A) = 1 and P(B) = 2,")?

    A display set so has a prose word in it, which keeps `_classify_row` from reading it as
    maths. The row is one when its only English words are one connective (`_CONNECTIVES`:
    "and", "or", "for all" ...), each side of it is a relation with something on both
    sides (after "for" a short condition), and the rest of it is maths. Any other word
    ("where x = 1 and y = 2 hold") leaves the row text.
    """
    spans = _prose_spans(row, info)
    spoken = " ".join(word for _, _, word in spans)
    if not spans or spoken not in _CONNECTIVES:
        return False
    chars = row.chars
    left = [c for c in chars[: spans[0][0]] if not c.blank]
    right = [c for c in chars[spans[-1][1] :] if not c.blank]
    if not left or not right:
        return False

    def whole(side: list[_Char]) -> bool:
        body = [c for c in side if c.c not in ",.;:"]
        return any(c.c in _ROW_RELATIONS and 0 < k < len(body) - 1 for k, c in enumerate(body))

    short = len([c for c in right if c.c not in ",.;:"]) <= 3
    return whole(left) and (whole(right) or (spoken.startswith("for") and short))


def _is_equation_number(text: str) -> bool:
    """ "(2.4)", "[3]", "Eq. 2.4" or a bare "2.4": what an equation number looks like."""
    return bool(EQUATION_NUMBER.match(text) or BARE_EQUATION_NUMBER.match(text))


def _code_row(row: _Row) -> bool:
    """Is the row mostly code face (with three letters of it at least)?"""
    ink = [c for c in row.chars if not c.blank]
    code = [c for c in ink if c.mono]
    return 2 * len(code) > len(ink) and sum(c.c.isalpha() for c in code) >= 3


def _mark_code_runs(rows: list[_Row]) -> None:
    """Set `row.code_run` on the rows within three lines of a row that is mostly code face.

    A word of the code face counts as a prose word in a row of an algorithm ("for each Alg
    do", or a step with an identifier in the code face and the rest of it plain): the row is
    text, and not a display equation. A display with one `\\mathtt` name, away from such rows,
    is not (`_classify_row`).
    """
    code = [r for r in rows if _code_row(r)]
    if not code:
        return
    index = _RowIndex(code)
    for row in rows:
        reach = 3.0 * row.size
        row.code_run = any(
            other is not row and other.x0 < row.x1 and row.x0 < other.x1
            for other in index.near(row.y0 - reach, row.y1 + reach)
        )


def _classify_row(row: _Row, *, document: _Document = _PLAIN) -> None:
    """Set row.kind: "math" (a display-equation line), "frag" (short maths-only piece such
    as a lone denominator, decided later) or "text"."""
    info = _CharInfo.of(row, document=document)
    text = row.clean
    fonts = sorted({c.font for c in row.ink})
    superscript = any(info.script)
    row.score, row.reasons = equation_score(text, fonts, superscript)
    ink = [i for i, c in enumerate(row.chars) if not c.blank]
    # Punctuation after maths ("v ." at the end of a displayed fraction) counts neither way.
    counted = [i for i in ink if row.chars[i].c not in ",.;:"]
    ratio = sum(info.mathy(i) for i in counted) / max(1, len(counted))
    prose = _prose_words(row, info, typewriter=row.code_run)
    if row.vertical or sum(k == "C" for k in info.classes) > 0.5 * len(ink):
        row.kind = "text"
    elif row.exercise_number:
        row.kind = "text"  # "18.": kept as text before the equation it numbers
    elif row.marker is not None:
        row.kind = "text"  # a list item ("b. - 40 m/s"): maths in it is inline
    elif not counted:
        row.kind = "frag"  # a lone "," or "." belongs to the maths it ends, if any
    elif _is_equation_number(text):
        row.kind = "frag"  # "(2.4)", "Eq. 2.4", "2.4": maths only beside an equation
    elif _reaction_list_row(row):
        row.kind = "text"  # "(a) A \u21cc B; (b) C \u21cc D": an item per reaction, inline
    elif _reaction_row(row, info, document=document):
        row.kind = "math"  # a chemical reaction on its own line
        row.reasons = [*row.reasons, REACTION_REASON]
        row.score = max(row.score, EQUATION_THRESHOLD)
    elif prose == 0 and row.score >= EQUATION_THRESHOLD and ratio >= 0.5:
        row.kind = "math"
    elif document.grouping and prose >= 1 and ratio >= 0.5 and _joined_formulas(row, info):
        row.kind = "math"  # "P(A) = 1 and P(B) = 2": two formulas of one display
        row.reasons = [*row.reasons, JOINED_REASON]
    elif prose == 0 and ratio >= 0.6 and len(ink) <= 40:
        row.kind = "frag"
    else:
        row.kind = "text"


def _inline_runs(
    row: _Row,
    info: _CharInfo | None = None,
    kinds: list[ScriptKind | None] | None = None,
    *,
    document: _Document = _PLAIN,
) -> list[tuple[int, int, list[str]]]:
    """(start, end, reasons) of the maths runs inside a prose row, by character index.

    A run is a stretch of maths-capable characters (maths/Greek/symbols, digits,
    operators, brackets, single letters, sub/superscripts). It is kept only with real
    evidence: a maths character; an italic single letter next to an operator, script or
    Greek letter; or a relation with operands on both sides ("x = 5"). A lone italic word,
    a citation "[3]" or a year range is not maths. A sub- or superscript of upright text
    (`_script_kinds`: H2O, Na+, cm3, 226Ra) is no evidence: it is written as raised or
    lowered text, so a run that only those scripts made is dropped; inside a run that is
    maths for another reason they stay part of it.
    """
    info = info or _CharInfo.of(row, document=document)
    kinds = _script_kinds(row, info, document=document) if kinds is None else kinds
    chars = row.chars
    runs: list[tuple[int, int, list[str]]] = []
    i, n = 0, len(chars)
    while i < n:
        # A hat or tilde is set before its letter ("ˆΣ"): the run starts at the accent, or the
        # accent stays in the prose and the equation is read without it.
        leading = chars[i].c in _ACCENTS and i + 1 < n and info.mathy(i + 1)
        if not (info.mathy(i) or leading) or chars[i].c in ")]}":
            i += 1
            continue
        start, depth = i, 0
        while i < n:
            c = chars[i].c
            c_font = chars[i].font
            if c in _OPENING:
                depth += 1
            elif c in ")]}":
                depth = max(0, depth - 1)
            between_digits = (
                c in ".,"
                and 0 < i < n - 1
                and chars[i - 1].c.isdigit()
                and chars[i + 1].c.isdigit()
            )
            inside = (
                between_digits
                or (c == "," and (depth > 0 or _ellipsis_comma(chars, i)))
                or (c in _ELLIPSIS_CHARS and _in_ellipsis(chars, i))
                or (c == ":" and i + 1 < n and chars[i + 1].c == "=")  # ":=", from \\coloneqq
                or _separator_in_bracket(chars, info, i, depth, document=document)
                or (
                    (document.tex_made or (document.inline_joins and _TEX_FACE.match(c_font)))
                    and glyph_math.script_punctuation(chars, info.script, i)
                )
                or (
                    document.tex_made and glyph_math.percent_of_number(chars, i)
                )  # the 5% of "phi = 5%"
            )
            accent = c in _ACCENTS and i + 1 < n and info.mathy(i + 1)  # the hat of x-hat
            # The degree sign of E°cell or ΔG°, a raised glyph of the text font after a symbol.
            accent = accent or (
                c in _DEGREE_SIGNS and info.script[i] and i > start and info.mathy(i - 1)
            )
            if (
                chars[i].blank
                and (
                    _between_formulas(chars, i)
                    or (
                        _word_space_between_operands(chars, info, i, document=document)
                        and not _math_word_joins(chars, info, kinds, start, i, document=document)
                    )
                )
                and not (document.tex_made and glyph_math.touching(chars, i))
            ):
                break
            if info.mathy(i) or chars[i].blank or inside or accent:
                i += 1
                continue
            break
        start, end = _trim_run(chars, info, start, i, document=document)
        if end > start:
            reasons = _inline_evidence(chars, info, start, end, kinds, document=document)
            if reasons:
                runs.append((start, end, reasons))
    runs = _outside_addresses(chars, runs)
    if document.inline_joins:
        runs = _joined_labels(chars, info, runs)
    joined = _with_simple_maths(row, info, _join_signatures(chars, runs), document=document)
    found = _with_chemistry(row, kinds, _with_nuclides(row, kinds, joined))
    return _clear_of_entry_numbers(row, found) if row.contents_level else found


def _outside_addresses(
    chars: list[_Char], runs: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """The runs that are not inside a typed web address.

    "watch?v=C-uekD6VTIQ" has a relation between operands, but it is a word of the address:
    a formula there cuts the address in two (`text_pass._address_end` stops at the marker).
    """
    if not runs:
        return runs
    text = "".join(" " if c.blank else c.c for c in chars)
    if len(text) != len(chars):  # a ligature glyph: the indexes no longer line up
        return runs
    spans = [m.span() for m in _ADDRESS.finditer(text)]
    return [r for r in runs if not any(a <= r[0] and r[1] <= b for a, b in spans)]


_ENTRY_PAGE = re.compile(r"\d{1,4}|[ivx]{1,6}", re.IGNORECASE)
_ENTRY_LABEL = re.compile(r"(\d+|[A-Z])(\.\d+)*\.?")


def _clear_of_entry_numbers(
    row: _Row, runs: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """`runs` of a line of a table of contents without the numbers that are not maths: the
    section number it starts with ("6.1 \u0393 = D4") and the page number it ends in ("Forces on
    \u03a3 9", a number set after the title). Digits next to a symbol read as part of the
    formula; here they are the entry's own."""
    chars = row.chars
    ink = [i for i, c in enumerate(chars) if not c.blank]
    if not ink:
        return runs
    first, last = ink[0], ink[-1]
    head = first
    while head <= last and not chars[head].blank:
        head += 1
    tail = last
    while tail > first and not chars[tail - 1].blank:
        tail -= 1
    label = _ENTRY_LABEL.fullmatch("".join(c.c for c in chars[first:head]))
    page = _ENTRY_PAGE.fullmatch("".join(c.c for c in chars[tail : last + 1]))
    out = []
    for start, end, reasons in runs:
        if label and head <= last and start == first and end > head:
            start = head
            while start < end and chars[start].blank:
                start += 1
        if page and tail > first and end == last + 1 and start < tail:
            end = tail
            while end > start and chars[end - 1].blank:
                end -= 1
        number = (label and (start, end) == (first, head)) or (
            page and (start, end) == (tail, last + 1)
        )
        if end > start and not number:
            out.append((start, end, reasons))
    return out


LABEL_MAX_GLYPHS = 4  # the longest label that joins a formula: "H0", "P(A)", "f_n"


def _joined_labels(
    chars: list[_Char], info: _CharInfo, runs: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """Rule (b). A short label that ends in a colon ("H0:") or in ":=" ("f :=") right before a
    run, and holds a maths letter, is the left side of the formula the run holds: the run takes
    it in, with the colon."""
    out: list[tuple[int, int, list[str]]] = []
    for start, end, reasons in runs:
        label = _label_before(chars, info, start)
        if label is not None:
            while out and out[-1][1] > label:  # the label was a run of its own
                label = min(label, out[-1][0])
                reasons = [*out.pop()[2], *reasons]
            start = label
        out.append((start, end, list(dict.fromkeys(reasons))))
    return out


def _label_before(chars: list[_Char], info: _CharInfo, start: int) -> int | None:
    """Where the label of the run at `start` begins, or None when no label ending in a colon
    stands right before it."""
    k = start - 1
    while k >= 0 and chars[k].blank:
        k -= 1
    defines = k >= 1 and chars[k].c == "=" and chars[k - 1].c == ":"
    if defines:
        k -= 1  # ":=": the run was cut after it
    if k < 0 or chars[k].c != ":":
        return None
    j = k - 1
    while j >= 0 and chars[j].blank:
        j -= 1
    last = j
    while j >= 0 and not chars[j].blank:
        j -= 1
    first = j + 1
    glyphs = range(first, last + 1)
    if first > last or last - first + 1 > LABEL_MAX_GLYPHS:
        return None
    if not all(
        info.mathy(g) and (chars[g].c.isalnum() or chars[g].c in "'\u2032*") for g in glyphs
    ):
        # "(e/c < M):" ends a condition and "P \u2297 P_B:" a statement; neither names a formula
        return None
    if not any(chars[g].c.isalpha() for g in glyphs):
        return None
    # A bare letter before a colon ("A:", "e:") is as often a heading of a list as the left
    # side of a formula; a label with an index ("H0:") is a name only a formula has.
    return first if defines or any(info.script[g] for g in glyphs) else None


def _with_simple_maths(
    row: _Row, info: _CharInfo, runs: list[tuple[int, int, list[str]]], *, document: _Document
) -> list[tuple[int, int, list[str]]]:
    """`runs` and the short formulas of `glyph_math` that no run covers: a relation set in a
    maths font before a number. TeX-made PDFs only; a candidate that touches a longer run is
    left to the run, and a run that is only its number is replaced by it."""
    if not document.tex_made:
        return runs
    chars = row.chars
    out = list(runs)
    for start, end, reason in glyph_math.relation_runs(chars, [c.math for c in chars], info.script):
        touching = [r for r in out if r[0] < end and start < r[1]]
        if any(r[0] < start or r[1] > end for r in touching):
            continue  # part of a longer run: it stays as it is
        # A run inside the candidate is its number: the run finder dropped the relation before it.
        reasons = [reason, *(x for r in touching for x in r[2] if x != reason)]
        out = [r for r in out if r not in touching]
        out.append((start, end, reasons))
    return sorted(out)


_SIGNATURE_ARROWS = frozenset("\u2192\u21a6\u27f6\u27fc\u2208\u2282\u2286")


def _join_signatures(
    chars: list[_Char], runs: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """The maps "H : R^d -> R^p" and "f : A -> B" are one formula: two runs with only a colon
    (and spaces) between them, the second holding an arrow or a membership sign, are joined.
    A colon after a word ("Note: x = 1") is not between two runs."""
    joined: list[tuple[int, int, list[str]]] = []
    for run in runs:
        if joined:
            start, end, reasons = joined[-1]
            between = [c.c for c in chars[end : run[0]] if not c.blank]
            if between == [":"] and any(c.c in _SIGNATURE_ARROWS for c in chars[run[0] : run[1]]):
                joined[-1] = (start, run[1], [*reasons, *(r for r in run[2] if r not in reasons)])
                continue
        joined.append(run)
    return joined


NUCLIDE_REASON = "nuclide with numbers before its symbol"
NUCLIDE_GAP = 0.35  # x font size: widest gap between the stacked numbers and the symbol
MAX_NUCLIDE_DIGITS = 6  # a mass number and an atomic number of three digits each


def _nuclide_runs(row: _Row, kinds: list[ScriptKind | None]) -> list[tuple[int, int, list[str]]]:
    """(start, end, reasons) of nuclide notation in a prose row: the mass number raised and
    usually the atomic number lowered before an element's symbol ("14C", "235U", "4 2 He" with
    the 4 above the 2), one run from the first number to the last letter of the symbol.

    The numbers are raised or lowered text of upright type that stands against the symbol
    (`_script_glyphs` gives them a kind: a group of digits and a capital letter at once after
    it). The run is kept only when the group is all digits, starts at the beginning of a word,
    and the letters after it are the symbol of an element (`speech_nuclides.ELEMENTS`) that
    does not go on into a longer word: "226Ra" is a nuclide, "10 m2S" is not. A footnote's
    mark (`NOTE_MARK_FLAG`) is no mass number: "6In fact" is a note, not indium."""
    from readablestem.pipeline.speech_nuclides import ELEMENTS

    chars = row.chars
    n = len(chars)
    runs: list[tuple[int, int, list[str]]] = []
    i = 0
    while i < n:
        if kinds[i] is None:
            i += 1
            continue
        j = i
        while j < n and kinds[j] is not None:
            j += 1
        group = chars[i:j]
        glued = i > 0 and chars[i - 1].c.isalnum()
        k = j  # a blank the PDF puts between the stacked numbers and the symbol is not a gap
        while k < n and chars[k].blank:
            k += 1
        if (
            not glued
            and group
            and all(c.c.isdigit() and not c.flags & NOTE_MARK_FLAG for c in group)
            and len(group) <= MAX_NUCLIDE_DIGITS
            and k < n
            and chars[k].c.isupper()
            and chars[k].x0 - max(c.x1 for c in group) <= NUCLIDE_GAP * row.size
        ):
            end = k + 1
            if end < n and chars[end].c.islower() and kinds[end] is None:
                end += 1
            symbol = "".join(c.c for c in chars[k:end])
            after = chars[end].c if end < n else " "
            if symbol in ELEMENTS and not after.isalpha():
                runs.append((i, end, [NUCLIDE_REASON]))
                i = end
                continue
        i = j
    return runs


def _with_nuclides(
    row: _Row, kinds: list[ScriptKind | None], runs: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """`runs` with the row's nuclide notation as runs of their own, a run that overlaps one
    taken into it."""
    found = _nuclide_runs(row, kinds)
    if not found:
        return runs
    merged = list(runs)
    for start, end, reasons in found:
        touching = [r for r in merged if r[0] < end and start < r[1]]
        merged = [r for r in merged if r not in touching]
        lo = min([start, *(r[0] for r in touching)])
        hi = max([end, *(r[1] for r in touching)])
        merged.append(
            (lo, hi, [*reasons, *(x for r in touching for x in r[2] if x not in reasons)])
        )
    return sorted(merged)


FORMULA_REASON = "chemical formula"
REACTION_REASON = "chemical reaction"


def _chem_words(row: _Row, kinds: Sequence[ScriptKind | None]) -> list[chem.Word]:
    """The row's words as `pipeline.chem` reads them: cut at a blank, at a gap (TeX sets no
    space after a script: "Cl-and"), and around an arrow glyph, which is a word of its own
    with the dashes that are its shaft ("\u2212\u2192"). The space between the
    lowered count and the raised charge of a stack ("SO4 2\u2212") is inside the word. The glyphs
    of a label over or under an arrow are words of their own, marked `over_arrow`."""
    chars = row.chars
    words: list[chem.Word] = []
    current: list[int] = []
    right = 0.0
    labels = _arrow_label_flags(chars)
    kinds = list(kinds)

    def flush() -> None:
        if current:
            first, last = current[0], current[-1] + 1
            words.append(
                chem.Word(
                    first,
                    last,
                    "".join(chars[k].c for k in range(first, last)),
                    tuple(kinds[first:last]),
                    over_arrow=labels[first],
                )
            )
            current.clear()

    for i, c in enumerate(chars):
        if c.blank:
            if _charge_space(chars, kinds, i):
                kinds[i] = "sup"  # the space the text layer puts in the stack of "SO4 2\u2212"
                current.append(i)
                right = c.x1
            else:
                flush()
            continue
        if c.c in chem.ARROW_HEADS:
            shaft = i
            while (
                shaft - 1 in current
                and chars[shaft - 1].c in _CHEM_DASHES
                and kinds[shaft - 1] is None
                and chars[shaft].x0 - chars[shaft - 1].x1 <= CHEM_WORD_GAP * c.size
            ):
                shaft -= 1
                current.remove(shaft)
            flush()
            words.append(
                chem.Word(
                    shaft,
                    i + 1,
                    "".join(x.c for x in chars[shaft : i + 1]),
                    (None,) * (i + 1 - shaft),
                )
            )
            right = c.x1
            continue
        if current and (
            labels[i] != labels[current[-1]]
            or (labels[i] and c.x0 < chars[current[-1]].x0)  # the label under the one over
            or c.x0 - right >= CHEM_WORD_GAP * c.size
        ):
            flush()
        if not current:
            right = c.x1
        current.append(i)
        right = max(right, c.x1)
    flush()
    return words


def _charge_space(chars: list[_Char], kinds: Sequence[ScriptKind | None], i: int) -> bool:
    """Is the blank at `i` the space between the lowered count and the raised charge of an ion
    (`SO4 2\u2212`, set as a stack: the text layer has the two as lines of their own)?"""
    return 0 < i < len(chars) - 1 and kinds[i - 1] == "sub" and kinds[i + 1] == "sup"


def _has_chemistry(row: _Row, kinds: Sequence[ScriptKind | None]) -> bool:
    """A cheap test before the words are cut: a raised or lowered glyph of upright text, or
    an arrow."""
    return any(k is not None for k in kinds) or any(c.c in chem.ARROW_HEADS for c in row.chars)


def _chem_runs(row: _Row, kinds: Sequence[ScriptKind | None]) -> list[tuple[int, int, list[str]]]:
    """(start, end, reasons) of the chemical formulas, ions and reactions in a prose row
    (`pipeline.chem`, docs/chemistry.md): formulas with lowered counts or raised charges,
    with their state symbols; reactions, an arrow with species on both sides."""
    if not _has_chemistry(row, kinds):
        return []
    labelled = chem.label_words(_chem_words(row, kinds))
    found = [(a, b, [FORMULA_REASON]) for a, b in chem.formulas(labelled)]
    found += [(a, b, [REACTION_REASON]) for a, b in chem.reactions(labelled)]
    return sorted(found)


def _with_chemistry(
    row: _Row, kinds: Sequence[ScriptKind | None], runs: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """`runs` with the row's chemistry as runs of their own; a run that overlaps one is taken
    into it (the arrow of a reaction was a run by itself, a formula may sit inside a run
    that is maths for another reason)."""
    found = _chem_runs(row, kinds)
    merged = list(runs)
    for start, end, reasons in found:
        touching = [r for r in merged if r[0] < end and start < r[1]]
        merged = [r for r in merged if r not in touching]
        lo = min([start, *(r[0] for r in touching)])
        hi = max([end, *(r[1] for r in touching)])
        # Formulas already inside this stretch (a reaction found them too) are one run.
        merged = [r for r in merged if not (lo <= r[0] and r[1] <= hi)]
        merged.append(
            (lo, hi, [*reasons, *(x for r in touching for x in r[2] if x not in reasons)])
        )
    return sorted(_split_chemistry_lists(row, merged))


LIST_SEPARATORS = frozenset(",;")

# One species as typeset text: an optional coefficient, an element symbol, then symbols, counts,
# brackets, charges, hydrate dots and a state symbol ("2 Ag+", "CO3 2-", "[Al(H2O)6]3+", "Fe(s)").
_SPECIES_PIECE = re.compile(r"^\d*[A-Z][a-z]?(?:[A-Za-z0-9()\[\]\u00b7.]|[+\u2212\u2013-])*$")


def _species_piece(row: _Row, lo: int, hi: int) -> bool:
    """Do `row.chars[lo:hi]` read as one chemical species with a count or a charge: its text
    (letters in a maths font as their plain letters) fits `_SPECIES_PIECE` and at least one
    glyph is raised or lowered? "F\u2212" and "H2O", not "Na", "x" or "CO3, F". Element symbols
    are upright: an italic letter outside a state's brackets is a variable (`W1, W2`)."""
    ink = [c for c in row.chars[lo:hi] if not c.blank]
    depth = 0
    for c in ink:
        depth += c.c in "(["
        depth -= c.c in ")]" and depth > 0
        if depth == 0 and (c.italic or is_math_alphanumeric(c.c)):
            return False
    text = "".join(base_char(c.c) for c in ink)
    return bool(ink) and any(row.is_script(c) for c in ink) and bool(_SPECIES_PIECE.match(text))


def _split_chemistry_lists(
    row: _Row, runs: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """`runs` with each list of chemical species cut into its items: "CO3 2\u2212, F\u2212" is two
    ions, as the author wrote them, not one formula. A run is a list when its top-level commas
    or semicolons cut it into two or more pieces that are each one species (`_species_piece`);
    a run with a reaction, an `=` or any other maths in it keeps whole. Each item takes the
    run's reasons and the formula reason."""
    out: list[tuple[int, int, list[str]]] = []
    for lo, hi, reasons in runs:
        cuts, depth, start = [], 0, lo
        for k in range(lo, hi):
            c = row.chars[k].c
            depth += c in _OPENING
            depth -= c in ")]}" and depth > 0
            if depth == 0 and c in LIST_SEPARATORS:
                cuts.append((start, k))
                start = k + 1
        if not cuts:
            out.append((lo, hi, reasons))
            continue
        cuts.append((start, hi))
        pieces = []
        for a, b in cuts:
            while a < b and row.chars[a].blank:
                a += 1
            while b > a and row.chars[b - 1].blank:
                b -= 1
            pieces.append((a, b))
        pieces = [_without_marker(row, a, b) for a, b in pieces]
        if len(pieces) < 2 or not all(
            a < b and (_species_piece(row, a, b) or _reaction_piece(row, a, b)) for a, b in pieces
        ):
            out.append((lo, hi, reasons))
            continue
        out.extend((a, b, _item_reasons(row, a, b, reasons)) for a, b in pieces)
    return out


def _item_reasons(row: _Row, lo: int, hi: int, reasons: list[str]) -> list[str]:
    own = REACTION_REASON if _reaction_piece(row, lo, hi) else FORMULA_REASON
    return [own, *(r for r in reasons if r not in (FORMULA_REASON, REACTION_REASON))]


_LIST_MARKER = re.compile(r"^\(?[A-Za-z0-9]{1,2}[.)]")


def _without_marker(row: _Row, lo: int, hi: int) -> tuple[int, int]:
    """`(lo, hi)` without the list marker ("(a)", "b.") that starts a reaction of a list."""
    ink = [k for k in range(lo, hi) if not row.chars[k].blank]
    text = _piece_text([row.chars[k] for k in ink])
    marker = _LIST_MARKER.match(text)
    if marker and len(ink) > marker.end() and _reaction_piece(row, lo, hi, marked=True):
        return ink[marker.end()], hi
    return lo, hi


def _reaction_piece(row: _Row, lo: int, hi: int, *, marked: bool = False) -> bool:
    """Is `row.chars[lo:hi]` one reaction of a list: one arrow, species and operators only,
    with a count, a charge or a state in it, and no italic letter outside a state's brackets?"""
    ink = [c for c in row.chars[lo:hi] if not c.blank]
    if marked:
        marker = _LIST_MARKER.match(_piece_text(ink))
        ink = ink[marker.end() :] if marker else ink
    text = _piece_text(ink)
    depth = 0
    for c in ink:
        depth += c.c in "(["
        depth -= c.c in ")]" and depth > 0
        if depth == 0 and (c.italic or is_math_alphanumeric(c.c)):
            return False
    evidence = any(row.is_script(c) for c in ink) or any(s in text for s in _STATES)
    return (
        sum(c.c in chem.ARROW_HEADS for c in ink) == 1
        and evidence
        and bool(_REACTION_PIECE.match(text))
    )


_STATES = ("(aq)", "(s)", "(l)", "(g)")


def _reaction_list_row(row: _Row) -> bool:
    """Is the row a list of reactions run together, `(a) A \u21cc B; (b) C \u21cc D`? Its top-level
    semicolons cut it into two or more pieces that each hold one arrow and nothing but a
    list marker, formulas, states and operators besides: no word. Such a line is not one
    display equation: each reaction is an equation of its own (`_split_chemistry_lists`),
    inside the sentence it numbers."""
    chars = row.chars
    if sum(c.c in chem.ARROW_HEADS for c in chars) < 2:
        return False
    pieces, start, depth = [], 0, 0
    for k, c in enumerate(chars):
        depth += c.c in _OPENING
        depth -= c.c in ")]}" and depth > 0
        if depth == 0 and c.c == ";":
            pieces.append((start, k))
            start = k + 1
    pieces.append((start, len(chars)))
    pieces = [(a, b) for a, b in pieces if any(not c.blank for c in chars[a:b])]
    return len(pieces) >= 2 and all(_reaction_piece(row, a, b, marked=True) for a, b in pieces)


def _piece_text(chars: Sequence[_Char]) -> str:
    return "".join(base_char(c.c) for c in chars)


# One reaction of a list: an optional marker "(a)" / "a." / "1)", then species, plus signs,
# coefficients, states, charges and one arrow, with an optional label of letters over it.
_REACTION_PIECE = re.compile(
    r"^[A-Za-z0-9()\[\]\u00b7.,+\u2212\u2013\-\u21cc\u2192\u27f6\u2194\u21c4\u21cb]+$"
)


def _reaction_row(row: _Row, info: _CharInfo, *, document: _Document) -> bool:
    """Is the whole row one chemical reaction (`Ag+(aq) + Cl-(aq) -> AgCl(s)`)? Then it is a
    display equation line, not a sentence with an arrow in it: the ink outside the reaction
    is at most the punctuation that ends it."""
    if not any(c.c in chem.ARROW_HEADS for c in row.chars):
        return False
    kinds = _script_kinds(row, info, document=document)
    labelled = chem.label_words(_chem_words(row, kinds))
    spans = chem.reactions(labelled)
    if len(spans) != 1:
        return False
    start, end = spans[0]
    outside = [c.c for i, c in enumerate(row.chars) if not c.blank and not start <= i < end]
    return all(ch in ",.;:" for ch in outside)


# TeX sets nothing between two operands of a formula ("f(x) (y)" has no space), so a visible
# gap there is a word space of the sentence, and the formula ends before it: "eps(k) (i = 1)" is
# two formulas, as the author typed them. A thin space ("dx \\, dy", 0.17 em) is narrower.
WORD_SPACE = 0.25  # x font size: the narrowest gap that is a word space
_OPERAND_END = frozenset(")]}'\u2032!\u221e%")
_OPERAND_START = frozenset("([{\u2202\u2207\u221a\u222b\u2211\u220f\u221e")


def _math_word_joins(
    chars: list[_Char],
    info: _CharInfo,
    kinds: Sequence[ScriptKind | None],
    start: int,
    i: int,
    *,
    document: _Document,
) -> bool:
    """Does the word space at `i` stay inside the run that began at `start`, because a maths word
    ("mod", "min") follows it, or because it follows one and an operand comes after?

    "a \\equiv b mod n" is one formula: the space before "mod" and the one after it are the
    author's `\\bmod` and `\\pmod` spacing, not the end of a formula. The run so far must be maths
    on its own evidence, and the word must not be the first thing of the run."""
    if not document.inline_joins:
        return False
    before = next((k for k in range(i - 1, start - 1, -1) if not chars[k].blank), None)
    after = next((k for k in range(i + 1, len(chars)) if not chars[k].blank), None)
    if before is None or after is None:
        return False
    if info.maths_words[after] and not info.script[after]:  # "b" then "mod"
        return bool(_inline_evidence(chars, info, start, i, kinds, document=document))
    if info.maths_words[before] and not info.script[before]:  # "mod" then "n"
        first = before
        while first > start and info.maths_words[first - 1]:
            first -= 1
        return first > start and info.mathy(after)
    return False


def _separator_in_bracket(
    chars: list[_Char], info: _CharInfo, i: int, depth: int, *, document: _Document
) -> bool:
    """Is the colon or semicolon at `i` an argument separator inside a bracket the run opened
    ("I(A : B)", "P(n; m)") that closes on this row with maths in between? Ending the run there
    left the bracket open in one formula and its closing half in the next."""
    if not (document.inline_joins and document.tex_made and chars[i].c in ";:" and depth > 0):
        return False
    for k in range(i + 1, min(len(chars), i + 40)):
        c = chars[k]
        if c.blank:
            continue
        if c.c in ")]}":
            return k > i + 1
        if not (info.mathy(k) or c.c in ",;:"):
            return False
    return False


def _word_space_between_operands(
    chars: list[_Char], info: _CharInfo, i: int, *, document: _Document
) -> bool:
    """Is the blank at `i` a word space between the end of one operand and the start of another?"""
    if not document.tex_made:
        return False
    before = next((k for k in range(i - 1, -1, -1) if not chars[k].blank), None)
    after = next((k for k in range(i + 1, len(chars)) if not chars[k].blank), None)
    if before is None or after is None or info.script[before] or info.script[after]:
        return False
    a, b = chars[before], chars[after]
    if a.c.isdigit() and b.c.isalpha():
        return False  # "8.5 m/s": a number and its unit are one quantity
    ends = a.c.isalnum() or a.c in _OPERAND_END
    starts = b.c.isalnum() or b.c in _OPERAND_START
    return ends and starts and b.x0 - a.x1 >= WORD_SPACE * max(a.size, b.size)


_ELLIPSES = frozenset("\u2026\u22ef\u22ee\u22f1")
_ELLIPSIS_CHARS = _ELLIPSES | frozenset(".\u00b7")


def _in_ellipsis(chars: list[_Char], i: int) -> bool:
    """Is the glyph at `i` one of an ellipsis: a single ellipsis character, or a dot (or a
    centred dot) in a group of three, set side by side, with a comma on one side of the group?"""
    if chars[i].c in _ELLIPSES:
        return True
    lo = hi = i
    while (lo > 0 and chars[lo - 1].c in ".\u00b7") or (
        lo > 1 and chars[lo - 1].blank and chars[lo - 2].c in ".\u00b7"
    ):
        lo -= 1
    while (hi + 1 < len(chars) and chars[hi + 1].c in ".\u00b7") or (
        hi + 2 < len(chars) and chars[hi + 1].blank and chars[hi + 2].c in ".\u00b7"
    ):
        hi += 1
    group = [c for c in chars[lo : hi + 1] if not c.blank]
    if len(group) != 3:
        return False
    before = next((chars[k].c for k in range(lo - 1, -1, -1) if not chars[k].blank), "")
    after = next((chars[k].c for k in range(hi + 1, len(chars)) if not chars[k].blank), "")
    return before == "," or after == ","


def _ellipsis_comma(chars: list[_Char], i: int) -> bool:
    """Is the comma at `i` next to an ellipsis ("i = 1, ..., S", "1, 2, ..., n")? Authors type
    such a list as one formula; a bare comma between two formulas ends the first."""

    def dots(indexes: Iterable[int]) -> bool:
        seen = 0
        for k in indexes:
            if chars[k].blank:
                if seen:
                    break
                continue
            if chars[k].c in _ELLIPSES:
                return True
            if chars[k].c not in ".\u00b7":
                break
            seen += 1
        return seen >= 3 or (seen > 0 and any(chars[k].c in "\u00b7" for k in indexes))

    ahead = dots(range(i + 1, min(len(chars), i + 8)))
    behind = dots(range(i - 1, max(-1, i - 8), -1))
    return ahead or behind


def _between_formulas(chars: list[_Char], i: int) -> bool:
    """Is the blank at `i` the space the page's text puts between two formulas?

    Chromium prints each MathML formula in a maths font, with the spaces inside it in
    that font too; the space between "B = 0," and "C = 1," (two formulas of one sentence)
    is a character of the text font. A blank in a text font with maths-font glyphs on both
    sides ends the run: they are two equations, as the source has them, not one.
    """
    if _font_traits(chars[i].font)[2]:
        return False
    before = next((c for c in reversed(chars[:i]) if not c.blank), None)
    after = next((c for c in chars[i + 1 :] if not c.blank), None)
    return (
        before is not None
        and after is not None
        and _font_traits(before.font)[2]
        and _font_traits(after.font)[2]
    )


def _trailing_ellipsis(chars: list[_Char], start: int, end: int) -> bool:
    """Do the last three glyphs of the run `[start, end)` make an ellipsis of its own, set
    after a comma or a relation or operator ("j = 1, 2, 3, ...", "0 > s1 > s2 > ...")?

    The list or chain goes on, and the dots are part of the formula, not the full stop of a
    sentence: trimming them left "j = 1, 2, 3" for a reader who needs to know it continues.
    Exactly three dots (a fourth is the sentence's full stop, which still goes), and only
    after one of those signs.
    """
    dots = [k for k in range(end - 1, start - 1, -1) if not chars[k].blank][:4]
    if len(dots) < 4 or any(chars[k].c not in ".·" for k in dots[:3]):
        return False
    return chars[dots[3]].c in ",<>=≤≥≠+−-×"  # noqa: RUF001


def _trim_run(
    chars: list[_Char], info: _CharInfo, start: int, end: int, *, document: _Document = _PLAIN
) -> tuple[int, int]:
    """Drop surrounding spaces, trailing operators/punctuation and unmatched brackets."""
    changed = True
    while changed and end > start:
        changed = False
        while end > start and (
            chars[end - 1].blank
            or (
                chars[end - 1].c in ",.;:"
                and not (document.grouping and _trailing_ellipsis(chars, start, end))
            )
        ):
            end -= 1
            changed = True
        while end > start and chars[start].blank:
            start += 1
            changed = True
        if _article(chars, info, start, end, start + 1):  # "a x" (maths x): the "a" is English
            start += 1
            changed = True
        elif _article(chars, info, start, end, end - 2):
            end -= 1
            changed = True
        if end > start and chars[end - 1].c in _TRAILING_OPERATORS and not info.script[end - 1]:
            end -= 1
            changed = True
        if end > start and chars[start].c in _INFIX_ONLY:  # "/h)] = 40": a broken piece
            start += 1
            changed = True
        text = "".join(c.c for c in chars[start:end])
        if end > start and text[-1] in ")]}" and _unbalanced(text, closing=True):
            end -= 1
            changed = True
        elif end > start and text[0] in "([{" and _unbalanced(text, closing=False):
            start += 1
            changed = True
        elif end > start and text.count("|") % 2 and (text[0] == "|" or text[-1] == "|"):
            # An unpaired bar at an edge ("|x" before a line break); "|x| < 1" keeps both.
            if text[0] == "|":
                start += 1
            else:
                end -= 1
            changed = True
        elif document.tex_made and end - start > 1 and text[-1] in "([{":
            end -= 1  # "K (see below)": the run never ends with a bracket it opens
            changed = True
        elif document.tex_made and end - start > 2 and _sentence_parentheses(text):
            start, end = start + 1, end - 1  # "(x = 0.9)": the pair is the sentence's
            changed = True
        elif (
            document.tex_made and (cut := _tail_outside_maths(chars, info, start, end)) is not None
        ):
            end = cut  # "t = 915 s", "x = 5 [8]": a unit and a citation are text
            changed = True
    return start, end


def _sentence_parentheses(text: str) -> bool:
    """Does one pair of parentheses enclose the whole run, with no comma at its top level?
    A tuple, "(a, b)", is maths; "(x = 0.9)" is a formula in a parenthesis of the sentence."""
    if text[0] != "(" or text[-1] != ")":
        return False
    depth = 0
    for k, c in enumerate(text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0 and k < len(text) - 1:
                return False  # "(a + b)(c + d)", "(x + 1)^2": the first pair closes early
        elif c == "," and depth == 1:
            return False
    return depth == 0


_CITATION_TAIL = re.compile(r"\[\d(?:[\d,\u2013\-\s]*\d)?\]$")


def _tail_outside_maths(chars: list[_Char], info: _CharInfo, start: int, end: int) -> int | None:
    """Where the run should end, when it ends in what the author writes outside the dollar
    signs: the upright unit of a number ("915 s", "9.8 m"), or a numbered citation ("[8]")
    after a space. None when it ends in neither."""
    text = "".join(c.c for c in chars[start:end])
    cite = _CITATION_TAIL.search(text)
    if cite is not None and cite.start() > 0 and text[cite.start() - 1].isspace():
        return start + cite.start()
    upright = [
        c.c.isalpha() and info.classes[k] == "L" and not (c.italic or c.math or info.script[k])
        for k, c in enumerate(chars)
    ]
    return glyph_math.trailing_unit(chars, start, end, upright=upright, script=info.script)


def _article(chars: list[_Char], info: _CharInfo, start: int, end: int, blank: int) -> bool:
    """Is the run edge next to `blank` a plain "a", "A" or "I" word separated by a space?"""
    if end - start < 3 or not start <= blank < end or not chars[blank].blank:
        return False
    edge = start if blank == start + 1 else end - 1
    ch = chars[edge]
    return ch.c in "aAI" and info.classes[edge] == "L" and not ch.italic and info.words[edge] == 1


def _unbalanced(text: str, *, closing: bool) -> bool:
    opened = sum(text.count(c) for c in "([{")
    closed = sum(text.count(c) for c in ")]}")
    return closed > opened if closing else opened > closed


# Operators that cannot end an expression: a run ending in one was cut by prose
# ("b - proj b" broken at a word the run does not take in). Not "|": it closes |x|.
_TRAILING_OPERATORS = frozenset("+-*/=<>^_~\u2212\u00b1\u2213\u00d7\u00f7\u22c5")

# Operators that cannot start an expression: a run starting with one is the tail of an
# expression cut by a line break or by prose ("(50 km)/(60 km/h)] = 40" -> "/h)] = 40").
# Not "|" (it opens |x|, see `_trim_run`) and not "~" ("~5 m" is about five metres).
_INFIX_ONLY = frozenset("/*^_=<>\u2264\u2265\u2260\u2248\u2261\u00d7\u00f7\u22c5")


def _broken_brackets(text: str) -> bool:
    """A closing bracket before any opening one, or an opening one never closed."""
    depth = 0
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return True
    return depth != 0


def _inline_evidence(
    chars: list[_Char],
    info: _CharInfo,
    start: int,
    end: int,
    kinds: Sequence[ScriptKind | None] | None = None,
    *,
    document: _Document,
) -> list[str]:
    """Why a run inside a paragraph row is maths, or [] when it is not.

    Without a maths character, a run must also be a whole expression: brackets that open
    and close in order (not "h)] = 40", a piece of a plain-text formula cut by a line
    break), and an operator that is not just a leading sign ("-49 m" is a quantity with
    an italic unit, not a formula). The sub- and superscripts of upright text (`kinds`,
    from `_script_kinds`) give none of this evidence.
    """
    own = kinds if kinds is not None else [None] * len(chars)
    idx = [i for i in range(start, end) if own[i] is None]
    if any(chars[i].stacked for i in idx):
        return ["stacked fraction"]
    if any(info.classes[i] == "M" for i in idx):
        return ["math font or symbol"]
    if _broken_brackets("".join(chars[i].c for i in range(start, end))):
        return []
    first = next((i for i in idx if not chars[i].blank), None)
    if first is None:
        return []
    operator = any(
        (info.classes[i] == "O" and not (i == first and chars[i].c in "+-\u2212\u00b1"))
        or info.script[i]
        for i in idx
    )
    italic_letter = any(
        info.classes[i] == "L" and info.words[i] == 1 and chars[i].italic and not info.script[i]
        for i in idx
    )
    if italic_letter and operator:
        return ["italic variable with operator"]
    if any(
        info.classes[i] == "L"
        and info.words[i] == 1
        and not info.script[i]
        and info.script[i + 1]
        and own[i + 1] is None
        for i in range(start, end - 1)
        if own[i] is None
    ):
        return ["letter with sub/superscript"]
    for i in range(start, end - 1):
        if chars[i].c not in ")]" or own[i] is not None:
            continue
        after = next((k for k in range(i + 1, end) if not chars[k].blank), None)
        if (
            after is not None
            and own[after] is None
            and info.script[after]
            and info.classes[after] in "LD"
        ):
            return ["bracket with sub/superscript"]  # (x)_n, [A]^2: a script only maths has
    if any(
        chars[i].c in _SIGNS and chars[i + 1].c.isdigit()
        for i in range(start, end - 1)
        if own[i] is None and own[i + 1] is None
    ):
        return ["signed number"]
    if _italic_variables(chars, info, start, end, own, document=document):
        return ["italic variable in prose"]
    if any(
        chars[k].c == "0"
        and info.script[k + 1]
        and own[k + 1] is None
        and _power_of_ten(chars, k, k + 1, document=document)
        for k in range(start, end - 1)
        if own[k] is None
    ):
        return ["power of ten"]
    text = "".join(chars[i].c for i in range(start, end))
    if _MARKUP.search(text):
        return []  # "<a><b/></a>" in a paragraph about XML is not a comparison
    for relation in RELATIONS:
        if relation in text:
            left, _, right = text.partition(relation)
            if any(c.isalnum() for c in left) and any(c.isalnum() for c in right):
                return ["relation between operands"]
    return []


# A run of italic letters in upright prose is a variable (`S`, `N_i`, `K(t)`): LaTeX sets the Latin
# letters of a formula in the text family's italic when the maths font only has the Greek and the
# symbols (mathptmx, mathpazo, Charter, newtx), so no glyph is in a maths font. Emphasis is words:
# a group of more than three letters, a short English word ("not"), a whole italic phrase, or a
# label ("(a)") is text.
_ITALIC_GROUP_LETTERS = 3
_LABEL_RUN = re.compile(r"^\(?[A-Za-z]\)$|^[A-Za-z]\)$")  # "(a)", "a)"
_FIGURE_PART = re.compile(r"^\d+\([A-Za-z](?:[,\u2013-][A-Za-z])*\)$")  # "6(a)", "5(d,e)"


def _italic_variables(
    chars: list[_Char],
    info: _CharInfo,
    start: int,
    end: int,
    own: Sequence[ScriptKind | None],
    *,
    document: _Document,
) -> bool:
    """Is the run `chars[start:end]` made of italic variables standing in upright prose?"""
    if not document.tex_made:
        return False
    # The letters of a maths word ("ln" in "ln X") are upright and say nothing against the
    # italic of the variables beside them; a run of nothing but them is not evidence.
    letters = [
        i
        for i in range(start, end)
        if own[i] is None and info.classes[i] == "L" and not info.maths_words[i]
    ]
    if not letters or not all(chars[i].italic for i in letters):
        return False
    row_letters = [c for c in chars if c.c.isalpha()]
    if not row_letters or sum(c.italic for c in row_letters) * 2 > len(row_letters):
        return False  # an italic phrase: its single letters are words ("a", "I")
    group: list[int] = []
    for i in [*letters, -1]:
        if group and (i < 0 or i != group[-1] + 1 or info.script[i] != info.script[group[0]]):
            word = "".join(chars[k].c for k in group).lower()
            if len(group) > _ITALIC_GROUP_LETTERS or (
                len(group) > 1 and not info.script[group[0]] and word in hyphens.words()
            ):
                return False
            group = []
        if i >= 0:
            group.append(i)
    for i in letters:  # "-49 m": a number, a space and an italic letter is a quantity
        if i > start and chars[i - 1].blank and not chars[i].c.isdigit():
            back = next((k for k in range(i - 1, start - 1, -1) if not chars[k].blank), None)
            if back is not None and chars[back].c.isdigit() and not info.script[back]:
                return False
    before = next((k for k in range(start - 1, -1, -1) if not chars[k].blank), None)
    after = next((k for k in range(end, len(chars)) if not chars[k].blank), None)
    if before is not None and chars[before].c.isdigit() and not info.script[before]:
        return False  # "-49 m", a number, a space and an italic letter: a quantity with its unit
    if any(k is not None and chars[k].c.isalpha() and chars[k].italic for k in (before, after)):
        return False  # the edge of a longer italic stretch
    text = "".join(c.c for c in chars[start:end] if not c.blank)
    if (
        before is not None
        and after is not None
        and chars[before].c == "("
        and chars[after].c == ")"
    ):
        return False  # the run was trimmed out of "(a)": a label
    return not (_LABEL_RUN.match(text) or _FIGURE_PART.match(text))  # "(a) Use", "Fig. 6(a)"


_CONNECTIVES = frozenset(
    {"and", "or", "for", "for all", "for any", "for every", "for each", "where", "with", "if",
     "when", "otherwise"}
)  # fmt: skip


JOINED_REASON = "formulas joined by a word"
