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

import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from defusedxml import ElementTree as SafeET

from a11y_stem.core.mathchars import styled_char
from a11y_stem.core.types import Element, Fix, Kind

# The step's own comparison rules, imported rather than copied so the screen and the
# step always agree (and pipeline/equation_step.py needs no change for the screen).
from a11y_stem.pipeline.equation_step import (
    _layer_letters,
    _letter_verdict,
    _LetterStyles,
    _MathLetters,
    _mathml_letters,
    _symbol_bag,
)

BBox = tuple[float, float, float, float]

# Signs a dropped or added one of which changes the value. "-" and U+2212 are one sign.
SIGNS = {"-": "minus", "−": "minus", "+": "plus", "±": "plus-minus", "∓": "minus-plus"}
SIGN_CHARS = {"minus": "−", "plus": "+", "plus-minus": "±", "minus-plus": "∓"}

# The compare crop: this many font sizes of page on each side of the equation.
INLINE_PAD_EM = 4.0
BLOCK_PAD_EM = 1.5
VERTICAL_PAD_EM = 0.45
DEFAULT_FONT_PT = 10.0
# Shown at most this many CSS px per point (a 10 pt equation at 40 px), and rendered at
# twice that for sharp text on a 2x screen.
COMPARE_PX_PER_PT = 4.0


@dataclass(frozen=True)
class DiffRow:
    """One line of the "What differs" table. Text only: the table is the way to get this
    information without seeing the outline on the image."""

    what: str  # "Style", "Symbol" or "Sign"
    symbol: str  # as the PDF shows it (or the proposal, when only it has it)
    pdf: str  # "bold", "2 times", "not there"
    proposal: str
    meaning: bool  # True: it changes what a student reads


@dataclass
class EquationCompare:
    rows: list[DiffRow] = field(default_factory=list)
    same: list[str] = field(default_factory=list)  # "d (bold)": compared, no difference
    pdf_marks: set[str] = field(default_factory=set)  # characters to outline on the crop
    proposal_marks: set[str] = field(default_factory=set)  # ... in the proposed MathML
    summary: str | None = None  # the one sentence that says what to look at

    @property
    def has_text_layer(self) -> bool:
        return bool(self.rows or self.same)


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


def _style_sentence(letter: str, pdf: str, proposal: str, meaning: bool) -> str:
    vector = " (a vector)" if meaning else ""
    if pdf.startswith("bold") and not proposal.startswith("bold"):
        text = f"The {letter} is bold in the PDF{vector} but {proposal} in the proposal."
    elif proposal.startswith("bold") and not pdf.startswith("bold"):
        text = f"The {letter} is {pdf} in the PDF but bold{vector} in the proposal."
    else:
        text = f"The {letter} is {pdf} in the PDF but {proposal} in the proposal."
    return text if meaning else text[:-1] + ", which does not change the meaning."


def _times(n: int) -> str:
    return "not there" if n == 0 else ("once" if n == 1 else f"{n} times")


def _signs(text: str) -> Counter[str]:
    return Counter(SIGNS[ch] for ch in text if ch in SIGNS)


def compare_equation(element: Element | None, fix: Fix) -> EquationCompare:
    """The differences the step's text-layer checks look at, letter by letter. Empty
    when the PDF has no text layer for the equation or there is no proposal."""
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
    sentences: list[tuple[int, str]] = []  # (priority, sentence): meaning first
    for letter in sorted(pdf.keys() & got.keys()):
        verdict = _letter_verdict(pdf[letter], got[letter], letter)
        pdf_style, proposal_style = _pdf_style(pdf[letter]), _proposal_style(got[letter])
        if verdict == "match":
            if pdf[letter].bold or pdf[letter].other:
                out.same.append(f"{letter} ({pdf_style})")
            continue
        meaning = verdict == "meaning"
        out.rows.append(
            DiffRow("Style", _shown(letter, pdf_style), pdf_style, proposal_style, meaning)
        )
        out.pdf_marks.add(letter)
        out.proposal_marks.add(letter)
        sentences.append(
            (0 if meaning else 2, _style_sentence(letter, pdf_style, proposal_style, meaning))
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
        sentences.append((1, f"The PDF has {missing[0]} where the proposal has {extra[0]}."))
    elif missing:
        sentences.append((1, f"The PDF has {_listed(missing)}, which the proposal does not."))
    elif extra:
        sentences.append((1, f"The proposal has {_listed(extra)}, which the PDF does not."))
    pdf_signs, got_signs = _signs(text), _signs(mathml_text)
    for sign in sorted(set(pdf_signs) | set(got_signs)):
        pdf_n, got_n = pdf_signs[sign], got_signs[sign]
        if pdf_n == got_n:
            continue
        out.rows.append(DiffRow("Sign", SIGN_CHARS[sign], _times(pdf_n), _times(got_n), True))
        out.pdf_marks.update(ch for ch, name in SIGNS.items() if name == sign)
        out.proposal_marks.update(ch for ch, name in SIGNS.items() if name == sign)
        sentences.append(
            (1, f"The PDF has {_signs_word(pdf_n, sign)}; the proposal has {got_n or 'none'}.")
        )
    if sentences:
        out.summary = min(sentences, key=lambda s: s[0])[1]
    return out


def _listed(symbols: list[str]) -> str:
    unique = list(dict.fromkeys(symbols))
    return unique[0] if len(unique) == 1 else ", ".join(unique[:-1]) + " and " + unique[-1]


def _signs_word(n: int, sign: str) -> str:
    return f"{n} {sign} sign{'' if n == 1 else 's'}" if n else f"no {sign} sign"


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
    """A heading with the lines around it: enough to see what kind of line it is."""
    font = element.font_size or DEFAULT_FONT_PT
    x0, y0, x1, y1 = element.bbox
    width = page_size[0] if page_size else x1 + 30 * font
    return _clamp(
        (x0 - 2 * font, y0 - 2.2 * font, max(x1 + 2 * font, width * 0.62), y1 + 3.4 * font),
        page_size,
    )


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
