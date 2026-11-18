"""How `assemble` writes running text: inline equations, links, colour and language.

Two seams, so the text features land here and not in `assemble.py`
(what the output looks like: docs/output.md "Links and
cross-references", "Language", "Coloured text"):

- `render_inline_text`: the one place inline text becomes HTML (paragraphs, list items,
  headings, captions). It escapes the text, puts each inline equation at its marker,
  and writes the element's spans (`core.inline`): links (`<a href>`), colour runs
  (`<span class>`) and struck text (`<del>`).
  Offsets are into `Element.text` as stored, markers included. A span that starts or
  ends inside a marker, reaches past the text or overlaps an earlier span of its own
  kind is skipped; a web link is written only while `core.types.is_safe_href` still
  holds, and a link inside the document only to a well-formed id. Links are outermost
  and never wrap an inline equation (a wide equation is a tab stop of its own, which
  cannot sit inside a link): a link across one is written in pieces around it. Colour
  spans sit inside links and may wrap maths, which takes the colour too. Raised and
  lowered runs (`Element.script_runs`: Na+, H2O, cm3) are written as `<sup>` and `<sub>`,
  innermost; a lowered and a raised run side by side (SO4 2-, the two numbers before a
  nuclide's symbol) are written as one stacked pair (`student_sheet.SCRIPT_STACK`). Struck text
  (`Element.runs`, kind "deleted": a cancelled date) sits inside links and outside colour
  and `<sup>`/`<sub>` (a colour span cut in two is not heard; a struck run cut in two would
  be said twice), and is written as `<del>` with the tool's own words "deleted:" before
  it and "end deleted" after it, visually hidden, so a listener hears that the words are
  struck whether or not the screen reader announces `<del>` (most do not by default), and
  a format that keeps text but not the tag (Word keeps both) still says so.
- `passage_attrs`: ` lang="..."` on the element's own block (`<p>`, `<li>`, `<h2>`...)
  when `Element.lang` is set.

Colour: each printed colour gets one class, `tc-rrggbb` after the colour as printed, and
one CSS rule (`add_style`) with the colour made dark enough for 4.5:1 contrast on the
output's white background, its hue kept (`pipeline.contrast`), and a second rule inside
the dark-scheme `@media` block with the printed colour lightened as far as the dark page
needs (no colour reaches 4.5:1 on both pages). Text without links or colour runs is written
exactly as before these features existed, and so is text without struck runs: the `<del>`
rule (`student_sheet.DELETED`) is added to the stylesheet only for a document that has one.

Rendering reads the stored element only (no options), so the review screen's re-render
of a stored result writes the same file.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from readablestem.core import inline
from readablestem.core.types import (
    INLINE_MARKER_RE,
    ColourRun,
    Element,
    Link,
    Run,
    ScriptRun,
    is_safe_href,
)
from readablestem.pipeline import contrast, student_sheet
from readablestem.pipeline.language import is_language_tag
from readablestem.pipeline.safe_html import escape_text
from readablestem.pipeline.table_html import AUTHORED_CLASS

# HTML for the inline equation with this element id, or None when the id is not an
# equation of this document (the marker then stays as text: a layout bug made visible).
EquationHtml = Callable[[str], str | None]
# Adds CSS to the document's stylesheet, once per name (`_Renderer.add_style`).
AddStyle = Callable[[str, str], None]
LinkAttrs = Callable[[Link], str]

COLOUR_CLASS_PREFIX = "tc-"
# A lowered and a raised run side by side, written one above the other as they are
# printed (the count and the charge of SO4 2-, the mass number and the atomic number of
# a nuclide). The two stay in the order of the text, which is the order a screen reader
# says them in (4, then 2-; 14, then 6); the raised one is drawn on top either way, by the
# direction of the column. Added to the stylesheet only for a document that has one.
STACK_CLASS = "script-stack"
STACK_LOW_FIRST = "script-stack-low-first"  # the lowered run comes first in the text
# Struck text: the words said around it (the tool's own words, so they carry its class and
# are marked English like every other word of the tool's), and its look. The line through is
# a text decoration, which forced colors keep, in the text's own colour, so it shows on a
# dark page too; it is drawn thicker than a browser's default so it is not lost at small
# sizes. Added to the stylesheet only for a document that has struck text.
DELETED_BEFORE = "deleted: "
DELETED_AFTER = " end deleted"
DELETED_CLASS = "deleted-run"
# Blanks and printed checkboxes (`Element.runs`, kinds "blank" and "checkbox"; found by
# `text_pass.token_runs`). The characters the page printed are kept, hidden from a screen
# reader (`aria-hidden`), and the tool's own words stand in for them, visually hidden, so a
# blank is heard as "blank" and a box as "Checkbox, not checked:" (SW36). A format that keeps
# text but not the tags (Word) shows both, which still says what the line is.
TOKEN_KINDS = ("blank", "checkbox")
BLANK_WORDS = "blank"
FILLED_WORDS = "blank, filled in: "  # a form field that has a value (SW39)
CHECKBOX_WORDS = {"not checked": "Checkbox, not checked: ", "checked": "Checkbox, checked: "}
CHECKED_BOXES = frozenset("\u2611\u2612")  # ballot box with check, ballot box with X
# An element id a link may point at ("p3-e12"), as the assembler writes ids.
LINK_TARGET = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")


def colour_class(rgb: tuple[int, int, int]) -> str:
    """The class of text printed in this colour: "tc-" and the colour's hex digits."""
    return COLOUR_CLASS_PREFIX + contrast.hex_colour(rgb)[1:]


