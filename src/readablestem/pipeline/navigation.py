"""The ways around a converted file: a skip link, a contents list, page markers, footnote links.

What the output looks like and why is in docs/output.md ("Navigation"). The assembler decides
*where* each piece goes (it knows the headings, the pages and the notes); this module only
writes the markup and holds the words, so both can be tested and changed on their own.

- `skip_link`: the first thing in the file, hidden until it is focused, jumping over the
  contents list to the empty `<span id="main-content">` just before `<main>` (WCAG 2.4.1,
  Bypass Blocks). The id is on a span, not on `<main>` itself, because other code and tests
  read the file by the literal `<main>` tag and its first child. No `tabindex` is needed:
  browsers move keyboard focus to a fragment's target, and Canvas removes `tabindex` from a
  pasted page anyway.
- `contents_nav`: `<nav aria-label="Contents">` with nested links to the headings, made from
  the outline as written (after the repair that never skips a level), when there are at
  least `MIN_TOC_HEADINGS` of them and the PDF has no contents page of its own (two lists
  of contents would be one too many).
- `pagebreak`: an `<hr role="doc-pagebreak" aria-label="Page N">` (axe accepts that role on
  `hr` only) and the same words as visible text beside it, so an LMS that drops the role or
  the label, or the CSS (Canvas keeps no pseudo-element rules), still shows the page.
- `backlink` and the `doc-noteref` / `doc-footnote` roles: a footnote's mark in the text
  links to the note, and the note links back.

The words are the tool's own: every element that carries them has `assemble.AUTHORED_CLASS`
so the wording check reads them and a document in another language marks them `lang="en"`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

MAIN_ID = "main-content"
SKIP_TEXT = "Skip to main content"
CONTENTS_LABEL = "Contents"
BACKLINK_TEXT = "Back to the text"
# A contents list is generated from at least this many headings: with fewer, a list of
# links costs the reader more than it saves.
MIN_TOC_HEADINGS = 3
# `Element.reasons` of an entry of the PDF's own contents page (`layout.CONTENTS_ENTRY`; a
# test keeps the two equal, since assembly does not import the PDF code).
PRINTED_CONTENTS_REASON = "contents entry"


@dataclass(frozen=True)
class TocEntry:
    level: int  # the heading level as written (2 for an `<h2>`)
    id: str
    text: str  # plain text of the heading


def skip_link(authored_class: str) -> str:
    return f'<a class="skip-link {authored_class}" href="#{MAIN_ID}">{SKIP_TEXT}</a>'


def contents_nav(entries: Sequence[TocEntry], authored_class: str) -> str:
    """The contents list: nested `<ul>`s by heading level, one link per heading. A heading
    more than one level below the one before it nests one level only; one above the first
    heading is a sibling of it."""
    out: list[str] = []
    stack: list[int] = []
    for entry in entries:
        if not stack or entry.level > stack[-1]:
            out.append("<ul>")
            stack.append(entry.level)
        else:
            out.append("</li>")
            while len(stack) > 1 and entry.level < stack[-1]:
                out.append("</ul></li>")
                stack.pop()
        out.append(f'<li><a href="#{escape(entry.id, quote=True)}">{escape(entry.text)}</a>')
    out.append("</li>" + "</ul></li>" * (len(stack) - 1) + "</ul>")
    return (
        f'<nav class="toc" aria-label="{CONTENTS_LABEL}">\n'
        f'<p class="toc-title {authored_class}" aria-hidden="true">{CONTENTS_LABEL}</p>\n'
        f"{''.join(out)}\n</nav>"
    )


def pagebreak(label: str, element_id: str | None, authored_class: str) -> str:
    """A page marker. `label` is the page's printed label, or its position in the PDF."""
    said = f"Page {label}"
    attrs = f' id="{escape(element_id, quote=True)}"' if element_id else ""
    return (
        f'<div class="pagebreak {authored_class}">'
        f'<hr role="doc-pagebreak" aria-label="{escape(said, quote=True)}"{attrs}>'
        f'<span aria-hidden="true">{escape(said)}</span></div>'
    )


def backlink(ref_id: str, authored_class: str) -> str:
    return (
        f'<a class="backlink {authored_class}" href="#{escape(ref_id, quote=True)}" '
        f'role="doc-backlink">{BACKLINK_TEXT}</a>'
    )


# Added to a document's stylesheet only when it has a contents list / page markers / notes
# (`_Renderer.add_style`), so documents without them are written as before. `border-top`,
# `margin`, `padding`, `font-size`, `color`, `text-align` and `display` are properties Canvas
# keeps, so the Canvas export carries these rules into `style` attributes.
TOC_CSS = """\
.toc { margin: 1rem auto; max-width: 42rem; padding: 0 1rem; }
.toc-title { font-weight: 700; margin: 0 0 0.25em; }
.toc ul { margin: 0; padding-left: 1.25em; }
.toc li { margin: 0.15em 0; }
"""
PAGEBREAK_CSS = """\
.pagebreak { margin: 1.5em 0 0.5em; padding: 0.25em 0 0; border-top: 1px solid #767676;
  font-size: 0.8rem; color: #595959; text-align: right; }
.pagebreak hr { margin: 0; padding: 0; border: 0; height: 0; }
@media screen and (prefers-color-scheme: dark) {
  .pagebreak { border-top-color: #8c8c8c; color: #c4c4c4; }
}
"""
FOOTNOTE_CSS = """\
.backlink { font-size: 0.85em; margin-left: 0.5em; }
"""
