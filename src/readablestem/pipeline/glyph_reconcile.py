# (the Greek letters and relation signs of the PDF's text layer are what is mapped here)
"""Corrections of a reading from the PDF's own glyphs (`EQUATION_GLYPH_RECONCILE`).

The equation step compares a reading with the glyphs under its box and flags a difference
(`equation_step._evaluate`). Some differences have one answer, and the glyphs hold it: a PDF made
by TeX names its symbols exactly, so a reading that differs from them by one named lookalike is
that symbol misread, and the model's reading of a slash, a unit or an equation number says
something the page does not. Four small rules, each a pure function of the reading's LaTeX, its
MathML symbols and the glyphs, none of which asks a model:

- F1 `reconcile_symbol`: one named lookalike (a Greek letter for a Latin one, a relation for
  another relation, `\\wedge` for a text caret) that the glyphs name differently is replaced by
  the glyphs' symbol;
- F2 `drop_unit_past_box`: the unit the line-context reading added after a number, which the PDF
  sets outside the box in the upright text font, goes (layout's own unit rule, `trailing_unit`);
- F3 `keep_slash`: a printed `a/b` stays a slash where the reading made it `\\frac{a}{b}`;
- F4 `drop_equation_number`: the `(N)` of a display, read into its LaTeX, goes and becomes the
  equation's number.

A rule fires only when the evidence is whole: a TeX-made PDF whose span has no unnamed glyph
(F1), one difference and no more, one place the symbol can be. In any doubt the reading stays and
the existing checks decide; nothing is guessed. Every change is a note on the fix
(`equation.pdf_correction`) and the reading before it is kept (`EquationFix.model_latex`).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import NamedTuple

import defusedxml.ElementTree as DET

from readablestem.core.types import Check
from readablestem.pipeline import glyph_math
from readablestem.pipeline.text_layer_compare import Glyph, mathml_bag, symbol_bag
from readablestem.pipeline.validate import LatexConversionError, safe_latex_to_mathml

CHECK_NAME = "equation.pdf_correction"
SYMBOL_CODE = "glyph_reconciled"
UNIT_CODE = "unit_dropped"
SLASH_CODE = "slash_kept"
NUMBER_CODE = "number_dropped"
CODES = frozenset({SYMBOL_CODE, UNIT_CODE, SLASH_CODE, NUMBER_CODE})
# One note per code, a warning that the review screen lists as optional (`NOTE_CODES`).
NOTES = {
    SYMBOL_CODE: Check(name=CHECK_NAME, passed=False, severity="warning", code=SYMBOL_CODE),
    UNIT_CODE: Check(name=CHECK_NAME, passed=False, severity="warning", code=UNIT_CODE),
    SLASH_CODE: Check(name=CHECK_NAME, passed=False, severity="warning", code=SLASH_CODE),
    NUMBER_CODE: Check(name=CHECK_NAME, passed=False, severity="warning", code=NUMBER_CODE),
}

# TeX's own maths and text fonts (Computer Modern and its relatives): the glyph names in them are
# exact. A font outside this list (a Word export's Cambria Math, an OCR layer) is not trusted.
TEX_FONT = re.compile(
    r"^(?:cm[a-z]+\d|lmroman|lmmath|lmmi|lmsy|lmex|lmsans|latinmodern|txmi|txsy|txex|txr|"
    r"pxmi|pxsy|pxex|pxr|newtx|stixmath|stix-?math|msam|msbm|eus[mb]|eu[fr][mb]|rsfs|"
    r"sfrm|sfti|sfbx|sfss|mnsy|mnr)",
    re.IGNORECASE,
)
# A script face the text layer cannot tell from CMSY's: RSFS and EUSM draw `\mathscr`.
SCRIPT_FONT = re.compile(r"^(?:eusm\d|eusb\d|rsfs)", re.IGNORECASE)


def tex_font(font: str) -> bool:
    return bool(TEX_FONT.match(font.split("+", 1)[-1]))


def script_font(font: str) -> bool:
    return bool(SCRIPT_FONT.match(font.split("+", 1)[-1]))


def _unnamed(text: str) -> bool:
    """A glyph the PDF cannot name: the replacement character, private use, an unmapped cid."""
    return (
        "\N{REPLACEMENT CHARACTER}" in text
        or "(cid:" in text
        or any(0xE000 <= ord(c) <= 0xF8FF for c in text)
    )


# --------------------------------------------------------------------------------- F1: symbols

# A Greek letter and the Latin letter it is mistaken for, with the command that writes it.
_GREEK = {
    "\u03c5": ("v", r"\upsilon"),
    "\u03bd": ("v", r"\nu"),
    "\u03b9": ("i", r"\iota"),
    "\u03ba": ("k", r"\kappa"),
    "\u03c1": ("p", r"\rho"),
    "\u03c7": ("x", r"\chi"),
    "\u03c9": ("w", r"\omega"),
}
_COMMAND_GREEK = {command: greek for greek, (_latin, command) in _GREEK.items()}
# Relations that look alike in print, and the commands that write each. A reading may name any
# of the group for a glyph that is another of it.
_RELATION_GROUPS = (
    {"\u224d": r"\asymp", "\u227f": r"\succsim", "\u2248": r"\approx", "\u223c": r"\sim",
     "\u2243": r"\simeq", "\u2245": r"\cong"},
    {"\u2272": r"\lesssim", "\u227e": r"\precsim"},
    {"\u227c": r"\preccurlyeq", "\u2aaf": r"\preceq"},
)  # fmt: skip
_RELATION_COMMAND = {ch: cmd for group in _RELATION_GROUPS for ch, cmd in group.items()}
_RELATION_GROUP = {ch: i for i, group in enumerate(_RELATION_GROUPS) for ch in group}
_TEXT_CARET = re.compile(r"\\text\{\s*\^\s*\}|\\textasciicircum(?![A-Za-z])\s*(?:\{\})?|\\\^\{\}")
WEDGE = "\u2227"


def _command_free(latex: str) -> str:
    """The LaTeX without command names and environment names, for counting bare letters."""
    return re.sub(r"\\(?:begin|end)\{[^}]*\}|\\[A-Za-z]+", " ", latex)


def _mathml_relations(mathml: str) -> list[str]:
    root = DET.fromstring(mathml, forbid_dtd=True)
    return [c for el in root.iter() for c in (el.text or "") if c in _RELATION_GROUP]


def _swap_command(latex: str, old: str, new: str) -> str | None:
    """`latex` with the one `old` command written `new`, or None unless it occurs exactly once."""
    pattern = re.compile(re.escape(old) + r"(?![A-Za-z])")
    if len(pattern.findall(latex)) != 1:
        return None
    return pattern.sub(lambda _m: new, latex, count=1)


def _greek_for_latin(latex: str, latin: str, greek: str) -> str | None:
    """The one bare letter `latin` of `latex` written as the command of `greek`."""
    if len(re.findall(re.escape(latin), _command_free(latex))) != 1:
        return None
    out: list[str] = []
    i, done = 0, False
    while i < len(latex):
        if latex.startswith("\\", i):
            found = re.match(r"\\(?:(?:begin|end)\{[^}]*\}|[A-Za-z]+)", latex[i:])
            step = found.end() if found else 2
            out.append(latex[i : i + step])
            i += step
            continue
        if latex[i] == latin and not done:
            out.append(_GREEK[greek][1] + " ")
            done = True
        else:
            out.append(latex[i])
        i += 1
    return "".join(out).replace("  ", " ").strip() if done else None


def reconcile_symbol(latex: str, mathml: str, text: str, tex: bool) -> str | None:
    """`latex` with the one symbol the glyphs name differently written as the glyphs name it, or
    None. `text` is the PDF text the reading is compared with; `tex` says the PDF is TeX-made and
    its glyphs are in TeX fonts (`GlyphContext.tex`). Needs no unnamed glyph in `text`, a closed
    pair of lookalikes, and the symbol once in the reading."""
    if not tex or not text or _unnamed(text):
        return None
    try:
        have, want = mathml_bag(mathml), symbol_bag(text)
    except (DET.ParseError, ValueError):
        return None
    if have != want:
        return _swap_letter(latex, have, want, text)
    swapped = _swap_relation(latex, mathml, text)
    if swapped is not None:
        return swapped
    return _swap_caret(latex, text)


def _swap_letter(latex: str, have: Counter[str], want: Counter[str], text: str) -> str | None:
    extra, missing = have - want, want - have
    if sum(extra.values()) != 1 or sum(missing.values()) != 1:
        return None
    (read,), (glyph,) = extra, missing
    if glyph in _GREEK and _GREEK[glyph][0] == read and have[read] == 1:
        return _greek_for_latin(latex, read, glyph)
    greek = next((g for g, (latin, _c) in _GREEK.items() if g == read and latin == glyph), None)
    if greek is None or have[read] != 1:
        return None
    letters = {c for c in text if c.casefold() == glyph}
    if len(letters) != 1:
        return None
    return _swap_command(latex, _GREEK[greek][1], next(iter(letters)))


def _swap_relation(latex: str, mathml: str, text: str) -> str | None:
    try:
        read = _mathml_relations(mathml)
    except (DET.ParseError, ValueError):
        return None
    glyphs = [c for c in text if c in _RELATION_GROUP]
    if len(read) != len(glyphs):
        return None
    differing = [(r, g) for r, g in zip(read, glyphs, strict=True) if r != g]
    if len(differing) != 1:
        return None
    ((was, now),) = differing
    if _RELATION_GROUP[was] != _RELATION_GROUP[now] or read.count(was) != 1:
        return None
    return _swap_command(latex, _RELATION_COMMAND[was], _RELATION_COMMAND[now])


def _swap_caret(latex: str, text: str) -> str | None:
    """A caret the reading wrote as text where the glyphs have `\\wedge` (a spreadsheet formula
    typeset in TeX): only when every caret of the reading is one of the glyphs' wedges."""
    carets = _TEXT_CARET.findall(latex)
    wedges = len(re.findall(r"\\(?:wedge|land)(?![A-Za-z])", latex))
    if not carets or text.count(WEDGE) != len(carets) + wedges:
        return None
    return _TEXT_CARET.sub(lambda _m: r"\wedge ", latex)


