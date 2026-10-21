r"""Chemistry in text: finding formulas, ions and reactions in a PDF's text layer, and the
`\ce{...}` (mhchem) subset the equation step accepts. Pure string work; no PDF, no model.

Two jobs, one module because they share one idea of what a chemical formula is
(docs/chemistry.md):

**Detection** (`label_words`, `formulas`, `reactions`). Layout gives each prose row as words
whose characters carry a script kind ("sub", "sup" or None, as `layout._script_glyphs` found
them). A word is a *formula* when its letters read as element symbols and it carries evidence
only chemistry has: a lowered number on an element or a bracket (`H2O`, `Al2(SO4)3`), a raised
charge (`Na+`, `SO4 2-`, `Fe3+`), or a state symbol (`NaCl(aq)`). A word that reads as elements
but has none of those (`HCl`, `NaOH`) is *bare*: it only counts inside a reaction, an arrow with
formulas on both sides. The scripts are required: plain letters and digits that a PDF flattened
(`H2O` set at one size) cannot be told from a word, so they are left alone. A word that is not
made of element symbols is never a formula (`cm3`, `m2`, `Kw`, `Nature1`), and a raised number
with no sign is an exponent or a footnote, not a charge.

**Conversion** (`ce_to_latex`, `convert_ce_commands`). The equation model may write chemistry as
`\ce{H2O}`, `\ce{SO4^2-}`, `\ce{2 H2 + O2 <=> 2 H2O}`. The strict LaTeX converter cannot read it,
so the subset below is turned into plain LaTeX first. Anything outside the subset raises
`CeError` and the caller leaves the `\ce` alone, which the LaTeX check then refuses: a reading we
cannot convert is never guessed.

Supported: species of element symbols with lowered counts (`H2O`, `C6H12O6`), brackets and
parentheses with counts (`Ca(OH)2`, `[Cu(NH3)4]^2+`), charges (`Na+`, `OH-`, `SO4^2-`,
`Fe^{3+}`), state symbols (`(aq)`, `(s)`, `(l)`, `(g)`), coefficients (`2`, `0.5`, `1/2`), `+`
between species, the electron and particles (`e-`, `n`, `p`), hydrates (`CuSO4.5H2O`), bonds
(`-`, `=`, `#`), nuclide prefixes (`^{14}_{6}C`, `^{0}_{-1}e`), and the arrows `->` `-->` `<-`
`<->` `<=>`, with a label (`->[\Delta]`, `->[heat]`). Not supported (`CeError`): `<=>>`, `<<=>`,
`^` and `v` as gas and precipitate marks, `$...$`, concentrations inside a state symbol
(`(aq, 0.10 M)`), two-part arrow labels, and any text that is not a species.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from readablestem.pipeline.speech_nuclides import ELEMENTS

ScriptKind = Literal["sup", "sub"]
WordLabel = Literal["formula", "bare", "state", "arrow", "plus", "number", "other"]

STATE_SYMBOLS = frozenset({"aq", "s", "l", "g"})
# Arrow glyphs a reaction is written with. The shaft of a long arrow ("−→", set as a minus
# glued to the head in some fonts) is a dash in front of the head.
ARROW_HEADS = frozenset("→⟶⇌⇄↔⟷←⟵⇋⇒⟹")
_SHAFTS = frozenset("−-–—=═")
_HYDRATE_DOTS = frozenset("·•∙⋅.")
_TRIM_END = frozenset(",.;:!?\"'’”")
_TRIM_START = frozenset("\"'‘“")
_MINUS_SIGNS = "−–-"
_CHARGE = re.compile(r"^\d*[+−–-]$")  # "+", "2-"; not "-1", which is an exponent
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?(?:/\d+)?")


# ============================================================================ detection


@dataclass(frozen=True)
class Word:
    """One word of a prose row: its text, the script kind of each character, and where it
    sits in the row (character indices, end exclusive)."""

    start: int
    end: int
    text: str
    kinds: tuple[ScriptKind | None, ...]


@dataclass(frozen=True)
class Labelled:
    """A word with what chemistry makes of it. `lo`/`hi` are offsets into the word's text
    after sentence marks and wrapping brackets are cut off."""

    word: Word
    label: WordLabel
    lo: int
    hi: int

    @property
    def first(self) -> int:
        return self.word.start + self.lo

    @property
    def last(self) -> int:
        return self.word.start + self.hi


def _is_charge(text: str) -> bool:
    return bool(_CHARGE.match(text))


def _find_close(text: str, at: int) -> int | None:
    """Index of the bracket closing the one at `at` (same kind), or None."""
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


@dataclass
class _Seen:
    atoms: int = 0
    subs: int = 0
    charges: int = 0
    states: int = 0


def _symbol_at(text: str, kinds: Sequence[ScriptKind | None], i: int) -> int:
    """Length (1 or 2) of the element symbol at `i`, or 0."""
    if kinds[i] is not None or not ("A" <= text[i] <= "Z"):
        return 0
    if (
        i + 1 < len(text)
        and kinds[i + 1] is None
        and "a" <= text[i + 1] <= "z"
        and text[i : i + 2] in ELEMENTS
    ):
        return 2
    return 1 if text[i] in ELEMENTS else 0


def _scripts_at(
    text: str, kinds: Sequence[ScriptKind | None], i: int, kind: ScriptKind
) -> tuple[str, int]:
    j = i
    while j < len(text) and kinds[j] == kind:
        j += 1
    return text[i:j], j


def _species(text: str, kinds: Sequence[ScriptKind | None], lo: int, hi: int, seen: _Seen) -> bool:
    """Does `text[lo:hi]` read as a formula body: element symbols, lowered counts, brackets
    with counts, a raised charge, hydrate dots, state symbols? Fills `seen`."""
    i = lo
    base = False  # something a script can belong to
    while i < hi:
        c, kind = text[i], kinds[i]
        size = _symbol_at(text, kinds, i)
        if size and i + size <= hi:
            seen.atoms += 1
            base = True
            i += size
        elif kind is None and c in "([":
            close = _find_close(text, i)
            if close is None or close >= hi:
                return False
            inner = text[i + 1 : close]
            if c == "(" and inner in STATE_SYMBOLS and base:
                if any(kinds[k] is not None for k in range(i + 1, close)):
                    return False
                seen.states += 1
                base = False  # nothing hangs on a state symbol
            elif _species(text, kinds, i + 1, close, seen):
                base = True
            else:
                return False
            i = close + 1
        elif kind == "sub":
            digits, j = _scripts_at(text, kinds, i, "sub")
            if not base or not digits.isdigit():
                return False
            seen.subs += 1
            i = j
        elif kind == "sup":
            raised, j = _scripts_at(text, kinds, i, "sup")
            if not base or not _is_charge(raised):
                return False
            seen.charges += 1
            i = j
        elif kind is None and c in _HYDRATE_DOTS and base and i + 1 < hi:
            j = i + 1
            while j < hi and kinds[j] is None and text[j].isdigit():
                j += 1
            if c == "." and j == i + 1:
                return False  # a full stop is a hydrate dot only when a count follows
            i = j
            base = False
        else:
            return False
    return seen.atoms > 0


def _wrapped(text: str, kinds: Sequence[ScriptKind | None], lo: int, hi: int) -> tuple[int, int]:
    """`(lo, hi)` without a pair of parentheses that wraps the whole formula: "(NaHCO3)" is
    a formula in parentheses, but "(SO4)3" and "Ca(OH)2" are not wrapped."""
    if (
        hi - lo > 2
        and text[lo] == "("
        and text[hi - 1] == ")"
        and kinds[hi - 1] is None
        and _find_close(text, lo) == hi - 1
        and text[lo + 1 : hi - 1] not in STATE_SYMBOLS
    ):
        return lo + 1, hi - 1
    return lo, hi


def _trim(text: str, kinds: Sequence[ScriptKind | None]) -> tuple[int, int]:
    """Bounds of the word without sentence marks and quotes at its edges, without a bracket
    the cut left unmatched, and without parentheses wrapped around the whole formula."""
    lo, hi = 0, len(text)
    while lo < hi and kinds[lo] is None and text[lo] in _TRIM_START:
        lo += 1
    while hi > lo and kinds[hi - 1] is None and text[hi - 1] in _TRIM_END:
        hi -= 1
    changed = True
    while changed and lo < hi:
        changed = False
        if text[lo] in "([" and kinds[lo] is None and _find_close(text, lo) is None:
            lo += 1
            changed = True
        elif text[hi - 1] in ")]" and kinds[hi - 1] is None:
            closer = text[hi - 1]
            opener = "(" if closer == ")" else "["
            if text[lo:hi].count(closer) > text[lo:hi].count(opener):
                hi -= 1
                while hi > lo and kinds[hi - 1] is None and text[hi - 1] in _TRIM_END:
                    hi -= 1
                changed = True
    return _wrapped(text, kinds, lo, hi) if lo < hi else (lo, hi)


def label_word(word: Word) -> Labelled:
    """What chemistry makes of one word (see the module docstring)."""
    text, kinds = word.text, word.kinds
    plain = all(k is None for k in kinds)
    if not text:
        return Labelled(word, "other", 0, 0)
    if plain and text == "+":
        return Labelled(word, "plus", 0, 1)
    if (
        plain
        and len(text) <= 3
        and text[-1] in ARROW_HEADS
        and all(c in _SHAFTS for c in text[:-1])
    ):
        return Labelled(word, "arrow", 0, len(text))
    if plain and _NUMBER.fullmatch(text):
        return Labelled(word, "number", 0, len(text))
    lo, hi = _trim(text, kinds)
    if hi <= lo:
        return Labelled(word, "other", 0, 0)
    body, body_kinds = text[lo:hi], kinds[lo:hi]
    if (
        len(body) >= 2
        and body[0] == "e"
        and body_kinds[0] is None
        and all(k == "sup" for k in body_kinds[1:])
        and body[1:].lstrip("0123456789") in set(_MINUS_SIGNS)
    ):
        return Labelled(word, "formula", lo, hi)  # the electron, e with a raised minus
    if all(k is None for k in body_kinds) and (body in {f"({s})" for s in STATE_SYMBOLS}):
        return Labelled(word, "state", lo, hi)
    seen = _Seen()
    if not _species(body, body_kinds, 0, len(body), seen):
        return Labelled(word, "other", lo, hi)
    if seen.subs or seen.charges or seen.states:
        return Labelled(word, "formula", lo, hi)
    return Labelled(word, "bare", lo, hi)


def label_words(words: Sequence[Word]) -> list[Labelled]:
    return [label_word(w) for w in words]


def formulas(labelled: Sequence[Labelled]) -> list[tuple[int, int]]:
    """(first character, last character + 1) in the row of each formula by itself: a
    formula word, with a state symbol word right after it taken in."""
    found: list[tuple[int, int]] = []
    for k, item in enumerate(labelled):
        if item.label != "formula":
            continue
        end = item.last
        if k + 1 < len(labelled) and labelled[k + 1].label == "state":
            end = labelled[k + 1].last
        found.append((item.first, end))
    return found


_JOINED = {"formula", "bare", "state", "plus", "number"}


def reactions(labelled: Sequence[Labelled]) -> list[tuple[int, int]]:
    """(first character, last character + 1) of each reaction: an arrow with at least one
    species on each side, the species, the `+` and the coefficients around it taken as far
    as they go, and at least one formula (not only bare words) in all. Several arrows in a
    row make one reaction."""
    spans: list[tuple[int, int]] = []
    n = len(labelled)
    taken = [False] * n
    for a, item in enumerate(labelled):
        if item.label != "arrow" or taken[a]:
            continue
        left = a
        while left > 0 and labelled[left - 1].label in _JOINED and not taken[left - 1]:
            left -= 1
        right = a
        while right + 1 < n and labelled[right + 1].label in _JOINED | {"arrow"}:
            right += 1
        # A reaction starts and ends on a species (or a state symbol after one).
        while (
            left < a
            and labelled[left].label in {"plus", "state", "number"}
            and not (
                labelled[left].label == "number"
                and left + 1 < n
                and labelled[left + 1].label in {"formula", "bare"}
            )
        ):
            left += 1
        while right > a and labelled[right].label in {"plus", "number", "arrow"}:
            right -= 1  # a trailing number belongs to the sentence ("... H2O 2.")
        species = {"formula", "bare"}
        before = [x for x in labelled[left:a] if x.label in species]
        after = [x for x in labelled[a + 1 : right + 1] if x.label in species]
        if not before or not after:
            continue
        if not any(x.label == "formula" for x in labelled[left : right + 1]):
            continue
        for k in range(left, right + 1):
            taken[k] = True
        spans.append((labelled[left].first, labelled[right].last))
    return spans


# ============================================================================ conversion


class CeError(ValueError):
    """A `\\ce{...}` the converter does not read. The message names the rule, not the text."""


_ARROWS: list[tuple[str, str | None]] = [
    ("<=>>", None),
    ("<<=>", None),
    ("<-->", "\\longleftrightarrow"),
    ("<=>", "\\rightleftharpoons"),
    ("<->", "\\longleftrightarrow"),
    ("-->", "\\longrightarrow"),
    ("<--", "\\longleftarrow"),
    ("->", "\\longrightarrow"),
    ("<-", "\\longleftarrow"),
]
_COEFFICIENT = re.compile(r"(\d+(?:\.\d+)?)(?:/(\d+))?")
_GREEK = re.compile(r"\\(alpha|beta|gamma|nu)(?![A-Za-z])")
_STATE = re.compile(r"\((?:aq|s|l|g)\)")
_LABEL_CHARS = re.compile(r"^[A-Za-z0-9 .,\\]+$")
_SIGNS = "+−–-"
_CARET_CHARGE = re.compile(r"\^([+−–-]?)(\d*)([+−–-]?)(?![A-Za-z0-9{])")


def _group(text: str) -> str:
    return text if len(text) == 1 else "{" + text + "}"


def _sign(ch: str) -> str:
    return "+" if ch == "+" else "-"


def _read_braced(s: str, i: int) -> tuple[str, int]:
    """The text inside the braces opening at `s[i]` and the index after the closing one."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
    raise CeError("unbalanced braces")


