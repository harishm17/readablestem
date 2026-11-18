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
- **A struck relation.** `\\not=` and `\\not\\perp` are converted to a slash in a zero-width
  `mpadded` and then the relation, two things that read as "solidus" and "equals". They are
  joined into one operator: the relation with the combining long solidus overlay (U+0338), as the
  single negated character where Unicode has one (`\\not=` is U+2260, `\\not\\in` U+2209) and the
  pair where it does not (`\\not\\perp`). `\\nleq` and the like are already one character.
- **Operators left as identifiers.** `\\pm`, `:`, `\\ldots`, `\\langle` and `\\rangle` come out as
  `<mi>`, which a browser sets without the spacing of an operator (about 0.44 em narrower for
  `a \\pm b`, 0.17 em for `f : A`, 0.55 em for `a \\ldots b`), so they are `<mo>`. Neither the
  speech here nor either engine reads them differently; the percent sign is `<mi>` too and
  is drawn and read the same as `<mo>`, so it stays as it is. The prime is an operator
  (`normal_form`, rule 4).

**The normal form** (`normal_form`) is the one list of rules every `<math>` of a student page
follows, whoever wrote it (the LaTeX converter, LaTeXML, the Word reader). It runs where MathML
is made (`validate.safe_latex_to_mathml`, `web.mathml.sanitize_mathml` for a source's own
MathML), so the speech is made from it, and again where it is written into a page
(`safe_html.sanitize_mathml`), so MathML stored before a rule existed is written the same
way. It is idempotent and changes no stored record.

1. A function name is one identifier. A run of single letters that spells a name the speech
   knows (`sin`, `log`, `lim`, `max`) is the same token `\\sin` or `\\max` writes: `<mi>` for
   a function, `<mo>` for a name that takes limits. An upright run (`\\mathrm{sin}`) always;
   an italic run (`sin x` typed in maths) only for a lower-case name, since `Pr` may be the
   Prandtl number.
2. A number is never styled: no bold (or other variant) on digits, written as plain digits,
   and no `mathvariant` on an `mn`. The double-struck one (the indicator) is a symbol and stays.
3. A `mathvariant` on an operator says nothing and goes.
4. A prime in a superscript is an `<mo>`.
5. An `mtable` that is the whole of the equation and has its columns aligned right and left
   (`aligned`, `align`, `split`) or centered (`gathered`) carries `data-aligned-rows="true"`.

