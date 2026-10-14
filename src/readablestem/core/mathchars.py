"""Unicode mathematical alphanumerics: one place for "bold d" <-> U+1D41D.

PDF text layers, Chromium-printed MathML and latex2mathml all express letter style
(bold vectors, double-struck sets, script letters) as code points in the U+1D400 block
rather than as font attributes. The layout step writes bold letters of an equation's text
layer the same way, so the equation step can compare letter *styles* between the PDF and
the model's transcription, not just the letters (a bold vector read as a plain scalar is
the most common transcription error on the OpenStax gold set).
"""

from __future__ import annotations

import unicodedata
from typing import Any

# MathML mathvariant -> the words Unicode uses after "MATHEMATICAL ".
VARIANT_NAMES = {
    "bold": "BOLD",
    "italic": "ITALIC",
    "bold-italic": "BOLD ITALIC",
    "double-struck": "DOUBLE-STRUCK",
    "script": "SCRIPT",
    "bold-script": "BOLD SCRIPT",
    "fraktur": "FRAKTUR",
    "bold-fraktur": "BOLD FRAKTUR",
    "sans-serif": "SANS-SERIF",
    "bold-sans-serif": "SANS-SERIF BOLD",
    "sans-serif-italic": "SANS-SERIF ITALIC",
    "sans-serif-bold-italic": "SANS-SERIF BOLD ITALIC",
    "monospace": "MONOSPACE",
}
# Longest names first, so "BOLD ITALIC" is not read as "BOLD".
_NAME_TO_VARIANT = sorted(
    ((name, variant) for variant, name in VARIANT_NAMES.items()), key=lambda p: -len(p[0])
)
_DIGITS = ["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"]
_PLAIN_PREFIXES = {
    "LATIN CAPITAL LETTER ": "CAPITAL ",
    "LATIN SMALL LETTER ": "SMALL ",
    "GREEK CAPITAL LETTER ": "CAPITAL ",
    "GREEK SMALL LETTER ": "SMALL ",
}

# Style groups for comparing a text layer with a transcription. Italic is the default
# style of single-letter identifiers, so it counts as plain; every bold flavour is "bold".
STYLE_GROUPS = {
    "bold": "bold",
    "bold-italic": "bold",
    "bold-sans-serif": "bold",
    "sans-serif-bold-italic": "bold",
    "bold-script": "bold",
    "bold-fraktur": "bold",
    "double-struck": "double-struck",
    "script": "script",
    "fraktur": "fraktur",
    "sans-serif": "sans-serif",
    "sans-serif-italic": "sans-serif",
    "monospace": "monospace",
}


# The "holes" in the U+1D400 block: these styled letters were encoded earlier, in
# Letterlike Symbols, and the block leaves their slots empty.
_HOLES: dict[tuple[str, str], str] = {
    ("italic", "h"): "ℎ",  # PLANCK CONSTANT
    **{("script", c): u for c, u in zip("BEFHILMR", "ℬℰℱℋℐℒℳℛ", strict=True)},
    **{("script", c): u for c, u in zip("ego", "ℯℊℴ", strict=True)},
    **{("fraktur", c): u for c, u in zip("CHIRZ", "ℭℌℑℜℨ", strict=True)},
    **{("double-struck", c): u for c, u in zip("CHNPQRZ", "ℂℍℕℙℚℝℤ", strict=True)},
}  # fmt: skip
_HOLE_VARIANT = {u: (variant, c) for (variant, c), u in _HOLES.items()}


def styled_char(ch: str, variant: str) -> str:
    """`ch` in the given mathvariant, or `ch` unchanged when Unicode has no such letter
    (e.g. italic small h is U+210E, outside the block) or `ch` is not a Latin/Greek
    letter or ASCII digit."""
    prefix = VARIANT_NAMES.get(variant)
    if prefix is None:
        return ch
    if ch.isascii() and ch.isdigit():
        name = f"MATHEMATICAL {prefix} DIGIT {_DIGITS[int(ch)]}"
    else:
        base = unicodedata.name(ch, "")
        rest = next(
            (short + base.removeprefix(long) for long, short in _PLAIN_PREFIXES.items()
             if base.startswith(long)),
            None,
        )  # fmt: skip
        if rest is None:
            return ch
        name = f"MATHEMATICAL {prefix} {rest}"
    try:
        return unicodedata.lookup(name)
    except KeyError:
        return _HOLES.get((variant, ch), ch)


def char_variant(ch: str) -> str | None:
    """The mathvariant a styled code point carries (U+1D41D -> "bold"), else None."""
    if ch in _HOLE_VARIANT:
        return _HOLE_VARIANT[ch][0]
    name = unicodedata.name(ch, "")
    if not name.startswith("MATHEMATICAL "):
        return None
    rest = name.removeprefix("MATHEMATICAL ")
    for words, variant in _NAME_TO_VARIANT:
        if rest.startswith(words + " "):
            return variant
    return None


def base_char(ch: str) -> str:
    """U+1D41D -> "d"; anything else unchanged (NFKC of a single character)."""
    if ch in _HOLE_VARIANT:
        return _HOLE_VARIANT[ch][1]
    return unicodedata.normalize("NFKC", ch) if char_variant(ch) else ch


# Weights browsers ignore on a MathML token. MathML Core (Chromium, and so most readers'
# browsers) draws only mathvariant="normal"; Firefox and Safari honour more. A bold
# \boldsymbol{\omega} that stays `<mi mathvariant="bold-italic">` is drawn as a plain
# italic omega there. Written as the Unicode bold code point, every maths font draws it
# bold and every screen reader says "bold omega". Latin letters already come out that way
# (\mathbf{d} is U+1D41D); Greek ones, which \mathbf and \boldsymbol turn into an
# attribute, are the gap this closes.
BOLD_VARIANTS = frozenset({"bold", "bold-italic"})
_TOKENS = frozenset({"mi", "mn", "mo"})


def _local_name(tag: object) -> str:
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def bake_bold_variants(root: Any, inherited: str | None = None) -> None:
    """Rewrite bold and bold-italic tokens of a parsed MathML tree in place as Unicode
    mathematical bold letters, and take the `mathvariant` off when it has done its job.

    The attribute is inherited, as MathML says (`<mstyle mathvariant="bold">` over
    `<mi>`s): it is read from the containers and applied to the tokens below, and a
    container's own bold attribute is removed. A token with its own attribute and no bold
    code point to show it (an operator, a letter Unicode has no bold form of) keeps the
    attribute, so a browser that honours it still draws it bold. Other variants are left
    alone."""
    own = root.attrib.get("mathvariant")
    variant = own if own in BOLD_VARIANTS else inherited
    if own is not None and own not in BOLD_VARIANTS and own != inherited:
        variant = None  # another variant overrides what was inherited
    if _local_name(root.tag) in _TOKENS:
        if variant and root.text:
            baked = "".join(styled_char(ch, variant) for ch in root.text)
            if baked != root.text:
                root.text = baked
                root.attrib.pop("mathvariant", None)
        return
    if own in BOLD_VARIANTS:
        del root.attrib["mathvariant"]
    for child in root:
        bake_bold_variants(child, variant)
