"""Characters a PDF gives as codes of its font, read as what the font draws.

A font with no table from its codes to characters (a Type 3 bitmap font from TeX, a symbol
font subset, an icon font) comes out of the text layer as control characters, private-use
code points, or plain letters that are not what is printed (Symbol's "h" is eta). `_clean` then drops the control characters without a trace
("effects" becomes "eects", a bullet vanishes and its list becomes one paragraph) and passes the
rest on as they are. This module is the one place a code is mapped. It rewrites, in place, the raw text
dictionary of one page (`layout._text_blocks`) before anything else reads it, so rows, list
markers, maths detection and every output see the mapped character:

- tables keyed on the font's base name (`_TABLES`): the Symbol family, Wingdings, MT-Extra,
  and the APS math font `AdvP4C4E74`, each for the private-use form of a code (Word and
  LibreOffice write Symbol's alpha as U+F061) and for the plain form where one is known. The
  same code is a different glyph in another font (U+F0A7 is a small square in Wingdings and a
  club in Symbol), so a code is never mapped without its font;
- the ligatures, dashes and quotes of TeX's two text encodings, which sit at control codes;
- a spacing accent TeX's old encoding prints over the next letter ("Ren´e"): one letter;
- an unreadable glyph that starts two or more lines of the page and is followed by text:
  a bullet.

Nothing here guesses at a glyph's shape. A code with no row stays as it was, for the text
checks to report. With `FONT_CODE_TABLES` off (`configure(False)`), only the two private-use
bullets the code used to map by code alone are mapped, whatever the font.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from typing import Any

_ENABLED = True


def configure(enabled: bool) -> None:
    """Switch the tables on or off for the process (`Settings.font_code_tables`)."""
    global _ENABLED
    _ENABLED = enabled


# What the code mapped by code point alone before the tables: Word's Symbol bullet and
# Wingdings' small square. Used only when the tables are off.
_CODE_ONLY = {"": "•", "": "▪"}

# TeX text encodings: the codes below 0x20. OT1 (Computer Modern as plain TeX and old LaTeX
# set it) and T1 (the "Cork" encoding, `\usepackage[T1]{fontenc}`).
_OT1 = {
    0x0B: "ff", 0x0C: "fi", 0x0D: "fl", 0x0E: "ffi", 0x0F: "ffl",
    0x10: "ı", 0x11: "ȷ", 0x12: "`", 0x13: "´", 0x14: "ˇ", 0x15: "˘",
    0x16: "¯", 0x17: "˚", 0x18: "¸", 0x19: "ß", 0x1A: "æ",
    0x1B: "œ", 0x1C: "ø", 0x1D: "Æ", 0x1E: "Œ", 0x1F: "Ø",
}  # fmt: skip
_T1 = {
    0x10: "“", 0x11: "”", 0x12: "„", 0x13: "«", 0x14: "»",
    0x15: "–", 0x16: "—", 0x17: "", 0x19: "ı", 0x1A: "ȷ",
    0x1B: "ff", 0x1C: "fi", 0x1D: "fl", 0x1E: "ffi", 0x1F: "ffl",
}  # fmt: skip
_OT1_ONLY = frozenset(range(0x0B, 0x10))

# Adobe's Symbol encoding (code -> character), the part documents use.
_SYMBOL = dict(
    zip(
        range(0x41, 0x5B),
        "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ",
        strict=True,
    )
)
_SYMBOL.update(
    zip(
        range(0x61, 0x7B),
        "αβχδεφγηιϕκλμνοπθρστυϖωξψζ",
        strict=True,
    )
)
_SYMBOL.update(
    {
        0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−",
        0x40: "≅", 0x5C: "∴", 0x5E: "⊥", 0x7E: "∼", 0xA1: "ϒ",
        0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞", 0xA6: "ƒ",
        0xA7: "♣", 0xA8: "♦", 0xA9: "♥", 0xAA: "♠", 0xAB: "↔",
        0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°",
        0xB1: "±", 0xB2: "″", 0xB3: "≥", 0xB4: "×", 0xB5: "∝",
        0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡",
        0xBB: "≈", 0xBC: "…", 0xBF: "↵", 0xC0: "ℵ", 0xC1: "ℑ",
        0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗", 0xC5: "⊕", 0xC6: "∅",
        0xC7: "∩", 0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇", 0xCB: "⊄",
        0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉", 0xD0: "∠",
        0xD1: "∇", 0xD2: "®", 0xD3: "©", 0xD4: "™", 0xD5: "∏",
        0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨",
        0xDB: "⇔", 0xDC: "⇐", 0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓",
        0xE0: "◊", 0xE1: "〈", 0xE5: "∑", 0xF1: "〉", 0xF2: "∫",
    }
)  # fmt: skip
# The letters of the Symbol encoding: in this font a Latin letter code is always a Greek letter.
_SYMBOL_LETTERS = {code: char for code, char in _SYMBOL.items() if chr(code).isalpha()}

# Wingdings, the five codes Word and PowerPoint use for bullets and an arrow. They are read
# in their private-use form (U+F0A7) and as the Latin-1 letters a font with no usable
# ToUnicode gives ("§", "Ø", "ü", "v", "à"): neither is text in this font.
_WINGDINGS = {0xA7: "▪", 0xD8: "➢", 0xFC: "✓", 0x76: "❖", 0xE0: "→"}  # fmt: skip

# APS journals' math font AdvP4C4E74 has no ToUnicode and puts the signs at Latin-1 letters
# ("+" at thorn, "=" at one-quarter, the parentheses at eth and capital thorn).
_APS = {0xFE: "+", 0xBC: "=", 0xF0: "(", 0xDE: ")"}  # fmt: skip


@dataclass(frozen=True)
class _Table:
    """The rows for one family of fonts. `private`: code -> character, read at U+F000 + code;
    `plain`: code -> character, read at the plain code point."""

    name: re.Pattern[str]
    private: dict[int, str] = field(default_factory=dict)
    plain: dict[int, str] = field(default_factory=dict)


_TABLES = (
    # Symbol, SymbolMT, SymbolPS, Standard Symbols: never the TeX symbol fonts (CMSY, MSAM,
    # Latin Modern Symbols), whose codes mean other things.
    _Table(
        re.compile(r"^(?:Standard ?)?Symbol(?:MT|PS|Neu)?(?:[,\-].*)?$", re.I),
        _SYMBOL,
        _SYMBOL_LETTERS,
    ),
    # OpenSymbol keeps Symbol's private-use codes; its other code points are its own.
    _Table(re.compile(r"^OpenSymbol(?:[,\-].*)?$", re.I), _SYMBOL),
    # Wingdings 2 and 3 are other tables: not matched.
    _Table(re.compile(r"^Wingdings(?:[,\-].*)?$", re.I), _WINGDINGS, _WINGDINGS),
    _Table(re.compile(r"^MT[\- ]?Extra(?:[,\-].*)?$", re.I), {0x6C: "ℓ"}),
    _Table(re.compile(r"^AdvP4C4E74$"), plain=_APS),
)
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


@cache
def _table(font: str) -> _Table | None:
    name = _SUBSET_PREFIX.sub("", font)
    return next((t for t in _TABLES if t.name.match(name)), None)


# Spacing accents and the combining mark each one is.
_ACCENTS = {
    "´": "́", "`": "̀", "ˆ": "̂", "^": "̂", "˜": "̃",
    "~": "̃", "¨": "̈", "¯": "̄", "˘": "̆", "˙": "̇",
    "ˇ": "̌", "˚": "̊", "¸": "̧", "˝": "̋",
}  # fmt: skip
_DOTLESS = {"ı": "i", "ȷ": "j"}


def unreadable(char: str) -> bool:
    """A character that says nothing about what is printed: a control code, a private-use
    code point or the replacement character."""
    if char in "\t\n\r\f\v":
        return False
    return char == "�" or unicodedata.category(char) in ("Cc", "Co", "Cn")


def _split(char: dict[str, Any], text: str, horizontal: bool) -> list[dict[str, Any]]:
    """`char` as the characters of `text`: the first keeps the glyph's box and the others
    get a box of no width at its end, as MuPDF gives a ligature it knows."""
    first = dict(char, c=text[0])
    out = [first]
    x0, y0, x1, y1 = char["bbox"]
    end = (x1, y0, x1, y1) if horizontal else (x0, y1, x1, y1)
    for letter in text[1:]:
        rest = dict(char, c=letter, bbox=end)
        if "origin" in char:
            rest["origin"] = (end[0], char["origin"][1]) if horizontal else char["origin"]
        out.append(rest)
    return out


def _compose(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A spacing accent printed over the letter after it becomes that letter with the accent
    (TeX's old encoding has no accented letters: it prints "´" and then "e" under it)."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(chars):
        char = chars[i]
        mark = _ACCENTS.get(char["c"])
        if mark is not None and i + 1 < len(chars):
            base = chars[i + 1]
            letter = str(_DOTLESS.get(base["c"], base["c"]))
            ax0, _, ax1, _ = char["bbox"]
            bx0, _, bx1, _ = base["bbox"]
            centre = (ax0 + ax1) / 2
            over = bx0 - 0.5 <= centre <= bx1 + 0.5 and ax1 - ax0 > 0
            joined = unicodedata.normalize("NFC", letter + mark)
            if over and letter.isalpha() and len(joined) == 1:
                out.append(dict(base, c=joined))
                i += 2
                continue
        out.append(char)
        i += 1
    return out


def _mapped(table: _Table, c: str) -> str | None:
    """The character `table` has for `c`, or None."""
    if len(c) != 1:
        return None
    code = ord(c)
    if 0xF000 <= code <= 0xF0FF:
        return table.private.get(code - 0xF000)
    return table.plain.get(code)


def _remap_code_only(blocks: list[dict[str, Any]]) -> None:
    for block in blocks:
        if block.get("type") == 0:
            for line in block["lines"]:
                for span in line["spans"]:
                    for char in span["chars"]:
                        if char["c"] in _CODE_ONLY:
                            char["c"] = _CODE_ONLY[char["c"]]


def remap(blocks: list[dict[str, Any]]) -> None:
    """Rewrite the characters of one page's raw text blocks in place (module docstring)."""
    if not _ENABLED:
        _remap_code_only(blocks)
        return
    spans: list[tuple[dict[str, Any], dict[str, Any]]] = [
        (line, span)
        for block in blocks
        if block.get("type") == 0
        for line in block["lines"]
        for span in line["spans"]
    ]
    if not any(_table(span["font"]) for _, span in spans):
        seen = {c["c"] for _, span in spans for c in span["chars"]}
        if not any(
            len(c) != 1 or c in _ACCENTS or 0x0B <= ord(c) <= 0x1F or unreadable(c) for c in seen
        ):
            return  # most pages: no font with a table and no character a rule here reads
    ot1 = {
        span["font"]
        for _, span in spans
        if any(len(c["c"]) == 1 and ord(c["c"]) in _OT1_ONLY for c in span["chars"])
    }
    starts: Counter[tuple[str, str]] = Counter()
    inside: Counter[tuple[str, str]] = Counter()
    first_of: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    for line, span in spans:
        chars = span["chars"]
        if not chars:
            continue
        font = span["font"]
        horizontal = abs(line["dir"][0]) >= 0.7
        table = _table(font)
        tex = _OT1 if font in ot1 else _T1
        changed = False
        new: list[dict[str, Any]] = []
        for index, char in enumerate(chars):
            c = char["c"]
            code = ord(c) if len(c) == 1 else -1
            row = _mapped(table, c) if table else None
            if row is not None:
                new.append(dict(char, c=row))
                changed = True
            elif 0x0B <= code <= 0x1F and code in tex and table is None:
                before = chars[index - 1]["c"] if index else " "
                after = chars[index + 1]["c"] if index + 1 < len(chars) else " "
                text = tex[code]
                if len(text) > 1 and not (before.isalpha() or after.isalpha()):
                    new.append(char)  # a ligature stands inside a word; anything else is left
                    continue
                changed = True
                if text:
                    new.extend(_split(char, text, horizontal))
            else:
                new.append(char)
        if any(c["c"] in _ACCENTS for c in new):
            composed = _compose(new)
            changed = changed or len(composed) != len(new)
            new = composed
        if changed:
            span["chars"] = new
    # an unreadable glyph that starts a line and is followed by text
    for line, _span in spans:
        if id(line) in first_of:
            continue
        flat = [(s, c) for s in line["spans"] for c in s["chars"]]
        shown = [(s, c) for s, c in flat if not c["c"].isspace()]
        if len(shown) < 3:
            continue
        span0, char0 = shown[0]
        for s, c in shown[1:]:
            if unreadable(c["c"]):
                inside[(s["font"], c["c"])] += 1  # a bullet stands nowhere but at a line start
        if (
            unreadable(char0["c"])
            and shown[1][1]["c"].isalnum()
            and not unreadable(shown[2][1]["c"])
            # set off from the text by a gap: a sign or a piece of a bracket touches it
            and shown[1][1]["bbox"][0] - char0["bbox"][2] >= 0.2 * span0["size"]
        ):
            first_of[id(line)] = (span0, char0)
            starts[(span0["font"], char0["c"])] += 1
    for span0, char0 in first_of.values():
        key = (span0["font"], char0["c"])
        if starts[key] >= 2 and not inside[key]:
            char0["c"] = "•"