def _prefix_number(s: str, i: int) -> tuple[str, int] | None:
    """A nuclide prefix number after `^` or `_` at `s[i]`: `{14}`, `{-1}`, `14`."""
    if i < len(s) and s[i] == "{":
        inner, j = _read_braced(s, i)
        inner = inner.replace("−", "-")
        return (inner, j) if re.fullmatch(r"[+-]?\d+|[A-Z]", inner) else None
    match = re.match(r"[+-]?\d+|[A-Z](?![a-z])", s[i:])
    return (match.group(0), i + match.end()) if match else None


def _nuclide_prefix(s: str, i: int) -> tuple[str, int] | None:
    """`^{14}_{6}` or `_{6}^{14}` (either order, either alone) at `s[i]`, in front of a
    letter: the LaTeX with an empty base (`{}^{14}_{6}`) and the index after."""
    sup = sub = None
    j = i
    for _ in range(2):
        if j < len(s) and s[j] in "^_":
            number = _prefix_number(s, j + 1)
            if number is None:
                return None
            if s[j] == "^":
                if sup is not None:
                    return None
                sup = number[0]
            else:
                if sub is not None:
                    return None
                sub = number[0]
            j = number[1]
    if (sup is None and sub is None) or j >= len(s) or not (s[j].isalpha() or s[j] == "\\"):
        return None
    return "{}" + (f"^{_group(sup)}" if sup else "") + (f"_{_group(sub)}" if sub else ""), j


