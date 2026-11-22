"""No output holds a character that is not text.

A private-use character (U+E000 to U+F8FF, planes 15 and 16), the replacement character
U+FFFD, a noncharacter, a lone surrogate or a control other than tab and line end is not
text: a screen reader says nothing at it, or "question mark". Such characters come from
symbol and dingbat fonts whose glyphs name no letter, from damaged text layers, and from
the tool's own stand-ins (`script_marks`, `math_canonical`) if a bug ever left one behind.
Measured on 61 files: 133 private-use characters on 38 pages and 170 U+FFFD on 26 pages
reached the student file, none flagged.

The rule applies to every file a student gets, where students read:

- the student page (`assemble.render_html`) never prints one: in text it writes a marked
  gap, `UNREADABLE` in the "not converted" style, which every output made from the page
  carries and Listen speaks; in an attribute (`alt`, `aria-label`, `alttext`, `title`) and
  in maths it writes the same words as plain text;
- the static checks (`html_checks.static_checks`) fail on a page that holds one or a gap,
  so the job page says so, at every render;
- each export runs the rule on its own file after it is built (`count_in_zip`, `count`),
  and a hit there, which cannot have come from the page, stops that file from being
  offered as finished.

The text layer's gap mark `GAP` (a glyph the file does not name, `glyph_names.mark_gaps`) is
one of them, so a glyph the conversion could not read comes out as `UNREADABLE` and fails the
static checks as U+FFFD does; it is not private use. The text-health check, the glyph names
and the font code tables that remove most of these characters at the source are the
conversion's; this module only keeps them out of the outputs.
Content-free: callers learn a count, never the text around a character.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator

# What students meet in place of a character that is not text (a student-facing string).
UNREADABLE = "[unreadable character]"
# The text layer's mark for a glyph the file does not name (`glyph_names`): not U+FFFD, which is
# what MuPDF gives for any failure, and not private use, which is a character of someone's
# font. Rows, maths detection and the checks see one glyph that is known to be unread. Like
# U+FFFD it never reaches an output: `NOT_TEXT` has it, so the page writes `UNREADABLE`.
GAP = "⯑"

# The pieces of built-up brackets, braces, parentheses, integrals and radicals, in the two forms
# a text layer holds them: Adobe's private-use code points (glyph list names radicalex to
# bracerightbt, U+F8E5-U+F8FE) that Symbol-style fonts and pdfTeX write them at, and the Unicode
# piece characters (U+239B-U+23B3) that `glyph_names` reads from the font. Layout reads the
# glyph's name first (`glyph_names.recover`), so the private-use form reaches a reader only
# when the name is missing; every reader that asks "is this a piece" asks here.
ADOBE_PIECES = frozenset(map(chr, range(0xF8E5, 0xF8FF)))
BRACKET_PIECES = frozenset(map(chr, range(0x239B, 0x23B4))) | {
    char for char in ADOBE_PIECES if char >= "\uf8eb"
}

_RANGES = [
    "\x00-\x08",  # C0 controls (tab, line feed and carriage return are text)
    "\x0b\x0c",
    "\x0e-\x1f",
    "\x7f-\x9f",  # DEL and the C1 controls
    # Lone surrogates, escaped for `re` (raw string): a str literal holding them cannot be
    # encoded as UTF-8 (tests/test_source_portability.py).
    r"\ud800-\udfff",
    "\ue000-\uf8ff",  # private use
    "\ufdd0-\ufdef",  # noncharacters
    "\ufffd\ufffe\uffff",  # the replacement character, noncharacters
    GAP,  # the text layer's mark for a glyph the file does not name
    *(f"{chr(plane << 16 | 0xFFFE)}{chr(plane << 16 | 0xFFFF)}" for plane in range(1, 15)),
    "\U000f0000-\U0010ffff",  # planes 15 and 16: private use and their noncharacters
]
NOT_TEXT = re.compile("[" + "".join(_RANGES) + "]")
# The same characters written as character references (`&#xE000;`, `&#65533;`).
_REFERENCE = re.compile(r"&#(?:[xX]([0-9a-fA-F]{1,8})|([0-9]{1,10}));")
# Members of an export's zip that hold text a reader meets.
_TEXT_MEMBERS = (".xml", ".rels", ".html", ".xhtml", ".htm", ".opf", ".ncx", ".txt", ".css")


def _code(match: re.Match[str]) -> int:
    hexa, decimal = match.groups()
    return int(hexa, 16) if hexa is not None else int(decimal)


def _is_not_text_code(code: int) -> bool:
    return code > 0x10FFFF or bool(NOT_TEXT.fullmatch(chr(code)))


def count(text: str) -> int:
    """How many characters in `text` are not text, written out or as a reference."""
    raw = len(NOT_TEXT.findall(text))
    if "&#" not in text:
        return raw
    return raw + sum(_is_not_text_code(_code(m)) for m in _REFERENCE.finditer(text))


def plain(text: str) -> str:
    """`text` with each character that is not text replaced by `UNREADABLE` (for titles,
    file metadata and attribute values, where no markup can go)."""
    return NOT_TEXT.sub(UNREADABLE, text)


def _replace(text: str, gap: str) -> str:
    text = NOT_TEXT.sub(lambda _: gap, text)
    if "&#" not in text:
        return text
    return _REFERENCE.sub(lambda m: gap if _is_not_text_code(_code(m)) else m.group(0), text)


# A tag or a declaration; everything between two of them (and two comments) is text.
_TAG = re.compile(r"<[^<>]*>")
# Where a gap is written as words, not as a marked span: no element may go inside these.
_PLAIN_INSIDE = ("math", "title", "style", "textarea")


def mark_html(html: str, gap: str) -> str:
    """A finished page with no character that is not text: in text, each is replaced by
    `gap` (markup for the marked gap); in a tag's attributes, in maths, the title and
    the stylesheet, by `UNREADABLE` as plain text. The page is returned as it is when it
    holds none, so a clean page is written byte for byte as before."""
    if not count(html):
        return html
    out: list[str] = []
    plain_depth = {name: 0 for name in _PLAIN_INSIDE}
    position = 0
    for start, end in _tokens(html):
        out.append(_text_part(html[position:start], plain_depth, gap))
        tag = html[start:end]
        if not tag.startswith("<!--"):
            name = re.match(r"</?\s*([A-Za-z][A-Za-z0-9:-]*)", tag)
            if name is not None and (key := name.group(1).lower()) in plain_depth:
                if tag.startswith("</"):
                    plain_depth[key] = max(0, plain_depth[key] - 1)
                elif not tag.endswith("/>"):
                    plain_depth[key] += 1
            tag = _replace(tag, UNREADABLE)
        out.append(tag)
        position = end
    out.append(_text_part(html[position:], plain_depth, gap))
    return "".join(out)


def _tokens(html: str) -> Iterator[tuple[int, int]]:
    """Where each comment, tag and declaration starts and ends, in order. A comment's end
    is searched for from where the comment opens, and never again once none is left, so a
    page of unclosed comment openers costs one pass and not one pass for each."""
    position = 0
    closes_left = True
    while (start := html.find("<", position)) != -1:
        end = -1
        if closes_left and html.startswith("<!--", start):
            close = html.find("-->", start + 4)
            closes_left = close != -1
            end = close + 3 if closes_left else -1
        if end == -1:
            tag = _TAG.match(html, start)
            end = tag.end() if tag is not None else -1
        if end == -1:
            position = start + 1
        else:
            yield start, end
            position = end


def _text_part(text: str, plain_depth: dict[str, int], gap: str) -> str:
    if not text or not count(text):
        return text
    if any(plain_depth.values()):
        return _replace(text, UNREADABLE)
    return _replace(text, gap)


def count_in_zip(data: bytes, *, skip: frozenset[str] = frozenset()) -> int:
    """Characters that are not text in the text members of a built zip (a Word file, an
    EPUB, a Canvas or Cartridge package): the rule run on the export's own file. `skip`
    names members that hold no document text (a format's own style parts)."""
    found = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.filename in skip or not info.filename.lower().endswith(_TEXT_MEMBERS):
                continue
            try:
                text = archive.read(info).decode("utf-8")
            except UnicodeDecodeError:
                found += 1  # text a reader cannot decode is not text either
                continue
            found += count(text)
    return found
