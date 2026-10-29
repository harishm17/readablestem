"""Which Tier 2 failure class an item of the extended development set belongs to.

Pure functions over MathML text and OpenStax page XHTML; no network, no files, no model.
The extended development set (`gold/devset.py`, docs/eval.md "Extended development set")
exists so that accuracy work on the classes the original development set cannot see can
be built and measured without touching held-out data. Each class here is one row of the
coverage table (`CLASSES`); an item carries the ids of the classes it belongs to in its
`classes` field.

Detection is by structure (MathML shape, the sup/sub elements of the page, the headings),
never by reading a model's output.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

MATHML_NS = "http://www.w3.org/1998/Math/MathML"


@dataclass(frozen=True)
class DevClass:
    id: str
    group: str  # maths | notation | prose | structure | image
    label: str
    unit: str  # what one item is: equation, script run, heading, table, ...
    target: int  # items wanted in the whole set (maths classes: 20 to 40)
    backlog: str  # the backlog items (T2-n) this class feeds


CLASSES: tuple[DevClass, ...] = (
    DevClass("inline_fraction", "maths", "Inline stacked fractions", "equation", 40, "T2-1"),
    DevClass("system_braced", "maths", "Systems of equations with a brace", "equation", 20, "T2-2"),
    DevClass(
        "multirow_equations",
        "maths",
        "Multi-row equations (unbraced systems, worked steps)",
        "equation",
        20,
        "T2-2",
    ),
    DevClass("determinant", "maths", "Determinants", "equation", 20, "T2-2"),
    DevClass("matrix", "maths", "Matrices", "equation", 20, "T2-2"),
    DevClass("piecewise_words", "maths", "Piecewise functions with words", "equation", 20, "T2-2"),
    DevClass(
        "numbered_exercise_eq", "maths", "Numbered exercises with equations", "equation", 40, "T1-5"
    ),
    DevClass("display_large", "maths", "Large display formulas (slides)", "equation", 20, "T1-14"),
    DevClass("equation_image", "image", "Equations as pictures", "equation", 20, "T2-3"),
    DevClass("chem_formula", "notation", "Chemical formulas", "equation", 20, "T2-4"),
    DevClass("chem_charge", "notation", "Charges on ions", "equation", 20, "T2-4"),
    DevClass("chem_equation", "notation", "Chemical equations", "equation", 20, "T2-4"),
    DevClass(
        "nuclide", "notation", "Nuclide notation (stacked prefix scripts)", "equation", 20, "T2-5"
    ),
    DevClass(
        "prose_unit_power",
        "prose",
        "Units with superscripts in prose (cm3, m/s2)",
        "script run",
        40,
        "T2-4",
    ),
    DevClass(
        "prose_chem_script",
        "prose",
        "Chemical subscripts and charges in prose",
        "script run",
        40,
        "T2-4",
    ),
    DevClass("prose_nuclide", "prose", "Nuclide prefix scripts in prose", "script run", 20, "T2-5"),
    DevClass(
        "prose_script_other",
        "prose",
        "Other sub- and superscripts in running text",
        "script run",
        40,
        "T2-4",
    ),
    DevClass("code_block", "structure", "Code blocks", "block", 20, "-"),
    DevClass("glossary_entry", "structure", "Glossary and key terms", "entry", 40, "-"),
    DevClass("table_data", "structure", "Data tables", "table", 20, "-"),
    DevClass("table_layout", "structure", "Layout tables", "table", 10, "-"),
    DevClass(
        "heading_sentence",
        "structure",
        "Headings that are sentences (bold lines)",
        "heading",
        40,
        "T2-7",
    ),
    DevClass(
        "heading_repeated_label", "structure", "Repeated heading labels", "heading", 40, "T2-7"
    ),
    DevClass(
        "heading_multiline", "structure", "Headings long enough to wrap", "heading", 20, "T2-8"
    ),
    DevClass("hyphen_break", "structure", "Words hyphenated at a line end", "line end", 40, "T1-9"),
)
CLASS_IDS = tuple(c.id for c in CLASSES)
MATHS_CLASSES = tuple(c.id for c in CLASSES if c.group in {"maths", "notation", "image"})


# ------------------------------------------------------------------------- MathML


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_math(mathml: str) -> ET.Element:
    """The presentation tree of one <math>: namespaces and annotations dropped."""
    root = ET.fromstring(mathml)
    for node in root.iter():
        node.tag = _local(node.tag)
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag in {"annotation", "annotation-xml"}:
                parent.remove(child)
    # <semantics> wraps the presentation; unwrap it so the tree starts at the content.
    return root


def _leaf_text(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


def _tokens(root: ET.Element) -> list[tuple[str, str]]:
    """(tag, text) of every token element, in document order."""
    return [
        (n.tag, _leaf_text(n)) for n in root.iter() if n.tag in {"mi", "mn", "mo", "mtext", "ms"}
    ]


_BRACES = {"{", "⎧", "⎨", "⎩", "⎫", "⎬", "⎭", "⎪"}
_BARS = {"|", "‖", "∣"}
_BRACKETS = {"[", "(", "⎡", "⎛", "⎜", "⎢"}
_RELATIONS = {"=", "<", ">", "≤", "≥", "≠", "≈", "≡", "→"}
_WORD = re.compile(r"[A-Za-z]{2,}")
_FUNCTION_NAMES = {
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "log",
    "ln",
    "lim",
    "det",
    "max",
    "min",
    "exp",
    "arg",
    "mod",
    "sup",
    "inf",
    "gcd",
    "dim",
    "ker",
    "rank",
}
_CONDITION_WORDS = {"if", "for", "when", "otherwise", "where", "and", "or", "else"}
_ARROWS = {"→", "⇌", "⟶", "⇄", "⇒", "⟷", "←", "⇋"}
_ELEMENT = re.compile(
    r"^(?:H|He|Li|Be|B|C|N|O|F|Ne|Na|Mg|Al|Si|P|S|Cl|Ar|K|Ca|Sc|Ti|V|Cr|Mn|Fe|Co|Ni|Cu|Zn|Ga|"
    r"Ge|As|Se|Br|Kr|Rb|Sr|Y|Zr|Nb|Mo|Tc|Ru|Rh|Pd|Ag|Cd|In|Sn|Sb|Te|I|Xe|Cs|Ba|La|Ce|Pr|Nd|"
    r"Pm|Sm|Eu|Gd|Tb|Dy|Ho|Er|Tm|Yb|Lu|Hf|Ta|W|Re|Os|Ir|Pt|Au|Hg|Tl|Pb|Bi|Po|At|Rn|Fr|Ra|Ac|"
    r"Th|Pa|U|Np|Pu|Am|Cm|Bk|Cf|Es|Fm|Md|No|Lr|e|n|p|d|t)$"
)
# H2O, NaCl, Ca(OH)2, 2H2, CO3: element symbols with optional counts, at least one of them.
_FORMULA = re.compile(r"^(?:\d+)?(?:[A-Z][a-z]?\d*|\((?:[A-Z][a-z]?\d*)+\)\d*)+$")
_CHEM_TWO_LETTERS = re.compile(r"[A-Z][a-z]|[A-Z]\d|\)\d")


def _row_texts(table: ET.Element) -> list[list[tuple[str, str]]]:
    return [_tokens(row) for row in table.iter("mtr")]


def _is_word(text: str) -> bool:
    return bool(_WORD.fullmatch(text)) and text.lower() not in _FUNCTION_NAMES


def _fences(root: ET.Element) -> list[tuple[str, str]]:
    """(open, close) fence of every mtable: an mfenced parent, or the mo before and after."""
    parents = {child: parent for parent in root.iter() for child in parent}
    found: list[tuple[str, str]] = []
    for table in root.iter("mtable"):
        node = table
        opening = closing = ""
        for _ in range(3):  # the table, its mrow, and the fenced group around that
            parent = parents.get(node)
            if parent is None:
                break
            if parent.tag == "mfenced":
                opening = parent.get("open", "(")
                closing = parent.get("close", ")")
                break
            siblings = list(parent)
            at = siblings.index(node)
            before = siblings[at - 1] if at else None
            after = siblings[at + 1] if at + 1 < len(siblings) else None
            if before is not None and before.tag == "mo" and _leaf_text(before):
                opening = _leaf_text(before)
                closing = _leaf_text(after) if after is not None and after.tag == "mo" else ""
                break
            node = parent
        found.append((opening, closing))
    return found


def math_features(mathml: str) -> dict:
    """Shape facts about one equation that the classes are defined from."""
    root = parse_math(mathml)
    tokens = _tokens(root)
    tables = list(root.iter("mtable"))
    fences = _fences(root)
    open_brace = any(o in _BRACES for o, _ in fences) or any(
        t == "mo" and x in _BRACES for t, x in tokens
    )
    bars = any(o in _BARS for o, _ in fences)
    brackets = any(o in _BRACKETS for o, _ in fences)
    rows = [row for table in tables for row in _row_texts(table)]
    with_relation = sum(any(t == "mo" and x in _RELATIONS for t, x in row) for row in rows)
    cond_words = any(
        (t == "mtext" and _is_word(x)) or (t in {"mi", "mtext"} and x.lower() in _CONDITION_WORDS)
        for t, x in tokens
    )
    texts = [x for _, x in tokens]
    return {
        "tokens": len(tokens),
        "mfrac": sum(1 for _ in root.iter("mfrac")),
        "tables": len(tables),
        "rows": len(rows),
        "brace": open_brace,
        "bars": bars,
        "brackets": brackets,
        "rows_with_relation": with_relation,
        "condition_words": cond_words,
        "prescripts": any(True for _ in root.iter("mprescripts")),
        "arrow": any(t == "mo" and x in _ARROWS for t, x in tokens),
        "texts": texts,
        "mtext": [x for t, x in tokens if t == "mtext" and x],
        "msub": sum(1 for _ in root.iter("msub")),
        "msup": sum(1 for _ in root.iter("msup")),
    }


def _chem_tokens(texts: list[str]) -> list[str]:
    return [x for x in texts if x and _FORMULA.match(x) and _CHEM_TWO_LETTERS.search(x)]


def _has_chem_formula(root: ET.Element, feats: dict) -> bool:
    """A formula written as an mtext or upright mi ("H2O", "NaCl"), or as upright letters
    with subscripts (H, sub 2, O)."""
    if _chem_tokens(feats["mtext"]):
        return True
    for node in root.iter("msub"):
        kids = list(node)
        if len(kids) < 2:
            continue
        base, sub = kids[0], kids[1]
        base_text = _leaf_text(base)
        if (
            base.tag in {"mi", "mtext"}
            and _ELEMENT.match(base_text)
            and re.fullmatch(r"\d+", _leaf_text(sub))
            and (base.get("mathvariant") == "normal" or base.tag == "mtext" or len(base_text) == 2)
        ):
            return True
    return False


def _has_charge(root: ET.Element) -> bool:
    for node in root.iter("msup"):
        kids = list(node)
        if len(kids) < 2:
            continue
        charge = _leaf_text(kids[1])
        if re.fullmatch(r"\d*[+−–\-]", charge) and _chem_base(kids[0]):
            return True
    for node in root.iter("msubsup"):
        kids = list(node)
        charged = len(kids) >= 3 and re.fullmatch(r"\d*[+−–\-]", _leaf_text(kids[2]))
        if charged and _chem_base(kids[0]):
            return True
    return False


def _chem_base(node: ET.Element) -> bool:
    text = _leaf_text(node)
    if node.tag in {"mi", "mtext"}:
        return bool(_ELEMENT.match(text)) or bool(_chem_tokens([text]))
    if node.tag in {"msub", "mrow"}:
        inner = [_leaf_text(n) for n in node.iter() if n.tag in {"mi", "mtext"}]
        return any(_ELEMENT.match(x) or _chem_tokens([x]) for x in inner)
    return False


def _has_nuclide(root: ET.Element) -> bool:
    """Stacked prefix scripts on an element symbol: mass number above atomic number, the
    symbol after them. OpenStax writes them as an msubsup with an empty base, or as an
    mmultiscripts with prescripts."""
    for node in root.iter():
        if node.tag in {"msubsup", "msup", "msub"} and len(node):
            kids = list(node)
            base = kids[0]
            scripts = [_leaf_text(c) for c in kids[1:]]
            if not _leaf_text(base) and any(
                re.fullmatch(r"[+\u2212\u2013-]?\d{1,3}", x) for x in scripts
            ):
                return True
        if node.tag == "mmultiscripts" and any(True for _ in node.iter("mprescripts")):
            kids = list(node)
            at = next((i for i, c in enumerate(kids) if c.tag == "mprescripts"), len(kids))
            pre = [_leaf_text(c) for c in kids[at + 1 :]]
            if kids and _ELEMENT.match(_leaf_text(kids[0])) and any(x.isdigit() for x in pre):
                return True
    return False


def classify_equation(
    mathml: str, *, display: str | None, exercise_number: str | None = None
) -> list[str]:
    """Class ids of one MathML equation. `display` is "block" or "inline"; `exercise_number`
    is the number printed before the equation when it opens a numbered exercise."""
    feats = math_features(mathml)
    root = parse_math(mathml)
    classes: list[str] = []
    rows = feats["rows"]
    chem_formula = _has_chem_formula(root, feats)
    charge = _has_charge(root)
    chemical = chem_formula or charge or feats["arrow"]
    if feats["mfrac"] and display != "block":
        classes.append("inline_fraction")
    if feats["tables"] and rows >= 2:
        if feats["brace"] and feats["condition_words"]:
            classes.append("piecewise_words")
        elif feats["brace"] and feats["rows_with_relation"] >= 2:
            classes.append("system_braced")
        elif (
            feats["rows_with_relation"] >= 2
            and not (feats["bars"] or feats["brackets"])
            and not chemical
        ):
            classes.append("multirow_equations")
    if feats["tables"] and (feats["bars"] or feats["brackets"]):
        # A bar-fenced table is a determinant; a bracket-fenced one is a matrix. A fenced
        # table with a relation in every row is a system, not a matrix.
        if feats["bars"]:
            classes.append("determinant")
        elif feats["rows_with_relation"] < 2:
            classes.append("matrix")
    if _has_nuclide(root):
        classes.append("nuclide")
    if chem_formula:
        classes.append("chem_formula")
    if charge:
        classes.append("chem_charge")
    if feats["arrow"] and (chem_formula or charge):
        classes.append("chem_equation")
    if exercise_number:
        classes.append("numbered_exercise_eq")
    return classes


# --------------------------------------------------------------------------- prose

UNIT_WORDS = {
    "m",
    "cm",
    "mm",
    "km",
    "nm",
    "um",
    "s",
    "ms",
    "kg",
    "g",
    "mg",
    "L",
    "mL",
    "ft",
    "in",
    "yd",
    "mi",
    "N",
    "J",
    "W",
    "Hz",
    "V",
    "A",
    "K",
    "mol",
    "Pa",
    "C",
    "T",
    "eV",
    "MeV",
    "min",
    "h",
    "m/s",
    "km/h",
    "kg/m",
    "g/cm",
    "mol/L",
    "N/m",
    "J/s",
    "cm/s",
    "ft/s",
    "mi/h",
    "m/s2",
    "rad/s",
    "kg/m3",
    "g/mL",
    "atm",
    "lb",
    "oz",
    "gal",
    "ha",
    "u",
    "Bq",
    "Gy",
    "Sv",
}
_UNIT_TAIL = re.compile(r"(?:^|[\s(/·⋅×])([A-Za-zμµ]{1,3}(?:/[A-Za-z]{1,3})?)$")
_SCRIPT_TAGS = {"sup", "sub"}


def _plain(node: ET.Element) -> str:
    return "".join(node.itertext())


def paragraph_runs(block: ET.Element) -> tuple[str, list[dict]]:
    """(marked text, runs) of one text block: sup and sub elements outside MathML.

    The marked text writes a superscript as ^{...} and a subscript as _{...}; each run
    names its script, value, the word it hangs on and its class. Maths inside the block
    is left out of the text as "[math]" (its own equation record carries it).
    """
    pieces: list[str] = []
    runs: list[dict] = []

    def walk(node: ET.Element) -> None:
        for child in _children(node):
            tag = _local(child.tag)
            if tag == "math":
                pieces.append("[math]")
            elif tag in _SCRIPT_TAGS:
                value = " ".join(_plain(child).split())
                before = "".join(pieces)
                runs.append(
                    {
                        "script": tag,
                        "value": value,
                        "before": before[-24:],
                        "after": "",
                        "class": None,
                        "_at": len(before),
                    }
                )
                pieces.append(("^{" if tag == "sup" else "_{") + value + "}")
            elif tag == "aside":
                continue
            else:
                walk(child)
            if child.tail:
                pieces.append(child.tail)

    def _children(node: ET.Element) -> list[ET.Element]:
        if node.text:
            pieces.append(node.text)
        return list(node)

    walk(block)
    text = " ".join("".join(pieces).split())
    for run in runs:
        at = run.pop("_at")
        run["after"] = "".join("".join(pieces)[at:].split(None, 1)[:1])[:24]
    marked_runs = _classify_runs(runs, "".join(pieces))
    return text, marked_runs


def _classify_runs(runs: list[dict], raw: str) -> list[dict]:
    """Give each run a class from the text around it."""
    out = []
    for run in runs:
        before = run["before"]
        value = run["value"]
        after_text = raw[raw.find(run["before"]) + len(run["before"]) :] if before else raw
        sup = run["script"] == "sup"
        # What the run hangs on: the characters just before, with an earlier script run
        # of the same word skipped (SO_{4}^{2-} -> the base is SO).
        base = re.sub(r"[_^]\{[^}]*\}$", "", before)
        tail = _UNIT_TAIL.search(base)
        unit_tail = tail.group(1) if tail else ""
        nuclide_prefix = (
            sup
            and re.fullmatch(r"\d{1,3}", value) is not None
            and not re.search(r"[A-Za-z0-9)\]]$", base)
            and re.match(r"^(?:[_^]\{[^}]*\})?\s*[A-Z][a-z]?\b", after_text) is not None
        )
        chem_base = re.search(r"(?:[A-Z][a-z]?|\))$", base) or re.search(
            r"[A-Z][a-z]?\d*[_^]\{[^}]*\}$", before
        )
        if nuclide_prefix:
            kind = "prose_nuclide"
        elif sup and re.fullmatch(r"[−–\-]?\d+", value) and unit_tail in UNIT_WORDS:
            kind = "prose_unit_power"
        elif (sup and re.fullmatch(r"\d*[+−–\-]", value) and chem_base) or (
            not sup and re.fullmatch(r"\d+", value) and re.search(r"(?:[A-Z][a-z]?|\))$", base)
        ):
            kind = "prose_chem_script"
        else:
            kind = "prose_script_other"
        out.append({**run, "class": kind})
    return out


_TEXT_BLOCKS = {"p", "li", "td", "th", "dd", "dt", "figcaption", "caption"}


def prose_script_records(root: ET.Element) -> list[dict]:
    """One record per text block holding at least one sup or sub outside MathML."""
    out = []
    for block in root.iter():
        if _local(block.tag) not in _TEXT_BLOCKS:
            continue
        if not any(_local(n.tag) in _SCRIPT_TAGS for n in block.iter()):
            continue
        # A sup in a nested list item belongs to that item, not to this block.
        if any(_local(n.tag) in _TEXT_BLOCKS - {"p"} for n in list(block.iter())[1:]):
            direct = [n for n in block if _local(n.tag) in _SCRIPT_TAGS or _local(n.tag) == "span"]
            if not direct:
                continue
        text, runs = paragraph_runs(block)
        if runs:
            out.append({"text": text, "runs": runs, "element_id": block.get("id")})
    return out


# ------------------------------------------------------------------- exercises


def _text_before(root: ET.Element, target: ET.Element, skip: set[int]) -> str | None:
    """Text of `root` that precedes `target` in document order (None if not found).
    Elements whose id() is in `skip` (the exercise number and its divider) add nothing."""
    parts: list[str] = []

    def walk(node: ET.Element) -> bool:
        if node is target:
            return True
        if id(node) in skip:
            return False
        if node.text:
            parts.append(node.text)
        for child in node:
            if walk(child):
                return True
            if child.tail:
                parts.append(child.tail)
        return False

    return "".join(parts) if walk(root) else None


def opening_exercise_number(math: ET.Element, parents: dict[ET.Element, ET.Element]) -> str | None:
    """The exercise number printed in front of `math` when the equation opens a numbered
    exercise ("18. 0 = ..."): an OpenStax problem with an os-number, or a list item of an
    ordered list. None when text comes first or the exercise has no number."""
    node = parents.get(math)
    while node is not None:
        if node.get("data-type") == "problem":
            marks = [
                c
                for c in node.iter()
                if "os-number" in (c.get("class") or "").split()
                or "os-divider" in (c.get("class") or "").split()
            ]
            number = next(
                (
                    "".join(c.itertext()).strip()
                    for c in marks
                    if "os-number" in (c.get("class") or "").split()
                ),
                "",
            )
            if not number:
                return None
            before = _text_before(node, math, {id(c) for c in marks})
            return number if before is not None and not before.strip() else None
        if _local(node.tag) == "li":
            parent = parents.get(node)
            if parent is not None and _local(parent.tag) == "ol":
                items = [c for c in parent if _local(c.tag) == "li"]
                try:
                    start = int(parent.get("start") or 1)
                except ValueError:
                    start = 1
                before = _text_before(node, math, set())
                if before is not None and not before.strip():
                    return str(start + items.index(node))
            return None
        node = parents.get(node)
    return None


# ------------------------------------------------------------------------ headings

SENTENCE_END = (".", "?", ":", "!")
MULTILINE_CHARS = 60


def heading_classes(text: str, same_page_texts: list[str]) -> list[str]:
    """Class ids of a heading from its text and the other headings of the page."""
    classes = []
    words = text.split()
    if text.endswith(SENTENCE_END) or len(words) >= 8:
        classes.append("heading_sentence")
    if same_page_texts.count(text) >= 2:
        classes.append("heading_repeated_label")
    if len(text) >= MULTILINE_CHARS:
        classes.append("heading_multiline")
    return classes


# --------------------------------------------------------------- code and glossary


def code_records(root: ET.Element) -> list[dict]:
    """<pre> blocks (and block-level <code>) of a page, with their exact lines."""
    out = []
    for node in root.iter():
        tag = _local(node.tag)
        if tag == "pre" or (tag == "code" and (node.get("class") or "").find("block") >= 0):
            text = _plain(node).strip("\n")
            if text.strip():
                out.append({"text": text, "element_id": node.get("id")})
    return out


def glossary_records(root: ET.Element) -> list[dict]:
    """(term, definition) pairs of the page: <dl> definition lists and OpenStax
    data-type="definition" blocks (term, meaning)."""
    out = []
    for node in root.iter():
        if node.get("data-type") == "definition":
            term = next((n for n in node.iter() if n.get("data-type") == "term"), None)
            meaning = next((n for n in node.iter() if n.get("data-type") == "meaning"), None)
            if term is not None and meaning is not None:
                out.append(
                    {
                        "term": " ".join(_plain(term).split()),
                        "definition": " ".join(_plain(meaning).split()),
                        "element_id": node.get("id"),
                    }
                )
    if out:
        return out
    for dl in root.iter():
        if _local(dl.tag) != "dl":
            continue
        term = None
        for child in dl:
            if _local(child.tag) == "dt":
                term = " ".join(_plain(child).split())
            elif _local(child.tag) == "dd" and term:
                out.append(
                    {
                        "term": term,
                        "definition": " ".join(_plain(child).split()),
                        "element_id": child.get("id"),
                    }
                )
    return out