def _caret(s: str, i: int) -> tuple[str, int]:
    """A `^...` after a species at `s[i]`: the LaTeX superscript and the index after."""
    if i + 1 < len(s) and s[i + 1] == "{":
        inner, j = _read_braced(s, i + 1)
        inner = inner.replace("−", "-").replace("–", "-")
        if not re.fullmatch(r"[0-9A-Za-z+*-]+", inner):
            raise CeError("unreadable superscript")
        return "^" + _group(inner), j
    match = _CARET_CHARGE.match(s, i)
    if not match or not any(match.groups()):
        raise CeError("unreadable superscript")
    lead, digits, tail = match.groups()
    sign = _sign(lead or tail) if (lead or tail) else ""
    return "^" + _group(digits + sign), match.end()


def _bond_next(s: str, i: int) -> bool:
    """Is what follows a `-` (ending at `i`) a bond's other end, a letter or a bracket, and
    not a state symbol: `C-C` and `C-(CH3)` are bonds, `Cl-(aq)` is a charge?"""
    after = s[i : i + 1]
    return bool(after) and (after.isalpha() or after in "([") and not _STATE.match(s, i)


def _species_latex(s: str, i: int) -> tuple[str, int]:
    r"""One species starting at `s[i]`: LaTeX and the index after it.

    Element symbols are set as `\text{...}`, a run of them in one group (`\text{NaCl}`), with
    the counts and charges as scripts after it (`\text{H}_2\text{O}`, `\text{SO}_4^{2-}`). That
    is how OpenStax writes them (`mtext`) and how the equation model wrote them before it was
    asked for `\ce`, so the readings are the same shape either way."""
    prefix = ""
    nuclide = _nuclide_prefix(s, i)
    if nuclide is not None:
        prefix, i = nuclide
    body: list[str] = []
    letters: list[str] = []  # element symbols not yet written: one \text group

    def put(piece: str) -> None:
        if letters:
            body.append("\\text{" + "".join(letters) + "}")
            letters.clear()
        body.append(piece)

    n = len(s)
    atoms = 0
    base = False  # the last thing written is something a script can hang on
    while i < n and not s[i].isspace():
        c = s[i]
        if c == "-" and s.startswith(("->", "-->"), i):
            break  # the arrow after the species, not a charge or a bond
        state = _STATE.match(s, i) if c == "(" and base else None
        if state:
            put("\\text{" + state.group(0) + "}")
            i = state.end()
            base = False
        elif c in "([":
            put(c)
            i += 1
            base = False
        elif c in ")]":
            put(c)
            i += 1
            base = True
        elif "A" <= c <= "Z":
            two = s[i : i + 2]
            if len(two) == 2 and "a" <= two[1] <= "z" and two in ELEMENTS:
                letters.append(two)
                i += 2
            elif c in ELEMENTS or (c in "XY" and prefix and not body and not letters):
                letters.append(c)  # X and Y: the generic symbol of a nuclide, ^{A}_{Z}X
                i += 1
            else:
                raise CeError("not an element symbol")
            atoms += 1
            base = True
        elif c == "\\" and (greek := _GREEK.match(s, i)) and not body and not letters:
            put("\\" + greek.group(1) + " ")  # a particle: alpha, beta, gamma, nu
            i = greek.end()
            atoms += 1
            base = True
        elif c in "enp" and not body and not letters:
            # the electron, neutron and proton: a lowercase symbol on its own
            after = s[i + 1 : i + 2]
            if after.isalpha() or (c != "e" and after and after not in " +,"):
                raise CeError("not a species")
            letters.append(c)
            i += 1
            atoms += 1
            base = True
        elif c.isdigit():
            j = i
            while j < n and s[j].isdigit():
                j += 1
            put(("_" + _group(s[i:j])) if base else s[i:j])
            i = j
        elif c == "^":
            if not base:
                raise CeError("superscript with nothing before it")
            latex, i = _caret(s, i)
            put(latex)
        elif c == "_":
            if not base:
                raise CeError("subscript with nothing before it")
            if i + 1 < n and s[i + 1] == "{":
                inner, j = _read_braced(s, i + 1)
                if not re.fullmatch(r"[0-9A-Za-z]+", inner):
                    raise CeError("unreadable subscript")
            else:
                match = re.match(r"_(\d+)", s[i:])
                if not match:
                    raise CeError("unreadable subscript")
                inner, j = match.group(1), i + match.end()
            put("_" + _group(inner))
            i = j
        elif c in _SIGNS and not (c == "-" and _bond_next(s, i + 1)):
            # a charge: signs right after the species ("Na+", "OH-", "Fe+++", "Cl-(aq)")
            if not base:
                if c == "+" and body:
                    break  # a plus after a state symbol: between two species
                raise CeError("sign with nothing before it")
            count = 1
            while i + count < n and s[i + count] == c:
                count += 1
            nxt = s[i + count : i + count + 1]
            if c == "+" and (nxt.isalnum() or nxt == "[") and nxt:
                break  # "H2+O2": a plus between two species
            if c == "+" and nxt == "(" and not _STATE.match(s, i + count):
                break
            put("^" + _group((str(count) if count > 1 else "") + _sign(c)))
            i += count
        elif c in "-=#":  # a bond: it joins two atoms, so something comes before it
            if not base:
                raise CeError("bond with nothing before it")
            put({"-": "\\text{-}", "=": "=", "#": "\\equiv "}[c])
            i += 1
            base = False
        elif c in "*." and base:
            put("\\cdot ")
            i += 1
            base = False
        else:
            break
    if not atoms:
        raise CeError("not a species")
    put("")
    return prefix + "".join(body).strip(), i


