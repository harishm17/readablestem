"""The ways around a converted file: a skip link, a contents list, page markers, footnote links.

What the output looks like and why is in docs/output.md ("Navigation"). The assembler decides
*where* each piece goes (it knows the headings, the pages and the notes); this module only
writes the markup and holds the words, so both can be tested and changed on their own.

- `skip_link`: the first thing in the file, hidden until it is focused, jumping to the page's
  one `<h1>` (WCAG 2.4.1, Bypass Blocks), or to the notice above it when the page has one (parts
  shown as pictures, or nobody reviewed the page): a link that jumped past that notice would
  hide it from the readers it is written for. The title is the start of the content, and the
  Contents list stands after it. The target carries `tabindex="-1"`, which makes it focusable
  by following the link (Firefox and VoiceOver move there; a link to an element that cannot
  take focus only scrolls) without making it a tab stop. A title the assembler adds has the id
  `MAIN_ID`; a heading that is the title keeps its own. Canvas removes `tabindex` from a pasted
  page, which also drops the skip link.
- `contents_nav`: `<nav aria-label="Contents">` after the `<h1>`, with nested links to the
  headings, made from the outline as written (after the repair that never skips a level), when
  there are at least `MIN_TOC_HEADINGS` of them and the PDF has no contents page of its own
  (two lists of contents would be one too many). The title is not an entry: it stands right
  above the list. A heading repeated under the same parent is one entry. The list is a
  `<details>`, closed in the markup so that every reader of the file that draws no style (a
  phone's first screen, Canvas, Word, a transcript) gets a short line, and open to the eye
  from `WIDE` up and on paper (`student_sheet.CONTENTS`).
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
from dataclasses import dataclass, field
from html import escape

# The id of a title `<h1>` the assembler adds; the skip link's target when no heading is it.
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
# The width from which the contents list is shown open (and the same on paper). Under it the
# list is one closed line, so a phone's first screen shows the title and the start of the text.
WIDE = "40rem"


@dataclass(frozen=True)
class TocEntry:
    level: int  # the heading level as written (2 for an `<h2>`)
    id: str
    text: str  # plain text of the heading


@dataclass
class _Entry:
    """One line of the list: a heading, and the later headings under the same parent that
    say the same words (`also`), with what is under all of them."""

    first: TocEntry
    also: list[TocEntry] = field(default_factory=list)
    children: list[_Entry] = field(default_factory=list)


def skip_link(authored_class: str, target_id: str = MAIN_ID) -> str:
    target = escape(target_id, quote=True)
    return f'<a class="skip-link {authored_class}" href="#{target}">{SKIP_TEXT}</a>'


def _tree(entries: Sequence[TocEntry]) -> list[_Entry]:
    """The entries as a tree by heading level. A heading more than one level below the one
    before it nests one level only; one above the first heading is a sibling of it. A heading
    with the words of an earlier one under the same parent is merged into it, so a heading
    that repeats ("Teacher Support" in every section) is one line."""
    roots: list[_Entry] = []
    path: list[_Entry] = []
    for entry in entries:
        while path and path[-1].first.level >= entry.level:
            path.pop()
        siblings = path[-1].children if path else roots
        words = " ".join(entry.text.casefold().split())
        same = next(
            (s for s in siblings if " ".join(s.first.text.casefold().split()) == words), None
        )
        if same is not None and same.first.level == entry.level:
            same.also.append(entry)
            path.append(same)
        else:
            line = _Entry(entry)
            siblings.append(line)
            path.append(line)
    return roots


def _list(lines: Sequence[_Entry]) -> str:
    out = ["<ul>"]
    for line in lines:
        first = line.first
        item = f'<li><a href="#{escape(first.id, quote=True)}">{escape(first.text)}</a>'
        if line.also:
            # The later places carry their number, so a link is never the same words twice.
            more = ", ".join(
                f'<a href="#{escape(e.id, quote=True)}" '
                f'aria-label="{escape(e.text, quote=True)} {number}">{number}</a>'
                for number, e in enumerate(line.also, 2)
            )
            item += f" ({more})"
        if line.children:
            item += _list(line.children)
        out.append(item + "</li>")
    out.append("</ul>")
    return "".join(out)


def contents_nav(
    entries: Sequence[TocEntry], authored_class: str, document_lang: str | None = None
) -> str:
    """The contents list: nested `<ul>`s by heading level, one link per heading, in a
    `<details>` whose summary is its label. `document_lang`: given when the document is not
    in English; the label (an English `aria-label`) is then marked English and the entries the
    document's."""
    items = _list(_tree(entries))
    nav_lang = ""
    if document_lang:
        nav_lang = ' lang="en"'
        items = f'<div lang="{escape(document_lang, quote=True)}">{items}</div>'
    return (
        f'<nav class="toc"{nav_lang} aria-label="{CONTENTS_LABEL}">\n'
        f'<details class="toc-details"><summary class="toc-title {authored_class}">'
        f"{CONTENTS_LABEL}</summary>\n{items}\n</details>\n</nav>"
    )


def pagebreak(said: str, element_id: str | None, authored_class: str) -> str:
    """A page marker. `said` is "Page 12" (the printed label), "PDF page 5" (the position in
    the PDF, for a page without a printed label among pages that have one) or "Page 5"."""
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
