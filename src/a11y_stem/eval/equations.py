r"""Equation scoring: MathML normalisation, exact match and tree-edit-distance similarity.

Ground truth is OpenStax presentation MathML. Predictions are either MathML or LaTeX
(converted with latex2mathml). Both sides go through the same `normalize()` so that
differences in markup style do not count as errors, while differences in the maths do.

Normalisation (strict, the default):
1. Drop namespaces, `semantics` wrappers, `annotation`/`annotation-xml`, and
   invisible layout elements (`mspace`, `mphantom`, `maligngroup`, `malignmark`).
2. `mfenced` becomes an `mrow` with explicit `mo` fences and separators.
3. `mathvariant` is inherited: on `mstyle` (or any other non-token element) it is pushed
   down to every token inside (`mi`, `mn`, `mo`, `mtext`, `ms`) that has no
   `mathvariant` of its own, as MathML specifies. OpenStax writes every bold vector as
   `<mstyle mathvariant="bold"><mi>d</mi></mstyle>`; latex2mathml writes `\mathbf{d}`
   as `<mi>U+1D41D</mi>`. Both end up as `<mi mathvariant="bold">d</mi>`.
4. `mstyle`, `mpadded` and `merror` then become plain, attribute-free `mrow` (styling
   only), so rule 8 can flatten or unwrap them.
5. Keep only meaning-bearing attributes (`mathvariant` on tokens, `linethickness`,
   `notation`, `rowspan`, `columnspan`) and serialise them in sorted order.
6. Token text: Unicode NFC, trimmed, whitespace collapsed; ASCII "-" and "'" in `mo`
   become U+2212 and U+2032; math alphanumerics (e.g. U+1D42F bold v) become the base
   letter plus `mathvariant`; invisible operators (U+2061-U+2064) are removed.
7. Redundant `mathvariant` defaults are removed after inheritance (single-letter `mi`
   is italic, other tokens are normal).
8. `mrow` is flattened inside elements that take any number of children (an "inferred
   mrow": math, mrow, msqrt, mtd, menclose). In fixed-arity slots (mfrac, msub, ...),
   a one-child `mrow` is replaced by its child.

Lenient mode additionally treats `mi`, `mn` and `mtext` as the same token type (OpenStax
often writes subscripts as `<mtext>0</mtext>` where LaTeX gives `<mn>0</mn>`) and drops
trailing sentence punctuation (".", ",", ";") at the end of the equation: a final
punctuation-only token of any type (`<mo>.</mo>` or `<mtext>.</mtext>`), or a final
punctuation mark at the end of the last text token (`<mtext>km/h.</mtext>`), including
inside the last cell of a final `mtable`.

Known gaps (not normalised; see tests/fixtures/sample_latex_predictions.jsonl):
OpenStax sometimes packs operators into text (`<mtext>time =</mtext>`,
`<mtext>m/s)(46 s)</mtext>`), which LaTeX writes as text plus `<mo>`. Whether lenient
mode should split text on operators is an open scoring decision (ADR 0001, question 3);
this scorer stays as it is, and `eval.equations_v2` (a stream comparison that splits
packed text and unifies look-alike characters) reports the answer beside it as a labelled
secondary.
"""

from __future__ import annotations

import html.entities
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from latex2mathml.converter import convert as _latex2mathml_convert

from a11y_stem.eval.ted import Node, tree_edit_distance

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

TOKEN_TAGS = {"mi", "mn", "mo", "mtext", "ms"}
# Elements whose children form an implicit row (any number of children).
INFERRED_MROW_TAGS = {"math", "mrow", "msqrt", "mtd", "menclose"}
# Styling-only wrappers: treated as a plain row.
TRANSPARENT_TAGS = {"mstyle", "mpadded", "merror"}
# Removed entirely (with their content).
DROP_TAGS = {"annotation", "annotation-xml", "mspace", "mphantom", "maligngroup", "malignmark"}
KEEP_ATTRS = {"mathvariant", "linethickness", "notation", "rowspan", "columnspan"}
INVISIBLE_OPERATORS = {"\u2061", "\u2062", "\u2063", "\u2064"}  # function app, times, sep, plus
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)
MO_ASCII_MAP = {"-": "\u2212", "'": "\u2032"}  # minus sign, prime
TRAILING_PUNCT = {".", ",", ";"}