# ----------------------------------------------------------------------------- F2: unit past box

_UNIT_TAIL = re.compile(
    r"(?:\\[,;:! ]|~|\s)*\\(?:text|mathrm|textrm|mbox)\{\s*([A-Za-z]{1,"
    + str(glyph_math.UNIT_LETTERS)
    + r"})\s*\}\s*$"
)


class _Run(NamedTuple):
    """A glyph for `glyph_math.trailing_unit`, which reads only the character and its blankness."""

    c: str
    x0: float = 0.0
    x1: float = 0.0
    size: float = 1.0

    @property
    def blank(self) -> bool:
        return self.c.isspace()


def _unit_follows_digit(unit: str, stream: Sequence[Glyph]) -> bool:
    """Do the glyphs have `unit` in upright type right after a digit, where layout's own rule
    (`glyph_math.trailing_unit`) cuts the formula before it?"""
    for i, (c, _style) in enumerate(stream):
        if not c.isdigit():
            continue
        k = i + 1
        while k < len(stream) and stream[k][0].isspace():
            k += 1
        letters = []
        while k < len(stream) and stream[k][1] == "upright" and stream[k][0].isalpha():
            letters.append(stream[k][0])
            k += 1
        if "".join(letters) != unit or (k < len(stream) and stream[k][0].isalpha()):
            continue
        window = stream[:k]
        cut = glyph_math.trailing_unit(
            [_Run(c) for c, _ in window],
            0,
            len(window),
            upright=[s == "upright" and c.isalpha() for c, s in window],
            script=[False] * len(window),
        )
        if cut == i + 1:
            return True
    return False


