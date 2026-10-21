"""Chemistry in the speech reading (docs/chemistry.md): formulas, ions, state symbols,
reaction arrows and concentrations, read the way a chemistry teacher says them.

`pipeline.speech` hands a row of MathML siblings to `read_at` before it reads one sibling
at a time. The row counts as chemistry (`is_chem_row`) only with evidence a formula has: an
upright element symbol with a lowered count or a raised charge (`H`, 2), a state symbol after
a species, two element symbols side by side, or a bracket around one species (`[NO]`). A lone
upright `H` or `O` is read as before, so a variable that happens to be upright is never "H".
Both shapes of MathML are read: the converter's (`\\mathrm{H_2O}`: one `mi` per element) and
OpenStax's (one `mtext` for "HSO" or "2e", `mn` for a count or a charge).

| MathML | spoken |
|---|---|
| `H`, subscript 2, `O` | "H 2 O" (counts are numbers, no "sub") |
| `Na`, superscript `+` | "N a plus" |
| `SO`, subscript 4, superscript `2-` | "S O 4 2 minus" |
| `(aq)` `(s)` `(l)` `(g)` after a species | "aqueous" "solid" "liquid" "gas" |
| `->` between species | "reacts to form" |
| `<=>` between species | "is in equilibrium with" |
| an arrow with `\\Delta` or `heat` over it | "reacts, with heat, to form" |
| `[`, a species, `]` | "the concentration of H 3 O plus" |
| the electron `e` (with a raised minus) | "e minus" |

Element symbols are spelled letter by letter ("N a", "C l"): a screen reader says "Na" as a
word. Names are not substituted ("sulfate"): a reading must not say more than the page.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable

from readablestem.pipeline.speech_nuclides import ELEMENTS

STATE_WORDS = {"aq": "aqueous", "s": "solid", "l": "liquid", "g": "gas"}
EQUILIBRIUM = {"⇌", "⇋", "⇄"}
FORWARD = {"→", "⟶", "⟹"}
BACKWARD = {"←", "⟵"}
_HEAT = {"Δ", "∆", "heat"}
_CHARGE = re.compile(r"^(\d*)([+−–-])$")
_MTEXT_FORMULA = re.compile(r"^(\d*)((?:[A-Z][a-z]?\d*)+)$")
_ELECTRON = re.compile(r"^(\d*)e$")
_SIGNS = {"+": "plus", "−": "minus", "–": "minus", "-": "minus"}
_SCRIPTS = {"msub", "msup", "msubsup"}

Speak = Callable[[ET.Element], list[str]]


def _text(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


def _symbols_in(text: str) -> list[str] | None:
    """`text` as element symbols ("NaCl" -> ["Na", "Cl"]), or None when it is not made of
    them."""
    out: list[str] = []
    i = 0
    while i < len(text):
        two = text[i : i + 2]
        if len(two) == 2 and two[1].islower() and two in ELEMENTS:
            out.append(two)
            i += 2
        elif text[i] in ELEMENTS:
            out.append(text[i])
            i += 1
        else:
            return None
    return out or None


def is_element(node: ET.Element) -> bool:
    """An upright element symbol: an `mi` that is upright (`mathvariant="normal"`, or
    longer than one letter) and names an element, or an `mtext` that does. Italic letters
    are variables."""
    text = _text(node)
    if node.tag == "mi":
        upright = node.get("mathvariant") == "normal" or len(text) > 1
        return upright and text in ELEMENTS
    if node.tag == "mtext":
        return text in ELEMENTS
    return False


def _formula_text(node: ET.Element) -> list[str] | None:
    """The element symbols of an `mtext` that holds a formula the way OpenStax writes one
    ("2H", "NaCl", "HSO"), or None."""
    if node.tag != "mtext":
        return None
    match = _MTEXT_FORMULA.match(_text(node))
    if not match:
        return None
    return _symbols_in(re.sub(r"\d", "", match.group(2)))


def _is_electron(node: ET.Element) -> bool:
    """The electron: an upright `e` (an `mtext`, or an `mi` set upright), with a coefficient
    in front when OpenStax puts it in the same token ("2e"). An italic e is Euler's number."""
    text = _text(node)
    if node.tag == "mtext":
        return bool(_ELECTRON.match(text))
    return node.tag == "mi" and node.get("mathvariant") == "normal" and text == "e"


def _is_species_token(node: ET.Element) -> bool:
    return is_element(node) or _formula_text(node) is not None or _is_electron(node)


def _is_closing(node: ET.Element) -> bool:
    return node.tag == "mo" and _text(node) in {")", "]"}


def _group_of_species(node: ET.Element) -> bool:
    """An `mrow` of a bracket pair around species: "(SO4)", "[Cu(NH3)4]"."""
    kids = list(node)
    if len(kids) < 3 or not (
        kids[0].tag == "mo"
        and _text(kids[0]) in {"(", "["}
        and kids[-1].tag == "mo"
        and _text(kids[-1]) in {")", "]"}
    ):
        return False
    return any(_is_species_token(k) or _scripted_species(k) for k in kids[1:-1])


