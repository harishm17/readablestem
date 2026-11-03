"""Small shape fixes for all maths, so a screen reader that follows the markup says the right
thing (docs/mathml-alttext.md, "Shape for screen readers"). Chemistry has its own module
(`chem_mathml`). Both run at the one place MathML is made from LaTeX
(`validate.safe_latex_to_mathml`).

- **The degree sign.** `^\\circ` is converted to the ring operator (U+2218), which Speech Rule
  Engine (MathJax, ChromeVox) says "composed with" or "ring". A ring raised on a symbol is the
  degree sign (U+00B0), which both MathCAT and Speech Rule Engine say as "degrees". The ring
  stays where it is an operator between two things (`f \\circ g`).
- **Overline and underline.** `\\overline` and `\\underline` use the horizontal bar (U+2015),
  which Speech Rule Engine says as "quotation dash". The macron (U+00AF, over) and the low line
  (U+005F, under) are the accents MathML names for them and are read as "bar".
- **Labelled arrows.** `\\xrightarrow{label}` is converted to an arrow in an `mstyle` with the
  label in an `mpadded` and an `mspace`. MathCAT's chemistry rules fail on that wrapper
  ("Pattern match/replacement failure") and the reader says nothing for the whole equation. The
  arrow and the label are put directly in the `mover` or `munder`, which a browser draws the same
  (the arrow stretches to the label).

What was checked and needs no change: `mtable` rows and columns (pmatrix, bmatrix, vmatrix,
cases, aligned) are spoken as matrices, determinants, cases and lines by both engines, and
`\\lim` and `\\sum` limits are read as limits whether they are scripts or under- and
over-scripts (`tests/test_screenreader_shape.py` pins these readings when the engines are built).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

RING = "∘"
DEGREE = "°"
HORIZONTAL_BAR = "―"
MACRON = "¯"
LOW_LINE = "_"
_OVER_UNDER = frozenset({"mover", "munder", "munderover"})


def _local(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _only_child(node: ET.Element) -> ET.Element:
    return node[0] if _local(node) == "mrow" and len(node) == 1 else node


def _unwrap_arrow_base(node: ET.Element) -> None:
    """`mstyle(mo)` as the base of an over- or under-script -> the `mo`."""
    base = node[0]
    if _local(base) == "mstyle" and len(base) == 1 and _local(base[0]) == "mo":
        node[0] = base[0]


def _unwrap_label(node: ET.Element, index: int) -> None:
    """`mpadded(mrow(label), mspace)` -> the label (an `mrow` of one child -> the child)."""
    label = node[index]
    if _local(label) != "mpadded":
        return
    kids = [k for k in label if _local(k) != "mspace"]
    if len(kids) == 1:
        node[index] = _only_child(kids[0])


def shape_general(root: ET.Element) -> None:
    """Apply the fixes above to a converted `math` tree, in place."""
    for node in root.iter():
        tag = _local(node)
        if tag in _OVER_UNDER and len(node) >= 2:
            _unwrap_arrow_base(node)
            for index in range(1, len(node)):
                _unwrap_label(node, index)
        if tag in {"msup", "msubsup"} and len(node) >= 2:
            sup = _only_child(node[-1])
            if _local(sup) == "mo" and (sup.text or "").strip() == RING:
                sup.text = DEGREE
        elif tag in {"mover", "munder"} and len(node) == 2:
            bar = _only_child(node[1])
            if _local(bar) == "mo" and (bar.text or "").strip() == HORIZONTAL_BAR:
                bar.text = MACRON if tag == "mover" else LOW_LINE
