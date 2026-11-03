# ruff: noqa: RUF001, RUF002
"""Chemistry species of a transcription against the PDF's text layer.

The symbol bag (`equation_step.text_layer_symbols_differ`) counts letters and digits, so it
cannot see what chemistry is made of: the order of an element and its count. `H2O` and
`2HO` have the same bag (a subscript read as a coefficient), and `Ag+(aq)` and `Ag−(aq)`
differ by one sign that the signs check only sees when the rest agrees. This check reads
both sides as *species* (a coefficient, a formula with its counts, a charge, a state) and
*arrows*, in order, and compares them.

The text layer is flat: a PDF prints `SO4` with a lowered 4 and `SO4 2−` with a raised 2 as
the same characters `SO42−`, and the transcription's MathML flattens to the same string. So
both sides go through one parser (`species_of`) and each species is compared as written, in
order; a reading is never asked to tell `Fe3+` from `Fe3 +` when the page cannot. Spacing is
the one thing taken from the sides: in the text layer a `+` between words is the operator and
a sign glued to a formula is its charge; in the MathML a sign in a superscript is the charge
and an operator token is a word of its own.

What it catches, each from a case seen in the dev and held-out runs (docs/chemistry.md):

- *elements and counts*: another element, a count that moved (`H2O` for `H2O2`);
- *subscript read as a coefficient* (`2HO` for `H2O`, `2 H` for `H2`) and the reverse;
- *charges and their signs*: `SO42−` read as `SO42+`, a charge dropped or added;
- *states*: `(aq)`, `(s)`, `(l)`, `(g)` changed, dropped or added;
- *arrows*: a reaction arrow read as an equilibrium arrow or the other way, missing or extra;
- *a neighbour word swallowed into a formula* (the reading holds a species the PDF has
  not, or letters stuck to a formula so it is no longer one);
- *a reaction cut mid-way*: species or an arrow at the end (or the start) that the reading
  lacks.

It runs only when the PDF text has evidence only chemistry has (`has_evidence`): a state
symbol, a charge on element symbols, or a formula of two element symbols with a count
(`H2O`, `CO2`), so a mathematics equation never reaches it. A formula that reads as
elements but has none of that (`HCl`, `NaOH`, a lone `C`) is *bare* and counts only in a
reaction (an arrow in the same text). Anything it cannot parse is ignored, never failed.

Pure functions of text and MathML; no model, no network. The detection half of chemistry
in running text is `pipeline.chem` (which has the script kinds that a PDF row gives and an
element's text does not).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

import defusedxml.ElementTree as DET

from readablestem.pipeline.speech_nuclides import ELEMENTS

Kind = Literal[
    "elements", "coefficient", "charge", "state", "arrow", "extra", "missing", "swallowed"
]

STATES = frozenset({"aq", "s", "l", "g"})
_MINUS = "−"
_MINUS_LIKE = "−–—‒‐‑-"
_FORWARD = "→⟶⇒⟹⟼"
_EQUILIBRIUM = "⇌⇄⇋⇆"
_BACK = "←⟵⇐⟸"
_BOTH = "↔⟷⇔"
_ARROW_GLYPHS = _FORWARD + _EQUILIBRIUM + _BACK + _BOTH + "⇀↽"
# An arrow with its shaft: "−→" is a minus glued to the head in some fonts. The shaft only
# counts when it starts a word, so "Cl−→" keeps the charge.
_SHAFTED = _FORWARD + _BACK + _BOTH  # an equilibrium arrow has no shaft: "HSO4 −⇌" is a charge
_ARROW = re.compile(
    rf"(?:(?<=\s)|^)[{re.escape(_MINUS_LIKE)}=]*[{_SHAFTED}][{_ARROW_GLYPHS}]*|[{_ARROW_GLYPHS}]+"
)
_SEPARATOR = re.compile(r"(?<!\d)[,;](?!\d)|(?<=\d)[,;](?!\d)|(?<!\d)[,;](?=\d)|[<>≈≤≥=]")
_LABEL = re.compile(r"(?<![\w)\]+−])\(([a-fh-km-rt-z]|iii?|iv)\)")
# A charge followed by a glued operator ("OH−+ H+", "CO3 2−+ H+").
_CHARGE_PLUS = re.compile(r"\s*([+−])\+(?=\s|$)")
_VARIABLE = "·"  # stands for a variable the PDF prints in a math italic or bold style
_VARIABLE_BEFORE = re.compile(r"(?<![A-Za-z])([a-z·])(?=\[)")
_VARIABLE_AFTER = re.compile(r"\](?=[a-z·])")
_BETWEEN = re.compile(r"\](\d*)\[")
# A charge the text layer put in front of its formula ("−CH3 CO2"): the sign is dropped.
_DISPLACED = re.compile(r"(?:(?<=\s)|^)[+−](?=[A-Z][a-z]?\d?)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?(?:/\d+)?")
_WORD_END = ".,;:!?\"'’”“‘"
_STATE = re.compile(r"\((aq|s|l|g)\)$")
# What a reaction's coefficient can be (a decimal like 0.077 is a value, not a coefficient).
_COEFFICIENT_ONLY = re.compile(r"\d{1,3}(?:/\d+)?|\d\.5")
# A letter with a number that is no element ("T1", "V2"): a variable's subscript, not chemistry.
_VARIABLE_WORD = re.compile(r"^[A-Za-z]\d+$")
_COEFFICIENT = re.compile(r"^(\d+(?:[.,]\d+)?(?:/\d+)?)(?=[A-Z(\[])")
_MATHML_TOKENS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
_SCRIPTS = frozenset({"msub", "msup", "msubsup"})
_OVER = frozenset({"munder", "mover", "munderover"})
_GLUED = frozenset({"(", ")", "[", "]", ",", ".", ";", ":"})
# A state with its conditions, "(aq, 1 M)" or "(g, 1 atm)", is its state; the cell notation's
# bars separate species and are not part of any.
_CONDITIONS = re.compile(r"\((aq|s|l|g)\s*,[^()]*\)")
_BARS = str.maketrans(dict.fromkeys("│║|∣‖", " "))
_MAX_WORDS = 400  # a longer text is not an equation; the check leaves it alone


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


# ---------------------------------------------------------------------------- species


@dataclass(frozen=True)
class Species:
    """One chemical species as the page writes it. `body` is the flat formula with its
    counts and charge (`SO42−`), `coefficient` the number in front (digits only, a fraction
    bar dropped), `state` one of `STATES`. `strong` is chemistry-only evidence: a state, a
    charge, or two element symbols with a count."""

    coefficient: str
    body: str
    state: str
    strong: bool

    def __str__(self) -> str:
        state = f"({self.state})" if self.state else ""
        return (
            f"{self.coefficient} {self.body}{state}".strip()
            if self.coefficient
            else (f"{self.body}{state}")
        )


@dataclass(frozen=True)
class Arrow:
    kind: str  # "forward", "equilibrium", "back", "both"

    def __str__(self) -> str:
        return {"forward": "→", "equilibrium": "⇌", "back": "←", "both": "↔"}[self.kind]


Token = Species | Arrow


def _element_at(text: str, i: int) -> int:
    """Length of the element symbol at `i` (1 or 2), or 0."""
    if i >= len(text) or not ("A" <= text[i] <= "Z"):
        return 0
    if i + 1 < len(text) and "a" <= text[i + 1] <= "z" and text[i : i + 2] in ELEMENTS:
        return 2
    return 1 if text[i] in ELEMENTS else 0


def _body_end(text: str, i: int, depth: int = 0) -> int | None:
    """End of the formula body that starts at `i` (element symbols with counts and bracketed
    groups with counts), or None when `text[i:]` does not start with one. A group "(aq)" is
    a state, never a group: the caller cuts it off first."""
    start = i
    while i < len(text):
        n = _element_at(text, i)
        if n:
            i += n
        elif text[i] in "([":
            closer = ")" if text[i] == "(" else "]"
            inner = _body_end(text, i + 1, depth + 1)
            if inner is None or inner >= len(text) or text[inner] != closer:
                return None
            i = inner + 1
        else:
            break
        while i < len(text) and text[i].isdigit():
            i += 1
    if i == start:
        return None
    if depth and i < len(text) and text[i] not in ")]":
        return None
    return i


def _two_elements_with_count(body: str) -> bool:
    symbols: set[str] = set()
    counted = False
    i = 0
    while i < len(body):
        n = _element_at(body, i)
        if n:
            symbols.add(body[i : i + n])
            i += n
            if i < len(body) and body[i].isdigit():
                counted = True
        else:
            i += 1
    return len(symbols) >= 2 and counted


def species_of(word: str) -> Species | None:
    """Read one word as a species, or None. The word is NFKC-folded and its sentence marks
    are cut off; a coefficient glued to it ("2H2O") is taken."""
    word = word.strip().rstrip(_WORD_END)
    while word.endswith(")") and word.count(")") > word.count("("):
        word = word[:-1]  # the bracket of the sentence around the formula
    if word.startswith("["):
        # a concentration, "[H3O+]" or a rate law's "[NO2]2": the species inside; a
        # complex ion ("[Cu(NH3)4]2+") has a charge or state after its bracket
        close = _find_close(word, 0)
        if close is not None and re.fullmatch(r"\d*", word[close + 1 :]):
            return species_of(word[1:close])
    coefficient = ""
    match = _COEFFICIENT.match(word)
    if match:
        coefficient = match.group(1)
        word = word[match.end() :]
    state = ""
    found = _STATE.search(word)
    if found and found.start() > 0:
        state = found.group(1)
        word = word[: found.start()]
    end = _body_end(word, 0)
    if end is None:
        return None
    body, tail = word[:end], word[end:]
    if tail and not re.fullmatch(r"\d*[+−]", tail):
        return None
    charged = bool(tail)
    strong = bool(state) or charged or _two_elements_with_count(body)
    return Species(coefficient.replace("/", "").replace(",", "."), body + tail, state, strong)


def _find_close(text: str, at: int) -> int | None:
    opener = text[at]
    closer = ")" if opener == "(" else "]"
    depth = 0
    for j in range(at, len(text)):
        if text[j] == opener:
            depth += 1
        elif text[j] == closer:
            depth -= 1
            if depth == 0:
                return j
    return None


def _open_square(text: str) -> bool:
    return text.count("[") > text.count("]")


def _joined(words: list[str]) -> list[str]:
    """The text layer breaks a word where the script changes (`H2 O`, `H3 O+`, `SO4 2−`,
    `Na[Al(OH)4 ](aq)`). Join a word that ends in a digit (or leaves a bracket open) with the
    next when the two together are one species; a lone sign is an operator and never joins.
    A concentration's `][` is cut again afterwards."""
    out: list[str] = []
    k = 0
    while k < len(words):
        current = words[k]
        k += 1
        while k < len(words):
            nxt = words[k]
            if any(ch in _ARROW_GLYPHS for ch in nxt) or nxt.endswith(":"):
                break  # a label ("C:") is a word of its own
            if nxt in {"+", _MINUS}:
                # a lone sign is the operator, unless nothing follows it or an arrow does
                # ("NH4 +" at the end, "NH4 + ⇌ H+"): then it is the charge
                after = words[k + 1] if k + 1 < len(words) else None
                if after is not None and not any(ch in _ARROW_GLYPHS for ch in after):
                    break
            elif not (current[-1:].isdigit() or _open_square(current)):
                break
            merged = current + nxt
            if species_of(merged) is None and not _open_square(current):
                break
            current = merged
            k += 1
        out.extend(current.replace("][", "] [").split())
    return out