# Unicode name fragment (after "MATHEMATICAL ") -> MathML mathvariant value.
_MATH_ALNUM_VARIANTS = [
    ("SANS-SERIF BOLD ITALIC ", "sans-serif-bold-italic"),
    ("SANS-SERIF BOLD ", "bold-sans-serif"),
    ("SANS-SERIF ITALIC ", "sans-serif-italic"),
    ("SANS-SERIF ", "sans-serif"),
    ("BOLD ITALIC ", "bold-italic"),
    ("BOLD SCRIPT ", "bold-script"),
    ("BOLD FRAKTUR ", "bold-fraktur"),
    ("BOLD ", "bold"),
    ("ITALIC ", "italic"),
    ("SCRIPT ", "script"),
    ("FRAKTUR ", "fraktur"),
    ("DOUBLE-STRUCK ", "double-struck"),
    ("MONOSPACE ", "monospace"),
]

_NAMED_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_XML_ENTITIES = {"lt", "gt", "amp", "quot", "apos"}


class MathMLError(ValueError):
    """Raised when input cannot be parsed as MathML or converted from LaTeX."""


# --------------------------------------------------------------------------- parsing


def _replace_html_entities(text: str) -> str:
    """Turn HTML named entities (e.g. &nbsp;) into characters; XML parsers reject them."""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _XML_ENTITIES:
            return match.group(0)
        char = html.entities.html5.get(name + ";")
        if char is None:
            return match.group(0)
        return {"<": "&lt;", "&": "&amp;", ">": "&gt;"}.get(char, char)

    return _NAMED_ENTITY.sub(repl, text)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _strip_namespaces(elem: ET.Element) -> None:
    for node in elem.iter():
        node.tag = _local(node.tag)
        for key in list(node.attrib):
            if key.startswith("{") or key.startswith("xmlns"):
                del node.attrib[key]


def parse_mathml(source: str | ET.Element) -> ET.Element:
    """Parse MathML text (or copy an element) into a namespace-free tree rooted at <math>."""
    if isinstance(source, ET.Element):
        root = ET.fromstring(ET.tostring(source, encoding="unicode"))
    else:
        try:
            root = ET.fromstring(_replace_html_entities(source.strip()))
        except ET.ParseError as exc:
            raise MathMLError(f"not well-formed MathML: {exc}") from exc
    _strip_namespaces(root)
    if root.tag != "math":
        wrapper = ET.Element("math")
        wrapper.append(root)
        root = wrapper
    return root


def latex_to_mathml(latex: str, display: str = "inline") -> str:
    """Convert LaTeX to presentation MathML with latex2mathml."""
    try:
        return _latex2mathml_convert(latex, display=display)
    except Exception as exc:  # latex2mathml raises several exception types
        raise MathMLError(f"latex2mathml could not convert {latex!r}: {exc}") from exc


# ------------------------------------------------------------------ normalisation


def _token_text(elem: ET.Element) -> str:
    text = unicodedata.normalize("NFC", "".join(elem.itertext()))
    text = text.translate(ZERO_WIDTH).replace("\u00a0", " ")
    return " ".join(text.split())


def _char_variant(ch: str) -> str | None:
    name = unicodedata.name(ch, "")
    if not name.startswith("MATHEMATICAL "):
        return None
    rest = name[len("MATHEMATICAL ") :]
    for prefix, variant in _MATH_ALNUM_VARIANTS:
        if rest.startswith(prefix):
            return variant
    return None


def _split_math_alphanumeric(text: str) -> tuple[str, str | None]:
    """U+1D42F (bold small v) -> ('v', 'bold'); a run of same-style letters too:
    U+1D41A U+1D42F U+1D420 ('avg' in bold) -> ('avg', 'bold'), as `\\mathbf{avg}` or a
    bold <mtext> gives. (text, None) when any character is unstyled or the styles mix."""
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return text, None
    variants = {_char_variant(ch) for ch in chars}
    if len(variants) != 1 or None in variants:
        return text, None
    return unicodedata.normalize("NFKC", text), variants.pop()


