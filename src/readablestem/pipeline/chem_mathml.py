"""The MathML shape of chemistry, as a screen reader's chemistry rules expect it.

The converter writes `\\text{H}_2\\text{O}` as `msub(mtext H, 2)` then `mtext O`, an ion as
`msup(mtext Na, +)`, a state as `mtext (aq)`, and a nuclide as an `msubsup` with an empty base
before an `mtext`. A reader that follows the markup (MathCAT in NVDA and JAWS, Speech Rule Engine
in MathJax, VoiceOver) then sees words and loose scripts. MathCAT recognises chemistry from the
structure: one upright `mi` per element symbol, a charge or count on the symbol it belongs to, a
state as `(` `aq` `)`, a nuclide as `mmultiscripts`, and an arrow as `mo` (docs/chemistry.md,
"MathML shape"). `shape_chemistry` rewrites a converted tree to that shape.

It acts on a row of siblings only when the row holds evidence only chemistry has (`_has_evidence`):
a symbol with a count or a charge, a nuclide prefix, two symbols side by side, a state after a
species, or an arrow with symbols on both sides. A lone upright `H` or `N` (hydrogen, newton) and
`\\text{No}` in a sentence are left as they are. The `math` element then gets `intent`
(`:chemical-formula` or `:chemical-equation`) when everything in it is chemistry; `intent` is the
one MathML 4 speech attribute the allowlists accept, and only with those two values
(`core.mathml_intent`).

Pure tree work: no model, no I/O. Applied for every path that makes MathML from LaTeX, at the one
place that does (`validate.safe_latex_to_mathml`).
"""

# ruff: noqa: RUF001

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from readablestem.core.mathml_intent import EQUATION_INTENT, FORMULA_INTENT
from readablestem.pipeline.chem import ARROW_HEADS, STATE_SYMBOLS
from readablestem.pipeline.speech_nuclides import ELEMENTS

INVISIBLE_SEPARATOR = chr(0x2063)
_ROWS = frozenset({"math", "mrow", "mtd", "mstyle"})
_SCRIPTS = frozenset({"msub", "msup", "msubsup"})
_ARROW_HOLDERS = frozenset({"mover", "munder", "munderover"})
_PARTICLES = frozenset({"e", "n", "p"})
_GREEK_PARTICLES = frozenset({"α", "β", "γ"})
_SIGNS = frozenset({"+", "−", "–", "-"})
_STATE_TEXT = re.compile(r"^\((" + "|".join(sorted(STATE_SYMBOLS)) + r")\)$")
_CLOSERS = frozenset({")", "]"})
_FORMULA_OPERATORS = frozenset({"+", "(", ")", "[", "]", "·", INVISIBLE_SEPARATOR})
_SCRIPT_MARKS = frozenset({"*", "∗", "′", "•", "⋅", "∙"})
_TWO_LETTER_SYMBOLS = frozenset(s for s in ELEMENTS if len(s) == 2)

# Nodes already rewritten as chemistry (by `id`), so a row that holds one counts it as a species.
_Marked = set[int]