def _species_base(node: ET.Element) -> bool:
    """Something a charge or a count can hang on: an element symbol, a species that already
    has a count ("SO" with 4, then its charge), or a bracket group around species."""
    if _is_species_token(node):
        return True
    if node.tag in _SCRIPTS and len(node):
        return _species_base(node[0])
    return node.tag == "mrow" and (
        _group_of_species(node) or (len(node) == 1 and _species_base(node[0]))
    )


def _scripted_species(node: ET.Element) -> bool:
    """A subscript or superscript on a species."""
    return node.tag in _SCRIPTS and len(node) > 0 and _species_base(node[0])


def _state_at(kids: list[ET.Element], i: int) -> tuple[str, int] | None:
    """`( aq )` at kids[i]: the state word and the siblings used."""
    if i + 2 >= len(kids) or not (kids[i].tag == "mo" and _text(kids[i]) == "("):
        return None
    for used in (1, 2):
        inner = kids[i + 1 : i + 1 + used]
        closer = kids[i + 1 + used] if i + 1 + used < len(kids) else None
        if closer is None or not (closer.tag == "mo" and _text(closer) == ")"):
            continue
        word = "".join(_text(k) for k in inner)
        if word in STATE_WORDS and all(k.tag in {"mi", "mtext"} for k in inner):
            return STATE_WORDS[word], used + 2
    return None


def _bracketed(kids: list[ET.Element], i: int) -> int | None:
    """`[ species ]` at kids[i] with one token inside: the number of siblings, else None."""
    if (
        i + 2 < len(kids)
        and kids[i].tag == "mo"
        and _text(kids[i]) == "["
        and _species_base(kids[i + 1])
        and _is_closing(kids[i + 2])
        and _text(kids[i + 2]) == "]"
    ):
        return 3
    return None


def _split_bracket(kids: list[ET.Element], i: int) -> ET.Element | None:
    """OpenStax puts the charge of a bracketed species on a row that holds the opening
    bracket: `msup(mrow([, Co), 2+)` then `]`. A scripted species made of what is inside, or
    None."""
    node = kids[i]
    if not (
        node.tag in _SCRIPTS
        and len(node)
        and i + 1 < len(kids)
        and _is_closing(kids[i + 1])
        and _text(kids[i + 1]) == "]"
    ):
        return None
    base = node[0]
    if not (
        base.tag == "mrow" and len(base) == 2 and _text(base[0]) == "[" and base[0].tag == "mo"
    ):
        return None
    if not _species_base(base[1]):
        return None
    synthetic = ET.Element(node.tag)
    synthetic.append(base[1])
    synthetic.extend(list(node)[1:])
    return synthetic


def is_chem_row(kids: list[ET.Element]) -> bool:
    """Does this row of siblings read as chemistry (see the module docstring)?"""
    species = 0
    for i, node in enumerate(kids):
        if _scripted_species(node) or _bracketed(kids, i) or _split_bracket(kids, i) is not None:
            return True
        if _is_species_token(node):
            species += 1
            if i > 0 and (_is_species_token(kids[i - 1]) or _scripted_species(kids[i - 1])):
                return True
        if _state_at(kids, i) is not None and i > 0 and _ends_species(kids[i - 1]):
            return True
    return species >= 2


def _is_charge_script(node: ET.Element) -> bool:
    """A charge on an empty base, which is how OpenStax writes the charge of a species that
    already has a count: `msub(MnO, 4)` then `msup(<empty>, −)`."""
    return (
        node.tag == "msup"
        and len(node) == 2
        and not _text(node[0])
        and len(node[0]) == 0
        and _charge_words(node[1]) is not None
    )


def _ends_species(node: ET.Element) -> bool:
    return (
        _is_species_token(node)
        or _scripted_species(node)
        or _is_closing(node)
        or _is_charge_script(node)
    )


def _letters(text: str) -> str:
    """ "Na" -> "N a": each letter said on its own, capitals as they are."""
    return " ".join(text)


def _charge_words(node: ET.Element) -> list[str] | None:
    """A raised charge ("+", "2−", "3+") as words: "plus", "2 minus"."""
    match = _CHARGE.match(_text(node).replace(" ", ""))
    if not match:
        return None
    digits, sign = match.groups()
    return [*([digits] if digits else []), _SIGNS[sign]]


def _species_words(node: ET.Element) -> list[str]:
    text = _text(node)
    if _is_electron(node):
        count = text[:-1]
        return [*([count] if count else []), "e"]
    if node.tag == "mtext" and _formula_text(node) is not None:
        return [
            _letters(piece) if piece[0].isalpha() else piece
            for piece in re.findall(r"[A-Z][a-z]?|\d+", text)
        ]
    return [_letters(text)]


def _is_count(node: ET.Element) -> bool:
    return node.tag == "mn" and _text(node).isdigit()


