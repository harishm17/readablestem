"""Signs and relations: does a transcription keep the PDF's minus signs and relations?

A dropped or flipped sign changes what an equation says while every letter and digit
stays right (e^{-λt} read as e^{λt}, a = -9.8 read as a = 9.8, x ≤ 1 read as x < 1), and
the symbol check (`equation_step.text_layer_symbols_differ`) compares letters, digits and
Greek letters only. `compare_signs` compares the sign and relation tokens of the PDF
text layer (`Element.text`) with those of the transcription's MathML.

Tokens, by class (typographic variants of one symbol fold into one class, `_SIGNS`):

- minus: U+2212 and its super/subscript forms; ASCII "-", the hyphens U+2010/U+2011,
  the en dash and the small/fullwidth hyphen-minus when they act as a minus (below);
- plus, plus-minus (±), minus-plus (∓);
- relations: = (with its small, fullwidth and script forms and the decorated equals
  signs ≐ ≔ ≜ ..., which TeX may print as a mark over "="), ≠ (TeX's negation slash
  over = composes to it), <, >, ≤ (⩽ ≦), ≥ (⩾ ≧), ≪, ≫, the negated ≮ ≯ ≰ ≱, ≈, ≉,
  ∼ (and ASCII ~), ≁, ≃, ≅, ≡, ≢, ∝;
- arrows: → (⟶, ↦, ⟼), ← (⟵, ↤), ↔ (⟷), ⇒ (⟹), ⇐ (⟸), ⇔ (⟺). The gold set uses →
  only, as a vector accent; limits ("x → 0") and implications use the others. TeX
  builds long arrows from a shaft of minus or = glyphs glued to a short arrow head
  (\\longrightarrow prints as "−→", \\Longrightarrow as "=⇒", \\overrightarrow{AB} as
  "−−→ AB", \\longleftarrow as "←−"); a real minus can touch the same head ("OB −−→ OA",
  "x ←−1" for x \\gets -1), so the head is an arrow and the glued run is uncertain (below).
  Two heads joined by a shaft ("⇐⇒", "←→") are one two-way arrow, and ASCII digraphs in
  typed text ("->", "=>", "<=>"; ">=" and "<=" are ≥, ≤; "+/-" and "-/+" are ±, ∓) are
  one arrow, relation or sign, folded the same way on both sides (in the MathML across
  adjacent `<mo>`, as a symbol-by-symbol transcription writes them, and in `<mtext>`).
  Other relations TeX stacks from two glyphs are one relation too: \\cong prints as "∼="
  (≅), newtx's \\mapsto as "↦→" (↦); \\models prints as "|=", which cannot be told from
  Chromium's "|𝑥|= 1", so an = straight after "|" is uncertain.
  Any other arrow (↪, ⇌, ↑) may stand for any arrow class;
- × and · (⋅ ∙): only to catch a dot product read as a cross product (below).

Left out on purpose:

- multiplication style: ×, · and juxtaposition are the same multiplication for numbers
  and scalars, and the text layer cannot show juxtaposition (`2ab`), while units in text
  mode (`\\text{N·m}`) put a dot on one side only. A swap of × and · is meaning-level
  only where the text layer shows a vector product (a bold letter on both sides): the
  dot and cross products. An ASCII letter x is never a times sign (it is a letter, so
  the symbol check sees it);
- division: / and ÷ mean what a fraction bar means, and fraction bars are drawn lines
  that never appear as text, so a/b against \\frac{a}{b} cannot be compared;
- brackets, bars and fences (out of scope), primes, accents other than arrows,
  invisible operators (U+2061-U+2064: never printed), set relations (∈, ⊂), and every
  other operator.

Uncertainty. Each side counts each class as an interval [certain, certain + uncertain],
and a class differs only when the two intervals do not meet. So a missing sign the PDF
certainly has is always caught, and an ambiguous glyph can widen a range but never
create a flag on its own:

- PDF: a hyphen or en dash directly between two letters or digits ("time-dependent",
  "1–3") may be a word hyphen or a range, so it is uncertain; anywhere else it is a
  minus. The figure dash, em dash and horizontal bar (Chromium prints \\overline as
  U+2015) are uncertain. A run of minus or = glyphs glued to an arrow head is one
  uncertain minus (or equals): a shaft, or a real sign touching the head. One exception:
  a single U+2212 touching a → that its letter follows at once, after an operator, an
  opening bracket or nothing ("= −→𝑥"), is Chromium's minus before a vector arrow, so it
  is certain. A glyph the PDF cannot name (private use, U+FFFD; old Symbol and MT Extra
  fonts put operators there) may be any sign: it widens every class. A relation directly
  beside a private-use glyph may have a negation slash laid over it (KaTeX prints \\neq
  as "=" under its \\not glyph, U+E020, which is only ever that slash), so it counts as
  uncertain, and so does its negation;
- MathML: `<mo>`, `<mi>` and `<mn>` are maths, so their signs are certain (a leading
  minus inside `<mn>` included; latex2mathml writes ± and ∼ as `<mi>`). An accent (the
  `<mo>` script of `<mover>`/`<munder>`: \\vec's arrow, \\tilde's ~) is uncertain:
  Chromium prints it as a standalone →, TeX as a combining accent or not at all. A
  relation after latex2mathml's \\not (a zero-width slash) is uncertain, and so is its
  negation: TeX composes the two into ≠, Chromium prints them apart. In
  `<mtext>` a hyphen between letters or digits is a word hyphen and is skipped
  ("time-dependent"); any other sign there is uncertain.

Everything returned is a class name, never document text.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import defusedxml.ElementTree as DET

from a11y_stem.core.mathchars import STYLE_GROUPS, char_variant

MINUS = "minus"
PLUS = "plus"
PLUS_MINUS = "plus-minus"
MINUS_PLUS = "minus-plus"
EQUALS = "equals"
NOT_EQUAL = "not-equal"
LESS = "less"
GREATER = "greater"
LESS_EQUAL = "less-equal"
GREATER_EQUAL = "greater-equal"
MUCH_LESS = "much-less"
MUCH_GREATER = "much-greater"
NOT_LESS = "not-less"
NOT_GREATER = "not-greater"
NOT_LESS_EQUAL = "not-less-equal"
NOT_GREATER_EQUAL = "not-greater-equal"
APPROX = "approx"
NOT_APPROX = "not-approx"
SIMILAR = "similar"
NOT_SIMILAR = "not-similar"
SIMILAR_EQUAL = "similar-equal"
CONGRUENT = "congruent"
EQUIVALENT = "equivalent"
NOT_EQUIVALENT = "not-equivalent"
PROPORTIONAL = "proportional"
RIGHT_ARROW = "right-arrow"
LEFT_ARROW = "left-arrow"
BOTH_ARROW = "left-right-arrow"
IMPLIES = "implies"
IMPLIED_BY = "implied-by"
IFF = "iff"
CROSS = "cross"
DOT = "dot"
# Reported when a × and a · swapped places in an equation with a vector product.
VECTOR_PRODUCT = "vector-product"

ARROWS = frozenset({RIGHT_ARROW, LEFT_ARROW, BOTH_ARROW, IMPLIES, IMPLIED_BY, IFF})
PRODUCTS = frozenset({CROSS, DOT})
# Every other class is a relation or an arrow, which a negation slash can overlay.
OPERATIONS = frozenset({MINUS, PLUS, PLUS_MINUS, MINUS_PLUS}) | PRODUCTS


def _classes(table: dict[str, str]) -> dict[str, str]:
    return {ch: cls for chars, cls in table.items() for ch in chars}


_SIGNS: dict[str, str] = _classes(
    {
        "\N{MINUS SIGN}\N{SUPERSCRIPT MINUS}\N{SUBSCRIPT MINUS}\N{HEAVY MINUS SIGN}": MINUS,
        "+\N{SUPERSCRIPT PLUS SIGN}\N{SUBSCRIPT PLUS SIGN}\N{SMALL PLUS SIGN}"
        "\N{FULLWIDTH PLUS SIGN}\N{HEAVY PLUS SIGN}": PLUS,
        "\N{PLUS-MINUS SIGN}": PLUS_MINUS,
        "\N{MINUS-OR-PLUS SIGN}": MINUS_PLUS,
        # = in its forms, and the decorated equals signs (≐ ≔ ≕ ≗ ≙ ≚ ≛ ≜ ≝ ≞ ≟): TeX may
        # print those as a symbol over a plain "=".
        "=\N{SUPERSCRIPT EQUALS SIGN}\N{SUBSCRIPT EQUALS SIGN}\N{SMALL EQUALS SIGN}"
        "\N{FULLWIDTH EQUALS SIGN}≐≔≕≗≙≚≛≜≝≞≟": EQUALS,
        "≠": NOT_EQUAL,
        "<\N{SMALL LESS-THAN SIGN}\N{FULLWIDTH LESS-THAN SIGN}": LESS,
        ">\N{SMALL GREATER-THAN SIGN}\N{FULLWIDTH GREATER-THAN SIGN}": GREATER,
        "≤⩽≦": LESS_EQUAL,
        "≥⩾≧": GREATER_EQUAL,
        "≪": MUCH_LESS,
        "≫": MUCH_GREATER,
        "≮": NOT_LESS,
        "≯": NOT_GREATER,
        "≰": NOT_LESS_EQUAL,
        "≱": NOT_GREATER_EQUAL,
        "≈": APPROX,
        "≉": NOT_APPROX,
        "\N{TILDE OPERATOR}~": SIMILAR,
        "≁": NOT_SIMILAR,
        "≃": SIMILAR_EQUAL,
        "≅": CONGRUENT,
        "≡": EQUIVALENT,
        "≢": NOT_EQUIVALENT,
        "∝": PROPORTIONAL,
        "→⟶↦⟼": RIGHT_ARROW,
        "←⟵↤": LEFT_ARROW,
        "↔⟷": BOTH_ARROW,
        "⇒⟹": IMPLIES,
        "⇐⟸": IMPLIED_BY,
        "⇔⟺": IFF,
        "\N{MULTIPLICATION SIGN}\N{VECTOR OR CROSS PRODUCT}": CROSS,
        "\N{MIDDLE DOT}\N{DOT OPERATOR}\N{BULLET OPERATOR}": DOT,
    }
)
# A minus when it stands as one; a word hyphen or a range between two letters or digits.
_DASHES = frozenset(
    "-\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{EN DASH}\N{SMALL HYPHEN-MINUS}\N{FULLWIDTH HYPHEN-MINUS}"
)
# Maybe a minus, maybe a rule or punctuation (Chromium prints \overline as a bar).
_LOOSE_DASHES = frozenset("\N{FIGURE DASH}\N{EM DASH}\N{HORIZONTAL BAR}")
_ANY_DASH = _DASHES | _LOOSE_DASHES
_DASH_RUN = re.compile("[" + re.escape("".join(sorted(_ANY_DASH))) + "]+")
# Arrows, relations and ± typed as plain ASCII ("x -> 0", "x >= 1", "5.0 +/- 0.2"),
# longest first, folded the same way in the text layer, in `<mtext>` and across adjacent
# `<mo>` (a transcription symbol by symbol is `<mo>&gt;</mo><mo>=</mo>`). "<-" alone
# stays as it is: "x<-1" is "x < -1".
_ASCII_DIGRAPHS = (
    ("+/-", "±"),
    ("-/+", "∓"),
    ("<=>", "⇔"),
    ("<->", "↔"),
    ("==>", "⇒"),
    ("-->", "→"),
    ("<==", "⇐"),
    ("<--", "←"),
    ("=>", "⇒"),
    ("->", "→"),
    ("<=", "≤"),
    (">=", "≥"),
)
# TeX draws long and extensible arrows as a shaft of minus or = glyphs glued to a short
# arrow head (\longrightarrow "−→", \Longrightarrow "=⇒", \overrightarrow{AB} "−−→ AB",
# \longleftarrow "←−"). A real minus can touch the same head ("OB −−→ OA" is a minus and
# an arrow over OA, "x ←−1" is x \gets -1), so a glued run is one uncertain minus (or
# equals) and the head counts as an arrow. Two heads joined by a shaft are one arrow.
_SHAFT = re.escape("\N{MINUS SIGN}" + "".join(sorted(_ANY_DASH)))
_RIGHT_HEADS = "→⇒↦"
_LEFT_HEADS = "←⇐"
_GLUED = re.compile(
    f"[{_SHAFT}]+(?=[{_RIGHT_HEADS}])|(?<=[{_LEFT_HEADS}])[{_SHAFT}]+"
    f"|=+(?=[{_RIGHT_HEADS}])|(?<=[{_LEFT_HEADS}])=+"
)
# Relations TeX stacks from two glyphs, which the text layer gives one after the other:
# two arrow heads joined by a shaft ("⇐⇒" for \iff, "←→"), \cong as ∼ over = ("∼="), and
# newtx's \mapsto as its bar and an arrow ("↦→", "↦−→" for \longmapsto).
_COMPOSITES = (
    (re.compile(f"←[{_SHAFT}]*→"), "↔"),
    (re.compile("⇐=*⇒"), "⇔"),
    (re.compile(f"↦[{_SHAFT}]*→"), "↦"),
    (re.compile("\N{TILDE OPERATOR}="), "≅"),
)
_OPENING = frozenset("([{⟨")
# Skipped when looking for the operands of a product: spaces and brackets.
_BRACKETS = frozenset("()[]{}|‖∥⟨⟩")
_MATH_TOKENS = frozenset({"mi", "mo", "mn"})
_TEXT_TOKENS = frozenset({"mtext", "ms"})
_UNDER_OVER = frozenset({"mover", "munder", "munderover"})
_ANNOTATIONS = frozenset({"annotation", "annotation-xml"})


def _unknown_arrow(ch: str) -> bool:
    """An arrow or harpoon not in `_SIGNS` (↪, ⇌, ↑): it may print as any arrow."""
    if ch in _SIGNS or unicodedata.category(ch) not in ("Sm", "So"):
        return False
    name = unicodedata.name(ch, "")
    return "ARROW" in name or "HARPOON" in name


def _unnamed(ch: str) -> bool:
    """A glyph the PDF could not name: private use or the replacement character."""
    return ch == "\N{REPLACEMENT CHARACTER}" or unicodedata.category(ch) == "Co"


# KaTeX's \not glyph (`\@not` in its symbol table, font KaTeX_Main), laid over a relation.
_KATEX_NOT = "\ue020"
# A negation slash: what TeX lays over a relation for \not, and what latex2mathml writes
# as a zero-width slash before it (`<mpadded width="0"><mtext>⧸</mtext></mpadded><mo>=</mo>`).
_NOT_OVERLAY = "\N{COMBINING LONG SOLIDUS OVERLAY}"
_NEGATION_SLASHES = frozenset("\N{BIG SOLIDUS}\N{COMBINING LONG SOLIDUS OVERLAY}")


@dataclass
class SignTokens:
    """One side's sign tokens: each class is an interval [certain, certain + uncertain]."""

    certain: Counter[str] = field(default_factory=Counter)
    uncertain: Counter[str] = field(default_factory=Counter)
    any_arrow: int = 0  # unknown arrows: each may be any arrow class
    any_sign: int = 0  # unnamed glyphs: each may be any class
    vector_products: int = 0  # PDF only: × or · with a bold letter on both sides

    def low(self, cls: str) -> int:
        return self.certain[cls]

    def high(self, cls: str) -> int:
        extra = self.any_sign + (self.any_arrow if cls in ARROWS else 0)
        return self.certain[cls] + self.uncertain[cls] + extra

    def shows_signs(self) -> bool:
        """Any compared token at all (unnamed glyphs alone say nothing)."""
        return bool((self.certain + self.uncertain).keys() - PRODUCTS) or bool(self.vector_products)