What was checked and needs no change: `mtable` rows and columns (pmatrix, bmatrix, vmatrix,
cases, aligned) are spoken as matrices, determinants, cases and lines by both engines, and
`\\lim` and `\\sum` limits are read as limits whether they are scripts or under- and
over-scripts (`tests/test_screenreader_shape.py` pins these readings when the engines are built).
"""

from __future__ import annotations

import unicodedata
import xml.etree.ElementTree as ET

from readablestem.core.mathchars import base_char, char_variant, styled_char
from readablestem.pipeline.speech import FUNCTIONS, LIMIT_OPERATORS

RING = "∘"
DEGREE = "°"
HORIZONTAL_BAR = "―"
MACRON = "¯"
LOW_LINE = "_"
_OVER_UNDER = frozenset({"mover", "munder", "munderover"})
SOLIDUS_OVERLAY = "\u0338"
_BIG_SOLIDUS = "\u29f8"
# The operators the converter leaves as `<mi>` and a browser then sets without operator spacing.
_OPERATORS_AS_IDENTIFIERS = frozenset("±:…⟨⟩")


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


def _as_operator(node: ET.Element) -> str:
    """The tag of `node` with its local name `mo`, in the same namespace."""
    return node.tag[: len(node.tag) - len(_local(node))] + "mo"


def _is_strike(node: ET.Element) -> bool:
    """`mpadded(mtext "big solidus")`: how the converter writes the slash of `\\not`."""
    return (
        _local(node) == "mpadded"
        and len(node) == 1
        and _local(node[0]) == "mtext"
        and (node[0].text or "").strip() == _BIG_SOLIDUS
    )


def _join_struck_relations(parent: ET.Element) -> None:
    """A strike followed by a one-character relation -> one `mo` with the overlay on it."""
    index = 0
    while index + 1 < len(parent):
        relation = parent[index + 1]
        text = (relation.text or "").strip()
        if _is_strike(parent[index]) and _local(relation) in {"mo", "mi"} and len(text) == 1:
            joined = ET.Element(_as_operator(relation))
            joined.text = unicodedata.normalize("NFC", text + SOLIDUS_OVERLAY)
            parent[index : index + 2] = [joined]
        index += 1


def shape_general(root: ET.Element) -> None:
    """Apply the fixes above to a converted `math` tree, in place."""
    for node in list(root.iter()):
        _join_struck_relations(node)
        if _local(node) == "mi" and (node.text or "") in _OPERATORS_AS_IDENTIFIERS:
            node.tag = _as_operator(node)
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


# ------------------------------------------------------------------------------ normal form

# Weights browsers ignore on a MathML token. MathML Core (Chromium, and so most readers'
# browsers) draws only mathvariant="normal"; Firefox and Safari honour more. A bold
# \boldsymbol{\omega} that stays `<mi mathvariant="bold-italic">` is drawn as a plain
# italic omega there. Written as the Unicode bold code point, every maths font draws it
# bold and every screen reader says "bold omega". Latin letters already come out that way
# (\mathbf{d} is U+1D41D); Greek ones, which \mathbf and \boldsymbol turn into an
# attribute, are the gap this closes. Digits are not baked (rule 2): a bold digit is read
# out as its code point name by some voices.
_BOLD_VARIANTS = frozenset({"bold", "bold-italic"})
_TOKENS = frozenset({"mi", "mn", "mo"})
INVISIBLE_TIMES = "⁢"
PRIMES = frozenset("′″‴⁗")
ALIGNED_ROWS = "data-aligned-rows"
_SCRIPTS = frozenset({"msub", "msup", "msubsup"})
_FUNCTION_NAMES = frozenset(FUNCTIONS) | frozenset(LIMIT_OPERATORS)


def _bake_bold(node: ET.Element, inherited: str | None = None) -> None:
    """Bold and bold-italic tokens as Unicode mathematical bold letters, the `mathvariant`
    taken off when it has done its job. The attribute is inherited, as MathML says
    (`<mstyle mathvariant="bold">` over `<mi>`s): read from the containers, applied to the
    tokens below, and a container's own bold attribute is removed. A token with its own
    attribute and no bold code point to show it (an operator, a letter Unicode has no bold
    form of) keeps the attribute until the rules below. Other variants are left alone."""
    own = node.attrib.get("mathvariant")
    variant = own if own in _BOLD_VARIANTS else inherited
    if own is not None and own not in _BOLD_VARIANTS and own != inherited:
        variant = None  # another variant overrides what was inherited
    if _local(node) in _TOKENS:
        if variant and node.text:
            baked = "".join(ch if ch.isdigit() else styled_char(ch, variant) for ch in node.text)
            if baked != node.text:
                node.text = baked
                node.attrib.pop("mathvariant", None)
        return
    if own in _BOLD_VARIANTS:
        del node.attrib["mathvariant"]
    for child in node:
        _bake_bold(child, variant)


def _letter(node: ET.Element) -> tuple[str, bool] | None:
    """(the letter, is it upright) of a one-letter identifier, else None."""
    text = node.text or ""
    if _local(node) != "mi" or len(node) or len(text) != 1:
        return None
    if not (text.isascii() and text.isalpha()):
        return None
    variant = node.get("mathvariant")
    if variant == "normal":
        return text, True
    return (text, False) if variant is None else None


def _script_letter(node: ET.Element) -> tuple[str, bool] | None:
    """The letter a script sits on (`n` of `n^2`), else None."""
    if _local(node) in _SCRIPTS and len(node):
        return _letter(node[0])
    return None


def _is_invisible_times(node: ET.Element) -> bool:
    return _local(node) == "mo" and (node.text or "").strip() == INVISIBLE_TIMES and not len(node)


def _function_run(kids: list[ET.Element], start: int) -> tuple[int, str, bool]:
    """The run of single letters from `start` (LaTeXML puts an invisible times between them,
    and the last may carry a script): (end index, the letters, are they all upright). The
    run is empty when `start` is not a letter, and mixed styles are not one name."""
    letters: list[str] = []
    upright: set[bool] = set()
    end = start
    j = start
    while j < len(kids):
        found = _letter(kids[j])
        if found is None and letters and _is_invisible_times(kids[j]) and j + 1 < len(kids):
            after = kids[j + 1]
            if _letter(after) is not None or _script_letter(after) is not None:
                j += 1
                continue
        if found is None:
            found = _script_letter(kids[j])
            if found is not None:
                letters.append(found[0])
                upright.add(found[1])
                end = j + 1
            break
        letters.append(found[0])
        upright.add(found[1])
        j += 1
        end = j
    if len(upright) > 1:
        return end, "", False
    return end, "".join(letters), upright == {True}


def _join_function_names(parent: ET.Element) -> None:
    kids = list(parent)
    out: list[ET.Element] = []
    i = 0
    changed = False
    while i < len(kids):
        end, word, upright = _function_run(kids, i)
        if end == i:
            out.append(kids[i])
            i += 1
            continue
        if word in _FUNCTION_NAMES and (upright or word.islower()):
            first = kids[i]
            kind = "mo" if word in LIMIT_OPERATORS else "mi"
            name = ET.Element(first.tag[: len(first.tag) - len(_local(first))] + kind)
            name.text = word
            last = kids[end - 1]
            if _local(last) in _SCRIPTS:  # `sin^2`: the name takes the script's base place
                last[0] = name
                out.append(last)
            else:
                out.append(name)
            changed = True
        else:
            out.extend(kids[i:end])
        i = end
    if changed:
        parent[:] = out


def _plain_digits(text: str) -> str:
    return "".join(
        base_char(ch)
        if (variant := char_variant(ch)) and variant != "double-struck" and base_char(ch).isdigit()
        else ch
        for ch in text
    )


def _aligned_rows(root: ET.Element) -> ET.Element | None:
    """The `mtable` that is the whole equation and aligned right and left (or centered)."""
    body = root
    while len(body) == 1 and _local(body[0]) == "mrow":
        body = body[0]
    if len(body) != 1 or _local(body[0]) != "mtable":
        return None
    table = body[0]
    rows = [[c.get("columnalign") for c in row] for row in table if _local(row) == "mtr"]
    cells = [a for row in rows for a in row]
    if not rows or len(rows) != len(table) or not cells or None in cells:
        return None
    if all(a == "center" for a in cells):
        return table
    alternating = all(
        a == ("right" if j % 2 == 0 else "left") for row in rows for j, a in enumerate(row)
    )
    return table if alternating else None


def normal_form(root: ET.Element) -> None:
    """Write a `math` tree in the normal form (module docstring), in place."""
    _bake_bold(root)
    for node in list(root.iter()):
        _join_function_names(node)
    for node in root.iter():
        tag = _local(node)
        if tag == "mn":
            if node.text:
                node.text = _plain_digits(node.text)
            if node.get("mathvariant") not in (None, "double-struck"):
                del node.attrib["mathvariant"]
        elif tag == "mo":
            node.attrib.pop("mathvariant", None)
        elif tag in {"msup", "msubsup"} and len(node):
            for token in node[-1].iter():
                if _local(token) == "mi" and token.text and set(token.text) <= PRIMES:
                    token.tag = _as_operator(token)
    table = _aligned_rows(root)
    if table is not None:
        table.set(ALIGNED_ROWS, "true")