def _base_words(base: ET.Element, speak: Speak) -> list[str]:
    """The words of what a script hangs on: a token, another scripted species, or a group."""
    if _is_species_token(base):
        return _species_words(base)
    if base.tag in _SCRIPTS:
        return scripted_words(base, speak) or speak(base)
    if base.tag == "mrow":
        return _read_row(list(base), speak)
    return speak(base)


def scripted_words(node: ET.Element, speak: Speak) -> list[str] | None:
    """A scripted species as a teacher says it: counts and charges are numbers and signs.
    None when it is not one (or its script is not a count or a charge)."""
    if not _scripted_species(node):
        return None
    if node.tag == "msub":
        sub, sup = node[1], None
    elif node.tag == "msup":
        sub, sup = None, node[1]
    else:
        sub, sup = node[1], node[2]
    if sub is not None and not (_is_count(sub) or _species_base(sub)):
        return None
    charge = None
    if sup is not None:
        charge = _charge_words(sup)
        if charge is None:
            return None
    words = _base_words(node[0], speak)
    if sub is not None:
        words += speak(sub)
    return [*words, *(charge or [])]


def _concentration(kids: list[ET.Element], i: int, speak: Speak) -> tuple[list[str], int] | None:
    """`[ species ]` as flat siblings: "the concentration of ..." and the siblings used."""
    if not (kids[i].tag == "mo" and _text(kids[i]) == "["):
        return None
    single = _bracketed(kids, i)
    if single is not None:
        return ["the concentration of", *_read_row([kids[i + 1]], speak)], single
    for j in range(i + 2, min(len(kids), i + 9)):
        if kids[j].tag == "mo" and _text(kids[j]) == "]":
            inner = kids[i + 1 : j]
            if (
                inner
                and not any("(" in _text(k) or "[" in _text(k) for k in inner)
                and is_chem_row(inner)
                and all(
                    _is_species_token(k) or _scripted_species(k) or _is_closing(k) for k in inner
                )
            ):
                return ["the concentration of", *_read_row(inner, speak)], j - i + 1
            return None
    return None


def _arrow_words(node: ET.Element) -> list[str] | None:
    text = _text(node)
    if node.tag not in {"mo", "mi"}:
        return None
    if text in EQUILIBRIUM:
        return ["is in equilibrium with"]
    if text in FORWARD:
        return ["reacts to form"]
    if text in BACKWARD:
        return ["is formed from"]
    return None


def _over_arrow(node: ET.Element) -> list[str] | None:
    """An arrow with a label over it: "reacts, with heat, to form"."""
    if node.tag != "mover" or len(node) != 2:
        return None
    arrow = _arrow_words(node[0])
    label = _text(node[1])
    if arrow is None or not label:
        return None
    with_ = "with heat" if label in _HEAT else f"with {label}"
    if arrow == ["reacts to form"]:
        return ["reacts,", with_ + ", to form"]
    if arrow == ["is in equilibrium with"]:
        return ["is in equilibrium with,", with_]
    return None


def _read_row(kids: list[ET.Element], speak: Speak) -> list[str]:
    """The words of a row already known to be chemistry."""
    words: list[str] = []
    i = 0
    while i < len(kids):
        found = read_at(kids, i, speak)
        if found is None:
            words += speak(kids[i])
            i += 1
        else:
            words += found[0]
            i += found[1]
    return words


def read_at(kids: list[ET.Element], i: int, speak: Speak) -> tuple[list[str], int] | None:
    """The chemistry reading of what starts at kids[i] (a species, an ion, a state symbol, an
    arrow, a concentration) and the siblings it covers, or None. The caller has decided the
    row is chemistry (`is_chem_row`)."""
    node = kids[i]
    state = _state_at(kids, i)
    if state is not None and i > 0 and _ends_species(kids[i - 1]):
        # A comma after it when more follows, so a plus after "aqueous" is the sign between
        # two species and not a charge ("A g plus aqueous, plus C l minus aqueous").
        more = i + state[1] < len(kids)
        return [state[0] + ("," if more else "")], state[1]
    concentration = _concentration(kids, i, speak)
    if concentration is not None:
        return concentration
    split = _split_bracket(kids, i)
    if split is not None:
        words = scripted_words(split, speak)
        if words is not None:
            return ["the concentration of", *words], 2
    if i > 0 and _ends_species(kids[i - 1]) and _is_charge_script(node):
        charge = _charge_words(node[1]) or []
        following = kids[i + 1] if i + 1 < len(kids) else None
        if following is not None and following.tag == "mo" and _text(following) == "+":
            charge[-1] += ","
        return charge, 1
    scripted = scripted_words(node, speak)
    if scripted is not None:
        following = kids[i + 1] if i + 1 < len(kids) else None
        if (
            node.tag in {"msup", "msubsup"}
            and following is not None
            and following.tag == "mo"
            and _text(following) == "+"
        ):
            scripted[-1] += ","  # "N a plus, plus C l minus": a charge, then the sign
        return scripted, 1
    if _is_species_token(node):
        return _species_words(node), 1
    arrow = _over_arrow(node) or _arrow_words(node)
    if arrow is not None:
        return arrow, 1
    return None