def _arrow_kind(glyphs: str) -> str:
    if any(g in _EQUILIBRIUM or g in "⇀↽" for g in glyphs):
        return "equilibrium"
    if any(g in _BOTH for g in glyphs):
        return "both"
    if all(g in _BACK for g in glyphs):
        return "back"
    return "forward"


def _fold(text: str) -> str:
    """NFKC, with one exception: a letter in a mathematical italic or bold style is a
    variable (TeX and Chromium print them so), never an element, so it is not folded to the
    plain letter; the italic state letters (`𝑎𝑞`, `𝑙`, `𝑔`, `𝑠`) are chemistry's own."""
    out: list[str] = []
    for ch in text:
        folded = unicodedata.normalize("NFKC", ch)
        if 0x1D400 <= ord(ch) <= 0x1D7FF or ch == "ℎ":
            out.append(folded if folded in "aqslg" and ch.isalpha() else _VARIABLE)
        else:
            out.append(_MINUS if folded in _MINUS_LIKE else folded)
    text = _CONDITIONS.sub(r"(\1)", _CHARGE_PLUS.sub(r"\1 +", "".join(out)))
    text = _DISPLACED.sub("", text)
    # rate laws: a variable or an exponent letter stuck to a concentration ("k[NO]m[Cl2]n")
    text = _VARIABLE_BEFORE.sub(r"\1 ", text)
    text = _VARIABLE_AFTER.sub("] ", text)
    text = _BETWEEN.sub(r"]\1 [", text)
    # a list's comma or semicolon and an item label "(a)" separate species ("CO3 2−, F−")
    return _LABEL.sub(" ", _SEPARATOR.sub(" ", text))