def _arrow_label(s: str, i: int) -> tuple[str | None, int]:
    """The `[label]` after an arrow at `s[i]`, as LaTeX, and the index after it."""
    if i >= len(s) or s[i] != "[":
        return None, i
    close = s.find("]", i)
    if close < 0:
        raise CeError("unbalanced arrow label")
    if close + 1 < len(s) and s[close + 1] == "[":
        raise CeError("two-part arrow label")
    raw = s[i + 1 : close].strip().strip("$").strip()
    if not raw or not _LABEL_CHARS.match(raw):
        raise CeError("unreadable arrow label")
    if "\\" in raw:
        command = re.fullmatch(r"\\([A-Za-z]+)", raw.replace(" ", ""))
        if not command:
            raise CeError("unreadable arrow label")
        return "\\" + command.group(1), close + 1
    return "\\text{" + raw + "}", close + 1


def _is_species_latex(piece: str) -> bool:
    """Is the last thing written a species (not a plus, an arrow or a coefficient)?"""
    return piece not in {"+", ","} and not piece.startswith(("\\long", "\\xr", "\\xl", "\\over"))


def ce_to_latex(source: str) -> str:
    r"""Plain LaTeX for the body of one `\ce{...}`. Raises `CeError` for anything outside the
    subset in the module docstring. The result uses only commands the LaTeX check allows."""
    s = source.replace("~", " ").strip()
    if not s:
        raise CeError("empty")
    if "$" in re.sub(r"\[[^\]]*\]", "", s):
        raise CeError("math inside")  # only an arrow's label may hold it: ->[$\Delta$]
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        arrow = next((a for a in _ARROWS if s.startswith(a[0], i)), None)
        if arrow is not None:
            if arrow[1] is None:
                raise CeError("unequal equilibrium arrow")
            i += len(arrow[0])
            label, i = _arrow_label(s, i)
            if label is None:
                out.append(arrow[1])
            elif arrow[1] == "\\longrightarrow":
                out.append("\\xrightarrow{" + label + "}")
            elif arrow[1] == "\\longleftarrow":
                out.append("\\xleftarrow{" + label + "}")
            else:
                out.append("\\overset{" + label + "}{" + arrow[1] + "}")
            continue
        if c in "+,":
            out.append(c)
            i += 1
            continue
        state = _STATE.match(s, i)
        if state and out and _is_species_latex(out[-1]):
            out[-1] += "\\text{" + state.group(0) + "}"
            i = state.end()
            continue
        match = _COEFFICIENT.match(s, i)
        if match:
            after = s[match.end() : match.end() + 1]
            if after == "" or after.isspace() or after.isalpha() or after in "([^_\\":
                whole, frac = match.group(1), match.group(2)
                out.append(f"\\frac{{{whole}}}{{{frac}}}" if frac else whole)
                i = match.end()
                continue
        latex, i = _species_latex(s, i)
        out.append(latex)
    if not out:
        raise CeError("empty")
    return " ".join(out)