def drop_unit_past_box(
    latex: str, mathml: str, text: str | None, span: Sequence[Glyph], after: Sequence[Glyph]
) -> str | None:
    """`latex` without the unit it adds after a number the box ends with, or None. The box's text
    ends in a digit, the reading ends in a single upright unit that the box does not have, and
    the glyphs have that unit in upright type after a digit where layout would cut a formula
    (`glyph_math.trailing_unit`): the author set it outside the dollar signs."""
    if not text or not text.rstrip()[-1:].isdigit():
        return None
    tail = _UNIT_TAIL.search(latex)
    if tail is None:
        return None
    unit = tail.group(1)
    try:
        beyond = mathml_bag(mathml) - symbol_bag(text)
    except (DET.ParseError, ValueError):
        return None
    if beyond != symbol_bag(unit) or not _unit_follows_digit(unit, [*span, *after]):
        return None
    kept = latex[: tail.start()].rstrip()
    return kept if kept else None


# ---------------------------------------------------------------------------- F3: printed slash

_SIGNS = "-+\u2212"


def _group_end(latex: str, start: int) -> int | None:
    """The index just after the brace group that opens at `start`, or None."""
    if start >= len(latex) or latex[start] != "{":
        return None
    depth = 0
    for k in range(start, len(latex)):
        if latex[k] == "\\":
            continue
        if latex[k] == "{" and latex[k - 1 : k] != "\\":
            depth += 1
        elif latex[k] == "}" and latex[k - 1 : k] != "\\":
            depth -= 1
            if depth == 0:
                return k + 1
    return None