def tokens_of(text: str) -> list[Token] | None:
    """Species and arrows of a flat text in order (operators and other words are dropped:
    a word that is not a species ends the coefficient before it). None: too long."""
    folded = _CONDITIONS.sub(r"(\1)", _fold(text)).translate(_BARS)
    out: list[Token] = []
    pieces: list[str] = []
    cursor = 0
    for match in _ARROW.finditer(folded):
        pieces.append(folded[cursor : match.start()])
        pieces.append(match.group())
        cursor = match.end()
    pieces.append(folded[cursor:])
    words: list[str] = []
    for piece in pieces:
        if (
            piece
            and all(ch in _ARROW_GLYPHS + _MINUS + "=" for ch in piece)
            and any(ch in _ARROW_GLYPHS for ch in piece)
        ):
            words.append(piece)
        else:
            words.extend(piece.split())
    if len(words) > _MAX_WORDS:
        return None
    words = _joined(words)
    numbers = ""  # numeric words just before the next word (a coefficient, or a nuclide's)
    for word in words:
        if any(ch in _ARROW_GLYPHS for ch in word) and all(
            ch in _ARROW_GLYPHS + _MINUS + "=" for ch in word
        ):
            out.append(Arrow(_arrow_kind(word)))
            numbers = ""
            continue
        stripped = word.rstrip(_WORD_END)
        if _NUMBER.fullmatch(stripped):
            numbers += stripped
            continue
        state = _STATE.fullmatch(stripped)
        if state and out and isinstance(out[-1], Species) and not out[-1].state:
            last = out[-1]
            out[-1] = Species(last.coefficient, last.body, state.group(1), True)
            numbers = ""
            continue
        species = species_of(word)
        if species is None:
            numbers = ""
            continue
        if (
            numbers
            and not species.coefficient
            and not word.startswith("[")  # a number before a concentration is a value
            and _COEFFICIENT_ONLY.fullmatch(numbers)
        ):
            species = Species(
                numbers.replace("/", "").replace(",", "."),
                species.body,
                species.state,
                species.strong,
            )
        numbers = ""
        out.append(species)
    return out