def _dash_runs(text: str) -> list[tuple[int, int]]:
    """(start, end) of each run of dashes: TeX's "--" is one en dash."""
    return [m.span() for m in _DASH_RUN.finditer(text)]


def _inside_word(text: str, start: int, end: int) -> bool:
    """Directly between two letters or digits: a word hyphen or a range."""
    return start > 0 and end < len(text) and text[start - 1].isalnum() and text[end].isalnum()


def _bold_letter(ch: str) -> bool:
    return ch.isalpha() and STYLE_GROUPS.get(char_variant(ch) or "") == "bold"


def _operand(text: str, index: int, step: int) -> str:
    """The nearest character before (step -1) or after (+1) `index`, skipping spaces and
    brackets: the operand next to a product sign in a flat text layer."""
    i = index + step
    while 0 <= i < len(text) and (text[i].isspace() or text[i] in _BRACKETS):
        i += step
    return text[i] if 0 <= i < len(text) else ""


def _typed_signs(text: str) -> str:
    """ASCII digraphs folded into the sign they stand for."""
    for typed, sign in _ASCII_DIGRAPHS:
        text = text.replace(typed, sign)
    return text


def _join_arrows(text: str) -> str:
    text = _typed_signs(text)
    for pattern, joined in _COMPOSITES:
        text = pattern.sub(joined, text)
    return text