def _local(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _like(node: ET.Element, name: str, text: str | None = None) -> ET.Element:
    """A new element in the namespace of `node`."""
    prefix = node.tag.rsplit("}", 1)[0] + "}" if "}" in node.tag else ""
    new = ET.Element(prefix + name)
    if text is not None:
        new.text = text
    return new


def _text(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


def _split_symbols(text: str) -> list[str] | None:
    """`NaCl` -> [Na, Cl]; None when it is not exactly element symbols."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i : i + 2] in _TWO_LETTER_SYMBOLS:
            out.append(text[i : i + 2])
            i += 2
        elif text[i] in ELEMENTS:
            out.append(text[i])
            i += 1
        else:
            return None
    return out or None


def _upright_letter(node: ET.Element) -> bool:
    text = node.text or ""
    return (
        _local(node) == "mi"
        and len(node) == 0
        and len(text) == 1
        and node.get("mathvariant") == "normal"
        and text.isascii()
        and text.isalpha()
    )


def _symbols(node: ET.Element) -> list[str] | None:
    """The element symbols one token spells (an `mtext`, or an upright `mi`)."""
    tag = _local(node)
    if tag not in {"mtext", "mi"} or len(node):
        return None
    text = node.text or ""
    if tag == "mi" and node.get("mathvariant") != "normal" and len(text) < 2:
        return None  # an italic letter is a variable
    return _split_symbols(text) if text[:1].isupper() else None


def _is_particle(node: ET.Element) -> bool:
    tag = _local(node)
    text = _text(node)
    if tag == "mtext" and not len(node):
        return text in _PARTICLES
    if tag == "mi" and not len(node):
        return (node.get("mathvariant") == "normal" and text in _PARTICLES) or (
            text in _GREEK_PARTICLES
        )
    return False


def _charge(node: ET.Element) -> bool:
    """A raised charge: `+`, `−−`, `2+`, `3−`."""
    tag = _local(node)
    text = _text(node)
    if tag == "mo":
        return bool(text) and all(c in _SIGNS for c in text)
    if tag == "mrow" and len(node) == 2:
        return (
            _local(node[0]) == "mn"
            and _text(node[0]).isdigit()
            and _local(node[1]) == "mo"
            and _text(node[1]) in _SIGNS
        )
    return tag == "mrow" and len(node) == 1 and _charge(node[0])


def _count(node: ET.Element) -> bool:
    return _local(node) == "mn" and _text(node).isdigit()


def _empty(node: ET.Element) -> bool:
    return _local(node) == "mrow" and len(node) == 0 and not (node.text or "").strip()


def _scripts_ok(script: ET.Element) -> bool:
    """Scripts a species carries: a count below, a charge above."""
    tag = _local(script)
    if tag == "msub":
        return _count(script[1])
    if tag == "msup":
        return _charge(script[1])
    return tag == "msubsup" and _count(script[1]) and _charge(script[2])


def _is_script(node: ET.Element) -> bool:
    return _local(node) in _SCRIPTS and len(node) == (3 if _local(node) == "msubsup" else 2)


def _species(node: ET.Element, marked: _Marked) -> tuple[ET.Element, ET.Element | None] | None:
    """`node` as a species: (the symbol token, its script wrapper or None)."""
    if _is_script(node) and _symbols(node[0]) is not None:
        return node[0], node
    if _symbols(node) is not None:
        return node, None
    return None


def _prefix_scripts(node: ET.Element) -> bool:
    """`{}^{14}_{6}`: an empty base with numbers above and below."""
    if not _is_script(node) or not _empty(node[0]):
        return False
    return all(_local(k) in {"mn", "mrow", "mo"} and _text(k) for k in list(node)[1:])


def _nuclide_symbol(node: ET.Element) -> tuple[ET.Element, ET.Element | None] | None:
    """What follows a nuclide prefix: an element or particle token, with its scripts or not."""
    base, wrapper = (node[0], node) if _is_script(node) else (node, None)
    if _symbols(base) is not None and len(_symbols(base) or []) == 1:
        return base, wrapper
    if _is_particle(base):
        return base, wrapper
    return None


def _is_arrow(node: ET.Element) -> bool:
    tag = _local(node)
    if tag == "mo":
        return _text(node) in ARROW_HEADS
    if tag in {"mstyle", "mrow"} and len(node) == 1:
        return _is_arrow(node[0])
    return tag in _ARROW_HOLDERS and len(node) > 0 and _is_arrow(node[0])


def _is_species_node(node: ET.Element, marked: _Marked) -> bool:
    """Something that reads as one species in a row: a symbol, a scripted symbol, a nuclide,
    or a group already rewritten as chemistry."""
    if id(node) in marked or _local(node) == "mmultiscripts":
        return True
    if _local(node) in _SCRIPTS and len(node) and id(node[0]) in marked:
        return True
    return _species(node, marked) is not None


def _ends_species(node: ET.Element, marked: _Marked) -> bool:
    if _is_species_node(node, marked) or _is_particle(node):
        return True
    if _is_script(node):
        return _is_particle(node[0]) or _empty(node[0])
    return _local(node) == "mo" and _text(node) in _CLOSERS


def _has_evidence(kids: list[ET.Element], marked: _Marked) -> bool:
    """Does this row hold what only chemistry has (see the module docstring)?"""
    first_species: int | None = None
    arrow_at: int | None = None
    previous_species = False
    for i, node in enumerate(kids):
        found = _species(node, marked)
        if found is not None:
            token, wrapper = found
            if wrapper is not None and _scripts_ok(wrapper):
                return True
            if len(_symbols(token) or []) >= 2 and _local(token) == "mtext":
                return True
        if _prefix_scripts(node) and i + 1 < len(kids) and _nuclide_symbol(kids[i + 1]):
            return True
        species = _is_species_node(node, marked)
        if species and previous_species:
            return True
        if (
            _local(node) == "mtext"
            and _STATE_TEXT.match(node.text or "")
            and i > 0
            and _is_species_node(kids[i - 1], marked)
        ):
            return True
        if _is_arrow(node):
            arrow_at = i
        elif species:
            if first_species is None:
                first_species = i
            elif arrow_at is not None and first_species < arrow_at:
                return True
        previous_species = species
    return False


def _unwrap(node: ET.Element) -> ET.Element:
    """`mrow(x)` -> `x`: a script of one item needs no row around it."""
    return node[0] if _local(node) == "mrow" and len(node) == 1 else node


def _symbol_nodes(token: ET.Element) -> list[ET.Element]:
    """One upright `mi` per element symbol of the token."""
    out = []
    for symbol in _symbols(token) or [_text(token)]:
        mi = _like(token, "mi", symbol)
        mi.set("mathvariant", "normal")
        out.append(mi)
    return out


def _with_scripts(nodes: list[ET.Element], wrapper: ET.Element | None) -> list[ET.Element]:
    """The symbol nodes with the wrapper's scripts on the last symbol."""
    if wrapper is None:
        return nodes
    *head, last = nodes
    script = _like(wrapper, _local(wrapper))
    script.append(last)
    script.extend(list(wrapper)[1:])
    return [*head, script]


def _prescripted(prefix: ET.Element, base: ET.Element, wrapper: ET.Element | None) -> ET.Element:
    """`{}^{14}_{6}C` (and a script after the symbol) as one `mmultiscripts`."""
    multi = _like(prefix, "mmultiscripts")

    def pair(script: ET.Element | None) -> list[ET.Element]:
        """(below, above) of a script element, `none` for the one it does not have."""
        if script is None:
            return []
        kind = _local(script)
        if kind == "msub":
            return [_unwrap(script[1]), _like(prefix, "none")]
        if kind == "msup":
            return [_like(prefix, "none"), _unwrap(script[1])]
        return [_unwrap(script[1]), _unwrap(script[2])]  # msubsup

    multi.append(base)
    multi.extend(pair(wrapper))
    multi.append(_like(prefix, "mprescripts"))
    multi.extend(pair(prefix))
    return multi


def _with_base(script: ET.Element, base: ET.Element) -> ET.Element:
    copy = _like(script, _local(script))
    copy.attrib.update(script.attrib)
    copy.extend([base, *list(script)[1:]])
    return copy


def _merge_letter_pairs(kids: list[ET.Element]) -> list[ET.Element]:
    """`N` `a` (two upright letters in a row, as `\\mathrm{NaCl}` is written) as one `Na`."""
    out: list[ET.Element] = []
    i = 0
    while i < len(kids):
        node = kids[i]
        nxt = kids[i + 1] if i + 1 < len(kids) else None
        if (
            nxt is not None
            and _upright_letter(node)
            and _upright_letter(nxt)
            and (node.text or "") + (nxt.text or "") in _TWO_LETTER_SYMBOLS
        ):
            merged = _like(node, "mi", (node.text or "") + (nxt.text or ""))
            merged.set("mathvariant", "normal")
            out.append(merged)
            i += 2
        elif _is_script(node) and len(node):
            base = node[0]
            if _local(base) == "mrow" and len(base) == 2 and all(_upright_letter(k) for k in base):
                pair = (base[0].text or "") + (base[1].text or "")
                if pair in _TWO_LETTER_SYMBOLS:
                    merged = _like(base, "mi", pair)
                    merged.set("mathvariant", "normal")
                    node = _with_base(node, merged)  # a copy: the row may not be chemistry
            out.append(node)
            i += 1
        else:
            out.append(node)
            i += 1
    return out


def _shape_row(parent: ET.Element, marked: _Marked) -> bool:
    """Rewrite one row in place; True when it was chemistry."""
    kids = _merge_letter_pairs(list(parent))
    if not _has_evidence(kids, marked):
        return False
    out: list[ET.Element] = []
    i = 0
    while i < len(kids):
        node = kids[i]
        following = kids[i + 1] if i + 1 < len(kids) else None
        nuclide = _nuclide_symbol(following) if following is not None else None
        if _prefix_scripts(node) and nuclide is not None:
            token, wrapper = nuclide
            out.append(_prescripted(node, _symbol_nodes(token)[0], wrapper))
            i += 2
            continue
        state = _STATE_TEXT.match(node.text or "") if _local(node) == "mtext" else None
        if state and out and _is_species_node(out[-1], marked):
            out += [
                _like(node, "mo", "("),
                _like(node, "mi", state.group(1)),
                _like(node, "mo", ")"),
            ]
        elif (found := _species(node, marked)) is not None:
            out.extend(_with_scripts(_symbol_nodes(found[0]), found[1]))
        elif _is_script(node) and _is_particle(node[0]) and _charge(node[-1]):
            out.extend(_with_scripts(_symbol_nodes(node[0]), node))
        else:
            out.append(node)
        i += 1
    parent[:] = out
    marked.add(id(parent))
    return True


def _numberish(node: ET.Element) -> bool:
    """A script made only of digits, signs and the marks of an excited or radical species."""
    if _local(node) in {"none", "mprescripts"}:
        return True
    leaves = [k for k in node.iter() if len(k) == 0]
    return bool(leaves) and all(
        _local(k) in {"mn", "mo"}
        and all(c.isdigit() or c in _SIGNS | _SCRIPT_MARKS for c in _text(k))
        for k in leaves
    )


def _only_chemistry(node: ET.Element) -> bool:
    """Is everything in `node` something a formula or an equation is made of?"""
    tag = _local(node)
    text = _text(node)
    if tag == "mn":
        return text.replace(".", "", 1).isdigit()
    if tag == "mi":
        return bool(text) and (
            _symbols(node) is not None or text in STATE_SYMBOLS or _is_particle(node)
        )
    if tag == "mo":
        return text in ARROW_HEADS or text in _FORMULA_OPERATORS
    if tag in _ARROW_HOLDERS:
        return _is_arrow(node)
    if tag in _SCRIPTS or tag == "mmultiscripts":
        return (
            len(node) > 0
            and _only_chemistry(node[0])
            and all(_numberish(k) or _only_chemistry(k) for k in list(node)[1:])
        )
    return tag == "mrow" and all(_only_chemistry(k) for k in node)


def _has_species(node: ET.Element) -> bool:
    return any(
        (_local(k) == "mi" and (_symbols(k) is not None or _is_particle(k)))
        or _local(k) == "mmultiscripts"
        for k in node.iter()
    )


def _all_chemistry(top: ET.Element) -> bool:
    """Is the whole row formulas, states, coefficients, plus signs and arrows?"""
    return len(top) > 0 and all(_only_chemistry(k) for k in top) and _has_species(top)


MAX_SHAPED_DEPTH = 64  # the checks' own limit (`validate.MAX_MATHML_DEPTH`); deeper is left alone


def _deeper_than(root: ET.Element, limit: int) -> bool:
    """Is the tree more than `limit` levels deep? Without recursion: the input may be hostile."""
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in node)
    return False


def shape_chemistry(root: ET.Element) -> bool:
    """Reshape `root` (a converted `math` tree) in place; True when it holds chemistry.

    Chemistry gets the shape above. When everything in the expression is chemistry, the `math`
    element also gets `intent`: `:chemical-equation` with an arrow in it, else
    `:chemical-formula`. MathCAT reads `intent` only on the `math` element (a smaller formula
    like `H2 -> O2` falls under its own likelihood threshold without it), and an `intent` on a
    row beside other maths would make it read that maths as chemistry, so chemistry mixed with
    other maths gets none and relies on the shape. Maths with no chemistry in it is untouched, and
    so is a tree deeper than `MAX_SHAPED_DEPTH` (the MathML check refuses it anyway)."""
    if _deeper_than(root, MAX_SHAPED_DEPTH):
        return False
    marked: _Marked = set()
    chemical = False
    for node in reversed([n for n in root.iter() if _local(n) in _ROWS]):
        chemical = _shape_row(node, marked) or chemical  # innermost rows first
    if not chemical:
        return False
    top = root[0] if len(root) == 1 and _local(root[0]) == "mrow" else root
    if _all_chemistry(top):
        root.set(
            "intent", EQUATION_INTENT if any(_is_arrow(k) for k in top.iter()) else FORMULA_INTENT
        )
    return True