# ------------------------------------------------------------------------ MathML side


def mathml_flat(mathml: str) -> str:
    """The MathML as the text layer would print it: tokens in reading order, a script glued
    to its base, an operator or arrow between spaces, a prescript (a nuclide's mass and
    atomic number) before its base, text kept as written."""
    root = DET.fromstring(mathml, forbid_dtd=True)
    parts: list[str] = []

    def leaf(elem: Any, in_script: bool) -> None:
        text = unicodedata.normalize("NFKC", elem.text or "").strip()
        if not text:
            return
        if _local(elem.tag) == "mo" and not in_script and text not in _GLUED:
            parts.append(f" {text} ")
        elif _local(elem.tag) == "mtext":
            parts.append(f" {text} " if " " in text else text)
        else:
            parts.append(text)

    def walk(elem: Any, in_script: bool) -> None:
        name = _local(elem.tag)
        children = list(elem)
        if name in {"annotation", "annotation-xml"}:
            return  # a source annotation repeats the formula
        if name in _MATHML_TOKENS:
            leaf(elem, in_script)
        elif name in _SCRIPTS and children:
            walk(children[0], in_script)
            for child in children[1:]:
                walk(child, True)
        elif name == "mmultiscripts" and children:
            rest = children[1:]
            split = next((k for k, c in enumerate(rest) if _local(c.tag) == "mprescripts"), None)
            post, pre = (rest, []) if split is None else (rest[:split], rest[split + 1 :])
            # prescripts are (sub, sup) pairs: a nuclide reads mass (sup) before atomic number
            for sub, sup in zip(pre[0::2], pre[1::2], strict=False):
                for c in (sup, sub):
                    walk(c, True)
                    parts.append(" ")
            walk(children[0], in_script)
            for c in post:
                walk(c, True)
        elif name in _OVER and children:
            walk(children[0], in_script)
            for child in children[1:]:
                parts.append(" ")
                walk(child, False)
        elif name == "mfrac":
            for child in children:
                parts.append(" ")
                walk(child, in_script)
                parts.append(" ")
        elif name in {"mspace", "mtd", "mtr", "mtable"}:
            parts.append(" ")
            for child in children:
                walk(child, in_script)
            parts.append(" ")
        else:
            for child in children:
                walk(child, in_script)

    walk(root, False)
    return "".join(parts)