def _wrapped(latex: str) -> bool:
    text = latex.strip()
    if text.startswith("\\left("):
        return text.endswith("\\right)")
    if not (text.startswith("(") and text.endswith(")")):
        return False
    depth = 0
    for k, c in enumerate(text):
        depth += c == "("
        depth -= c == ")"
        if depth == 0 and k < len(text) - 1:
            return False
    return True


def _bag_of(latex: str) -> Counter[str] | None:
    try:
        return mathml_bag(safe_latex_to_mathml(latex, "inline"))
    except (LatexConversionError, DET.ParseError, ValueError):
        return None


def keep_slash(latex: str, text: str | None, *, fraction_bar: bool) -> str | None:
    r"""`latex` with a `\frac{A}{B}` written `A/B` where the PDF prints a slash, or None. The
    reading is the fraction alone (a sign aside), the text has one slash and no fraction bar was
    found in the box, and the letters and digits of A and B are those on each side of the slash.
    A side the glyphs put in parentheses keeps them."""
    if not text or fraction_bar or text.count("/") != 1:
        return None
    out = latex.strip()
    sign = ""
    if out[:1] in _SIGNS:
        sign, out = out[0], out[1:].lstrip()
    found = re.match(r"\\[dt]?frac\s*", out)
    if found is None:
        return None
    first = _group_end(out, found.end())
    second = _group_end(out, first) if first is not None else None
    if first is None or second != len(out):
        return None
    top, bottom = out[found.end() + 1 : first - 1], out[first + 1 : second - 1]
    if "\\frac" in top or "\\frac" in bottom or "\\dfrac" in top + bottom:
        return None
    left, right = text.split("/")
    pairs = [(top, left), (bottom, right)]
    sides = []
    for part, printed in pairs:
        bag = _bag_of(part)
        if bag is None or not bag or bag != symbol_bag(printed):
            return None
        shown = printed.strip().lstrip(_SIGNS).strip()
        if shown.startswith("(") and shown.endswith(")") and not _wrapped(part):
            part = f"({part.strip()})"
        sides.append(part.strip())
    return f"{sign}{sides[0]}/{sides[1]}"


# ------------------------------------------------------------------------ F4: equation number

_NUMBER = r"\(\s*(\d+(?:\.\d+)?)\s*\)"
_SPACING = r"(?:\s|~|\\[;:! ,]|\\q?quad(?![A-Za-z]))"
_TRAILING_NUMBER = re.compile(
    rf"(?P<lead>(?:{_SPACING}|,)*){_NUMBER}\s*(?P<end>(?:\\end\{{[a-z*]+\}}\s*)?)$"
)
_LEADING_NUMBER = re.compile(rf"^\s*{_NUMBER}(?P<tail>{_SPACING}*)")
_QUAD = re.compile(r"\\q?quad(?![A-Za-z])")


def without_label(text: str | None, label: str) -> str | None:
    """The PDF text without the equation number that was taken out of the reading, so the checks
    compare the formula with its own glyphs (the last `(N)` of the text)."""
    if not text:
        return text
    found = list(
        re.finditer(re.escape(label[0]) + r"\s*" + re.escape(label[1:-1]) + r"\s*\)", text)
    )
    if not found:
        return text
    last = found[-1]
    return " ".join((text[: last.start()] + " " + text[last.end() :]).split())


