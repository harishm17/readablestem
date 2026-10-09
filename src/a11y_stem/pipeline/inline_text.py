"""How `assemble` writes running text (owned by the text builder: links, colour, language).

Two seams, so the text features land here and not in `assemble.py`
(docs/design/content-features.md):

- `render_inline_text`: the one place inline text becomes HTML (paragraphs, list items,
  headings, captions). Today it escapes the text and puts each inline equation at its
  marker. `Element.links` and `Element.colour_runs` (offsets into `Element.text` as
  stored, markers included) will be applied here: a span that starts or ends inside a
  marker, or reaches past the text, must be skipped, and `Link.href` written only when
  `core.types.is_safe_href` still holds.
- `passage_attrs`: extra attributes for an element's own block (`<p>`, `<li>`,
  `<h2>`...). Today none; `Element.lang` will become `lang="..."` here.

Rendering reads the stored element only (no options), so the review screen's re-render
of a stored result writes the same file.
"""

from __future__ import annotations

from collections.abc import Callable

from a11y_stem.core.types import INLINE_MARKER_RE, Element
from a11y_stem.pipeline.safe_html import escape_text

# HTML for the inline equation with this element id, or None when the id is not an
# equation of this document (the marker then stays as text: a layout bug made visible).
EquationHtml = Callable[[str], str | None]
# Adds CSS to the document's stylesheet, once per name (`_Renderer.add_style`).
AddStyle = Callable[[str, str], None]


def render_inline_text(
    text: str,
    *,
    element: Element | None,
    equation: EquationHtml,
    add_style: AddStyle | None = None,
) -> str:
    """`text` as HTML: escaped, with inline-equation markers replaced by `equation(id)`.

    `element` is the element whose stored `text` this is, or None when the text is
    something else (a caption, a heading a reviewer retyped): only then do the offsets
    of its links and colour runs apply. `add_style` lets a feature add the CSS it needs
    to documents that use it.
    """
    out, pos = [], 0
    for match in INLINE_MARKER_RE.finditer(text):
        maths = equation(match.group(1))
        if maths is None:
            continue  # not one of ours: leave the characters as text
        out.append(escape_text(text[pos : match.start()]))
        out.append(maths)
        pos = match.end()
    out.append(escape_text(text[pos:]))
    return "".join(out)


def passage_attrs(element: Element) -> str:
    """Attributes for the element's own block tag, each with a leading space ("" today)."""
    return ""