# ----------------------------------------------------------------------- the check


@dataclass(frozen=True)
class SpeciesDiff:
    """What the species of the text layer and of the transcription do not agree on.
    `kinds` are the sorted kinds of difference; `rows` are (kind, in the PDF, in the
    transcription) with an empty string for a side that has nothing there."""

    rows: tuple[tuple[Kind, str, str], ...]

    @property
    def differ(self) -> bool:
        return bool(self.rows)

    @property
    def kinds(self) -> tuple[Kind, ...]:
        return tuple(sorted({row[0] for row in self.rows}))


def has_evidence(tokens: list[Token]) -> bool:
    """Chemistry-only evidence in a text's tokens: a state, a formula of two element symbols
    with a count (`H2O`, `CO2`), two charged species, or a charged species in a reaction
    (an arrow). A lone `Na+` or `B+` is not enough: a letter and a sign are algebra often
    enough."""
    species = [t for t in tokens if isinstance(t, Species)]
    if any(t.state or _two_elements_with_count(t.body.rstrip("+" + _MINUS)) for t in species):
        return True
    charged = [t for t in species if t.body[-1:] in "+" + _MINUS]
    arrow = any(isinstance(t, Arrow) for t in tokens)
    return len(charged) >= 2 or (bool(charged) and arrow)


def _keep(tokens: list[Token]) -> list[Token]:
    """Bare words (one element, no count, charge or state: `HCl`, a lone `C`) are species
    only inside a reaction."""
    reaction = any(isinstance(t, Arrow) for t in tokens)
    kept: list[Token] = []
    for t in tokens:
        if isinstance(t, Species) and not t.strong:
            bare = t.body == _letters(t.body)
            if len(t.body) == 1 or (not reaction and bare):
                continue  # a unit or a variable (V, M, K), or a bare word outside a reaction
            if bare and len(t.coefficient) >= 2:
                # a nuclide's mass number: the text layer prints it apart from its atomic
                # number, so only the symbol is compared
                t = Species("", t.body, t.state, t.strong)
        kept.append(t)
    # an arrow with nothing on one side is not a reaction's (a caption's "→ a few ...")
    return [
        t
        for k, t in enumerate(kept)
        if not isinstance(t, Arrow)
        or (
            any(isinstance(x, Species) for x in kept[:k])
            and any(isinstance(x, Species) for x in kept[k + 1 :])
        )
    ]


def _letters(body: str) -> str:
    return "".join(ch for ch in body if ch.isalpha())


def _digits_of(species: Species) -> Counter[str]:
    return Counter(ch for ch in species.coefficient + species.body if ch.isdigit())


def _kind(pdf: Species, got: Species) -> Kind:
    if _letters(pdf.body) != _letters(got.body):
        return "elements"
    pdf_sign, got_sign = pdf.body[-1:], got.body[-1:]
    signs = "+" + _MINUS
    if pdf.state == got.state and pdf.coefficient == got.coefficient:
        # the same formula with another sign, or one side without the charge
        pdf_core = pdf.body[:-1] if pdf_sign in signs else pdf.body
        got_core = got.body[:-1] if got_sign in signs else got.body
        if (
            pdf_sign != got_sign
            and (pdf_sign in signs or got_sign in signs)
            and pdf_core == got_core
        ):
            return "charge"
    if pdf.coefficient + pdf.body != got.coefficient + got.body and _digits_of(pdf) == _digits_of(
        got
    ):
        return "coefficient"
    if pdf.coefficient != got.coefficient and pdf.body == got.body:
        return "coefficient"
    if pdf.state != got.state and (pdf.coefficient, pdf.body) == (got.coefficient, got.body):
        return "state"
    if pdf.body[-1:] in "+" + _MINUS or got.body[-1:] in "+" + _MINUS:
        return "charge"
    return "elements"


