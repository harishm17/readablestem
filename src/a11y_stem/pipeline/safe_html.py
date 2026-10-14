"""The two safety primitives every HTML writer in the pipeline shares.

- `escape_text`: document and model text as HTML text or attribute values (C0 control
  characters dropped, since they are parse errors in HTML).
- `sanitize_mathml`: MathML is never pasted as a string. It is parsed with defusedxml
  and an allowlisted tree of presentation elements and attributes is written back
  (docs/output.md "MathML is rebuilt, never pasted").

They live here, not in `assemble`, so that `table_html` (tables, and the figures' data
tables) uses exactly the same rules without importing the document assembler.
`assemble` re-exports the names it always had (`assemble.sanitize_mathml` and the
`MATHML_*` constants).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Literal
from xml.etree.ElementTree import Element as XmlElement
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from a11y_stem.core.mathchars import bake_bold_variants

Display = Literal["block", "inline"]

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

# C0 controls other than tab/newline/carriage return are HTML parse errors.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_text(text: str) -> str:
    """`text` safe as HTML text or as a double- or single-quoted attribute value."""
    return html.escape(_CONTROL.sub("", text), quote=True)


# --------------------------------------------------------------------------- MathML

# Presentation MathML kept in the output: MathML Core plus `menclose` (latex2mathml uses
# it for \boxed and \cancel). Any other element makes the whole equation unusable, so it
# is shown as the original image instead of as maths with a silent hole in it.
MATHML_ELEMENTS = frozenset(
    {
        "math", "mrow", "mi", "mn", "mo", "mtext", "ms", "mspace", "msub", "msup",
        "msubsup", "munder", "mover", "munderover", "mmultiscripts", "mprescripts", "none",
        "mfrac", "msqrt", "mroot", "mstyle", "merror", "mpadded", "mphantom", "mtable",
        "mtr", "mtd", "menclose",
    }
)  # fmt: skip
# Wrappers replaced by their first child, which is what a browser shows anyway:
# `semantics` (the rest are annotations) and `maction` (the rest are alternatives).
MATHML_UNWRAP = frozenset({"semantics", "maction"})
# Layout attributes only. No href, event handlers, style, class, id or colour.
MATHML_ATTRIBUTES = frozenset(
    {
        "mathvariant", "displaystyle", "scriptlevel", "dir", "form", "fence", "separator",
        "lspace", "rspace", "stretchy", "symmetric", "maxsize", "minsize", "largeop",
        "movablelimits", "accent", "accentunder", "linethickness", "width", "height",
        "depth", "voffset", "notation", "columnalign", "rowalign", "columnspan", "rowspan",
        "columnlines", "rowlines", "columnspacing", "rowspacing", "frame", "framespacing",
        "align", "linebreak", "lquote", "rquote",
    }
)  # fmt: skip
_ATTR_VALUE = re.compile(r"[A-Za-z0-9 .%+\-]{0,64}")
MAX_MATHML_DEPTH = 64
MAX_MATHML_NODES = 5000


class _Unusable(Exception):
    """MathML that cannot be shown faithfully (unknown element, too deep, too big)."""


def split_tag(tag: str) -> tuple[str, str]:
    """ElementTree tag "{ns}name" -> (ns, name)."""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


@dataclass
class _MathWriter:
    nodes: int = 0
    has_text: bool = False

    def text(self, value: str | None) -> str:
        if not value:
            return ""
        if value.strip():
            self.has_text = True
        return escape_text(value)

    def element(self, node: XmlElement, depth: int) -> str:
        self.nodes += 1
        if depth > MAX_MATHML_DEPTH or self.nodes > MAX_MATHML_NODES:
            raise _Unusable("too deep or too large")
        if not isinstance(node.tag, str):  # comment or processing instruction
            return ""
        ns, name = split_tag(node.tag)
        if ns not in ("", MATHML_NS):
            raise _Unusable("foreign namespace")
        if name in MATHML_UNWRAP:
            first = next(iter(node), None)
            return "" if first is None else self.element(first, depth + 1)
        if name not in MATHML_ELEMENTS or name == "math":
            raise _Unusable("element not allowed")
        return f"<{name}{_attributes(node)}>{self.children(node, depth)}</{name}>"

    def children(self, node: XmlElement, depth: int) -> str:
        parts = [self.text(node.text)]
        for child in node:
            parts.append(self.element(child, depth + 1))
            parts.append(self.text(child.tail))
        return "".join(parts)


def _attributes(node: XmlElement) -> str:
    kept = []
    for key, value in sorted(node.attrib.items()):
        if key in MATHML_ATTRIBUTES and _ATTR_VALUE.fullmatch(value):
            kept.append(f' {key}="{escape_text(value)}"')
    return "".join(kept)


def sanitize_mathml(mathml: str, *, display: Display, element_id: str | None = None) -> str | None:
    """Re-serialise MathML from an allowlisted tree, or None if it cannot be shown.

    The input comes from a converter fed by model output, or from a reviewer's edit, so
    it is untrusted. It is parsed with defusedxml (no DTDs, entities or external
    references), and only allowlisted presentation elements and attributes are written
    back, with all text escaped. Unknown attributes are dropped; an unknown element
    rejects the whole equation (the caller then shows the original image). `display`
    is forced to where the equation is placed; `element_id` becomes the root id.
    """
    try:
        root = SafeET.fromstring(mathml, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError):
        return None
    ns, name = split_tag(root.tag)
    if name != "math" or ns not in ("", MATHML_NS):
        return None
    bake_bold_variants(root)
    writer = _MathWriter()
    try:
        inner = writer.children(root, depth=1)
    except (_Unusable, RecursionError):
        return None
    if not writer.has_text:  # nothing a screen reader could speak
        return None
    id_attr = f' id="{escape_text(element_id)}"' if element_id else ""
    return f'<math display="{display}"{id_attr}>{inner}</math>'