def _overlaid(text: str, index: int, cls: str) -> bool:
    """A relation glyph next to a private-use glyph, which may be a slash laid over it:
    KaTeX prints \\neq as "=" with its \\not glyph (U+E020) over it. U+FFFD is left out:
    TeX's brace pieces and newtx's accents come out as U+FFFD beside an "="."""
    if cls in OPERATIONS:
        return False
    beside = text[max(index - 1, 0) : index] + text[index + 1 : index + 2]
    return any(_unnamed(ch) and ch != "\N{REPLACEMENT CHARACTER}" for ch in beside)


def _models_bar(text: str, index: int, cls: str) -> bool:
    """An = straight after "|": TeX prints \\models as "|=", while Chromium writes
    "|𝑥|= 1" with no space before a relation, so "|=" cannot simply be folded."""
    return cls == EQUALS and index > 0 and text[index - 1] == "|"


def _negation(ch: str) -> str | None:
    """The class of a relation with a negation slash over it (= gives ≠, < gives ≮)."""
    return _SIGNS.get(unicodedata.normalize("NFC", ch + _NOT_OVERLAY))


def _minus_before_vector_arrow(text: str, start: int, end: int) -> bool:
    """A minus that touches a vector arrow, not a long arrow's shaft.

    Chromium prints \\vec's arrow as a standalone → directly before its letter, and the
    minus in front may touch it ("= −→𝑥", "( −→𝑎"). One U+2212 glued to a → that a letter
    follows at once, after an operator, an opening bracket or nothing, is that minus: a
    long arrow sits between two operands ("x −→ 0"), and TeX's \\overrightarrow leaves a
    space before its letters ("−−→ AB")."""
    if end - start != 1 or text[start] != "\N{MINUS SIGN}":
        return False
    if text[end] != "→" or end + 1 >= len(text) or not text[end + 1].isalpha():
        return False
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    return i < 0 or text[i] in _OPENING or text[i] in _SIGNS