def _segments(tokens: list[Token]) -> list[object]:
    """The species between the arrows, each side of an arrow as a multiset: the text layer
    sets a fraction's numerator and denominator in an order of its own, so the order of
    species within one side of an arrow is not compared (which side of the arrow a species
    is on is)."""
    out: list[object] = []
    side: Counter[tuple[str, ...]] = Counter()
    for t in tokens:
        if isinstance(t, Arrow):
            out.extend([side, t])
            side = Counter()
        else:
            side[_key(t)] += 1
    out.append(side)
    return out


def _signs(text: str) -> Counter[str]:
    return Counter(
        _MINUS if ch in _MINUS_LIKE else ch
        for ch in unicodedata.normalize("NFKC", text)
        if ch in "+" + _MINUS_LIKE
    )


def _describe(token: Token) -> str:
    return str(token)


def _same(a: Token, b: Token) -> bool:
    if isinstance(a, Arrow) or isinstance(b, Arrow):
        return a == b
    return (a.coefficient, a.body, a.state) == (b.coefficient, b.body, b.state)


def _pairs(a: list[Token], b: list[Token]) -> Iterator[tuple[str, list[Token], list[Token]]]:
    matcher = SequenceMatcher(None, [_key(t) for t in a], [_key(t) for t in b], autojunk=False)
    yield from ((op, a[i1:i2], b[j1:j2]) for op, i1, i2, j1, j2 in matcher.get_opcodes())


def _key(token: Token) -> tuple[str, ...]:
    if isinstance(token, Arrow):
        return ("arrow", token.kind)
    return ("species", token.coefficient, token.body, token.state)


def _has_variable_words(text: str) -> bool:
    """A word like `T1` or `x2` in the text: a variable with a subscript, so this is
    mathematics that happens to hold letters of the periodic table."""
    return any(
        _VARIABLE_WORD.match(w) and species_of(w) is None
        for w in (word.rstrip(_WORD_END) for word in _fold(text).split())
    )


def compare_species(text_layer: str | None, mathml: str) -> SpeciesDiff | None:
    """Do the species and arrows of the text layer and the transcription agree? None: the
    text layer has no chemistry evidence (or none, or is too long), so the check does not
    apply."""
    if not text_layer:
        return None
    pdf_all = tokens_of(text_layer)
    if pdf_all is None or not has_evidence(pdf_all) or _has_variable_words(text_layer):
        return None
    try:
        got_all = tokens_of(mathml_flat(mathml))
    except Exception:
        return None
    if got_all is None:
        return None
    pdf, got = _keep(pdf_all), _keep(got_all)
    if _segments(pdf) == _segments(got):
        return SpeciesDiff(())
    # A text layer sometimes puts a charge's sign away from its formula; a charge that
    # differs is reported only when the page's signs, counted, differ as well (a flipped or
    # dropped sign always changes the count).
    same_signs = _signs(text_layer) == _signs(mathml_flat(mathml))
    rows: list[tuple[Kind, str, str]] = []
    for op, left, right in _pairs(pdf, got):
        if op == "equal":
            continue
        if op == "replace" and len(left) == len(right):
            for a, b in zip(left, right, strict=True):
                if isinstance(a, Arrow) or isinstance(b, Arrow):
                    rows.append(("arrow", _describe(a), _describe(b)))
                elif not _same(a, b):
                    kind = _kind(a, b)
                    if kind == "charge" and same_signs:
                        continue
                    rows.append((kind, _describe(a), _describe(b)))
        elif op == "replace":
            rows.extend(_uneven(left, right))
        elif op == "delete":
            rows.extend(
                ("arrow" if isinstance(t, Arrow) else "missing", _describe(t), "") for t in left
            )
        else:
            rows.extend(
                ("arrow" if isinstance(t, Arrow) else "extra", "", _describe(t)) for t in right
            )
    return SpeciesDiff(tuple(rows))


def _uneven(left: list[Token], right: list[Token]) -> list[tuple[Kind, str, str]]:
    """A run where the two sides have different numbers of species: a neighbour taken in
    (more on the transcription's side), a reaction cut (fewer), or both."""
    kind: Kind = "swallowed" if len(right) > len(left) else "missing"
    return [(kind, " ".join(map(str, left)), " ".join(map(str, right)))]
