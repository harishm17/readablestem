"""What differs between an equation in the PDF and its proposal, for the review screen.

The equation step compares the PDF's own text layer (`Element.text`, with bold letters
written as math bold code points) with the proposed MathML, but it records only a
content-free check code ("style_mismatch"). The reviewer needs the letters themselves:
"the ω is bold in the PDF (a vector) but plain in the proposal". This module works that
out again when the review screen is shown, from the stored element and fix, with the
step's own rules (`pipeline.equation_step`), so nothing about the document is added to
the stored checks, the logs or the aggregates.

It also places the "same-size compare": a crop of the page a little wider than the
equation, so the neighbouring words show how bold the body text is (`context_clip`),
and the size the proposal must be drawn at to match it.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import cache

from defusedxml import ElementTree as SafeET

from a11y_stem.core.mathchars import base_char, char_variant, styled_char
from a11y_stem.core.types import Element, Fix, Kind

# The step's own comparison rules, imported rather than copied so the screen and the
# step always agree (and pipeline/equation_step.py needs no change for the screen).
from a11y_stem.pipeline.equation_step import (
    LETTER_STYLES,
    _layer_letters,
    _letter_verdict,
    _LetterStyles,
    _MathLetters,
    _mathml_letters,
    _symbol_bag,
)
from a11y_stem.pipeline.text_layer_compare import compare_case, numbers_differ

BBox = tuple[float, float, float, float]

# Signs a dropped or added one of which changes the value. "-" and U+2212 are one sign.
SIGNS = {"-": "minus", "−": "minus", "+": "plus", "±": "plus-minus", "∓": "minus-plus"}
SIGN_CHARS = {"minus": "−", "plus": "+", "plus-minus": "±", "minus-plus": "∓"}

# The compare crop: this many font sizes of page on each side of the equation.
INLINE_PAD_EM = 4.0
BLOCK_PAD_EM = 1.5
VERTICAL_PAD_EM = 0.2  # more pulls in the descenders of the line above
DEFAULT_FONT_PT = 10.0
# Shown at most this many CSS px per point (a 10 pt equation at 35 px), and rendered at
# twice that for sharp text on a 2x screen.
COMPARE_PX_PER_PT = 3.5


@dataclass(frozen=True)
class DiffRow:
    """One line of the "What differs" table. Text only: the table is the way to get this
    information without seeing the outline on the image."""

    what: str  # "Style", "Symbol", "Sign", "Number" or "Case"
    symbol: str  # as the PDF shows it (or the proposal, when only it has it)
    pdf: str  # "bold", "2 times", "not there"
    proposal: str
    meaning: bool  # True: it changes what a student reads
    letter: str = ""  # a style row's letter, unstyled ("ω" for a bold 𝛚)


@dataclass
class EquationCompare:
    rows: list[DiffRow] = field(default_factory=list)
    same: list[str] = field(default_factory=list)  # "d (bold)": compared, no difference
    # Letters the proposal gives a style the PDF's text layer cannot show ("double-struck"
    # and the letter in it: a double-struck R from an MSBM font is a plain "R" there). Not
    # a difference: only the image can tell.
    unseen: list[str] = field(default_factory=list)
    pdf_marks: set[str] = field(default_factory=set)  # characters to outline on the crop
    proposal_marks: set[str] = field(default_factory=set)  # ... in the proposed MathML
    summary: str | None = None  # the one sentence that says what to look at
    # Every sentence, by what it is about: "style" (a style that changes the meaning),
    # "symbol", "sign", "number", "case", "cosmetic" (a style that does not) and "unseen".
    said: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_text_layer(self) -> bool:
        return bool(self.rows or self.same or self.unseen)

    @property
    def meaning(self) -> bool:
        """A difference that changes what a student reads."""
        return any(row.meaning for row in self.rows)


def _fold(ch: str) -> str:
    return unicodedata.normalize("NFKC", ch).casefold()


def _pdf_style(styles: _LetterStyles) -> str:
    parts = []
    if styles.bold:
        parts.append("bold")
    if styles.plain:
        other = sorted(styles.other)
        parts.append(" and ".join(other) if other else "plain")
    return _join(parts)


def _proposal_style(letters: _MathLetters) -> str:
    bold = letters.var_bold + letters.other_bold
    plain = letters.var_plain + letters.other_plain
    parts = []
    if bold:
        parts.append("bold")
    if plain:
        other = sorted(letters.styles)
        parts.append(" and ".join(other) if other else "plain")
    return _join(parts)


def _join(parts: list[str]) -> str:
    return " in one place, ".join(parts) + (" in another" if len(parts) > 1 else "")


def _shown(letter: str, style: str) -> str:
    """The letter drawn in the style named first (bold d -> U+1D41D), for the table."""
    variant = style.split(" ", 1)[0]
    if variant in {"bold", "double-struck", "script", "fraktur"}:
        return styled_char(letter, variant)
    return letter


def _style_sentence(
    letter: str, pdf: str, proposal: str, meaning: bool, other: str = "the proposal"
) -> str:
    vector = " (a vector)" if meaning else ""
    if pdf.startswith("bold") and not proposal.startswith("bold"):
        text = f"The {letter} is bold in the PDF{vector} but {proposal} in {other}."
    elif proposal.startswith("bold") and not pdf.startswith("bold"):
        text = f"The {letter} is {pdf} in the PDF but bold{vector} in {other}."
    else:
        text = f"The {letter} is {pdf} in the PDF but {proposal} in {other}."
    return text if meaning else text[:-1] + ", which does not change the meaning."


def _capital(words: str) -> str:
    return words[:1].upper() + words[1:]


def _times(n: int) -> str:
    return "not there" if n == 0 else ("once" if n == 1 else f"{n} times")


def _signs(text: str) -> Counter[str]:
    return Counter(SIGNS[ch] for ch in text if ch in SIGNS)


def compare_equation(
    element: Element | None, fix: Fix, *, other: str = "the proposal"
) -> EquationCompare:
    """The differences the step's text-layer checks look at, letter by letter. Empty
    when the PDF has no text layer for the equation or there is no proposal. `other`
    names the transcription in the sentences ("your edit" for a reviewer's LaTeX)."""
    out = EquationCompare()
    if element is None or fix.kind is not Kind.EQUATION or fix.equation is None:
        return out
    text = element.text or ""
    if not any(ch.isalnum() for ch in text):
        return out
    try:
        root = SafeET.fromstring(fix.equation.mathml, forbid_dtd=True)
        mathml_text = " ".join(root.itertext())
        got = _mathml_letters(fix.equation.mathml)
    except Exception:  # unparsable MathML: the checks say so; nothing to compare
        return out
    pdf = _layer_letters(text, element.regular_letters or "")
    sentences: list[tuple[int, str, str]] = []  # (priority, about, sentence): meaning first
    for letter in sorted(pdf.keys() & got.keys()):
        verdict = _letter_verdict(pdf[letter], got[letter], letter)
        pdf_style, proposal_style = _pdf_style(pdf[letter]), _proposal_style(got[letter])
        if verdict == "match":
            if pdf[letter].bold or pdf[letter].other:
                out.same.append(f"{letter} ({pdf_style})")
            continue
        if verdict == "cosmetic" and _unseen_style(pdf[letter], got[letter]):
            style = sorted(got[letter].styles)[0]
            out.unseen.append(f"{style} {styled_char(letter, style)}")
            sentences.append(
                (
                    3,
                    "unseen",
                    f"{_capital(other)} has a {out.unseen[-1]}. The PDF's text cannot show "
                    "that style, so check it in the image.",
                )
            )
            continue
        meaning = verdict == "meaning"
        out.rows.append(
            DiffRow("Style", _shown(letter, pdf_style), pdf_style, proposal_style, meaning, letter)
        )
        out.pdf_marks.add(letter)
        out.proposal_marks.add(letter)
        sentences.append(
            (
                0 if meaning else 2,
                "style" if meaning else "cosmetic",
                _style_sentence(letter, pdf_style, proposal_style, meaning, other),
            )
        )
    expected, proposed = _symbol_bag(text), _symbol_bag(mathml_text)
    missing = sorted((expected - proposed).elements())
    extra = sorted((proposed - expected).elements())
    for symbol in sorted(set(expected) | set(proposed)):
        pdf_n, got_n = expected[symbol], proposed[symbol]
        if pdf_n == got_n:
            continue
        out.rows.append(DiffRow("Symbol", symbol, _times(pdf_n), _times(got_n), True))
        if pdf_n:
            out.pdf_marks.add(symbol)
        if got_n:
            out.proposal_marks.add(symbol)
    if len(missing) == 1 and len(extra) == 1:
        sentences.append((1, "symbol", f"The PDF has {missing[0]} where {other} has {extra[0]}."))
    elif missing:
        sentences.append((1, "symbol", f"The PDF has {_listed(missing)}, which {other} does not."))
    elif extra:
        sentences.append(
            (1, "symbol", f"{_capital(other)} has {_listed(extra)}, which the PDF does not.")
        )
    pdf_signs, got_signs = _signs(text), _signs(mathml_text)
    for sign in sorted(set(pdf_signs) | set(got_signs)):
        pdf_n, got_n = pdf_signs[sign], got_signs[sign]
        if pdf_n == got_n:
            continue
        out.rows.append(DiffRow("Sign", SIGN_CHARS[sign], _times(pdf_n), _times(got_n), True))
        out.pdf_marks.update(ch for ch, name in SIGNS.items() if name == sign)
        out.proposal_marks.update(ch for ch, name in SIGNS.items() if name == sign)
        sentences.append(
            (1, "sign", f"The PDF has {_signs_word(pdf_n, sign)}; {other} has {got_n or 'none'}.")
        )
    if not missing and not extra:
        # The letters and digits agree as multisets: the step then asks whether the
        # numbers are the same numbers and the capitals the same capitals.
        _numbers_and_case(out, sentences, text, fix.equation.mathml, other)
    if sentences:
        out.summary = min(sentences, key=lambda s: s[0])[2]
    for _, about, sentence in sorted(sentences, key=lambda s: s[0]):
        out.said.setdefault(about, []).append(sentence)
    return out


def _numbers_and_case(
    out: EquationCompare, sentences: list[tuple[int, str, str]], text: str, mathml: str, other: str
) -> None:
    """The rows and sentences for `number_mismatch` and `case_mismatch` (the step's
    `text_layer_compare`): 36 for 63, W for w."""
    numbers = numbers_differ(text, mathml)
    if numbers is not None and numbers.differ:
        for number in numbers.pdf:
            out.rows.append(DiffRow("Number", number, "there", "not there", True))
        for number in numbers.mathml:
            out.rows.append(DiffRow("Number", number, "not there", "there", True))
        out.pdf_marks.update(ch for n in numbers.pdf for ch in n if ch.isdigit())
        out.proposal_marks.update(ch for n in numbers.mathml for ch in n if ch.isdigit())
        if len(numbers.pdf) == 1 and len(numbers.mathml) == 1:
            said = f"The PDF has the number {numbers.pdf[0]} where {other} has {numbers.mathml[0]}."
        elif numbers.pdf and numbers.mathml:
            said = (
                f"The PDF has the numbers {_listed(list(numbers.pdf))}; {other} has "
                f"{_listed(list(numbers.mathml))} (the same digits make other numbers)."
            )
        elif numbers.pdf:
            said = f"The PDF has the number {_listed(list(numbers.pdf))}, which {other} does not."
        else:
            said = (
                f"{_capital(other)} has the number {_listed(list(numbers.mathml))}, "
                "which the PDF does not."
            )
        sentences.append((1, "number", said))
    for row in compare_case(text, mathml):
        pdf_case = _case_word(row.pdf_capital, row.pdf_small)
        got_case = _case_word(row.mathml_capital, row.mathml_small)
        out.rows.append(DiffRow("Case", row.letter, pdf_case, got_case, True))
        out.pdf_marks.update({row.letter, row.letter.upper()})
        out.proposal_marks.update({row.letter, row.letter.upper()})
        sentences.append(
            (
                1,
                "case",
                f"The PDF has {_case_phrase(row.letter, row.pdf_capital, row.pdf_small)}; "
                f"{other} has {_case_phrase(row.letter, row.mathml_capital, row.mathml_small)}.",
            )
        )


def _case_word(capital: int, small: int) -> str:
    parts = []
    if capital:
        parts.append(f"capital {_times(capital)}")
    if small:
        parts.append(f"small {_times(small)}")
    return ", ".join(parts) or "not there"


def _case_phrase(letter: str, capital: int, small: int) -> str:
    parts = []
    if capital:
        parts.append(f"a capital {letter.upper()}")
    if small:
        parts.append(f"a small {letter}")
    return " and ".join(parts) or f"no {letter}"


def _unseen_style(pdf: _LetterStyles, got: _MathLetters) -> bool:
    """The only difference is a letter style the text layer cannot carry: the PDF shows
    the letter plain (not known to come from a regular font, which would make it really
    plain), the proposal gives it a double-struck, script or fraktur style and no bold.
    The equation step calls this cosmetic (`_letter_verdict`); the screen must not call
    it a difference either, since the image above may well show the same style."""
    return (
        not pdf.bold
        and not pdf.other
        and not pdf.known_plain
        and bool(got.styles)
        and got.styles <= LETTER_STYLES
        and not (got.var_bold + got.other_bold)
    )


def _listed(symbols: list[str]) -> str:
    unique = list(dict.fromkeys(symbols))
    return unique[0] if len(unique) == 1 else ", ".join(unique[:-1]) + " and " + unique[-1]


def _signs_word(n: int, sign: str) -> str:
    return f"{n} {sign} sign{'' if n == 1 else 's'}" if n else f"no {sign} sign"


# --------------------------------------------------------------------------- suggestion


@dataclass(frozen=True)
class Suggestion:
    """A one-click fix for a style difference that changes the meaning: the proposal's
    LaTeX with only that letter's style changed to the PDF's ("Use bold ω")."""

    label: str  # the button: "Use bold ω"
    latex: str  # posted as an edit, so it goes through the same checks as a typed one


# A brace group after these is left alone: words (\text{avg}), or letters already given a
# style (a bold one is not a plain one to make bold).
_PROTECTING = frozenset(
    {
        *("text", "textrm", "textit", "textbf", "textsf", "texttt", "mbox", "operatorname"),
        *("mathrm", "mathit", "mathsf", "mathtt", "mathbf", "boldsymbol", "bm", "pmb"),
        *("mathbb", "mathcal", "mathscr", "mathfrak"),
    }
)
_BOLD_WRAPPERS = ("mathbf", "boldsymbol", "bm")
_STYLE_WRAPPERS = {"double-struck": "mathbb", "script": "mathcal", "fraktur": "mathfrak"}
_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|[{}]|\s+|[^\\{}\s]", re.S)


@cache
def _command_char(command: str) -> str:
    """The character a symbol command stands for (\\omega -> ω), else ""."""
    from latex2mathml.symbols_parser import convert_symbol

    code = convert_symbol(command)
    try:
        return chr(int(code, 16)) if code else ""
    except ValueError:
        return ""


def _is_letter(token: str, letter: str) -> bool:
    if token.startswith("\\"):
        return len(token) > 2 and base_char(_command_char(token)) == letter
    return token == letter


def _wrap_letters(latex: str, letter: str, wrapper: str) -> str | None:
    """`latex` with every occurrence of `letter` outside a protected group (`_PROTECTING`)
    wrapped in `\\wrapper{...}`; None when there is none to wrap."""
    tokens = _TOKEN.findall(latex)
    out: list[str] = []
    depth_protected: list[bool] = []  # one per open brace: is it a protected group?
    protect_next = False  # the token after a protecting command (its argument)
    wrapped = 0
    for token in tokens:
        protected = any(depth_protected)
        if token == "{":
            depth_protected.append(protect_next)
            protect_next = False
            out.append(token)
            continue
        if token == "}":
            if depth_protected:
                depth_protected.pop()
            out.append(token)
            continue
        if token.isspace():
            out.append(token)
            continue
        if token.startswith("\\") and token[1:] in _PROTECTING:
            protect_next = True
            out.append(token)
            continue
        if protect_next:  # \mathbf v: an argument without braces
            protect_next = False
            out.append(token)
            continue
        if not protected and _is_letter(token, letter):
            out.append(f"\\{wrapper}{{{token}}}")
            wrapped += 1
            continue
        out.append(token)
    return "".join(out) if wrapped else None


def _unwrap_bold(latex: str, letter: str) -> str | None:
    """`latex` with `\\mathbf{x}`, `\\boldsymbol{x}` and `\\bm{x}` around exactly `letter`
    made plain; None when there is none."""
    pattern = re.compile(r"\\(?:" + "|".join(_BOLD_WRAPPERS) + r")\s*\{\s*(\\[A-Za-z]+|.)\s*\}")
    count = 0

    def plain(match: re.Match[str]) -> str:
        nonlocal count
        if not _is_letter(match.group(1), letter):
            return match.group(0)
        count += 1
        return match.group(1)

    out = pattern.sub(plain, latex)
    return out if count else None


def _candidate(element: Element, fix: Fix, row: DiffRow) -> tuple[str, str] | None:
    """(style word, LaTeX) that would give `row.letter` the PDF's style."""
    assert fix.equation is not None
    latex, letter = fix.equation.latex, row.letter
    if row.pdf == "bold" and row.proposal == "plain":
        # Upright bold for a Latin letter the PDF has upright (\mathbf{v}); otherwise, and
        # always for Greek (which \mathbf leaves plain in LaTeX), \boldsymbol.
        variants = {char_variant(ch) for ch in element.text or "" if base_char(ch) == letter}
        upright = letter.isascii() and variants == {"bold"}
        wrapped = _wrap_letters(latex, letter, "mathbf" if upright else "boldsymbol")
        return ("bold", wrapped) if wrapped else None
    if row.pdf == "plain" and row.proposal == "bold":
        unwrapped = _unwrap_bold(latex, letter)
        return ("plain", unwrapped) if unwrapped else None
    if row.pdf in _STYLE_WRAPPERS and row.proposal == "plain" and letter.isascii():
        wrapped = _wrap_letters(latex, letter, _STYLE_WRAPPERS[row.pdf])
        return (row.pdf, wrapped) if wrapped else None
    return None


def suggest_fix(element: Element | None, fix: Fix, compare: EquationCompare) -> Suggestion | None:
    """The one-click fix for a proposal whose only meaning-level difference is one letter's
    style, or None. It is offered only when the changed LaTeX passes the same checks a
    typed edit must pass and the same comparison then finds no meaning-level difference
    (so a rewrite that does not do what its label says is never offered), and when Save
    edit would take it as it is (`web.edit_check` finds nothing that changes the meaning,
    so the button never ends in "Use my edit anyway")."""
    from a11y_stem.pipeline import validate

    if element is None or fix.equation is None:
        return None
    meaning = [row for row in compare.rows if row.meaning]
    if len(meaning) != 1 or meaning[0].what != "Style" or not meaning[0].letter:
        return None
    found = _candidate(element, fix, meaning[0])
    if found is None:
        return None
    style, latex = found
    if len(latex) > validate.MAX_LATEX_CHARS:
        return None
    if any(not c.passed and c.severity == "error" for c in validate.check_latex(latex)):
        return None
    try:
        mathml = validate.safe_latex_to_mathml(latex, fix.equation.display)
    except ValueError:
        return None
    if any(not c.passed and c.severity == "error" for c in validate.check_mathml(mathml)):
        return None
    changed = fix.model_copy(
        update={"equation": fix.equation.model_copy(update={"latex": latex, "mathml": mathml})}
    )
    if compare_equation(element, changed).meaning:
        return None
    from a11y_stem.web.edit_check import check_equation_edit

    checked = check_equation_edit(element, fix, latex)
    if checked.mathml is None or checked.meaning or checked.unchanged:
        return None
    return Suggestion(f"Use {style} {meaning[0].letter}", latex)


# --------------------------------------------------------------------------- regions


def _clamp(box: BBox, page_size: tuple[float, float] | None) -> BBox:
    x0, y0, x1, y1 = box
    width, height = page_size if page_size else (float("inf"), float("inf"))
    return (max(0.0, x0), max(0.0, y0), min(width, x1), min(height, y1))


def context_clip(element: Element, page_size: tuple[float, float] | None) -> BBox:
    """The equation's box with some of the page around it: a few words on each side of an
    inline equation (so the weight of a letter can be judged against the body text in
    the same image), a margin round a displayed one."""
    font = element.font_size or DEFAULT_FONT_PT
    x0, y0, x1, y1 = element.bbox
    pad_x = (INLINE_PAD_EM if element.display == "inline" else BLOCK_PAD_EM) * font
    pad_y = VERTICAL_PAD_EM * font
    return _clamp((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y), page_size)


def band_clip(element: Element, page_size: tuple[float, float] | None) -> BBox:
    """A heading with the lines around it (one above, two below) and about half a page
    width of them: enough to see whether it opens a section or starts a paragraph."""
    font = element.font_size or DEFAULT_FONT_PT
    x0, y0, x1, y1 = element.bbox
    width = page_size[0] if page_size else x1 + 30 * font
    left = x0 - 1.5 * font
    right = max(x1 + 2 * font, left + 0.55 * width)
    return _clamp((left, y0 - 1.6 * font, right, y1 + 3.0 * font), page_size)


@dataclass(frozen=True)
class CompareGeometry:
    """Where the proposal goes under the crop, in units of the crop's width (CSS `cqw`):
    its font size and its left edge, so both rows are drawn at the same scale."""

    max_width_px: float  # the crop's natural display width
    font_cqw: float
    left_cqw: float
    zoom: float  # pixels per point to render the crop at


def compare_geometry(element: Element, clip: BBox, *, max_side_px: int) -> CompareGeometry:
    font = element.font_size or DEFAULT_FONT_PT
    width = max(clip[2] - clip[0], 1.0)
    return CompareGeometry(
        max_width_px=round(width * COMPARE_PX_PER_PT, 1),
        font_cqw=round(100 * font / width, 3),
        left_cqw=round(100 * max(element.bbox[0] - clip[0], 0.0) / width, 3),
        zoom=min(2 * COMPARE_PX_PER_PT, (max_side_px - 1) / width),
    )