def _normalize_attrs(elem: ET.Element) -> dict[str, str]:
    attrs = {}
    for key, value in elem.attrib.items():
        if key not in KEEP_ATTRS:
            continue
        value = value.strip()
        if key in {"mathvariant", "notation"}:
            value = " ".join(value.lower().split())
        if key == "linethickness" and re.fullmatch(r"0+(\.0+)?([a-z]{2}|%)?", value):
            value = "0"
        attrs[key] = value
    return attrs


def _fenced_to_mrow(elem: ET.Element) -> ET.Element:
    row = ET.Element("mrow")
    open_ = elem.get("open", "(")
    close = elem.get("close", ")")
    separators = "".join(elem.get("separators", ",").split())
    children = list(elem)
    if open_:
        ET.SubElement(row, "mo").text = open_
    for index, child in enumerate(children):
        row.append(child)
        if index < len(children) - 1 and separators:
            sep = separators[min(index, len(separators) - 1)]
            ET.SubElement(row, "mo").text = sep
    if close:
        ET.SubElement(row, "mo").text = close
    return row


def _is_empty_token(elem: ET.Element) -> bool:
    return elem.tag in TOKEN_TAGS and not elem.text


def _normalize_node(elem: ET.Element, inherited_variant: str | None = None) -> ET.Element | None:
    """Return a normalised copy of `elem`, or None when it should be dropped.

    `inherited_variant` is the nearest ancestor's `mathvariant` (e.g. from `mstyle`).
    """
    tag = elem.tag
    if tag in DROP_TAGS:
        return None
    if tag == "semantics":
        for child in elem:
            if child.tag not in {"annotation", "annotation-xml"}:
                return _normalize_node(child, inherited_variant)
        return None
    if tag == "mfenced":
        return _normalize_node(_fenced_to_mrow(elem), inherited_variant)

    attrs = _normalize_attrs(elem)

    if tag in TOKEN_TAGS:
        out = ET.Element(tag, attrs)
        text = _token_text(elem)
        text, variant = _split_math_alphanumeric(text)
        # Precedence: the token's own attribute, then a styled character, then inherited.
        if "mathvariant" not in out.attrib:
            if variant:
                out.set("mathvariant", variant)
            elif inherited_variant:
                out.set("mathvariant", inherited_variant)
        if tag == "mo":
            if text and all(ch in INVISIBLE_OPERATORS for ch in text):
                return None
            text = MO_ASCII_MAP.get(text, text)
        out.text = text
        # Drop default mathvariant values.
        mv = out.get("mathvariant")
        if (tag == "mi" and len(text) == 1 and mv == "italic") or (
            mv == "normal" and not (tag == "mi" and len(text) == 1)
        ):
            del out.attrib["mathvariant"]
        return out

    # mathvariant only means something on tokens; elsewhere it is inherited downwards.
    own_variant = attrs.pop("mathvariant", None)
    if own_variant is not None:
        inherited_variant = own_variant
    if tag in TRANSPARENT_TAGS:
        tag, attrs = "mrow", {}
    out = ET.Element(tag, attrs)

    children = [
        c for c in (_normalize_node(child, inherited_variant) for child in elem) if c is not None
    ]

    if tag in INFERRED_MROW_TAGS:
        flat: list[ET.Element] = []
        for child in children:
            if child.tag == "mrow" and not child.attrib:
                flat.extend(child)
            elif not _is_empty_token(child):
                flat.append(child)
        children = flat
    else:
        # Fixed-arity element (mfrac, msub, munderover, mtable, mtr, ...).
        children = [
            child[0] if child.tag == "mrow" and not child.attrib and len(child) == 1 else child
            for child in children
        ]
    out.extend(children)
    return out


# Containers whose last child is the visual end of the equation.
_TRAILING_CONTAINERS = {"math", "mrow", "mtable", "mtr", "mtd"}


