"""A glyph the text layer cannot decode, read by the name the file gives it.

MuPDF turns a code into a character through the font's table to Unicode, or through the
glyph's name when Adobe's list has it. TeX's extension fonts (CMEX10, CMEX9, txex,
LMMathExtension10) have neither for their summation and integral signs, radicals, big
brackets and the pieces of built-up ones, so the text layer gives U+FFFD or one of Adobe's
private-use codes. The file still names each glyph, inside the embedded font program, and
`glyph-names.tsv` (data/README.md) says which character each name is.

`recover` is the second step of the ladder `layout._text_blocks` runs, for the glyphs the
file's own table left undecoded (the first step is MuPDF's); `font_codes.remap` is the third
(a table by font name, for fonts whose names are absent or wrong); `mark_gaps` is the last:
a glyph still undecoded is passed on as `output_chars.GAP`, a glyph the file does not name,
not as U+FFFD and not dropped. A character recovered here has `"how": "name"` in its raw
dictionary, one the font table gave has `"how": "font"`, a gap `"how": "gap"`; a character
the file's own table gave has no key.

How a character becomes a glyph: the raw character is not a code, and a private-use
character keeps its Unicode, so nothing is read back from it. `page.get_texttrace()` lists
the glyph ids MuPDF drew, with each origin, and the two lists agree on font and origin. The
glyph id is turned into a name by the font program itself (`fz_get_glyph_name`, through
PyMuPDF), so no font parser is added. The trace is read only for a page that holds an
undecoded glyph, and a font's names are read once for the page.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from functools import cache
from importlib import resources
from typing import Any

import pymupdf

from readablestem.pipeline.output_chars import GAP

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
_UNI = re.compile(r"^uni([0-9A-F]{4})$")
# TeX's \l and \L print the glyph `suppress` (a stroke) over a plain l or L at the same origin
# and no advance of its own. The stroke reads with the letter it crosses, as one character;
# over any other letter it says nothing and stays a gap.
_STROKE = "suppress"
_STROKED = {"l": "ł", "L": "Ł"}


@cache
def _table() -> dict[str, str]:
    text = resources.files("readablestem.pipeline").joinpath("data/glyph-names.tsv").read_text()
    return {
        name: chr(int(code, 16))
        for name, code in (
            row.split("\t") for row in text.split("\n") if row and not row.startswith("#")
        )
    }


def character(name: str) -> str | None:
    """The character a glyph name stands for, or None. A sized variant (`summationdisplay`,
    `parenleftBigg`) is its base character; a built-up piece is the Unicode piece character;
    a suffix after a dot (`a.sc`) is a variant of the name before it."""
    base = name.split(".", 1)[0]
    found = _table().get(base)
    if found is None and (match := _UNI.match(base)):
        found = chr(int(match.group(1), 16))
    if found is None or _undecoded(found):
        return None
    return found


def _undecoded(char: str) -> bool:
    """U+FFFD or a private-use character: what MuPDF gives for a glyph it cannot decode."""
    return char == "\ufffd" or unicodedata.category(char) == "Co"


def _missing(blocks: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (span, char)
        for block in blocks
        if block.get("type") == 0
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
        if _undecoded(char["c"])
    ]


def _base(font: str) -> str:
    return _SUBSET_PREFIX.sub("", font)


def _glyph_names(doc: pymupdf.Document, xref: int) -> Callable[[int], str] | None:
    """The glyph id -> name lookup of an embedded font program, or None for a font with
    no program (a standard font, a Type 3 font) or one the loader cannot read."""
    try:
        buffer = doc.extract_font(xref)[3]
        font = pymupdf.Font(fontbuffer=buffer) if buffer else None
    except (RuntimeError, ValueError, pymupdf.mupdf.FzErrorBase):  # "object is not a stream"
        return None
    if font is None:
        return None

    def name(gid: int) -> str:
        try:
            return pymupdf.mupdf.fz_get_glyph_name2(font.this, gid)
        except pymupdf.mupdf.FzErrorBase:
            return ""

    return name


def _stroked(span: dict[str, Any], stroke: dict[str, Any]) -> str | None:
    """The letter with a stroke that `stroke` and the l or L beside it in the span make; the
    letter is taken out of the span, the stroke's character becomes the pair. None when no
    l or L is printed at the stroke's origin."""
    chars = span["chars"]
    at = next(i for i, char in enumerate(chars) if char is stroke)
    x, y = stroke["origin"]
    for near in (at + 1, at - 1):
        if 0 <= near < len(chars) and chars[near]["c"] in _STROKED:
            other_x, other_y = chars[near]["origin"]
            if abs(other_y - y) < 0.1 * span["size"] and abs(other_x - x) < 0.1 * span["size"]:
                return _STROKED[chars.pop(near)["c"]]
    return None


def recover(page: pymupdf.Page, blocks: list[dict[str, Any]]) -> None:
    """Step 2 of the ladder: give each undecoded glyph of `blocks` (the page's raw text, in
    place) the character its glyph name stands for."""
    missing = _missing(blocks)
    doc = page.parent
    if not missing or doc is None:
        return
    # A glyph is found by font and origin, and by the character the text layer gave it: TeX
    # draws an overlay (the slash of \l) at the origin of the letter it crosses.
    glyphs: dict[tuple[str, float, float], list[tuple[int, int]]] = {}
    for traced in page.get_texttrace():
        font = _base(traced["font"])
        for ucs, gid, (x, y), _box in traced["chars"]:
            glyphs.setdefault((font, round(x, 2), round(y, 2)), []).append((ucs, gid))
    page_fonts = [(_base(base), xref) for xref, *_rest, base, _name, _encoding in page.get_fonts()]
    lookups: dict[str, list[Callable[[int], str]]] = {}
    for span, char in missing:
        font = _base(span["font"])
        x, y = char["origin"]
        if font not in lookups:
            # MuPDF cuts a font's name short in the text layer ("LMMathExtension10-Regula")
            fonts = (_glyph_names(doc, xref) for base, xref in page_fonts if base.startswith(font))
            lookups[font] = [lookup for lookup in fonts if lookup]
        found = {
            lookup(gid)
            for ucs, gid in glyphs.get((font, round(x, 2), round(y, 2)), ())
            if ucs == ord(char["c"])
            for lookup in lookups[font]
        }
        name = found.pop() if len(found) == 1 else None  # two names: not told apart
        text = _stroked(span, char) if name == _STROKE else name and character(name)
        if text:
            char["c"], char["how"] = text, "name"


def mark_gaps(blocks: list[dict[str, Any]]) -> None:
    """The last step: a glyph still undecoded becomes `output_chars.GAP`."""
    for _span, char in _missing(blocks):
        char["c"], char["how"] = GAP, "gap"