def edge_label(latex: str, labels: Sequence[str]) -> tuple[str, str] | None:
    """(LaTeX without its equation number, the number as printed) when the reading ends with
    `, (N)` or `\\quad (N)`, or starts with `(N) \\qquad`, and the glyphs have `(N)` at that edge of
    the box apart from the formula (`labels`, `edge_labels`); else None. `f(1)` at the end of a
    formula has neither the separator nor the gap, so it stays."""
    wanted = {re.sub(r"\s+", "", label) for label in labels}
    trailing = _TRAILING_NUMBER.search(latex)
    if trailing is not None and ("," in trailing["lead"] or _QUAD.search(trailing["lead"])):
        label = f"({trailing.group(2)})"
        kept = latex[: trailing.start()].rstrip()
        if label in wanted and kept:
            comma = "," if "," in trailing["lead"] else ""
            return f"{kept}{comma} {trailing['end']}".strip() if trailing[
                "end"
            ] else kept + comma, label
    leading = _LEADING_NUMBER.match(latex)
    if leading is not None and _QUAD.search(leading["tail"]):
        label = f"({leading.group(1)})"
        kept = latex[leading.end() :].strip()
        if label in wanted and kept:
            return kept, label
    return None


class Item(NamedTuple):
    """One glyph of the box with its place: character, left and right edge, middle of its height,
    font size."""

    c: str
    x0: float
    x1: float
    cy: float
    size: float


_LABEL_TEXT = re.compile(r"^\(\d+(?:\.\d+)?\)$")
CLUSTER_GAP = 0.5  # x font size: glyphs closer than this are one run (the digits of "(5.5)")
LABEL_GAP = 0.5  # x font size: a label stands this far from the formula on its row (real ones: 0.7)
ROW_TOLERANCE = 0.5  # x font size: glyphs this close in height share a row


def edge_labels(items: Sequence[Item]) -> tuple[str, ...]:
    """The equation numbers `(N)` or `(N.M)` printed at the left or right edge of a box, a gap
    of at least `LABEL_GAP` (half an em) of the font size from the rest of their row (or alone on
    it).
    rightmost and the leftmost run of the row each of those glyphs sits on."""
    ink = [i for i in items if i.c.strip()]
    if not ink:
        return ()
    found: list[str] = []
    for edge, reverse in (
        (max(ink, key=lambda i: i.x1), True),
        (min(ink, key=lambda i: i.x0), False),
    ):
        row = sorted(
            (i for i in ink if abs(i.cy - edge.cy) <= ROW_TOLERANCE * max(i.size, edge.size)),
            key=lambda i: i.x0,
            reverse=reverse,
        )
        run = [row[0]]
        for item in row[1:]:
            left, right = (item, run[-1]) if reverse else (run[-1], item)
            if right.x0 - left.x1 >= CLUSTER_GAP * max(left.size, right.size):
                break
            run.append(item)
        rest = row[len(run) :]
        text = "".join(i.c for i in (reversed(run) if reverse else run))
        if not _LABEL_TEXT.match(text):
            continue
        if rest:
            nearest = run[-1]
            other = rest[0]
            gap = (nearest.x0 - other.x1) if reverse else (other.x0 - nearest.x1)
            if gap < LABEL_GAP * max(nearest.size, other.size):
                continue
        found.append(text)
    return tuple(dict.fromkeys(found))


# --------------------------------------------------------------------------------- F7: script

_CAL_LETTER = re.compile(r"\\mathcal\s*(?:\{\s*([A-Z])\s*\}|([A-Z]))")


def restyle_script(latex: str, scripts: str) -> str:
    r"""`\mathcal{E}` written `\mathscr{E}` for a capital the PDF draws in an RSFS or EUSM face
    (`GlyphContext.scr`): CMSY's capitals stay `\mathcal`. A letter the reading writes both ways
    is left as it is."""
    if not scripts or "\\mathcal" not in latex:
        return latex
    both = {m.group(1) for m in re.finditer(r"\\mathscr\s*\{\s*([A-Z])\s*\}", latex)}

    def swap(match: re.Match[str]) -> str:
        letter = match.group(1) or match.group(2)
        return (
            f"\\mathscr{{{letter}}}" if letter in scripts and letter not in both else match.group(0)
        )

    return _CAL_LETTER.sub(swap, latex)