def layer_signs(text_layer: str) -> SignTokens:
    """Sign tokens of a PDF text layer (see the module docstring for what is uncertain)."""
    text = _join_arrows(unicodedata.normalize("NFC", text_layer))
    out = SignTokens()
    glued: set[int] = set()  # positions in a long arrow's shaft, counted here
    for match in _GLUED.finditer(text):
        start, end = match.span()
        glued.update(range(start, end))
        if text[start] == "=":
            out.uncertain[EQUALS] += 1
        elif _minus_before_vector_arrow(text, start, end):
            out.certain[MINUS] += 1
        else:
            out.uncertain[MINUS] += 1
    for start, end in _dash_runs(text):
        if start in glued:
            continue
        loose = any(ch in _LOOSE_DASHES for ch in text[start:end])
        if loose or _inside_word(text, start, end):
            out.uncertain[MINUS] += 1
        else:
            out.certain[MINUS] += 1
    for index, ch in enumerate(text):
        if ch in _ANY_DASH or index in glued:
            continue
        cls = _SIGNS.get(ch)
        if cls is not None:
            if _overlaid(text, index, cls):
                # This relation or its negation (a negation slash may be what is over it).
                out.uncertain[cls] += 1
                negation = _negation(ch)
                if negation is not None:
                    out.uncertain[negation] += 1
            elif _models_bar(text, index, cls):
                out.uncertain[cls] += 1
            else:
                out.certain[cls] += 1
            if cls in PRODUCTS and all(
                _bold_letter(_operand(text, index, step)) for step in (-1, 1)
            ):
                out.vector_products += 1
        elif ch == _KATEX_NOT:
            continue  # only ever a slash over a relation, counted with that relation
        elif _unnamed(ch):
            out.any_sign += 1
        elif _unknown_arrow(ch):
            out.any_arrow += 1
    return out