def _href(link: Link) -> str | None:
    if link.kind == "uri":
        return link.href if link.href and is_safe_href(link.href) else None
    if link.target_id and LINK_TARGET.fullmatch(link.target_id):
        return f"#{link.target_id}"
    return None


def _usable[S: (Link, ColourRun, ScriptRun, Run)](
    spans: Sequence[S], text: str, markers: Sequence[tuple[int, int]]
) -> list[S]:
    """Spans inside the text, not starting or ending inside a marker, without overlaps
    (the earliest wins)."""

    def inside(offset: int) -> bool:
        return any(start < offset < end for start, end in markers)

    out: list[S] = []
    for span in sorted(spans, key=lambda s: (s.start, s.end)):
        if span.end > len(text) or inside(span.start) or inside(span.end):
            continue
        if out and span.start < out[-1].end:
            continue
        out.append(span)
    return out


@dataclass(frozen=True)
class _Segment:
    start: int
    end: int
    maths: str | None  # the equation's HTML when this segment is a placed marker
    link: int | None  # index into the usable links
    colour: tuple[int, int, int] | None
    script: str | None = None  # "sup" or "sub" when this piece is a raised or lowered run
    deleted: bool = False  # inside struck text
    token: int | None = None  # index of the blank or checkbox run this piece is


