"""Re-serialise MathML for display on the review screen.

The proposed MathML came from a model (via LaTeX) and has been validated by the
pipeline, but the review page is our own origin, so it is treated as untrusted anyway:
parse with defusedxml, keep only presentation-MathML elements and harmless attributes,
and write a fresh tree with every text node and attribute escaped. Nothing from the input
is pasted into the page as a string (architecture rule 2).
"""

from __future__ import annotations

from html import escape
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET
from markupsafe import Markup

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

ALLOWED_ELEMENTS = frozenset(
    {
        "math", "mrow", "mi", "mn", "mo", "ms", "mtext", "mspace", "msub", "msup",
        "msubsup", "mfrac", "msqrt", "mroot", "mstyle", "merror", "mpadded", "mphantom",
        "mfenced", "menclose", "munder", "mover", "munderover", "mtable", "mtr", "mtd",
        "mlabeledtr", "mmultiscripts", "mprescripts", "none", "semantics", "annotation",
    }
)  # fmt: skip

ALLOWED_ATTRIBUTES = frozenset(
    {
        "display", "mathvariant", "mathsize", "stretchy", "fence", "separator", "accent",
        "accentunder", "lspace", "rspace", "form", "columnalign", "rowalign",
        "columnspacing", "rowspacing", "columnlines", "rowlines", "frame", "linethickness",
        "width", "height", "depth", "notation", "scriptlevel", "displaystyle",
        "movablelimits", "largeop", "symmetric", "minsize", "maxsize", "open", "close",
        "separators", "encoding", "columnspan", "rowspan", "align", "voffset",
    }
)  # fmt: skip

# Dropped silently with their subtree: alternative encodings browsers do not render.
DROPPED_ELEMENTS = frozenset({"annotation-xml"})

MAX_INPUT_CHARS = 200_000
MAX_DEPTH = 64


class UnsafeMathML(ValueError):
    """The MathML could not be parsed or holds something we will not render."""


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _write(node: Element, out: list[str], depth: int) -> None:
    if depth > MAX_DEPTH:
        raise UnsafeMathML("too deep")
    tag = _local(node.tag)
    if tag not in ALLOWED_ELEMENTS:
        raise UnsafeMathML("element not allowed")
    attrs = []
    for name, value in sorted(node.attrib.items()):
        local = _local(name)
        if local in ALLOWED_ATTRIBUTES and "{" not in name:
            attrs.append(f' {local}="{escape(value, quote=True)}"')
    if tag == "math":
        attrs.insert(0, f' xmlns="{MATHML_NS}"')
    out.append(f"<{tag}{''.join(attrs)}>")
    if node.text:
        out.append(escape(node.text, quote=False))
    if tag != "annotation":  # annotation holds text only (e.g. the LaTeX source)
        for child in node:
            if _local(child.tag) not in DROPPED_ELEMENTS:
                _write(child, out, depth + 1)
            if child.tail:
                out.append(escape(child.tail, quote=False))
    out.append(f"</{tag}>")


def sanitize_mathml(mathml: str) -> Markup:
    """Allowlisted, re-serialised MathML, safe to place in a page. Raises UnsafeMathML."""
    if not mathml or len(mathml) > MAX_INPUT_CHARS:
        raise UnsafeMathML("empty or too long")
    try:
        root = SafeET.fromstring(mathml)
    except Exception:  # defusedxml raises ParseError and its own DefusedXmlException types
        raise UnsafeMathML("not well-formed") from None
    if _local(root.tag) != "math":
        raise UnsafeMathML("root is not <math>")
    out: list[str] = []
    _write(root, out, 0)
    return Markup("".join(out))  # every piece above was escaped or is a fixed literal


def try_sanitize_mathml(mathml: str | None) -> Markup | None:
    """`sanitize_mathml`, or None when there is nothing safe to show."""
    if not mathml:
        return None
    try:
        return sanitize_mathml(mathml)
    except UnsafeMathML:
        return None