def _negation_slash(elem: Any) -> bool:
    """latex2mathml's \\not: an element holding nothing but a negation slash."""
    return "".join(elem.itertext()).strip() in _NEGATION_SLASHES and len(elem) <= 1


def _count_math(text: str, out: SignTokens, *, accent: bool, negated: bool = False) -> None:
    counts = out.uncertain if accent else out.certain
    for ch in unicodedata.normalize("NFC", text):
        if ch in _LOOSE_DASHES:
            out.uncertain[MINUS] += 1
        elif ch in _DASHES:
            counts[MINUS] += 1  # in a maths token a hyphen is a minus
        elif ch in _SIGNS:
            cls = _SIGNS[ch]
            if negated and cls not in OPERATIONS:
                # \not over this relation: the PDF may show it composed (≠) or not (=).
                out.uncertain[cls] += 1
                negation = _negation(ch)
                if negation is not None:
                    out.uncertain[negation] += 1
                negated = False
            else:
                counts[cls] += 1
        elif _unknown_arrow(ch):
            out.any_arrow += 1


def _count_text(text: str, out: SignTokens) -> None:
    """`<mtext>`: word hyphens and ranges are skipped, any other sign is uncertain."""
    text = _typed_signs(unicodedata.normalize("NFC", text))
    for start, end in _dash_runs(text):
        if not _inside_word(text, start, end):
            out.uncertain[MINUS] += 1
    for ch in text:
        if ch in _SIGNS:
            out.uncertain[_SIGNS[ch]] += 1
        elif _unknown_arrow(ch):
            out.any_arrow += 1