def render_inline_text(
    text: str,
    *,
    element: Element | None,
    equation: EquationHtml,
    add_style: AddStyle | None = None,
    link_attrs: LinkAttrs | None = None,
) -> str:
    """`text` as HTML: escaped, with inline-equation markers replaced by `equation(id)`,
    and the element's links and colour runs written (see the module docstring).

    `element` is the element whose stored `text` this is, or None when the text is
    something else (a caption, a heading a reviewer retyped): only then do the offsets
    of its links and colour runs apply. `add_style` adds the CSS colour runs need; with
    none, colour runs are not written. `link_attrs` gives the extra attributes (with a
    leading space, already escaped) of the `<a>` written for a link, e.g. a footnote mark's
    role; it is called once per link that is written.
    """
    markers = [(m.start(), m.end()) for m in INLINE_MARKER_RE.finditer(text)]
    placed: dict[int, tuple[int, str]] = {}
    for match in INLINE_MARKER_RE.finditer(text):
        maths = equation(match.group(1))
        if maths is not None:  # not one of ours: leave the characters as text
            placed[match.start()] = (match.end(), maths)
    links: list[tuple[Link, str]] = []
    runs: list[ColourRun] = []
    scripts: list[ScriptRun] = []
    struck: list[Run] = []
    tokens: list[Run] = []
    if element is not None:
        found = inline.spans(element)
        scripts = _usable(found.scripts, text, markers)
        for link in _usable(found.links, text, markers):
            href = _href(link)
            if href is not None:
                links.append((link, href))
        if add_style is not None:
            runs = _usable(found.colours, text, markers)
        struck = _usable([r for r in found.runs if r.kind == "deleted"], text, markers)
        tokens = _usable([r for r in found.runs if r.kind in TOKEN_KINDS], text, markers)
    if not links and not runs and not scripts and not struck and not tokens:
        return _plain(text, placed)
    segments = _segments(text, placed, [k for k, _ in links], runs, scripts, struck, tokens)
    for rgb in sorted({s.colour for s in segments if s.colour is not None}):
        if add_style is not None:
            add_style(f"colour-{colour_class(rgb)}", student_sheet.colour(colour_class(rgb), rgb))
    if add_style is not None and _stacked(segments):
        add_style(STACK_CLASS, student_sheet.SCRIPT_STACK)
    if add_style is not None and struck:
        add_style(DELETED_CLASS, student_sheet.DELETED)
    return _write(
        text, segments, [href for _, href in links], [k for k, _ in links], link_attrs, tokens
    )


def _plain(text: str, placed: dict[int, tuple[int, str]]) -> str:
    """Escaped text with the placed equations (the output before links and colour)."""
    out, pos = [], 0
    for start in sorted(placed):
        end, maths = placed[start]
        out.append(escape_text(text[pos:start]))
        out.append(maths)
        pos = end
    out.append(escape_text(text[pos:]))
    return "".join(out)


def _segments(
    text: str,
    placed: dict[int, tuple[int, str]],
    links: list[Link],
    runs: list[ColourRun],
    scripts: Sequence[ScriptRun] = (),
    struck: Sequence[Run] = (),
    tokens: Sequence[Run] = (),
) -> list[_Segment]:
    """The text cut at every marker, link and run edge, each piece with what covers it."""
    cuts = {0, len(text)}
    for start, (end, _) in placed.items():
        cuts |= {start, end}
    for span in (*links, *runs, *scripts, *struck, *tokens):
        cuts |= {span.start, span.end}
    edges = sorted(cuts)
    segments = []
    for start, end in pairwise(edges):
        maths = placed[start][1] if start in placed and placed[start][0] == end else None
        link = next(
            (i for i, k in enumerate(links) if k.start <= start and end <= k.end and maths is None),
            None,
        )
        colour = next((r.rgb for r in runs if r.start <= start and end <= r.end), None)
        script = next(
            (r.kind for r in scripts if r.start <= start and end <= r.end and maths is None), None
        )
        deleted = any(r.start <= start and end <= r.end for r in struck)
        token = next(
            (
                i
                for i, r in enumerate(tokens)
                if r.start <= start and end <= r.end and maths is None
            ),
            None,
        )
        segments.append(_Segment(start, end, maths, link, colour, script, deleted, token))
    return segments


def _stacked(segments: Sequence[_Segment]) -> bool:
    """Is there a lowered and a raised run side by side?"""
    return any(a.script and b.script and a.script != b.script for a, b in pairwise(segments))


def _token(kind: str, shown: str) -> str:
    """A blank or a checkbox as the page printed it, with the words that say it."""
    if kind == "blank" and shown.strip("_"):  # a filled field: its value is what is said
        return f'<span class="blank">{_hidden(FILLED_WORDS)}{escape_text(shown)}</span>'
    if kind == "blank":
        return (
            f'<span class="blank">{_hidden(BLANK_WORDS)}'
            f'<span aria-hidden="true">{escape_text(shown)}</span></span>'
        )
    state = "checked" if shown in CHECKED_BOXES else "not checked"
    return (
        f'<span class="checkbox">{_hidden(CHECKBOX_WORDS[state])}'
        f'<span aria-hidden="true">{escape_text(shown)}</span></span>'
    )