def _strip_trailing_punctuation(node: ET.Element) -> None:
    """Drop sentence punctuation at the very end of the equation (lenient mode only)."""
    while len(node):
        last = node[-1]
        if last.tag in _TRAILING_CONTAINERS:
            _strip_trailing_punctuation(last)
            return
        if last.tag not in TOKEN_TAGS | {"tok"}:
            return
        text = last.text or ""
        if text and all(ch in TRAILING_PUNCT or ch.isspace() for ch in text):
            node.remove(last)
            continue
        if last.tag in {"mtext", "tok"} and text[-1:] in TRAILING_PUNCT:
            last.text = text.rstrip("".join(TRAILING_PUNCT)).rstrip()
        return


def _apply_lenient(root: ET.Element) -> None:
    for node in root.iter():
        if node.tag in {"mi", "mn", "mtext"}:
            node.tag = "tok"
    _strip_trailing_punctuation(root)


def normalize(source: str | ET.Element, *, lenient: bool = False) -> ET.Element:
    """Return a normalised MathML tree rooted at <math> (see module docstring)."""
    root = _normalize_node(parse_mathml(source))
    if root is None:  # pragma: no cover - parse_mathml always returns <math>
        root = ET.Element("math")
    if lenient:
        _apply_lenient(root)
    return root


def canonical(source: str | ET.Element, *, lenient: bool = False) -> str:
    """Canonical string: normalised tree, sorted attributes, no whitespace between tags."""
    return _serialize(normalize(source, lenient=lenient))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _serialize(elem: ET.Element) -> str:
    attrs = "".join(
        f' {key}="{_escape(value).replace(chr(34), "&quot;")}"'
        for key, value in sorted(elem.attrib.items())
    )
    inner = _escape(elem.text or "") if len(elem) == 0 else "".join(_serialize(c) for c in elem)
    return f"<{elem.tag}{attrs}>{inner}</{elem.tag}>"


def to_tree(elem: ET.Element) -> Node:
    """Convert a normalised element into a TED tree. Labels include attributes and text."""
    label = elem.tag
    if elem.attrib:
        label += "[" + ",".join(f"{k}={v}" for k, v in sorted(elem.attrib.items())) + "]"
    if len(elem) == 0 and elem.text:
        label += ":" + elem.text
    return Node(label, [to_tree(child) for child in elem])


# ------------------------------------------------------------------------ scoring


@dataclass
class EquationScore:
    exact_strict: bool
    exact_lenient: bool
    ted: int
    teds: float
    gold_nodes: int
    pred_nodes: int
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def score_mathml(pred: str | ET.Element, gold: str | ET.Element) -> EquationScore:
    """Score predicted MathML against gold MathML."""
    gold_strict = normalize(gold)
    gold_tree = to_tree(gold_strict)
    try:
        pred_strict = normalize(pred)
    except MathMLError as exc:
        return EquationScore(False, False, gold_tree.size(), 0.0, gold_tree.size(), 0, str(exc))
    pred_tree = to_tree(pred_strict)
    distance = tree_edit_distance(pred_tree, gold_tree)
    largest = max(pred_tree.size(), gold_tree.size())
    return EquationScore(
        exact_strict=_serialize(pred_strict) == _serialize(gold_strict),
        exact_lenient=canonical(pred, lenient=True) == canonical(gold, lenient=True),
        ted=distance,
        teds=max(0.0, 1.0 - distance / largest),
        gold_nodes=gold_tree.size(),
        pred_nodes=pred_tree.size(),
    )


def score_latex(pred_latex: str, gold: str | ET.Element) -> EquationScore:
    """Convert predicted LaTeX to MathML, then score. Conversion failure scores zero."""
    try:
        pred = latex_to_mathml(pred_latex)
    except MathMLError as exc:
        gold_size = to_tree(normalize(gold)).size()
        return EquationScore(False, False, gold_size, 0.0, gold_size, 0, str(exc))
    return score_mathml(pred, gold)


def summarize(scores: list[EquationScore]) -> dict:
    """Aggregate scores. Rates are over all items, errors included."""
    n = len(scores)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "exact_strict": sum(s.exact_strict for s in scores) / n,
        "exact_lenient": sum(s.exact_lenient for s in scores) / n,
        "mean_teds": sum(s.teds for s in scores) / n,
        "errors": sum(s.error is not None for s in scores),
    }