def mathml_signs(mathml: str) -> SignTokens:
    """Sign tokens of a MathML tree (see the module docstring for what is uncertain)."""
    out = SignTokens()

    def walk(elem: Any, accent: bool, negated: bool) -> None:
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag in _ANNOTATIONS:
            return
        if tag in _TEXT_TOKENS:
            _count_text(elem.text or "", out)
            return
        if tag in _MATH_TOKENS:
            _count_math(elem.text or "", out, accent=accent, negated=negated)
            return
        under_over = tag in _UNDER_OVER
        negate = False
        run: list[str] = []  # adjacent <mo> children, counted together (`_ASCII_DIGRAPHS`)

        def flush() -> None:
            nonlocal negate
            if run:
                typed = _typed_signs("".join(run).replace("\N{MINUS SIGN}", "-"))
                _count_math(typed, out, accent=False, negated=negate)
                run.clear()
                negate = False

        for index, child in enumerate(elem):
            if _negation_slash(child):
                flush()
                negate = True
                continue
            child_tag = child.tag.rsplit("}", 1)[-1]
            script_mo = under_over and index > 0 and child_tag == "mo"
            if child_tag == "mo" and not script_mo:
                run.append(child.text or "")
                continue
            flush()
            walk(child, script_mo, negate)
            negate = False
        flush()

    walk(DET.fromstring(mathml, forbid_dtd=True), False, False)
    return out


def _apart(layer: SignTokens, got: SignTokens, cls: str) -> bool:
    return got.high(cls) < layer.low(cls) or layer.high(cls) < got.low(cls)


def _product_swapped(layer: SignTokens, got: SignTokens) -> bool:
    """A × read as a · (or the reverse) where the text layer shows a vector product."""
    if not layer.vector_products:
        return False
    lost = any(got.high(cls) < layer.low(cls) for cls in PRODUCTS)
    gained = any(got.low(cls) > layer.high(cls) for cls in PRODUCTS)
    return lost and gained  # one class lost, the other gained: never the same one


@dataclass(frozen=True)
class SignComparison:
    """The sign classes whose counts cannot agree, sorted (empty: the signs match)."""

    differs: tuple[str, ...]

    @property
    def mismatch(self) -> bool:
        return bool(self.differs)


def compare_signs(text_layer: str | None, mathml: str) -> SignComparison | None:
    """Compare the minus signs, plus signs, plus/minus and relations of the PDF text
    layer with the transcription's MathML.

    None when the text layer shows no sign or relation at all: then it says nothing
    (a lone variable, or a font whose operators came out unnamed). The caller decides
    whether the text layer is trustworthy enough to ask (`equation_step._evaluate` asks
    only when the letters and digits mostly agree)."""
    if not text_layer:
        return None
    layer = layer_signs(text_layer)
    if not layer.shows_signs():
        return None
    got = mathml_signs(mathml)
    # A class on neither side cannot differ: unnamed glyphs only widen intervals.
    classes = {*layer.certain, *layer.uncertain, *got.certain, *got.uncertain} - PRODUCTS
    differs = {cls for cls in classes if _apart(layer, got, cls)}
    if _product_swapped(layer, got):
        differs.add(VECTOR_PRODUCT)
    return SignComparison(tuple(sorted(differs)))