def _inner(text: str, segments: Sequence[_Segment], tokens: Sequence[Run] = ()) -> str:
    """The pieces as HTML: maths as written, raised and lowered runs in `<sup>` and `<sub>`
    (a pair side by side in one stacked span), blanks and checkboxes with their words, the
    rest escaped."""
    out: list[str] = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg.token is not None:
            j = i
            while j + 1 < len(segments) and segments[j + 1].token == seg.token:
                j += 1  # one run cut by a link or colour edge is written once
            run = tokens[seg.token]
            out.append(_token(run.kind, text[segments[i].start : segments[j].end]))
            i = j
        elif seg.maths:
            out.append(seg.maths)
        elif seg.script:
            tag = seg.script
            following = segments[i + 1] if i + 1 < len(segments) else None
            this = f"<{tag}>{escape_text(text[seg.start : seg.end])}</{tag}>"
            if following is not None and following.script and following.script != tag:
                other = following.script
                that = f"<{other}>{escape_text(text[following.start : following.end])}</{other}>"
                # Text order for the reader; the raised run is drawn on top (`SCRIPT_STACK`).
                css = STACK_CLASS if tag == "sup" else f"{STACK_CLASS} {STACK_LOW_FIRST}"
                out.append(f'<span class="{css}">{this}{that}</span>')
                i += 1
            else:
                out.append(this)
        else:
            out.append(escape_text(text[seg.start : seg.end]))
        i += 1
    return "".join(out)


def _hidden(words: str) -> str:
    return f'<span class="visually-hidden {AUTHORED_CLASS}">{words}</span>'


def _struck(text: str, segments: Sequence[_Segment], tokens: Sequence[Run] = ()) -> str:
    """The pieces with each stretch of struck text as `<del>`, said as struck (`DELETED_*`)."""
    out: list[str] = []
    i = 0
    while i < len(segments):
        j = i
        while j < len(segments) and segments[j].deleted == segments[i].deleted:
            j += 1
        inner = _coloured(text, segments[i:j], tokens)
        if segments[i].deleted:
            inner = f"{_hidden(DELETED_BEFORE)}<del>{inner}</del>{_hidden(DELETED_AFTER)}"
        out.append(inner)
        i = j
    return "".join(out)


def _coloured(text: str, segments: Sequence[_Segment], tokens: Sequence[Run] = ()) -> str:
    out: list[str] = []
    i = 0
    while i < len(segments):
        j = i
        while j < len(segments) and segments[j].colour == segments[i].colour:
            j += 1
        inner = _inner(text, segments[i:j], tokens)
        rgb = segments[i].colour
        out.append(f'<span class="{colour_class(rgb)}">{inner}</span>' if rgb else inner)
        i = j
    return "".join(out)


def _write(
    text: str,
    segments: list[_Segment],
    hrefs: list[str],
    links: Sequence[Link] = (),
    link_attrs: LinkAttrs | None = None,
    tokens: Sequence[Run] = (),
) -> str:
    out: list[str] = []
    i = 0
    while i < len(segments):
        j = i
        while j < len(segments) and segments[j].link == segments[i].link:
            j += 1
        inner = _struck(text, segments[i:j], tokens)
        link = segments[i].link
        shown = "".join(text[s.start : s.end] for s in segments[i:j])
        if link is not None and shown.strip():
            extra = link_attrs(links[link]) if link_attrs is not None and links else ""
            out.append(f'<a href="{escape_text(hrefs[link])}"{extra}>{inner}</a>')
        else:
            out.append(inner)
        i = j
    return "".join(out)


def passage_attrs(element: Element) -> str:
    """Attributes for the element's own block tag, each with a leading space: ` lang`
    when the passage is in another language than the document ("" otherwise)."""
    if element.lang and is_language_tag(element.lang):
        return f' lang="{escape_text(element.lang)}"'
    return ""