def _find_ce(latex: str, start: int) -> int:
    """Index of the next `\\ce` command at or after `start` that is not part of a longer
    name (`\\cell`), or -1."""
    while True:
        at = latex.find("\\ce", start)
        if at < 0:
            return -1
        end = at + 3
        if end < len(latex) and latex[end].isalpha():
            start = end
            continue
        return at


def convert_ce_commands(latex: str) -> str:
    r"""`latex` with every `\ce{...}` replaced by plain LaTeX (`ce_to_latex`). Text with no
    `\ce` comes back unchanged. Raises `CeError` when one of them is outside the subset."""
    out: list[str] = []
    pos = 0
    while True:
        at = _find_ce(latex, pos)
        if at < 0:
            out.append(latex[pos:])
            return "".join(out)
        out.append(latex[pos:at])
        j = at + 3
        while j < len(latex) and latex[j] == " ":
            j += 1
        if j >= len(latex) or latex[j] != "{":
            raise CeError("\\ce without braces")
        inner, end = _read_braced(latex, j)
        out.append(ce_to_latex(inner))
        pos = end


# ================================================================================== MathML


def _local(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _upright_letter(node: ET.Element, *, upper: bool) -> bool:
    """An `mi` of one upright capital (or lowercase) letter: what `\\mathrm{Na}` makes of
    each of its letters."""
    text = node.text or ""
    return (
        _local(node) == "mi"
        and len(node) == 0
        and len(text) == 1
        and node.get("mathvariant") == "normal"
        and (text.isupper() if upper else text.islower())
        and text.isascii()
    )


def arrows_as_operators(root: ET.Element) -> None:
    """An arrow the converter wrote as an identifier (`\\longrightarrow` is an `mi`) is an
    operator, as published MathML has it: a reader then says "right arrow", not a letter."""
    for node in root.iter():
        if _local(node) == "mi" and len(node) == 0 and (node.text or "") in ARROW_HEADS:
            node.tag = node.tag.rsplit("}", 1)[0] + "}mo" if "}" in node.tag else "mo"


def group_element_symbols(root: ET.Element) -> None:
    r"""Make a charge or a count belong to its element symbol, in place. `\mathrm{Na^+}` is `N`
    and `msup(a, +)` in the converter's output: the charge hangs on the `a`, and a reader that
    follows the MathML says "N a to the power plus" and cannot tell the ion from two variables.
    An upright capital followed by a script whose base is the upright lowercase letter that
    spells an element with it becomes `msup(mrow(N, a), +)`: the letters stay what they were
    (one `mi` each, as published MathML and the scorers have them), and the script is on the
    symbol. A symbol with no script is left alone, and so are `Hz`, `km` and italic letters."""
    for parent in root.iter():
        kids = list(parent)
        i = 0
        while i + 1 < len(kids):
            first, second = kids[i], kids[i + 1]
            if (
                _upright_letter(first, upper=True)
                and _local(second) in {"msub", "msup", "msubsup"}
                and len(second)
                and _upright_letter(second[0], upper=False)
                and (first.text or "") + (second[0].text or "") in ELEMENTS
            ):
                base = second[0]
                symbol = ET.Element(
                    second.tag.rsplit("}", 1)[0] + "}mrow" if "}" in second.tag else "mrow"
                )
                symbol.extend([first, base])
                second.remove(base)
                second.insert(0, symbol)
                parent.remove(first)
                kids.pop(i)
                continue
            i += 1
